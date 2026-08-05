-- Disposable RTCE UPSERT verification fixture.
--
-- Run only against a dedicated test environment (the workshop uses f1wp050).
-- These tables deliberately do not overlap Terraform-managed topics.
--
-- Test A copies the current race_standings schema and changes only Kafka
-- cleanup to delete. `CREATE TABLE ... LIKE ... WITH` is not accepted by the
-- current Confluent Cloud Flink parser, so this is the exact schema captured by
-- SHOW CREATE TABLE on f1wp050.
CREATE TABLE IF NOT EXISTS `rtce_standings_delete_test`
(
  `car_number` INT NOT NULL COMMENT 'Car number identifier',
  `driver` STRING COMMENT 'Driver full name',
  `team` STRING COMMENT 'Constructor team name',
  `lap` INT COMMENT 'Current lap number (1-60)',
  `position` INT COMMENT 'Current race position (1-22)',
  `gap_to_leader_sec` DOUBLE COMMENT 'Time gap to race leader in seconds',
  `gap_to_ahead_sec` DOUBLE COMMENT 'Time gap to car directly ahead in seconds',
  `last_lap_time_sec` DOUBLE COMMENT 'Last completed lap time in seconds',
  `pit_stops` INT COMMENT 'Number of pit stops completed',
  `tire_compound` STRING COMMENT 'Current tire compound (SOFT, MEDIUM, HARD)',
  `tire_age_laps` INT COMMENT 'Number of laps on current set of tires',
  `in_pit_lane` BOOLEAN COMMENT 'Whether car is currently in the pit lane',
  `event_time` TIMESTAMP(3) COMMENT 'FIA timing feed timestamp',
  WATERMARK FOR `event_time` AS `event_time` - INTERVAL '10' SECOND,
  PRIMARY KEY (`car_number`) NOT ENFORCED
) DISTRIBUTED BY (`car_number`) INTO 1 BUCKETS
WITH (
  'changelog.mode' = 'upsert',
  'connector' = 'confluent',
  'kafka.cleanup-policy' = 'delete',
  'kafka.retention.time' = '1 h',
  'scan.startup.mode' = 'earliest-offset',
  'value.format' = 'avro-registry'
);

-- Test B is the documented native RTCE-upsert shape: a raw STRING Kafka key
-- and a compacted topic. Confluent Cloud requires the raw-key column to be
-- named exactly `key`; it is also retained in the value for RTCE filtering.
CREATE TABLE IF NOT EXISTS `rtce_standings_raw_compact_test` (
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
  'kafka.retention.time' = '1 h',
  'key.format' = 'raw',
  'value.fields-include' = 'all',
  'value.format' = 'avro-registry',
  'scan.startup.mode' = 'earliest-offset'
);
