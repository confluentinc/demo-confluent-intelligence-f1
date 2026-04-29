output "cluster_id" {
  value = confluent_kafka_cluster.main.id
}

output "cluster_name" {
  value = confluent_kafka_cluster.main.display_name
}

output "cluster_bootstrap" {
  value = confluent_kafka_cluster.main.bootstrap_endpoint
}

output "cluster_rest_endpoint" {
  value = confluent_kafka_cluster.main.rest_endpoint
}

output "app_api_key" {
  value     = confluent_api_key.app.id
  sensitive = true
}

output "app_api_secret" {
  value     = confluent_api_key.app.secret
  sensitive = true
}

output "service_account_id" {
  value = confluent_service_account.app.id
}

output "service_account_api_version" {
  value = confluent_service_account.app.api_version
}

output "sr_api_key" {
  value     = confluent_api_key.schema_registry.id
  sensitive = true
}

output "sr_api_secret" {
  value     = confluent_api_key.schema_registry.secret
  sensitive = true
}

output "schema_registry_rest_endpoint" {
  value = data.confluent_schema_registry_cluster.main.rest_endpoint
}
