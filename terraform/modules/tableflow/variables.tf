variable "environment_id" {
  description = "Confluent Cloud environment ID"
  type        = string
}

variable "name_prefix" {
  description = "Prefix for all named Confluent Cloud resources (e.g. f1-demo-bren)"
  type        = string
}

variable "bucket_name" {
  description = "S3 bucket name prefix for Delta Lake storage"
  type        = string
  default     = "f1-demo-tableflow"
}

variable "owner_email" {
  description = "Owner email for AWS resource tagging"
  type        = string
}
