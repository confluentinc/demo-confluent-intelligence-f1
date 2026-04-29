# --- Environment ---

output "environment_id" {
  value = module.environment.environment_id
}

output "environment_name" {
  value = module.environment.environment_name
}

# --- Cluster ---

output "cluster_id" {
  value = module.cluster.cluster_id
}

output "cluster_name" {
  value = module.cluster.cluster_name
}

output "cluster_bootstrap" {
  value = module.cluster.cluster_bootstrap
}

output "cluster_rest_endpoint" {
  value = module.cluster.cluster_rest_endpoint
}

output "app_api_key" {
  value     = module.cluster.app_api_key
  sensitive = true
}

output "app_api_secret" {
  value     = module.cluster.app_api_secret
  sensitive = true
}

output "service_account_id" {
  value = module.cluster.service_account_id
}

output "sr_api_key" {
  value     = module.cluster.sr_api_key
  sensitive = true
}

output "sr_api_secret" {
  value     = module.cluster.sr_api_secret
  sensitive = true
}

output "schema_registry_rest_endpoint" {
  value = module.cluster.schema_registry_rest_endpoint
}

# --- Flink ---

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

# --- Organization ---

output "organization_id" {
  value = data.confluent_organization.main.id
}

# --- Pass-through (needed by demo module's confluent provider) ---

output "confluent_cloud_api_key" {
  value     = var.confluent_cloud_api_key
  sensitive = true
}

output "confluent_cloud_api_secret" {
  value     = var.confluent_cloud_api_secret
  sensitive = true
}

# --- LLM Connections ---

output "llm_textgen_connection_name" {
  value = confluent_flink_connection.bedrock_textgen_connection.display_name
}

output "llm_embedding_connection_name" {
  value = confluent_flink_connection.bedrock_embedding_connection.display_name
}

# --- Deployment metadata ---

output "region" {
  value = local.region
}

output "owner_email" {
  value = var.owner_email
}
