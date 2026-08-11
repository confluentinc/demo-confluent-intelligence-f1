# Workshop Constraints

These are hard constraints for this workshop. Do not violate them.

## Must Have

- Two ingestion paths: direct Kafka (telemetry + standings, produced by the simulator), Postgres CDC (history)
- `ML_DETECT_ANOMALIES` running, but only `tire_temp_fl_c` fires an anomaly
- Single anomaly at lap 32 — no other anomalies in the entire race
- AI agent decides pit strategy — no threshold formulas in Flink SQL
- +6 positions gained after agent recommendation (P8 → P2)
- 60 seconds per simulated lap by default (60-min race, one lap per minute, spans the full lab session); tunable via `seconds_per_lap`
- Hero entry is fictional: team = River Racing, driver = John Doe, car #88. The rest of the grid uses real 2025 F1 driver names (with fictional team names); car #44 is Lewis Hamilton
- Circuit: Silverstone; 22 drivers, 11 teams
- Per-attendee isolation: separate CC environment/cluster/Flink pool per attendee; shared Postgres/ECR/Bedrock
- Everything provisioned by organizers via `wsa` — driven by `uv run workshop build` — (or `uv run deploy` for a single environment) — attendees never run Terraform
- Race simulator runs as an ECS Fargate service per attendee with `RACE_LOOP=true`; workshop services are provisioned and prepared at desired count 0, then the organizer starts them through manifest-scoped `uv run workshop start-races` / `stop-races` commands. A single deployment uses `uv run race`.
- Single partition topics: `DISTRIBUTED BY (col) INTO 1 BUCKETS`

## Must NOT Have

- Anomalies on any metric other than `tire_temp_fl_c`
- Multiple anomalies at different laps
- Probability formulas or threshold logic in Flink SQL
- Real driver or team names
- A Copilot/chatbot layer on top of the agent output
- Anything outside Confluent Cloud + AWS in the attendee path — no Tableflow, Databricks, dbt, or IBM MQ
- Batch processing anywhere in the pipeline
- Attendee-run infrastructure steps — labs are Flink SQL only (LAB 3 / LAB 4)
