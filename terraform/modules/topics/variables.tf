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

variable "demo_name" {
  description = "Unique demo instance name for resource prefixing"
  type        = string
}

variable "owner_email" {
  description = "Owner email for AWS resource tagging"
  type        = string
}
