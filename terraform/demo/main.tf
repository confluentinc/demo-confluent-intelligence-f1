# Read core infrastructure outputs
data "terraform_remote_state" "core" {
  backend = "local"
  config = {
    path = "../core/terraform.tfstate"
  }
}

locals {
  region      = data.terraform_remote_state.core.outputs.region
  demo_name   = data.terraform_remote_state.core.outputs.demo_name
  owner_email = data.terraform_remote_state.core.outputs.owner_email
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
  demo_name           = local.demo_name
  owner_email         = local.owner_email
}

module "mq" {
  source      = "../modules/mq"
  aws_region  = local.region
  demo_name   = local.demo_name
  owner_email = local.owner_email
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
  demo_name        = local.demo_name
  owner_email      = local.owner_email
}

module "postgres" {
  source      = "../modules/postgres"
  aws_region  = local.region
  demo_name   = local.demo_name
  owner_email = local.owner_email
}

module "tableflow" {
  source         = "../modules/tableflow"
  environment_id = data.terraform_remote_state.core.outputs.environment_id
  bucket_name    = "f1-demo-${local.demo_name}-tableflow"
  demo_name      = local.demo_name
  owner_email    = local.owner_email
}

# --- Generated connector configs ---

resource "local_file" "mq_connector_config" {
  filename = "${path.module}/../../generated/mq_connector_config.json"
  content = jsonencode({
    name = "f1-mq-source"
    config = {
      "connector.class"          = "IbmMQSource"
      "kafka.auth.mode"          = "SERVICE_ACCOUNT"
      "kafka.service.account.id" = data.terraform_remote_state.core.outputs.service_account_id
      "kafka.topic"              = "race-standings-raw"
      "mq.hostname"              = module.mq.mq_public_ip
      "mq.port"                  = "1414"
      "mq.queue.manager"         = "QM1"
      "mq.channel"               = "DEV.ADMIN.SVRCONN"
      "jms.destination.name"     = "dev/race-standings"
      "jms.destination.type"     = "topic"
      "jms.subscription.durable" = "true"
      "jms.subscription.name"    = "f1-mq-source-sub"
      "mq.username"              = "admin"
      "mq.password"              = "passw0rd"
      "kafka.api.key"            = data.terraform_remote_state.core.outputs.app_api_key
      "kafka.api.secret"         = data.terraform_remote_state.core.outputs.app_api_secret
      "output.data.format"       = "AVRO"
      "tasks.max"                = "1"
    }
  })
}

resource "local_file" "cdc_connector_config" {
  filename = "${path.module}/../../generated/cdc_connector_config.json"
  content = jsonencode({
    name = "f1-postgres-cdc"
    config = {
      "connector.class"          = "PostgresSource"
      "kafka.auth.mode"          = "SERVICE_ACCOUNT"
      "kafka.service.account.id" = data.terraform_remote_state.core.outputs.service_account_id
      "connection.host"          = module.postgres.postgres_public_ip
      "connection.port"          = "5432"
      "connection.user"          = "f1user"
      "connection.password"      = "f1passw0rd"
      "db.name"                  = "f1demo"
      "table.include.list"       = "public.drivers"
      "output.data.format"       = "JSON"
      "tasks.max"                = "1"
      "topic.prefix"             = ""
      "topic.creation.default.partitions"         = "1"
      "topic.creation.default.replication.factor" = "3"
    }
  })
}
