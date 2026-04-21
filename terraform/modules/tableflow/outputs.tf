output "s3_bucket_name" {
  value = aws_s3_bucket.tableflow.id
}

output "s3_bucket_arn" {
  value = aws_s3_bucket.tableflow.arn
}

output "iam_role_arn" {
  value = aws_iam_role.tableflow.arn
}

output "provider_integration_id" {
  value = confluent_provider_integration.main.id
}
