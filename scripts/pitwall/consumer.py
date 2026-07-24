"""Kafka → RaceState consumer for the Pit Wall dashboard.

Reads the attendee's four topics with the keys on their credential card and feeds
each record into the shared ``RaceState``. This is the same confluent-kafka +
Schema Registry stack the simulator uses to *produce* (``datagen/simulator.py``),
run in reverse:

  - ``car_telemetry`` starts at the *latest* offset (a live view — we don't want
    to replay the whole race's high-volume telemetry on launch).
  - ``race_standings`` starts at the *earliest* offset. It's a keyed upsert
    snapshot table (PRIMARY KEY car_number), not an event stream — reading from
    the beginning lets the dashboard populate the full leaderboard immediately
    from the latest value per car, instead of showing an empty board until the
    simulator happens to produce the next lap. Replay is cheap (22 keys, and the
    changelog topic is compacted).
  - ``car_state`` + ``pit_decisions`` start at the *earliest* offset so a
    dashboard launched mid-lab still shows the rows the attendee has already
    produced (these topics are also low-volume).

``car_state`` / ``pit_decisions`` don't exist until the attendee builds LAB 3 /
LAB 4. Subscribing to a not-yet-created topic is fine — the consumer picks it up
on the next metadata refresh once Flink creates it; we just suppress the
``UNKNOWN_TOPIC`` log noise until then.
"""

from __future__ import annotations

import logging
import uuid

from confluent_kafka import OFFSET_BEGINNING, OFFSET_END, Consumer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer
from confluent_kafka.serialization import MessageField, SerializationContext

from scripts.pitwall.state import RaceState

logger = logging.getLogger("f1-pitwall.consumer")

TELEMETRY_TOPIC = "car_telemetry"
STANDINGS_TOPIC = "race_standings"
CAR_STATE_TOPIC = "car_state"
PIT_DECISIONS_TOPIC = "pit_decisions"

# Topics we want from the beginning: the keyed standings snapshot table plus the
# low-volume lab outputs. Only the high-volume telemetry stream tails from the end.
EARLIEST_TOPICS = {STANDINGS_TOPIC, CAR_STATE_TOPIC, PIT_DECISIONS_TOPIC}
ALL_TOPICS = [TELEMETRY_TOPIC, STANDINGS_TOPIC, CAR_STATE_TOPIC, PIT_DECISIONS_TOPIC]


def _bootstrap(raw: str) -> str:
    """Strip the ``SASL_SSL://`` scheme the credential card carries."""
    return raw.split("://", 1)[-1] if "://" in raw else raw


def _build_consumer(creds: dict[str, str]) -> Consumer:
    try:
        return Consumer(
            {
                "bootstrap.servers": _bootstrap(creds["F1_KAFKA_BOOTSTRAP"]),
                "security.protocol": "SASL_SSL",
                "sasl.mechanisms": "PLAIN",
                "sasl.username": creds["F1_KAFKA_API_KEY"],
                "sasl.password": creds["F1_KAFKA_API_SECRET"],
                # Fresh group every launch — we never commit, this is a live tail.
                "group.id": f"f1-pitwall-{uuid.uuid4().hex[:8]}",
                "enable.auto.commit": False,
                "auto.offset.reset": "earliest",
                "log_level": 0,
            }
        )
    except KeyError as e:
        raise SystemExit(
            f"Credential file is missing {e}. Regenerate it with `uv run workshop creds`."
        ) from e


def _build_deserializer(creds: dict[str, str]) -> AvroDeserializer:
    try:
        sr = SchemaRegistryClient(
            {
                "url": creds["F1_SCHEMA_REGISTRY_URL"],
                "basic.auth.user.info": f"{creds['F1_SR_API_KEY']}:{creds['F1_SR_API_SECRET']}",
            }
        )
    except KeyError as e:
        raise SystemExit(
            f"Credential file is missing {e}. Regenerate it with `uv run workshop creds`."
        ) from e
    # schema_str=None: the writer schema is resolved per-message from the embedded
    # schema id, so one deserializer serves every Avro value topic.
    return AvroDeserializer(sr, schema_str=None)


def _decode_standings_key(deserialize: AvroDeserializer, raw_key: bytes | None) -> int | None:
    """Pull car_number out of a race_standings Avro message key.

    Confluent Cloud Flink registers the single-column PRIMARY KEY either as a
    record wrapping car_number ({"car_number": 88}) or as a bare primitive int,
    so we accept both shapes (mirroring the simulator's key encoding).
    """
    if raw_key is None:
        return None
    try:
        key = deserialize(raw_key, SerializationContext(STANDINGS_TOPIC, MessageField.KEY))
    except Exception as e:  # a bad key must not kill the tail
        logger.debug("deserialize %s key failed: %s", STANDINGS_TOPIC, e)
        return None
    if isinstance(key, dict):
        return key.get("car_number")
    return key


def _on_assign(consumer: Consumer, partitions) -> None:
    """Seek lab-output partitions to the beginning, live topics to the end."""
    for tp in partitions:
        tp.offset = OFFSET_BEGINNING if tp.topic in EARLIEST_TOPICS else OFFSET_END
    consumer.assign(partitions)


def run_consumer(creds: dict[str, str], state: RaceState, stop) -> None:
    """Poll Kafka until ``stop`` is set, routing each record into ``state``.

    ``stop`` is a ``threading.Event``-like object (anything with ``is_set()``).
    """
    consumer = _build_consumer(creds)
    deserialize = _build_deserializer(creds)
    consumer.subscribe(ALL_TOPICS, on_assign=_on_assign)
    logger.info("Consuming %s", ", ".join(ALL_TOPICS))

    routes = {
        TELEMETRY_TOPIC: state.update_telemetry,
        STANDINGS_TOPIC: state.update_standing,
        CAR_STATE_TOPIC: state.update_car_state,
        PIT_DECISIONS_TOPIC: state.add_decision,
    }

    try:
        while not stop.is_set():
            msg = consumer.poll(0.5)
            if msg is None:
                continue
            if msg.error():
                # UNKNOWN_TOPIC is expected until the attendee builds LAB 3/4.
                logger.debug("kafka: %s", msg.error())
                continue
            topic = msg.topic()
            try:
                value = deserialize(msg.value(), SerializationContext(topic, MessageField.VALUE))
            except Exception as e:  # one bad record must not kill the tail
                logger.debug("deserialize %s failed: %s", topic, e)
                continue
            if value is None:
                continue
            # race_standings is a Flink upsert table keyed by car_number, so
            # car_number lives in the Avro *key* (subject race_standings-key) and
            # is absent from the value. Decode the key and merge it back in, or
            # update_standing() drops every record for want of a car_number.
            if topic == STANDINGS_TOPIC and "car_number" not in value:
                car_number = _decode_standings_key(deserialize, msg.key())
                if car_number is None:
                    continue
                value["car_number"] = car_number
            routes[topic](value)
    finally:
        consumer.close()
