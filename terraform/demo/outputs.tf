# --- MQ ---

output "mq_public_ip" {
  value = module.mq.mq_public_ip
}

output "mq_connection_string" {
  value = module.mq.mq_connection_string
}

# --- Postgres ---

output "postgres_public_ip" {
  value = module.postgres.postgres_public_ip
}

output "postgres_connection_string" {
  value = module.postgres.postgres_connection_string
}

# --- Tableflow ---

output "tableflow_s3_bucket" {
  value = module.tableflow.s3_bucket_name
}

output "tableflow_iam_role_arn" {
  value = module.tableflow.iam_role_arn
}

output "tableflow_provider_integration_id" {
  value = module.tableflow.provider_integration_id
}

# --- ECS ---

output "ecs_cluster_name" {
  value = module.ecs.cluster_name
}

output "ecs_task_definition" {
  value = module.ecs.task_definition_arn
}

output "ecs_security_group_id" {
  value = module.ecs.security_group_id
}

output "ecs_subnets" {
  value = module.ecs.subnets
}
