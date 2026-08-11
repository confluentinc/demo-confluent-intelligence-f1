variable "organization_id" {
  description = "Confluent Cloud organization ID"
  type        = string
}

variable "environment_id" {
  description = "Confluent Cloud environment ID"
  type        = string
}

variable "compute_pool_id" {
  description = "Flink compute pool ID"
  type        = string
}

variable "service_account_id" {
  description = "Service account principal ID that owns the connections/statements"
  type        = string
}

variable "flink_rest_endpoint" {
  description = "Flink REST endpoint URL"
  type        = string
}

variable "flink_api_key" {
  description = "Flink API key"
  type        = string
  sensitive   = true
}

variable "flink_api_secret" {
  description = "Flink API secret"
  type        = string
  sensitive   = true
}

variable "environment_name" {
  description = "Confluent environment name (Flink catalog) — e.g. RIVER-RACING-<prefix>-ENV"
  type        = string
}

variable "cluster_name" {
  description = "Kafka cluster name (Flink database) — e.g. RIVER-RACING-<prefix>-CLUSTER"
  type        = string
}

variable "region" {
  description = "AWS region hosting the Bedrock runtime endpoint"
  type        = string
}

variable "aws_bedrock_access_key" {
  description = "AWS Bedrock Access Key for the Flink AI connections"
  type        = string
  sensitive   = true
}

variable "aws_bedrock_secret_key" {
  description = "AWS Bedrock Secret Key for the Flink AI connections"
  type        = string
  sensitive   = true
}

variable "aws_session_token" {
  description = "AWS Session Token (only for temporary ASIA* credentials; leave blank for long-lived keys)"
  type        = string
  sensitive   = true
  default     = ""
}
