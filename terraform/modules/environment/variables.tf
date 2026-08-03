variable "environment_name" {
  description = "Name for the Confluent Cloud environment"
  type        = string
}

variable "owner_email" {
  description = "Owner email for AWS resource tagging"
  type        = string
}

variable "attendee_email" {
  description = "Confluent Cloud login of the attendee who gets Console access to this environment. Must already exist as a CC user (invitation created AND accepted) — the confluent_user lookup fails at plan time otherwise. Ignored unless grant_console_access is true."
  type        = string
  default     = ""
}

variable "grant_console_access" {
  description = "Bind attendee_email to this environment as EnvironmentAdmin so they can log in to the Confluent Cloud Console and use the Flink SQL workspace. Off by default: the standalone and self-service tracks pass the operator's own email, which may not resolve as a CC user (SSO alias mismatch). wsa-spec-aws.yaml turns it on for the workshop."
  type        = bool
  default     = false
}
