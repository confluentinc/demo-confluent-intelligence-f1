"""Kafka → FeedState consumer for one attendee's social feed.

Reuses the Pit Wall's confluent-kafka + Schema Registry building blocks
(``scripts.pitwall.consumer``) — same credential-card keys, same Avro
deserializer, same race_standings key decoding — but routes into a ``FeedState``
digest instead of the dashboard's ``RaceState``.

We subscribe to the three low-volume topics that describe the *race situation*:
``race_standings`` (keyed upsert snapshot), ``car_state`` (LAB 3 output, carries
the tire anomaly flag) and ``pit_decisions`` (LAB 4 output). All three read from
the earliest offset (the consumer uses a fresh, never-committed group with
``auto.offset.reset=earliest``) so a feed started mid-session is fully populated.
We skip ``car_telemetry`` — the agent posts about race events, not raw sensor Hz.
"""

from __future__ import annotations

import logging

from confluent_kafka.serialization import MessageField, SerializationContext

from scripts.pitwall.consumer import (
    CAR_STATE_TOPIC,
    PIT_DECISIONS_TOPIC,
    STANDINGS_TOPIC,
    ConsumerErrorReporter,
    _build_consumer,
    _build_deserializer,
    _decode_standings_key,
)
from scripts.social_feed.state import FeedState

logger = logging.getLogger("f1-social-feed.consumer")

TOPICS = [STANDINGS_TOPIC, CAR_STATE_TOPIC, PIT_DECISIONS_TOPIC]


def run_consumer(creds: dict[str, str], feed: FeedState, stop) -> None:
    """Poll Kafka until ``stop`` is set, routing each record into ``feed``."""
    reporter = ConsumerErrorReporter(feed, label=feed.prefix)
    try:
        consumer = _build_consumer(creds, error_cb=reporter.kafka_error)
        deserialize = _build_deserializer(creds)
        consumer.subscribe(TOPICS)
    except SystemExit as exc:
        logger.error("[%s] %s", feed.prefix, exc)
        feed.record_error("CARD_INCOMPLETE", str(exc))
        return
    except Exception as exc:
        logger.error("[%s] could not start consumer: %s", feed.prefix, exc)
        feed.record_error("FEED_START_FAILED", f"Could not start the race feed: {exc}")
        return
    feed.mark_consumer_ready()
    logger.info("[%s] consuming %s", feed.prefix, ", ".join(TOPICS))

    routes = {
        STANDINGS_TOPIC: feed.update_standing,
        CAR_STATE_TOPIC: feed.update_car_state,
        PIT_DECISIONS_TOPIC: feed.add_decision,
    }

    try:
        while not stop.is_set():
            msg = consumer.poll(0.5)
            if msg is None:
                continue
            if msg.error():
                # UNKNOWN_TOPIC is expected until the attendee builds LAB 3/4.
                reporter.kafka_error(msg.error())
                continue
            topic = msg.topic()
            try:
                value = deserialize(msg.value(), SerializationContext(topic, MessageField.VALUE))
            except Exception as e:  # one bad record must not kill the tail
                reporter.deserialize_error(topic, e)
                continue
            if value is None:
                continue
            # The standings key contains race_id + car_number after migration;
            # only car_number is absent from the value and needs merging here.
            if topic == STANDINGS_TOPIC and "car_number" not in value:
                car_number = _decode_standings_key(deserialize, msg.key(), reporter)
                if car_number is None:
                    continue
                value["car_number"] = car_number
            routes[topic](value)
            reporter.clear()
    except Exception as exc:
        logger.error("[%s] race feed stopped: %s", feed.prefix, exc)
        feed.record_error("FEED_STOPPED", f"The race feed stopped: {exc}")
    finally:
        consumer.close()
