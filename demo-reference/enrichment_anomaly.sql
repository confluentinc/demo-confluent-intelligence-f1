-- Job 1: Enrichment + Anomaly Detection
-- Deployed via DBT as a streaming_table materialization
-- Input: car-telemetry (stream), race-standings (versioned table)
-- Output: car-state (one record per 10-second window)

CREATE TABLE `car-state` (
  PRIMARY KEY (car_number) NOT ENFORCED
)
WITH ('changelog.mode' = 'append')
AS
WITH windowed AS (
  SELECT
    window_start,
    window_end,
    window_time,
    car_number,
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
    AVG(fuel_remaining_kg) AS fuel_remaining_kg
  FROM TABLE(
    TUMBLE(TABLE `car-telemetry`, DESCRIPTOR(event_time), INTERVAL '10' SECOND)
  )
  GROUP BY window_start, window_end, window_time, car_number
),
anomaly AS (
  SELECT
    *,
    AI_DETECT_ANOMALIES(tire_temp_fl_c, window_time,
      JSON_OBJECT('upperBoundConfidencePercentage' VALUE 99.0, 'lowerBoundConfidencePercentage' VALUE 99.9))
      OVER (ORDER BY window_time RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
      AS anomaly_tire_temp_fl_result,
    AI_DETECT_ANOMALIES(tire_temp_fr_c, window_time,
      JSON_OBJECT('upperBoundConfidencePercentage' VALUE 99.0, 'lowerBoundConfidencePercentage' VALUE 99.9))
      OVER (ORDER BY window_time RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
      AS anomaly_tire_temp_fr_result,
    AI_DETECT_ANOMALIES(tire_temp_rl_c, window_time,
      JSON_OBJECT('upperBoundConfidencePercentage' VALUE 99.0, 'lowerBoundConfidencePercentage' VALUE 99.9))
      OVER (ORDER BY window_time RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
      AS anomaly_tire_temp_rl_result,
    AI_DETECT_ANOMALIES(tire_temp_rr_c, window_time,
      JSON_OBJECT('upperBoundConfidencePercentage' VALUE 99.0, 'lowerBoundConfidencePercentage' VALUE 99.9))
      OVER (ORDER BY window_time RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
      AS anomaly_tire_temp_rr_result,
    AI_DETECT_ANOMALIES(tire_pressure_fl_psi, window_time,
      JSON_OBJECT('upperBoundConfidencePercentage' VALUE 99.0, 'lowerBoundConfidencePercentage' VALUE 99.9))
      OVER (ORDER BY window_time RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
      AS anomaly_tire_pressure_fl_result,
    AI_DETECT_ANOMALIES(tire_pressure_fr_psi, window_time,
      JSON_OBJECT('upperBoundConfidencePercentage' VALUE 99.0, 'lowerBoundConfidencePercentage' VALUE 99.9))
      OVER (ORDER BY window_time RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
      AS anomaly_tire_pressure_fr_result,
    AI_DETECT_ANOMALIES(engine_temp_c, window_time,
      JSON_OBJECT('upperBoundConfidencePercentage' VALUE 99.0, 'lowerBoundConfidencePercentage' VALUE 99.9))
      OVER (ORDER BY window_time RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
      AS anomaly_engine_temp_result,
    AI_DETECT_ANOMALIES(brake_temp_fl_c, window_time,
      JSON_OBJECT('upperBoundConfidencePercentage' VALUE 99.0, 'lowerBoundConfidencePercentage' VALUE 99.9))
      OVER (ORDER BY window_time RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
      AS anomaly_brake_temp_fl_result,
    AI_DETECT_ANOMALIES(brake_temp_fr_c, window_time,
      JSON_OBJECT('upperBoundConfidencePercentage' VALUE 99.0, 'lowerBoundConfidencePercentage' VALUE 99.9))
      OVER (ORDER BY window_time RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
      AS anomaly_brake_temp_fr_result,
    AI_DETECT_ANOMALIES(battery_charge_pct, window_time,
      JSON_OBJECT('upperBoundConfidencePercentage' VALUE 99.0, 'lowerBoundConfidencePercentage' VALUE 99.9))
      OVER (ORDER BY window_time RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
      AS anomaly_battery_result,
    AI_DETECT_ANOMALIES(fuel_remaining_kg, window_time,
      JSON_OBJECT('upperBoundConfidencePercentage' VALUE 99.0, 'lowerBoundConfidencePercentage' VALUE 99.9))
      OVER (ORDER BY window_time RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
      AS anomaly_fuel_result
  FROM windowed
)
SELECT
  a.car_number,
  a.lap,
  a.tire_temp_fl_c,
  a.tire_temp_fr_c,
  a.tire_temp_rl_c,
  a.tire_temp_rr_c,
  a.tire_pressure_fl_psi,
  a.tire_pressure_fr_psi,
  a.tire_pressure_rl_psi,
  a.tire_pressure_rr_psi,
  a.engine_temp_c,
  a.brake_temp_fl_c,
  a.brake_temp_fr_c,
  a.battery_charge_pct,
  a.fuel_remaining_kg,
  -- Anomaly boolean flags (true only if above upper bound)
  a.anomaly_tire_temp_fl_result.is_anomaly AS anomaly_tire_temp_fl,
  a.anomaly_tire_temp_fr_result.is_anomaly AS anomaly_tire_temp_fr,
  a.anomaly_tire_temp_rl_result.is_anomaly AS anomaly_tire_temp_rl,
  a.anomaly_tire_temp_rr_result.is_anomaly AS anomaly_tire_temp_rr,
  a.anomaly_tire_pressure_fl_result.is_anomaly AS anomaly_tire_pressure_fl,
  a.anomaly_tire_pressure_fr_result.is_anomaly AS anomaly_tire_pressure_fr,
  a.anomaly_engine_temp_result.is_anomaly AS anomaly_engine_temp,
  a.anomaly_brake_temp_fl_result.is_anomaly AS anomaly_brake_temp_fl,
  a.anomaly_brake_temp_fr_result.is_anomaly AS anomaly_brake_temp_fr,
  a.anomaly_battery_result.is_anomaly AS anomaly_battery,
  a.anomaly_fuel_result.is_anomaly AS anomaly_fuel,
  -- Race context from temporal join
  r.position,
  r.gap_to_ahead_sec,
  r.gap_to_leader_sec,
  r.pit_stops,
  r.tire_compound,
  r.tire_age_laps
FROM anomaly a
JOIN `race-standings` FOR SYSTEM_TIME AS OF a.window_time AS r
  ON a.car_number = r.car_number;
