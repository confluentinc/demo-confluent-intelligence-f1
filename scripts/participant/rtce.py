"""Enable and inspect Confluent RTCE topics from an attendee credential card."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from typing import Any

from scripts.common.credentials import load_card
from scripts.social_feed_rtce.rtce_client import RTCEClient, basic_token

DEFAULT_REGION = "us-east-1"
TOPIC_DESCRIPTIONS = {
    "car_state": (
        "Current per-race state for River Racing car 88, including lap, position, tire condition, race gaps and "
        "anomaly status. Query newest event_time first."
    ),
    "pit_decisions": (
        "Pit strategy decisions for River Racing car 88, grounded in live car state. Query newest event_time first "
        "and use a small result limit."
    ),
}


def _need(card: dict[str, str], key: str) -> str:
    value = card.get(key)
    if not value:
        sys.exit(f"Credential card is missing {key}. Ask the instructor for a refreshed card.")
    return value


def _cloud_env(card: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env["CONFLUENT_CLOUD_API_KEY"] = _need(card, "F1_RTCE_API_KEY")
    env["CONFLUENT_CLOUD_API_SECRET"] = _need(card, "F1_RTCE_API_SECRET")
    return env


def _run_cli(card: dict[str, str], args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=900, env=_cloud_env(card))


def _topic_name(row: dict[str, Any]) -> str:
    spec = row.get("spec") if isinstance(row.get("spec"), dict) else {}
    return str(row.get("topic_name") or row.get("topicName") or spec.get("topic_name") or spec.get("topicName") or "")


def _topic_status(row: dict[str, Any]) -> str:
    status = row.get("status")
    if isinstance(status, dict):
        return str(status.get("phase") or status.get("state") or status.get("status") or "UNKNOWN")
    return str(status or row.get("phase") or row.get("state") or "UNKNOWN")


def wait_until_online(card: dict[str, str], topics: list[str], *, timeout: float = 720, interval: float = 10) -> None:
    """Poll existing and newly created registrations until every requested topic is online."""
    wanted = set(topics)
    deadline = time.monotonic() + timeout
    last: dict[str, str] = {}
    while time.monotonic() < deadline:
        rows = list_registrations(card)
        last = {_topic_name(row): _topic_status(row).upper() for row in rows if _topic_name(row) in wanted}
        failed = {name: state for name, state in last.items() if state in {"FAILED", "ERROR"}}
        if failed:
            rendered = ", ".join(f"{name}={state}" for name, state in sorted(failed.items()))
            sys.exit(f"RTCE materialization failed: {rendered}")
        if wanted and all(last.get(name) in {"ACTIVE", "ONLINE", "READY"} for name in wanted):
            return
        time.sleep(interval)
    rendered = ", ".join(f"{name}={last.get(name, 'MISSING')}" for name in sorted(wanted))
    sys.exit(f"Timed out waiting for RTCE topics: {rendered}")


def list_registrations(card: dict[str, str]) -> list[dict[str, Any]]:
    cmd = [
        "confluent", "rtce", "rtce-topic", "list",
        "--environment", _need(card, "F1_ENVIRONMENT_ID"),
        "--cluster", _need(card, "F1_CLUSTER_ID"),
        "--cloud", "aws",
        "--region", card.get("F1_REGION") or DEFAULT_REGION,
        "-o", "json",
    ]
    result = _run_cli(card, cmd)
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "unknown CLI error"
        sys.exit(
            f"Could not list RTCE topics: {detail}\n"
            "Check the card and run `confluent login` if your CLI session is stale."
        )
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        sys.exit("The Confluent CLI returned invalid JSON while listing RTCE topics.")
    if isinstance(payload, dict):
        payload = payload.get("data") or payload.get("items") or []
    return [row for row in payload if isinstance(row, dict)]


def enable_topics(card: dict[str, str], topics: list[str]) -> None:
    unknown = sorted(set(topics) - TOPIC_DESCRIPTIONS.keys())
    if unknown:
        sys.exit(f"Unsupported workshop topic(s): {', '.join(unknown)}. Choose car_state and/or pit_decisions.")
    registered = {_topic_name(row) for row in list_registrations(card)}
    for topic in dict.fromkeys(topics):
        if topic in registered:
            print(f"{topic}: already registered")
            continue
        cmd = [
            "confluent", "rtce", "rtce-topic", "create",
            "--topic-name", topic,
            "--description", TOPIC_DESCRIPTIONS[topic],
            "--cloud", "aws",
            "--region", card.get("F1_REGION") or DEFAULT_REGION,
            "--environment", _need(card, "F1_ENVIRONMENT_ID"),
            "--cluster", _need(card, "F1_CLUSTER_ID"),
            "--wait", "--timeout", "12m",
            "-o", "json",
        ]
        result = _run_cli(card, cmd)
        if result.returncode != 0:
            detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "unknown CLI error"
            sys.exit(f"{topic}: RTCE registration failed: {detail}")
        print(f"{topic}: online")
    wait_until_online(card, list(dict.fromkeys(topics)))
    print("All requested RTCE topics are online.")


def print_status(card: dict[str, str]) -> None:
    rows = list_registrations(card)
    if not rows:
        print("No RTCE topics are registered for this cluster.")
        return
    for row in sorted(rows, key=_topic_name):
        print(f"{_topic_name(row) or '<unknown>'}: {_topic_status(row)}")


async def probe(card: dict[str, str]) -> None:
    endpoint = _need(card, "F1_RTCE_MCP_ENDPOINT")
    token = basic_token(_need(card, "F1_RTCE_API_KEY"), _need(card, "F1_RTCE_API_SECRET"))
    client = RTCEClient(endpoint, token)
    discovered = await client.discover_tools()
    print("MCP tools: " + ", ".join(discovered.values()))
    topics_result = await client.list_topics()
    topic_names = client.topic_names(topics_result)
    print("Topics: " + (", ".join(topic_names) if topic_names else "none returned"))
    preferred = next((name for name in ("car_state", "pit_decisions", "car_telemetry") if name in topic_names), None)
    if preferred is None:
        sys.exit("Probe connected, but none of the workshop topics are online in RTCE yet.")
    await client.get_metadata(preferred)
    rows = await client.query(preferred, order_by='"EVENT_TIME" DESC', limit=5, max_rows=5)
    print(f"{preferred}: metadata read; query returned {len(rows)} row(s)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Enable and test workshop topics in Confluent RTCE")
    parser.add_argument("--creds", help="Path to credentials.env or an attendee credential card")
    sub = parser.add_subparsers(dest="command", required=True)
    enable = sub.add_parser("enable", help="Create missing RTCE topic registrations and wait for them")
    enable.add_argument("topics", nargs="+", choices=sorted(TOPIC_DESCRIPTIONS))
    sub.add_parser("status", help="Show RTCE registration state")
    sub.add_parser("probe", help="Discover the live MCP tools and make metadata/query calls")
    args = parser.parse_args()
    _, card = load_card(args.creds)
    if args.command == "enable":
        enable_topics(card, args.topics)
    elif args.command == "status":
        print_status(card)
    else:
        asyncio.run(probe(card))


if __name__ == "__main__":
    main()
