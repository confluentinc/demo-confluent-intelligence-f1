-- Create the retained RTCE serving table once in the f1wp050 recording environment.
-- Never drop and recreate this topic under the same name: RTCE can retain stale
-- data-provider state for that lifecycle.
CREATE TABLE IF NOT EXISTS `race_standings_rtce` (
  `key` STRING NOT NULL,
  `car_number` INT,
  `driver` STRING,
  `team` STRING,
  `lap` INT,
  `position` INT,
  `gap_to_leader_sec` DOUBLE,
  `gap_to_ahead_sec` DOUBLE,
  `last_lap_time_sec` DOUBLE,
  `pit_stops` INT,
  `tire_compound` STRING,
  `tire_age_laps` INT,
  `in_pit_lane` BOOLEAN,
  `event_time` TIMESTAMP(3),
  PRIMARY KEY (`key`) NOT ENFORCED
) DISTRIBUTED BY (`key`) INTO 1 BUCKETS
WITH (
  'changelog.mode' = 'upsert',
  'connector' = 'confluent',
  'kafka.cleanup-policy' = 'compact',
  'kafka.compaction.time' = '1 h',
  'key.format' = 'raw',
  'scan.startup.mode' = 'earliest-offset',
  'value.fields-include' = 'all',
  'value.format' = 'avro-registry'
);
