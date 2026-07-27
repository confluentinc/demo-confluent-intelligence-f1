# LAB 3 — Stream Processing: Enrichment + Anomaly Detection

## Overview

Build the intelligence layer. You'll combine the live telemetry and standings
into a single `car_state` stream and detect the front-left tire-temperature
anomaly that signals a failing tire — using Flink's built-in `ML_DETECT_ANOMALIES`.

### What you'll accomplish

1. Tumble `car_telemetry` into 10-second windows (one row per lap)
2. Temporal-join with `race_standings` to add position, gaps, and tire context
3. Run `ML_DETECT_ANOMALIES` on `tire_temp_fl_c`
4. Produce the `car_state` table

### Prerequisites

[LAB 2](../LAB2_explore_environment/LAB2.md) — data is flowing, models exist.

## Steps

### Step 1: Create the `car_state` table

In your `f1-sql` shell, paste the whole statement below and end it with `;`:

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

### Step 2: Verify

```sql
SELECT car_number, lap, `position`, tire_compound, tire_age_laps,
       anomaly_tire_temp_fl, tire_temp_fl_c
FROM `car_state`;
```

You should see one row per lap. Around **lap 32**, `anomaly_tire_temp_fl` flips
to `true` and `tire_temp_fl_c` spikes to ~145°C. (Ctrl-C to stop the query.)

> `ML_DETECT_ANOMALIES` needs ~20 data points before it fires, so it won't flag
> the first laps. If `car_state` stays empty, see
> [troubleshooting](../../shared/troubleshooting.md).

## Conclusion

`car_state` is the live, enriched, anomaly-aware view of the car. Feed it to the
AI agent in [LAB 4 — Streaming agent](../LAB4_streaming_agent/LAB4.md).
