output "cluster_name" {
  value = aws_ecs_cluster.simulator.name
}

output "task_definition_arn" {
  value = aws_ecs_task_definition.simulator.arn
}

output "security_group_id" {
  value = aws_security_group.ecs.id
}

output "subnets" {
  value = join(",", data.aws_subnets.default.ids)
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.simulator.name
}
