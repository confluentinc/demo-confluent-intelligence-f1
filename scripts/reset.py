"""
Reset lab state for a fresh race re-run.

Stops the simulator, deletes all Flink statements, deletes output topics
(car-state, pit-decisions, race-standings-raw) + their SR subjects, then
recreates race-standings-raw so the MQ connector has a clean topic.
Connectors are left running — MQ subscription is already empty after the
race ends; Postgres CDC reference data is unchanged between races.

Usage: uv run reset
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from base64 import b64encode
from pathlib import Path

import boto3
from dotenv import dotenv_values

from scripts.common.terraform import get_project_root, run_terraform_output

RESET_TOPICS = ["car-state", "pit-decisions", "race-standings-raw"]

RACE_STANDINGS_RAW_SQL = """\
CREATE TABLE `race-standings-raw` (
  `key` VARBINARY(2147483647),
  `messageID` VARCHAR(2147483647) NOT NULL,
  `messageType` VARCHAR(2147483647) NOT NULL,
  `timestamp` BIGINT NOT NULL,
  `deliveryMode` INT NOT NULL,
  `correlationID` VARCHAR(2147483647),
  `replyTo` ROW<`destinationType` VARCHAR(2147483647) NOT NULL, `name` VARCHAR(2147483647) NOT NULL>,
  `destination` ROW<`destinationType` VARCHAR(2147483647) NOT NULL, `name` VARCHAR(2147483647) NOT NULL>,
  `redelivered` BOOLEAN NOT NULL,
  `type` VARCHAR(2147483647),
  `expiration` BIGINT NOT NULL,
  `priority` INT NOT NULL,
  `properties` MAP<VARCHAR(2147483647) NOT NULL, ROW<`propertyType` VARCHAR(2147483647) NOT NULL, `boolean` BOOLEAN, `byte` TINYINT, `short` SMALLINT, `integer` INT, `long` BIGINT, `float` FLOAT, `double` DOUBLE, `string` VARCHAR(2147483647), `bytes` VARBINARY(2147483647)> NOT NULL> NOT NULL,
  `bytes` VARBINARY(2147483647),
  `map` MAP<VARCHAR(2147483647) NOT NULL, ROW<`propertyType` VARCHAR(2147483647) NOT NULL, `boolean` BOOLEAN, `byte` TINYINT, `short` SMALLINT, `integer` INT, `long` BIGINT, `float` FLOAT, `double` DOUBLE, `string` VARCHAR(2147483647), `bytes` VARBINARY(2147483647)> NOT NULL>,
  `text` VARCHAR(2147483647)
)
DISTRIBUTED BY HASH(`key`) INTO 1 BUCKETS
WITH (
  'changelog.mode' = 'append',
  'connector' = 'confluent',
  'kafka.cleanup-policy' = 'delete',
  'kafka.max-message-size' = '8 mb',
  'kafka.retention.time' = '7 d',
  'key.format' = 'raw',
  'value.format' = 'avro-registry'
)"""


def run_cli(cmd: list[str]) -> tuple[int, str, str]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def stop_simulator(root: Path, demo: dict, region: str) -> None:
    arn_file = root / "scripts" / ".race-task-arn"
    if not arn_file.exists():
        print("  No active race task found (skipping)")
        return

    task_arn = arn_file.read_text().strip()
    cluster = demo["ecs_cluster_name"]
    try:
        ecs = boto3.client("ecs", region_name=region)
        ecs.stop_task(cluster=cluster, task=task_arn, reason="reset")
        arn_file.unlink()
        print(f"  Stopped task {task_arn}")
    except Exception as e:
        print(f"  Warning: could not stop ECS task: {e}")


def delete_flink_statements(env_id: str, pool_id: str) -> None:
    rc, stdout, _ = run_cli([
        "confluent", "flink", "statement", "list",
        "--environment", env_id,
        "--compute-pool", pool_id,
        "-o", "json",
    ])
    if rc != 0 or not stdout.strip():
        print("  No Flink statements found (or CLI error)")
        return

    try:
        statements = json.loads(stdout)
    except json.JSONDecodeError:
        print("  Warning: could not parse Flink statement list")
        return

    if not statements:
        print("  No Flink statements to delete")
        return

    for stmt in statements:
        name = stmt.get("name") or stmt.get("Name", "")
        if not name:
            continue
        rc, _, stderr = run_cli([
            "confluent", "flink", "statement", "delete", name,
            "--environment", env_id,
        ])
        status = "deleted" if rc == 0 else f"failed ({stderr.strip()[:80]})"
        print(f"  {name}: {status}")


def delete_topic_and_subjects(topic: str, env_id: str, cluster_id: str) -> None:
    rc, _, stderr = run_cli([
        "confluent", "kafka", "topic", "delete", topic,
        "--environment", env_id,
        "--cluster", cluster_id,
    ])
    print(f"  Topic {topic}: {'deleted' if rc == 0 else f'skipped ({stderr.strip()[:60]})'}")

    for subject in [f"{topic}-key", f"{topic}-value"]:
        base_cmd = [
            "confluent", "schema-registry", "schema", "delete",
            "--subject", subject,
            "--version", "all",
            "--environment", env_id,
        ]
        run_cli(base_cmd)
        run_cli([*base_cmd, "--permanent"])
        print(f"  SR {subject}: cleaned")


def recreate_race_standings_raw(core: dict) -> bool:
    env_id = core["environment_id"]
    rest = core["flink_rest_endpoint"].rstrip("/")
    api_key = core["flink_api_key"]
    api_secret = core["flink_api_secret"]

    stmt_name = f"reset-rsraw-{int(time.time())}"
    token = b64encode(f"{api_key}:{api_secret}".encode()).decode()
    headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    body = json.dumps({
        "name": stmt_name,
        "spec": {
            "statement": RACE_STANDINGS_RAW_SQL,
            "compute_pool": {"id": core["compute_pool_id"]},
            "principal": {"id": core["service_account_id"]},
            "properties": {
                "sql.current-catalog": core["environment_name"],
                "sql.current-database": core["cluster_name"],
            },
        },
    }).encode()

    post_url = f"{rest}/sql/v1/environments/{env_id}/statements"
    try:
        req = urllib.request.Request(post_url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req) as resp:
            if resp.status not in (200, 201):
                print(f"  Error: POST returned {resp.status}")
                return False
    except urllib.error.HTTPError as e:
        print(f"  Error: POST failed {e.code}: {e.read().decode()[:200]}")
        return False

    # Poll until COMPLETED or FAILED (max 60s)
    poll_url = f"{rest}/sql/v1/environments/{env_id}/statements/{stmt_name}"
    poll_req = urllib.request.Request(poll_url, headers={"Authorization": f"Basic {token}"})
    for _ in range(30):
        time.sleep(2)
        try:
            with urllib.request.urlopen(poll_req) as resp:
                data = json.loads(resp.read())
                phase = data.get("status", {}).get("phase", "")
                if phase == "COMPLETED":
                    print(f"  race-standings-raw recreated (statement: {stmt_name})")
                    return True
                if phase in ("FAILED", "STOPPED"):
                    detail = data.get("status", {}).get("detail", "")
                    print(f"  Error: statement {phase}: {detail}")
                    return False
        except Exception as e:
            print(f"  Warning: poll error: {e}")

    print("  Timed out waiting for race-standings-raw creation")
    return False


def main() -> None:
    print("=== F1 Demo Reset ===\n")

    root = get_project_root()

    # Load credentials.env so TF_VAR_* vars are in the environment for CLI auth
    creds_file = root / "credentials.env"
    if creds_file.exists():
        for k, v in dotenv_values(creds_file).items():
            if v:
                os.environ[k] = v

    core_state = root / "terraform" / "core" / "terraform.tfstate"
    demo_state = root / "terraform" / "demo" / "terraform.tfstate"
    try:
        core = run_terraform_output(core_state)
        demo = run_terraform_output(demo_state)
    except Exception as e:
        print(f"Error reading terraform state: {e}")
        print("Have you run 'uv run deploy' yet?")
        sys.exit(1)

    env_id = core["environment_id"]
    cluster_id = core["cluster_id"]
    pool_id = core["compute_pool_id"]
    region = core["region"]

    print("1. Stopping race simulator...")
    stop_simulator(root, demo, region)

    print("\n2. Deleting Flink statements...")
    delete_flink_statements(env_id, pool_id)

    print("\n3. Deleting topics and schema subjects...")
    for topic in RESET_TOPICS:
        delete_topic_and_subjects(topic, env_id, cluster_id)

    print("\n4. Recreating race-standings-raw...")
    ok = recreate_race_standings_raw(core)

    print("\n=== Reset complete ===")
    if ok:
        print("\nNext steps:")
        print("  1. Re-deploy Flink Jobs 0, 1, 2 in the SQL Workspace")
        print("  2. Start the race:  ./scripts/start-race.sh")
    else:
        print("\nWarning: race-standings-raw recreation may have failed.")
        print("Recreate manually in the Flink SQL Workspace if needed.")


if __name__ == "__main__":
    main()
