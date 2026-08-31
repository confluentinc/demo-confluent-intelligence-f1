# Technical notes

These are the current implementation traps that aren't obvious from one source file. Keep live-run observations, account IDs, and unreleased product details in ignored local notes.

## Race cadence

The default race pace is 20 seconds per lap. Lab 3 uses the same 20-second `TUMBLE`, which gives `car_state` one row per lap and limits `AI_RUN_AGENT` to one call per lap. Changing `seconds_per_lap` requires a matching SQL-window change.

The anomaly fires at lap 24. Both anomaly functions need 12 windows of context, so pacing below roughly 10 seconds per lap can reach the anomaly before enough context exists.

The simulator phase-locks to the 20-second wall-clock epoch: it rounds lap 1's start up to the next 20-second boundary, then schedules every subsequent lap from an absolute deadline (`race_start + (lap-1)*20s`) rather than accumulating per-lap sleeps. This guarantees exactly one source lap per Flink `TUMBLE` window, with no cumulative drift; a missed deadline is logged rather than silently absorbed. The consequence is that `f1-race` may wait up to one full lap interval (~20s) before lap 1 appears. Downstream code and docs that assume telemetry starts the instant `f1-race` is launched must account for that delay.

## Table startup and temporal-join order

Join raw `car_telemetry` to the versioned `race_standings` table before any `OVER` or `TUMBLE` operation. Windowing removes the row-time attribute needed by `FOR SYSTEM_TIME AS OF`.

`car_telemetry` starts at the earliest offset through its table definition. `race_standings` starts at the latest offset. Lab 3 must reach `RUNNING` before a new race begins, or the temporal join misses standings versions for earlier laps.

## Anomaly-function variants

The attendee path uses `ML_DETECT_ANOMALIES`. The optional `AI_DETECT_ANOMALIES` file keeps the same output schema and can be selected with `F1_ANOMALY_FN=ai`.

During the July 31, 2026 validation run, the `ttm` variant populated forecasts and RMSE but left `is_anomaly`, `upper_bound`, and `lower_bound` null. It never flagged the lap-24 spike. Re-test the current service before making that variant the default.

The functions use different configuration names:

- `ML_DETECT_ANOMALIES`: `minTrainingSize`, `maxTrainingSize`, and `enableStl`
- `AI_DETECT_ANOMALIES`: `minContextSize` and `maxContextSize`

## Kafka keys and schemas

Keep `car_telemetry` append-only without a primary key. The simulator writes string message keys; adding an integer primary key registers an incompatible Avro key schema.

`race_standings` needs `PRIMARY KEY (car_number) NOT ENFORCED` because the temporal join reads it as a versioned table. The simulator resolves the registered key schema and writes each key in the matching Avro form.

Dropping a Flink table leaves its Schema Registry subjects behind. `scripts/reset.py` permanently removes the stale key and value subjects before recreating lab objects.

## Confluent Cloud Flink SQL

- `PROCTIME()` isn't supported. Use an event-time temporal join.
- Use `json-registry`, not `json`, for JSON backed by Schema Registry.
- Use `WITH (...)` or `ALTER TABLE ... SET` for table options.
- Use `DISTRIBUTED BY (column) INTO 1 BUCKETS` for the workshop's single-partition topics.

## RTCE

RTCE works with `car_telemetry`. The compacted `race_standings` topic is excluded because queries against it fail with `MT_UPSERT_NOT_SUPPORTED` in the current workshop environment. The separate RTCE UPSERT recording demo creates a raw-key serving table to test that path without changing attendee resources.
