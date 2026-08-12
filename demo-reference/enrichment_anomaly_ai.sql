-- Job 1: Enrichment + Anomaly Detection — foundation-model (IBM Granite) variant
--
-- OPT-IN, NOT THE DEFAULT. The canonical LAB 3 path is `enrichment_anomaly.sql`
-- (GA `ML_DETECT_ANOMALIES`, ARIMA). Select this one with:
--   F1_ANOMALY_FN=ai uv run reset --with-labs
--   uv run f1-sql --file demo-reference/enrichment_anomaly_ai.sql
--
-- KNOWN LIMITATION — READ BEFORE DEMOING THIS FILE. On the build measured here,
-- `AI_DETECT_ANOMALIES` resolves, is accepted, and populates `actual_value`,
-- `forecast_value`, and `rmse` correctly. It does NOT populate `is_anomaly`,
-- `upper_bound`, or `lower_bound` — all three stay NULL. The CASE at the bottom
-- therefore can never be true, and NOTHING ERRORS: the statement runs, `car_state`
-- fills normally, and every row carries `anomaly_tire_temp_fl = false`. The lap-32
-- This is the optional foundation-model version of LAB 3. It retains the same
-- car_state schema as the default implementation, making it suitable for
-- evaluation without changing the downstream labs.
--
-- Output schema is identical to the ARIMA version: the same `car_state` columns and
-- the same boolean `anomaly_tire_temp_fl`, so LAB 4/5, the pit wall dashboard, and
-- the social feed cannot tell which one produced it (only whether it ever fires).
--
-- Input: car_telemetry (stream), race_standings (versioned table)
-- Output: car_state (one record per 60-second race lap)
--
-- Design notes (learned from live debugging — keep!):
--
-- 1. Temporal join MUST happen BEFORE the OVER aggregations (in `enriched`),
--    not in the final SELECT. After multiple OVER aggregations, `window_time`
--    loses its rowtime attribute and `FOR SYSTEM_TIME AS OF` silently emits
--    zero rows. Joining on raw `event_time` keeps the rowtime clean.
--
-- 2. Only `tire_temp_fl_c` runs through AI_DETECT_ANOMALIES. The simulator's
--    other metrics (brake/battery/engine) carry too much noise (~±25°C on
--    brakes), and the predictable ones (tire_temp_fr/rl/rr, pressures, fuel)
--    only generate false positives that distract from the demo narrative.
--
-- 3. Config keys are NOT the same as ML_DETECT_ANOMALIES'. `enableStl` was
--    ARIMA/STL-specific and DOES NOT EXIST on AI_DETECT_ANOMALIES — copying the
--    other file's options block across is the likeliest way to break this
--    statement. `minTrainingSize`/`maxTrainingSize` are spelled
--    `minContextSize`/`maxContextSize` here, and because `ttm` is a pretrained
--    foundation model there is no per-partition training phase at all:
--    `minContextSize` is an emission gate, not a training size. Held at 20 so the
--    per-lap sampling stays consistent — measured: `forecast_value` starts
--    populating at about row 21 (roughly 20 minutes at the workshop pace).
--    `maxContextSize=50` keeps the ARIMA version's *rolling* 50-window context: at
--    60 windows per race, a larger value would condition the model on the cool
--    early laps, dragging the forecast below a monotonic 0.42°C/lap gradient.
--    `confidencePercentage=99.99` is carried over from the ARIMA tuning and is
--    UNVERIFIED here — it cannot be tuned while the bounds it controls are NULL.
--
-- 4. The CASE filter restricts anomalies to `actual_value > upper_bound`.
--    On the ARIMA version this is what suppresses the post-pit drop at lap 33
--    (145°C → 95°C), which is semantically a recovery, not a problem. Keep it when
--    the bounds start populating — a foundation model emits that excursion too.
--
-- 5. AI_DETECT_ANOMALIES is gated behind an Early Access Program, and Granite model
--    support is earlier still. On an org without it: "Function AI_DETECT_ANOMALIES
--    does not exist or you do not have permission to access it." Use
--    `enrichment_anomaly.sql` (the default) instead.

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
    TUMBLE(TABLE enriched, DESCRIPTOR(event_time), INTERVAL '60' SECOND)
  )
  GROUP BY window_start, window_end, window_time, car_number
),
anomaly AS (
  SELECT
    *,
    AI_DETECT_ANOMALIES(tire_temp_fl_c, window_time,
      -- Swap this one line to compare model backends: 'ttm' (IBM Granite
      -- TinyTimeMixer), 'flowstate', 'patchtstfm', or 'timesfm-2.5' (the default
      -- when 'model' is omitted). Same function, same output — the point of
      -- foundation-model support is that this is a one-word change.
      JSON_OBJECT('model' VALUE 'ttm',
                  'minContextSize' VALUE 20,
                  'maxContextSize' VALUE 50,
                  'confidencePercentage' VALUE 99.99))
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
