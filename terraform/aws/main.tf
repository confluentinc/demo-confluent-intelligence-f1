# =============================================================================
# Per-attendee Confluent Cloud environment + the AWS race simulator that feeds
# it. Run ONCE per attendee (via wsa), consuming terraform/aws-shared outputs.
#
# This pre-provisions everything the attendee needs but does NOT deploy the
# stream-processing jobs — anomaly detection (Job 1) and the streaming agent
# (Job 2) are the hands-on labs the attendee writes themselves.
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
  # The attendee's Confluent Cloud login is their wsa pool account — the same
  # plus-aliased address wsa builds this environment for.
  attendee_email       = var.owner_email
  grant_console_access = var.grant_console_access
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

# --- Topics: car_telemetry + race_standings (produced directly by the simulator) ---

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

  # The value edges above reach only confluent_service_account.app, NOT the role
  # bindings that give it authority. On destroy those edges reverse, so without
  # this the role bindings and these statements become siblings and Terraform is
  # free to revoke the principal's permissions first — then the statement delete
  # fails 403 Forbidden. Same guard module.llm already carries below.
  depends_on = [module.cluster]
}

# --- Stream Catalog tag on the raw ingest topic ---

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

# --- LLM connections + models (AWS Bedrock) — env-scoped, shared credentials ---

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

# Migrate existing state into the extracted module so re-apply is a no-op.
moved {
  from = confluent_flink_connection.bedrock_textgen_connection
  to   = module.llm.confluent_flink_connection.bedrock_textgen_connection
}

moved {
  from = confluent_flink_connection.bedrock_embedding_connection
  to   = module.llm.confluent_flink_connection.bedrock_embedding_connection
}

moved {
  from = confluent_flink_statement.llm_textgen_model
  to   = module.llm.confluent_flink_statement.llm_textgen_model
}

moved {
  from = confluent_flink_statement.llm_embedding_model
  to   = module.llm.confluent_flink_statement.llm_embedding_model
}

# --- Postgres CDC connector (driver_race_history) ---
# Reads the shared Postgres host. Each attendee uses a uniquely-named
# replication slot + publication so many connectors can tail the same database.

resource "confluent_connector" "postgres_cdc" {
  environment {
    id = module.environment.environment_id
  }
  kafka_cluster {
    id = module.cluster.cluster_id
  }
  config_sensitive = {
    "kafka.api.key"     = module.cluster.app_api_key
    "kafka.api.secret"  = module.cluster.app_api_secret
    "database.password" = var.shared_postgres_password
  }
  config_nonsensitive = {
    "connector.class"          = "PostgresCdcSourceV2"
    "name"                     = "f1-postgres-cdc"
    "kafka.auth.mode"          = "SERVICE_ACCOUNT"
    "kafka.service.account.id" = module.cluster.service_account_id
    "database.hostname"        = var.shared_postgres_host
    "database.port"            = tostring(var.shared_postgres_port)
    "database.user"            = var.shared_postgres_user
    "database.dbname"          = var.shared_postgres_dbname
    "topic.prefix"             = "f1demo"
    "table.include.list"       = var.table_include_list
    "output.data.format"       = "JSON"
    "tasks.max"                = "1"
    # Per-attendee slot + publication so many connectors share one Postgres.
    # Postgres slot/publication names only allow [a-z0-9_], so lowercase the prefix.
    "slot.name"                   = "f1_cdc_${lower(var.prefix)}"
    "publication.name"            = "f1_pub_${lower(var.prefix)}"
    "publication.autocreate.mode" = "filtered"
    # Strip "f1demo.public." prefix so the topic is just `driver_race_history`.
    "transforms"                        = "Reroute,Unwrap"
    "transforms.Reroute.type"           = "io.confluent.connect.cloud.transforms.TopicRegexRouter"
    "transforms.Reroute.regex"          = "^.*\\.public\\.(.+)$"
    "transforms.Reroute.replacement"    = "$1"
    "transforms.Unwrap.type"            = "io.debezium.transforms.ExtractNewRecordState"
    "transforms.Unwrap.drop.tombstones" = "false"
  }
}
