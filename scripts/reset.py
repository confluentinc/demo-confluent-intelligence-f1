"""
Reset lab state for a fresh race re-run.

Stops all running Flink statements, drops the three schema-bearing topics
and their SR subjects, then recreates them via `terraform apply -replace
-target` so Terraform is the single source of truth for topic schemas.
Connectors are left running.

Usage: uv run reset
"""

import json
import os
import subprocess
import sys
import urllib.request
from base64 import b64encode

from dotenv import dotenv_values

from scripts.common.login_checks import check_confluent_login
from scripts.common.terraform import get_project_root, run_terraform_output

SCHEMA_TOPICS = ["car-telemetry", "race-standings", "race-standings-raw"]

TF_RESOURCES = [
    "module.topics.confluent_flink_statement.create_car_telemetry_table",
    "module.topics.confluent_flink_statement.create_race_standings_table",
    "module.topics.confluent_flink_statement.create_race_standings_raw_table",
]


def run_cli(cmd: list[str], confirm: bool = False) -> tuple[int, str, str]:
    result = subprocess.run(cmd, capture_output=True, text=True, input="y\n" if confirm else None)
    return result.returncode, result.stdout, result.stderr


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


def main() -> None:
    print("=== F1 Demo Reset ===\n")

    if not check_confluent_login():
        print("Error: Not logged into Confluent Cloud. Run: confluent login")
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
    try:
        core = run_terraform_output(core_state)
    except Exception as e:
        print(f"Error reading terraform state: {e}\nHave you run 'uv run deploy' yet?")
        sys.exit(1)

    env_id = core["environment_id"]
    cluster_id = core["cluster_id"]

    print("1. Stopping Flink statements...")
    delete_flink_statements(core)

    print("\n2. Dropping schema-bearing topics and SR subjects...")
    for topic in SCHEMA_TOPICS:
        delete_topic_and_subjects(topic, env_id, cluster_id)

    print("\n3. Recreating topics via Terraform...")
    target_flags = [f"-target={r}" for r in TF_RESOURCES]
    replace_flags = [f"-replace={r}" for r in TF_RESOURCES]
    cmd = ["terraform", f"-chdir={root}/terraform/demo", "apply", *target_flags, *replace_flags, "-auto-approve"]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("\nError: terraform apply failed — check output above.")
        sys.exit(1)

    print("\n=== Reset complete ===")
    print("Next steps:")
    print("  1. Re-deploy Flink Jobs 0, 1, 2 in the SQL Workspace")
    print("  2. Start the race:  ./scripts/start-race.sh")


if __name__ == "__main__":
    main()
