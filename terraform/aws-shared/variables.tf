variable "region" {
  description = "AWS region for all shared workshop infrastructure"
  type        = string
  default     = "us-east-1"
}

variable "owner_email" {
  description = "Email tagged on all shared AWS resources"
  type        = string
}

variable "prefix" {
  description = "Naming prefix for the shared workshop resources (e.g. f1-workshop)"
  type        = string
  default     = "f1-workshop"
}

variable "attendee_count" {
  description = "Accepted for compatibility with workshop tooling. Shared Postgres capacity is fixed by postgres_max_replication_slots so a resumed run with a different attendee count does not modify EC2 user_data."
  type        = number
  default     = 50
}

variable "postgres_max_replication_slots" {
  description = "Shared Postgres replication-slot and WAL-sender capacity. The default supports the accelerator's 95-account maximum plus ten spare slots. Treat a changed value as an instance-replacement migration; see POSTGRES-PASSWORD-MIGRATION.md."
  type        = number
  default     = 105

  validation {
    condition     = var.postgres_max_replication_slots >= 1 && floor(var.postgres_max_replication_slots) == var.postgres_max_replication_slots
    error_message = "postgres_max_replication_slots must be a positive whole number."
  }
}

variable "postgres_instance_type" {
  description = "EC2 instance type for the shared Postgres host"
  type        = string
  default     = "t3.large"
}

variable "ssh_ingress_cidr" {
  description = "CIDR blocks allowed to reach the shared Postgres host over SSH. Leave empty unless temporary operator access is required."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for cidr in var.ssh_ingress_cidr : can(cidrnetmask(cidr))])
    error_message = "Every ssh_ingress_cidr entry must be a valid IPv4 CIDR block."
  }
}

# --- Injected by wsa on every shared-infra apply (see workshop-setup-accelerator
# injectSharedInfraVars). Declared so Terraform accepts the -var flags; this
# workshop does not implement CloudWatch monitoring, so alert_email is unused. ---

variable "run_id" {
  description = "wsa run ID, passed on every shared apply for run tagging / orphan detection across runs"
  type        = string
  default     = ""
}

variable "alert_email" {
  description = "CloudWatch alarm SNS email (defaults to owner_email in wsa when monitoring is on). Accepted but unused — this workshop's shared infra does not create CloudWatch alarms."
  type        = string
  default     = ""
}
