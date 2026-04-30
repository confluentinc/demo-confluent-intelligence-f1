# Technical Discoveries

Hard-won findings from building this demo. Check here before debugging anything related to Flink SQL, MQ, Terraform, or serialization.

## Confluent Cloud Flink SQL

1. **CREATE TABLE without WITH clause** — CC auto-creates backing Kafka topic + schema subjects.
2. **DISTRIBUTED BY INTO BUCKETS** — Use `DISTRIBUTED BY (col) INTO 1 BUCKETS` to control partition count. `kafka.partitions` WITH option is deprecated.
3. **COMMENT on columns** — `column_name TYPE COMMENT 'description'` adds descriptions stored in Schema Registry.
4. **`confluent_flink_statement`** — Terraform resource for submitting any Flink SQL. Requires `rest_endpoint` + `credentials` block.
5. **Required providers in modules** — Each Terraform module using `confluent_*` resources needs its own `required_providers` block with `source = "confluentinc/confluent"`.
6. **Streaming Agents = Flink SQL** — `CREATE AGENT` DDL, not YAML, not REST API.
7. **`PROCTIME()` not supported** — `FOR SYSTEM_TIME AS OF PROCTIME()` is invalid. CC Flink supports event-time temporal joins only (`FOR SYSTEM_TIME AS OF t.event_time`).
8. **`'value.format' = 'json'` not supported** — Only `avro-registry`, `json-registry`, and `raw` are valid. Use `json-registry` (paired with `"output.data.format": "JSON_SR"` on the connector side). Plain `json` returns "Unsupported format: json".
9. **Default scan startup mode is `latest`** — A new INSERT INTO / SELECT only sees messages arriving after it starts. Deploy Flink jobs before starting the race, or add `/*+ OPTIONS('scan.startup.mode'='earliest-offset') */`.
10. **Don't rebuild resource names in outputs** — `core/outputs.tf` must read `module.cluster.cluster_name` (which sources from `confluent_kafka_cluster.main.display_name`), not reconstruct it from locals. Reconstructed names silently drift when suffixes change, causing "current database not set" in downstream Flink statements.
11. **`outputs.tf` cluster_name = single source of truth** — Read from the actual resource, not from `"${local.name_prefix}-CLUSTER"`.

## Flink Job 1 — Enrichment + Anomaly

12. **Temporal join must be BEFORE OVER aggregations** — `JOIN race-standings FOR SYSTEM_TIME AS OF a.window_time` in the final SELECT after OVER aggregations silently returns zero rows. `window_time` loses its rowtime attribute through the OVER chain. Put the temporal join in the `enriched` CTE on the raw stream (using `event_time`), then aggregate the joined result.
13. **Pre-creating `race-standings-raw` fixes stale temporal join data** — The versioned build-side of the temporal join only retains the latest version per key. If Job 1 deploys after the race ends, it sees only final-lap standings frozen in state. Deploy Job 0 and Job 1 before the race starts so the join sees live data.
14. **AI_DETECT_ANOMALIES default thresholds too loose for noisy synthetic data** — Default `confidencePercentage=99.0` flags ~1% of normal points. Use `99.99` + `minTrainingSize=20` + `maxTrainingSize=50`. Only run it on `tire_temp_fl_c`; other metrics (brake ±25°C, battery) generate too many false positives.
15. **AI_DETECT_ANOMALIES output struct fields** — `is_anomaly` (BOOLEAN, NULL during warmup), `actual_value`, `forecast_value`, `lower_bound`, `upper_bound`, `timestamp`. Filter to `actual_value > upper_bound` to suppress post-pit cold-drop false positives.
16. **AI_DETECT_ANOMALIES warmup** — During warmup (rows < `minContextSize`), rows are emitted with NULL `is_anomaly`. This is normal.

## MQ Connector + JMS

17. **MQ connector always wraps in JMS envelope** — Regardless of output format, the connector produces a JMS struct with `text`, `bytes`, `properties`, `messageType`, etc. Raw JSON is in `text`, not at top level.
18. **JMS TextMessage vs BytesMessage** — Default `pymqi.Queue.put()` sends BytesMessage (payload base64 in `bytes`). Use `put_rfh2()` with RFH2 headers to send TextMessage (payload as string in `text`).
19. **RFH2 header chain** — `md.Format = MQFMT_RF_HEADER_2`, `rfh2["Format"] = MQFMT_STRING`, folder `<mcd><Msd>jms_text</Msd></mcd>`. Chain: MQMD → RFH2 → STRING payload.
20. **`jms.destination.name` not `mq.queue`** — The managed MQ connector config key for the destination is `jms.destination.name`.
21. **ValueToKey SMT fails on JMS envelope** — Can't extract `car_number` from nested `text` field via SMT. Use Flink Job 0 to parse + key instead.
22. **Schema compatibility conflicts** — Pre-created Flink flat schema conflicts with the connector's JMS envelope schema on the same topic. Solution: connector writes to `-raw` topic with JMS envelope; Job 0 transforms to clean topic.
23. **MQ pub/sub requires `admin` channel** — The `app` user lacks topic permissions (`SYSTEM.BASE.TOPIC` auth). Use `DEV.ADMIN.SVRCONN` channel with `admin` credentials. Connector uses durable subscription (`jms.subscription.durable: true`, name: `f1-mq-source-sub`), topic: `dev/race-standings`. Do not revert to queues.
24. **MQ retained publication + durable subscription = 2 Kafka records** — One `MQPMO_RETAIN` publish lands in `race-standings-raw` as two identical records. Cosmetic. Job 0's `WHERE car_number > 0` filter handles it.

## Terraform & Infrastructure

25. **SR data source timing** — Schema Registry takes time after environment creation. Move SR data source to cluster module with `depends_on = [confluent_api_key.app]` (app key takes ~2 min — natural delay). No `time_sleep` needed.
26. **`customer_role_arn` not `iam_role_arn`** — `confluent_provider_integration` AWS block uses `customer_role_arn`.
27. **Docker `--platform linux/amd64`** — ECS Fargate is x86_64; Mac builds ARM. IBM MQ C client tar is x86_64 only.
28. **Dockerfile needs `gcc`** — `pymqi` compiles C extensions. Add `gcc libc6-dev` to `apt-get install`.
29. **Auto-generated connector configs** — `local_file` resources in Terraform write `generated/mq_connector_config.json` and `generated/cdc_connector_config.json` with real values. The managed `PostgresSource` connector uses `connection.host`, `connection.port`, `connection.user`, `connection.password`, `db.name` — NOT Debezium-style `database.*` keys.
30. **ECS task definition rebuild** — `null_resource.docker_build_push` triggers on file hashes. After `datagen/` changes: `terraform -chdir=terraform/demo taint module.ecs.null_resource.docker_build_push && terraform apply`.
31. **Naming convention** — `name_prefix = "RIVER-RACING-${var.deployment_id}"` is built once as a local in `terraform/core/main.tf` and `terraform/demo/main.tf`. CC resources use it verbatim with uppercase suffixes. AWS modules accept `var.name_prefix` and apply `lower()` internally (S3/ECR/IAM require lowercase).
32. **AWS modules take `name_prefix`, not `deployment_id`** — Each AWS module (`mq`, `postgres`, `ecs`, `tableflow`) receives `var.name_prefix` and builds `${lower(var.name_prefix)}-<role>`. Don't pass `deployment_id` and reconstruct the prefix inside modules.
33. **`deployment_id` accepts uppercase** — Validation accepts any alphanumeric ≤8 chars. Lowercase only happens inside AWS modules.
34. **Tableflow `bucket_name` derived in module** — `${lower(var.name_prefix)}-tableflow-${random_id.suffix.hex}`. Not passed as a variable.
35. **`pymqi` requires `$HOME`** — IBM MQ client probes `$HOME` for trace logs. EC2 user_data runs as root with no HOME, causing `AMQ6235E: Directory '$HOME' missing`. Fix: `export HOME=/root` in user_data.
36. **`AWS_RETRY_MODE=adaptive` + `AWS_MAX_ATTEMPTS=10`** — Required in `deploy.py` before calling `run_terraform()`. The AWS provider's default retry doesn't cover network-layer DNS failures.
37. **MQ EC2 user_data materialises `race-standings-raw`** — The user_data publishes one retained warmup message on boot. The connector picks it up and creates the topic + schema. Without this, Job 0 validation fails (`Table 'race-standings-raw' does not exist`).

## Data & Serialization

38. **Avro serialization** — `AvroSerializer(schema_str=None, conf={'auto.register.schemas': False, 'use.latest.version': True})`. Uses schema registered by Flink CREATE TABLE.
39. **Schema Registry API key** — Separate from Kafka API key. `EnvironmentAdmin` role covers SR access.
40. **Temporal join needs both watermarks** — Both sides need advancing watermarks. Versioned table needs PRIMARY KEY + watermark.
41. **No PRIMARY KEY on `car-telemetry`** — Adding PRIMARY KEY registers an Avro INT key schema. The simulator writes string keys, causing Job 1 deserialization failures. `car-telemetry` is append-only and windowed — no PRIMARY KEY needed. Keep PRIMARY KEY only on `race-standings` (needs versioned-table semantics).
42. **Schema Registry hard-delete required after DROP TABLE** — Flink DROP TABLE deletes the Kafka topic but leaves `<topic>-key` and `<topic>-value` subjects in SR. Recreating with a different schema fails. Fix: `confluent schema-registry schema delete --subject <topic>-value --version all` then `--permanent`, for both key and value subjects.
43. **Postgres table is `driver_race_history`, not `race_results`** — The CDC Reroute SMT propagates the table name to the Kafka topic and Tableflow Delta Lake table. Genie queries reference `f1_demo.driver_race_history`.

## Git & Deployment

44. **Standalone git repo** — Has its own `.git` at project root, separate from any parent monorepo. Remote: `confluentinc/demo-confluent-intelligence-f1`.
45. **`git push-external`** — Required for pushing to `confluentinc` org repos (Confluent airlock security policy).
