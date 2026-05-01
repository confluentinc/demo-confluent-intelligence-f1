# F1 Pit Wall AI Demo

Real-time F1 pit strategy system. Streams simulated race telemetry through Confluent Cloud, detects tire anomalies via Flink + AI_DETECT_ANOMALIES, and runs a Flink Streaming Agent (Bedrock/Claude) to recommend pit stops.

**Team:** River Racing | **Driver:** Sean Falconer (#44) | **Circuit:** Silverstone | **57 laps, ~10s each, ~9.5 min total**

---

## Commands

```bash
uv run deploy                  # Interactive deploy: prompts → credentials.env → terraform core → terraform demo
uv run deploy --automated      # Same as above but also deploys Jobs 1 & 2 via Terraform (full pipeline, no Workspace needed)
uv run destroy                 # Tear down demo then core (confirms before each)
uv run reset                   # Stop Flink jobs, drop topics/schemas, recreate + Jobs 1 & 2 (default: automated)
uv run reset --manual          # Same but skips Jobs 1 & 2; re-deploy them manually in SQL Workspace
uv run api-keys create         # Create AWS IAM user + keys for Bedrock access
uv run setup-mcp               # Write confluent-mcp.env from TF outputs, register MCP server with Claude Code

./scripts/start-race.sh   # Launch race simulator in ECS Fargate
./scripts/stop-race.sh    # Stop ECS task
aws logs tail /ecs/f1-simulator --follow

# Read Terraform outputs
cd terraform/core && terraform output      # CC: env, cluster, flink, API keys
cd terraform/demo && terraform output      # AWS: MQ IP, Postgres IP, ECS cluster

# Force simulator rebuild after changing datagen/
terraform -chdir=terraform/demo taint module.ecs.null_resource.docker_build_push
terraform -chdir=terraform/demo apply -auto-approve

# Deploy connectors manually (generated/ has real values; demo-reference/ has placeholders)
confluent connect cluster create --config-file generated/mq_connector_config.json \
  --environment $(cd terraform/core && terraform output -raw environment_id) \
  --cluster $(cd terraform/core && terraform output -raw cluster_id)

cd datagen && python -m pytest tests/ -v
```

---

## Architecture

```
Race Simulator (ECS Fargate)
  ├── Kafka produce (AVRO)   → car_telemetry
  └── MQ publish (JMS text)  → IBM MQ EC2 → MQ Source Connector → race_standings_raw
                                                                         │
                                                          Job 0 (Terraform-managed Flink SQL)
                                                          Parse JMS envelope, key by car_number
                                                          WHERE car_number > 0 (drops warmup)
                                                                         │
                                                                  race_standings
                                                                         │
Postgres (EC2) → CDC Debezium Connector → driver_race_history ──────────┤
                                                                         │
                                                          Job 1 (manual Flink SQL)
                                                          10s tumbling window + temporal join
                                                          AI_DETECT_ANOMALIES(tire_temp_fl_c)
                                                                         │
                                                                     car_state
                                                                         │
                                                          Job 2 (manual Flink SQL)
                                                          AI_RUN_AGENT → pit_decisions
                                                                         │
                                                              Tableflow → S3 → Databricks Genie
```

---

## Terraform Layout

Two stacks — deploy core first. Demo reads core via `terraform_remote_state` (local backend). Demo has zero variables.

| Stack | Path | What it creates |
|-------|------|-----------------|
| core | `terraform/core/` | CC environment, Kafka cluster, Schema Registry, Flink compute pool, Bedrock LLM connections + models, API keys |
| demo | `terraform/demo/` | `car_telemetry` + `race_standings` topics (Flink CREATE TABLE), IBM MQ EC2, ECS Fargate, Postgres EC2, S3 Tableflow, MQ + CDC connectors, Job 0 Flink statement |

**Naming:** `RIVER-RACING-${deployment_id}` prefix. CC resources uppercase (`RIVER-RACING-PROD-ENV`); AWS modules apply `lower()` internally. `deployment_id` accepts any alphanumeric ≤8 chars (e.g., `PROD`).

**Core variables:** `confluent_cloud_api_key`, `confluent_cloud_api_secret`, `owner_email`, `deployment_id`, `aws_bedrock_access_key`, `aws_bedrock_secret_key`, `aws_session_token` (optional, required for `ASIA*` temp keys). Region hardcoded to `us-east-1`.

---

## Kafka Topics

| Topic | Created by | Tableflow | Notes |
|-------|-----------|-----------|-------|
| `car_telemetry` | Terraform (Flink CREATE TABLE) | No | AVRO, 1 partition, no PRIMARY KEY |
| `race_standings_raw` | MQ Connector (auto on warmup message) | No | JMS envelope schema |
| `race_standings` | Terraform (Flink CREATE TABLE) | No | PRIMARY KEY(car_number) for versioned temporal join |
| `driver_race_history` | CDC Connector | Yes | 198 rows historical |
| `car_state` | Job 1 Flink statement | No | One record per 10s window |
| `pit_decisions` | Job 2 Flink statement | Yes | Agent output |

Topic schemas (CREATE TABLE SQL): `terraform/modules/topics/main.tf`
`pit_decisions` schema: `demo-reference/streaming_agent_pit_decisions.sql`

---

## Flink Jobs

Jobs 1 and 2 are copy-pasted into Flink SQL Workspace during the demo, or deployed via Terraform with `--automated`. Job 0 is always Terraform-managed.

| Job | SQL file | How deployed | Input → Output |
|-----|----------|--------------|----------------|
| 0 | `demo-reference/parse_standings.sql` | Terraform | `race_standings_raw` → `race_standings` |
| 1 | `demo-reference/enrichment_anomaly.sql` | Manual (Workspace) — or via `--automated` | `car_telemetry` + `race_standings` → `car_state` |
| 2a | `demo-reference/streaming_agent_create_agent.sql` | Manual (Workspace) — or via `--automated` | Creates `pit_strategy_agent` |
| 2b | `demo-reference/streaming_agent_pit_decisions.sql` | Manual (Workspace) — or via `--automated` | `car_state` → `pit_decisions` |

**Job 1 CTE pattern:** `enriched` (temporal join on `event_time`) → `windowed` (10s TUMBLE, AVG sensors) → `anomaly` (AI_DETECT_ANOMALIES on `tire_temp_fl_c`, conf=99.99, minTraining=20, maxTraining=50, enableStl=false) → final SELECT with `actual_value > upper_bound` filter.

**Job 2 pattern:** CREATE AGENT (Bedrock Claude) → AI_RUN_AGENT on each `car_state` row → REGEXP_EXTRACT 7 labeled fields from response text.

**DBT:** Integration is planned but not implemented — no dbt project exists in the repo. Do not attempt to run `dbt run` or scaffold a dbt project without being explicitly asked.

---

## Critical Gotchas

**Temporal join ordering:** The temporal join (`FOR SYSTEM_TIME AS OF event_time`) must be in an early CTE on the raw stream. After OVER() aggregations, `window_time` loses its rowtime attribute — the join silently returns zero rows.

**No PRIMARY KEY on `car_telemetry`:** Only `race_standings` needs PRIMARY KEY for versioned-table semantics. Adding it to `car_telemetry` registers an Avro INT key schema; the simulator writes string keys and Job 1 deserialization fails.

**AI_DETECT_ANOMALIES warmup:** Emits rows with NULL `is_anomaly` for the first `minTrainingSize` rows. Normal — don't filter these out entirely.

**SR hard-delete after DROP TABLE:** Dropping a Flink table leaves `<topic>-key` and `<topic>-value` subjects in Schema Registry. Recreating with a different schema fails. Delete both subjects with `--permanent` before recreating.

**`json` format unsupported:** Use `json-registry` (not `json`) in Flink CREATE TABLE. Pair with `"output.data.format": "JSON_SR"` on the connector side.

**`PROCTIME()` unsupported:** CC Flink only supports event-time temporal joins. No `FOR SYSTEM_TIME AS OF PROCTIME()`.

**Deploy jobs before race starts:** Default scan startup mode is `latest` — jobs miss earlier laps if deployed after race starts. Add `/*+ OPTIONS('scan.startup.mode'='earliest-offset') */` if deploying mid-race.

Full technical discoveries (MQ, Terraform, serialization, git): `docs/technical-discoveries.md`

---

## Secrets & Credentials

| File | Purpose | Created by |
|------|---------|------------|
| `credentials.env` | All deploy secrets | `deploy.py` (interactive prompts) |
| `terraform/core/terraform.tfvars` | TF variables | `deploy.py` |
| `generated/mq_connector_config.json` | MQ connector with real values | Terraform |
| `generated/cdc_connector_config.json` | CDC connector with real values | Terraform |
| `confluent-mcp.env` | MCP server env vars | `uv run setup-mcp` |

All gitignored. Do not commit any of these files.

---

## File Sync Rule

`demo-reference/*.sql` and `Walkthrough.md` must stay identical. When you modify SQL in one, update the other in the same pass.

---

## Key File Locations

| File | Purpose |
|------|---------|
| `deploy.py` | Deployment orchestrator |
| `scripts/reset.py` | Reset for fresh race re-run |
| `scripts/common/` | Shared utils: terraform, credentials, UI |
| `datagen/simulator.py` | Race simulator main loop |
| `datagen/race_script.py` | Lap-by-lap race state machine |
| `datagen/config.py` | Simulator env var definitions |
| `terraform/core/main.tf` | CC infra definition |
| `terraform/demo/main.tf` | AWS + Flink tables + connectors + Jobs 0–2 (automated) |
| `terraform/modules/topics/main.tf` | `car_telemetry` + `race_standings` CREATE TABLE SQL |
| `demo-reference/enrichment_anomaly.sql` | Job 1 (copy-paste to Workspace or via --automated) |
| `demo-reference/streaming_agent_create_agent.sql` | Job 2a — CREATE AGENT (copy-paste or via --automated) |
| `demo-reference/streaming_agent_pit_decisions.sql` | Job 2b — CREATE TABLE pit_decisions (copy-paste or via --automated) |
| `Walkthrough.md` | Step-by-step demo guide |

---

## Reference Docs

| What | Where |
|------|-------|
| Use case + race narrative (lap-by-lap script, Genie expected answers) | `docs/USE-CASE.md` |
| Demo constraints (Must Have / Must NOT Have) | `docs/constraints.md` |
| Full technical discoveries (all numbered gotchas) | `docs/technical-discoveries.md` |
| Tableflow setup | `docs/SETUP-TABLEFLOW.md` |
| Genie setup + example queries | `docs/SETUP-GENIE.md`, `tableflow/EXAMPLE-QUERIES.md` |
| DBT adapter setup | `docs/SETUP-DBT-ADAPTER.md` |

---

## Git

Standalone repo with its own `.git`. Remote: `confluentinc/demo-confluent-intelligence-f1`. Use `git push-external` to push (Confluent airlock policy for org repos).
