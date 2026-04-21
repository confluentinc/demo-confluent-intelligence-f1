# Project Context: F1 Pit Wall AI Demo — Silverstone Racing

## Project Overview

12-15 minute keynote demo for a Confluent conference (London). Real-time AI pit strategy system for an F1 team. An AI agent monitors live car telemetry and race standings, detects anomalies, and recommends pit stop strategy in real time.

**Team Name:** River Racing
**Driver:** James River (car #44)
**Circuit:** Silverstone (British Grand Prix)
**Audience:** Mixed technical (data engineers, architects, CTOs, business stakeholders)
**Delivery:** Pre-recorded video (edited screen recordings + voiceover) + GitHub repo
**GitHub:** `confluentinc/demo-confluent-intelligence-f1` (branch: `initial-codebase`)
**All drivers and teams are fictional.**

---

## Architecture (Final)

```
Car Sensors (internal)     ──→ Direct to Kafka            ──→ car-telemetry
                                                                   │
FIA Timing Feed (external) ──→ IBM MQ ──→ MQ Connector ──→ race-standings-raw
                                                                   │
                                                        Job 0: Parse + Key (Flink SQL)
                                                        - Extract JSON from JMS text
                                                        - Set car_number as key
                                                                   │
                                                            race-standings
                                                                   │
Driver Profiles            ──→ Postgres ──→ CDC Debezium ──→ drivers ──→ Tableflow ──→ Lakehouse
                                                                   │
                                          ┌────────────────────────┘
                                          ▼
                                Job 1: Enrichment + Anomaly Detection (Flink SQL / DBT)
                                - 10s tumbling window on car-telemetry
                                - AI_DETECT_ANOMALIES on all sensor metrics
                                - Temporal join with race-standings
                                          │
                                          ▼
                                      car-state (1 record per lap)
                                          │
                                          ▼
                                Job 2: Streaming Agent (Flink SQL)
                                - Reads car-state
                                - Uses RTCE for competitor standings
                                - AI_RUN_AGENT → pit decision + reasoning
                                          │
                                          ▼
                                    pit-decisions ──→ Tableflow ──→ Lakehouse
                                                                       │
                                                                  Databricks Genie
                                                            (natural language analytics)
```

### Key Architecture Choices

1. **Three data sources, three ingestion paths** — Car telemetry direct to Kafka (internal, modern), race standings via MQ queue (external FIA feed through legacy middleware), drivers via Postgres CDC (reference data).
2. **Three Flink jobs** — Job 0 parses JMS envelope + sets key. Job 1 enriches + detects anomalies. Job 2 runs the AI agent.
3. **MQ connector writes JMS envelope** — The Confluent MQ Source connector always wraps messages in a JMS envelope schema (with `text`, `bytes`, `properties` fields). A separate Flink job (Job 0) extracts the raw JSON from `text` and writes to a clean `race-standings` topic with `car_number` as key.
4. **JMS TextMessage via RFH2 headers** — The simulator sends messages to MQ using `put_rfh2()` with `<mcd><Msd>jms_text</Msd></mcd>` so the connector receives JMS TextMessages (payload in `text` field, not base64 in `bytes`).
5. **AI_DETECT_ANOMALIES on all metrics** — Monitors every sensor, but only tire_temp_fl_c fires an anomaly at lap 32.
6. **Agent decides everything** — No threshold formulas in Flink SQL. The agent classifies suggestion (PIT NOW / PIT SOON / STAY OUT) and provides reasoning.
7. **RTCE for competitor context** — Agent uses Real-Time Context Engine to look up competitor standings from `race-standings`.
8. **Tableflow on two topics** — `pit-decisions` and `drivers` materialize to Delta Lake for Genie analytics.
9. **10 seconds per lap** — Simulated race completes in ~9.5 minutes (57 laps).
10. **Auto-generated connector config** — Terraform generates `generated/mq_connector_config.json` with real values (MQ IP, API keys, service account ID). Deploy with `confluent connect cluster create --config-file`.

---

## Data Sources (3 Total)

### Data Source 1: Car Telemetry (Internal — Direct to Kafka)

Sensor readings from car #44 only. Race simulator produces directly to Kafka via `confluent-kafka[avro]` with Avro serialization. Every ~1-2 seconds (~5 readings per 10-second lap). Topic: `car-telemetry` (created by Terraform via Flink CREATE TABLE, 1 partition).

```json
{
  "car_number": 44, "lap": 32,
  "tire_temp_fl_c": 145.2, "tire_temp_fr_c": 109.1,
  "tire_temp_rl_c": 105.3, "tire_temp_rr_c": 104.7,
  "tire_pressure_fl_psi": 20.8, "tire_pressure_fr_psi": 21.0,
  "tire_pressure_rl_psi": 19.9, "tire_pressure_rr_psi": 19.8,
  "engine_temp_c": 120.1, "brake_temp_fl_c": 455.3, "brake_temp_fr_c": 462.1,
  "battery_charge_pct": 61.8, "fuel_remaining_kg": 38.2,
  "drs_active": false, "speed_kph": 312.4, "throttle_pct": 98.2, "brake_pct": 0.0,
  "event_time": 1718373723412
}
```

### Data Source 2: Race Standings (External — FIA via MQ)

Position, gaps, pit status, and tire info for ALL 22 cars. FIA feed arrives through IBM MQ (legacy middleware for external data). Simulator writes to MQ queue `DEV.QUEUE.1` as JMS TextMessages (via RFH2 headers). MQ Source Connector reads from the queue and writes to `race-standings-raw` (JMS envelope schema). A Flink parsing job (Job 0) extracts JSON from the `text` field and writes to clean `race-standings` topic with `car_number` as key. 22 messages per lap.

**Clean message (after Job 0 parsing):**
```json
{
  "car_number": 44, "driver": "James River", "team": "River Racing",
  "lap": 32, "position": 8,
  "gap_to_leader_sec": 18.4, "gap_to_ahead_sec": 2.1, "last_lap_time_sec": 92.100,
  "pit_stops": 0, "tire_compound": "SOFT", "tire_age_laps": 32,
  "in_pit_lane": false, "event_time": 1718373720000
}
```

### Data Source 3: Drivers (Reference — Postgres CDC)

22 fictional drivers and teams (11 teams, 2 drivers each). Postgres on EC2 (Docker), pre-loaded during Terraform provisioning. CDC Debezium Connector streams to Kafka. Schema: car_number, driver, team, nationality, championships, career_wins, career_podiums, season_points, season_position. See `data/drivers.csv` and `datagen/drivers.py` for the full grid.

---

## Kafka Topics (6 Total)

| Topic | Producer | Consumer | Created By | Tableflow |
|---|---|---|---|---|
| `car-telemetry` | Race simulator (direct to Kafka) | Job 1: Enrichment | **Terraform** (Flink CREATE TABLE) | No |
| `race-standings-raw` | MQ Connector (JMS envelope) | Job 0: Parse + Key | **MQ Connector** (auto-creates during demo) | No |
| `race-standings` | Job 0: Parse + Key | Job 1: Enrichment (temporal join) + RTCE | **Terraform** (Flink CREATE TABLE) | No |
| `drivers` | Postgres → CDC Debezium | — | **CDC Connector** (during demo) | **Yes** |
| `car-state` | Job 1: Enrichment + Anomaly | Job 2: Agent | **DBT** (streaming_table materialization) | No |
| `pit-decisions` | Job 2: Agent | — | **Agent** (during demo) | **Yes** |

**Only `car-telemetry` and `race-standings` are created by Terraform** (need schemas, watermarks, primary keys for temporal joins). `race-standings-raw` is created by the MQ connector with its JMS envelope schema. The other topics are created during the demo.

---

## Schemas (Flink CREATE TABLE — Terraform)

### Car Telemetry

```sql
CREATE TABLE `car-telemetry` (
  `car_number` INT COMMENT 'Car number identifier',
  `lap` INT COMMENT 'Current lap number (1-57)',
  `tire_temp_fl_c` DOUBLE COMMENT 'Front-left tire temperature in Celsius',
  `tire_temp_fr_c` DOUBLE COMMENT 'Front-right tire temperature in Celsius',
  `tire_temp_rl_c` DOUBLE COMMENT 'Rear-left tire temperature in Celsius',
  `tire_temp_rr_c` DOUBLE COMMENT 'Rear-right tire temperature in Celsius',
  `tire_pressure_fl_psi` DOUBLE COMMENT 'Front-left tire pressure in PSI',
  `tire_pressure_fr_psi` DOUBLE COMMENT 'Front-right tire pressure in PSI',
  `tire_pressure_rl_psi` DOUBLE COMMENT 'Rear-left tire pressure in PSI',
  `tire_pressure_rr_psi` DOUBLE COMMENT 'Rear-right tire pressure in PSI',
  `engine_temp_c` DOUBLE COMMENT 'Engine temperature in Celsius',
  `brake_temp_fl_c` DOUBLE COMMENT 'Front-left brake temperature in Celsius',
  `brake_temp_fr_c` DOUBLE COMMENT 'Front-right brake temperature in Celsius',
  `battery_charge_pct` DOUBLE COMMENT 'Hybrid battery charge percentage (0-100)',
  `fuel_remaining_kg` DOUBLE COMMENT 'Remaining fuel in kilograms',
  `drs_active` BOOLEAN COMMENT 'Drag Reduction System active flag',
  `speed_kph` DOUBLE COMMENT 'Current speed in km/h',
  `throttle_pct` DOUBLE COMMENT 'Throttle pedal position percentage (0-100)',
  `brake_pct` DOUBLE COMMENT 'Brake pedal position percentage (0-100)',
  `event_time` TIMESTAMP(3) COMMENT 'Sensor reading timestamp',
  WATERMARK FOR `event_time` AS `event_time` - INTERVAL '5' SECOND,
  PRIMARY KEY (`car_number`) NOT ENFORCED
) DISTRIBUTED BY (`car_number`) INTO 1 BUCKETS;
```

### Race Standings

```sql
CREATE TABLE `race-standings` (
  `car_number` INT COMMENT 'Car number identifier',
  `driver` STRING COMMENT 'Driver full name',
  `team` STRING COMMENT 'Constructor team name',
  `lap` INT COMMENT 'Current lap number (1-57)',
  `position` INT COMMENT 'Current race position (1-22)',
  `gap_to_leader_sec` DOUBLE COMMENT 'Time gap to race leader in seconds',
  `gap_to_ahead_sec` DOUBLE COMMENT 'Time gap to car directly ahead in seconds',
  `last_lap_time_sec` DOUBLE COMMENT 'Last completed lap time in seconds',
  `pit_stops` INT COMMENT 'Number of pit stops completed',
  `tire_compound` STRING COMMENT 'Current tire compound (SOFT, MEDIUM, HARD)',
  `tire_age_laps` INT COMMENT 'Number of laps on current set of tires',
  `in_pit_lane` BOOLEAN COMMENT 'Whether car is currently in the pit lane',
  `event_time` TIMESTAMP(3) COMMENT 'FIA timing feed timestamp',
  WATERMARK FOR `event_time` AS `event_time` - INTERVAL '10' SECOND,
  PRIMARY KEY (`car_number`) NOT ENFORCED
) DISTRIBUTED BY (`car_number`) INTO 1 BUCKETS;
```

### Pit Decisions (Agent Output — Job 2)

Flat schema. One record per lap (57 total). Three possible `suggestion` values: **PIT NOW**, **PIT SOON**, **STAY OUT**. For `STAY OUT` laps, recommendation fields are `null`.

```json
{
  "car_number": 44, "lap": 32, "position": 8,
  "tire_compound_current": "SOFT",
  "suggestion": "PIT NOW",
  "condition_summary": "Front-left tire temperature anomaly detected at 145C, 20C above expected upper bound.",
  "race_context": "Currently P8. Vega (P4) and Walsh (P5) already pitted onto mediums.",
  "recommended_tire_compound": "MEDIUM",
  "recommended_stint_laps": 25,
  "recommended_reason": "Mediums will last the remaining 25 laps.",
  "reasoning": "Pitting now onto mediums avoids tire failure and puts you on fresher rubber than 6 cars ahead.",
  "timestamp": "2026-06-14T15:42:03Z"
}
```

---

## Flink Job 0: Parse Race Standings

Extracts structured fields from the MQ connector's JMS envelope and writes to the clean `race-standings` topic with `car_number` as key.

**Input:** `race-standings-raw` (JMS envelope with JSON in `text` field)
**Output:** `race-standings` (flat schema, keyed by `car_number`)

```sql
INSERT INTO `race-standings`
SELECT
  CAST(JSON_VALUE(`text`, '$.car_number') AS INT) AS `car_number`,
  JSON_VALUE(`text`, '$.driver') AS `driver`,
  ...
  TO_TIMESTAMP_LTZ(CAST(JSON_VALUE(`text`, '$.event_time') AS BIGINT), 3) AS `event_time`
FROM `race-standings-raw`;
```

**Full SQL:** `demo-reference/parse_standings.sql`

---

## Flink Job 1: Enrichment + Anomaly Detection

Single Flink SQL statement using CTEs. Deployed via DBT (`streaming_table` materialization).

**Input:** `car-telemetry` (stream), `race-standings` (versioned table for temporal join)
**Output:** `car-state` (one record per lap)

**CTE pattern:** `windowed` (10s TUMBLE on car-telemetry, AVG all metrics, MAX lap) → `anomaly` (AI_DETECT_ANOMALIES on 11 metrics with OVER window) → final SELECT (extract `.is_anomaly` booleans + temporal join with race-standings for position/gap/pit/tire context).

**Full SQL:** `demo-reference/enrichment_anomaly.sql`

### AI_DETECT_ANOMALIES Configuration

| Parameter | Value |
|---|---|
| `upperBoundConfidencePercentage` | 99.0 (sensitive to spikes) |
| `lowerBoundConfidencePercentage` | 99.9 (tolerant of dips/declines) |
| `minContextSize` / `maxContextSize` | Defaults (20 / 512) |
| `model` | Default (`timesfm-2.5`) |

**Only `tire_temp_fl_c` produces an anomaly** (spike to ~145C at lap 32). All other metrics follow gradual/predictable patterns.

---

## Flink Job 2: Streaming Agent

Reads `car-state`, uses RTCE for competitor context, produces `pit-decisions`.

**Pattern:** CREATE CONNECTION (OpenAI) → CREATE MODEL → CREATE CONNECTION (MCP/RTCE) → CREATE TOOL (race standings lookup) → CREATE AGENT (with prompt + tools) → INSERT INTO pit-decisions SELECT AI_RUN_AGENT(...) FROM car-state.

**Full SQL:** `demo-reference/streaming_agent.sql`

---

## Race Simulation — Semi-Scripted Narrative

### Race Configuration

| Setting | Value |
|---|---|
| Circuit | Silverstone (British GP) |
| Total laps | 57 |
| Seconds per lap (simulation) | 10 |
| Total race time | ~9.5 minutes |
| Cars on grid | 22 (all fictional) |
| Our car | #44 — James River, River Racing |
| Pit stop time loss | ~20 seconds (simulated) |

### Race Script for James River (Car #44)

| Laps | Position | Tire | Anomaly | Suggestion | What's Happening |
|---|---|---|---|---|---|
| 1-15 | P3 | SOFT (fresh) | None | STAY OUT | Competitive, stable, good pace |
| 16-25 | P3 → P5 | SOFT (aging) | None | STAY OUT | Tires wearing, pace dropping, two cars pass |
| 26-31 | P5 → P8 | SOFT (critical) | None | PIT SOON | Tires falling off, three more cars pass |
| **32** | **P8** | **SOFT (dead)** | **tire_temp_fl = true** | **PIT NOW** | **Anomaly fires. Agent recommends pit.** |
| 33 | P12 | In pit lane | None | STAY OUT | Drops spots during pit stop. Fresh mediums. |
| 34-42 | P12 → P5 | MEDIUM (fresh) | None | STAY OUT | Fastest car on track, overtaking |
| 43-57 | P5 → P3 | MEDIUM (aging) | None | STAY OUT | Competitors pit on older tires, River jumps them |

**Net result: P8 → P3 = +5 positions gained from the moment the agent made the call.**

---

## Race Simulator (Data Generator)

- **Language:** Python. **Libraries:** `pymqi` (MQ writes via RFH2), `confluent-kafka[avro]` (Avro-serialized Kafka produce via Schema Registry)
- **Location:** ECS Fargate container (Docker image in ECR). Started manually via `./scripts/start-race.sh`.
- **Rate:** 10 seconds per simulated lap. Produces car telemetry to Kafka (every ~2s) and race standings to MQ (22 msgs per lap).

### MQ Message Format

Messages are sent as **JMS TextMessages** using `pymqi.Queue.put_rfh2()` with RFH2 headers:
- `md.Format = MQFMT_RF_HEADER_2` — tells MQ the body starts with an RFH2 header
- `rfh2["Format"] = MQFMT_STRING` — tells MQ the payload after RFH2 is a string
- `<mcd><Msd>jms_text</Msd></mcd>` — marks message as JMS TextMessage
- `<jms><Dst>queue:///DEV.QUEUE.1</Dst></jms>` — JMS destination

This ensures the MQ connector receives TextMessages (JSON in `text` field) rather than BytesMessages (base64 in `bytes` field).

### Kafka Message Format

Car telemetry is sent as **Avro** via `confluent-kafka[avro]` with `AvroSerializer(schema_str=None, conf={'auto.register.schemas': False, 'use.latest.version': True})`. Uses the schema registered by Flink CREATE TABLE. `event_time` = epoch millis for Avro `timestamp-millis`.

---

## Terraform Configuration

### Variables (5 Total)

| Variable | Required | Default | Description |
|---|---|---|---|
| `confluent_cloud_api_key` | **Yes** | — | Confluent Cloud API Key |
| `confluent_cloud_api_secret` | **Yes** | — | Confluent Cloud API Secret |
| `region` | No | `us-east-2` | Single region for both AWS and Confluent Cloud |
| `demo_name` | **Yes** | — | Unique name prefix for all resources (e.g., your first name) |
| `owner_email` | **Yes** | — | Email tagged on all AWS resources |

**AWS credentials** use the default provider chain (`~/.aws/credentials`, env vars, or IAM role).

### Modules (8 Total)

| Module | What It Creates |
|---|---|
| `modules/environment/` | Confluent Cloud environment |
| `modules/cluster/` | Kafka cluster + service accounts + API keys (Kafka + Schema Registry). SR data source lives here (depends on app API key for timing). |
| `modules/topics/` | `car-telemetry` + `race-standings` topics (Flink CREATE TABLE with schema, watermarks, COMMENT, DISTRIBUTED BY INTO 1 BUCKETS) |
| `modules/flink/` | Compute pool + Flink API key |
| `modules/mq/` | EC2 + Docker IBM MQ (queue: DEV.QUEUE.1) |
| `modules/ecs/` | ECR repo + ECS Fargate cluster + task definition for race simulator. Builds Docker image with `--platform linux/amd64`. |
| `modules/postgres/` | EC2 + Docker Postgres (pre-loaded with 22 drivers) |
| `modules/tableflow/` | S3 bucket + IAM role + provider integration (uses `customer_role_arn`) |

### Auto-Generated Connector Config

Terraform generates `generated/mq_connector_config.json` via `local_file` resource with real values from module outputs (MQ IP, service account ID, API keys). The `generated/` directory is gitignored (contains secrets).

### Module Dependencies

- Topics module `depends_on = [module.flink, module.cluster]` — ensures Flink compute pool exists AND Schema Registry is ready before CREATE TABLE statements run.
- SR data source in cluster module `depends_on = [confluent_api_key.app]` — the app API key takes ~2 minutes to create, giving SR time to provision. No artificial `time_sleep` needed.
- Flink statements require `rest_endpoint` + `credentials` block (not just provider config).

### What Terraform Deploys vs. Demo Hero Moments

**Terraform deploys (before demo):**
- Confluent Cloud environment + schema registry
- Kafka cluster + service accounts + API keys (Kafka + Schema Registry)
- `car-telemetry` + `race-standings` topics with schemas/watermarks/comments (Flink CREATE TABLE)
- Flink compute pool + API key
- EC2 with IBM MQ (queue: DEV.QUEUE.1)
- ECR + ECS Fargate task definition for race simulator (not started)
- EC2 with Postgres + drivers table (pre-loaded)
- S3 bucket + IAM role for Tableflow
- Auto-generated MQ connector config (`generated/mq_connector_config.json`)

**Left for demo (hero moments):**
1. Deploy MQ Source Connector (creates `race-standings-raw` topic with JMS envelope schema)
2. Start race simulator (`./scripts/start-race.sh`)
3. Deploy Job 0: Parse standings (`parse_standings.sql`) — extracts JSON from JMS text, sets key
4. Deploy CDC Debezium Connector (creates `drivers` topic)
5. Deploy Job 1: Enrichment + anomaly detection via DBT (creates `car-state` topic)
6. Deploy Job 2: Streaming Agent (creates `pit-decisions` topic)
7. Enable Tableflow on `pit-decisions` and `drivers`
8. Query with Databricks Genie

---

## Tableflow

S3 BYOS (Bring Your Own Storage) → Delta Lake → Databricks Unity Catalog. IAM role assumption with external ID, no OIDC. Terraform creates: S3 bucket, IAM role/policy (9 S3 permissions), and `confluent_provider_integration` (uses `customer_role_arn`, not `iam_role_arn`).

| Topic | Why |
|---|---|
| `pit-decisions` | Agent's AI output — the data product for analytics |
| `drivers` | Reference data — dimension table for joins in lakehouse |

---

## Databricks Genie — Analytics Questions

Reference queries in `tableflow/EXAMPLE-QUERIES.md`.

**Question 1 — Position Gain (Tabular):** *"How many positions did we gain after following the agent's recommendation?"* Uses `pit-decisions` table. Expected answer: P8 at lap 32 → P3 at finish = +5 positions.

**Question 2 — Tire Strategy (Pie Chart):** *"What percentage of the race did our driver spend on each tire compound?"* Joins `pit-decisions` with `drivers`. Expected answer: SOFT 56% / MEDIUM 44%.

---

## Demo Recording Strategy

### Section 1: Data Discovery & Connection (~4 min)

- Open Claude Desktop with MCP
- Ask: *"What data sources do I have?"* — MCP discovers MQ queue + Postgres table
- Deploy MQ Source Connector → creates `race-standings-raw` topic
- Start race simulator → data flowing to `car-telemetry` and MQ
- Deploy Job 0: Parse standings → clean `race-standings` topic with keyed data
- Deploy CDC Debezium Connector → creates `drivers` topic

### Section 2: Building Intelligence (~6 min)

- Show `car-telemetry` and `race-standings` topics flowing
- MCP scaffolds DBT enrichment model (tumbling window + AI_DETECT_ANOMALIES + temporal join)
- `dbt run` → continuous Flink Job 1 starts → `car-state` topic created
- MCP helps configure Streaming Agent (Flink SQL: CREATE AGENT)
- Deploy agent → Flink Job 2 starts
- **Wait for lap 32** — anomaly fires on `tire_temp_fl`, agent says PIT NOW
- Show the `pit-decisions` message with the agent's reasoning

### Section 3: Analytics & Impact (~4 min)

- Enable Tableflow on `pit-decisions` and `drivers`
- Open Databricks Genie
- Ask: *"How many positions did we gain?"* → +5
- Ask: *"What tire compound split?"* → Pie chart (SOFT 56% / MEDIUM 44%)
- **Closing:** *"AI at both ends — Confluent's streaming agent made the call in real time, Databricks Genie analyzes the impact."*

---

## File Structure

```
F1/
├── CLAUDE.md                       # This file
├── README.md                       # Quick start guide with all SQL queries
├── .gitignore                      # Excludes tfvars, .terraform, generated/, __pycache__
├── terraform/                      # Infrastructure (8 modules)
│   ├── main.tf                     # Providers + modules + auto-generated connector config
│   ├── variables.tf, outputs.tf, versions.tf, terraform.tfvars.example
│   └── modules/{environment,cluster,topics,flink,mq,ecs,postgres,tableflow}/
├── generated/                      # Auto-generated by Terraform (gitignored, contains secrets)
│   └── mq_connector_config.json    # Ready-to-deploy connector config with real values
├── demo-reference/                 # SQL + connector configs for demo hero moments
│   ├── parse_standings.sql         # Job 0: Parse JMS envelope + set key
│   ├── enrichment_anomaly.sql      # Job 1: Flink SQL (enrichment + anomaly detection)
│   ├── streaming_agent.sql         # Job 2: Flink SQL (streaming agent)
│   ├── mq_connector_config.json    # MQ connector template (with placeholders)
│   └── cdc_connector_config.json   # CDC connector template
├── datagen/                        # Race simulator (ECS Fargate / Docker)
│   ├── Dockerfile                  # Python 3.11-slim + IBM MQ C client + gcc
│   ├── requirements.txt            # pymqi, confluent-kafka[avro], faker
│   ├── config.py                   # MQ + Kafka + race config
│   ├── simulator.py                # Main entry point (Avro to Kafka, RFH2 TextMessage to MQ)
│   ├── telemetry.py                # Sensor curves + lap 32 anomaly
│   ├── race_script.py              # State management + gap calculations
│   ├── drivers.py                  # 22 fictional drivers grid (pit_lap, pit_tire per driver)
│   └── tests/                      # test_simulator.py, test_telemetry.py, test_race_script.py
├── data/                           # drivers.csv + drivers_seed.sql (Postgres INSERT statements)
├── docs/                           # SETUP-DBT-ADAPTER.md, SETUP-TABLEFLOW.md, SETUP-GENIE.md
├── scripts/                        # setup.sh, teardown.sh, start-race.sh, stop-race.sh
└── tableflow/                      # EXAMPLE-QUERIES.md (Genie reference queries)
```

---

## Constraints & Preferences

### Must Have
- Three data sources, three ingestion paths (direct Kafka, MQ queue, Postgres CDC)
- AI_DETECT_ANOMALIES on all metrics, only tire_temp_fl fires
- Single anomaly at lap 32 — no other anomalies in the entire race
- AI agent decides pit strategy (no formulas in SQL)
- +5 positions gained after agent recommendation
- 10 seconds per simulated lap (~9.5 min total race)
- All fictional drivers and teams (team: River Racing, driver: James River)
- Circuit: Silverstone; 22 drivers, 11 teams
- Tableflow on pit-decisions + drivers
- Databricks Genie for analytics
- Everything deployable via Terraform (infra) + scripts
- Race simulator runs on ECS Fargate, started manually via `./scripts/start-race.sh`
- MQ messages sent as JMS TextMessage (RFH2 headers)
- Single partition topics (DISTRIBUTED BY INTO 1 BUCKETS)

### Must NOT Have
- Anomalies on any metric other than tire_temp_fl_c
- Multiple anomalies at different laps
- Probability formulas in Flink SQL
- Real driver or team names
- A Copilot layer (agent output is sufficient)
- Tableflow on race-standings or car-telemetry
- Batch processing
- MQ pub/sub topics (connector wraps in JMS envelope; use queues instead)

---

## Technical Discoveries

### Confluent Cloud Flink SQL
1. **CREATE TABLE without WITH clause** — Confluent Cloud auto-creates backing Kafka topic + schema subjects.
2. **DISTRIBUTED BY INTO BUCKETS** — Use `DISTRIBUTED BY (col) INTO 1 BUCKETS` to control partition count. `kafka.partitions` WITH option is deprecated.
3. **COMMENT on columns** — `column_name TYPE COMMENT 'description'` adds descriptions stored in Schema Registry.
4. **`confluent_flink_statement`** — Terraform resource for submitting any Flink SQL. Requires `rest_endpoint` + `credentials` block.
5. **Required providers in modules** — Each Terraform module using `confluent_*` resources needs its own `required_providers` block with `source = "confluentinc/confluent"`.
6. **Streaming Agents = Flink SQL** — CREATE AGENT DDL, not YAML, not REST API.

### MQ Connector + JMS
7. **MQ connector always wraps in JMS envelope** — Regardless of output format (AVRO, JSON), the connector produces a JMS message struct with `text`, `bytes`, `properties`, `messageType`, etc. Raw JSON payload is not extracted.
8. **JMS TextMessage vs BytesMessage** — Default `pymqi.Queue.put()` sends BytesMessage (payload base64 in `bytes` field). Must use `put_rfh2()` with RFH2 headers to send TextMessage (payload as string in `text` field).
9. **RFH2 header for TextMessage** — `md.Format = MQFMT_RF_HEADER_2`, `rfh2["Format"] = MQFMT_STRING`, `<mcd><Msd>jms_text</Msd></mcd>` folder. Chain: MQMD → RFH2 → STRING payload.
10. **`jms.destination.name` not `mq.queue`** — Confluent Cloud managed MQ connector uses `jms.destination.name` for the queue/topic name, not `mq.queue`.
11. **ValueToKey SMT fails on JMS envelope** — Can't extract `car_number` from nested `text` field. Solution: skip SMT, use Flink Job 0 to parse + key.
12. **Schema compatibility conflicts** — Pre-created Flink schema (flat) conflicts with connector's JMS envelope schema. Solution: connector writes to `-raw` topic, Flink Job 0 transforms to clean topic.
13. **MQ pub/sub not viable** — Tested pub/sub topics extensively. Connector still wraps in JMS envelope. Also: `pymqi.Topic` requires `SYSTEM.BASE.TOPIC` auth, `app` user lacks topic permissions on dev image, and `admin` channel needed. Reverted to queues.

### Terraform & Infrastructure
14. **SR data source timing** — Schema Registry takes time to provision after environment creation. Solution: move SR data source to cluster module with `depends_on = [confluent_api_key.app]` (app key takes ~2 min, natural delay). No `time_sleep` needed.
15. **`customer_role_arn` not `iam_role_arn`** — `confluent_provider_integration` AWS block uses `customer_role_arn` for the IAM role Confluent assumes.
16. **Docker `--platform linux/amd64`** — ECS Fargate runs x86_64. Mac builds ARM by default. IBM MQ C client tar is x86_64 only.
17. **Dockerfile needs `gcc`** — `pymqi` compiles C extensions. Add `gcc libc6-dev` to `apt-get install`.
18. **Auto-generate connector config** — `local_file` resource in Terraform generates `generated/mq_connector_config.json` with real values from module outputs. Gitignored.
19. **ECS task definition versioning** — `null_resource.docker_build_push` triggers on file hashes. After code changes, `terraform taint module.ecs.null_resource.docker_build_push` forces rebuild.
20. **Multi-tenant deployments** — All resources prefixed with `demo_name`. AWS resources tagged with `owner_email`.

### Data & Serialization
21. **Avro serialization** — `confluent-kafka[avro]` with `AvroSerializer(schema_str=None, conf={'auto.register.schemas': False, 'use.latest.version': True})`. Uses schema registered by Flink CREATE TABLE.
22. **Schema Registry API key** — Separate from Kafka API key. Created in cluster module. `EnvironmentAdmin` role covers SR access.
23. **Temporal join needs both watermarks** — Both sides need advancing watermarks. Versioned table needs PRIMARY KEY + watermark.
24. **Semi-scripted simulator** — Stateful data generator. Script key events (pit laps per driver), simulate the rest (gaps, positions, degradation) via cumulative race time model.

### Git & Deployment
25. **Standalone git repo** — F1 project has its own `.git` at `F1/` root, separate from the parent monorepo. Remote: `confluentinc/demo-confluent-intelligence-f1`.
26. **`git push-external`** — Required for pushing to `confluentinc` org repos (Confluent security policy). Goes through airlock proprietary code check.

---

**Last Updated:** April 21, 2026
**Document Version:** 4.0
**Parent Project:** World Cup Ticketing Demo (same repo structure pattern)
