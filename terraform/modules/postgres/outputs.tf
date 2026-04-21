output "postgres_public_ip" {
  value = aws_instance.postgres.public_ip
}

output "postgres_public_dns" {
  value = aws_instance.postgres.public_dns
}

output "postgres_instance_id" {
  value = aws_instance.postgres.id
}

output "postgres_connection_string" {
  value = "postgresql://f1user:f1passw0rd@${aws_instance.postgres.public_ip}:5432/f1demo"
}
