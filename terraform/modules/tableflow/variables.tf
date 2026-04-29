variable "environment_id" {
  description = "Confluent Cloud environment ID"
  type        = string
}

variable "name_prefix" {
  description = "Prefix for resource names (e.g. RIVER-RACING-PROD). Used verbatim for CC display name; lowercased for AWS resources (S3, IAM)."
  type        = string
}

variable "owner_email" {
  description = "Owner email for AWS resource tagging"
  type        = string
}
