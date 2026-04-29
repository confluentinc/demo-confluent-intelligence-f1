terraform {
  required_providers {
    confluent = {
      source = "confluentinc/confluent"
    }
  }
}

data "aws_caller_identity" "current" {}

resource "random_id" "suffix" {
  byte_length = 4
}

locals {
  aws_prefix  = lower(var.name_prefix)
  bucket_name = "${local.aws_prefix}-tableflow-${random_id.suffix.hex}"
  role_name   = "${local.aws_prefix}-tableflow-role-${random_id.suffix.hex}"
  policy_name = "${local.aws_prefix}-tableflow-policy-${random_id.suffix.hex}"
}

# S3 Bucket
resource "aws_s3_bucket" "tableflow" {
  bucket        = local.bucket_name
  force_destroy = true

  tags = {
    Name        = "${local.aws_prefix}-tableflow"
    owner_email = var.owner_email
  }
}

resource "aws_s3_bucket_public_access_block" "tableflow" {
  bucket = aws_s3_bucket.tableflow.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Confluent Provider Integration
resource "confluent_provider_integration" "main" {
  display_name = "${var.name_prefix}-aws-integration"

  environment {
    id = var.environment_id
  }

  aws {
    customer_role_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${local.role_name}"
  }
}

# IAM Role with trust policy for Confluent
resource "aws_iam_role" "tableflow" {
  name = local.role_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          AWS = confluent_provider_integration.main.aws[0].iam_role_arn
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "sts:ExternalId" = confluent_provider_integration.main.aws[0].external_id
          }
        }
      }
    ]
  })
}

# IAM Policy with S3 permissions
resource "aws_iam_policy" "tableflow" {
  name = local.policy_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation"
        ]
        Resource = aws_s3_bucket.tableflow.arn
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListMultipartUploadParts",
          "s3:AbortMultipartUpload",
          "s3:GetObjectVersion",
          "s3:ListBucketMultipartUploads"
        ]
        Resource = "${aws_s3_bucket.tableflow.arn}/*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "tableflow" {
  role       = aws_iam_role.tableflow.name
  policy_arn = aws_iam_policy.tableflow.arn
}

# Bucket policy allowing the IAM role
resource "aws_s3_bucket_policy" "tableflow" {
  bucket = aws_s3_bucket.tableflow.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.tableflow.arn
        }
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListMultipartUploadParts",
          "s3:AbortMultipartUpload",
          "s3:GetObjectVersion",
          "s3:ListBucketMultipartUploads"
        ]
        Resource = [
          aws_s3_bucket.tableflow.arn,
          "${aws_s3_bucket.tableflow.arn}/*"
        ]
      }
    ]
  })
}
