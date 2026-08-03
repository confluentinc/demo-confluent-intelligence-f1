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
  description = "Expected number of attendees. Drives Postgres replication-slot capacity (one CDC slot per attendee). `create-workshop` exports TF_VAR_attendee_count from its --attendees value (and deploy.py sets 1), so this default applies only to a hand-run apply — no need to bump it by hand for a larger workshop."
  type        = number
  default     = 50
}

variable "postgres_instance_type" {
  description = "EC2 instance type for the shared Postgres host"
  type        = string
  default     = "t3.large"
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
