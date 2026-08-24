# Video-only RTCE UPSERT demo

This is the reusable recording runbook for the existing `f1wp050` deployment.
It is deliberately separate from workshop provisioning, attendee materials,
reset flows, and the social-feed service. The data path is:

```text
race_standings -> continuous Flink UPSERT -> race_standings_rtce -> RTCE MCP
```

Use `runs/f7zxf/credentials/f1wp050.env` and the existing
`bheintz+f1wp50@confluent.io` account. Do not create another user, environment,
cluster, or compute pool.

## One-time setup

Capture the source schema before changing anything, then prove the serving table
name has never existed. Save both outputs with the recording artifacts.

```bash
uv run f1-sql --creds runs/f7zxf/credentials/f1wp050.env \
  --exec 'SHOW CREATE TABLE `race_standings`'
uv run f1-sql --creds runs/f7zxf/credentials/f1wp050.env \
  --exec "SHOW TABLES LIKE 'race_standings_rtce'"
```

The second command must return no rows. If it does, inspect and reuse the
existing object; never drop and recreate the topic under the same name.

Create the raw-key, compacted serving table:

```bash
uv run f1-sql --creds runs/f7zxf/credentials/f1wp050.env \
  --file docs/demo-reference/rtce_upsert_verification_setup.sql
```

Before submitting the continuous feed, paste the contents of
`docs/demo-reference/rtce_upsert_verification_feed.sql` after `EXPLAIN` in the Flink
SQL workspace. The plan is acceptable only when it reports:

- derived upsert key `[key]` and sink primary key `[key]`;
- no `UpsertMaterialize` operator;
- no `UPSERT_AND_PRIMARY_KEYS_DIFFERENT` warning; and
- no `HIGH_STATE_OPERATOR_WITHOUT_TTL` warning.

Do not substitute a direct cast projection. Explicit `INT` to `STRING` casts
currently lose the source upsert-key metadata. The grouped `LAST_VALUE`
reduction establishes `key` as the derived upsert key, and the one-hour
`STATE_TTL` bounds aggregation state. An idle car's last compacted RTCE row
remains available; its next source event upserts the same key again.

Submit the unchanged feed only after the plan passes:

```bash
uv run f1-sql --creds runs/f7zxf/credentials/f1wp050.env \
  --file docs/demo-reference/rtce_upsert_verification_feed.sql
```

Save the printed Flink statement name; that is the only statement stopped after
recording.

Enable RTCE for `race_standings_rtce` in the Console's **Real-Time Context
Engine** panel, or with the currently supported `confluent rtce` command shown
by `confluent rtce --help`, and wait for `ACTIVE`. Allow up to 12 minutes after
`ACTIVE` for the fresh materialization. `DP_INVALID_TABLE` and
`DP_TABLE_NOT_AVAILABLE` are retryable during this window. If it remains
unqueryable after 10–12 minutes, use the existing Lightning on-call escalation;
do not recreate the topic.

## Recording proof

Use the already registered Claude RTCE MCP server. Query by both the raw key and
the business identifier:

```sql
SELECT * FROM "race_standings_rtce" WHERE "KEY" = '88'
SELECT * FROM "race_standings_rtce" WHERE "CAR_NUMBER" = 88
```

Record one current row for car 88, including position, both gaps, tire compound,
tire age, and pit-stop state (`pit_stops` and `in_pit_lane`). Wait for a later
lap, repeat both lookups, and save the transcript proving that the same key now
has the newer lap and that no historical duplicate row exists.

## After recording

Stop only the continuous INSERT by its saved statement name. In the Flink SQL
workspace use **Stop**, or use the matching supported statement-stop command
from `confluent flink statement --help`. Retain `race_standings_rtce`, its Kafka
topic, schemas, and RTCE configuration until the entire f1wp050 environment is
torn down. There is intentionally no cleanup SQL file.
