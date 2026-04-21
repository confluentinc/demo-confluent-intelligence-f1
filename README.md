# F1 Pit Wall AI Demo — River Racing at Silverstone

Real-time AI pit strategy system for a Formula 1 team. An AI agent monitors live car telemetry, detects anomalies, and recommends pit stop strategy — all powered by Confluent Cloud.

## Architecture

```
Car Sensors ──→ Direct to Kafka ──→ car-telemetry ──┐
                                                     ├──→ Flink Job 1: Enrichment + Anomaly Detection ──→ car-state
FIA Timing  ──→ IBM MQ ──→ MQ Connector ──→ race-standings ──┘                                              │
                                                                                                             ▼
Postgres    ──→ CDC Debezium ──→ drivers ──→ Tableflow                              Flink Job 2: Streaming Agent
                                                                                                             │
                                                                                                             ▼
                                                                                          pit-decisions ──→ Tableflow ──→ Databricks Genie
```

## Quick Start

### Prerequisites

- Confluent Cloud account with API key/secret
- AWS credentials configured (`~/.aws/credentials` or env vars)
- Terraform >= 1.0
- Python 3.9+

### Deploy Infrastructure

```bash
# 1. Configure credentials
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
# Edit terraform.tfvars with your Confluent Cloud API key/secret

# 2. Deploy everything
./scripts/setup.sh
```

This creates: Confluent Cloud environment, Kafka cluster, Flink compute pool, `car-telemetry` topic, EC2 with IBM MQ + race simulator (auto-starts), EC2 with Postgres + 22 drivers, S3 + IAM for Tableflow.

### Run the Demo

The race simulator starts automatically after `terraform apply`. It runs a 57-lap race in ~9.5 minutes.

During the demo recording, use Claude Desktop with MCP to:
1. Deploy MQ Source Connector (creates `race-standings` topic)
2. Deploy CDC Debezium Connector (creates `drivers` topic)
3. Build enrichment pipeline with `AI_DETECT_ANOMALIES` via DBT
4. Configure and deploy Streaming Agent
5. Enable Tableflow on `pit-decisions` and `drivers`
6. Query with Databricks Genie

### Teardown

```bash
./scripts/teardown.sh
```

## Demo Scenario

**Team:** River Racing | **Driver:** James River (#44) | **Circuit:** Silverstone

- Laps 1-31: Tires degrade, position drops from P3 to P8
- Lap 32: Front-left tire anomaly detected (145°C spike). Agent says **PIT NOW**.
- Laps 33-57: Fresh mediums, recovers to P3. **+5 positions gained.**

## Documentation

- [Design Doc](CLAUDE.md) — Full architecture, schemas, SQL, constraints
- [DBT Adapter Setup](docs/SETUP-DBT-ADAPTER.md)
- [Tableflow Setup](docs/SETUP-TABLEFLOW.md)
- [Databricks Genie Setup](docs/SETUP-GENIE.md)
