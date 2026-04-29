variable "confluent_cloud_api_key" {
  description = "Confluent Cloud API Key"
  type        = string
  sensitive   = true
}

variable "confluent_cloud_api_secret" {
  description = "Confluent Cloud API Secret"
  type        = string
  sensitive   = true
}

variable "owner_email" {
  description = "Email tagged on all resources"
  type        = string
}

variable "deployment_id" {
  description = "Short unique identifier for this deployment (e.g. initials). Namespaces CC environment and cluster names so multiple deployers can coexist in the same org."
  type        = string
}

variable "aws_bedrock_access_key" {
  description = "AWS Bedrock Access Key for Flink AI connections"
  type        = string
  sensitive   = true
}

variable "aws_bedrock_secret_key" {
  description = "AWS Bedrock Secret Key for Flink AI connections"
  type        = string
  sensitive   = true
}

variable "aws_session_token" {
  description = "AWS Session Token (required for temporary ASIA* credentials)"
  type        = string
  sensitive   = true
  default     = ""
}
