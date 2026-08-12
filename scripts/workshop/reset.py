"""`workshop reset-races` — fleet-level reset for all attendee environments.

Stops all race feeds, then fans out the per-environment reset over every
credential card, leaving feeds stopped so LAB 3 can be submitted before
the race resumes:

    uv run workshop reset-races
    uv run workshop reset-races --keep-source

This is the fleet equivalent of ``uv run reset`` (which works on a
single card via Terraform state). The key ordering constraint: feeds must
be stopped **first** (``race_standings`` reads from ``latest``, so LAB 3
must be RUNNING before the race resumes), and they stay stopped on exit.
The instructor restarts with ``uv run workshop start-races`` once
attendees have submitted LAB 3.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from glob import glob
from pathlib import Path

import boto3
from dotenv import dotenv_values

from scripts.common.credentials import load_or_create_credentials_file
from scripts.common.login_checks import ensure_confluent_login
from scripts.common.terraform import get_project_root
from scripts.instructor._common import (
    add_fleet_arguments,
    find_simulator_clusters,
    scale_all_services,
)
from scripts.reset import (
    LAB_DROPS,
    LAB_TOPICS,
    SOURCE_TOPICS,
    delete_flink_statements,
    delete_topic_and_subjects,
    drop_flink_objects,
    existing_topics,
    kafka_admin,
    truncate_topics,
)

DEFAULT_CREDS_GLOB = "runs/*/credentials/*.env"
RESET_WORKERS = 8


def _card_to_tf(card: dict[str, str | None]) -> dict[str, str]:
    """Map F1_* card keys back to the Terraform output shape reset.py expects."""
    def get(key: str) -> str:
        return (card.get(f"F1_{key.upper()}") or "").strip()

    return {
        "flink_rest_endpoint": get("flink_rest_endpoint"),
        "flink_api_key": get("flink_api_key"),
        "flink_api_secret": get("flink_api_secret"),
        "organization_id": get("organization_id"),
        "environment_id": get("environment_id"),
        "compute_pool_id": get("compute_pool_id"),
        "environment_name": get("catalog"),
        "cluster_name": get("database"),
        "cluster_bootstrap": get("kafka_bootstrap"),
        "kafka_api_key": get("kafka_api_key"),
        "kafka_api_secret": get("kafka_api_secret"),
        "cluster_id": get("cluster_id"),
    }


def _validate_tf(tf: dict[str, str], card_path: str) -> list[str]:
    """Check the minimum keys needed for a reset are present."""
    required = ["flink_rest_endpoint", "flink_api_key", "environment_id", "cluster_id"]
    missing = [k for k in required if not tf.get(k)]
    if missing:
        return [f"{card_path}: missing keys {', '.join(missing)} — skipping"]
    return []


def _wait_for_fleet_drain(region: str, name_filter: str, timeout: int = 120) -> bool:
    """Wait until every matched ECS service has runningCount == 0."""
    ecs = boto3.client("ecs", region_name=region)
    clusters = find_simulator_clusters(ecs, name_filter)
    if not clusters:
        return True

    deadline = time.time() + timeout
    while time.time() < deadline:
        all_drained = True
        for cluster_arn in clusters:
            service_arns = ecs.list_services(cluster=cluster_arn).get("serviceArns", [])
            if not service_arns:
                continue
            resp = ecs.describe_services(cluster=cluster_arn, services=service_arns)
            for svc in resp.get("services", []):
                if svc.get("runningCount", 0) > 0:
                    all_drained = False
                    break
            if not all_drained:
                break
        if all_drained:
            return True
        time.sleep(5)

    print(f"  Warning: some simulators still running after {timeout}s")
    return False


def _reset_card(card_path: str, keep_source: bool) -> tuple[str, list[str]]:
    """Reset one credential card without changing any other environment."""
    card = dotenv_values(card_path)
    prefix = card.get("F1_PREFIX") or Path(card_path).stem
    tf = _card_to_tf(card)

    validation = _validate_tf(tf, card_path)
    if validation:
        return prefix, validation

    env_id = tf["environment_id"]
    cluster_id = tf["cluster_id"]
    problems = delete_flink_statements(tf)
    problems += drop_flink_objects(tf, LAB_DROPS)

    try:
        admin = kafka_admin(tf)
    except Exception as exc:
        problems.append(f"no Kafka admin access ({exc})")
        admin = None

    present = existing_topics(admin) if admin is not None else None
    for topic in LAB_TOPICS:
        exists = None if present is None else (topic in present)
        problems += delete_topic_and_subjects(topic, env_id, cluster_id, exists)

    if not keep_source and admin is not None:
        problems += truncate_topics(admin, SOURCE_TOPICS, present)

    return prefix, problems


def _reset_cards(card_paths: list[str], keep_source: bool) -> list[tuple[int, str, list[str]]]:
    """Reset independent cards concurrently while returning deterministic results."""
    results: list[tuple[int, str, list[str]]] = []
    workers = min(RESET_WORKERS, len(card_paths))
    # Keep each confluent_kafka AdminClient in its own process. Thread workers
    # corrupt the client's Future callback state under concurrent fleet resets.
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_reset_card, card_path, keep_source): (index, card_path)
            for index, card_path in enumerate(card_paths, 1)
        }
        for future in as_completed(futures):
            index, card_path = futures[future]
            try:
                prefix, problems = future.result()
            except Exception as exc:
                prefix = Path(card_path).stem
                problems = [f"unexpected reset failure ({exc})"]
            results.append((index, prefix, problems))
    return sorted(results)


def reset_races(args: argparse.Namespace) -> None:
    root = get_project_root()

    card_paths = sorted(glob(str(root / args.creds_glob)))
    if not card_paths:
        raise SystemExit(
            f"No credential cards found matching {args.creds_glob!r}\n"
            "Run `uv run create-workshop` first, or pass --creds-glob with the right pattern."
        )

    print(f"=== Fleet Reset ({len(card_paths)} attendee environment{'s' if len(card_paths) != 1 else ''}) ===\n")

    # --- 0. Confluent CLI login (needed for topic/SR deletes) ---
    creds_file, creds = load_or_create_credentials_file(root)
    if not ensure_confluent_login(creds, creds_file=creds_file, interactive=True):
        raise SystemExit("Confluent CLI login required for topic and Schema Registry cleanup.")
    if creds.get("TF_VAR_confluent_cloud_api_key"):
        os.environ["CONFLUENT_CLOUD_API_KEY"] = creds["TF_VAR_confluent_cloud_api_key"]
    if creds.get("TF_VAR_confluent_cloud_api_secret"):
        os.environ["CONFLUENT_CLOUD_API_SECRET"] = creds["TF_VAR_confluent_cloud_api_secret"]

    # --- 1. Stop all races and wait for tasks to drain ---
    print("1. Stopping all race feeds...")
    updated = scale_all_services(args.region, args.filter, 0)
    if updated == 0:
        print("   No simulator services found — feeds may already be stopped, or no ECS infra.")
    elif updated > 0:
        print("   Waiting for simulator tasks to stop...")
        _wait_for_fleet_drain(args.region, args.filter)
    print()

    # --- 2. Fan out per-card reset ---
    all_problems: list[str] = []
    print(f"2. Resetting environments with up to {min(RESET_WORKERS, len(card_paths))} workers...")
    for i, prefix, problems in _reset_cards(card_paths, args.keep_source):
        print(f"--- [{i}/{len(card_paths)}] {prefix} ---")
        if problems:
            all_problems.extend(f"{prefix}: {p}" for p in problems)
            print(f"  INCOMPLETE — {len(problems)} problem(s)")
        else:
            print("  OK")
        print()

    # --- 3. Summary ---
    if all_problems:
        print("=== Reset INCOMPLETE ===\n")
        for problem in all_problems:
            print(f"  - {problem}")
        print("\nFix the issues above and re-run `uv run workshop reset-races`.")
        print("Feeds are stopped; do NOT start them until all environments reset cleanly.")
        sys.exit(1)

    print("=== Reset complete ===\n")
    print("All attendee environments are reset. Race feeds are stopped.\n")
    print("Next steps:")
    print("  1. Attendees submit LAB 3 (via `uv run f1-sql`)")
    print("  2. Once LAB 3 is RUNNING:  uv run workshop start-races")
    print()
    print("  (race_standings reads from `latest` — a LAB 3 statement submitted")
    print("  after the race starts never sees the versions its first laps need.)")


def add_arguments(p: argparse.ArgumentParser) -> None:
    add_fleet_arguments(p)
    p.add_argument(
        "--creds-glob",
        default=DEFAULT_CREDS_GLOB,
        help=f"Glob pattern for credential cards (default: {DEFAULT_CREDS_GLOB})",
    )
    p.add_argument(
        "--keep-source",
        action="store_true",
        help="Leave car_telemetry / race_standings data in place",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="workshop reset-races",
        description="Reset all attendee environments for a new race — stops feeds, clears lab state",
    )
    add_arguments(parser)
    args = parser.parse_args()
    reset_races(args)


if __name__ == "__main__":
    main()
