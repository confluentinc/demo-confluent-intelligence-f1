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

## `wsa build` concurrency and Confluent Cloud rate limits

`wsa build` defaults to `--concurrency 10` parallel Terraform runs. For a full `account_count: 10` build that means every account's environment, cluster, service account, role bindings, compute pool, Flink statements, and CDC connector are all created at once — enough simultaneous Confluent Cloud API traffic to trigger `429 Too Many Requests` on plain data-source reads (observed on `data.confluent_organization.main`), and to widen the normal eventual-consistency gap between a service account's role binding becoming visible to Terraform and becoming visible to the Flink SQL control plane. The latter surfaces as `Error looking up resources for Statement. Compute pool or principal not found` on `confluent_flink_statement` resources (both `llm_textgen_model` and `module.topics`'s table-creation statements) — and can repeat across every retry because each retry is still running at the same concurrency.

For builds of ~8+ accounts, pass a lower `--concurrency` (2-4) to `workshop build`/`wsa build`. It's slower but avoids the cascade above. A run that already partially succeeded can be resumed with `--run-id <existing-run-id> --accounts <failed-only>` — wsa's per-account Terraform state under `wsa-output/<run-id>/terraform/aws/terraform.tfstate.d/<account>/` is reused, so this only retries what's actually missing rather than recreating the whole account.

## Recovering from a crashed `terraform apply` (orphaned Confluent resources)

Under the API pressure described above, the Confluent Terraform provider can crash mid-apply (`panic: Failed to serialize resource instance in state: ... has status ObjectStatus(0), which cannot be saved in state`, seen while polling a `confluent_flink_statement`). When this happens, a resource can be fully created in Confluent Cloud but never written to Terraform's state — the crash happens before the state file is flushed. The next apply then tries to create that same resource again and fails with a "name already in use" error (observed on `confluent_connector.postgres_cdc`, whose name is a fixed literal per environment, not templated with the account prefix — that's fine when nothing is orphaned, but means a leftover duplicate collides on the exact name).

To recover: diff the account's actual Terraform state against what's expected (`terraform/modules/topics/main.tf` for the two table statements, `terraform/aws/main.tf` for the top-level resources) against `wsa-output/<run-id>/terraform/aws/terraform.tfstate.d/<account>/terraform.tfstate`, find the resource type missing from state, then look it up directly in Confluent Cloud (`confluent connect cluster list --environment <env> --cluster <cluster>` for connectors) and delete the orphan (`confluent connect cluster delete <id> --environment <env> --cluster <cluster> --force`) so the next `wsa build` retry can create the one Terraform is actually tracking. Re-running at lower concurrency (previous section) makes the crash itself much less likely.

## 1Password item structure for `confluent-cloud/password`

`wsa` and `workshop creds --resolve-op` read each attendee's Console password via `op read 'op://<vault>/Account NNN/confluent-cloud/password'` — a custom section literally labeled `confluent-cloud` (case-insensitive for `op read`, but `wsa accept-account-invitation` normally writes it lowercase) containing `username`/`password` fields, distinct from the item's built-in login username/password. An item missing that section (e.g. because the password was set through some path other than `wsa accept-account-invitation`) makes `wsa build` report "No Confluent Cloud password in 1Password" even though the item exists and its built-in password field is populated — the generic error doesn't distinguish "item missing" from "item present but missing the section." If this happens, compare the account's item structure against a known-good one (`op item get <id> --format json`, diffing `.sections` and `.fields[].section`) rather than assuming the account was never accepted.
