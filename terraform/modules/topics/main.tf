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

  statement = <<-EOT
    CREATE TABLE `car-telemetry` (
      `car_number` INT,
      `lap` INT,
      `tire_temp_fl_c` DOUBLE,
      `tire_temp_fr_c` DOUBLE,
      `tire_temp_rl_c` DOUBLE,
      `tire_temp_rr_c` DOUBLE,
      `tire_pressure_fl_psi` DOUBLE,
      `tire_pressure_fr_psi` DOUBLE,
      `tire_pressure_rl_psi` DOUBLE,
      `tire_pressure_rr_psi` DOUBLE,
      `engine_temp_c` DOUBLE,
      `brake_temp_fl_c` DOUBLE,
      `brake_temp_fr_c` DOUBLE,
      `battery_charge_pct` DOUBLE,
      `fuel_remaining_kg` DOUBLE,
      `drs_active` BOOLEAN,
      `speed_kph` DOUBLE,
      `throttle_pct` DOUBLE,
      `brake_pct` DOUBLE,
      `event_time` TIMESTAMP(3),
      WATERMARK FOR `event_time` AS `event_time` - INTERVAL '5' SECOND,
      PRIMARY KEY (`car_number`) NOT ENFORCED
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

  statement = <<-EOT
    CREATE TABLE `race-standings` (
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
      WATERMARK FOR `event_time` AS `event_time` - INTERVAL '10' SECOND,
      PRIMARY KEY (`car_number`) NOT ENFORCED
    );
  EOT

  properties = {
    "sql.current-catalog"  = var.environment_name
    "sql.current-database" = var.cluster_name
  }

  depends_on = [confluent_flink_statement.create_car_telemetry_table]
}
