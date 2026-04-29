terraform {
  required_providers {
    confluent = {
      source = "confluentinc/confluent"
    }
  }
}

resource "confluent_kafka_cluster" "main" {
  display_name = var.cluster_name
  cloud        = var.cloud_provider
  region       = var.cloud_region
  availability = "SINGLE_ZONE"

  standard {}

  environment {
    id = var.environment_id
  }
}

data "confluent_environment" "main" {
  id = var.environment_id
}

resource "confluent_service_account" "app" {
  display_name = "${var.name_prefix}-app"
  description  = "Service account for F1 demo application"
}

resource "confluent_role_binding" "app_manager" {
  principal   = "User:${confluent_service_account.app.id}"
  role_name   = "CloudClusterAdmin"
  crn_pattern = confluent_kafka_cluster.main.rbac_crn
}

resource "confluent_role_binding" "app_environment_admin" {
  principal   = "User:${confluent_service_account.app.id}"
  role_name   = "EnvironmentAdmin"
  crn_pattern = data.confluent_environment.main.resource_name
}

resource "confluent_api_key" "app" {
  display_name = "${var.name_prefix}-app-key"

  owner {
    id          = confluent_service_account.app.id
    api_version = confluent_service_account.app.api_version
    kind        = confluent_service_account.app.kind
  }

  managed_resource {
    id          = confluent_kafka_cluster.main.id
    api_version = confluent_kafka_cluster.main.api_version
    kind        = confluent_kafka_cluster.main.kind

    environment {
      id = var.environment_id
    }
  }

  depends_on = [
    confluent_role_binding.app_manager,
  ]
}

data "confluent_schema_registry_cluster" "main" {
  environment {
    id = var.environment_id
  }

  depends_on = [confluent_api_key.app]
}

resource "confluent_api_key" "schema_registry" {
  display_name = "${var.name_prefix}-sr-key"

  owner {
    id          = confluent_service_account.app.id
    api_version = confluent_service_account.app.api_version
    kind        = confluent_service_account.app.kind
  }

  managed_resource {
    id          = data.confluent_schema_registry_cluster.main.id
    api_version = data.confluent_schema_registry_cluster.main.api_version
    kind        = data.confluent_schema_registry_cluster.main.kind

    environment {
      id = var.environment_id
    }
  }

  depends_on = [
    confluent_role_binding.app_environment_admin,
  ]
}
