# F1 Pit Wall AI Demo — Walkthrough

A real-time AI pit strategy system for River Racing at the Silverstone Grand Prix. An AI agent monitors live car telemetry, detects anomalies via `AI_DETECT_ANOMALIES`, and recommends pit stop strategy — all running as Flink SQL on Confluent Cloud.

**Team:** River Racing | **Driver:** James River (#44) | **Circuit:** Silverstone | **Laps:** 57 (~10s each, ~9.5 min total)

## Prerequisites

- **Confluent Cloud** account with an API key/secret (Organization Admin)
- **AWS** credentials configured (`~/.aws/credentials` or env vars) — needed for EC2, ECS, ECR
- **Confluent CLI** installed and logged in (`confluent login`)
- **Terraform** >= 1.3
- **Docker Desktop** running (builds the race simulator image for ECS)
- **uv** installed ([install guide](https://docs.astral.sh/uv/getting-started/installation/))

> [!TIP]
>
> Don't have a Confluent Cloud API key? Run `uv run api-keys create` to generate one automatically.

---

## 1. Deploy Infrastructure

```bash
uv run deploy
```

You'll be prompted for:

| Prompt | Example | Notes |
|--------|---------|-------|
| Confluent Cloud API Key | `ABCDEF1234567890` | Organization-scoped key |
| Confluent Cloud API Secret | `abc123...` | |
| Owner email | `you@example.com` | Tagged on AWS resources |

Region is hardcoded to `us-east-2`. Resource names are auto-generated with a unique suffix (e.g., `f1-demo-a1b2c3d4`).

The deploy creates two Terraform stacks:

**Core** (Confluent Cloud):
- Environment, Kafka cluster, Schema Registry
- Flink compute pool + API keys
- Service account with EnvironmentAdmin

**Demo** (AWS + Flink tables):
- `car-telemetry` and `race-standings` topics (Flink CREATE TABLE with schemas, watermarks, primary keys)
- EC2 with IBM MQ (pub/sub topic: `dev/race-standings`, durable subscription)
- EC2 with Postgres (22 fictional drivers pre-loaded)
- ECS Fargate task definition (race simulator Docker image)
- S3 bucket + IAM role for Tableflow
- Auto-generated connector configs at `generated/mq_connector_config.json` and `generated/cdc_connector_config.json`

Deployment takes 15-20 minutes (mostly ECR image build + Flink compute pool provisioning).

### Verify deployment

After deployment completes, open the **Confluent Cloud UI** and confirm:
- Environment exists (named `f1-demo-env`)
- Kafka cluster exists with `car-telemetry` and `race-standings` topics
- Flink compute pool is provisioned

---

## 2. Set up Confluent MCP for Claude Code (optional)

```bash
uv run setup-mcp
```

This reads Terraform core outputs and registers a Confluent MCP server with Claude Code. Restart Claude Code after running this.

---

## 3. Deploy the MQ Source Connector

The MQ Source Connector bridges IBM MQ to Kafka. It subscribes to the MQ pub/sub topic `dev/race-standings` and writes JMS-envelope-wrapped messages to a new `race-standings-raw` Kafka topic.

```bash
confluent connect cluster create \
  --config-file generated/mq_connector_config.json \
  --environment $(cd terraform/core && terraform output -raw environment_id) \
  --cluster $(cd terraform/core && terraform output -raw cluster_id)
```

Wait for the connector to reach `RUNNING` status:

```bash
confluent connect cluster list \
  --environment $(cd terraform/core && terraform output -raw environment_id) \
  --cluster $(cd terraform/core && terraform output -raw cluster_id)
```

**What this creates:** A new `race-standings-raw` topic with the JMS envelope schema (fields: `text`, `bytes`, `properties`, `messageType`, etc.). The raw JSON payload is inside the `text` field.

---

## 4. Start the Race

```bash
./scripts/start-race.sh
```

This launches the race simulator on ECS Fargate. The simulator runs a 57-lap race:
- Produces **car telemetry** (car #44 only) directly to Kafka via Avro (`car-telemetry` topic, ~5 readings per lap)
- Produces **race standings** (all 22 cars) to IBM MQ as JMS TextMessages (22 messages per lap)

### Monitor the race

```bash
aws logs tail /ecs/f1-simulator --follow
```

You should see output like:

```
=== LAP 1/57 ===
Published 22 standings to MQ
Published 5 telemetry readings to Kafka
Lap 1 complete (10.0s)
```

### Verify data is flowing

In the **Confluent Cloud UI**, check:
- `car-telemetry` topic — messages arriving (Avro format, keyed by `car_number`)
- `race-standings-raw` topic — messages arriving (JMS envelope with JSON in `text` field)

---

## 5. Deploy Flink Job 0: Parse Race Standings

Open the **Flink SQL Workspace** in Confluent Cloud. Set your catalog and database:

```sql
USE CATALOG `f1-demo-env`;
USE `f1-demo-cluster`;
```

Run Job 0 to extract structured fields from the JMS envelope and write to the clean `race-standings` topic:

```sql
INSERT INTO `race-standings`
SELECT
  CAST(JSON_VALUE(`text`, '$.car_number') AS INT) AS `car_number`,
  JSON_VALUE(`text`, '$.driver') AS `driver`,
  JSON_VALUE(`text`, '$.team') AS `team`,
  CAST(JSON_VALUE(`text`, '$.lap') AS INT) AS `lap`,
  CAST(JSON_VALUE(`text`, '$.position') AS INT) AS `position`,
  CAST(JSON_VALUE(`text`, '$.gap_to_leader_sec') AS DOUBLE) AS `gap_to_leader_sec`,
  CAST(JSON_VALUE(`text`, '$.gap_to_ahead_sec') AS DOUBLE) AS `gap_to_ahead_sec`,
  CAST(JSON_VALUE(`text`, '$.last_lap_time_sec') AS DOUBLE) AS `last_lap_time_sec`,
  CAST(JSON_VALUE(`text`, '$.pit_stops') AS INT) AS `pit_stops`,
  JSON_VALUE(`text`, '$.tire_compound') AS `tire_compound`,
  CAST(JSON_VALUE(`text`, '$.tire_age_laps') AS INT) AS `tire_age_laps`,
  CAST(JSON_VALUE(`text`, '$.in_pit_lane' RETURNING BOOLEAN) AS BOOLEAN) AS `in_pit_lane`,
  TO_TIMESTAMP_LTZ(CAST(JSON_VALUE(`text`, '$.event_time') AS BIGINT), 3) AS `event_time`
FROM `race-standings-raw`;
```

### Verify

Query the clean `race-standings` topic to see parsed data:

```sql
SELECT * FROM `race-standings`;
```

You should see rows with structured fields: `car_number`, `driver`, `team`, `position`, `gap_to_leader_sec`, etc. for all 22 cars.

---

## 6. Deploy the CDC Connector (Drivers)

The CDC connector streams the 22-driver reference table from Postgres to Kafka. The config is auto-generated by Terraform with real values at deploy time.

```bash
confluent connect cluster create \
  --config-file generated/cdc_connector_config.json \
  --environment $(cd terraform/core && terraform output -raw environment_id) \
  --cluster $(cd terraform/core && terraform output -raw cluster_id)
```

**What this creates:** A `race_results` topic with 198 records (22 drivers × 9 prior GPs), each with starting/finishing positions, pit_stops, and tire stints. Composite primary key `(race_id, car_number)`.

---

## 7. Deploy Flink Job 1: Enrichment + Anomaly Detection

This is the core intelligence layer. It:

1. **Tumbles** car telemetry into 10-second windows (1 record per lap)
2. **Detects anomalies** on all 11 sensor metrics using `AI_DETECT_ANOMALIES`
3. **Temporal joins** with `race-standings` to add position, gaps, pit status, and tire context

Run in the Flink SQL Workspace:

```sql
CREATE TABLE `car-state` AS
WITH enriched AS (s
    SELECT
      t.car_number, t.event_time, t.lap,
      t.tire_temp_fl_c, t.tire_temp_fr_c, t.tire_temp_rl_c, t.tire_temp_rr_c,
      t.tire_pressure_fl_psi, t.tire_pressure_fr_psi,
      t.tire_pressure_rl_psi, t.tire_pressure_rr_psi,
      t.engine_temp_c, t.brake_temp_fl_c, t.brake_temp_fr_c,
      t.battery_charge_pct, t.fuel_remaining_kg,
      r.`position`, r.gap_to_ahead_sec, r.gap_to_leader_sec,
      r.pit_stops, r.tire_compound, r.tire_age_laps
    FROM `car-telemetry` t
    JOIN `race-standings` FOR SYSTEM_TIME AS OF t.event_time AS r
      ON t.car_number = r.car_number
  ),
  windowed AS (
    SELECT
      window_start, window_end, window_time, car_number,
      MAX(lap) AS lap,
      AVG(tire_temp_fl_c) AS tire_temp_fl_c,
      AVG(tire_temp_fr_c) AS tire_temp_fr_c,
      AVG(tire_temp_rl_c) AS tire_temp_rl_c,
      AVG(tire_temp_rr_c) AS tire_temp_rr_c,
      AVG(tire_pressure_fl_psi) AS tire_pressure_fl_psi,
      AVG(tire_pressure_fr_psi) AS tire_pressure_fr_psi,
      AVG(tire_pressure_rl_psi) AS tire_pressure_rl_psi,
      AVG(tire_pressure_rr_psi) AS tire_pressure_rr_psi,
      AVG(engine_temp_c) AS engine_temp_c,
      AVG(brake_temp_fl_c) AS brake_temp_fl_c,
      AVG(brake_temp_fr_c) AS brake_temp_fr_c,
      AVG(battery_charge_pct) AS battery_charge_pct,
      AVG(fuel_remaining_kg) AS fuel_remaining_kg,
      MAX(`position`) AS `position`,
      MAX(gap_to_ahead_sec) AS gap_to_ahead_sec,
      MAX(gap_to_leader_sec) AS gap_to_leader_sec,
      MAX(pit_stops) AS pit_stops,
      MAX(tire_compound) AS tire_compound,
      MAX(tire_age_laps) AS tire_age_laps
    FROM TABLE(
      TUMBLE(TABLE enriched, DESCRIPTOR(event_time), INTERVAL '10' SECOND)
    )
    GROUP BY window_start, window_end, window_time, car_number
  ),
  anomaly AS (
    SELECT
      *,
      ML_DETECT_ANOMALIES(tire_temp_fl_c, window_time,
        JSON_OBJECT('upperBoundConfidencePercentage' VALUE 99.99,
                    'lowerBoundConfidencePercentage' VALUE 99.99,
                    'minContextSize' VALUE 30,
                    'maxContextSize' VALUE 200))
        OVER (PARTITION BY car_number ORDER BY window_time RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        AS anomaly_tire_temp_fl_result
    FROM windowed
  )
  SELECT
    car_number, lap,
    tire_temp_fl_c, tire_temp_fr_c, tire_temp_rl_c, tire_temp_rr_c,
    tire_pressure_fl_psi, tire_pressure_fr_psi,
    tire_pressure_rl_psi, tire_pressure_rr_psi,
    engine_temp_c, brake_temp_fl_c, brake_temp_fr_c,
    battery_charge_pct, fuel_remaining_kg,
    CASE
      WHEN anomaly_tire_temp_fl_result.is_anomaly
           AND anomaly_tire_temp_fl_result.actual_value > anomaly_tire_temp_fl_result.upper_bound
      THEN true
      ELSE false
    END AS anomaly_tire_temp_fl,
    `position`, gap_to_ahead_sec, gap_to_leader_sec,
    pit_stops, tire_compound, tire_age_laps
  FROM anomaly;
```

> Leave this running as a continuous Flink job.

### Verify

```sql
SELECT car_number, lap, `position`, tire_compound, tire_age_laps,
       anomaly_tire_temp_fl, tire_temp_fl_c
FROM `car-state`;
```

You should see one record per lap. Around **lap 32**, `anomaly_tire_temp_fl` will flip to `true` and `tire_temp_fl_c` will spike to ~145C.

---

## 8. Deploy Flink Job 2: Streaming Agent

> [!WARNING]
>
> **Not yet fully automated.** The streaming agent SQL (`demo-reference/streaming_agent.sql`) has placeholder values for connection endpoints and API keys that must be filled in manually. The connection configuration depends on your LLM provider and RTCE setup, which vary per environment.
>
> **TODO:** Automate the agent deployment — either via Terraform (once CREATE AGENT is supported as a `confluent_flink_statement` resource) or by templating the SQL with Terraform outputs. The placeholders that need values are:
> - `ai_connection` endpoint and API key (your OpenAI-compatible LLM provider)
> - `rtce_connection` endpoint (your Confluent RTCE endpoint)

Once you have the connection details, run each statement in the Flink SQL Workspace:

```sql
-- 1. AI Connection
CREATE CONNECTION ai_connection
  WITH ('type' = 'openai', 'endpoint' = '<YOUR_LLM_ENDPOINT>', 'api-key' = '<YOUR_API_KEY>');

-- 2. Model
CREATE MODEL pit_strategy_model
  USING CONNECTION ai_connection;

-- 3. RTCE Connection (for competitor standings)
CREATE CONNECTION rtce_connection
  WITH ('type' = 'mcp_server', 'endpoint' = '<YOUR_RTCE_ENDPOINT>', 'transport-type' = 'STREAMABLE_HTTP');

-- 4. RTCE Tool
CREATE TOOL race_standings_tool
  USING CONNECTION rtce_connection
  WITH (
    'type' = 'mcp',
    'description' = 'Look up current race standings for any car by car_number. Returns position, gap, pit stops, tire compound, and tire age.'
  );

-- 5. Agent
CREATE AGENT pit_strategy_agent
  USING MODEL pit_strategy_model
  USING PROMPT 'You are an F1 pit wall strategist for River Racing. Analyze car state data and recommend pit strategy.

For each lap, evaluate:
1. Anomaly flags - any sensor showing anomalous behavior
2. Tire condition - compound, age, temperatures
3. Race position and gaps to competitors
4. Use the race_standings_tool to check what competitors are doing

Respond with:
- suggestion: PIT NOW, PIT SOON, or STAY OUT
- condition_summary: brief description of car condition
- race_context: current race situation
- recommended_tire_compound: SOFT, MEDIUM, or HARD (null if STAY OUT)
- recommended_stint_laps: expected laps on new tires (null if STAY OUT)
- recommended_reason: why this compound (null if STAY OUT)
- reasoning: full explanation of your decision'
  USING TOOLS race_standings_tool
  WITH ('max_iterations' = '5');

-- 6. Agent invocation — processes every car-state record
INSERT INTO `pit-decisions`
SELECT
  car_number, lap, position,
  tire_compound AS tire_compound_current,
  AI_RUN_AGENT(pit_strategy_agent, *)
FROM `car-state`;
```

### What to expect

The agent produces one `pit-decisions` record per lap. Watch for the key moment:

| Lap | Position | Suggestion | What's happening |
|-----|----------|-----------|------------------|
| 1-15 | P3 | STAY OUT | Competitive, stable, good pace |
| 16-25 | P3-P5 | STAY OUT | Tires wearing, pace dropping |
| 26-31 | P5-P8 | PIT SOON | Tires falling off, losing positions |
| **32** | **P8** | **PIT NOW** | **Front-left tire anomaly at 145C. This is the key moment.** |
| 33 | P12 | STAY OUT | In pit lane, fresh mediums |
| 34-42 | P12-P5 | STAY OUT | Fastest car on track, overtaking |
| 43-57 | P5-P3 | STAY OUT | Competitors pit on worn tires, James jumps them |

**Net result: P8 at agent's call -> P3 at finish = +5 positions gained.**

### Verify

```sql
SELECT car_number, lap, position, suggestion, condition_summary, reasoning
FROM `pit-decisions`
WHERE suggestion <> 'STAY OUT';
```

---

## 9. Enable Tableflow

In the **Confluent Cloud UI**:

1. Go to your environment -> cluster -> Topics
2. Select `pit-decisions` -> **Tableflow** tab -> **Enable**
   - Format: **Delta Lake**
   - Storage: **BYOS** (Bring Your Own Storage)
   - Provider integration: select `f1-demo-aws-integration`
3. Repeat for `race_results`

These topics are now continuously materialized as Delta Lake tables in S3.

---

## 10. Query with Databricks Genie

### Set up external tables in Databricks

In Databricks Unity Catalog, create external tables pointing to the Tableflow S3 location:

```sql
CREATE TABLE f1_demo.pit_decisions
  USING DELTA
  LOCATION 's3://f1-demo-tableflow-<hex>/topics/pit-decisions/';

CREATE TABLE f1_demo.race_results
  USING DELTA
  LOCATION 's3://f1-demo-tableflow-<hex>/topics/race_results/';
```

### Create a Genie Space

1. In Databricks, go to **Genie** -> **Create Space**
2. Name: `F1 Pit Strategy Analytics`
3. Add tables: `f1_demo.pit_decisions`, `f1_demo.race_results`
4. Description: *"Analyze F1 pit strategy decisions made by River Racing's AI agent during the Silverstone Grand Prix."*

### Ask Genie

**Question 1 — Position gain:**

> *"How many positions did we gain after following the agent's recommendation?"*

Expected answer: **+5 positions** (P8 at lap 32 -> P3 at finish).

**Question 2 — Tire compound split:**

> *"What percentage of the race did our driver spend on each tire compound?"*

Expected answer: Pie chart — **SOFT 56%** (32 laps) / **MEDIUM 44%** (25 laps).

---

## Stop the Race

```bash
./scripts/stop-race.sh
```

## Teardown

```bash
uv run destroy
```

This destroys demo resources first, then core infrastructure. Confirm when prompted.

---

## Troubleshooting

<details>
<summary>MQ connector stuck in PROVISIONING</summary>

The MQ EC2 instance may not be ready yet. Wait 2-3 minutes for the Docker container to start, then check the MQ console at `https://<mq_ip>:9443/ibmmq/console` (admin/passw0rd). If the page loads, the connector should provision successfully.

</details>

<details>
<summary>race-standings-raw has messages but race-standings is empty</summary>

Make sure Job 0 (parse standings) is running. Check the Flink statement status in the SQL Workspace — it should show `RUNNING`. If it failed, the most common cause is running the query before `race-standings-raw` exists (deploy the MQ connector first).

</details>

<details>
<summary>car-state has no records</summary>

Job 1 needs both `car-telemetry` AND `race-standings` to have data with advancing watermarks. Check:
- Is the race simulator running? (`aws ecs list-tasks --cluster <cluster-name>`)
- Is Job 0 running? (produces to `race-standings`)
- Are both topics receiving data? (check in the Confluent Cloud UI)

The temporal join requires watermarks to advance on both sides before producing output.

</details>

<details>
<summary>No anomaly at lap 32</summary>

The anomaly fires only on `tire_temp_fl_c` (front-left tire temperature) when it spikes to ~145C. Check:
- `AI_DETECT_ANOMALIES` needs enough context (20+ data points) before it can detect anomalies — it won't fire on very early laps.
- Verify the spike exists: `SELECT lap, tire_temp_fl_c FROM car-state WHERE lap BETWEEN 30 AND 34`

</details>

<details>
<summary>Docker build fails during deployment</summary>

The ECS module builds a Docker image with `--platform linux/amd64` (required for Fargate). If the build fails:
- Make sure Docker Desktop is running
- Check that the IBM MQ C client download URL is accessible
- Try `terraform -chdir=terraform/demo taint module.ecs.null_resource.docker_build_push` then re-run `uv run deploy`

</details>

---

## What's Not Yet Built

The following items are planned but not yet implemented:

- [ ] **Automate streaming agent deployment** — The agent SQL (`streaming_agent.sql`) has placeholder connection endpoints/API keys. Needs templating from Terraform outputs or a dedicated setup script.
- [ ] **LLM connections in core Terraform** — `CREATE CONNECTION` and `CREATE MODEL` statements for the AI provider should be in `terraform/core/` so they're reusable and not manually entered each time.
- [ ] **`uv run start-race` / `uv run stop-race`** — Replace the bash scripts with Python entry points for consistency.
- [ ] **Automate connector deployment** — Add `uv run deploy-connectors` to deploy both MQ and CDC connectors with real values.
- [ ] **Automate Flink job deployment** — Add `uv run deploy-jobs` to submit all three Flink SQL jobs via the Confluent CLI.
- [ ] **DBT integration** — Job 1 (enrichment + anomaly detection) is currently raw Flink SQL. Planned to use `dbt-confluent` with `streaming_table` materialization.
- [ ] **Repo cleanup** — Remove `docs/superpowers/`, simplify directory structure, consolidate reference files.

## Navigation

- **Infrastructure details**: [CLAUDE.md](CLAUDE.md)
- **Tableflow setup**: [docs/SETUP-TABLEFLOW.md](docs/SETUP-TABLEFLOW.md)
- **Genie setup**: [docs/SETUP-GENIE.md](docs/SETUP-GENIE.md)
- **Implementation plan**: [PLAN.md](PLAN.md)
