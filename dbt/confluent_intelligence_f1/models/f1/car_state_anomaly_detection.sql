-- Job 1: Enrichment + Anomaly Detection
-- Equivalent to demo-reference/enrichment_anomaly.sql, deployed here via dbt.
-- race_standings is a Terraform-managed streaming_source (PRIMARY KEY on car_number),
-- enabling the versioned temporal join below.
{{ config(
    materialized='streaming_table',
    alias='car_state',
    table_properties={"changelog.mode": "append"}
) }}

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
  FROM {{ source('river_racing', 'car_telemetry') }} t
  JOIN {{ source('river_racing', 'race_standings') }} FOR SYSTEM_TIME AS OF t.event_time AS r
    ON t.car_number = r.car_number
),
windowed AS (
  SELECT
    window_start, window_end, window_time, car_number,
    MAX(lap)                  AS lap,
    AVG(tire_temp_fl_c)       AS tire_temp_fl_c,
    AVG(tire_temp_fr_c)       AS tire_temp_fr_c,
    AVG(tire_temp_rl_c)       AS tire_temp_rl_c,
    AVG(tire_temp_rr_c)       AS tire_temp_rr_c,
    AVG(tire_pressure_fl_psi) AS tire_pressure_fl_psi,
    AVG(tire_pressure_fr_psi) AS tire_pressure_fr_psi,
    AVG(tire_pressure_rl_psi) AS tire_pressure_rl_psi,
    AVG(tire_pressure_rr_psi) AS tire_pressure_rr_psi,
    AVG(engine_temp_c)        AS engine_temp_c,
    AVG(brake_temp_fl_c)      AS brake_temp_fl_c,
    AVG(brake_temp_fr_c)      AS brake_temp_fr_c,
    AVG(battery_charge_pct)   AS battery_charge_pct,
    AVG(fuel_remaining_kg)    AS fuel_remaining_kg,
    MAX(`position`)           AS `position`,
    MAX(gap_to_ahead_sec)     AS gap_to_ahead_sec,
    MAX(gap_to_leader_sec)    AS gap_to_leader_sec,
    MAX(pit_stops)            AS pit_stops,
    MAX(tire_compound)        AS tire_compound,
    MAX(tire_age_laps)        AS tire_age_laps
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
FROM anomaly
WHERE lap > 0
