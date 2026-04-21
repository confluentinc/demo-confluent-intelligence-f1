output "environment_id" {
  value = confluent_environment.main.id
}

output "environment_name" {
  value = confluent_environment.main.display_name
}
