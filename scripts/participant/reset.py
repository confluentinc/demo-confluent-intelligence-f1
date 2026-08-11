"""Reset one assigned Confluent account without AWS access."""

from __future__ import annotations

import argparse
import sys

import requests
from confluent_kafka import OFFSET_END, TopicPartition
from confluent_kafka.admin import AdminClient

from scripts.common.credentials import load_card
from scripts.participant.feed import ACTIVE_WITHIN_SECONDS, active_feed

APPEND_TOPICS = ("car_telemetry", "car_state", "pit_decisions")
LAB_SQL_MARKERS = ("car_state", "pit_decisions", "pit_strategy_agent")
TERMINAL_PHASES = {"COMPLETED", "FAILED", "STOPPED", "DELETING"}


def _need(card: dict[str, str], key: str) -> str:
    value = card.get(key)
    if not value:
        sys.exit(f"Credential card is missing {key}. Run `uv run f1-onboard` again or ask for a new card.")
    return value


def _redact(message: object, card: dict[str, str]) -> str:
    text = str(message)
    for key, value in card.items():
        if value and ("SECRET" in key or "PASSWORD" in key or "API_KEY" in key):
            text = text.replace(value, "[redacted]")
    return text


def _flink_base(card: dict[str, str]) -> tuple[str, tuple[str, str]]:
    rest = _need(card, "F1_FLINK_REST_ENDPOINT").rstrip("/")
    org = _need(card, "F1_ORGANIZATION_ID")
    env = _need(card, "F1_ENVIRONMENT_ID")
    return (
        f"{rest}/sql/v1/organizations/{org}/environments/{env}/statements",
        (_need(card, "F1_FLINK_API_KEY"), _need(card, "F1_FLINK_API_SECRET")),
    )


def cancel_lab_statements(card: dict[str, str]) -> int:
    """Cancel active statements whose submitted SQL refers to workshop lab objects."""
    base, auth = _flink_base(card)
    response = requests.get(base, params={"page_size": 100}, auth=auth, timeout=30)
    response.raise_for_status()
    cancelled = 0
    for summary in response.json().get("data", []):
        name = summary.get("name", "")
        phase = (summary.get("status") or {}).get("phase")
        if not name or phase in TERMINAL_PHASES:
            continue
        detail_response = requests.get(f"{base}/{name}", auth=auth, timeout=30)
        detail_response.raise_for_status()
        detail = detail_response.json()
        sql = str((detail.get("spec") or {}).get("statement") or "").lower()
        if not any(marker in sql for marker in LAB_SQL_MARKERS):
            continue
        delete = requests.delete(f"{base}/{name}", auth=auth, timeout=30)
        delete.raise_for_status()
        cancelled += 1
    return cancelled


def _admin(card: dict[str, str]) -> AdminClient:
    return AdminClient(
        {
            "bootstrap.servers": _need(card, "F1_KAFKA_BOOTSTRAP").split("://", 1)[-1],
            "security.protocol": "SASL_SSL",
            "sasl.mechanisms": "PLAIN",
            "sasl.username": _need(card, "F1_KAFKA_API_KEY"),
            "sasl.password": _need(card, "F1_KAFKA_API_SECRET"),
            "log_level": 0,
        }
    )


def truncate_append_topics(card: dict[str, str]) -> tuple[list[str], list[str]]:
    """Move low watermarks to the end for existing append-only workshop topics."""
    admin = _admin(card)
    metadata = admin.list_topics(timeout=30)
    present = set(metadata.topics)
    cleared: list[str] = []
    skipped: list[str] = []
    for topic in APPEND_TOPICS:
        if topic not in present:
            skipped.append(topic)
            continue
        topic_meta = admin.list_topics(topic=topic, timeout=30).topics[topic]
        partitions = [TopicPartition(topic, number, OFFSET_END) for number in topic_meta.partitions]
        for future in admin.delete_records(partitions).values():
            future.result()
        cleared.append(topic)
    return cleared, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset the labs in one assigned workshop account")
    parser.add_argument("--creds", help="Path to credentials.env or an attendee credential card")
    args = parser.parse_args()
    path, card = load_card(args.creds)
    print(f"Using credential card: {path}")

    try:
        active, age = active_feed(card, max_age=ACTIVE_WITHIN_SECONDS)
    except Exception as exc:
        sys.exit(f"Could not verify whether the race feed is active: {_redact(exc, card)}")
    if active:
        sys.exit(
            f"Reset refused: telemetry arrived {age:.0f}s ago. Stop the cloud race or local fallback, "
            "wait 90 seconds, then run this command again."
        )

    try:
        cancelled = cancel_lab_statements(card)
        cleared, skipped = truncate_append_topics(card)
    except Exception as exc:
        sys.exit(f"Account reset failed: {_redact(exc, card)}")

    print(f"Stopped {cancelled} active lab statement(s).")
    print("Cleared append-only topics: " + (", ".join(cleared) if cleared else "none"))
    if skipped:
        print("Not created yet: " + ", ".join(skipped))
    print("Compacted state was left in place; race_id keeps the next race isolated.")


if __name__ == "__main__":
    main()
