terraform {
  required_version = ">= 1.3.0"

  required_providers {
    confluent = {
      source  = "confluentinc/confluent"
      version = "= 2.92.0"
    }
    http = {
      source  = "hashicorp/http"
      version = "= 3.6.3"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
    time = {
      source  = "hashicorp/time"
      version = "= 0.14.2"
    }
  }
}
