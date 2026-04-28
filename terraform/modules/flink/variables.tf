variable "organization_id" {
  description = "Confluent Cloud organization ID"
  type        = string
}

variable "environment_id" {
  description = "Confluent Cloud environment ID"
  type        = string
}

variable "environment_name" {
  description = "Confluent Cloud environment name (Flink catalog)"
  type        = string
}

variable "cluster_name" {
  description = "Kafka cluster name (Flink database)"
  type        = string
}

variable "cloud_provider" {
  description = "Cloud provider"
  type        = string
}

variable "cloud_region" {
  description = "Cloud region"
  type        = string
}

variable "service_account_id" {
  description = "Service account ID for Flink"
  type        = string
}

variable "owner_email" {
  description = "Owner email for AWS resource tagging"
  type        = string
}
