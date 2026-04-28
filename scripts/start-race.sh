#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TF_DIR="$SCRIPT_DIR/../terraform/demo"

echo "Reading Terraform outputs..."
CLUSTER=$(cd "$TF_DIR" && terraform output -raw ecs_cluster_name)
TASK_DEF=$(cd "$TF_DIR" && terraform output -raw ecs_task_definition)
SUBNETS=$(cd "$TF_DIR" && terraform output -raw ecs_subnets)
SG=$(cd "$TF_DIR" && terraform output -raw ecs_security_group_id)

echo "Starting race simulator..."
echo "  Cluster: $CLUSTER"
echo "  Task: $TASK_DEF"

TASK_ARN=$(aws ecs run-task \
  --region us-east-2 \
  --cluster "$CLUSTER" \
  --task-definition "$TASK_DEF" \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=ENABLED}" \
  --query 'tasks[0].taskArn' \
  --output text)

echo "Race started! Task: $TASK_ARN"
echo "$TASK_ARN" > "$SCRIPT_DIR/.race-task-arn"
LOG_GROUP=$(cd "$TF_DIR" && terraform output -raw ecs_log_group)
echo "Logs: aws logs tail --region us-east-2 $LOG_GROUP --follow"
