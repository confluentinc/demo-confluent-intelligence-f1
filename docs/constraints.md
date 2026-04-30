# Demo Constraints

These are hard constraints for this demo. Do not violate them.

## Must Have

- Three data sources, three ingestion paths: direct Kafka (telemetry), MQ pub/sub (standings), Postgres CDC (history)
- `AI_DETECT_ANOMALIES` running, but only `tire_temp_fl_c` fires an anomaly
- Single anomaly at lap 32 — no other anomalies in the entire race
- AI agent decides pit strategy — no threshold formulas in Flink SQL
- +6 positions gained after agent recommendation (P8 → P2)
- 10 seconds per simulated lap (~9.5 min total race)
- All fictional drivers and teams: team = River Racing, driver = Sean Falconer, car #44
- Circuit: Silverstone; 22 drivers, 11 teams
- Tableflow on `pit_decisions` + `driver_race_history` only
- Databricks Genie for analytics
- Everything deployable via Terraform + Python scripts
- Race simulator runs on ECS Fargate, started manually via `./scripts/start-race.sh`
- MQ messages sent as JMS TextMessage (RFH2 headers) — do not use BytesMessage
- Single partition topics: `DISTRIBUTED BY (col) INTO 1 BUCKETS`

## Must NOT Have

- Anomalies on any metric other than `tire_temp_fl_c`
- Multiple anomalies at different laps
- Probability formulas or threshold logic in Flink SQL
- Real driver or team names
- A Copilot/chatbot layer on top of the agent output
- Tableflow on `race_standings` or `car_telemetry`
- Batch processing anywhere in the pipeline
- MQ queues — use pub/sub topic `dev/race_standings` with durable subscription `f1-mq-source-sub`; do not revert to queues
