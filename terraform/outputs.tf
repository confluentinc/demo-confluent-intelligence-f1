output "environment_id" {
  value = module.environment.environment_id
}

output "cluster_id" {
  value = module.cluster.cluster_id
}

output "cluster_bootstrap" {
  value = module.cluster.cluster_bootstrap
}

output "app_api_key" {
  value     = module.cluster.app_api_key
  sensitive = true
}

output "app_api_secret" {
  value     = module.cluster.app_api_secret
  sensitive = true
}

output "mq_public_ip" {
  value = module.mq.mq_public_ip
}

output "mq_connection_string" {
  value = module.mq.mq_connection_string
}

output "postgres_public_ip" {
  value = module.postgres.postgres_public_ip
}

output "postgres_connection_string" {
  value = module.postgres.postgres_connection_string
}

output "tableflow_s3_bucket" {
  value = module.tableflow.s3_bucket_name
}

output "tableflow_iam_role_arn" {
  value = module.tableflow.iam_role_arn
}

output "tableflow_provider_integration_id" {
  value = module.tableflow.provider_integration_id
}

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
