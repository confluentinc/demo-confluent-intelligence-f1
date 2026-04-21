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

variable "region" {
  description = "AWS and Confluent Cloud region"
  type        = string
  default     = "us-east-2"
}

variable "demo_name" {
  description = "Unique name for this demo instance (e.g., your first name). Used to prefix all resources."
  type        = string
}

variable "owner_email" {
  description = "Email of the person deploying this demo. Tagged on all AWS resources."
  type        = string
}
