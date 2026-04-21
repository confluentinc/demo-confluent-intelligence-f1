# ECS Simulator Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the race simulator from the MQ EC2 instance to ECS Fargate so it runs as a Docker container started on demand.

**Architecture:** Create a Dockerfile with pymqi + confluent-kafka[avro], push to ECR, define an ECS Fargate task with all credentials as env vars. Strip simulator code from the MQ EC2 module. Provide start/stop scripts that wrap `aws ecs run-task`.

**Tech Stack:** Docker, AWS ECS Fargate, ECR, Terraform, Python

**Spec:** `docs/superpowers/specs/2026-04-20-ecs-simulator-migration.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `datagen/Dockerfile` | Docker image with pymqi + confluent-kafka[avro] + simulator code |
| Create | `terraform/modules/ecs/main.tf` | ECR, ECS cluster, task definition, IAM, security group, logs, image build |
| Create | `terraform/modules/ecs/variables.tf` | Input variables for credentials and MQ host |
| Create | `terraform/modules/ecs/outputs.tf` | Cluster name, task def ARN, security group ID |
| Modify | `terraform/main.tf` | Add ECS module, strip Kafka/SR vars from MQ module |
| Modify | `terraform/outputs.tf` | Add ECS outputs |
| Modify | `terraform/modules/mq/main.tf` | Remove Kafka/SR vars from templatefile call |
| Modify | `terraform/modules/mq/variables.tf` | Remove 6 Kafka/SR variables |
| Modify | `terraform/modules/mq/user_data.sh` | Strip simulator code, keep only MQ Docker |
| Create | `scripts/start-race.sh` | aws ecs run-task wrapper |
| Create | `scripts/stop-race.sh` | aws ecs stop-task wrapper |

---

### Task 1: Create Dockerfile

**Files:**
- Create: `datagen/Dockerfile`

The Dockerfile needs IBM MQ C client libraries for `pymqi`. IBM provides an Ubuntu apt repository for this.

- [ ] **Step 1: Create the Dockerfile**

Create `datagen/Dockerfile`:

```dockerfile
FROM python:3.11-slim

# Install IBM MQ C client libraries (required by pymqi)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl gnupg && \
    curl -fsSL https://public.dhe.ibm.com/ibmdl/export/pub/software/websphere/messaging/mqdev/redist/9.4.1.0-IBM-MQC-Redist-LinuxX64.tar.gz \
      -o /tmp/mq.tar.gz && \
    mkdir -p /opt/mqm && \
    tar -xzf /tmp/mq.tar.gz -C /opt/mqm && \
    rm /tmp/mq.tar.gz && \
    apt-get purge -y curl gnupg && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

ENV LD_LIBRARY_PATH=/opt/mqm/lib64
ENV MQ_HOME=/opt/mqm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/datagen/
# Create __init__.py at /app level so `python -m datagen.simulator` works
RUN touch /app/datagen/__init__.py

ENTRYPOINT ["python", "-m", "datagen.simulator"]
```

- [ ] **Step 2: Test Docker build locally (optional)**

Run: `cd /Users/ahmedsaefzamzam/0-workshops/CurrentLondon/KeynoteDemo/F1/datagen && docker build -t f1-simulator .`

Expected: Image builds successfully. It won't run locally without MQ/Kafka credentials, but the build should complete.

- [ ] **Step 3: Commit**

```bash
git add datagen/Dockerfile
git commit -m "feat(datagen): add Dockerfile for race simulator with pymqi and confluent-kafka"
```

---

### Task 2: Create ECS Terraform Module

**Files:**
- Create: `terraform/modules/ecs/main.tf`
- Create: `terraform/modules/ecs/variables.tf`
- Create: `terraform/modules/ecs/outputs.tf`

- [ ] **Step 1: Create variables.tf**

Create `terraform/modules/ecs/variables.tf`:

```terraform
variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "kafka_bootstrap" {
  description = "Kafka bootstrap server"
  type        = string
}

variable "kafka_api_key" {
  description = "Kafka API key"
  type        = string
  sensitive   = true
}

variable "kafka_api_secret" {
  description = "Kafka API secret"
  type        = string
  sensitive   = true
}

variable "sr_url" {
  description = "Schema Registry REST endpoint URL"
  type        = string
}

variable "sr_api_key" {
  description = "Schema Registry API key"
  type        = string
  sensitive   = true
}

variable "sr_api_secret" {
  description = "Schema Registry API secret"
  type        = string
  sensitive   = true
}

variable "mq_host" {
  description = "IBM MQ host (EC2 public IP)"
  type        = string
}

variable "dockerfile_path" {
  description = "Path to the datagen directory containing the Dockerfile"
  type        = string
}
```

- [ ] **Step 2: Create main.tf**

Create `terraform/modules/ecs/main.tf`:

```terraform
data "aws_caller_identity" "current" {}
data "aws_vpc" "default" { default = true }
data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# --- ECR Repository ---

resource "aws_ecr_repository" "simulator" {
  name         = "f1-demo-simulator"
  force_delete = true
}

# --- Build and push Docker image ---

resource "null_resource" "docker_build_push" {
  triggers = {
    dockerfile_hash = filemd5("${var.dockerfile_path}/Dockerfile")
    requirements_hash = filemd5("${var.dockerfile_path}/requirements.txt")
    simulator_hash = filemd5("${var.dockerfile_path}/simulator.py")
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws ecr get-login-password --region ${var.aws_region} | \
        docker login --username AWS --password-stdin ${aws_ecr_repository.simulator.repository_url}
      docker build -t ${aws_ecr_repository.simulator.repository_url}:latest ${var.dockerfile_path}
      docker push ${aws_ecr_repository.simulator.repository_url}:latest
    EOT
  }

  depends_on = [aws_ecr_repository.simulator]
}

# --- IAM Roles ---

resource "aws_iam_role" "ecs_execution" {
  name = "f1-demo-ecs-execution"

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
  name              = "/ecs/f1-simulator"
  retention_in_days = 7
}

# --- Security Group ---

resource "aws_security_group" "ecs" {
  name_prefix = "f1-demo-ecs-"
  description = "Security group for F1 simulator ECS task"
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "f1-demo-ecs"
  }
}

# --- ECS Cluster ---

resource "aws_ecs_cluster" "simulator" {
  name = "f1-demo-simulator"
}

# --- ECS Task Definition ---

resource "aws_ecs_task_definition" "simulator" {
  family                   = "f1-simulator"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution.arn

  container_definitions = jsonencode([{
    name      = "f1-simulator"
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
```

- [ ] **Step 3: Create outputs.tf**

Create `terraform/modules/ecs/outputs.tf`:

```terraform
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
```

- [ ] **Step 4: Commit**

```bash
git add terraform/modules/ecs/
git commit -m "feat(terraform): add ECS Fargate module for race simulator"
```

---

### Task 3: Strip Simulator Code from MQ Module

**Files:**
- Modify: `terraform/modules/mq/user_data.sh`
- Modify: `terraform/modules/mq/variables.tf`
- Modify: `terraform/modules/mq/main.tf`

- [ ] **Step 1: Replace user_data.sh**

Replace the entire contents of `terraform/modules/mq/user_data.sh` with:

```bash
#!/bin/bash
set -e

# System setup
yum update -y
yum install -y docker
systemctl start docker
systemctl enable docker

# Start IBM MQ
docker run -d \
  --name ibm-mq \
  -p 1414:1414 \
  -p 9443:9443 \
  -e LICENSE=accept \
  -e MQ_QMGR_NAME=QM1 \
  -e MQ_APP_PASSWORD=passw0rd \
  -e MQ_ADMIN_PASSWORD=passw0rd \
  icr.io/ibm-messaging/mq:latest

# Wait for MQ to be ready
sleep 30
```

- [ ] **Step 2: Replace variables.tf**

Replace the entire contents of `terraform/modules/mq/variables.tf` with:

```terraform
variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.medium"
}

variable "key_pair_name" {
  description = "EC2 key pair name for SSH access (optional)"
  type        = string
  default     = ""
}
```

- [ ] **Step 3: Update main.tf templatefile call**

In `terraform/modules/mq/main.tf`, replace the `user_data` block (lines 62-69) with:

```terraform
  user_data = file("${path.module}/user_data.sh")
```

Since `user_data.sh` no longer has any template variables, use `file()` instead of `templatefile()`.

- [ ] **Step 4: Commit**

```bash
git add terraform/modules/mq/
git commit -m "refactor(terraform): strip simulator code from MQ module — EC2 now only runs IBM MQ"
```

---

### Task 4: Wire ECS Module and Update Root Terraform

**Files:**
- Modify: `terraform/main.tf`
- Modify: `terraform/outputs.tf`

- [ ] **Step 1: Update module "mq" block**

In `terraform/main.tf`, replace the `module "mq"` block (lines 53-62) with:

```terraform
module "mq" {
  source     = "./modules/mq"
  aws_region = var.region
}
```

- [ ] **Step 2: Add module "ecs" block**

In `terraform/main.tf`, add after the `module "mq"` block:

```terraform
module "ecs" {
  source           = "./modules/ecs"
  aws_region       = var.region
  kafka_bootstrap  = module.cluster.cluster_bootstrap
  kafka_api_key    = module.cluster.app_api_key
  kafka_api_secret = module.cluster.app_api_secret
  sr_url           = module.environment.schema_registry_rest_endpoint
  sr_api_key       = module.cluster.sr_api_key
  sr_api_secret    = module.cluster.sr_api_secret
  mq_host          = module.mq.mq_public_ip
  dockerfile_path  = "${path.module}/../datagen"
}
```

- [ ] **Step 3: Add ECS outputs**

In `terraform/outputs.tf`, append after the existing outputs:

```terraform
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
```

- [ ] **Step 4: Commit**

```bash
git add terraform/main.tf terraform/outputs.tf
git commit -m "feat(terraform): wire ECS module and strip Kafka/SR vars from MQ module call"
```

---

### Task 5: Create Start/Stop Scripts

**Files:**
- Create: `scripts/start-race.sh`
- Create: `scripts/stop-race.sh`

- [ ] **Step 1: Create start-race.sh**

Create `scripts/start-race.sh`:

```bash
#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TF_DIR="$SCRIPT_DIR/../terraform"

echo "Reading Terraform outputs..."
CLUSTER=$(cd "$TF_DIR" && terraform output -raw ecs_cluster_name)
TASK_DEF=$(cd "$TF_DIR" && terraform output -raw ecs_task_definition)
SUBNETS=$(cd "$TF_DIR" && terraform output -raw ecs_subnets)
SG=$(cd "$TF_DIR" && terraform output -raw ecs_security_group_id)

echo "Starting race simulator..."
echo "  Cluster: $CLUSTER"
echo "  Task: $TASK_DEF"

TASK_ARN=$(aws ecs run-task \
  --cluster "$CLUSTER" \
  --task-definition "$TASK_DEF" \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=ENABLED}" \
  --query 'tasks[0].taskArn' \
  --output text)

echo "Race started! Task: $TASK_ARN"
echo "$TASK_ARN" > "$SCRIPT_DIR/.race-task-arn"
echo "Logs: aws logs tail /ecs/f1-simulator --follow"
```

- [ ] **Step 2: Create stop-race.sh**

Create `scripts/stop-race.sh`:

```bash
#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TF_DIR="$SCRIPT_DIR/../terraform"

CLUSTER=$(cd "$TF_DIR" && terraform output -raw ecs_cluster_name)

if [ -f "$SCRIPT_DIR/.race-task-arn" ]; then
  TASK_ARN=$(cat "$SCRIPT_DIR/.race-task-arn")
  echo "Stopping task: $TASK_ARN"
  aws ecs stop-task --cluster "$CLUSTER" --task "$TASK_ARN" --reason "Manual stop"
  rm "$SCRIPT_DIR/.race-task-arn"
  echo "Race stopped."
else
  echo "No running race found. Checking ECS..."
  aws ecs list-tasks --cluster "$CLUSTER" --desired-status RUNNING
fi
```

- [ ] **Step 3: Make scripts executable**

Run: `chmod +x /Users/ahmedsaefzamzam/0-workshops/CurrentLondon/KeynoteDemo/F1/scripts/start-race.sh /Users/ahmedsaefzamzam/0-workshops/CurrentLondon/KeynoteDemo/F1/scripts/stop-race.sh`

- [ ] **Step 4: Add .race-task-arn to .gitignore**

Append to `.gitignore`:

```
scripts/.race-task-arn
```

- [ ] **Step 5: Commit**

```bash
git add scripts/start-race.sh scripts/stop-race.sh .gitignore
git commit -m "feat(scripts): add start-race.sh and stop-race.sh for ECS simulator control"
```

---

### Task 6: Final Validation

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/ahmedsaefzamzam/0-workshops/CurrentLondon/KeynoteDemo/F1 && python3 -m pytest datagen/tests/ -v`

Expected: All 14 tests pass. No simulator code was changed.

- [ ] **Step 2: Verify MQ user_data.sh has no template variables**

Search for `${` in `terraform/modules/mq/user_data.sh`. It must not appear — the file is now plain bash, not a template.

- [ ] **Step 3: Verify MQ module has no Kafka/SR variables**

Search for `kafka_` or `sr_` in `terraform/modules/mq/variables.tf`. No matches expected.

- [ ] **Step 4: Commit plan and spec docs**

```bash
git add docs/superpowers/plans/2026-04-20-ecs-simulator-migration.md
git commit -m "docs: add ECS simulator migration implementation plan"
```
