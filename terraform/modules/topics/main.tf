terraform {
  required_providers {
    confluent = {
      source = "confluentinc/confluent"
    }
  }
}

# Create car_telemetry topic via Flink CREATE TABLE
# This auto-creates the backing Kafka topic + schema subjects.
# IF NOT EXISTS is required for reliability at scale: the Confluent provider
# intermittently "orphans" a statement under concurrent builds (the table is
# created server-side but the resource errors out, leaving it absent from TF
# state). Without IF NOT EXISTS every retry then fails with "table already
# exists" and the account can never recover; with it, the retry is a
# successful no-op so wsa's per-account retries self-heal the flaky create.
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
    CREATE TABLE IF NOT EXISTS `car_telemetry` (
      `race_id` STRING COMMENT 'Unique sortable race-loop identifier',
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
      'kafka.max-message-size' = '2097164 bytes',
      'kafka.message-timestamp-type' = 'create-time',
      'kafka.retention.size' = '0 bytes',
      'kafka.retention.time' = '24 h',
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

# Create race_standings topic via Flink CREATE TABLE
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
    CREATE TABLE IF NOT EXISTS `race_standings` (
      `race_id` STRING COMMENT 'Unique sortable race-loop identifier',
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
      PRIMARY KEY (`race_id`, `car_number`) NOT ENFORCED
    ) DISTRIBUTED BY (`race_id`, `car_number`) INTO 1 BUCKETS
    WITH (
      'changelog.mode' = 'upsert',
      'connector' = 'confluent',
      'kafka.cleanup-policy' = 'compact,delete',
      'kafka.compaction.time' = '6 h',
      'kafka.max-message-size' = '2097164 bytes',
      'kafka.message-timestamp-type' = 'create-time',
      'kafka.retention.size' = '0 bytes',
      'kafka.retention.time' = '24 h',
      'key.format' = 'avro-registry',
      'scan.bounded.mode' = 'unbounded',
      'scan.startup.mode' = 'earliest-offset',
      'value.format' = 'avro-registry'
    );
  EOT

  properties = {
    "sql.current-catalog"  = var.environment_name
    "sql.current-database" = var.cluster_name
  }

  depends_on = [confluent_flink_statement.create_car_telemetry_table]
}

# --- Real-Time Context Engine -------------------------------------------------
#
# RTCE materializes a topic into a lookup-optimized table and serves it to AI
# agents over MCP, so an attendee's coding agent can ask questions about the live
# race without a Kafka client or a consumer group. Enablement is PER TOPIC.
#
# Three constraints worth knowing before editing:
#
#  1. A registered schema is mandatory, which is why the resource depends on the
#     CREATE TABLE statement above — that is what registers the Avro subject.
#     Enabling RTCE on a topic with no schema fails.
#  2. It's regional and per-org (see var.enable_rtce). `confluent rtce region
#     list` is the authority on where it exists.
#  3. `description` is REQUIRED and is *model-readable* — the agent sees it when
#     choosing a topic. Treat it as prompt text, not a code comment.
#  4. `description` is capped at **256 characters** by the API, which rejects a
#     longer one with `400 Bad Request: description must be at most 256
#     characters`. Nothing catches that at plan time — it surfaces mid-apply,
#     after the environment, cluster, and topics already exist, and wsa burns
#     every retry on it. Both strings below are kept under 230 so an edit has
#     room to breathe; `tests/test_rtce_descriptions.py` fails the build long
#     before Terraform would.
#
# car_state is deliberately absent: attendees create it in LAB 3, so it doesn't
# exist at apply time. They toggle it on in the Console themselves (LAB 3), which
# is also the cheapest way to show what enablement actually does.

resource "confluent_rtce_topic" "car_telemetry" {
  count = var.enable_rtce ? 1 : 0

  # Uppercase to match every other Confluent provider resource in this repo
  # (modules/cluster, modules/flink both pass "AWS"). Note the CLI spells the
  # same argument lowercase — `confluent rtce rtce-topic create --cloud aws`.
  cloud       = "AWS"
  region      = var.region
  topic_name  = "car_telemetry"
  description = "Live sensor telemetry for River Racing car #88 at Silverstone: tire temps and pressures, engine and brake temps, battery, fuel, DRS, speed, throttle, brake. Many rows per lap. Use for car condition, tire wear, pit timing."

  environment {
    id = var.environment_id
  }

  kafka_cluster {
    id = var.cluster_id
  }

  depends_on = [confluent_flink_statement.create_car_telemetry_table]
}
