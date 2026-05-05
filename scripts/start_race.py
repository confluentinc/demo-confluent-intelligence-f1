"""
Launch the F1 race simulator as an ECS Fargate task.

Usage:
  uv run start-race                   # 10 seconds per lap (default)
  uv run start-race --20              # 20 seconds per lap
  uv run start-race --seconds-per-lap 30  # any custom value
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def get_tf_output(tf_dir: Path, key: str) -> str:
    result = subprocess.run(
        ["terraform", "output", "-raw", key],
        cwd=tf_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def main():
    # Preprocess argv: --20 → --seconds-per-lap 20
    argv = []
    for arg in sys.argv[1:]:
        if arg.startswith("--") and arg[2:].isdigit():
            argv += ["--seconds-per-lap", arg[2:]]
        else:
            argv.append(arg)

    parser = argparse.ArgumentParser(description="Start the F1 race simulator on ECS Fargate.")
    parser.add_argument("--seconds-per-lap", type=int, default=10, metavar="N", help="Seconds per simulated lap (default: 10)")
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parents[1]
    tf_dir = project_root / "terraform" / "demo"

    print("Reading Terraform outputs...")
    cluster = get_tf_output(tf_dir, "ecs_cluster_name")
    task_def = get_tf_output(tf_dir, "ecs_task_definition")
    subnets = get_tf_output(tf_dir, "ecs_subnets")
    sg = get_tf_output(tf_dir, "ecs_security_group_id")
    log_group = get_tf_output(tf_dir, "ecs_log_group")

    print(f"Starting race simulator (seconds_per_lap={args.seconds_per_lap})...")
    print(f"  Cluster: {cluster}")
    print(f"  Task:    {task_def}")

    cmd = [
        "aws", "ecs", "run-task",
        "--region", "us-east-1",
        "--cluster", cluster,
        "--task-definition", task_def,
        "--launch-type", "FARGATE",
        "--network-configuration",
        f"awsvpcConfiguration={{subnets=[{subnets}],securityGroups=[{sg}],assignPublicIp=ENABLED}}",
        "--query", "tasks[0].taskArn",
        "--output", "text",
    ]

    if args.seconds_per_lap != 10:
        # Derive container name from task definition family (strip revision suffix)
        container_name = task_def.rsplit(":", 1)[0].split("/")[-1]
        overrides = {
            "containerOverrides": [{
                "name": container_name,
                "environment": [{"name": "SECONDS_PER_LAP", "value": str(args.seconds_per_lap)}],
            }]
        }
        cmd += ["--overrides", json.dumps(overrides)]

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    task_arn = result.stdout.strip()

    print(f"Race started! Task: {task_arn}")
    scripts_dir = Path(__file__).resolve().parent
    (scripts_dir / ".race-task-arn").write_text(task_arn)
    print(f"Logs: aws logs tail --region us-east-1 {log_group} --follow")
