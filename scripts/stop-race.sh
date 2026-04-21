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
