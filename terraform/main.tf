provider "confluent" {
  cloud_api_key    = var.confluent_cloud_api_key
  cloud_api_secret = var.confluent_cloud_api_secret
}

provider "aws" {
  region = var.region
}

data "confluent_organization" "main" {}

# --- Modules ---

module "environment" {
  source           = "./modules/environment"
  environment_name = "prod-f1-${var.demo_name}-env"
  demo_name        = var.demo_name
  owner_email      = var.owner_email
}

module "cluster" {
  source                      = "./modules/cluster"
  environment_id              = module.environment.environment_id
  cluster_name                = "prod-f1-${var.demo_name}-cluster"
  cloud_provider              = "AWS"
  cloud_region                = var.region
  schema_registry_id          = module.environment.schema_registry_id
  schema_registry_api_version = module.environment.schema_registry_api_version
  schema_registry_kind        = module.environment.schema_registry_kind
  demo_name                   = var.demo_name
  owner_email                 = var.owner_email
}

module "flink" {
  source             = "./modules/flink"
  organization_id    = data.confluent_organization.main.id
  environment_id     = module.environment.environment_id
  environment_name   = "prod-f1-${var.demo_name}-env"
  cluster_name       = "prod-f1-${var.demo_name}-cluster"
  cloud_provider     = "AWS"
  cloud_region       = var.region
  service_account_id = module.cluster.service_account_id
  demo_name          = var.demo_name
  owner_email        = var.owner_email
}

module "topics" {
  source             = "./modules/topics"
  organization_id    = data.confluent_organization.main.id
  environment_id     = module.environment.environment_id
  environment_name   = "prod-f1-${var.demo_name}-env"
  cluster_name       = "prod-f1-${var.demo_name}-cluster"
  compute_pool_id    = module.flink.compute_pool_id
  service_account_id = module.cluster.service_account_id
  demo_name          = var.demo_name
  owner_email        = var.owner_email

  depends_on = [module.flink]
}

module "mq" {
  source      = "./modules/mq"
  aws_region  = var.region
  demo_name   = var.demo_name
  owner_email = var.owner_email
}

module "ecs" {
  source           = "./modules/ecs"
  aws_region       = var.region
  kafka_bootstrap  = module.cluster.cluster_bootstrap
  kafka_api_key    = module.cluster.app_api_key
  kafka_api_secret = module.cluster.app_api_secret
  sr_url           = module.environment.schema_registry_rest_endpoint
  sr_api_key       = module.cluster.sr_api_key
  sr_api_secret    = module.cluster.sr_api_secret
  mq_host          = module.mq.mq_public_ip
  dockerfile_path  = "${path.module}/../datagen"
  demo_name        = var.demo_name
  owner_email      = var.owner_email
}

module "postgres" {
  source      = "./modules/postgres"
  aws_region  = var.region
  demo_name   = var.demo_name
  owner_email = var.owner_email
}

module "tableflow" {
  source         = "./modules/tableflow"
  environment_id = module.environment.environment_id
  bucket_name    = "f1-demo-${var.demo_name}-tableflow"
  demo_name      = var.demo_name
  owner_email    = var.owner_email
}
