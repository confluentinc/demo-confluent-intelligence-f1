# =============================================================================
# Per-attendee race simulator.
#
# Runs the shared simulator image (var.ecr_image_uri) as a single-task ECS
# Fargate SERVICE producing car_telemetry + race_standings into THIS attendee's
# cluster. It is provisioned stopped; the workshop lifecycle commands start it
# explicitly after preparation and reset it back to a clean stopped boundary.
# =============================================================================

resource "random_id" "suffix" {
  byte_length = 4
}

locals {
  ecs_prefix = "${lower(var.prefix)}-${random_id.suffix.hex}"
  # Lower-cased to match the instructor fan-out scripts' cluster filter.
  cluster_name = "river-racing-${local.ecs_prefix}-simulator"
}

resource "aws_iam_role" "ecs_execution" {
  name = "${local.ecs_prefix}-ecs-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_cloudwatch_log_group" "simulator" {
  name              = "/ecs/${local.ecs_prefix}-simulator"
  retention_in_days = 7
}

resource "aws_security_group" "ecs" {
  name_prefix = "${lower(var.prefix)}-ecs-"
  description = "Security group for F1 simulator ECS task"
  vpc_id      = var.shared_vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "${lower(var.prefix)}-ecs"
    owner_email = var.owner_email
  }
}

resource "aws_ecs_cluster" "simulator" {
  name = local.cluster_name
}

resource "aws_ecs_task_definition" "simulator" {
  family                   = "${local.ecs_prefix}-simulator"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution.arn

  container_definitions = jsonencode([{
    name      = "${local.ecs_prefix}-simulator"
    image     = var.shared_ecr_image_uri
    essential = true

    environment = [
      { name = "KAFKA_BOOTSTRAP", value = module.cluster.cluster_bootstrap },
      { name = "KAFKA_API_KEY", value = module.cluster.app_api_key },
      { name = "KAFKA_API_SECRET", value = module.cluster.app_api_secret },
      { name = "SR_URL", value = module.cluster.schema_registry_rest_endpoint },
      { name = "SR_API_KEY", value = module.cluster.sr_api_key },
      { name = "SR_API_SECRET", value = module.cluster.sr_api_secret },
      { name = "RACE_LOOP", value = "true" },
      { name = "RACE_SEED", value = "42" },
      { name = "PRE_RACE_WARMUP_LAPS", value = "0" },
      { name = "SECONDS_PER_LAP", value = tostring(var.seconds_per_lap) },
      { name = "RESTART_DELAY_SEC", value = "30" },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.simulator.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "simulator"
      }
    }
  }])
}

resource "aws_ecs_service" "simulator" {
  name            = "${local.ecs_prefix}-simulator"
  cluster         = aws_ecs_cluster.simulator.id
  task_definition = aws_ecs_task_definition.simulator.arn
  desired_count   = 0
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.shared_subnet_ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = true
  }

  # The simulator fetches registered schemas on startup, so the topics/tables
  # must exist first.
  depends_on = [module.topics]
}
