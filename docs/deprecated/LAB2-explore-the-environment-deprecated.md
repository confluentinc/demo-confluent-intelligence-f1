# LAB 2 — Explore the Environment (deprecated)

> Retained for reference. Use the canonical [`Walkthrough.md`](../../Walkthrough.md).

## Overview

Everything that feeds the pipeline is already running. Before you build anything,
explore the pre-provisioned pieces and confirm live data is flowing — all from
the SQL workspace you opened in LAB 1.

### What you'll accomplish

1. Inspect the source tables and their shapes
2. Confirm the live race feed is producing
3. Find the historical CDC data and the pre-deployed LLM models

### Prerequisites

[LAB 1](LAB1-open-your-environment-deprecated.md) — you're signed in with a SQL workspace open.

## Steps

### Step 1: The tables

```sql
SHOW TABLES;
```

| Table | Source | Format |
|-------|--------|--------|
| `car_telemetry` | Race simulator — car #88 sensors, ~5 readings/lap | Avro |
| `race_standings` | Race simulator — all 22 cars, keyed by `car_number` (upsert) | Avro |
| `driver_race_history` | CDC from the shared Postgres (198 historical rows) | JSON |

> **Self-service track?** If you provisioned with `uv run selfservice up`,
> `driver_race_history` is **Avro**, not JSON — there is no CDC connector there, so
> the same 198 rows are seeded by a bounded Flink `INSERT`. The other two tables and
> every query in this lab are identical.

Look at the telemetry stream (Ctrl-C to stop and return to the prompt):

```sql
SELECT car_number, lap, tire_temp_fl_c, tire_pressure_fl_psi, engine_temp_c
FROM car_telemetry;
```

You should see readings arriving live. Now the standings:

```sql
SELECT car_number, `position`, gap_to_leader_sec, tire_compound, tire_age_laps
FROM race_standings;
```

One row per car with live position, gaps, and tire context.

> The simulator produces `race_standings` straight to
> Kafka as keyed Avro, so the table is already a clean upsert (versioned) table —
> exactly what the temporal join in LAB 3 needs.

### Step 2: The historical CDC data

The `f1-postgres-cdc` connector streams each driver's prior-race tire strategies
and results from the shared Postgres into your cluster. Confirm it landed:

```sql
SELECT COUNT(*) FROM driver_race_history;
```

This converges to **198** rows. (It's a streaming count, so you'll see the value
climb to 198 and settle — Ctrl-C once it stops.) This is historical context the
agent's reasoning can draw on in LAB 4.

> **Self-service track?** There is no connector and no Postgres to look at:
> `uv run selfservice up` writes the same 198 rows with a bounded Flink `INSERT`
> before handing the environment over, so the count is already complete.

### Step 3: The pre-deployed AI models

The agent in LAB 4 uses a Bedrock-backed model that's already created for you:

```sql
SHOW MODELS;
```

You should see `llm_textgen_model` (Bedrock / Claude, for the agent) and
`llm_embedding_model`. You can also confirm the Bedrock connections exist:

```sql
SHOW CONNECTIONS;
```

These are pre-created so you don't have to set up the Bedrock connection
yourself.

## Conclusion

Source data is flowing and the AI models are ready. Now build the intelligence
layer in [LAB 3 — Stream processing](LAB3-stream-processing-deprecated.md).
