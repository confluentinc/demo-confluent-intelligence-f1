variable "organization_id" {
  description = "Confluent Cloud organization ID"
  type        = string
}

variable "environment_id" {
  description = "Confluent Cloud environment ID"
  type        = string
}

variable "environment_name" {
  description = "Confluent Cloud environment name (Flink catalog)"
  type        = string
}

variable "cluster_name" {
  description = "Kafka cluster name (Flink database)"
  type        = string
}

variable "compute_pool_id" {
  description = "Flink compute pool ID"
  type        = string
}

variable "service_account_id" {
  description = "Service account ID"
  type        = string
}

variable "flink_rest_endpoint" {
  description = "Flink REST endpoint URL"
  type        = string
}

variable "flink_api_key" {
  description = "Flink API key"
  type        = string
}

variable "flink_api_secret" {
  description = "Flink API secret"
  type        = string
  sensitive   = true
}

variable "owner_email" {
  description = "Owner email for AWS resource tagging"
  type        = string
}

variable "cluster_id" {
  description = "Kafka cluster ID (lkc-*) — the RTCE topics attach to this cluster"
  type        = string
}

variable "region" {
  description = <<-EOT
    Cloud region of the Kafka cluster. Only used for the RTCE topics, which are
    regional: it must be a region in `confluent rtce region list` (11 AWS
    regions as of 2026-08) or the create fails.
  EOT
  type        = string
  default     = "us-east-1"
}

variable "enable_rtce" {
  description = <<-EOT
    Enable the Real-Time Context Engine on car_telemetry, so an attendee's MCP
    client can query it (see modules/topics/main.tf). race_standings is
    deliberately excluded — it's a compacted, upsert-keyed topic and RTCE
    queries against it fail with MT_UPSERT_NOT_SUPPORTED.

    The escape hatch is deliberate: RTCE is per-org and region-limited, so an org
    without it, or a build in an unsupported region, fails on this resource and
    nothing else. `TF_VAR_enable_rtce=false` skips it and leaves every other
    topic behaviour untouched.
  EOT
  type        = bool
  default     = true
}
