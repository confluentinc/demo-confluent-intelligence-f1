-- Job 1: Enrichment + Anomaly Detection
-- Deployed via DBT as a streaming_table materialization
-- Input: car-telemetry (stream), race-standings (versioned table)
-- Output: car-state (one record per 10-second window)
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
-- 3. Confidence is set to 99.99% with `minContextSize=30` because synthetic
--    tire data has ±1°C noise on a 0.42°C/lap gradient — looser thresholds
--    produce constant false positives.
--
-- 4. The CASE filter restricts anomalies to `actual_value > upper_bound`.
--    Otherwise the post-pit drop at lap 33 (145°C → 95°C) flags a second
--    anomaly that's semantically a recovery, not a problem.

CREATE TABLE `car-state` (
  PRIMARY KEY (car_number) NOT ENFORCED
)
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
  FROM `car-telemetry` t
  JOIN `race-standings` FOR SYSTEM_TIME AS OF t.event_time AS r
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
    AI_DETECT_ANOMALIES(tire_temp_fl_c, window_time,
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
FROM anomaly;
