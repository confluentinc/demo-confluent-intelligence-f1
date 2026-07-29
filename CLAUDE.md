# F1 Pit Wall AI Workshop

Multi-attendee, instructor-led workshop. Each attendee gets an isolated Confluent
Cloud environment with a live race feed; they use Flink SQL to detect tire
anomalies (`ML_DETECT_ANOMALIES`) and run a Flink Streaming Agent (Bedrock/Claude)
that recommends pit stops, then (LAB 5) build a no-code IBM watsonx Orchestrate
agent that drafts social posts from the same live feed. Organizers provision
everything from a single Confluent org + AWS account with **`wsa`**
(confluentinc/workshop-setup-accelerator, run from a sibling checkout, driven by
this repo's `wsa-spec-aws.yaml`); attendees never run Terraform and **never log
in to the Confluent Console** — they claim an account through the wsa dispenser
(or get an instructor-distributed card) and run Flink SQL through the bundled
shell (`uv run f1-sql`). See "WSA (organizer provisioning)" below.

**Team:** River Racing | **Driver:** John Doe (#88) | **Circuit:** Silverstone | **60 laps**

---

## Commands

```bash
# Organizer: full workshop (many attendees) via wsa — run from a SIBLING
# workshop-setup-accelerator checkout (its ONBOARDING.md "Local layout"),
# pointed at this repo's wsa-spec-aws.yaml.
op run --env-file=.env.tpl -- ./bin/wsa validate -w <path-to-this-repo>/wsa-spec-aws.yaml
op run --env-file=.env.tpl -- ./bin/wsa build -w <path-to-this-repo>/wsa-spec-aws.yaml \
  --accounts 1-20 --concurrency 4       # applies terraform/aws-shared then N terraform/aws
./bin/wsa dispenser-upload --sheets-credentials sheets-credentials.json  # self-serve claim (optional)
op run --env-file=.env.tpl -- ./bin/wsa clean -w <path-to-this-repo>/wsa-spec-aws.yaml
#   --no-password-reset --no-dispenser-clear   # skip if this workshop doesn't use the dispenser

# Organizer: turn wsa's build-output.csv into the credential cards our tools expect
uv run workshop creds --csv <wsa-repo>/wsa-output/<run-id>/build-output.csv --name <name>
uv run workshop validate --creds-glob 'runs/*/credentials/*.env'   # API-key health checks, no AWS/login needed

# Attendee, self-serve (wsa dispenser claim email -> local credentials.env)
uv run f1-onboard                # prompts field-by-field, or --paste to parse a pasted email

# Attendee (no Console login): run Flink SQL with a credential card.
# The card is resolved automatically — see "Credential card resolution" below.
uv run f1-sql
uv run f1-sql --creds runs/<name>/credentials/f1wp001.env   # override

# Attendee: live race dashboard (consumes their own Kafka topics, no login)
uv run f1-pitwall                                           # → http://localhost:8000
uv run f1-pitwall --mock                                    # offline demo/dev, no Confluent env

# Organizer: shared race-feed service for LAB 5 (OpenAPI tool for watsonx Orchestrate)
uv run f1-social-feed --creds-glob 'runs/*/credentials/*.env'   # → :8080, serves /race-feed/{prefix}
uv run f1-social-feed --mock                                    # offline demo/dev, no Confluent env
# Same OpenAPI tool, but sourced from the Real-Time Context Engine (MCP) instead of Kafka:
RTCE_API_KEY=... RTCE_API_SECRET=... uv run f1-social-feed-rtce --creds-glob 'runs/*/credentials/*.env'
RTCE_API_KEY=... RTCE_API_SECRET=... uv run f1-social-feed-rtce --probe --creds <card>.env  # validate RTCE contract

# Organizer: single environment (smoke test / demo) — shared then attendee
uv run deploy                  # prompts → credentials.env → terraform/aws-shared → terraform/aws
uv run deploy --automated      # same, no prompts (reads credentials.env)
uv run destroy                 # pick which local deployment(s) to tear down, confirm, destroy
                               #   groups: "deploy" (aws + aws-shared) / "self-service"
                               #   A wsa workshop is unreachable (wsa keeps state in its own
                               #   run dir) — tear one down with `wsa clean`. Hand-applied
                               #   aws-shared state IS reachable, behind a typed confirmation.

# Self-service (solo): Confluent-only, NO AWS infra (no Postgres/CDC/ECS/ECR/Docker)
uv run selfservice up          # apply terraform/self-service → credential card → seed driver_race_history
uv run selfservice up --automated   # no prompts (reads credentials.env)
uv run selfservice down        # tear down terraform/self-service
uv run f1-race                 # local simulator (ECS stand-in)

# Control all attendee race feeds (ECS services)
uv run start-all-races         # scale every attendee simulator to 1
uv run stop-all-races          # scale every attendee simulator to 0
uv run reset                   # blank slate for a new race: drops lab objects (car_state,
                               #   pit_decisions, agent) AND truncates car_telemetry so LAB 3
                               #   doesn't replay finished races. race_standings is compacted,
                               #   so it can't be truncated (harmless — see scripts/reset.py).
                               #   --keep-source skips the truncation.

uv run api-keys create         # Create AWS IAM user + keys for Bedrock access

# Read attendee Terraform outputs
cd terraform/aws && terraform output -json attendee_credentials

# Logs for one attendee simulator
aws logs tail /ecs/<prefix>-<hex>-simulator --follow

# Tests / lint
uv run --with "confluent-kafka[avro]" --with httpx --with pytest --with fastavro python -m pytest datagen/tests/
uv run --with ruff ruff check datagen/ scripts/ deploy.py
```

---

## Architecture

```
Race Simulator (ECS Fargate service, one per attendee, RACE_LOOP=true)
  ├── car_telemetry   (car #88, AVRO)            → Kafka (direct)
  └── race_standings  (22 cars, AVRO, keyed)     → Kafka (direct, upsert)
                                                         │
Shared Postgres → CDC Debezium (per-attendee slot) → driver_race_history
                                                         │
                              LAB 3 — Flink SQL (attendee-written)
                              10s tumbling window + temporal join
                              ML_DETECT_ANOMALIES(tire_temp_fl_c) → car_state
                                                         │
                              LAB 4 — Flink SQL (attendee-written)
                              CREATE AGENT + AI_RUN_AGENT → pit_decisions
                                                         │
                              LAB 5 — IBM watsonx Orchestrate (no-code)
                              OpenAPI tool → f1-social-feed → drafted social posts
```

There is **no IBM MQ and no Job 0** — the simulator produces `race_standings`
straight to Kafka as keyed Avro, so the topic is already a clean upsert/versioned
table. There is **no Tableflow/Databricks/dbt**. The only external SaaS is **IBM
watsonx Orchestrate** in LAB 5, reached read-only via the shared `f1-social-feed`
HTTP service (an OpenAPI tool) — everything upstream is Confluent-only.

---

## Terraform Layout

Two tiers. `aws-shared` is applied once; `aws` is applied per attendee (by `wsa`,
or once by `deploy.py`). The `aws` tier consumes `aws-shared` outputs as variables
(injected by wsa, or by `deploy.py` reading the shared state).

| Tier | Path | What it creates |
|------|------|-----------------|
| shared | `terraform/aws-shared/` | Default VPC/subnets lookup, shared Postgres (N replication slots, seeded `driver_race_history`), ECR repo + simulator image build |
| per-attendee | `terraform/aws/` | CC environment, cluster, SR, Flink pool + keys, `modules/llm` (Bedrock connections + `CREATE MODEL`), topics (`car_telemetry`, `race_standings`), per-attendee Postgres CDC connector, ECS cluster + task def + **service** running the simulator |
| self-service | `terraform/self-service/` | Confluent-**only**: CC environment, cluster, SR, Flink pool, topics, `modules/llm`, and an empty `driver_race_history` table. **No** AWS (Postgres/CDC/ECS/ECR). `uv run selfservice up` seeds `driver_race_history` with a bounded Flink INSERT and the local `f1-race` simulator feeds the topics. |

The Bedrock connections + `CREATE MODEL` statements live in the shared
`terraform/modules/llm/` module, consumed by both `terraform/aws` and
`terraform/self-service` (keep them in sync via the module, not by copy).

**Naming:** per-attendee CC resources use `RIVER-RACING-${prefix}` (e.g.
`RIVER-RACING-f1wp001-ENV`); ECS resources use the lowercased
`river-racing-${prefix}-<hex>-simulator` (the instructor scripts filter on
`river-racing`).

**Per-attendee isolation:** separate CC environment/cluster/Flink pool; CDC
connector uses `slot.name=f1_cdc_${prefix}` + `publication.name=f1_pub_${prefix}`
so many connectors share one Postgres. Bedrock credentials are shared across all
attendees. `aws-shared` sets `max_replication_slots = attendee_count + 10`.

**Key `aws` variables:** `prefix`, `owner_email`, `region`,
`confluent_cloud_api_key/_secret`, `aws_bedrock_access_key/_secret`,
`aws_session_token` (optional), and the shared inputs `shared_vpc_id`,
`shared_subnet_ids`, `shared_postgres_host`, `shared_postgres_password`,
`shared_ecr_image_uri` — every `shared_*` variable name must exactly match an
output name in `terraform/aws-shared/outputs.tf` (`shared_X` ← output `X`),
since `wsa` injects them by that naming convention as `TF_VAR_shared_X`.
`flink_max_cfu` (default 5), `seconds_per_lap` (default 60 → 60-minute race),
`race_loop` (default true) tune cost/pacing.

---

## Kafka Topics

| Topic | Created by | Notes |
|-------|-----------|-------|
| `car_telemetry` | Terraform (Flink CREATE TABLE) | AVRO, no PRIMARY KEY, string message key |
| `race_standings` | Terraform (Flink CREATE TABLE) | AVRO, PRIMARY KEY(car_number), upsert — produced directly by the simulator |
| `driver_race_history` | per-attendee CDC connector | 198 historical rows |
| `car_state` | LAB 3 Flink statement | one record per 10s window |
| `pit_decisions` | LAB 4 Flink statement | agent output |

Topic schemas (CREATE TABLE SQL): `terraform/modules/topics/main.tf`.

---

## Flink Jobs (the labs)

Jobs 1 & 2 are **not** pre-deployed — attendees write them in LAB 3 / LAB 4. The
canonical SQL is in `demo-reference/` and reproduced in the lab guides.

| Job | SQL file | Input → Output |
|-----|----------|----------------|
| 1 | `demo-reference/enrichment_anomaly.sql` | `car_telemetry` + `race_standings` → `car_state` |
| 2a | `demo-reference/streaming_agent_create_agent.sql` | creates `pit_strategy_agent` |
| 2b | `demo-reference/streaming_agent_pit_decisions.sql` | `car_state` → `pit_decisions` |

`llm_textgen_model` / `llm_embedding_model` are pre-deployed per environment by
`terraform/aws`.

**LAB 5 is not Flink** — it's a no-code IBM watsonx Orchestrate agent that reads
the live feed via an OpenAPI tool served by `scripts/social_feed/` (`f1-social-feed`).
Canonical agent config: `demo-reference/orchestrate_social_agent.md`. Lab order is
now LAB 1–4 (Flink/SQL) → LAB 5 (Orchestrate) → LAB 6 (wrap-up).

The OpenAPI tool has **two interchangeable backends** behind the identical
`/race-feed/{prefix}` + `/openapi.json` surface: `f1-social-feed` (tails Kafka) and
`f1-social-feed-rtce` (MCP client to the Real-Time Context Engine). Orchestrate
imports the same spec either way — only which service you host changes. Orchestrate
can't consume RTCE's MCP endpoint directly (it supports only *local* MCP servers),
which is why the shim re-exposes RTCE as REST/OpenAPI.

---

## Critical Gotchas

**Direct `race_standings` production (the new failure mode):** the simulator now
owns producing schema-valid Avro to `race_standings` with the correct **key**.
`datagen/simulator.py` resolves the registered `race_standings-key` schema and
encodes the key as a primitive int or a record automatically. After the first
deploy, verify the `race_standings-key`/`-value` subjects look right — a wrong key
encoding makes the LAB 3 temporal join silently return zero rows.

**Temporal join ordering:** `FOR SYSTEM_TIME AS OF event_time` must be in an early
CTE on the raw stream. After OVER()/TUMBLE aggregations, `window_time` loses its
rowtime attribute and the join silently returns zero rows.

**No PRIMARY KEY on `car_telemetry`:** only `race_standings` is keyed. The
telemetry producer writes a string message key; do not add a PK that would
register an Avro int key schema.

**ML_DETECT_ANOMALIES warmup:** withholds output for the first `minTrainingSize`
(20) windows. The simulator produces 4 warmup telemetry windows (lap=0) to prime
it; Job 1 filters `lap > 0`.

**SR hard-delete after DROP TABLE:** dropping a Flink table leaves `<topic>-key`
and `<topic>-value` subjects. `scripts/reset.py` deletes them with `--permanent`.

**`json` format unsupported:** use `json-registry` in Flink CREATE TABLE.

**Deploy jobs before/independent of race start:** default scan startup is
`latest`. `pit_decisions` uses `scan.startup.mode=earliest-offset` so it processes
laps already in `car_state`.

Full technical discoveries: `docs/technical-discoveries.md`.

---

## Secrets & Credentials

| File | Purpose | Created by |
|------|---------|------------|
| `credentials.env` | Deploy secrets (`TF_VAR_*`) + the `F1_CARD` pointer | `deploy.py` |
| `runs/<name>/credentials/<prefix>.env` | Credential card (`F1_*`) — what the attendee tools authenticate with | `workshop creds`, `deploy.py`, `selfservice up` |

### Credential card resolution

`f1-sql` / `f1-pitwall` / `f1-race` no longer require `--creds`. `resolve_card()` in
`scripts/common/credentials.py` picks the card, first hit wins:

1. `--creds <path>`
2. `$F1_CREDS`
3. `credentials.env` — its `F1_CARD=<path>` pointer (skipped if the target is gone), or
   the file itself when it holds `F1_*` keys (what `f1-onboard` writes)
4. the only card under `runs/*/credentials/*.env`

Ambiguity is an error, never a guess: several cards and no pointer exits listing them.
`deploy.py` and `selfservice up` call `set_active_card()`; `destroy` and
`selfservice down` call `clear_active_card(only_if_under=...)`, scoped so tearing down
one deployment leaves another's pointer alone. The organizer fan-out tools
(`f1-social-feed`, `f1-social-feed-rtce`, `workshop validate`) deliberately keep
explicit `--creds` / `--creds-glob` — operating over many cards is their whole job.

Gitignored. Do not commit. The `aws` tier's flat outputs (`environment_id`,
`kafka_api_key`, `sr_api_key`, ...) are what `wsa-spec-aws.yaml`'s
`credentials:` fields point at (`source: terraform`); `wsa` turns those into
the dispenser CSV and each attendee's claim email. The nested
`attendee_credentials` map output still exists for the single-environment
smoke-test flow (`terraform output -json attendee_credentials`, `deploy.py`).

---

## WSA (organizer provisioning)

Provisioning and teardown are owned by `wsa` (confluentinc/workshop-setup-accelerator),
not a repo-local orchestrator. Run it from a **sibling checkout**
(`workshop-setup-accelerator/`, per that repo's `ONBOARDING.md` "Local
layout"), pointed at this repo's `wsa-spec-aws.yaml` with `-w`.

- **Spec:** `wsa-spec-aws.yaml` (repo root) — `account_count: 20` by default;
  bump `terraform/aws-shared`'s `attendee_count` Terraform default to match if
  you need more (`wsa` does not forward `account_count` to the shared apply).
- **Terraform contract:** `shared_infra_path: terraform/aws-shared/`,
  `terraform_path: terraform/aws/`. Every `credentials:` field with
  `source: terraform` must match a flat root `output` in
  `terraform/aws/outputs.tf` by name.
- **Secrets:** plain `.env`/shell `TF_VAR_*` exports (Confluent + Bedrock
  keys) — this workshop does not use the TMM 1Password vault, so `op` is
  omitted from `tools_required`.
- **Dispenser:** attendees can claim via `wsa dispenser-upload` (Google
  Form/Sheet) and self-serve `uv run f1-onboard` their claim-email values
  into a local `credentials.env`, or an instructor can run
  `uv run workshop creds --csv <run>/build-output.csv --name <name>` and hand
  out `runs/<name>/credentials/<prefix>.{env,md}` directly — same downstream
  tools either way.
- **Clean:** `wsa clean -w wsa-spec-aws.yaml` — pass `--no-password-reset
  --no-dispenser-clear` if this run never used the dispenser/Gmail reset.

---

## File Sync Rule

`demo-reference/*.sql` and the lab guides under `labs/instructor-led/` must stay
in sync — when you change the SQL in one, update the other in the same pass. The
same applies to `demo-reference/orchestrate_social_agent.md` ↔ the LAB 5 guide
(`labs/instructor-led/LAB5_orchestrate_integration/LAB5.md`).

---

## Key File Locations

| File | Purpose |
|------|---------|
| `deploy.py` | Standalone two-tier deploy (shared + one attendee) |
| `scripts/reset.py` | Clear an attendee's lab objects + truncate source topics |
| `scripts/pitwall/` | `f1-pitwall` live web dashboard — Kafka consumer → FastAPI/websocket → animated browser view; progressive reveal of LAB 3/4 panels; `--mock` offline feed |
| `scripts/social_feed/` | `f1-social-feed` shared HTTP service for LAB 5 — tails each attendee's Kafka topics, serves `GET /race-feed/{prefix}` + auto OpenAPI spec for the watsonx Orchestrate tool; reuses pitwall consumer; `--mock` offline feed |
| `scripts/social_feed_rtce/` | `f1-social-feed-rtce` — same OpenAPI tool, but an MCP client to the Real-Time Context Engine (RTCE) instead of Kafka. Reuses `social_feed`'s `FeedState`+`create_app`; new bits are the RTCE MCP client + poller. Global API key via `RTCE_API_KEY/SECRET`; per-attendee endpoint from card `F1_RTCE_MCP_ENDPOINT`; `--probe` validates the live contract |
| `scripts/instructor/` | Fan-out start/stop of all attendee race feeds |
| `scripts/common/` | Shared utils: terraform, credentials, UI |
| `datagen/simulator.py` | Race simulator — produces telemetry + standings to Kafka, RACE_LOOP |
| `datagen/config.py` | Simulator env vars |
| `terraform/aws-shared/` | Shared infra (VPC, Postgres, ECR image) |
| `terraform/aws/` | Per-attendee CC env + ECS simulator service |
| `terraform/modules/topics/main.tf` | `car_telemetry` + `race_standings` CREATE TABLE |
| `wsa-spec-aws.yaml` | wsa orchestration spec — read by `wsa build`/`clean`/`validate` |
| `scripts/workshop/creds.py` | `workshop creds` — wsa's build-output.csv → `runs/<name>/credentials/*.env,.md` |
| `scripts/workshop/onboard.py` | `f1-onboard` — self-serve: wsa claim-email values → local `credentials.env` |
| `scripts/workshop/validate.py` | `workshop validate` — API-key health checks against one or many cards |
| `labs/` | Attendee lab guides |
| `demo-reference/*.sql` | Canonical lab SQL |
| `demo-reference/orchestrate_social_agent.md` | Canonical LAB 5 Orchestrate agent config (persona, tool, prompts) |

---

## Git

Standalone repo with its own `.git`. Remote:
`confluentinc/demo-confluent-intelligence-f1`. Use `git push-external` to push
(Confluent airlock policy for org repos).
