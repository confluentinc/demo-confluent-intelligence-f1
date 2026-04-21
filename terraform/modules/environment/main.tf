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
