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

variable "compute_pool_id" {
  description = "Flink compute pool ID"
  type        = string
}

variable "service_account_id" {
  description = "Service account ID"
  type        = string
}

variable "flink_rest_endpoint" {
  description = "Flink REST endpoint URL"
  type        = string
}

variable "flink_api_key" {
  description = "Flink API key"
  type        = string
}

variable "flink_api_secret" {
  description = "Flink API secret"
  type        = string
  sensitive   = true
}

variable "owner_email" {
  description = "Owner email for AWS resource tagging"
  type        = string
}
