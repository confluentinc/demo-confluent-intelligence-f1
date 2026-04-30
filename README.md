# F1 Pit Wall AI Demo — River Racing at Silverstone

Real-time AI pit strategy system for a Formula 1 team. An AI agent monitors live car telemetry, detects anomalies, and recommends pit stop strategy — all powered by Confluent Cloud.

## Architecture

```
Race Simulator (ECS Fargate)
  ├── Kafka produce (AVRO)   → car-telemetry
  └── MQ publish (JMS text)  → IBM MQ EC2 → MQ Source Connector → race-standings-raw
                                                                         │
                                                          Job 0 (Terraform-managed Flink SQL)
                                                          Parse JMS envelope, key by car_number
                                                                         │
                                                                  race-standings
                                                                         │
Postgres (EC2) → CDC Debezium Connector → driver_race_history ──────────┤
                                                                         │
                                                          Job 1 (Flink SQL Workspace)
                                                          10s tumbling window + temporal join
                                                          AI_DETECT_ANOMALIES(tire_temp_fl_c)
                                                                         │
                                                                     car-state
                                                                         │
                                                          Job 2 (Flink SQL Workspace)
                                                          AI_RUN_AGENT → pit-decisions
                                                                         │
                                                              Tableflow → S3 → Databricks Genie
```

## Quick Start

### Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Confluent Cloud account + API key/secret (Organization Admin)
- Confluent CLI installed and logged in (`confluent login`)
- AWS credentials configured (`~/.aws/credentials` or env vars)
- Terraform >= 1.3
- Docker Desktop running (builds the race simulator image for ECS)

### 1. Deploy infrastructure (~15-20 min)

```bash
uv run deploy
```

Prompts for credentials, then deploys two Terraform stacks (Confluent Cloud + AWS). Creates:
- Confluent Cloud environment, Kafka cluster, Schema Registry, Flink compute pool
- `car-telemetry` and `race-standings` topics (Flink CREATE TABLE with schemas + watermarks)
- IBM MQ EC2, Postgres EC2 (198 historical `driver_race_history` rows pre-loaded)
- ECS Fargate task definition (race simulator)
- MQ Source Connector + CDC Debezium Connector (both auto-deployed and running)
- S3 + IAM role for Tableflow
- Job 0 Flink statement (parse + key race standings) — running and waiting for data

### 2. Start the race

```bash
./scripts/start-race.sh
aws logs tail /ecs/f1-simulator --follow
```

Runs a 57-lap race in ~9.5 minutes. Job 0 immediately starts writing parsed standings to `race-standings`.

### 3. Deploy Jobs 1 & 2 in the Flink SQL Workspace

See **[Walkthrough.md](Walkthrough.md)** for the full step-by-step including SQL to paste, how to enable Tableflow, and how to query with Databricks Genie.

SQL files: `demo-reference/enrichment_anomaly.sql` (Job 1), `demo-reference/streaming_agent.sql` (Job 2).

---

## Demo Scenario

**Team:** River Racing | **Driver:** James River (#44) | **Circuit:** Silverstone | **57 laps, ~10s each**

| Laps | Position | Tire | What Happens |
|------|----------|------|--------------|
| 1–15 | P3 | SOFT (fresh) | Competitive, stable pace |
| 16–25 | P3 → P5 | SOFT (aging) | Tires wearing, two cars pass |
| 26–31 | P5 → P8 | SOFT (critical) | Tires falling off, three more pass — agent says PIT SOON |
| **32** | **P8** | **SOFT (dead)** | **tire_temp_fl anomaly fires — agent says PIT NOW** |
| 33 | P12 | MEDIUM (fresh) | Pit stop, drops spots |
| 34–57 | P12 → P3 | MEDIUM | Fastest car on track, climbs back |

**Result: P8 at pit call → P3 at finish = +5 positions gained**

---

## Reset & Teardown

```bash
uv run reset     # Stop Flink jobs, drop and recreate topics — use between race re-runs
uv run destroy   # Tear down all infrastructure
```

---

## Documentation

| | |
|--|--|
| [Walkthrough.md](Walkthrough.md) | Full step-by-step demo guide |
| [docs/USE-CASE.md](docs/USE-CASE.md) | Use case narrative + race script + Genie expected answers |
| [docs/SETUP-TABLEFLOW.md](docs/SETUP-TABLEFLOW.md) | Tableflow + Delta Lake setup |
| [docs/SETUP-GENIE.md](docs/SETUP-GENIE.md) | Databricks Genie setup |
| [docs/SETUP-DBT-ADAPTER.md](docs/SETUP-DBT-ADAPTER.md) | DBT adapter setup (optional) |
