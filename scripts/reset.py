"""
Reset lab state for a fresh race re-run.

Deletes ALL race topics (including car-telemetry and race-standings) so the
UI shows a clean slate. Recreates the three schema-bearing topics via Flink
REST API. Connectors are left running — MQ subscription is already empty
after the race ends; Postgres CDC reference data is unchanged between races.

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

from scripts.common.login_checks import check_aws_configured, check_confluent_login
from scripts.common.terraform import get_project_root, run_terraform_output

# Deleted every reset. car-telemetry/race-standings are recreated; the others
# are job outputs that Flink recreates when jobs are redeployed.
RESET_TOPICS = [
    "car-state",
    "pit-decisions",
    "car-telemetry",
    "race-standings",
    "race-standings-raw",
    "drivers",          # stale — dropped from architecture
    "car-telemetry-laps",  # stale experiment topic
]

CAR_TELEMETRY_SQL = """\
CREATE TABLE `car-telemetry` (
  `car_number` INT COMMENT 'Car number identifier',
  `lap` INT COMMENT 'Current lap number (1-57)',
  `tire_temp_fl_c` DOUBLE COMMENT 'Front-left tire temperature in Celsius',
  `tire_temp_fr_c` DOUBLE COMMENT 'Front-right tire temperature in Celsius',
  `tire_temp_rl_c` DOUBLE COMMENT 'Rear-left tire temperature in Celsius',
  `tire_temp_rr_c` DOUBLE COMMENT 'Rear-right tire temperature in Celsius',
  `tire_pressure_fl_psi` DOUBLE COMMENT 'Front-left tire pressure in PSI',
  `tire_pressure_fr_psi` DOUBLE COMMENT 'Front-right tire pressure in PSI',
  `tire_pressure_rl_psi` DOUBLE COMMENT 'Rear-left tire pressure in PSI',
  `tire_pressure_rr_psi` DOUBLE COMMENT 'Rear-right tire pressure in PSI',
  `engine_temp_c` DOUBLE COMMENT 'Engine temperature in Celsius',
  `brake_temp_fl_c` DOUBLE COMMENT 'Front-left brake temperature in Celsius',
  `brake_temp_fr_c` DOUBLE COMMENT 'Front-right brake temperature in Celsius',
  `battery_charge_pct` DOUBLE COMMENT 'Hybrid battery charge percentage (0-100)',
  `fuel_remaining_kg` DOUBLE COMMENT 'Remaining fuel in kilograms',
  `drs_active` BOOLEAN COMMENT 'Drag Reduction System active flag',
  `speed_kph` DOUBLE COMMENT 'Current speed in km/h',
  `throttle_pct` DOUBLE COMMENT 'Throttle pedal position percentage (0-100)',
  `brake_pct` DOUBLE COMMENT 'Brake pedal position percentage (0-100)',
  `event_time` TIMESTAMP(3) COMMENT 'Sensor reading timestamp',
  WATERMARK FOR `event_time` AS `event_time` - INTERVAL '5' SECOND
) DISTRIBUTED INTO 1 BUCKETS"""

RACE_STANDINGS_SQL = """\
CREATE TABLE `race-standings` (
  `car_number` INT COMMENT 'Car number identifier',
  `driver` STRING COMMENT 'Driver full name',
  `team` STRING COMMENT 'Constructor team name',
  `lap` INT COMMENT 'Current lap number (1-57)',
  `position` INT COMMENT 'Current race position (1-22)',
  `gap_to_leader_sec` DOUBLE COMMENT 'Time gap to race leader in seconds',
  `gap_to_ahead_sec` DOUBLE COMMENT 'Time gap to car directly ahead in seconds',
  `last_lap_time_sec` DOUBLE COMMENT 'Last completed lap time in seconds',
  `pit_stops` INT COMMENT 'Number of pit stops completed',
  `tire_compound` STRING COMMENT 'Current tire compound (SOFT, MEDIUM, HARD)',
  `tire_age_laps` INT COMMENT 'Number of laps on current set of tires',
  `in_pit_lane` BOOLEAN COMMENT 'Whether car is currently in the pit lane',
  `event_time` TIMESTAMP(3) COMMENT 'FIA timing feed timestamp',
  WATERMARK FOR `event_time` AS `event_time` - INTERVAL '10' SECOND,
  PRIMARY KEY (`car_number`) NOT ENFORCED
) DISTRIBUTED BY (`car_number`) INTO 1 BUCKETS"""

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


def run_cli(cmd: list[str], confirm: bool = False) -> tuple[int, str, str]:
    result = subprocess.run(cmd, capture_output=True, text=True, input="y\n" if confirm else None)
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


def delete_flink_statements(core: dict) -> None:
    env_id = core["environment_id"]
    org_id = core["organization_id"]
    rest = core["flink_rest_endpoint"].rstrip("/")
    api_key = core["flink_api_key"]
    api_secret = core["flink_api_secret"]

    token = b64encode(f"{api_key}:{api_secret}".encode()).decode()
    auth = {"Authorization": f"Basic {token}"}

    list_url = f"{rest}/sql/v1/organizations/{org_id}/environments/{env_id}/statements?page_size=100"
    try:
        with urllib.request.urlopen(urllib.request.Request(list_url, headers=auth)) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"  Warning: could not list Flink statements: {e}")
        return

    statements = data.get("data", [])
    running = [
        s["name"] for s in statements
        if s.get("status", {}).get("phase") not in ("COMPLETED", "FAILED", "STOPPED", "DELETING")
    ]

    if not running:
        print("  No running Flink statements found")
        return

    for name in running:
        delete_url = f"{rest}/sql/v1/organizations/{org_id}/environments/{env_id}/statements/{name}"
        try:
            req = urllib.request.Request(delete_url, headers=auth, method="DELETE")
            urllib.request.urlopen(req)
            print(f"  {name}: deleted")
        except Exception as e:
            print(f"  {name}: failed ({e})")


def delete_topic_and_subjects(topic: str, env_id: str, cluster_id: str) -> None:
    rc, _, stderr = run_cli([
        "confluent", "kafka", "topic", "delete", topic,
        "--environment", env_id,
        "--cluster", cluster_id,
    ], confirm=True)
    first_line = stderr.strip().splitlines()[0] if stderr.strip() else ""
    print(f"  Topic {topic}: {'deleted' if rc == 0 else f'skipped ({first_line})'}")

    for subject in [f"{topic}-key", f"{topic}-value"]:
        base_cmd = [
            "confluent", "schema-registry", "schema", "delete",
            "--subject", subject,
            "--version", "all",
            "--environment", env_id,
        ]
        run_cli(base_cmd, confirm=True)
        run_cli([*base_cmd, "--permanent"], confirm=True)
        print(f"  SR {subject}: cleaned")


def recreate_table(core: dict, label: str, sql: str) -> bool:
    env_id = core["environment_id"]
    org_id = core["organization_id"]
    rest = core["flink_rest_endpoint"].rstrip("/")
    api_key = core["flink_api_key"]
    api_secret = core["flink_api_secret"]

    stmt_name = f"reset-{label}-{int(time.time())}"
    token = b64encode(f"{api_key}:{api_secret}".encode()).decode()
    headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    body = json.dumps({
        "name": stmt_name,
        "spec": {
            "statement": sql,
            "compute_pool": {"id": core["compute_pool_id"]},
            "principal": core["service_account_id"],
            "properties": {
                "sql.current-catalog": core["environment_name"],
                "sql.current-database": core["cluster_name"],
            },
        },
    }).encode()

    post_url = f"{rest}/sql/v1/organizations/{org_id}/environments/{env_id}/statements"
    try:
        req = urllib.request.Request(post_url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req) as resp:
            if resp.status not in (200, 201):
                print(f"  Error: POST returned {resp.status}")
                return False
    except urllib.error.HTTPError as e:
        print(f"  Error: POST failed {e.code}: {e.read().decode()[:200]}")
        return False

    poll_url = f"{rest}/sql/v1/organizations/{org_id}/environments/{env_id}/statements/{stmt_name}"
    poll_req = urllib.request.Request(poll_url, headers={"Authorization": f"Basic {token}"})
    for _ in range(30):
        time.sleep(2)
        try:
            with urllib.request.urlopen(poll_req) as resp:
                data = json.loads(resp.read())
                phase = data.get("status", {}).get("phase", "")
                if phase == "COMPLETED":
                    print(f"  {label}: recreated")
                    return True
                if phase in ("FAILED", "STOPPED"):
                    detail = data.get("status", {}).get("detail", "")
                    print(f"  Error: {label} statement {phase}: {detail}")
                    return False
        except Exception as e:
            print(f"  Warning: poll error: {e}")

    print(f"  Timed out waiting for {label} creation")
    return False


def main() -> None:
    print("=== F1 Demo Reset ===\n")

    if not check_confluent_login():
        print("Error: Not logged into Confluent Cloud.")
        print("Run: confluent login")
        sys.exit(1)

    if not check_aws_configured():
        print("Error: AWS CLI not configured.")
        print("Run: aws configure")
        sys.exit(1)

    root = get_project_root()

    creds_file = root / "credentials.env"
    if creds_file.exists():
        creds = dotenv_values(creds_file)
        for k, v in creds.items():
            if v:
                os.environ[k] = v
        if creds.get("TF_VAR_confluent_cloud_api_key"):
            os.environ["CONFLUENT_CLOUD_API_KEY"] = creds["TF_VAR_confluent_cloud_api_key"]
        if creds.get("TF_VAR_confluent_cloud_api_secret"):
            os.environ["CONFLUENT_CLOUD_API_SECRET"] = creds["TF_VAR_confluent_cloud_api_secret"]

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
    region = core["region"]

    print("1. Stopping race simulator...")
    stop_simulator(root, demo, region)

    print("\n2. Deleting Flink statements...")
    delete_flink_statements(core)

    print("\n3. Deleting topics and schema subjects...")
    for topic in RESET_TOPICS:
        delete_topic_and_subjects(topic, env_id, cluster_id)

    print("\n4. Recreating schema-bearing topics...")
    results = []
    results.append(recreate_table(core, "car-telemetry", CAR_TELEMETRY_SQL))
    results.append(recreate_table(core, "race-standings", RACE_STANDINGS_SQL))
    rsraw_ok = recreate_table(core, "race-standings-raw", RACE_STANDINGS_RAW_SQL)
    results.append(rsraw_ok)
    if rsraw_ok:
        run_cli([
            "confluent", "schema-registry", "subject", "update",
            "race-standings-raw-value",
            "--compatibility", "NONE",
            "--environment", env_id,
        ])
        print("  SR race-standings-raw-value: compatibility set to NONE")

    print("\n=== Reset complete ===")
    if all(results):
        print("\nNext steps:")
        print("  1. Re-deploy Flink Jobs 0, 1, 2 in the SQL Workspace")
        print("  2. Start the race:  ./scripts/start-race.sh")
    else:
        print("\nWarning: one or more topics failed to recreate — check above.")


if __name__ == "__main__":
    main()
