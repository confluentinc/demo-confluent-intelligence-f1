# Flat outputs consumed by the self-service CLI (credential card + seeding).

output "organization_id" {
  value = data.confluent_organization.main.id
}

output "environment_id" {
  value = module.environment.environment_id
}

output "environment_name" {
  value = module.environment.environment_name
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

# Consolidated credentials (the CLI flattens this into the credential card).
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
