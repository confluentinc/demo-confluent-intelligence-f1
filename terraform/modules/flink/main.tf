terraform {
  required_providers {
    confluent = {
      source = "confluentinc/confluent"
    }
  }
}

data "confluent_flink_region" "main" {
  cloud  = var.cloud_provider
  region = var.cloud_region
}

resource "confluent_flink_compute_pool" "main" {
  display_name = "f1-demo-${var.demo_name}-pool"
  cloud        = var.cloud_provider
  region       = var.cloud_region
  max_cfu      = 10

  environment {
    id = var.environment_id
  }
}

resource "confluent_api_key" "flink" {
  display_name = "f1-demo-${var.demo_name}-flink-key"

  owner {
    id          = var.service_account_id
    api_version = "iam/v2"
    kind        = "ServiceAccount"
  }

  managed_resource {
    id          = data.confluent_flink_region.main.id
    api_version = data.confluent_flink_region.main.api_version
    kind        = data.confluent_flink_region.main.kind

    environment {
      id = var.environment_id
    }
  }
}
