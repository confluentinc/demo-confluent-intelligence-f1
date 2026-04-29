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

Region is hardcoded to `us-east-1`. Resource names are auto-generated with a unique suffix (e.g., `f1-demo-a1b2c3d4`).

The deploy creates two Terraform stacks:

**Core** (Confluent Cloud):
- Environment, Kafka cluster, Schema Registry
- Flink compute pool + API keys
- Service account with EnvironmentAdmin

**Demo** (AWS + Flink tables + connectors):
- `car-telemetry`, `race-standings`, and `race-standings-raw` topics (Flink CREATE TABLE with schemas, watermarks, primary keys)
- MQ Source Connector (`f1-mq-source`) — subscribes to IBM MQ and writes to `race-standings-raw`
- CDC Connector (`f1-postgres-cdc`) — streams `race_results` from Postgres to Kafka
- EC2 with IBM MQ (pub/sub topic: `dev/race-standings`, durable subscription)
- EC2 with Postgres (198 historical race_results rows pre-loaded — 22 drivers × 9 prior GPs)
- ECS Fargate task definition (race simulator Docker image)
- S3 bucket + IAM role for Tableflow
- Auto-generated connector configs at `generated/mq_connector_config.json` and `generated/cdc_connector_config.json`

Deployment takes 15-20 minutes (mostly ECR image build + Flink compute pool provisioning + waiting for EC2 services to be ready before connectors are validated).

### Verify deployment

After deployment completes, open the **Confluent Cloud UI** and confirm:
- Environment exists (named `f1-demo-env`)
- Kafka cluster exists with `car-telemetry`, `race-standings`, and `race-standings-raw` topics
- Flink compute pool is provisioned
- Two connectors (`f1-mq-source`, `f1-postgres-cdc`) are in `RUNNING` state

---

## 2. Set up Confluent MCP for Claude Code (optional)

```bash
uv run setup-mcp
```

This reads Terraform core outputs and registers a Confluent MCP server with Claude Code. Restart Claude Code after running this.

---

## 3. Start the Race

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
- `car-telemetry` topic — messages arriving (Avro format)
- `race-standings-raw` topic — messages arriving (JMS envelope with JSON in `text` field)

---

## 4. Deploy Flink Job 0: Parse Race Standings

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

## 5. Deploy Flink Job 1: Enrichment + Anomaly Detection

This is the core intelligence layer. It:

1. **Tumbles** car telemetry into 10-second windows (1 record per lap)
2. **Temporal joins** with `race-standings` to add position, gaps, pit status, and tire context
3. **Detects anomalies** on front-left tire temperature using `AI_DETECT_ANOMALIES`

Run in the Flink SQL Workspace:

```sql
CREATE TABLE `car-state` (
  PRIMARY KEY (car_number) NOT ENFORCED
)
WITH ('changelog.mode' = 'append')
AS
WITH enriched AS (
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
      AI_DETECT_ANOMALIES(tire_temp_fl_c, window_time,
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

## 6. Deploy Flink Job 2: Streaming Agent

`llm_textgen_model` is already deployed by Terraform — no connection or model setup needed. Run the two statements below sequentially in the Flink SQL Workspace.

> [!NOTE]
>
> **RTCE** (Real-Time Context Engine) for live tool-based standings lookup is not yet active. Competitor standings are provided instead via a direct JOIN with `race-standings` in the CREATE TABLE statement. The RTCE connection and tool are included in `demo-reference/streaming_agent.sql` as commented-out stubs for when RTCE is enabled.

```sql
-- 1. Pit Strategy Agent
--    Competitor standings are passed as structured text in each input message.
--    (When RTCE is enabled, uncomment USING TOOLS and update the prompt.)
CREATE AGENT `pit_strategy_agent`
USING MODEL `llm_textgen_model`
USING PROMPT 'OUTPUT FORMAT — respond with exactly these 7 labeled lines in this order. No markdown, no asterisks, no bold, plain text only.

Suggestion: [PIT NOW | PIT SOON | STAY OUT]
Condition Summary: [one sentence describing current car condition]
Race Context: [one sentence on race situation based on competitor standings in the input]
Recommended Compound: [SOFT | MEDIUM | HARD | N/A if STAY OUT]
Recommended Stint Laps: [integer expected laps on new tires | N/A if STAY OUT]
Recommended Reason: [one sentence explaining compound choice | N/A if STAY OUT]
Reasoning: [2-4 sentences full explanation of your decision]

Correct STAY OUT example:
Suggestion: STAY OUT
Condition Summary: Front-left tire temperature is nominal at 107C with 18 laps of age on SOFT compound.
Race Context: Currently P3. No competitors in top 10 have pitted yet. Leader is 8.2s ahead.
Recommended Compound: N/A
Recommended Stint Laps: N/A
Recommended Reason: N/A
Reasoning: Tire temps and pressures are within normal operating windows for a SOFT at this age. Track position P3 is strong. Pitting now would surrender 4-6 seconds and drop James behind cars currently behind us.

Correct PIT NOW example:
Suggestion: PIT NOW
Condition Summary: Front-left tire temperature anomaly at 145C, 20C above expected upper bound — failure risk imminent.
Race Context: Currently P8. P4 and P5 already pitted 3 laps ago and are pushing on fresh mediums.
Recommended Compound: MEDIUM
Recommended Stint Laps: 25
Recommended Reason: Mediums will last the remaining 25 laps and give James the pace to recover positions lost during the stop.
Reasoning: The FL anomaly flag indicates the SOFT has gone past its operating limit with blowout risk. Pitting now onto mediums avoids tire failure. Based on historical data, James averages +2.75 positions on SOFT-MEDIUM — this is his strongest strategy.

---

You are the AI pit wall strategist for River Racing at the 2026 British Grand Prix (Silverstone, 57 laps).
Driver: James River, Car #44.

DECISION RULES:

PIT NOW — pit this lap (urgent):
- anomaly_tire_temp_fl = true (tire anomaly detected, failure risk imminent — act immediately)
- tire_age_laps > 35 on SOFT compound (tires are past the cliff, no pace recoverable)

PIT SOON — pit within the next 1-3 laps (strategic):
- tire_age_laps > 28 on SOFT with temperatures rising and positions falling
- Car directly ahead just pitted onto fresher rubber (undercut window closing)
- Tire temperatures consistently 10C above expected range for compound and age

STAY OUT — continue on current tires:
- Tire temps and pressures are nominal for compound type and age
- Tire age is within the expected stint window
- Current track position is strong and pitting would surrender too many places

COMPETITOR CONTEXT:
Current top-10 standings are provided at the end of each input. Use them to identify:
- Which competitors have already pitted (and are now on fresher rubber)
- Who is still on old tires and likely to pit soon
- Whether James is at risk of being undercut, or has an overcut opportunity

TIRE STRATEGY at Silverstone:
- SOFT: fast but degrades, ideal as a first stint (~20-30 laps)
- MEDIUM: balanced choice, best after a SOFT first stint (~25-35 laps), allows 1-stop strategy
- HARD: very durable but slow, only consider if more than 40 laps remain at the second stop
- James River historical best: SOFT then MEDIUM (1-stop) averages +2.75 positions gained over 4 prior races

REMINDER: For any STAY OUT decision, write N/A for Recommended Compound, Recommended Stint Laps, and Recommended Reason.'
WITH ('max_iterations' = '10');
```

```sql
-- 2. Create pit-decisions table — one record per lap, driven by car-state.
--
-- competitor_grid aggregates the top-10 race-standings rows per lap into a
-- single string passed to the agent in lieu of an RTCE tool call.
--
-- REGEXP_EXTRACT uses \*{0,2} around each label to tolerate optional markdown
-- bold markers (**Label:**) that some LLMs emit despite being instructed otherwise.
--
-- raw_response preserves the full agent output for debugging when parsed fields are null.

SET 'sql.state-ttl' = '1 d';

CREATE TABLE `pit-decisions` (
  PRIMARY KEY (car_number) NOT ENFORCED
)
WITH ('changelog.mode' = 'append')
AS
WITH competitor_grid AS (
  SELECT
    lap,
    LISTAGG(
      CONCAT(
        'P', CAST(`position` AS STRING), ': Car #', CAST(car_number AS STRING),
        ' (', driver, ') — ', tire_compound, ' age=', CAST(tire_age_laps AS STRING),
        ' laps, ', CAST(pit_stops AS STRING), ' stop(s)',
        CASE WHEN in_pit_lane THEN ' [IN PIT LANE]' ELSE '' END
      ),
      '\n'
    ) AS standings_top10
  FROM `race-standings`
  WHERE `position` <= 10
  GROUP BY lap
)
SELECT
  cs.car_number,
  cs.lap,
  cs.position,
  cs.tire_compound AS tire_compound_current,
  TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Suggestion:\*{0,2}\s*([A-Z ]+)', 1)) AS suggestion,
  TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Condition Summary:\*{0,2}\s*([^\n]+)', 1)) AS condition_summary,
  TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Race Context:\*{0,2}\s*([^\n]+)', 1)) AS race_context,
  NULLIF(TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Recommended Compound:\*{0,2}\s*([^\n]+)', 1)), 'N/A') AS recommended_tire_compound,
  CAST(NULLIF(TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Recommended Stint Laps:\*{0,2}\s*([^\n]+)', 1)), 'N/A') AS INT) AS recommended_stint_laps,
  NULLIF(TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Recommended Reason:\*{0,2}\s*([^\n]+)', 1)), 'N/A') AS recommended_reason,
  TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Reasoning:\*{0,2}\s*([\s\S]+?)$', 1)) AS reasoning,
  CAST(CURRENT_TIMESTAMP AS STRING) AS `timestamp`,
  CAST(response AS STRING) AS raw_response
FROM `car-state` cs
JOIN competitor_grid cg ON cs.lap = cg.lap,
LATERAL TABLE(AI_RUN_AGENT(
  `pit_strategy_agent`,
  CONCAT(
    'CAR STATE — Lap ', CAST(cs.lap AS STRING), ' of 57 | Silverstone British Grand Prix\n',
    'Driver: James River (#', CAST(cs.car_number AS STRING), ') | Current Position: P', CAST(cs.position AS STRING), '\n',
    '\nTIRE DATA:\n',
    '  Compound: ', cs.tire_compound, ' | Age: ', CAST(cs.tire_age_laps AS STRING), ' laps\n',
    '  FL Temp: ', CAST(ROUND(cs.tire_temp_fl_c, 1) AS STRING), 'C',
    '  FR: ', CAST(ROUND(cs.tire_temp_fr_c, 1) AS STRING), 'C',
    '  RL: ', CAST(ROUND(cs.tire_temp_rl_c, 1) AS STRING), 'C',
    '  RR: ', CAST(ROUND(cs.tire_temp_rr_c, 1) AS STRING), 'C\n',
    '  FL Pressure: ', CAST(ROUND(cs.tire_pressure_fl_psi, 1) AS STRING), 'psi',
    '  FR: ', CAST(ROUND(cs.tire_pressure_fr_psi, 1) AS STRING), 'psi',
    '  RL: ', CAST(ROUND(cs.tire_pressure_rl_psi, 1) AS STRING), 'psi',
    '  RR: ', CAST(ROUND(cs.tire_pressure_rr_psi, 1) AS STRING), 'psi\n',
    '  FL Tire Anomaly Detected: ', CAST(cs.anomaly_tire_temp_fl AS STRING), '\n',
    '\nCAR SYSTEMS:\n',
    '  Engine Temp: ', CAST(ROUND(cs.engine_temp_c, 1) AS STRING), 'C',
    '  Brake FL: ', CAST(ROUND(cs.brake_temp_fl_c, 1) AS STRING), 'C',
    '  Brake FR: ', CAST(ROUND(cs.brake_temp_fr_c, 1) AS STRING), 'C\n',
    '  Battery: ', CAST(ROUND(cs.battery_charge_pct, 1) AS STRING), '%',
    '  Fuel Remaining: ', CAST(ROUND(cs.fuel_remaining_kg, 1) AS STRING), 'kg\n',
    '\nRACE CONTEXT:\n',
    '  Gap to Leader: ', CAST(ROUND(cs.gap_to_leader_sec, 2) AS STRING), 's',
    '  Gap to Car Ahead: ', CAST(ROUND(cs.gap_to_ahead_sec, 2) AS STRING), 's\n',
    '  Pit Stops Taken: ', CAST(cs.pit_stops AS STRING), '\n',
    '  Laps Remaining: ', CAST(57 - cs.lap AS STRING), '\n',
    '\nCOMPETITOR STANDINGS (Top 10):\n', cg.standings_top10
  ),
  MAP['debug', 'true']
));
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

## 7. Enable Tableflow

In the **Confluent Cloud UI**:

1. Go to your environment -> cluster -> Topics
2. Select `pit-decisions` -> **Tableflow** tab -> **Enable**
   - Format: **Delta Lake**
   - Storage: **BYOS** (Bring Your Own Storage)
   - Provider integration: select `f1-demo-aws-integration`
3. Repeat for `race_results`

These topics are now continuously materialized as Delta Lake tables in S3.

---

## 8. Query with Databricks Genie

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
<summary>A connector is FAILED after deployment</summary>

The MQ and CDC connectors are deployed by Terraform and include a readiness waiter that polls the EC2 ports before provisioning. If a connector still fails, check:
- The EC2 instance is running: `aws ec2 describe-instances --filters "Name=tag:Name,Values=f1-demo-*-mq"` (or `-postgres`)
- The service is listening: `nc -zv <ip> 1414` for MQ, `nc -zv <ip> 5432` for Postgres
- MQ web console at `https://<mq_ip>:9443/ibmmq/console` (admin/passw0rd) — if it loads, QM1 is up

To recreate a failed connector, run `terraform -chdir=terraform/demo apply` again after verifying the EC2 service is healthy.

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
- `AI_DETECT_ANOMALIES` needs at least 30 data points (`minContextSize=30`) before it can detect anomalies — it won't fire on early laps.
- Verify the spike exists: `SELECT lap, tire_temp_fl_c FROM \`car-state\` WHERE lap BETWEEN 30 AND 34`

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
- [ ] **Automate Flink job deployment** — Add `uv run deploy-jobs` to submit all three Flink SQL jobs via the Confluent CLI.
- [ ] **DBT integration** — Job 1 (enrichment + anomaly detection) is currently raw Flink SQL. Planned to use `dbt-confluent` with `streaming_table` materialization.
- [ ] **Repo cleanup** — Remove `docs/superpowers/`, simplify directory structure, consolidate reference files.

## Navigation

- **Infrastructure details**: [CLAUDE.md](CLAUDE.md)
- **Tableflow setup**: [docs/SETUP-TABLEFLOW.md](docs/SETUP-TABLEFLOW.md)
- **Genie setup**: [docs/SETUP-GENIE.md](docs/SETUP-GENIE.md)
- **Implementation plan**: [PLAN.md](PLAN.md)
