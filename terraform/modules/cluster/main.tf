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

resource "confluent_service_account" "app" {
  display_name = "f1-demo-${var.demo_name}-app"
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
  crn_pattern = confluent_kafka_cluster.main.environment.0.resource_name
}

resource "confluent_api_key" "app" {
  display_name = "f1-demo-${var.demo_name}-app-key"

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

resource "confluent_api_key" "schema_registry" {
  display_name = "f1-demo-${var.demo_name}-sr-key"

  owner {
    id          = confluent_service_account.app.id
    api_version = confluent_service_account.app.api_version
    kind        = confluent_service_account.app.kind
  }

  managed_resource {
    id          = var.schema_registry_id
    api_version = var.schema_registry_api_version
    kind        = var.schema_registry_kind

    environment {
      id = var.environment_id
    }
  }

  depends_on = [
    confluent_role_binding.app_environment_admin,
  ]
}
