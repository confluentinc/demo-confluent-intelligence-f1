output "compute_pool_id" {
  value = confluent_flink_compute_pool.main.id
}

output "flink_rest_endpoint" {
  value = data.confluent_flink_region.main.rest_endpoint
}

output "flink_api_key" {
  value     = confluent_api_key.flink.id
  sensitive = true
}

output "flink_api_secret" {
  value     = confluent_api_key.flink.secret
  sensitive = true
}
