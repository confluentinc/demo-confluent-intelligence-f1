# RTCE lookup → UPSERT → lookup verification

This is a disposable, live verification of RTCE's behavior for an F1 standings
lookup. RTCE itself is read-only: the UPSERT is written through Flink SQL, then
the same row is queried again through RTCE.

Before creating the final demo fixture, confirm that the workshop organization
is enabled in `lightning.cheetahdb.upsert.allowlist`. Without that org-level
feature flag, both valid upsert variants fail with the non-retryable
`MT_UPSERT_NOT_SUPPORTED` error. Changing only `kafka.cleanup-policy` to
`delete` is not a workaround while `changelog.mode` remains `upsert`.

Use a fresh topic name that has never had RTCE enabled for the post-allowlist
verification. Do not drop and recreate a previously enabled topic under the
same name; internal issue `CHEETAH-1418` documents stale data-provider state for
that lifecycle. Rename the table consistently in the setup, baseline, update,
lookup, and cleanup SQL before running the final transcript.

Use the `f1wp050` credential card, never the Terraform-managed
`race_standings` topic. First create the empty test tables:

```bash
uv run f1-sql --creds runs/f7zxf/credentials/f1wp050.env \
  --file demo-reference/rtce_upsert_verification_setup.sql
```

Enable both `rtce_standings_*_test` topics in the Console's **Real-Time Context
Engine** panel (or with `confluent rtce rtce-topic create`) and wait for `ACTIVE`.
Then write the baseline **after** enablement so RTCE has post-enable data to
materialize:

```bash
uv run f1-sql --creds runs/f7zxf/credentials/f1wp050.env \
  --file demo-reference/rtce_upsert_verification_baseline.sql
```

Use the attendee's RTCE MCP endpoint to run these exact lookups:

```sql
SELECT * FROM "rtce_standings_delete_test" WHERE "CAR_NUMBER" = 88
SELECT * FROM "rtce_standings_raw_compact_test" WHERE "KEY" = '88'
```

Save those baseline results, then write the same keys again:

```bash
uv run f1-sql --creds runs/f7zxf/credentials/f1wp050.env \
  --file demo-reference/rtce_upsert_verification_update.sql
```

Repeat the two RTCE lookups until materialization catches up. Success is one
row with the updated values: lap 32, P7, MEDIUM tires, and tire age 0. If the
delete-policy table returns both the lap-31 and lap-32 rows, it is queryable but
has RTCE append semantics; do not present it as an RTCE UPSERT lookup.

The native compacted/raw-key table is the preferred demo candidate if it returns
only the lap-32 row. It is a true current-state lookup and mirrors the existing
`race_standings` story: “Where is John Doe now, and what tires is he on?”

After saving the complete transcript, remove only the disposable objects:

```bash
uv run f1-sql --creds runs/f7zxf/credentials/f1wp050.env \
  --file demo-reference/rtce_upsert_verification_cleanup.sql
```
