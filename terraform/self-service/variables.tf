# =============================================================================
# Self-service (solo) variables. Confluent-only — no AWS infrastructure.
#
# The self-service CLI (`uv run selfservice up`) injects these as TF_VAR_*.
# Bedrock keys are still required (they back the Flink AI connections for LAB 4),
# but they are credentials, not infrastructure — no EC2/ECS/VPC is created here.
# =============================================================================

variable "prefix" {
  description = "Short identifier that namespaces the Confluent environment/cluster (e.g. solo)."
  type        = string
  default     = "solo"

  validation {
    condition     = can(regex("^[A-Za-z0-9]{1,12}$", var.prefix))
    error_message = "prefix must be 1-12 alphanumeric characters (e.g. solo). It is lowercased where AWS/Postgres require it."
  }
}

variable "owner_email" {
  description = "Owner email, tagged on the Confluent environment."
  type        = string
}

variable "region" {
  description = "Cloud region for the Confluent cluster + Flink pool, and the Bedrock runtime endpoint."
  type        = string
  default     = "us-east-1"
}

variable "enable_rtce" {
  description = <<-EOT
    Enable the Real-Time Context Engine on car_telemetry + race_standings so they
    can be queried from an MCP client. Set TF_VAR_enable_rtce=false for an org or
    region where RTCE isn't available — see modules/topics/variables.tf.
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
  description = "Autoscaling ceiling for the Flink compute pool; CFUs are consumed on demand, not reserved"
  type        = number
  default     = 10
}

# --- AWS Bedrock (credentials only — no AWS infrastructure) ---

variable "aws_bedrock_access_key" {
  description = "AWS Bedrock Access Key for the Flink AI connections"
  type        = string
  sensitive   = true
}

variable "aws_bedrock_secret_key" {
  description = "AWS Bedrock Secret Key for the Flink AI connections"
  type        = string
  sensitive   = true
}

variable "aws_session_token" {
  description = "AWS Session Token (only for temporary ASIA* credentials; leave blank for long-lived keys)"
  type        = string
  sensitive   = true
  default     = ""
}
