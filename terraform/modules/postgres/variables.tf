variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type. Sized up from the demo default because one shared Postgres serves every attendee's CDC connector."
  type        = string
  default     = "t3.large"
}

variable "max_replication_slots" {
  description = "Postgres max_replication_slots / max_wal_senders. Must be >= the number of workshop attendees (one CDC replication slot per attendee), plus headroom."
  type        = number
  default     = 40
}

variable "key_pair_name" {
  description = "EC2 key pair name for SSH access (optional)"
  type        = string
  default     = ""
}

variable "owner_email" {
  description = "Owner email for AWS resource tagging"
  type        = string
}

variable "name_prefix" {
  description = "Prefix for AWS resource names (e.g. RIVER-RACING-PROD). Lowercased internally where AWS requires."
  type        = string
}
