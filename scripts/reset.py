"""
Reset an attendee's lab state for a fresh run of the stream-processing labs.

Stops running Flink statements and drops the objects attendees create during
the labs — the `car_state` and `pit_decisions` tables, the `pit_strategy_agent`,
and their backing topics + Schema Registry subjects. The schema-bearing source
topics (`car_telemetry`, `race_standings`) are owned by Terraform and fed by the
live race simulator, so they are left untouched.

This is a Confluent-only operation — it does not run Terraform.

Usage: uv run reset
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from base64 import b64encode

from dotenv import dotenv_values

from scripts.common.login_checks import ensure_confluent_login
from scripts.common.terraform import get_project_root, run_terraform_output

# Topics created by attendees while running the labs — deleted only (they are
# recreated when the attendee re-runs the Flink jobs).
LAB_TOPICS = ["car_state", "pit_decisions"]

# Flink objects created by the labs, dropped before their topics. Labels become
# part of the Flink statement name, which rejects underscores (HTTP 400).
LAB_DROPS = [
    ("drop-car-state",     "DROP TABLE IF EXISTS `car_state`"),
    ("drop-pit-decisions", "DROP TABLE IF EXISTS `pit_decisions`"),
    ("drop-pit-agent",     "DROP AGENT IF EXISTS `pit_strategy_agent`"),
]


def run_cli(cmd: list[str], confirm: bool = False) -> tuple[int, str, str]:
    result = subprocess.run(cmd, capture_output=True, text=True, input="y\n" if confirm else None)
    return result.returncode, result.stdout, result.stderr


def delete_flink_statements(tf: dict) -> None:
    env_id = tf["environment_id"]
    org_id = tf["organization_id"]
    rest = tf["flink_rest_endpoint"].rstrip("/")
    api_key = tf["flink_api_key"]
    api_secret = tf["flink_api_secret"]

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
        s["name"]
        for s in statements
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
    rc, _, stderr = run_cli(
        [
            "confluent",
            "kafka",
            "topic",
            "delete",
            topic,
            "--environment",
            env_id,
            "--cluster",
            cluster_id,
        ],
        confirm=True,
    )
    first_line = stderr.strip().splitlines()[0] if stderr.strip() else ""
    print(f"  Topic {topic}: {'deleted' if rc == 0 else f'skipped ({first_line})'}")

    for subject in [f"{topic}-key", f"{topic}-value"]:
        base_cmd = [
            "confluent",
            "schema-registry",
            "schema",
            "delete",
            "--subject",
            subject,
            "--version",
            "all",
            "--environment",
            env_id,
        ]
        run_cli(base_cmd, confirm=True)
        run_cli([*base_cmd, "--permanent"], confirm=True)
        print(f"  SR {subject}: cleaned")


def drop_flink_objects(tf: dict, drops: list) -> None:
    """Submit DROP TABLE / DROP AGENT statements via the Flink REST API."""
    org_id = tf["organization_id"]
    env_id = tf["environment_id"]
    rest = tf["flink_rest_endpoint"].rstrip("/")
    api_key = tf["flink_api_key"]
    api_secret = tf["flink_api_secret"]
    compute_pool_id = tf["compute_pool_id"]
    catalog = tf["environment_name"]
    database = tf["cluster_name"]

    token = b64encode(f"{api_key}:{api_secret}".encode()).decode()
    headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}
    url = f"{rest}/sql/v1/organizations/{org_id}/environments/{env_id}/statements"

    for label, sql in drops:
        name = f"reset-{label}-{int(time.time())}"
        body = json.dumps(
            {
                "name": name,
                "spec": {
                    "statement": sql,
                    "compute_pool": {"id": compute_pool_id},
                    "properties": {
                        "sql.current-catalog": catalog,
                        "sql.current-database": database,
                    },
                },
            }
        ).encode()
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            urllib.request.urlopen(req)
            print(f"  {sql}: submitted")
        except Exception as e:
            print(f"  {sql}: {e}")


def main() -> None:
    argparse.ArgumentParser(description="Reset attendee lab state for a fresh stream-processing run").parse_args()

    print("=== F1 Workshop Lab Reset ===\n")

    root = get_project_root()

    creds_file = root / "credentials.env"
    creds = dotenv_values(creds_file) if creds_file.exists() else {}

    if not ensure_confluent_login(creds, creds_file=creds_file, interactive=True):
        sys.exit(1)

    for k, v in creds.items():
        if v:
            os.environ[k] = v
    if creds.get("TF_VAR_confluent_cloud_api_key"):
        os.environ["CONFLUENT_CLOUD_API_KEY"] = creds["TF_VAR_confluent_cloud_api_key"]
    if creds.get("TF_VAR_confluent_cloud_api_secret"):
        os.environ["CONFLUENT_CLOUD_API_SECRET"] = creds["TF_VAR_confluent_cloud_api_secret"]

    tf_state = root / "terraform" / "aws" / "terraform.tfstate"
    try:
        tf = run_terraform_output(tf_state)
    except Exception as e:
        print(f"Error reading terraform state: {e}\nHave you provisioned the attendee environment yet?")
        sys.exit(1)

    env_id = tf["environment_id"]
    cluster_id = tf["cluster_id"]

    print("1. Stopping Flink statements...")
    delete_flink_statements(tf)

    print("\n2. Dropping lab Flink objects (car_state, pit_decisions, pit_strategy_agent)...")
    drop_flink_objects(tf, LAB_DROPS)

    print("\n3. Dropping lab topics and SR subjects...")
    for topic in LAB_TOPICS:
        delete_topic_and_subjects(topic, env_id, cluster_id)

    print("\n=== Reset complete ===")
    print("Next steps:")
    print("  Re-run the stream-processing labs (LAB3 → LAB4) in the Flink SQL Workspace.")
    print("  The race simulator keeps feeding car_telemetry and race_standings — no restart needed.")


if __name__ == "__main__":
    main()
