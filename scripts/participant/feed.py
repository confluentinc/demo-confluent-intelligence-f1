"""Detect a recently active race feed without consuming the topic history."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

from confluent_kafka import Consumer, TopicPartition

TELEMETRY_TOPIC = "car_telemetry"
ACTIVE_WITHIN_SECONDS = 90


def timestamp_is_recent(
    timestamp_ms: int | None, *, now: float | None = None, max_age: int = ACTIVE_WITHIN_SECONDS
) -> bool:
    """Return whether a Kafka create-time timestamp falls inside ``max_age``."""
    if timestamp_ms is None or timestamp_ms < 0:
        return False
    current = time.time() if now is None else now
    age = current - (timestamp_ms / 1000)
    return 0 <= age <= max_age


def _need(creds: dict[str, str], key: str) -> str:
    value = creds.get(key)
    if not value:
        raise SystemExit(
            f"Credential card is missing {key}. Run `uv run f1-onboard` again or ask the instructor for a new card."
        )
    return value


def _consumer(creds: dict[str, str]) -> Consumer:
    bootstrap = _need(creds, "F1_KAFKA_BOOTSTRAP").split("://", 1)[-1]
    return Consumer(
        {
            "bootstrap.servers": bootstrap,
            "security.protocol": "SASL_SSL",
            "sasl.mechanisms": "PLAIN",
            "sasl.username": _need(creds, "F1_KAFKA_API_KEY"),
            "sasl.password": _need(creds, "F1_KAFKA_API_SECRET"),
            "group.id": f"f1-feed-check-{uuid.uuid4().hex[:10]}",
            "enable.auto.commit": False,
            "log_level": 0,
        }
    )


def latest_telemetry_timestamp(
    creds: dict[str, str], *, timeout: float = 8.0, consumer_factory: Callable[[dict[str, str]], Consumer] = _consumer
) -> int | None:
    """Read only the last record in each telemetry partition and return its newest timestamp."""
    consumer = consumer_factory(creds)
    try:
        metadata = consumer.list_topics(topic=TELEMETRY_TOPIC, timeout=timeout)
        topic = metadata.topics.get(TELEMETRY_TOPIC)
        if topic is None or topic.error is not None:
            return None

        targets: list[TopicPartition] = []
        for partition in topic.partitions:
            low, high = consumer.get_watermark_offsets(TopicPartition(TELEMETRY_TOPIC, partition), timeout=timeout)
            if high > low:
                targets.append(TopicPartition(TELEMETRY_TOPIC, partition, high - 1))
        if not targets:
            return None

        consumer.assign(targets)
        pending = {(tp.topic, tp.partition) for tp in targets}
        newest: int | None = None
        deadline = time.monotonic() + timeout
        while pending and time.monotonic() < deadline:
            msg = consumer.poll(min(0.5, max(0.0, deadline - time.monotonic())))
            if msg is None:
                continue
            if msg.error():
                continue
            pending.discard((msg.topic(), msg.partition()))
            _, timestamp_ms = msg.timestamp()
            if timestamp_ms is not None and timestamp_ms >= 0:
                newest = timestamp_ms if newest is None else max(newest, timestamp_ms)
        return newest
    finally:
        consumer.close()


def active_feed(
    creds: dict[str, str], *, max_age: int = ACTIVE_WITHIN_SECONDS, timeout: float = 8.0
) -> tuple[bool, float | None]:
    """Return ``(active, age_seconds)`` for the newest telemetry record."""
    timestamp_ms = latest_telemetry_timestamp(creds, timeout=timeout)
    if timestamp_ms is None:
        return False, None
    age = max(0.0, time.time() - timestamp_ms / 1000)
    return timestamp_is_recent(timestamp_ms, max_age=max_age), age
