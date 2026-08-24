-- Reduce race_standings continuously to one current RTCE row per car.
-- The grouped expression preserves the derived upsert key; a direct projection
-- with CAST(car_number AS STRING) currently introduces UpsertMaterialize.
INSERT INTO `race_standings_rtce`
SELECT /*+ STATE_TTL('standings' = '1h') */
  CAST(`car_number` AS STRING) AS `key`,
  LAST_VALUE(`car_number`) AS `car_number`,
  LAST_VALUE(`driver`) AS `driver`,
  LAST_VALUE(`team`) AS `team`,
  LAST_VALUE(`lap`) AS `lap`,
  LAST_VALUE(`position`) AS `position`,
  LAST_VALUE(`gap_to_leader_sec`) AS `gap_to_leader_sec`,
  LAST_VALUE(`gap_to_ahead_sec`) AS `gap_to_ahead_sec`,
  LAST_VALUE(`last_lap_time_sec`) AS `last_lap_time_sec`,
  LAST_VALUE(`pit_stops`) AS `pit_stops`,
  LAST_VALUE(`tire_compound`) AS `tire_compound`,
  LAST_VALUE(`tire_age_laps`) AS `tire_age_laps`,
  LAST_VALUE(`in_pit_lane`) AS `in_pit_lane`,
  LAST_VALUE(`event_time`) AS `event_time`
FROM `race_standings` AS `standings`
GROUP BY CAST(`car_number` AS STRING);
