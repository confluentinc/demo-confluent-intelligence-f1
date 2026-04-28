# F1 Pit Wall AI Demo — River Racing at Silverstone

Real-time AI pit strategy system for a Formula 1 team. An AI agent monitors live car telemetry, detects anomalies, and recommends pit stop strategy — all powered by Confluent Cloud.

## Architecture

```
Car Sensors ──→ Direct to Kafka ──→ car-telemetry ──┐
                                                     ├──→ Flink Job 1: Enrichment + Anomaly Detection ──→ car-state
FIA Timing  ──→ IBM MQ ──→ MQ Connector ──→ race-standings-raw ──→ Flink Job 0: Parse + Key ──→ race-standings ──┘
                                                                                                        │
Postgres    ──→ CDC Debezium ──→ race_results ──→ Tableflow                          Flink Job 2: Streaming Agent
                                                                                                        │
                                                                                                        ▼
                                                                                     pit-decisions ──→ Tableflow ──→ Databricks Genie
```

## Quick Start

### Prerequisites

- Confluent Cloud account with API key/secret
- AWS credentials configured (`~/.aws/credentials` or env vars)
- Terraform >= 1.3
- Docker (for building the race simulator image)
- Confluent CLI

### 1. Deploy Infrastructure

```bash
# Configure credentials
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
# Edit terraform.tfvars with your values

# Deploy
cd terraform
terraform init
terraform apply
```

This creates:
- Confluent Cloud environment, Kafka cluster, Flink compute pool
- `car-telemetry` and `race-standings` topics (Flink CREATE TABLE with schemas)
- EC2 with IBM MQ (queue: DEV.QUEUE.1)
- EC2 with Postgres (22 fictional drivers pre-loaded)
- ECS Fargate task definition (race simulator Docker image)
- S3 bucket + IAM role for Tableflow
- Auto-generated connector config at `generated/mq_connector_config.json`

### 2. Deploy MQ Source Connector

```bash
confluent connect cluster create \
  --config-file generated/mq_connector_config.json \
  --environment $(terraform output -raw environment_id) \
  --cluster $(terraform output -raw cluster_id)
```

This creates the `race-standings-raw` topic. The connector reads from the MQ queue and writes JMS-wrapped messages to Kafka.

Wait for the connector to show `RUNNING`:

```bash
confluent connect cluster describe <CONNECTOR_ID> \
  --environment $(terraform output -raw environment_id) \
  --cluster $(terraform output -raw cluster_id)
```

### 3. Start the Race

```bash
./scripts/start-race.sh
```

This launches the race simulator on ECS Fargate. It runs a 57-lap race in ~9.5 minutes:
- Produces car telemetry (car #44) directly to Kafka (`car-telemetry`)
- Produces race standings (all 22 cars) to IBM MQ as JMS TextMessages

Monitor the race:

```bash
aws logs tail /ecs/f1-simulator --follow
```

### 4. Deploy Flink Jobs

All Flink SQL is run in the Confluent Cloud Flink SQL workspace. Set the catalog and database first:

```sql
USE CATALOG `<environment-name>`;
USE `<cluster-name>`;
```

#### Job 0: Parse Race Standings (extract from JMS envelope + set key)

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

#### Job 1: Enrichment + Anomaly Detection

Joins car telemetry with race standings (temporal join on `event_time`), tumbles into 10-second windows, runs `AI_DETECT_ANOMALIES` on `tire_temp_fl_c`, and emits the windowed snapshot enriched with race context. The CASE filter keeps only spikes above the upper bound — post-pit drops are not flagged as anomalies.

> **Why only `tire_temp_fl_c`?** The simulator's other metrics carry too much noise (brake ±25°C, battery cycle, etc.) and produce false anomalies that distract from the demo narrative. The hero anomaly at lap 32 is the front-left tire overheating to ~145°C — that's the entire story.
>
> **Why is the temporal join in the first CTE, not at the end?** After multiple OVER aggregations, `window_time` loses its rowtime attribute and `FOR SYSTEM_TIME AS OF` silently emits zero rows. Joining on raw `event_time` keeps the rowtime clean.

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
      OVER (PARTITION BY car_number
            ORDER BY window_time
            RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
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
         AND anomaly_tire_temp_fl_result.actual_value
             > anomaly_tire_temp_fl_result.upper_bound
    THEN true
    ELSE false
  END AS anomaly_tire_temp_fl,
  `position`, gap_to_ahead_sec, gap_to_leader_sec,
  pit_stops, tire_compound, tire_age_laps
FROM anomaly;
```

#### Job 2: Streaming Agent

```sql
-- 1. AI Connection
CREATE CONNECTION ai_connection
  WITH ('type' = 'openai', 'endpoint' = '...', 'api-key' = '...');

-- 2. Model
CREATE MODEL pit_strategy_model
  USING CONNECTION ai_connection;

-- 3. RTCE Connection (for competitor standings)
CREATE CONNECTION rtce_connection
  WITH ('type' = 'mcp_server', 'endpoint' = '...', 'transport-type' = 'STREAMABLE_HTTP');

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

-- 6. Agent invocation
INSERT INTO `pit-decisions`
SELECT
  car_number, lap, position,
  tire_compound AS tire_compound_current,
  AI_RUN_AGENT(pit_strategy_agent, *)
FROM `car-state`;
```

### 5. Enable Tableflow

In the Confluent Cloud UI, enable Tableflow on:
- `pit-decisions` — agent output (Delta Lake format, BYOS to S3)
- `race_results` — historical season data, 198 rows (Delta Lake format, BYOS to S3)

### 6. Query with Databricks Genie

Connect Databricks Unity Catalog to the Tableflow Delta Lake tables, then ask Genie:

- *"How many positions did we gain after following the agent's recommendation?"*
- *"What percentage of the race did our driver spend on each tire compound?"*

### Stop the Race

```bash
./scripts/stop-race.sh
```

### Teardown

```bash
./scripts/teardown.sh
```

## Demo Scenario

**Team:** River Racing | **Driver:** James River (#44) | **Circuit:** Silverstone | **Laps:** 57 (10s each)

| Phase | Laps | Position | Tire | What Happens |
|---|---|---|---|---|
| Competitive | 1-15 | P3 | SOFT | Stable, good pace |
| Leads | 18-27 | P2-P1 | SOFT | Others pit, James leads |
| Tires die | 28-32 | P1-P8 | SOFT | Degradation, cars pass |
| **Anomaly** | **32** | **P8** | **SOFT** | **tire_temp_fl spikes to 145C. Agent: PIT NOW** |
| Pit stop | 33 | P12 | MEDIUM | Fresh tires, drops 4 spots |
| Recovery | 34-57 | P12-P3 | MEDIUM | Fastest on track, climbs back |

**Result: P8 at pit call -> P3 at finish = +5 positions gained**

## Documentation

- [Design Doc](CLAUDE.md) — Full architecture, schemas, constraints
- [DBT Adapter Setup](docs/SETUP-DBT-ADAPTER.md)
- [Tableflow Setup](docs/SETUP-TABLEFLOW.md)
- [Databricks Genie Setup](docs/SETUP-GENIE.md)
