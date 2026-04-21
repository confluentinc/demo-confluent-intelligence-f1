# ECS Simulator Migration

**Date:** 2026-04-20
**Status:** Approved
**Scope:** Move the race simulator from the MQ EC2 instance to ECS Fargate with a Docker container

---

## Problem

The simulator code is never deployed to the MQ EC2 instance (`user_data.sh` has a placeholder). The EC2 instance serves two roles (MQ broker + simulator) but only MQ works. Copying code via SCP is fragile and manual.

## Solution

Package the simulator as a Docker image, push to ECR, run on ECS Fargate. The MQ EC2 instance only runs IBM MQ. The simulator starts on demand via `aws ecs run-task`.

---

## New Resources

### Dockerfile (`datagen/Dockerfile`)

- Base: `python:3.11-slim`
- Install IBM MQ C client libraries (required by `pymqi`)
- Install Python dependencies: `pymqi`, `confluent-kafka[avro]`
- Copy `datagen/` package
- Entrypoint: `python -m datagen.simulator`

### Terraform Module (`modules/ecs/`)

| Resource | Purpose |
|---|---|
| `aws_ecr_repository` | Stores the simulator Docker image |
| `aws_ecs_cluster` | `f1-demo-simulator` cluster |
| `aws_ecs_task_definition` | Fargate task with env vars (Kafka, SR, MQ credentials) |
| `aws_iam_role` (execution) | ECS task execution role (pull from ECR, write CloudWatch logs) |
| `aws_iam_role` (task) | ECS task role (no special permissions needed) |
| `aws_iam_policy_attachment` | Attach `AmazonECSTaskExecutionRolePolicy` |
| `aws_security_group` | Egress-only (needs to reach MQ on 1414 + Confluent Cloud on 443) |
| `aws_cloudwatch_log_group` | `/ecs/f1-simulator` for container logs |
| `null_resource` | Builds and pushes Docker image to ECR during `terraform apply` |

**Task definition env vars** (injected from Terraform):
- `KAFKA_BOOTSTRAP`, `KAFKA_API_KEY`, `KAFKA_API_SECRET`
- `SR_URL`, `SR_API_KEY`, `SR_API_SECRET`
- `MQ_HOST` (EC2 public IP from MQ module output)

MQ_PORT, MQ_QUEUE_MANAGER, MQ_CHANNEL, MQ_QUEUE, MQ_USER, MQ_PASSWORD use defaults from `config.py`.

**Networking:** Default VPC, public subnets, `assign_public_ip = true`.

### Scripts

- `scripts/start-race.sh` — Runs `aws ecs run-task` with cluster name and task definition from Terraform outputs. Looks up default VPC subnets and security group.
- `scripts/stop-race.sh` — Runs `aws ecs stop-task` to terminate early if needed.

---

## Changes to Existing Files

### MQ Module — Strip Simulator Code

**`modules/mq/user_data.sh`:** Remove the `.env` heredoc, simulator placeholder, and commented startup command. Keep only: yum install docker, start docker, run IBM MQ container, sleep 30.

**`modules/mq/variables.tf`:** Remove `kafka_bootstrap`, `kafka_api_key`, `kafka_api_secret`, `sr_url`, `sr_api_key`, `sr_api_secret`. Keep `aws_region`, `instance_type`, `key_pair_name`.

**`modules/mq/main.tf`:** Remove the 6 Kafka/SR vars from the `templatefile()` call. The user_data.sh template no longer uses them.

### Root `terraform/main.tf`

- Remove Kafka/SR vars from `module "mq"` block
- Add `module "ecs"` block passing all credentials + MQ host

### Root `terraform/outputs.tf`

- Add `ecs_cluster_name`, `ecs_task_definition_arn`

---

## What Does NOT Change

- Simulator Python code (`simulator.py`, `telemetry.py`, `race_script.py`, `drivers.py`, `config.py`)
- MQ Docker container setup on EC2
- Any Flink SQL, connector configs, or topic definitions
- Test files
