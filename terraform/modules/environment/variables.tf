variable "environment_name" {
  description = "Name for the Confluent Cloud environment"
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
