variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "kafka_bootstrap" {
  description = "Kafka bootstrap server"
  type        = string
}

variable "kafka_api_key" {
  description = "Kafka API key"
  type        = string
  sensitive   = true
}

variable "kafka_api_secret" {
  description = "Kafka API secret"
  type        = string
  sensitive   = true
}

variable "sr_url" {
  description = "Schema Registry REST endpoint URL"
  type        = string
}

variable "sr_api_key" {
  description = "Schema Registry API key"
  type        = string
  sensitive   = true
}

variable "sr_api_secret" {
  description = "Schema Registry API secret"
  type        = string
  sensitive   = true
}

variable "mq_host" {
  description = "IBM MQ host (EC2 public IP)"
  type        = string
}

variable "dockerfile_path" {
  description = "Path to the datagen directory containing the Dockerfile"
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
