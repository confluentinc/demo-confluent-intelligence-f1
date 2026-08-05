# LAB 3 — Stream Processing: Enrichment + Anomaly Detection

## Overview

Build the intelligence layer. You'll combine the live telemetry and standings
into a single `car_state` stream and detect the front-left tire-temperature
anomaly that signals a failing tire — using Flink's built-in
`ML_DETECT_ANOMALIES` — then use IBM Granite to forecast the next three
tire-temperature windows.

### What you'll accomplish

1. Tumble `car_telemetry` into 10-second windows
2. Temporal-join with `race_standings` to add position, gaps, and tire context
3. Run `ML_DETECT_ANOMALIES` on `tire_temp_fl_c`
4. Produce the `car_state` table
5. Run `AI_FORECAST` with IBM Granite TinyTimeMixer (`ttm`)

### Prerequisites

[LAB 2](../LAB2_explore_environment/LAB2.md) — data is flowing, models exist.

## Steps

### Step 1: Create the `car_state` table

In your SQL workspace, paste the whole statement below into a cell and run it:

```sql
CREATE TABLE `car_state`
WITH ('changelog.mode' = 'append')
AS
WITH enriched AS (
  SELECT
    t.car_number, t.event_time, t.lap,
    t.tire_temp_fl_c, t.tire_temp_fr_c, t.tire_temp_rl_c, t.tire_temp_rr_c,
    t.tire_pressure_fl_psi, t.tire_pressure_fr_psi,
    t.tire_pressure_rl_psi, t.tire_pressure_rr_psi,
    t.engine_temp_c, t.brake_temp_fl_c, t.brake_temp_fr_c,
    t.battery_charge_pct, t.fuel_remaining_kg,
    r.`position`, r.gap_to_ahead_sec, r.gap_to_leader_sec,
    r.pit_stops, r.tire_compound, r.tire_age_laps
  FROM `car_telemetry` t
  JOIN `race_standings` FOR SYSTEM_TIME AS OF t.event_time AS r
    ON t.car_number = r.car_number
),
windowed AS (
  SELECT
    window_start, window_end, window_time, car_number,
    MAX(lap) AS lap,
    AVG(tire_temp_fl_c) AS tire_temp_fl_c,
    AVG(tire_temp_fr_c) AS tire_temp_fr_c,
    AVG(tire_temp_rl_c) AS tire_temp_rl_c,
    AVG(tire_temp_rr_c) AS tire_temp_rr_c,
    AVG(tire_pressure_fl_psi) AS tire_pressure_fl_psi,
    AVG(tire_pressure_fr_psi) AS tire_pressure_fr_psi,
    AVG(tire_pressure_rl_psi) AS tire_pressure_rl_psi,
    AVG(tire_pressure_rr_psi) AS tire_pressure_rr_psi,
    AVG(engine_temp_c) AS engine_temp_c,
    AVG(brake_temp_fl_c) AS brake_temp_fl_c,
    AVG(brake_temp_fr_c) AS brake_temp_fr_c,
    AVG(battery_charge_pct) AS battery_charge_pct,
    AVG(fuel_remaining_kg) AS fuel_remaining_kg,
    MAX(`position`) AS `position`,
    MAX(gap_to_ahead_sec) AS gap_to_ahead_sec,
    MAX(gap_to_leader_sec) AS gap_to_leader_sec,
    MAX(pit_stops) AS pit_stops,
    MAX(tire_compound) AS tire_compound,
    MAX(tire_age_laps) AS tire_age_laps
  FROM TABLE(
    TUMBLE(TABLE enriched, DESCRIPTOR(event_time), INTERVAL '10' SECOND)
  )
  GROUP BY window_start, window_end, window_time, car_number
),
anomaly AS (
  SELECT
    *,
    ML_DETECT_ANOMALIES(tire_temp_fl_c, window_time,
      JSON_OBJECT('minTrainingSize' VALUE 20,
                  'maxTrainingSize' VALUE 50,
                  'confidencePercentage' VALUE 99.99,
                  'enableStl' VALUE FALSE))
      OVER (PARTITION BY car_number ORDER BY window_time RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
      AS anomaly_tire_temp_fl_result
  FROM windowed
)
SELECT
  car_number, lap,
  tire_temp_fl_c, tire_temp_fr_c, tire_temp_rl_c, tire_temp_rr_c,
  tire_pressure_fl_psi, tire_pressure_fr_psi,
  tire_pressure_rl_psi, tire_pressure_rr_psi,
  engine_temp_c, brake_temp_fl_c, brake_temp_fr_c,
  battery_charge_pct, fuel_remaining_kg,
  CASE
    WHEN anomaly_tire_temp_fl_result.is_anomaly
         AND anomaly_tire_temp_fl_result.actual_value
             > anomaly_tire_temp_fl_result.upper_bound
    THEN true
    ELSE false
  END AS anomaly_tire_temp_fl,
  `position`, gap_to_ahead_sec, gap_to_leader_sec,
  pit_stops, tire_compound, tire_age_laps
FROM anomaly
WHERE lap > 0;
```

The shell will report the statement as **running** and print its name — this is a
continuous job, so leave it running for the next step.

### Why it's built this way

- **Temporal join before the windows.** The `FOR SYSTEM_TIME AS OF t.event_time`
  join uses `race_standings` as a versioned (upsert) table. It must happen on the
  raw `event_time` rowtime, before the `TUMBLE`/`OVER` aggregations — otherwise
  `window_time` loses its rowtime attribute and the join silently returns zero
  rows.
- **Only `tire_temp_fl_c` runs through `ML_DETECT_ANOMALIES`.** The other sensors
  are noisier and only produce false positives that distract from the story.
- **`actual_value > upper_bound` filter.** This keeps only the *overheating*
  spike as an anomaly, not the cold drop after the pit stop (which is a recovery,
  not a problem).
- **`minTrainingSize` / `enableStl` tune the ARIMA model.** `ML_DETECT_ANOMALIES`
  fits a statistical model per partition, so it needs 20 windows of history before
  it will judge anything, and `maxTrainingSize` keeps that history a *rolling* 50
  windows rather than the whole race. STL seasonal-trend decomposition is off: the
  synthetic data has no seasonality for it to find, only added variance.

> **Bonus — the same job with a foundation model.** Flink also ships
> `AI_DETECT_ANOMALIES`, which swaps ARIMA for a pretrained time-series model you
> pick by name: IBM Granite `ttm` (TinyTimeMixer — small enough to run on CPUs),
> `flowstate`, `patchtstfm`, or Google `timesfm-2.5` (the default if you omit the
> parameter). Nothing else in the statement changes when you swap models — that is
> the point of the feature. It is gated behind an Early Access Program, so on most
> orgs you will see *"Function AI_DETECT_ANOMALIES does not exist or you do not have
> permission to access it."*
>
> **On the build we tested, this variant does not flag the anomaly.** It runs, and
> it forecasts well, but it leaves `is_anomaly` and both bounds NULL — so the `CASE`
> never becomes true and `anomaly_tire_temp_fl` stays `false` all race.
>
> **Read this one; don't run it during the lab.** `car_state` already exists by now,
> so swapping the CTE means dropping and recreating the table — which also strands
> its Schema Registry subjects and takes LAB 4 down with it. If you want to see it
> for yourself, do it after LAB 6, and ask your instructor to reset the environment
> afterwards.
>
> <details>
> <summary>Foundation-model variant — the <code>anomaly</code> CTE to use instead</summary>
>
> ```sql
> anomaly AS (
>   SELECT
>     *,
>     AI_DETECT_ANOMALIES(tire_temp_fl_c, window_time,
>       -- Swap this one line to compare model backends: 'ttm' (IBM Granite
>       -- TinyTimeMixer), 'flowstate', 'patchtstfm', or 'timesfm-2.5' (the default
>       -- when 'model' is omitted). Same function, same output — the point of
>       -- foundation-model support is that this is a one-word change.
>       JSON_OBJECT('model' VALUE 'ttm',
>                   'minContextSize' VALUE 20,
>                   'maxContextSize' VALUE 50,
>                   'confidencePercentage' VALUE 99.99))
>       OVER (PARTITION BY car_number ORDER BY window_time RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
>       AS anomaly_tire_temp_fl_result
>   FROM windowed
> )
> ```
>
> Note the different config keys: `minContextSize`/`maxContextSize` rather than
> `minTrainingSize`/`maxTrainingSize`, and no `enableStl` — that option is
> ARIMA-specific and does not exist here. (`ttm` is pretrained, so there is no
> training phase at all; `minContextSize` is just an emission gate.) Everything
> above and below the CTE stays exactly as written. The full statement is in
> `demo-reference/enrichment_anomaly_ai.sql`.
> </details>

### Step 2: Verify

```sql
SELECT car_number, lap, `position`, tire_compound, tire_age_laps,
       anomaly_tire_temp_fl, tire_temp_fl_c
FROM `car_state`;
```

You should see a row every 10 seconds — six per lap at the default 60s/lap pace.
Around **lap 32**, `anomaly_tire_temp_fl` flips to `true` and `tire_temp_fl_c`
spikes to ~145°C. (Ctrl-C to stop the query.)

> `ML_DETECT_ANOMALIES` needs 20 windows of history before it fires
> (`minTrainingSize`) — 20 × 10 seconds, so about 3½ minutes of live data — and it
> won't flag anything before then. If
> `car_state` stays empty, see
> [troubleshooting](../../shared/troubleshooting.md).

### Step 3: Forecast tire temperature with IBM Granite

Open a new SQL cell and run the query below. It uses the same 10-second tire
temperature windows, but asks the built-in `AI_FORECAST` function for the next
three values. The `model` option selects IBM Granite TinyTimeMixer directly;
there is no connection or model to register.

```sql
WITH windowed AS (
  SELECT
    window_start, window_end, window_time, car_number,
    MAX(lap) AS lap,
    AVG(tire_temp_fl_c) AS tire_temp_fl_c
  FROM TABLE(
    TUMBLE(TABLE `car_telemetry`, DESCRIPTOR(event_time), INTERVAL '10' SECOND)
  )
  GROUP BY window_start, window_end, window_time, car_number
),
forecasted AS (
  SELECT
    *,
    AI_FORECAST(
      tire_temp_fl_c,
      window_time,
      JSON_OBJECT(
        'model' VALUE 'ttm',
        'horizon' VALUE 3,
        'minContextSize' VALUE 20,
        'maxContextSize' VALUE 50,
        'rmseWindowSize' VALUE 5
      )
    ) OVER (
      PARTITION BY car_number
      ORDER BY window_time
      RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS forecast_result
  FROM windowed
)
SELECT
  lap,
  window_time AS forecast_generated_at,
  tire_temp_fl_c AS current_tire_temperature_c,
  forecast_result.forecast AS tire_temperature_forecast,
  forecast_result.metadata AS forecast_metadata
FROM forecasted
WHERE CARDINALITY(forecast_result.forecast) > 0;
```

Each result contains a three-element forecast array. Each element has a future
timestamp and predicted mean. The metadata identifies `ttm`, confirming that
Granite served the forecast. `minContextSize = 20` means the model needs about
3½ minutes of 10-second windows before it can forecast.

This SELECT is only an experiment. After you see forecast rows, stop the
statement in the SQL workspace so LAB 4 has the full compute pool available.
The checked-in copy is
[`granite_tire_forecast.sql`](granite_tire_forecast.sql).

### Step 4 (optional): Publish `car_state` to the Real-Time Context Engine

`car_telemetry` is already published to Confluent's Real-Time Context Engine,
so an AI agent can query the sensor stream over MCP without a Kafka client or a
consumer group. `car_state` isn't, for a simple reason: you just created it, so
it didn't exist when your environment was built.

`race_standings` is intentionally not published. It is a compacted upsert topic,
and RTCE currently rejects queries against it in this workshop environment. The
Flink temporal join in Step 1 still uses its upsert semantics normally.

Turning it on is one toggle:

1. In the Console, go to your cluster → **Topics** → `car_state`.
2. Open the **Real-Time Context Engine** panel and switch it from **Off** to
   **On**.
3. Give it a description. This one matters: the agent *reads* the description to
   decide whether the topic answers a question. Something like:

   > Per-10-second enriched state for River Racing car #88: track position, tire
   > compound and age, front-left tire temperature, and an anomaly flag that fires
   > when the front-left tire is running abnormally hot.

Confluent materializes the topic into a lookup-optimized table, and the three MCP
tools (`list_topics`, `get_metadata`, `query_data`) pick it up automatically.

> Enablement is **per topic**, and a topic needs a registered schema — which the
> `CREATE TABLE` above gave you. If your credential card has an "Ask an AI agent
> about the live race" section, it holds a one-line `claude mcp add` command with
> your endpoint and credentials already filled in.

## Conclusion

`car_state` is the live, enriched, anomaly-aware view of the car, and Granite
can forecast its source telemetry without changing that proven anomaly path.
Feed `car_state` to the AI agent in
[LAB 4 — Streaming agent](../LAB4_streaming_agent/LAB4.md).
