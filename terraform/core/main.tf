provider "confluent" {
  cloud_api_key    = var.confluent_cloud_api_key
  cloud_api_secret = var.confluent_cloud_api_secret
}

data "confluent_organization" "main" {}

# --- Auto-generated unique suffix ---

resource "random_string" "suffix" {
  length  = 8
  special = false
  upper   = false
}

locals {
  region      = "us-east-2"
  demo_name   = random_string.suffix.result
  name_prefix = "f1-demo-${local.demo_name}"
}

# --- Modules ---

module "environment" {
  source           = "../modules/environment"
  environment_name = "${local.name_prefix}-env"
  demo_name        = local.demo_name
  owner_email      = var.owner_email
}

module "cluster" {
  source         = "../modules/cluster"
  environment_id = module.environment.environment_id
  cluster_name   = "${local.name_prefix}-cluster"
  cloud_provider = "AWS"
  cloud_region   = local.region
  demo_name      = local.demo_name
  owner_email    = var.owner_email
}

module "flink" {
  source             = "../modules/flink"
  organization_id    = data.confluent_organization.main.id
  environment_id     = module.environment.environment_id
  environment_name   = "${local.name_prefix}-env"
  cluster_name       = "${local.name_prefix}-cluster"
  cloud_provider     = "AWS"
  cloud_region       = local.region
  service_account_id = module.cluster.service_account_id
  demo_name          = local.demo_name
  owner_email        = var.owner_email
}
