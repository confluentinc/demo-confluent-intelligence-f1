# Read core infrastructure outputs
data "terraform_remote_state" "core" {
  backend = "local"
  config = {
    path = "../core/terraform.tfstate"
  }
}

locals {
  region      = data.terraform_remote_state.core.outputs.region
  owner_email = data.terraform_remote_state.core.outputs.owner_email
  name_prefix = "RIVER-RACING-${var.deployment_id}"
}

provider "confluent" {
  cloud_api_key    = data.terraform_remote_state.core.outputs.confluent_cloud_api_key
  cloud_api_secret = data.terraform_remote_state.core.outputs.confluent_cloud_api_secret
}

provider "aws" {
  region = local.region
}

data "confluent_organization" "main" {}

# --- Modules ---

module "topics" {
  source              = "../modules/topics"
  organization_id     = data.confluent_organization.main.id
  environment_id      = data.terraform_remote_state.core.outputs.environment_id
  environment_name    = data.terraform_remote_state.core.outputs.environment_name
  cluster_name        = data.terraform_remote_state.core.outputs.cluster_name
  compute_pool_id     = data.terraform_remote_state.core.outputs.compute_pool_id
  service_account_id  = data.terraform_remote_state.core.outputs.service_account_id
  flink_rest_endpoint = data.terraform_remote_state.core.outputs.flink_rest_endpoint
  flink_api_key       = data.terraform_remote_state.core.outputs.flink_api_key
  flink_api_secret    = data.terraform_remote_state.core.outputs.flink_api_secret
  owner_email         = local.owner_email
}

# --- Stream Catalog tags ---

resource "confluent_tag" "raw_data" {
  schema_registry_cluster {
    id = data.terraform_remote_state.core.outputs.schema_registry_id
  }
  rest_endpoint = data.terraform_remote_state.core.outputs.schema_registry_rest_endpoint
  credentials {
    key    = data.terraform_remote_state.core.outputs.sr_api_key
    secret = data.terraform_remote_state.core.outputs.sr_api_secret
  }

  name        = "RAW_DATA"
  description = "Raw ingest topic — unprocessed sensor data"
  depends_on  = [module.topics]
}

resource "confluent_tag_binding" "car_telemetry_raw_data" {
  schema_registry_cluster {
    id = data.terraform_remote_state.core.outputs.schema_registry_id
  }
  rest_endpoint = data.terraform_remote_state.core.outputs.schema_registry_rest_endpoint
  credentials {
    key    = data.terraform_remote_state.core.outputs.sr_api_key
    secret = data.terraform_remote_state.core.outputs.sr_api_secret
  }

  tag_name    = confluent_tag.raw_data.name
  entity_name = "${data.terraform_remote_state.core.outputs.cluster_id}:car_telemetry"
  entity_type = "kafka_topic"
}

module "mq" {
  source      = "../modules/mq"
  aws_region  = local.region
  owner_email = local.owner_email
  name_prefix = local.name_prefix
}

module "ecs" {
  source           = "../modules/ecs"
  aws_region       = local.region
  kafka_bootstrap  = data.terraform_remote_state.core.outputs.cluster_bootstrap
  kafka_api_key    = data.terraform_remote_state.core.outputs.app_api_key
  kafka_api_secret = data.terraform_remote_state.core.outputs.app_api_secret
  sr_url           = data.terraform_remote_state.core.outputs.schema_registry_rest_endpoint
  sr_api_key       = data.terraform_remote_state.core.outputs.sr_api_key
  sr_api_secret    = data.terraform_remote_state.core.outputs.sr_api_secret
  mq_host          = module.mq.mq_public_ip
  dockerfile_path  = "${path.module}/../../datagen"
  owner_email      = local.owner_email
  name_prefix      = local.name_prefix
}

module "postgres" {
  source      = "../modules/postgres"
  aws_region  = local.region
  owner_email = local.owner_email
  name_prefix = local.name_prefix
}

module "tableflow" {
  source         = "../modules/tableflow"
  environment_id = data.terraform_remote_state.core.outputs.environment_id
  name_prefix    = local.name_prefix
  owner_email    = local.owner_email
}

# --- Readiness waiters ---
# EC2 user_data runs asynchronously after the instance is created. These resources
# poll the service ports until they accept connections before connectors are provisioned.

resource "null_resource" "wait_for_mq" {
  depends_on = [module.mq]

  provisioner "local-exec" {
    command = <<-EOT
      echo "Waiting for IBM MQ at ${module.mq.mq_public_ip}:1414..."
      for i in $(seq 1 120); do
        if nc -z -w5 ${module.mq.mq_public_ip} 1414 2>/dev/null; then
          echo "IBM MQ port open after $((i * 15))s"
          exit 0
        fi
        echo "  attempt $i/120 — retrying in 15s..."
        sleep 15
      done
      echo "Timeout: IBM MQ not ready after 30 minutes" && exit 1
    EOT
  }
}

resource "null_resource" "wait_for_postgres" {
  depends_on = [module.postgres]

  provisioner "local-exec" {
    command = <<-EOT
      echo "Waiting for Postgres at ${module.postgres.postgres_public_ip}:5432..."
      for i in $(seq 1 80); do
        if nc -z -w5 ${module.postgres.postgres_public_ip} 5432 2>/dev/null; then
          echo "Postgres port open after $((i * 15))s"
          exit 0
        fi
        echo "  attempt $i/80 — retrying in 15s..."
        sleep 15
      done
      echo "Timeout: Postgres not ready after 20 minutes" && exit 1
    EOT
  }
}

# --- Managed connectors ---

resource "confluent_connector" "mq_source" {
  environment {
    id = data.terraform_remote_state.core.outputs.environment_id
  }
  kafka_cluster {
    id = data.terraform_remote_state.core.outputs.cluster_id
  }
  config_sensitive = {
    "kafka.api.key"    = data.terraform_remote_state.core.outputs.app_api_key
    "kafka.api.secret" = data.terraform_remote_state.core.outputs.app_api_secret
    "mq.password"      = "passw0rd"
  }
  config_nonsensitive = {
    "connector.class"          = "IbmMQSource"
    "name"                     = "f1-mq-source"
    "kafka.auth.mode"          = "SERVICE_ACCOUNT"
    "kafka.service.account.id" = data.terraform_remote_state.core.outputs.service_account_id
    "kafka.topic"              = "race_standings_raw"
    "mq.hostname"              = module.mq.mq_public_ip
    "mq.port"                  = "1414"
    "mq.queue.manager"         = "QM1"
    "mq.channel"               = "DEV.ADMIN.SVRCONN"
    "jms.destination.name"     = "dev/race_standings"
    "jms.destination.type"     = "topic"
    "jms.subscription.durable" = "true"
    "jms.subscription.name"    = "f1-mq-source-sub"
    "mq.username"              = "admin"
    "output.data.format"       = "JSON_SR"
    "tasks.max"                = "1"
  }
  depends_on = [module.topics, null_resource.wait_for_mq]
}

resource "confluent_connector" "postgres_cdc" {
  environment {
    id = data.terraform_remote_state.core.outputs.environment_id
  }
  kafka_cluster {
    id = data.terraform_remote_state.core.outputs.cluster_id
  }
  config_sensitive = {
    "kafka.api.key"     = data.terraform_remote_state.core.outputs.app_api_key
    "kafka.api.secret"  = data.terraform_remote_state.core.outputs.app_api_secret
    "database.password" = "f1passw0rd"
  }
  config_nonsensitive = {
    "connector.class"                   = "PostgresCdcSourceV2"
    "name"                              = "f1-postgres-cdc"
    "kafka.auth.mode"                   = "SERVICE_ACCOUNT"
    "kafka.service.account.id"          = data.terraform_remote_state.core.outputs.service_account_id
    "database.hostname"                 = module.postgres.postgres_public_ip
    "database.port"                     = "5432"
    "database.user"                     = "f1user"
    "database.dbname"                   = "f1demo"
    "topic.prefix"                      = "f1demo"
    "table.include.list"                = "public.driver_race_history"
    "output.data.format"                = "JSON"
    "tasks.max"                         = "1"
    "transforms"                        = "Reroute,Unwrap"
    "transforms.Reroute.type"           = "io.confluent.connect.cloud.transforms.TopicRegexRouter"
    "transforms.Reroute.regex"          = "^.*\\.public\\.(.+)$"
    "transforms.Reroute.replacement"    = "$1"
    "transforms.Unwrap.type"            = "io.debezium.transforms.ExtractNewRecordState"
    "transforms.Unwrap.drop.tombstones" = "false"
  }
  depends_on = [null_resource.wait_for_postgres]
}

# --- Job 0: Parse race_standings_raw → race_standings ---
# Reads the SQL from demo-reference/parse_standings.sql so the file remains
# the single source of truth (also referenced from README and Walkthrough).
#
# Dependency chain that guarantees race_standings_raw exists before this
# statement deploys:
#   module.mq                 → EC2 boots, user_data publishes a retained
#                               warmup message to dev/race_standings
#   confluent_connector.mq_source → subscribes (gets the retained warmup)
#                                   and writes to race_standings_raw,
#                                   materialising the topic + schema
#   this statement            → deploys against the now-existing topic

resource "confluent_flink_statement" "job0_parse_standings" {
  organization {
    id = data.confluent_organization.main.id
  }
  environment {
    id = data.terraform_remote_state.core.outputs.environment_id
  }
  compute_pool {
    id = data.terraform_remote_state.core.outputs.compute_pool_id
  }
  principal {
    id = data.terraform_remote_state.core.outputs.service_account_id
  }
  rest_endpoint = data.terraform_remote_state.core.outputs.flink_rest_endpoint
  credentials {
    key    = data.terraform_remote_state.core.outputs.flink_api_key
    secret = data.terraform_remote_state.core.outputs.flink_api_secret
  }
  statement = file("${path.module}/../../demo-reference/parse_standings.sql")
  properties = {
    "sql.current-catalog"  = data.terraform_remote_state.core.outputs.environment_name
    "sql.current-database" = data.terraform_remote_state.core.outputs.cluster_name
  }
  depends_on = [confluent_connector.mq_source, module.topics]
}

resource "confluent_flink_statement" "job1_enrichment_anomaly" {
  count = var.automated ? 1 : 0
  organization {
    id = data.confluent_organization.main.id
  }
  environment {
    id = data.terraform_remote_state.core.outputs.environment_id
  }
  compute_pool {
    id = data.terraform_remote_state.core.outputs.compute_pool_id
  }
  principal {
    id = data.terraform_remote_state.core.outputs.service_account_id
  }
  rest_endpoint = data.terraform_remote_state.core.outputs.flink_rest_endpoint
  credentials {
    key    = data.terraform_remote_state.core.outputs.flink_api_key
    secret = data.terraform_remote_state.core.outputs.flink_api_secret
  }
  statement = file("${path.module}/../../demo-reference/enrichment_anomaly.sql")
  properties = {
    "sql.current-catalog"  = data.terraform_remote_state.core.outputs.environment_name
    "sql.current-database" = data.terraform_remote_state.core.outputs.cluster_name
  }
  depends_on = [module.topics]
}

resource "confluent_flink_statement" "job2_create_agent" {
  count = var.automated ? 1 : 0
  organization {
    id = data.confluent_organization.main.id
  }
  environment {
    id = data.terraform_remote_state.core.outputs.environment_id
  }
  compute_pool {
    id = data.terraform_remote_state.core.outputs.compute_pool_id
  }
  principal {
    id = data.terraform_remote_state.core.outputs.service_account_id
  }
  rest_endpoint = data.terraform_remote_state.core.outputs.flink_rest_endpoint
  credentials {
    key    = data.terraform_remote_state.core.outputs.flink_api_key
    secret = data.terraform_remote_state.core.outputs.flink_api_secret
  }
  statement = file("${path.module}/../../demo-reference/streaming_agent_create_agent.sql")
  properties = {
    "sql.current-catalog"  = data.terraform_remote_state.core.outputs.environment_name
    "sql.current-database" = data.terraform_remote_state.core.outputs.cluster_name
  }
  depends_on = [module.topics]
}

resource "confluent_flink_statement" "job2_pit_decisions" {
  count = var.automated ? 1 : 0
  organization {
    id = data.confluent_organization.main.id
  }
  environment {
    id = data.terraform_remote_state.core.outputs.environment_id
  }
  compute_pool {
    id = data.terraform_remote_state.core.outputs.compute_pool_id
  }
  principal {
    id = data.terraform_remote_state.core.outputs.service_account_id
  }
  rest_endpoint = data.terraform_remote_state.core.outputs.flink_rest_endpoint
  credentials {
    key    = data.terraform_remote_state.core.outputs.flink_api_key
    secret = data.terraform_remote_state.core.outputs.flink_api_secret
  }
  statement = file("${path.module}/../../demo-reference/streaming_agent_pit_decisions.sql")
  properties = {
    "sql.current-catalog"  = data.terraform_remote_state.core.outputs.environment_name
    "sql.current-database" = data.terraform_remote_state.core.outputs.cluster_name
  }
  depends_on = [
    confluent_flink_statement.job1_enrichment_anomaly[0],
    confluent_flink_statement.job2_create_agent[0],
  ]
}

# --- Generated connector configs (for manual CLI deployment fallback) ---

resource "local_file" "mq_connector_config" {
  filename = "${path.module}/../../generated/mq_connector_config.json"
  content = jsonencode({
    name = "f1-mq-source"
    config = {
      "connector.class"          = "IbmMQSource"
      "kafka.auth.mode"          = "SERVICE_ACCOUNT"
      "kafka.service.account.id" = data.terraform_remote_state.core.outputs.service_account_id
      "kafka.topic"              = "race_standings_raw"
      "mq.hostname"              = module.mq.mq_public_ip
      "mq.port"                  = "1414"
      "mq.queue.manager"         = "QM1"
      "mq.channel"               = "DEV.ADMIN.SVRCONN"
      "jms.destination.name"     = "dev/race_standings"
      "jms.destination.type"     = "topic"
      "jms.subscription.durable" = "true"
      "jms.subscription.name"    = "f1-mq-source-sub"
      "mq.username"              = "admin"
      "mq.password"              = "passw0rd"
      "kafka.api.key"            = data.terraform_remote_state.core.outputs.app_api_key
      "kafka.api.secret"         = data.terraform_remote_state.core.outputs.app_api_secret
      "output.data.format"       = "JSON_SR"
      "tasks.max"                = "1"
    }
  })
}

resource "local_file" "cdc_connector_config" {
  filename = "${path.module}/../../generated/cdc_connector_config.json"
  content = jsonencode({
    name = "f1-postgres-cdc"
    config = {
      "connector.class"          = "PostgresCdcSourceV2"
      "kafka.auth.mode"          = "SERVICE_ACCOUNT"
      "kafka.service.account.id" = data.terraform_remote_state.core.outputs.service_account_id
      "database.hostname"        = module.postgres.postgres_public_ip
      "database.port"            = "5432"
      "database.user"            = "f1user"
      "database.password"        = "f1passw0rd"
      "database.dbname"          = "f1demo"
      "topic.prefix"             = "f1demo"
      "table.include.list"       = "public.driver_race_history"
      "output.data.format"       = "JSON"
      "tasks.max"                = "1"
      # Strip "f1demo.public." prefix so the topic is just `driver_race_history`.
      "transforms"                     = "Reroute,Unwrap"
      "transforms.Reroute.type"        = "io.confluent.connect.cloud.transforms.TopicRegexRouter"
      "transforms.Reroute.regex"       = "^.*\\.public\\.(.+)$"
      "transforms.Reroute.replacement" = "$1"
      # Unwrap Debezium envelope so the value is just the row, not the change-event metadata.
      "transforms.Unwrap.type"            = "io.debezium.transforms.ExtractNewRecordState"
      "transforms.Unwrap.drop.tombstones" = "false"
    }
  })
}
