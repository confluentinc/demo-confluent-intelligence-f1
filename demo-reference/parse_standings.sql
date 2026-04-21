-- Job 0: Parse race-standings-raw (JMS envelope) into clean race-standings
-- Deployed after the MQ Source Connector creates race-standings-raw
-- Extracts JSON fields from the JMS text payload and sets car_number as key

INSERT INTO `race-standings`
SELECT
  CAST(JSON_VALUE(`text`, '$.car_number') AS INT) AS `car_number`,
  JSON_VALUE(`text`, '$.driver') AS `driver`,
  JSON_VALUE(`text`, '$.team') AS `team`,
  CAST(JSON_VALUE(`text`, '$.lap') AS INT) AS `lap`,
  CAST(JSON_VALUE(`text`, '$.position') AS INT) AS `position`,
  CAST(JSON_VALUE(`text`, '$.gap_to_leader_sec') AS DOUBLE) AS `gap_to_leader_sec`,
  CAST(JSON_VALUE(`text`, '$.gap_to_ahead_sec') AS DOUBLE) AS `gap_to_ahead_sec`,
  CAST(JSON_VALUE(`text`, '$.last_lap_time_sec') AS DOUBLE) AS `last_lap_time_sec`,
  CAST(JSON_VALUE(`text`, '$.pit_stops') AS INT) AS `pit_stops`,
  JSON_VALUE(`text`, '$.tire_compound') AS `tire_compound`,
  CAST(JSON_VALUE(`text`, '$.tire_age_laps') AS INT) AS `tire_age_laps`,
  CAST(JSON_VALUE(`text`, '$.in_pit_lane' RETURNING BOOLEAN) AS BOOLEAN) AS `in_pit_lane`,
  TO_TIMESTAMP_LTZ(CAST(JSON_VALUE(`text`, '$.event_time') AS BIGINT), 3) AS `event_time`
FROM `race-standings-raw`;
