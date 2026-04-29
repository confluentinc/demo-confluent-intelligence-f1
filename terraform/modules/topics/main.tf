terraform {
  required_providers {
    confluent = {
      source = "confluentinc/confluent"
    }
  }
}

# Create car-telemetry topic via Flink CREATE TABLE
# This auto-creates the backing Kafka topic + schema subjects
resource "confluent_flink_statement" "create_car_telemetry_table" {
  organization {
    id = var.organization_id
  }
  environment {
    id = var.environment_id
  }
  compute_pool {
    id = var.compute_pool_id
  }
  principal {
    id = var.service_account_id
  }

  rest_endpoint = var.flink_rest_endpoint

  credentials {
    key    = var.flink_api_key
    secret = var.flink_api_secret
  }

  statement = <<-EOT
    CREATE TABLE `car-telemetry` (
      `car_number` INT COMMENT 'Car number identifier',
      `lap` INT COMMENT 'Current lap number (1-57)',
      `tire_temp_fl_c` DOUBLE COMMENT 'Front-left tire temperature in Celsius',
      `tire_temp_fr_c` DOUBLE COMMENT 'Front-right tire temperature in Celsius',
      `tire_temp_rl_c` DOUBLE COMMENT 'Rear-left tire temperature in Celsius',
      `tire_temp_rr_c` DOUBLE COMMENT 'Rear-right tire temperature in Celsius',
      `tire_pressure_fl_psi` DOUBLE COMMENT 'Front-left tire pressure in PSI',
      `tire_pressure_fr_psi` DOUBLE COMMENT 'Front-right tire pressure in PSI',
      `tire_pressure_rl_psi` DOUBLE COMMENT 'Rear-left tire pressure in PSI',
      `tire_pressure_rr_psi` DOUBLE COMMENT 'Rear-right tire pressure in PSI',
      `engine_temp_c` DOUBLE COMMENT 'Engine temperature in Celsius',
      `brake_temp_fl_c` DOUBLE COMMENT 'Front-left brake temperature in Celsius',
      `brake_temp_fr_c` DOUBLE COMMENT 'Front-right brake temperature in Celsius',
      `battery_charge_pct` DOUBLE COMMENT 'Hybrid battery charge percentage (0-100)',
      `fuel_remaining_kg` DOUBLE COMMENT 'Remaining fuel in kilograms',
      `drs_active` BOOLEAN COMMENT 'Drag Reduction System active flag',
      `speed_kph` DOUBLE COMMENT 'Current speed in km/h',
      `throttle_pct` DOUBLE COMMENT 'Throttle pedal position percentage (0-100)',
      `brake_pct` DOUBLE COMMENT 'Brake pedal position percentage (0-100)',
      `event_time` TIMESTAMP(3) COMMENT 'Sensor reading timestamp',
      WATERMARK FOR `event_time` AS `event_time` - INTERVAL '5' SECOND
    )
    DISTRIBUTED INTO 1 BUCKETS
    WITH (
      'changelog.mode' = 'append',
      'connector' = 'confluent',
      'kafka.cleanup-policy' = 'delete',
      'kafka.compaction.time' = '0 ms',
      'kafka.max-message-size' = '2097164 bytes',
      'kafka.message-timestamp-type' = 'create-time',
      'kafka.retention.size' = '0 bytes',
      'kafka.retention.time' = '0 ms',
      'scan.bounded.mode' = 'unbounded',
      'scan.startup.mode' = 'earliest-offset',
      'value.format' = 'avro-registry'
    );
  EOT

  properties = {
    "sql.current-catalog"  = var.environment_name
    "sql.current-database" = var.cluster_name
  }
}

# Create race-standings topic via Flink CREATE TABLE
# This auto-creates the backing Kafka topic + schema subjects
# Includes event_time watermark and primary key for temporal joins
resource "confluent_flink_statement" "create_race_standings_table" {
  organization {
    id = var.organization_id
  }
  environment {
    id = var.environment_id
  }
  compute_pool {
    id = var.compute_pool_id
  }
  principal {
    id = var.service_account_id
  }

  rest_endpoint = var.flink_rest_endpoint

  credentials {
    key    = var.flink_api_key
    secret = var.flink_api_secret
  }

  statement = <<-EOT
    CREATE TABLE `race-standings` (
      `car_number` INT COMMENT 'Car number identifier',
      `driver` STRING COMMENT 'Driver full name',
      `team` STRING COMMENT 'Constructor team name',
      `lap` INT COMMENT 'Current lap number (1-57)',
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
    ) DISTRIBUTED BY (`car_number`) INTO 1 BUCKETS;
  EOT

  properties = {
    "sql.current-catalog"  = var.environment_name
    "sql.current-database" = var.cluster_name
  }

  depends_on = [confluent_flink_statement.create_car_telemetry_table]
}

# Pre-create race-standings-raw with the exact JMS Avro schema the MQ connector produces.
# Avoids schema conflicts when the connector auto-creates the topic on first message.
resource "confluent_flink_statement" "create_race_standings_raw_table" {
  organization {
    id = var.organization_id
  }
  environment {
    id = var.environment_id
  }
  compute_pool {
    id = var.compute_pool_id
  }
  principal {
    id = var.service_account_id
  }

  rest_endpoint = var.flink_rest_endpoint

  credentials {
    key    = var.flink_api_key
    secret = var.flink_api_secret
  }

  statement = <<-EOT
    CREATE TABLE `race-standings-raw` (
      `key` VARBINARY(2147483647),
      `messageID` VARCHAR(2147483647) NOT NULL,
      `messageType` VARCHAR(2147483647) NOT NULL,
      `timestamp` BIGINT NOT NULL,
      `deliveryMode` INT NOT NULL,
      `correlationID` VARCHAR(2147483647),
      `replyTo` ROW<`destinationType` VARCHAR(2147483647) NOT NULL, `name` VARCHAR(2147483647) NOT NULL>,
      `destination` ROW<`destinationType` VARCHAR(2147483647) NOT NULL, `name` VARCHAR(2147483647) NOT NULL>,
      `redelivered` BOOLEAN NOT NULL,
      `type` VARCHAR(2147483647),
      `expiration` BIGINT NOT NULL,
      `priority` INT NOT NULL,
      `properties` MAP<VARCHAR(2147483647) NOT NULL, ROW<`propertyType` VARCHAR(2147483647) NOT NULL, `boolean` BOOLEAN, `byte` TINYINT, `short` SMALLINT, `integer` INT, `long` BIGINT, `float` FLOAT, `double` DOUBLE, `string` VARCHAR(2147483647), `bytes` VARBINARY(2147483647)> NOT NULL> NOT NULL,
      `bytes` VARBINARY(2147483647),
      `map` MAP<VARCHAR(2147483647) NOT NULL, ROW<`propertyType` VARCHAR(2147483647) NOT NULL, `boolean` BOOLEAN, `byte` TINYINT, `short` SMALLINT, `integer` INT, `long` BIGINT, `float` FLOAT, `double` DOUBLE, `string` VARCHAR(2147483647), `bytes` VARBINARY(2147483647)> NOT NULL>,
      `text` VARCHAR(2147483647)
    )
    DISTRIBUTED BY HASH(`key`) INTO 1 BUCKETS
    WITH (
      'changelog.mode' = 'append',
      'connector' = 'confluent',
      'kafka.cleanup-policy' = 'delete',
      'kafka.compaction.time' = '0 ms',
      'kafka.max-message-size' = '8 mb',
      'kafka.message-timestamp-type' = 'create-time',
      'kafka.retention.size' = '0 bytes',
      'kafka.retention.time' = '7 d',
      'key.format' = 'raw',
      'scan.bounded.mode' = 'unbounded',
      'scan.startup.mode' = 'earliest-offset',
      'value.format' = 'avro-registry'
    );
  EOT

  properties = {
    "sql.current-catalog"  = var.environment_name
    "sql.current-database" = var.cluster_name
  }

  depends_on = [confluent_flink_statement.create_race_standings_table]
}
