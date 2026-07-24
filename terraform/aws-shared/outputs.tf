# Networking — injected into each attendee's terraform/aws workspace.
output "vpc_id" {
  value = data.aws_vpc.default.id
}

output "subnet_ids" {
  value = data.aws_subnets.default.ids
}

# Shared Postgres — every attendee's CDC connector points here (each with its
# own replication slot / publication, namespaced by attendee prefix).
output "postgres_host" {
  value = module.postgres.postgres_public_ip
}

output "postgres_port" {
  value = 5432
}

output "postgres_dbname" {
  value = "f1demo"
}

output "postgres_user" {
  value = "f1user"
}

output "postgres_password" {
  value     = "f1passw0rd"
  sensitive = true
}

# Shared race-simulator image — referenced by per-attendee ECS task definitions.
output "ecr_image_uri" {
  value = "${aws_ecr_repository.simulator.repository_url}:${local.image_tag}"
}

output "region" {
  value = var.region
}
