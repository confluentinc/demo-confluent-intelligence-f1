data "aws_caller_identity" "current" {}
data "aws_vpc" "default" { default = true }
data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "random_id" "suffix" {
  byte_length = 4
}

locals {
  prefix = "${lower(var.name_prefix)}-${random_id.suffix.hex}"
}

# --- ECR Repository ---

resource "aws_ecr_repository" "simulator" {
  name         = "${local.prefix}-simulator"
  force_delete = true
}

# --- Build and push Docker image ---

resource "null_resource" "docker_build_push" {
  triggers = {
    dockerfile_hash   = filemd5("${var.dockerfile_path}/Dockerfile")
    requirements_hash = filemd5("${var.dockerfile_path}/requirements.txt")
    simulator_hash    = filemd5("${var.dockerfile_path}/simulator.py")
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws ecr get-login-password --region ${var.aws_region} | \
        docker login --username AWS --password-stdin ${aws_ecr_repository.simulator.repository_url}
      docker build --platform linux/amd64 -t ${aws_ecr_repository.simulator.repository_url}:latest ${var.dockerfile_path}
      docker push ${aws_ecr_repository.simulator.repository_url}:latest
    EOT
  }

  depends_on = [aws_ecr_repository.simulator]
}

# --- IAM Roles ---

resource "aws_iam_role" "ecs_execution" {
  name = "${local.prefix}-ecs-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"

}

# --- CloudWatch Logs ---

resource "aws_cloudwatch_log_group" "simulator" {
  name              = "/ecs/${local.prefix}-simulator"
  retention_in_days = 7
}

# --- Security Group ---

resource "aws_security_group" "ecs" {
  name_prefix = "${lower(var.name_prefix)}-ecs-"
  description = "Security group for F1 simulator ECS task"
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "${lower(var.name_prefix)}-ecs"
    owner_email = var.owner_email
  }
}

# --- ECS Cluster ---

resource "aws_ecs_cluster" "simulator" {
  name = "${local.prefix}-simulator"
}

# --- ECS Task Definition ---

resource "aws_ecs_task_definition" "simulator" {
  family                   = "${local.prefix}-simulator"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution.arn

  container_definitions = jsonencode([{
    name      = "${local.prefix}-simulator"
    image     = "${aws_ecr_repository.simulator.repository_url}:latest"
    essential = true

    environment = [
      { name = "KAFKA_BOOTSTRAP", value = var.kafka_bootstrap },
      { name = "KAFKA_API_KEY", value = var.kafka_api_key },
      { name = "KAFKA_API_SECRET", value = var.kafka_api_secret },
      { name = "SR_URL", value = var.sr_url },
      { name = "SR_API_KEY", value = var.sr_api_key },
      { name = "SR_API_SECRET", value = var.sr_api_secret },
      { name = "MQ_HOST", value = var.mq_host },
      # Set for reproducible telemetry noise (demo recordings). Remove for
      # live-race-style variability.
      { name = "RACE_SEED", value = "42" },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.simulator.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "simulator"
      }
    }
  }])

  depends_on = [null_resource.docker_build_push]
}
