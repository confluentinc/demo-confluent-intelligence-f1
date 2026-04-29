variable "environment_id" {
  description = "Confluent Cloud environment ID"
  type        = string
}

variable "name_prefix" {
  description = "Prefix for all named Confluent Cloud resources (e.g. RIVER-RACING-PROD)"
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

variable "owner_email" {
  description = "Owner email for AWS resource tagging"
  type        = string
}
