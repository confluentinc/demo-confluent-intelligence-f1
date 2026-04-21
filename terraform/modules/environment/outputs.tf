output "environment_id" {
  value = confluent_environment.main.id
}

output "environment_name" {
  value = confluent_environment.main.display_name
}

output "schema_registry_id" {
  value = data.confluent_schema_registry_cluster.main.id
}

output "schema_registry_rest_endpoint" {
  value = data.confluent_schema_registry_cluster.main.rest_endpoint
}

output "schema_registry_api_version" {
  value = data.confluent_schema_registry_cluster.main.api_version
}

output "schema_registry_kind" {
  value = data.confluent_schema_registry_cluster.main.kind
}
