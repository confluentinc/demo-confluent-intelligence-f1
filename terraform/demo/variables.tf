variable "deployment_id" {
  description = "Short unique identifier for this deployment (e.g. initials). Used to namespace AWS resource names so multiple deployments can coexist in the same account."
  type        = string
}

variable "automated" {
  description = "When true, deploy the full Flink job graph (Jobs 1 & 2) via Terraform. When false (default), only Job 0 is deployed and Jobs 1 & 2 must be run manually in the Flink SQL Workspace per Walkthrough.md."
  type        = bool
  default     = false
}
