# Technical Discoveries

Hard-won findings from building this workshop. Check here before debugging
anything related to Flink SQL, Terraform, or serialization.

> Findings specific to architecture that has since been removed (IBM MQ + Job 0,
> Tableflow/Databricks Genie, the old `terraform/core` + `terraform/demo` layout)
> were pruned along with that architecture — see git history if you need them.

## Confluent Cloud Flink SQL

1. **CREATE TABLE without WITH clause** — CC auto-creates backing Kafka topic + schema subjects.
2. **DISTRIBUTED BY INTO BUCKETS** — Use `DISTRIBUTED BY (col) INTO 1 BUCKETS` to control partition count. `kafka.partitions` WITH option is deprecated.
3. **COMMENT on columns** — `column_name TYPE COMMENT 'description'` adds descriptions stored in Schema Registry.
4. **`confluent_flink_statement`** — Terraform resource for submitting any Flink SQL. Requires `rest_endpoint` + `credentials` block.
5. **Required providers in modules** — Each Terraform module using `confluent_*` resources needs its own `required_providers` block with `source = "confluentinc/confluent"`.
6. **Streaming Agents = Flink SQL** — `CREATE AGENT` DDL, not YAML, not REST API.
7. **`PROCTIME()` not supported** — `FOR SYSTEM_TIME AS OF PROCTIME()` is invalid. CC Flink supports event-time temporal joins only (`FOR SYSTEM_TIME AS OF t.event_time`).
8. **`'value.format' = 'json'` not supported** — Only `avro-registry`, `json-registry`, and `raw` are valid. Use `json-registry` (paired with `"output.data.format": "JSON_SR"` on the connector side). Plain `json` returns "Unsupported format: json".
9. **Default scan startup mode is `latest`** — A new INSERT INTO / SELECT only sees messages arriving after it starts. Deploy Flink jobs before starting the race, or add `/*+ OPTIONS('scan.startup.mode'='earliest-offset') */`. **But check the table's own options before concluding a query starts from `latest`:** `car_telemetry`'s CREATE TABLE sets `'scan.startup.mode' = 'earliest-offset'` (`terraform/modules/topics/main.tf`), so LAB 3 replays it from the beginning with no inline hint at all, while `race_standings` — the versioned side of the same temporal join — keeps the `latest` default. Reading only `demo-reference/*.sql` will give you the wrong answer.
10. **Don't rebuild resource names in outputs** — Outputs must read names from the actual resource (e.g. `module.cluster.cluster_name` sourced from `confluent_kafka_cluster.main.display_name`), never reconstruct them from locals like `"${local.name_prefix}-CLUSTER"`. Reconstructed names silently drift when suffixes change, causing "current database not set" in downstream Flink statements.

## Flink Job 1 — Enrichment + Anomaly

10b. **Function is `ML_DETECT_ANOMALIES`, not `AI_DETECT_ANOMALIES`** — A fresh
    Confluent Cloud environment exposes the GA `ML_DETECT_ANOMALIES`;
    `AI_DETECT_ANOMALIES` only resolves under an Early Access Program that is not
    enabled by default and fails with "Function ... does not exist or you do not
    have permission to access it." The two are drop-in compatible (same
    signature and output struct), so the labs and `demo-reference/` use
    `ML_DETECT_ANOMALIES`. If your org has the EAP enabled and prefers the
    `AI_` name, a global rename back is safe.
11. **Temporal join must be BEFORE OVER aggregations** — `JOIN race_standings FOR SYSTEM_TIME AS OF a.window_time` in the final SELECT after OVER aggregations silently returns zero rows. `window_time` loses its rowtime attribute through the OVER chain. Put the temporal join in the `enriched` CTE on the raw stream (using `event_time`), then aggregate the joined result.
12. **Versioned build-side only retains the latest version per key** — If Job 1 deploys after the race ends, the temporal join sees only final-lap standings frozen in state. Deploy Job 1 before the race starts (or rely on `RACE_LOOP` keeping the feed live) so the join sees advancing versions.
13. **ML_DETECT_ANOMALIES default thresholds too loose for noisy synthetic data** — Default `confidencePercentage=99.0` flags ~1% of normal points. Use `99.99` + `minTrainingSize=20` + `maxTrainingSize=50`. Only run it on `tire_temp_fl_c`; other metrics (brake ±25°C, battery) generate too many false positives.
14. **ML_DETECT_ANOMALIES output struct fields** — `is_anomaly` (BOOLEAN, NULL during warmup), `actual_value`, `forecast_value`, `lower_bound`, `upper_bound`, `timestamp`. Filter to `actual_value > upper_bound` to suppress post-pit cold-drop false positives.
15. **ML_DETECT_ANOMALIES warmup** — During warmup (rows < `minContextSize`), rows are emitted with NULL `is_anomaly`. This is normal.
    **Unverified against the current labs:** everything downstream (the lab guides,
    `docs/STANDALONE-DEMO.md`, the "ready" messages `reset` and `selfservice up`
    print) describes `car_state` as *empty* for the first ~20 windows rather than
    populated with NULL anomaly flags — and our SQL passes `minTrainingSize`, not
    `minContextSize`. If you see NULL-flagged rows arriving immediately, this note is
    why; if you see nothing until training completes, this note is the stale one.
    Confirm on a live environment before relying on either.

## Terraform & Infrastructure

16. **SR data source timing** — Schema Registry takes time after environment creation. Move SR data source to cluster module with `depends_on = [confluent_api_key.app]` (app key takes ~2 min — natural delay). No `time_sleep` needed.
17. **Docker `--platform linux/amd64`** — ECS Fargate is x86_64; Mac builds ARM.
18. **Managed `PostgresSource` connector config keys** — Uses `connection.host`, `connection.port`, `connection.user`, `connection.password`, `db.name` — NOT Debezium-style `database.*` keys.
19. **`AWS_RETRY_MODE=adaptive` + `AWS_MAX_ATTEMPTS=10`** — Required in `deploy.py` before calling `run_terraform()`. The AWS provider's default retry doesn't cover network-layer DNS failures.

## Data & Serialization

20. **Avro serialization** — `AvroSerializer(schema_str=None, conf={'auto.register.schemas': False, 'use.latest.version': True})`. Uses schema registered by Flink CREATE TABLE.
21. **Schema Registry API key** — Separate from Kafka API key. `EnvironmentAdmin` role covers SR access.
22. **Temporal join needs both watermarks** — Both sides need advancing watermarks. Versioned table needs PRIMARY KEY + watermark.
23. **No PRIMARY KEY on `car_telemetry`** — Adding PRIMARY KEY registers an Avro INT key schema. The simulator writes string keys, causing Job 1 deserialization failures. `car_telemetry` is append-only and windowed — no PRIMARY KEY needed. Keep PRIMARY KEY only on `race_standings` (needs versioned-table semantics).
24. **Schema Registry hard-delete required after DROP TABLE** — Flink DROP TABLE deletes the Kafka topic but leaves `<topic>-key` and `<topic>-value` subjects in SR. Recreating with a different schema fails. Fix: `confluent schema-registry schema delete --subject <topic>-value --version all` then `--permanent`, for both key and value subjects.
25. **Postgres table is `driver_race_history`, not `race_results`** — The CDC Reroute SMT propagates the table name to the Kafka topic.

## Git & Deployment

26. **Standalone git repo** — Has its own `.git` at project root, separate from any parent monorepo. Remote: `confluentinc/demo-confluent-intelligence-f1`.
27. **`git push-external`** — Required for pushing to `confluentinc` org repos (Confluent airlock security policy).
