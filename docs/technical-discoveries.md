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

10b. **`ML_DETECT_ANOMALIES` is the default; `AI_DETECT_ANOMALIES` is an opt-in that
    does not currently work** — LAB 3 ships the GA ARIMA `ML_DETECT_ANOMALIES` in
    `demo-reference/enrichment_anomaly.sql`. Granite was tried as the primary path and
    reverted: `AI_DETECT_ANOMALIES` submits and forecasts cleanly but never populates
    `is_anomaly` or the bounds, so the demo silently loses its anomaly (item 13b — read
    that before reconsidering). It is kept at
    `demo-reference/enrichment_anomaly_ai.sql`, selected with `F1_ANOMALY_FN=ai`
    (`scripts/common/simulator_control.py`), and the lab guides carry its `anomaly` CTE
    in a collapsed `<details>` block.
    Two further gates on `AI_` independent of item 13b: it resolves only under an Early
    Access Program **not** enabled on a fresh environment ("Function ... does not exist
    or you do not have permission to access it"), and Granite model support is earlier
    still than the EAP itself. The two functions are also **not** quite drop-in — the
    argument order and output struct match, but the config keys do not (item 13).
11. **Temporal join must be BEFORE OVER aggregations** — `JOIN race_standings FOR SYSTEM_TIME AS OF a.window_time` in the final SELECT after OVER aggregations silently returns zero rows. `window_time` loses its rowtime attribute through the OVER chain. Put the temporal join in the `enriched` CTE on the raw stream (using `event_time`), then aggregate the joined result.
12. **Versioned build-side only retains the latest version per key** — If Job 1 deploys after the race ends, the temporal join sees only final-lap standings frozen in state. Deploy Job 1 before the race starts (or rely on `RACE_LOOP` keeping the feed live) so the join sees advancing versions.
13. **The two functions' config keys differ — this is the trap when switching** — `minTrainingSize`/`maxTrainingSize` (ML_) are `minContextSize`/`maxContextSize` (AI_), and **`enableStl` does not exist on `AI_`** (it was ARIMA/STL-specific), so copying an options block across fails at submit. `AI_` adds `'model'` (default `'timesfm-2.5'`; Granite values `'ttm'`, `'flowstate'`, `'patchtstfm'`) and `rmseWindowSize` (default 5). Both share `confidencePercentage`, whose default of `99.0` is too loose for noisy synthetic data — it flags ~1% of normal points. We use `99.99` + context 20/50. Only run it on `tire_temp_fl_c`; other metrics (brake ±25°C, battery) generate too many false positives.
    Note `maxContextSize` is a *rolling* window, so it is not free to enlarge: 512 over a ~360-window race conditions the model on the cool early laps, dragging the forecast below the 0.42°C/lap gradient. We keep 50 to match the ARIMA behavior.
    **Unverified on `AI_`:** `confidencePercentage=99.99` was tuned against ARIMA
    residuals, and a foundation model's bounds are quantile-based, so the same number maps
    to a different interval width. It could not be tuned yet — see item 13b for why the
    attempt was inconclusive. Confirm on a topic that has raced past lap 32 that exactly
    the lap-32 cluster fires (`docs/constraints.md`); if extras appear, raise it or tighten
    only the top edge with `upperBoundConfidencePercentage`.
13b. **`AI_DETECT_ANOMALIES` emits forecasts but NEVER `is_anomaly`/`lower_bound`/
    `upper_bound` — confirmed 2026-07-31 on a full real race, and it breaks LAB 3 silently.**
    What works: the function resolves on our EAP org, `'model' VALUE 'ttm'` (Granite) is
    accepted, argument order is `(value, timestamp, options)` as in `ML_`, every struct
    field *name* the lab SQL reads resolves, `actual_value` populates from row 1, and
    `forecast_value` + `rmse` populate from ~row 21 (matching `minContextSize` 20).
    What does not: `is_anomaly`, `lower_bound` and `upper_bound` are NULL in **every** row.
    Verified on a topic holding a genuine race through lap 38 — the lap-32 spike present
    and correct (123 → 145 → 145°C against forecasts of 108 → 116 → 133°C) and ~150 windows
    of context, far past the gate. Also invariant across confidence (99.99 / 80.0 /
    default 99.0 / asymmetric `upperBoundConfidencePercentage` 95.0), model (`ttm` vs
    default `timesfm-2.5`, so not Granite-specific), input shape (raw 2s stream vs 10s
    `TUMBLE`), and context (`min` 10–20, `max` 50–512).
    So LAB 3's `CASE WHEN ...is_anomaly AND ...actual_value > ...upper_bound` can never be
    true on this build, and **nothing errors** — `car_state` fills normally with
    `anomaly_tire_temp_fl = false` forever.
13c. **A `rmse`-ratio anomaly test does NOT work as a substitute — the rolling RMSE
    absorbs the spike.** The obvious workaround for 13b is
    `(actual_value - forecast_value) > k * rmse`, since those three fields do populate.
    Measured: it does not discriminate. `rmse` jumps 0.48 → 6.85 → 14.97 → 16.06 as the
    spike enters its `rmseWindowSize` (default 5) window, so the ratio at lap 32 is only
    ~2.0–2.2 — no larger than normal laps (lap 29 also hit 2.0). A `k=4` filter returns
    **zero** rows across the whole race; `k=2` fires on healthy laps too.
    What *does* separate cleanly is the **raw** deviation: `|actual - forecast|` stays
    ≤ ~1.5°C on laps 20–31 and reaches 13–30°C at lap 32 — roughly 10× headroom. A test
    would have to be `actual_value - forecast_value > <°C>` (one-sided, so the lap-33
    post-pit cold drop stays suppressed), not a multiple of `rmse`.
13d. **Do not trust a bare streaming aggregate as a data-presence check.**
    `SELECT MAX(lap), COUNT(*) FROM car_telemetry` under a `timeout` is an *incremental*
    streaming aggregate: it emits partial results as it consumes, so killing it early
    reports whatever prefix it reached. It printed `max_lap=0, n=25` on a topic that
    demonstrably held lap 38 and 145°C. Two probes' worth of wrong conclusions came from
    reading that as ground truth. Verify data presence with a **filtered detail query**
    (`WHERE lap BETWEEN 25 AND 40`) and let it run, or check ECS/simulator logs instead.
    Two more traps in the same family, both of which produced a wrong reading here:
    (a) `f1-sql` prints booleans **uppercase** (`TRUE`/`FALSE`), so `grep -cE 'True|False'`
    returns 0 on output that is full of them — use `grep -ciE`. (b) Never truncate the
    probe with `head -N` when N lands mid-range: a `head -45` dump stopped three rows
    into lap 32, exactly where the behavior being tested changes.
14. **Anomaly output struct fields** — `is_anomaly` (BOOLEAN, NULL during warmup), `actual_value`, `forecast_value`, `lower_bound`, `upper_bound`, `timestamp` — identical across both functions, which is why `car_state`'s schema is function-independent. `AI_` adds `rmse` (rolling RMSE of predictions — a useful diagnostic ARIMA never gave us: flat and small through laps 1–31 means the model is tracking the gradient) and `aic` (reserved). Filter to `actual_value > upper_bound` to suppress post-pit cold-drop false positives.
15. **Warmup emits NULL `is_anomaly`, not nothing** — during warmup (rows < `minContextSize`), rows are emitted with NULL `is_anomaly`. The LAB 3 `CASE` handles this for free: NULL fails the `WHEN`, so those rows land on `false`.
    **Measured on a live EAP environment (2026-07-31), `AI_DETECT_ANOMALIES`:** rows are
    emitted **immediately**, from the very first window, carrying a populated
    `actual_value` and a NULL `is_anomaly`/`forecast_value`. `forecast_value` starts
    populating at ~row 21, consistent with `minContextSize` 20. So this note is correct
    and the downstream prose is the stale part: everything that describes `car_state` as
    *empty* for the first ~20 windows (the lab guides, `docs/STANDALONE-DEMO.md`, the
    "ready" messages `reset` and `selfservice up` print) is describing the flag gate, not
    an empty topic. Harmless for the demo — the `CASE` maps those rows to `false` — but
    do not debug an "empty car_state" against that prose.

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
