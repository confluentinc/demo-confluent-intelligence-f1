# Versions are pinned in the root's versions.tf.
terraform {
  required_providers {
    confluent = {
      source = "confluentinc/confluent"
    }
    time = {
      source = "hashicorp/time"
    }
  }
}
