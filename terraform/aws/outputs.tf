# --- Flat outputs (consumed by scripts/reset.py, the instructor scripts, and
# the wsa-spec-aws.yaml `credentials:` fields with source: terraform) ---

output "organization_id" {
  value = data.confluent_organization.main.id
}

output "prefix" {
  value = var.prefix
}

output "environment_id" {
  value = module.environment.environment_id
}

output "environment_name" {
  value = module.environment.environment_name
}

output "environment_url" {
  value = "https://confluent.cloud/environments/${module.environment.environment_id}"
}

output "cluster_id" {
  value = module.cluster.cluster_id
}

output "cluster_name" {
  value = module.cluster.cluster_name
}

output "cluster_bootstrap" {
  value = module.cluster.cluster_bootstrap
}

output "kafka_api_key" {
  value     = module.cluster.app_api_key
  sensitive = true
}

output "kafka_api_secret" {
  value     = module.cluster.app_api_secret
  sensitive = true
}

output "schema_registry_url" {
  value = module.cluster.schema_registry_rest_endpoint
}

output "sr_api_key" {
  value     = module.cluster.sr_api_key
  sensitive = true
}

output "sr_api_secret" {
  value     = module.cluster.sr_api_secret
  sensitive = true
}

output "compute_pool_id" {
  value = module.flink.compute_pool_id
}

output "flink_rest_endpoint" {
  value = module.flink.flink_rest_endpoint
}

output "flink_api_key" {
  value     = module.flink.flink_api_key
  sensitive = true
}

output "flink_api_secret" {
  value     = module.flink.flink_api_secret
  sensitive = true
}

# --- ECS simulator (instructor fan-out / debugging) ---

output "ecs_cluster_name" {
  value = aws_ecs_cluster.simulator.name
}

output "ecs_service_name" {
  value = aws_ecs_service.simulator.name
}

output "ecs_log_group" {
  value = aws_cloudwatch_log_group.simulator.name
}

# --- Consolidated attendee credentials (convenience for `terraform output
# -json attendee_credentials` in the single-environment smoke-test flow —
# scripts/setup.sh, deploy.py. wsa itself reads the flat outputs above,
# matched by name to wsa-spec-aws.yaml's `credentials:` fields.) ---

output "attendee_credentials" {
  sensitive = true
  value = {
    environment_id      = module.environment.environment_id
    environment_name    = module.environment.environment_name
    environment_url     = "https://confluent.cloud/environments/${module.environment.environment_id}"
    cluster_id          = module.cluster.cluster_id
    cluster_bootstrap   = module.cluster.cluster_bootstrap
    compute_pool_id     = module.flink.compute_pool_id
    service_account_id  = module.cluster.service_account_id
    kafka_api_key       = module.cluster.app_api_key
    kafka_api_secret    = module.cluster.app_api_secret
    schema_registry_url = module.cluster.schema_registry_rest_endpoint
    sr_api_key          = module.cluster.sr_api_key
    sr_api_secret       = module.cluster.sr_api_secret
    flink_api_key       = module.flink.flink_api_key
    flink_api_secret    = module.flink.flink_api_secret
  }
}
