"""
Reset an attendee's lab state for a fresh run of the stream-processing labs.

Gives a blank slate to start a new race from. Stops running Flink statements,
drops the objects attendees create during the labs — the `car_state` and
`pit_decisions` tables, the `pit_strategy_agent`, and their backing topics +
Schema Registry subjects — and clears the race data already sitting in the
source topics.

The source topics are *truncated*, not deleted: `car_telemetry` and
`race_standings` are Terraform-owned (created by a Flink CREATE TABLE, with
registered schemas), so deleting them would break Terraform state and the
schemas the simulator produces against. Deleting their records leaves the topic,
its config, and its subjects intact while removing every record — which is what
"free from previous races" actually requires. Pass --keep-source to skip it.

`race_standings` is compacted (it is the keyed upsert side of the LAB 3 temporal
join) and Kafka refuses delete-records on a compacted topic. That is reported,
not worked around: compaction already reduces it to the latest row per
car_number, lap 0 of the next race overwrites all 22 keys, and the temporal join
resolves versions by event time so a finished race's rows can never be selected
by newer telemetry.

This is a Confluent-only operation — it does not run Terraform.

Usage: uv run reset [--keep-source]
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

# Terraform-owned source topics the simulator produces to. Truncated, never
# deleted — see the module docstring.
SOURCE_TOPICS = ["car_telemetry", "race_standings"]

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


def running_simulator_count(region: str = "us-east-1") -> int:
    """How many attendee race simulators are still running.

    Truncating while the simulator produces is pointless — records land again
    the moment delete_records returns — so reset warns instead of silently
    doing half a job. Best-effort: a missing/denied AWS setup must not block a
    Confluent-only reset, so any failure reports 0.
    """
    try:
        import boto3

        from scripts.instructor._common import DEFAULT_CLUSTER_FILTER, find_simulator_clusters

        ecs = boto3.client("ecs", region_name=region)
        running = 0
        for cluster_arn in find_simulator_clusters(ecs, DEFAULT_CLUSTER_FILTER):
            service_arns = ecs.list_services(cluster=cluster_arn).get("serviceArns", [])
            if not service_arns:
                continue
            for svc in ecs.describe_services(cluster=cluster_arn, services=service_arns).get("services", []):
                if svc.get("desiredCount", 0) > 0:
                    running += 1
        return running
    except Exception:
        return 0


def truncate_topics(tf: dict, topics: list[str]) -> None:
    """Delete every record in `topics`, leaving the topics and schemas in place.

    Uses the Kafka delete-records admin API (there is no `confluent kafka topic
    delete-records` CLI equivalent) to move each partition's low watermark up to
    its high watermark.
    """
    from confluent_kafka import OFFSET_END, TopicPartition
    from confluent_kafka.admin import AdminClient

    admin = AdminClient(
        {
            "bootstrap.servers": tf["cluster_bootstrap"].split("://", 1)[-1],
            "security.protocol": "SASL_SSL",
            "sasl.mechanisms": "PLAIN",
            "sasl.username": tf["kafka_api_key"],
            "sasl.password": tf["kafka_api_secret"],
        }
    )

    for topic in topics:
        try:
            meta = admin.list_topics(topic=topic, timeout=30)
        except Exception as e:
            print(f"  Topic {topic}: skipped (metadata lookup failed: {e})")
            continue

        topic_meta = meta.topics.get(topic)
        if topic_meta is None or topic_meta.error is not None:
            print(f"  Topic {topic}: skipped (not found)")
            continue

        # OFFSET_END deletes up to each partition's current high watermark.
        partitions = [TopicPartition(topic, p, OFFSET_END) for p in topic_meta.partitions]
        futures = admin.delete_records(partitions)

        deleted, errors = 0, []
        for tp, future in futures.items():
            try:
                future.result()
                deleted += 1
            except Exception as e:
                errors.append((tp.partition, e))

        if not errors:
            print(f"  Topic {topic}: cleared ({deleted} partition(s))")
            continue

        # Kafka rejects delete-records on a compacted topic. `race_standings` is
        # compacted because it is the keyed upsert side of the LAB 3 temporal
        # join, so this is expected and not worth alarming anyone over: a
        # compacted topic only resolves to the latest value per car_number, and
        # the temporal join picks the version as of each telemetry row's
        # event_time. Rows from a finished race are strictly older than any new
        # telemetry, so they can never be selected, and lap 0 of the next race
        # overwrites all 22 keys anyway.
        if any("POLICY_VIOLATION" in str(e) for _, e in errors):
            print(f"  Topic {topic}: kept (compacted topic — records can't be deleted)")
            print("    Harmless: the next race overwrites every key on lap 0, and the")
            print("    temporal join can't reach versions older than the new telemetry.")
            continue

        for partition, e in errors:
            print(f"    partition {partition}: {e}")
        print(f"  Topic {topic}: {deleted} partition(s) cleared, {len(errors)} failed")


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
    parser = argparse.ArgumentParser(description="Reset attendee lab state for a fresh stream-processing run")
    parser.add_argument(
        "--keep-source",
        action="store_true",
        help="Leave car_telemetry / race_standings data in place (default: clear them for a blank slate)",
    )
    args = parser.parse_args()

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

    if args.keep_source:
        print("\n4. Keeping source topic data (--keep-source).")
    else:
        print("\n4. Clearing race data from source topics...")
        still_running = running_simulator_count()
        if still_running:
            print(f"  WARNING: {still_running} race simulator(s) still running — they will")
            print("  keep producing, so the topics will not stay empty. Stop them first with")
            print("  `uv run stop-all-races`, re-run this reset, then `uv run start-all-races`.")
        truncate_topics(tf, SOURCE_TOPICS)

    print("\n=== Reset complete ===")
    print("Next steps:")
    if args.keep_source:
        print("  Re-run the stream-processing labs (LAB 3 → LAB 4).")
        print("  The race simulator keeps feeding car_telemetry and race_standings.")
    else:
        print("  1. `uv run start-all-races`  — starts a fresh race from lap 0")
        print("  2. Re-run the stream-processing labs (LAB 3 → LAB 4)")
        print("  car_telemetry is empty, so LAB 3 sees only the new race instead of")
        print("  replaying finished ones.")


if __name__ == "__main__":
    main()
