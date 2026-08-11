-- Optional LAB 3 extension: forecast front-left tire temperature with IBM Granite TTM.
--
-- This is a temporary SELECT, not a CREATE TABLE. Run it after car_state is
-- producing rows, inspect the 20-step forecasts, then stop the statement in
-- the SQL workspace so it does not consume compute during LAB 4.
--
-- AI_FORECAST is a built-in function. Granite is selected by the `model`
-- property; no CREATE CONNECTION or CREATE MODEL statement is required.

WITH windowed AS (
  SELECT
    window_start,
    window_end,
    window_time,
    car_number,
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
        'horizon' VALUE 20,
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
  forecast_result.forecast[0].`timestamp` AS next_point_at,
  forecast_result.forecast[0].mean AS next_point_c,
  forecast_result.forecast[9].`timestamp` AS hundred_seconds_out_at,
  forecast_result.forecast[9].mean AS hundred_seconds_out_c,
  forecast_result.forecast[19].`timestamp` AS two_hundred_seconds_out_at,
  forecast_result.forecast[19].mean AS two_hundred_seconds_out_c,
  forecast_result.forecast AS full_forecast,
  forecast_result.metadata AS forecast_metadata
FROM forecasted
WHERE CARDINALITY(forecast_result.forecast) > 0;
