# LAB 6 — Wrap-Up (deprecated)

> Retained for reference. Use the canonical [`README.md`](../../README.md).

## Overview

Review what your pipeline produced and reflect on what you built.

### Prerequisites

[LAB 5](LAB5-social-media-agent-deprecated.md) — your Orchestrate social agent
is drafting posts (and `pit_decisions` from [LAB 4](LAB4-streaming-agent-deprecated.md)
is populating).

## Steps

### Step 1: See every non-trivial call

```sql
SELECT lap, `position`, suggestion, condition_summary, reasoning
FROM `pit_decisions`
WHERE suggestion <> 'STAY OUT';
```

Rows stream in roughly in lap order (`pit_decisions` follows `car_state`, which is
produced window by window), so
you'll see the `PIT SOON` warnings as the tire degrades, then the decisive
`PIT NOW` at the anomaly around lap 32 — each with the agent's reasoning.

> **Why no `ORDER BY lap`?** In a continuous Flink query, `ORDER BY` is only
> supported on a time-attribute column — sorting an unbounded stream on a plain
> field like `lap` raises *"Sort on a non-time-attribute field is not supported."*

### Step 2: Inspect the key decision

```sql
SELECT lap, `position`, tire_compound_current, tire_age_laps,
       anomaly_tire_temp_fl, suggestion,
       recommended_tire_compound, recommended_stint_laps, reasoning
FROM `pit_decisions`
WHERE anomaly_tire_temp_fl = true;
```

This is the moment the AI earned its keep: an overheating front-left tire, a
`PIT NOW` call, and a recommended MEDIUM compound for the rest of the race.

### Step 3 (optional): Trace the pipeline

You built a three-stage streaming pipeline:

```
car_telemetry + race_standings  →  car_state  →  pit_decisions
```

Confirm each stage is still live from your shell:

```sql
SHOW TABLES;        -- car_state and pit_decisions now sit alongside the sources
SHOW AGENTS;        -- pit_strategy_agent
```

> For the same graph visually, open the **Stream Lineage** view in your
> environment — every table you built shows up as a node.

## What you built

- A continuously enriched, anomaly-aware `car_state` view joining a live event
  stream to a versioned (upsert) standings table by event time.
- A Flink **Streaming Agent** that calls an LLM per lap and turns raw telemetry
  into an explainable pit-strategy decision — all in SQL, no application code.
- A no-code **IBM watsonx Orchestrate** agent that reads the same live feed and
  drafts on-brand social posts — the streaming pipeline reaching a business user.

## Reset (if you want to run it again)

Your race feed loops continuously, so you can re-run the labs anytime. To clear
your lab objects (`car_state`, `pit_decisions`, `pit_strategy_agent`) and start
fresh, drop them in your SQL workspace:

```sql
DROP TABLE IF EXISTS `pit_decisions`;
DROP TABLE IF EXISTS `car_state`;
DROP AGENT IF EXISTS `pit_strategy_agent`;
```

…or ask your instructor to run `uv run reset` against your environment.

## Done 🏁

Thanks for racing with River Racing. Sign out when you're finished — your
instructor will tear down the environments afterward, and your workshop account's
password is rotated at teardown.
