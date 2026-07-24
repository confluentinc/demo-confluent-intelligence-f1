# =============================================================================
# Self-service (solo) Confluent Cloud environment — the whole workshop for one
# person, with NO AWS infrastructure.
#
# Provisions exactly what the labs need on Confluent: environment, cluster,
# Flink pool, the two live topics (car_telemetry + race_standings), the Bedrock
# LLM connections + models, and a `driver_race_history` table. Unlike
# terraform/aws there is no Postgres/CDC (the CLI seeds driver_race_history with
# a bounded Flink INSERT) and no ECS simulator (the user runs `uv run f1-race`
# locally). The labs (LAB 1-4, 6) run against this environment unchanged.
# =============================================================================

data "confluent_organization" "main" {}

locals {
  name_prefix = "RIVER-RACING-${var.prefix}"
}

# --- Confluent Cloud: environment, cluster, Flink pool ---

module "environment" {
  source           = "../modules/environment"
  environment_name = "${local.name_prefix}-ENV"
  owner_email      = var.owner_email
}

module "cluster" {
  source         = "../modules/cluster"
  environment_id = module.environment.environment_id
  cluster_name   = "${local.name_prefix}-CLUSTER"
  name_prefix    = local.name_prefix
  cloud_provider = "AWS"
  cloud_region   = var.region
  owner_email    = var.owner_email
}

module "flink" {
  source             = "../modules/flink"
  organization_id    = data.confluent_organization.main.id
  environment_id     = module.environment.environment_id
  environment_name   = "${local.name_prefix}-ENV"
  cluster_name       = "${local.name_prefix}-CLUSTER"
  name_prefix        = local.name_prefix
  cloud_provider     = "AWS"
  cloud_region       = var.region
  service_account_id = module.cluster.service_account_id
  max_cfu            = var.flink_max_cfu
  owner_email        = var.owner_email
}

# --- Topics: car_telemetry + race_standings (produced by the local simulator) ---

module "topics" {
  source              = "../modules/topics"
  organization_id     = data.confluent_organization.main.id
  environment_id      = module.environment.environment_id
  environment_name    = module.environment.environment_name
  cluster_name        = module.cluster.cluster_name
  compute_pool_id     = module.flink.compute_pool_id
  service_account_id  = module.cluster.service_account_id
  flink_rest_endpoint = module.flink.flink_rest_endpoint
  flink_api_key       = module.flink.flink_api_key
  flink_api_secret    = module.flink.flink_api_secret
  owner_email         = var.owner_email
}

# --- Stream Catalog tag on the raw ingest topic (LAB 2 catalog story) ---

resource "confluent_tag" "raw_data" {
  schema_registry_cluster {
    id = module.cluster.schema_registry_id
  }
  rest_endpoint = module.cluster.schema_registry_rest_endpoint
  credentials {
    key    = module.cluster.sr_api_key
    secret = module.cluster.sr_api_secret
  }

  name        = "RAW_DATA"
  description = "Raw ingest topic — unprocessed sensor data"
  depends_on  = [module.topics]
}

resource "confluent_tag_binding" "car_telemetry_raw_data" {
  schema_registry_cluster {
    id = module.cluster.schema_registry_id
  }
  rest_endpoint = module.cluster.schema_registry_rest_endpoint
  credentials {
    key    = module.cluster.sr_api_key
    secret = module.cluster.sr_api_secret
  }

  tag_name    = confluent_tag.raw_data.name
  entity_name = "${module.cluster.schema_registry_id}:${module.cluster.cluster_id}:car_telemetry"
  entity_type = "kafka_topic"
}

# --- LLM connections + models (AWS Bedrock) ---

module "llm" {
  source                 = "../modules/llm"
  organization_id        = data.confluent_organization.main.id
  environment_id         = module.environment.environment_id
  compute_pool_id        = module.flink.compute_pool_id
  service_account_id     = module.cluster.service_account_id
  flink_rest_endpoint    = module.flink.flink_rest_endpoint
  flink_api_key          = module.flink.flink_api_key
  flink_api_secret       = module.flink.flink_api_secret
  environment_name       = "${local.name_prefix}-ENV"
  cluster_name           = "${local.name_prefix}-CLUSTER"
  region                 = var.region
  aws_bedrock_access_key = var.aws_bedrock_access_key
  aws_bedrock_secret_key = var.aws_bedrock_secret_key
  aws_session_token      = var.aws_session_token

  depends_on = [module.cluster]
}

# --- driver_race_history table ---
# In terraform/aws this topic is fed by a Postgres CDC connector. Self-service
# has no Postgres: the CLI seeds the 198 historical rows with a bounded Flink
# INSERT after apply. Here we just create the empty table so the schema exists.

resource "confluent_flink_statement" "create_driver_race_history_table" {
  organization { id = data.confluent_organization.main.id }
  environment { id = module.environment.environment_id }
  compute_pool { id = module.flink.compute_pool_id }
  principal { id = module.cluster.service_account_id }
  rest_endpoint = module.flink.flink_rest_endpoint
  credentials {
    key    = module.flink.flink_api_key
    secret = module.flink.flink_api_secret
  }

  statement = <<-EOT
    CREATE TABLE `driver_race_history` (
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
      'scan.startup.mode' = 'earliest-offset',
      'value.format' = 'avro-registry'
    );
  EOT

  properties = {
    "sql.current-catalog"  = module.environment.environment_name
    "sql.current-database" = module.cluster.cluster_name
  }

  depends_on = [module.topics]
}
