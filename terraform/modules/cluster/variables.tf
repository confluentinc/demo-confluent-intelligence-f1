variable "environment_id" {
  description = "Confluent Cloud environment ID"
  type        = string
}

variable "cluster_name" {
  description = "Name for the Kafka cluster"
  type        = string
}

variable "cloud_provider" {
  description = "Cloud provider"
  type        = string
  default     = "AWS"
}

variable "cloud_region" {
  description = "Cloud region"
  type        = string
}

variable "schema_registry_id" {
  description = "Schema Registry cluster ID"
  type        = string
}

variable "schema_registry_api_version" {
  description = "Schema Registry API version"
  type        = string
}

variable "schema_registry_kind" {
  description = "Schema Registry cluster kind"
  type        = string
}

variable "demo_name" {
  description = "Unique demo instance name for resource prefixing"
  type        = string
}

variable "owner_email" {
  description = "Owner email for AWS resource tagging"
  type        = string
}
