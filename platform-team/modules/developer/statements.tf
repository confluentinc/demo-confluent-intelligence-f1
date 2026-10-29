# The workshop's tables, seed and model, created as the platform team: each
# statement is signed with its Global key and runs as that key's owner
# (principal_id).
#
# The DDL is all IF NOT EXISTS, so re-running apply is a no-op for anything
# already there: after the provider's double-POST flake (409 'Statement with
# name "tf-..." already exists'), and after terminal statements are
# auto-deleted (30 days). The seed INSERT isn't; see its comment.
#
# The table DDL is copied from terraform/modules/topics and
# terraform/self-service, and the seed is generated from
# scripts/selfservice/seed.py, because this folder has to work on its own;
# tests/test_platform_team_sync.py in the workshop repo catches drift.

resource "confluent_flink_statement" "create_car_telemetry_table" {
  organization { id = data.confluent_organization.main.id }
  environment { id = confluent_environment.main.id }
  compute_pool { id = confluent_flink_compute_pool.main.id }
  principal { id = var.principal_id }
  rest_endpoint = data.confluent_flink_region.main.rest_endpoint
  credentials {
    key    = var.confluent_cloud_api_key
    secret = var.confluent_cloud_api_secret
  }

  statement = <<-EOT
    CREATE TABLE IF NOT EXISTS `car_telemetry` (
      `car_number` INT COMMENT 'Car number identifier',
      `lap` INT COMMENT 'Current lap number (1-60)',
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
      'kafka.consumer.isolation-level' = 'read-uncommitted',
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
    "sql.current-catalog"  = confluent_environment.main.display_name
    "sql.current-database" = confluent_kafka_cluster.main.display_name
  }

  depends_on = [time_sleep.cluster_settle]
}

resource "confluent_flink_statement" "create_race_standings_table" {
  organization { id = data.confluent_organization.main.id }
  environment { id = confluent_environment.main.id }
  compute_pool { id = confluent_flink_compute_pool.main.id }
  principal { id = var.principal_id }
  rest_endpoint = data.confluent_flink_region.main.rest_endpoint
  credentials {
    key    = var.confluent_cloud_api_key
    secret = var.confluent_cloud_api_secret
  }

  statement = <<-EOT
    CREATE TABLE IF NOT EXISTS `race_standings` (
      `car_number` INT COMMENT 'Car number identifier',
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
      'kafka.consumer.isolation-level' = 'read-uncommitted',
      'scan.startup.mode' = 'earliest-offset',
      'value.format' = 'avro-registry'
    );
  EOT

  properties = {
    "sql.current-catalog"  = confluent_environment.main.display_name
    "sql.current-database" = confluent_kafka_cluster.main.display_name
  }

  depends_on = [confluent_flink_statement.create_car_telemetry_table]
}

# Created empty; the seed INSERT depends on this statement.
resource "confluent_flink_statement" "create_driver_race_history_table" {
  organization { id = data.confluent_organization.main.id }
  environment { id = confluent_environment.main.id }
  compute_pool { id = confluent_flink_compute_pool.main.id }
  principal { id = var.principal_id }
  rest_endpoint = data.confluent_flink_region.main.rest_endpoint
  credentials {
    key    = var.confluent_cloud_api_key
    secret = var.confluent_cloud_api_secret
  }

  statement = <<-EOT
    CREATE TABLE IF NOT EXISTS `driver_race_history` (
      `race_id` STRING COMMENT 'Race identifier (e.g. bahrain_2026)',
      `gp_name` STRING COMMENT 'Grand Prix name',
      `race_date` DATE COMMENT 'Date the race was held',
      `car_number` INT COMMENT 'Car number identifier',
      `driver` STRING COMMENT 'Driver full name',
      `team` STRING COMMENT 'Constructor team name',
      `starting_grid` INT COMMENT 'Starting grid position',
      `finishing_pos` INT COMMENT 'Finishing position',
      `positions_gained` INT COMMENT 'Positions gained (start - finish)',
      `pit_stops` INT COMMENT 'Number of pit stops',
      `stint_1_tire` STRING COMMENT 'Tire compound for stint 1',
      `stint_2_tire` STRING COMMENT 'Tire compound for stint 2',
      `stint_3_tire` STRING COMMENT 'Tire compound for stint 3 (or n/a)'
    )
    DISTRIBUTED INTO 1 BUCKETS
    WITH (
      'changelog.mode' = 'append',
      'connector' = 'confluent',
      'kafka.consumer.isolation-level' = 'read-uncommitted',
      'scan.startup.mode' = 'earliest-offset',
      'value.format' = 'avro-registry'
    );
  EOT

  properties = {
    "sql.current-catalog"  = confluent_environment.main.display_name
    "sql.current-database" = confluent_kafka_cluster.main.display_name
  }

  depends_on = [confluent_flink_statement.create_race_standings_table]
}

# The 198 historical rows, as one bounded INSERT that names no catalog (the
# properties supply it). Terraform can't tell whether it worked:
#   - create returns at the first RUNNING, so a later FAILED never reaches state;
#   - after the double-POST flake, the re-apply submits a new auto-named
#     statement. That's right when the orphan FAILED, but leaves 396 rows if it
#     ran and only the response was lost;
#   - a 404 on read drops the statement from state, and terminal statements are
#     auto-deleted after 30 days, so a re-apply after that inserts again.
# The guard is the developer's deploy: it counts the rows, seeds an empty
# table, and stops on any count other than 198. No fixed statement_name: every
# orphan would then block the re-apply with a 409.
#
# Regenerate the file from the workshop repo's root:
#   uv run python -m scripts.selfservice.seed > platform-team/modules/developer/driver_race_history_seed.sql
resource "confluent_flink_statement" "seed_driver_race_history" {
  organization { id = data.confluent_organization.main.id }
  environment { id = confluent_environment.main.id }
  compute_pool { id = confluent_flink_compute_pool.main.id }
  principal { id = var.principal_id }
  rest_endpoint = data.confluent_flink_region.main.rest_endpoint
  credentials {
    key    = var.confluent_cloud_api_key
    secret = var.confluent_cloud_api_secret
  }

  statement = file("${path.module}/driver_race_history_seed.sql")

  properties = {
    "sql.current-catalog"  = confluent_environment.main.display_name
    "sql.current-database" = confluent_kafka_cluster.main.display_name
  }

  depends_on = [confluent_flink_statement.create_driver_race_history_table]
}

# CSP Hosted Flink Model Inference only: Confluent runs the model, so there is
# no connection and no AWS key. Same name and options as terraform/self-service's
# 'confluent' branch, so LAB 4's agent SQL doesn't change.
locals {
  llm_textgen_with = join(", ", [
    "'provider' = 'confluent'",
    "'task' = 'text_generation'",
    "'confluent.model' = '${var.csp_hosted_model}'",
    "'confluent.params.max_tokens' = '50000'",
  ])
}

resource "confluent_flink_statement" "llm_textgen_model" {
  organization { id = data.confluent_organization.main.id }
  environment { id = confluent_environment.main.id }
  compute_pool { id = confluent_flink_compute_pool.main.id }
  principal { id = var.principal_id }
  rest_endpoint = data.confluent_flink_region.main.rest_endpoint
  credentials {
    key    = var.confluent_cloud_api_key
    secret = var.confluent_cloud_api_secret
  }

  statement = "CREATE MODEL IF NOT EXISTS `${confluent_environment.main.display_name}`.`${confluent_kafka_cluster.main.display_name}`.`llm_textgen_model` INPUT (prompt STRING) OUTPUT (response STRING) WITH (${local.llm_textgen_with});"

  properties = {
    "sql.current-catalog"  = confluent_environment.main.display_name
    "sql.current-database" = confluent_kafka_cluster.main.display_name
  }

  depends_on = [time_sleep.cluster_settle]
}
