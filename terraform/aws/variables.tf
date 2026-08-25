# =============================================================================
# Per-attendee variables. wsa injects these for each attendee workspace:
#   - identity:      prefix, owner_email          (wsa-spec-aws.yaml terraform_vars)
#   - Confluent:     confluent_cloud_api_key / _secret         (env_vars, TF_VAR_*)
#   - Bedrock (shared across attendees): aws_bedrock_* / aws_session_token (env_vars)
#   - shared infra: terraform/aws-shared's outputs are injected by wsa as
#     TF_VAR_shared_<output name> — every variable below prefixed shared_
#     must match an output name in terraform/aws-shared/outputs.tf exactly.
# =============================================================================

variable "prefix" {
  description = "Short per-attendee identifier (e.g. f1wp001). Namespaces the Confluent environment/cluster and the attendee's CDC slot + AWS resources."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9]{1,12}$", var.prefix))
    error_message = "prefix must be 1-12 alphanumeric characters (e.g. f1wp001). It is lowercased where AWS/Postgres require it."
  }
}

variable "owner_email" {
  description = "Attendee/owner email, tagged on AWS resources. Doubles as the attendee's Confluent Cloud login when grant_console_access is on."
  type        = string
}

variable "grant_console_access" {
  description = "Give the attendee EnvironmentAdmin on their own environment so they can log in to the Confluent Cloud Console and use the Flink SQL workspace. Requires owner_email to already exist as an accepted CC user — see docs/organizer/WORKSHOP-GUIDE.md's one-time org prep."
  type        = bool
  default     = false
}

variable "region" {
  description = "AWS region (must match terraform/aws-shared)"
  type        = string
  default     = "us-east-1"
}

variable "enable_rtce" {
  description = <<-EOT
    Enable the Real-Time Context Engine on car_telemetry + race_standings so
    attendees can query them from an MCP client. Set TF_VAR_enable_rtce=false for
    an org or region where RTCE isn't available — see modules/topics/variables.tf.
  EOT
  type        = bool
  default     = true
}

# --- Confluent Cloud ---

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

variable "flink_max_cfu" {
  description = "Autoscaling ceiling for this attendee's Flink compute pool; CFUs are consumed on demand, not reserved"
  type        = number
  default     = 10
}

# --- AWS Bedrock (shared across all attendees) ---

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
  description = "AWS Session Token (only for temporary ASIA* credentials; leave blank for long-lived keys)"
  type        = string
  sensitive   = true
  default     = ""
}

# --- Shared infrastructure (outputs of terraform/aws-shared) ---

variable "shared_vpc_id" {
  description = "VPC ID from terraform/aws-shared"
  type        = string
}

variable "shared_subnet_ids" {
  description = "Public subnet IDs from terraform/aws-shared (used by the attendee's ECS simulator task)"
  type        = list(string)
}

variable "shared_postgres_host" {
  description = "Shared Postgres host (from terraform/aws-shared)"
  type        = string
}

variable "shared_postgres_port" {
  description = "Shared Postgres port"
  type        = number
  default     = 5432
}

variable "shared_postgres_dbname" {
  description = "Shared Postgres database name"
  type        = string
  default     = "f1demo"
}

variable "shared_postgres_user" {
  description = "Shared Postgres user"
  type        = string
  default     = "f1user"
}

variable "shared_postgres_password" {
  description = "Shared Postgres password"
  type        = string
  sensitive   = true
}

variable "shared_ecr_image_uri" {
  description = "Race-simulator image URI from terraform/aws-shared"
  type        = string
}

variable "table_include_list" {
  description = "Postgres tables the CDC connector captures"
  type        = string
  default     = "public.driver_race_history"
}

# --- Race simulator behaviour ---

variable "seconds_per_lap" {
  description = "Simulated seconds per lap. 20 → 20-minute races (three laps per minute)."
  type        = number
  default     = 20
}

variable "race_loop" {
  description = "When true the simulator replays races back-to-back so the feed is always live."
  type        = bool
  default     = true
}
