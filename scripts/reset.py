"""
Reset lab state for a fresh race re-run.

Stops all running Flink statements, drops demo Flink objects (car_state and
pit_decisions tables, pit_strategy_agent), drops all topics and their SR
subjects, then recreates schema-bearing topics via `terraform apply -replace
-target` so Terraform is the single source of truth for topic schemas.
Also resets the MQ connector for a clean subscription.

Usage: uv run reset [--manual]
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

# Topics created by Terraform — deleted and recreated via terraform apply -replace
SCHEMA_TOPICS = ["car_telemetry", "race_standings", "race_standings_raw"]

# Topics created during the demo — deleted only (user re-creates them by running the jobs)
DEMO_TOPICS = ["car_state", "pit_decisions"]

TF_RESOURCES = [
    "module.topics.confluent_flink_statement.create_car_telemetry_table",
    "module.topics.confluent_flink_statement.create_race_standings_table",
    "module.topics.confluent_flink_statement.create_race_standings_raw_table",
]

# Job 0 is always recreated in the same terraform apply as the topics,
# since race_standings_raw now exists as a Terraform resource (no MQ warmup needed).
JOB0_TF_RESOURCE = "confluent_flink_statement.job0_parse_standings"

AUTOMATED_TF_RESOURCES = [
    "confluent_flink_statement.job1_enrichment_anomaly[0]",
    "confluent_flink_statement.job2_create_agent[0]",
    "confluent_flink_statement.job2_pit_decisions[0]",
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


def drop_all_flink_objects(core: dict) -> None:
    """Submit DROP TABLE / DROP AGENT for all demo Flink objects via REST API.

    Includes the three Terraform-managed schema tables (car_telemetry, race_standings,
    race_standings_raw) so their Flink catalog entries are cleared before Terraform
    recreates them.  Without these drops, Terraform's -replace only deletes the
    statement record — not the catalog entry — so the subsequent CREATE TABLE fails
    with "table already exists" and the Kafka topics are never recreated.
    """
    org_id = core["organization_id"]
    env_id = core["environment_id"]
    rest = core["flink_rest_endpoint"].rstrip("/")
    api_key = core["flink_api_key"]
    api_secret = core["flink_api_secret"]
    compute_pool_id = core["compute_pool_id"]
    catalog = core["environment_name"]
    database = core["cluster_name"]

    token = b64encode(f"{api_key}:{api_secret}".encode()).decode()
    headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}
    url = f"{rest}/sql/v1/organizations/{org_id}/environments/{env_id}/statements"

    drops = [
        # Schema-managed tables — must be cleared so Terraform can re-CREATE them
        ("drop-race_standings_raw", "DROP TABLE IF EXISTS `race_standings_raw`"),
        ("drop-race_standings", "DROP TABLE IF EXISTS `race_standings`"),
        ("drop-car_telemetry", "DROP TABLE IF EXISTS `car_telemetry`"),
        # Demo tables created by the Flink jobs themselves
        ("drop-car_state", "DROP TABLE IF EXISTS `car_state`"),
        ("drop-pit_decisions", "DROP TABLE IF EXISTS `pit_decisions`"),
        ("drop-pit-agent", "DROP AGENT IF EXISTS `pit_strategy_agent`"),
    ]

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


MQ_CONNECTOR_NAME = "f1-mq-source"
MQ_CONNECTOR_CONFIG = "generated/mq_connector_config.json"


def get_connector_id(env_id: str, cluster_id: str, name: str) -> str | None:
    """Return the connector ID for the given name, or None if not found."""
    rc, stdout, _ = run_cli(
        ["confluent", "connect", "cluster", "list", "-o", "json", "--environment", env_id, "--cluster", cluster_id]
    )
    if rc != 0:
        return None
    try:
        for c in json.loads(stdout):
            if c.get("name") == name:
                return c["id"]
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def reset_mq_connector(env_id: str, cluster_id: str, root) -> None:
    config_path = root / MQ_CONNECTOR_CONFIG
    if not config_path.exists():
        print(f"  Skipping — {MQ_CONNECTOR_CONFIG} not found (run 'uv run deploy' first)")
        return

    connector_id = get_connector_id(env_id, cluster_id, MQ_CONNECTOR_NAME)
    if connector_id is None:
        print(f"  {MQ_CONNECTOR_NAME} not found — skipping delete, proceeding to create")
    else:
        rc, _, stderr = run_cli(
            [
                "confluent",
                "connect",
                "cluster",
                "delete",
                connector_id,
                "--force",
                "--environment",
                env_id,
                "--cluster",
                cluster_id,
            ],
        )
        first_line = stderr.strip().splitlines()[0] if stderr.strip() else ""
        if rc == 0:
            print(f"  Deleted {MQ_CONNECTOR_NAME} ({connector_id})")
        else:
            print(f"  Delete failed: {first_line}")
            if get_connector_id(env_id, cluster_id, MQ_CONNECTOR_NAME) is not None:
                print(f"  {MQ_CONNECTOR_NAME} still exists — delete failed. Aborting connector reset.")
                print(f"  To fix: delete the connector manually then re-run reset.")
                return
            print(f"  Confirmed {MQ_CONNECTOR_NAME} is gone — proceeding to recreate")

    rc, _, stderr = run_cli(
        [
            "confluent",
            "connect",
            "cluster",
            "create",
            "--config-file",
            str(config_path),
            "--environment",
            env_id,
            "--cluster",
            cluster_id,
        ]
    )
    if rc == 0:
        print(f"  Recreated {MQ_CONNECTOR_NAME}")
    else:
        print(f"  Failed to recreate {MQ_CONNECTOR_NAME}: {stderr.strip()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset F1 demo for a fresh race re-run")
    parser.add_argument(
        "--manual",
        action="store_true",
        default=False,
        help="Skip recreating Jobs 1 & 2 via Terraform; re-deploy them manually in SQL Workspace after reset.",
    )
    args = parser.parse_args()
    automated = not args.manual

    print("=== F1 Demo Reset ===\n")

    root = get_project_root()

    creds_file = root / "credentials.env"
    creds = dotenv_values(creds_file) if creds_file.exists() else {}

    if not ensure_confluent_login(creds):
        sys.exit(1)

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

    print("\n2. Dropping all Flink objects (tables + agent)...")
    drop_all_flink_objects(core)

    print("\n3. Dropping topics and SR subjects...")
    for topic in SCHEMA_TOPICS + DEMO_TOPICS:
        delete_topic_and_subjects(topic, env_id, cluster_id)

    if automated:
        os.environ["TF_VAR_automated"] = "true"

    print("\n4. Recreating schema topics + Job 0 (and Jobs 1 & 2 unless --manual) via Terraform...")
    target_flags = [f"-target={r}" for r in TF_RESOURCES]
    replace_flags = [f"-replace={r}" for r in TF_RESOURCES]
    target_flags += [f"-target={JOB0_TF_RESOURCE}"]
    replace_flags += [f"-replace={JOB0_TF_RESOURCE}"]
    if automated:
        target_flags += [f"-target={r}" for r in AUTOMATED_TF_RESOURCES]
        replace_flags += [f"-replace={r}" for r in AUTOMATED_TF_RESOURCES]
    cmd = ["terraform", f"-chdir={root}/terraform/demo", "apply", *target_flags, *replace_flags, "-auto-approve"]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("\nError: terraform apply failed — check output above.")
        sys.exit(1)

    print("\n5. Resetting MQ connector...")
    reset_mq_connector(env_id, cluster_id, root)

    print("\n=== Reset complete ===")
    print("Next steps:")
    if automated:
        print("  1. Start the race:  ./scripts/start-race.sh")
    else:
        print("  1. Re-deploy Flink Jobs 1 & 2 in the SQL Workspace (Job 0 already deployed)")
        print("  2. Start the race:  ./scripts/start-race.sh")


if __name__ == "__main__":
    main()
