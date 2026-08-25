# Hosted workshop constraints

These constraints protect the hosted workshop scenario. The self-service and standalone tracks change who provisions the environment and where the simulator runs, but they keep the same race, SQL, and expected result.

## Must Have

- Two ingestion paths: direct Kafka (telemetry + standings, produced by the simulator), Postgres CDC (history)
- `ML_DETECT_ANOMALIES` running, but only `tire_temp_fl_c` fires an anomaly
- Single anomaly at lap 24 — no other anomalies in the entire race
- AI agent decides pit strategy — no threshold formulas in Flink SQL
- Recovery from P8 at the agent's call to P1–P2 at the flag
- 20 seconds per simulated lap by default (20-min race, three laps per minute); must match the 20s TUMBLE window in the LAB 3 SQL — tunable via `seconds_per_lap` only with a matching SQL-window change
- Hero entry is fictional: team = River Racing, driver = John Doe, car #88. The rest of the grid uses real 2025 F1 driver names (with fictional team names); car #44 is Lewis Hamilton
- Circuit: Silverstone; 22 drivers, 11 teams
- Per-attendee isolation: separate CC environment/cluster/Flink pool per attendee; shared Postgres/ECR/Bedrock
- Everything provisioned by organizers via `wsa` — driven by `uv run workshop build` — (or `uv run deploy` for a single environment) — attendees never run Terraform
- Race simulator runs as an ECS Fargate service per attendee with `RACE_LOOP=true` so the feed is always live; instructors control fleets via `uv run workshop start-races` / `stop-races`, and a single deployment via `uv run race`
- Single partition topics: `DISTRIBUTED BY (col) INTO 1 BUCKETS`

## Must NOT Have

- Anomalies on any metric other than `tire_temp_fl_c`
- Multiple anomalies at different laps
- Probability formulas or threshold logic in Flink SQL
- Real F1 team names, or replacing the fictional River Racing drivers with real drivers
- A Copilot/chatbot layer on top of the agent output
- Anything outside Confluent Cloud + AWS in the attendee path — no Tableflow, Databricks, dbt, or IBM MQ
- Batch processing anywhere in the pipeline
- Attendee-run infrastructure steps — labs are Flink SQL only (LAB 3 / LAB 4)
