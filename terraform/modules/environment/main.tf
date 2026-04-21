terraform {
  required_providers {
    confluent = {
      source = "confluentinc/confluent"
    }
  }
}

resource "confluent_environment" "main" {
  display_name = var.environment_name

  stream_governance {
    package = "ESSENTIALS"
  }
}

data "confluent_schema_registry_cluster" "main" {
  environment {
    id = confluent_environment.main.id
  }
}
