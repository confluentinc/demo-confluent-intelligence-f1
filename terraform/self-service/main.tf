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
  cluster_id          = module.cluster.cluster_id
  cluster_name        = module.cluster.cluster_name
  compute_pool_id     = module.flink.compute_pool_id
  service_account_id  = module.cluster.service_account_id
  flink_rest_endpoint = module.flink.flink_rest_endpoint
  flink_api_key       = module.flink.flink_api_key
  flink_api_secret    = module.flink.flink_api_secret
  owner_email         = var.owner_email
  region              = var.region
  enable_rtce         = var.enable_rtce

  # The value edges above reach only confluent_service_account.app, NOT the role
  # bindings that give it authority. On destroy those edges reverse, so without
  # this the role bindings and these statements become siblings and Terraform is
  # free to revoke the principal's permissions first — then the statement delete
  # fails 403 Forbidden. Same guard module.llm already carries below.
  depends_on = [module.cluster]
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

# Confluent's Catalog API deletes tag bindings asynchronously; deleting the tag
# immediately after can still 409 because the binding removal hasn't propagated.
resource "time_sleep" "wait_for_tag_binding_removal" {
  depends_on       = [confluent_tag.raw_data]
  destroy_duration = "30s"
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

  depends_on = [time_sleep.wait_for_tag_binding_removal]
}

# --- LLM connection + model (AWS Bedrock) — text generation only ---
# terraform/modules/llm also creates an embedding connection/model
# (`llm_embedding_model`, Titan) for terraform/aws, but no self-service lab
# ever references it (LAB 4's pit_strategy_agent uses only `llm_textgen_model`)
# — so self-service doesn't call that module and inlines just the half it
# needs, rather than making the shared module conditional and risking a
# resource-address change for terraform/aws's already-applied state.

resource "confluent_flink_connection" "bedrock_textgen_connection" {
  organization { id = data.confluent_organization.main.id }
  environment { id = module.environment.environment_id }
  compute_pool { id = module.flink.compute_pool_id }
  principal { id = module.cluster.service_account_id }
  rest_endpoint = module.flink.flink_rest_endpoint
  credentials {
    key    = module.flink.flink_api_key
    secret = module.flink.flink_api_secret
  }

  display_name      = "llm-textgen-connection"
  type              = "BEDROCK"
  endpoint          = "https://bedrock-runtime.${var.region}.amazonaws.com/model/us.anthropic.claude-sonnet-4-5-20250929-v1:0/invoke"
  aws_access_key    = var.aws_bedrock_access_key
  aws_secret_key    = var.aws_bedrock_secret_key
  aws_session_token = var.aws_session_token != "" ? var.aws_session_token : null

  depends_on = [module.cluster]
}

resource "confluent_flink_statement" "llm_textgen_model" {
  organization { id = data.confluent_organization.main.id }
  environment { id = module.environment.environment_id }
  compute_pool { id = module.flink.compute_pool_id }
  principal { id = module.cluster.service_account_id }
  rest_endpoint = module.flink.flink_rest_endpoint
  credentials {
    key    = module.flink.flink_api_key
    secret = module.flink.flink_api_secret
  }

  statement = "CREATE MODEL `${local.name_prefix}-ENV`.`${local.name_prefix}-CLUSTER`.`llm_textgen_model` INPUT (prompt STRING) OUTPUT (response STRING) WITH ('provider' = 'bedrock', 'task' = 'text_generation', 'bedrock.connection' = '${confluent_flink_connection.bedrock_textgen_connection.display_name}', 'bedrock.params.max_tokens' = '50000');"

  properties = {
    "sql.current-catalog"  = "${local.name_prefix}-ENV"
    "sql.current-database" = "${local.name_prefix}-CLUSTER"
  }

  depends_on = [confluent_flink_connection.bedrock_textgen_connection]
}

# Keep the already-applied Bedrock connection/model in place instead of
# destroying and recreating them under a new address. The embedding
# connection/model this used to also create (module.llm.*_embedding_*) have
# no replacement here and simply fall out of state as a destroy.
moved {
  from = module.llm.confluent_flink_connection.bedrock_textgen_connection
  to   = confluent_flink_connection.bedrock_textgen_connection
}

moved {
  from = module.llm.confluent_flink_statement.llm_textgen_model
  to   = confluent_flink_statement.llm_textgen_model
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
