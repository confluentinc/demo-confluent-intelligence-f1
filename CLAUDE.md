# Project Context: F1 Pit Wall AI Demo — Silverstone Racing

## Project Overview

12-15 minute keynote demo for a Confluent conference (London). Real-time AI pit strategy system for an F1 team. An AI agent monitors live car telemetry and race standings, detects anomalies, and recommends pit stop strategy in real time.

**Team Name:** River Racing
**Driver:** James River (car #44)
**Circuit:** Silverstone (British Grand Prix)
**Audience:** Mixed technical (data engineers, architects, CTOs, business stakeholders)
**Delivery:** Pre-recorded video (edited screen recordings + voiceover) + GitHub repo
**All drivers and teams are fictional.**

---

## Architecture (Final)

```
Car Sensors (internal)     ──→ Direct to Kafka            ──→ car-telemetry
                                                                   │
FIA Timing Feed (external) ──→ IBM MQ ──→ MQ Connector ──→ race-standings
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

1. **Three data sources, three ingestion paths** — Car telemetry direct to Kafka (internal, modern), race standings via MQ (external FIA feed through legacy middleware), drivers via Postgres CDC (reference data).
2. **Two Flink jobs** — Job 1 enriches + detects anomalies. Job 2 runs the AI agent. Clean separation.
3. **AI_DETECT_ANOMALIES on all metrics** — Monitors every sensor, but only tire_temp_fl_c fires an anomaly at lap 32.
4. **Agent decides everything** — No threshold formulas in Flink SQL. The agent classifies suggestion (PIT NOW / PIT SOON / STAY OUT) and provides reasoning.
5. **RTCE for competitor context** — Agent uses Real-Time Context Engine to look up competitor standings from `race-standings`.
6. **Tableflow on two topics** — `pit-decisions` and `drivers` materialize to Delta Lake for Genie analytics.
7. **No Copilot** — The agent produces pit decisions with full reasoning. No separate conversational layer needed.
8. **10 seconds per lap** — Simulated race completes in ~9.5 minutes (57 laps).

---

## Data Sources (3 Total)

### Data Source 1: Car Telemetry (Internal — Direct to Kafka)

Sensor readings from car #44 only. Race simulator produces directly to Kafka via `confluent-kafka[avro]`. Every ~1-2 seconds (~5-10 readings per 10-second lap). Topic: `car-telemetry` (created by Terraform via Flink CREATE TABLE).

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

Position, gaps, pit status, and tire info for ALL 22 cars. FIA feed arrives through IBM MQ (legacy middleware for external data) then MQ Source Connector writes to Kafka. 22 messages per lap. Topic: `race-standings` (created by Terraform via Flink CREATE TABLE).

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

## Kafka Topics (5 Total)

| Topic | Producer | Consumer | Created By | Tableflow |
|---|---|---|---|---|
| `car-telemetry` | Race simulator (direct to Kafka) | Job 1: Enrichment | **Terraform** (Flink CREATE TABLE) | No |
| `race-standings` | FIA → MQ → MQ Connector | Job 1: Enrichment (temporal join) + RTCE | **Terraform** (Flink CREATE TABLE) | No |
| `drivers` | Postgres → CDC Debezium | — | **CDC Connector** (during demo) | **Yes** |
| `car-state` | Job 1: Enrichment + Anomaly | Job 2: Agent | **DBT** (streaming_table materialization) | No |
| `pit-decisions` | Job 2: Agent | — | **Agent** (during demo) | **Yes** |

`car-telemetry` and `race-standings` are created by Terraform (need schemas, watermarks, primary keys for temporal joins). The other 3 topics are created during the demo by their respective producers.

---

## Schemas (Flink CREATE TABLE — Terraform)

### Car Telemetry

```sql
CREATE TABLE `car-telemetry` (
  `car_number` INT, `lap` INT,
  `tire_temp_fl_c` DOUBLE, `tire_temp_fr_c` DOUBLE,
  `tire_temp_rl_c` DOUBLE, `tire_temp_rr_c` DOUBLE,
  `tire_pressure_fl_psi` DOUBLE, `tire_pressure_fr_psi` DOUBLE,
  `tire_pressure_rl_psi` DOUBLE, `tire_pressure_rr_psi` DOUBLE,
  `engine_temp_c` DOUBLE, `brake_temp_fl_c` DOUBLE, `brake_temp_fr_c` DOUBLE,
  `battery_charge_pct` DOUBLE, `fuel_remaining_kg` DOUBLE,
  `drs_active` BOOLEAN, `speed_kph` DOUBLE, `throttle_pct` DOUBLE, `brake_pct` DOUBLE,
  `event_time` TIMESTAMP(3),
  WATERMARK FOR `event_time` AS `event_time` - INTERVAL '5' SECOND,
  PRIMARY KEY (`car_number`) NOT ENFORCED
);
```

### Race Standings

```sql
CREATE TABLE `race-standings` (
  `car_number` INT, `driver` STRING, `team` STRING, `lap` INT,
  `position` INT, `gap_to_leader_sec` DOUBLE, `gap_to_ahead_sec` DOUBLE,
  `last_lap_time_sec` DOUBLE, `pit_stops` INT, `tire_compound` STRING,
  `tire_age_laps` INT, `in_pit_lane` BOOLEAN, `event_time` TIMESTAMP(3),
  WATERMARK FOR `event_time` AS `event_time` - INTERVAL '10' SECOND,
  PRIMARY KEY (`car_number`) NOT ENFORCED
);
```

### Pit Decisions (Agent Output — Job 2)

Flat schema. One record per lap. Three possible `suggestion` values: **PIT NOW**, **PIT SOON**, **STAY OUT**. For `STAY OUT` laps, recommendation fields are `null`.

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

## Flink Job 1: Enrichment + Anomaly Detection

Single Flink SQL statement using CTEs. Deployed via DBT (`streaming_table` materialization).

**Input:** `car-telemetry` (stream), `race-standings` (versioned table for temporal join)
**Output:** `car-state` (one record per lap)

**CTE pattern:** `windowed` (10s TUMBLE on car-telemetry, AVG all metrics, MAX lap) → `anomaly` (AI_DETECT_ANOMALIES on 11 metrics with OVER window) → final SELECT (extract `.is_anomaly` booleans + temporal join with race-standings for position/gap/pit/tire context).

**Full SQL:** `demo-reference/enrichment_anomaly.sql`

**Fallback:** If temporal join doesn't work with `window_time` from a CTE, split into two DBT models: `car_state_sensors` (tumble + anomaly) → `car_state` (temporal join).

### AI_DETECT_ANOMALIES Configuration

| Parameter | Value |
|---|---|
| `upperBoundConfidencePercentage` | 99.0 (sensitive to spikes) |
| `lowerBoundConfidencePercentage` | 99.9 (tolerant of dips/declines) |
| `minContextSize` / `maxContextSize` | Defaults (20 / 512) |
| `model` | Default (`timesfm-2.5`) |

TimesFM learns gradual trends and only flags sudden deviations. Asymmetric bounds (99.0 upper / 99.9 lower) catch spikes while ignoring natural declines (fuel, tire pressure).

**Only `tire_temp_fl_c` produces an anomaly** (spike to ~145C at lap 32). All other metrics follow gradual/predictable patterns. See `datagen/telemetry.py` for exact curves.

---

## Flink Job 2: Streaming Agent

Reads `car-state`, uses RTCE for competitor context, produces `pit-decisions`.

**Pattern:** CREATE CONNECTION (OpenAI) → CREATE MODEL → CREATE CONNECTION (MCP/RTCE) → CREATE TOOL (race standings lookup) → CREATE AGENT (with prompt + tools) → INSERT INTO pit-decisions SELECT AI_RUN_AGENT(...) FROM car-state.

Agent response is parsed with REGEXP_EXTRACT into structured fields (suggestion, condition_summary, reasoning, etc.).

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
| 33 | P10 | In pit lane | None | STAY OUT | Drops 2 spots during pit stop. Fresh mediums. |
| 34-42 | P10 → P5 | MEDIUM (fresh) | None | STAY OUT | Fastest car on track, overtaking |
| 43-57 | P5 → P3 | MEDIUM (aging) | None | STAY OUT | Competitors pit on older tires, River jumps them |

**Net result: P8 → P3 = +5 positions gained from the moment the agent made the call.**

### Key Demo Moments

1. **Lap 32 — The Anomaly:** Front-left tire temperature spikes to 145C. AI detects the anomaly. Agent says PIT NOW. All other sensors are clean — the AI pinpointed the exact issue.
2. **Lap 33-42 — The Recovery:** Fresh mediums make James River the fastest car on track.
3. **Final Result:** Started P3, dropped to P8 on dying tires, agent caught it, recovered to P3.

### Other Cars' Behavior (Simplified)

| Car | Driver | Team | Key Behavior |
|---|---|---|---|
| #1 | Max Eriksson | Titan Dynamics | Race leader. Pits lap 18 onto mediums. Wins. |
| #4 | Luca Novak | Apex Motorsport | P2 start. Pits lap 22 onto mediums. Finishes P2. |
| #16 | Carlos Vega | Scuderia Rossa | P4 start. Pits lap 15. Closes on James River laps 26-31. |
| #63 | Oliver Walsh | Sterling GP | P5 start. Pits lap 20. Passes James River at lap 27. |
| #14 | Fernando Reyes | Aston Verde | P6 start. Late pit lap 35. James River passes him on fresh tires. |
| P7-P22 | Various | Various | Simple strategies, hold position, background noise. |

---

## Race Simulator (Data Generator)

- **Language:** Python. **Libraries:** `pymqi` (MQ writes), `confluent-kafka[avro]` (Avro-serialized Kafka produce via Schema Registry)
- **Location:** ECS Fargate container (Docker image in ECR). Started manually via `./scripts/start-race.sh`.
- **Rate:** 10 seconds per simulated lap. Produces car telemetry to Kafka (every ~1-2s) and race standings to MQ (22 msgs per lap).

Pit stops are defined per driver in `datagen/drivers.py` GRID (each driver has `pit_lap` and `pit_tire`). The anomaly (tire_temp_fl spike at lap 32) is triggered in `datagen/telemetry.py`. Tire degradation, position changes, and gap calculations happen automatically in `datagen/race_script.py` based on a cumulative race time model.

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
| `modules/environment/` | Confluent Cloud environment + Schema Registry |
| `modules/cluster/` | Kafka cluster + service accounts + API keys (Kafka + Schema Registry) |
| `modules/topics/` | `car-telemetry` + `race-standings` topics (Flink CREATE TABLE with schema + watermarks) |
| `modules/flink/` | Compute pool only |
| `modules/mq/` | EC2 + Docker IBM MQ (broker only) |
| `modules/ecs/` | ECR repo + ECS Fargate cluster + task definition for race simulator |
| `modules/postgres/` | EC2 + Docker Postgres (pre-loaded with 22 drivers) |
| `modules/tableflow/` | S3 bucket + IAM role + provider integration (Confluent side) |

### What Terraform Deploys vs. Demo Hero Moments

**Terraform deploys (before demo):**
- Confluent Cloud environment + schema registry
- Kafka cluster + service accounts + API keys (Kafka + Schema Registry)
- `car-telemetry` + `race-standings` topics with schemas/watermarks (Flink CREATE TABLE)
- Flink compute pool
- EC2 with IBM MQ (broker only)
- ECR + ECS Fargate task definition for race simulator (not started)
- EC2 with Postgres + drivers table (pre-loaded)
- S3 bucket + IAM role for Tableflow

**Left for demo (hero moments with MCP):**
- Deploy MQ Source Connector (writes to existing `race-standings` topic)
- Deploy CDC Debezium Connector (creates `drivers` topic)
- Build + deploy DBT enrichment model with anomaly detection (creates `car-state` topic)
- Configure + deploy Streaming Agent (creates `pit-decisions` topic)
- Enable Tableflow on `pit-decisions` and `drivers`
- Query with Databricks Genie

---

## Tableflow

S3 BYOS (Bring Your Own Storage) → Delta Lake → Databricks Unity Catalog. IAM role assumption with external ID, no OIDC. Terraform creates: S3 bucket, IAM role/policy (9 S3 permissions), and `confluent_provider_integration`.

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
- MCP generates MQ connector config → deploy MQ Source Connector (writes to `race-standings`)
- MCP generates CDC connector config → deploy CDC Debezium Connector (creates `drivers` topic)
- Show data flowing — race standings from MQ, driver profiles from Postgres

### Section 2: Building Intelligence (~6 min)

- Show `car-telemetry` topic already flowing (started by Terraform)
- MCP scaffolds DBT enrichment model (tumbling window + AI_DETECT_ANOMALIES + temporal join)
- `dbt run` → continuous Flink Job 1 starts → `car-state` topic created
- MCP helps configure Streaming Agent (Flink SQL: CREATE AGENT)
- Deploy agent → Flink Job 2 starts
- **Wait for lap 32** — anomaly fires on `tire_temp_fl`, agent says PIT NOW
- Show the `pit-decisions` message with the agent's reasoning

### Section 3: Analytics & Impact (~4 min)

- Enable Tableflow on `pit-decisions` and `drivers` via MCP
- Open Databricks Genie
- Ask: *"How many positions did we gain?"* → +5
- Ask: *"What tire compound split?"* → Pie chart (SOFT 56% / MEDIUM 44%)
- **Closing:** *"AI at both ends — Confluent's streaming agent made the call in real time, Databricks Genie analyzes the impact."*

---

## File Structure

```
F1/
├── CLAUDE.md                       # This file
├── README.md                       # Quick start guide
├── terraform/                      # Infrastructure (8 modules: environment, cluster, topics, flink, mq, ecs, postgres, tableflow)
│   ├── main.tf, variables.tf, outputs.tf, versions.tf, terraform.tfvars.example
│   └── modules/{environment,cluster,topics,flink,mq,ecs,postgres,tableflow}/
├── demo-reference/                 # SQL + connector configs built during demo with MCP
│   ├── enrichment_anomaly.sql      # Job 1: Flink SQL
│   ├── streaming_agent.sql         # Job 2: Flink SQL
│   ├── mq_connector_config.json    # MQ connector (AVRO + ValueToKey SMT)
│   └── cdc_connector_config.json   # CDC connector
├── datagen/                        # Race simulator (ECS Fargate / Docker)
│   ├── Dockerfile, requirements.txt, config.py
│   ├── simulator.py                # Main entry point (Avro producer)
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
- Three data sources, three ingestion paths (direct Kafka, MQ, Postgres CDC)
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

### Must NOT Have
- Anomalies on any metric other than tire_temp_fl_c
- Multiple anomalies at different laps
- Probability formulas in Flink SQL
- Real driver or team names
- A Copilot layer (agent output is sufficient)
- Tableflow on race-standings or car-telemetry
- More than 2 Flink jobs
- Batch processing

---

## Technical Discoveries

1. **CREATE TABLE without WITH clause** — Confluent Cloud auto-creates backing Kafka topic + schema subjects.
2. **`confluent_flink_statement`** — Terraform resource for submitting any Flink SQL.
3. **Streaming Agents = Flink SQL** — CREATE AGENT DDL, not YAML, not REST API.
4. **DBT-Confluent Adapter** — `type: confluent`, `execution_mode: streaming_query`, `streaming_table` materialization.
5. **Tableflow BYOS** — Cross-account IAM role assumption with external ID, no OIDC.
6. **Pre-create topics needing schemas/watermarks** — `car-telemetry` and `race-standings` via Terraform Flink CREATE TABLE; connectors/dbt/agent create their own.
7. **AI_DETECT_ANOMALIES** — OVER() window function, returns ROW with `is_anomaly`, `forecast_value`, `lower_bound`, `upper_bound`. One call per metric.
8. **CTE pattern for enrichment** — Tumble → anomaly detection → temporal join, chained via CTEs in one statement.
9. **Temporal join with window_time** — May not work from CTE. Fallback: split into two Flink statements.
10. **Semi-scripted simulator** — Stateful data generator. Script key events, simulate the rest.
11. **MQ for external data** — Justified for FIA feed (external, team doesn't control). Internal telemetry goes direct to Kafka.
12. **Asymmetric anomaly bounds** — upper 99.0 catches spikes, lower 99.9 ignores natural declines. TimesFM learns trends.
13. **Avro serialization** — `confluent-kafka[avro]` with `AvroSerializer(schema_str=None, conf={'auto.register.schemas': False, 'use.latest.version': True})`. `event_time` = epoch millis for Avro `timestamp-millis`.
14. **Schema Registry API key** — Separate from Kafka API key. `confluent_api_key` with `managed_resource` pointing to SR cluster. `EnvironmentAdmin` covers SR access.
15. **Temporal join needs both watermarks** — Both sides need advancing watermarks. Versioned table needs PRIMARY KEY + watermark.
16. **MQ connector AVRO + ValueToKey SMT** — `output.data.format: AVRO` matches Flink schema. `ValueToKey` extracts `car_number` as message key.
17. **Multi-tenant deployments** — All resources prefixed with `demo_name`. AWS resources tagged with `owner_email`.
18. **ECS Fargate for simulator** — Docker image with pymqi + IBM MQ C client libs. ECR stores image. Started on demand via `aws ecs run-task`.

---

**Last Updated:** April 20, 2026
**Document Version:** 3.0
**Parent Project:** World Cup Ticketing Demo (same repo structure pattern)
