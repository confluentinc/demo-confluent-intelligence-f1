variable "deployment_id" {
  description = "Short unique identifier for this deployment (e.g. initials). Used to namespace AWS resource names so multiple deployments can coexist in the same account."
  type        = string
}
