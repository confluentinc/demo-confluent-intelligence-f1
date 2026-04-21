variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.micro"
}

variable "key_pair_name" {
  description = "EC2 key pair name for SSH access (optional)"
  type        = string
  default     = ""
}

variable "demo_name" {
  description = "Unique demo instance name for resource prefixing"
  type        = string
}

variable "owner_email" {
  description = "Owner email for AWS resource tagging"
  type        = string
}
