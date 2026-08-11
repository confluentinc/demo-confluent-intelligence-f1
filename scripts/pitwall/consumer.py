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

That suppression used to be a bare ``except``-everything: *all* poll errors went
to ``logger.debug``, which ``app.py``'s ``basicConfig(INFO)`` then discarded. A
stale credential card therefore produced a dashboard that loaded, rendered empty,
reported ``live: false`` and never said why. ``ConsumerErrorReporter`` keeps the
UNKNOWN_TOPIC quiet (the reason the code exists) while surfacing everything else:
once per distinct error code to the terminal, and into ``RaceState`` so the
running page and ``/healthz`` carry the cause too.
"""

from __future__ import annotations

import logging
import uuid

from confluent_kafka import OFFSET_BEGINNING, OFFSET_END, Consumer, KafkaError
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

# Remediation for a card that is missing a key, or an environment that rejects it.
# The canonical home for card guidance is scripts/common/credentials.py; this
# module can't import it without a cycle through the card loader, and FlinkSession
# in scripts/workshop/sql_shell.py carries the same text for the same reason.
# Phrased conditionally on purpose: FlinkSession is also built straight from
# Terraform outputs (scripts/common/simulator_control.py), where no card exists.
CARD_REMEDIATION = (
    "If this came from a credential card, recreate it the way you made it:\n"
    "    uv run deploy          standalone deploy (AWS + Confluent)\n"
    "    uv run selfservice up  solo, Confluent-only\n"
    "    uv run f1-onboard      workshop attendee, from your claim email\n"
    "    uv run workshop creds  organizer, from wsa's build-output.csv"
)

# Expected in normal operation — these must stay quiet. car_state and
# pit_decisions genuinely do not exist until the attendee writes LAB 3 / LAB 4,
# and the consumer picks them up on a later metadata refresh with no help from us.
BENIGN_ERROR_CODES = frozenset(
    {
        KafkaError.UNKNOWN_TOPIC_OR_PART,
        KafkaError._UNKNOWN_TOPIC,
        KafkaError._UNKNOWN_PARTITION,
        KafkaError._PARTITION_EOF,
    }
)

# What to tell the user per failure category. Anything not listed still warns —
# an unrecognized error is exactly what must not be swallowed a second time.
ERROR_HINTS = {
    KafkaError._AUTHENTICATION: f"Kafka rejected the API key/secret. {CARD_REMEDIATION}",
    KafkaError.SASL_AUTHENTICATION_FAILED: f"Kafka rejected the API key/secret. {CARD_REMEDIATION}",
    KafkaError.TOPIC_AUTHORIZATION_FAILED: "The API key exists but is not authorized for this topic — "
    "check it belongs to this cluster.",
    KafkaError.GROUP_AUTHORIZATION_FAILED: "The API key is not authorized to join a consumer group on this cluster.",
    KafkaError.CLUSTER_AUTHORIZATION_FAILED: "The API key is not authorized on this cluster — "
    "it may belong to a torn-down environment.",
    KafkaError._RESOLVE: "Could not resolve the bootstrap host — check F1_KAFKA_BOOTSTRAP on your card.",
    # Deliberately hedged: this same code covers a routine idle disconnect (which
    # librdkafka reconnects from on its own) and a cluster that no longer exists.
    KafkaError._TRANSPORT: "Lost the broker connection. Harmless if the board keeps updating; "
    "if it stays empty, check the network and that the cluster still exists.",
    KafkaError._ALL_BROKERS_DOWN: "No broker is reachable — the cluster may be deleted, or the network is blocked.",
}


class ConsumerErrorReporter:
    """Classify feed errors: suppress the expected, warn once for the rest.

    Warn-*once* matters because this sits in a 0.5s poll loop — librdkafka
    re-emits an auth failure on every reconnect attempt, so an unguarded
    ``logger.warning`` would bury the terminal in the same line. Keyed by error
    code (not by message text, which carries a changing broker name), so each
    distinct cause still gets said exactly once.
    """

    def __init__(self, state: RaceState | None = None, label: str = "") -> None:
        self.state = state
        self.label = f"[{label}] " if label else ""
        self.warned: set[object] = set()

    def _publish(self, code: str, detail: str) -> None:
        if self.state is not None:
            self.state.record_error(code, detail)

    def kafka_error(self, err: KafkaError) -> bool:
        """Report a poll() error event. Returns True if it was suppressed as benign."""
        code = err.code()
        if code in BENIGN_ERROR_CODES:
            logger.debug("%skafka: %s", self.label, err)
            return True

        detail = err.str() or str(err)
        hint = ERROR_HINTS.get(code)
        self._publish(err.name(), f"{detail}\n{hint}" if hint else detail)
        if code not in self.warned:
            self.warned.add(code)
            logger.warning("%sKafka %s: %s", self.label, err.name(), detail)
            if hint:
                logger.warning("%s  %s", self.label, hint)
        return False

    def deserialize_error(self, topic: str, exc: Exception, field: str = "value") -> None:
        """Report an Avro decode failure. One bad record must not kill the tail."""
        key = (topic, field)
        detail = f"Could not decode a {topic} {field}: {exc}"
        self._publish("DESERIALIZATION_FAILED", f"{detail}\nCheck the Schema Registry keys on your card.")
        if key not in self.warned:
            self.warned.add(key)
            logger.warning("%s%s — check the Schema Registry keys on your card.", self.label, detail)

    def clear(self) -> None:
        """A record arrived — retract the published error (see RaceState.clear_error)."""
        if self.state is not None:
            self.state.clear_error()


def _bootstrap(raw: str) -> str:
    """Strip the ``SASL_SSL://`` scheme the credential card carries."""
    return raw.split("://", 1)[-1] if "://" in raw else raw


def _build_consumer(creds: dict[str, str], error_cb=None) -> Consumer:
    """Build the tailing consumer. ``error_cb`` receives client-level failures.

    ``error_cb`` is optional and omitted from the config entirely when not given,
    so callers that don't pass one (the social feed's consumer) get exactly the
    configuration they always had.

    Passing one is what makes a broken connection *visible*: librdkafka delivers
    resolve/connect/authentication failures to ``error_cb``, **not** as ``poll()``
    error events. Measured against an unresolvable bootstrap host: 0 events from
    ``poll()`` in 15s with no callback, 17 ``_RESOLVE``/``_ALL_BROKERS_DOWN``
    callbacks with one. ``log_level: 0`` silences librdkafka's own log, so without
    the callback there is no channel left and the failure is simply invisible.
    """
    try:
        conf = {
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
    except KeyError as e:
        raise SystemExit(f"Credential file is missing {e}.\n  {CARD_REMEDIATION}") from e
    if error_cb is not None:
        conf["error_cb"] = error_cb
    return Consumer(conf)


def _build_deserializer(creds: dict[str, str]) -> AvroDeserializer:
    try:
        sr = SchemaRegistryClient(
            {
                "url": creds["F1_SCHEMA_REGISTRY_URL"],
                "basic.auth.user.info": f"{creds['F1_SR_API_KEY']}:{creds['F1_SR_API_SECRET']}",
            }
        )
    except KeyError as e:
        raise SystemExit(f"Credential file is missing {e}.\n  {CARD_REMEDIATION}") from e
    # schema_str=None: the writer schema is resolved per-message from the embedded
    # schema id, so one deserializer serves every Avro value topic.
    return AvroDeserializer(sr, schema_str=None)


def _decode_standings_key(
    deserialize: AvroDeserializer,
    raw_key: bytes | None,
    reporter: ConsumerErrorReporter | None = None,
) -> int | None:
    """Pull car_number out of a race_standings Avro message key.

    Confluent Cloud Flink registers the single-column PRIMARY KEY either as a
    record wrapping car_number ({"car_number": 88}) or as a bare primitive int,
    so we accept both shapes (mirroring the simulator's key encoding).

    ``reporter`` is optional so callers that don't classify errors (the social
    feed's own consumer) keep the original two-argument call.
    """
    if raw_key is None:
        return None
    try:
        key = deserialize(raw_key, SerializationContext(STANDINGS_TOPIC, MessageField.KEY))
    except Exception as e:  # a bad key must not kill the tail
        if reporter is not None:
            reporter.deserialize_error(STANDINGS_TOPIC, e, field="key")
        else:
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
    reporter = ConsumerErrorReporter(state)
    try:
        # error_cb, not just msg.error(): the client-level failures that matter most
        # (bad API key, unresolvable or unreachable broker) are delivered *only* to
        # the callback — see _build_consumer. Both paths funnel into the same
        # reporter, so warn-once dedup is shared between them.
        consumer = _build_consumer(creds, error_cb=reporter.kafka_error)
        deserialize = _build_deserializer(creds)
    except SystemExit as e:
        # This runs on a daemon thread, and threading.excepthook discards
        # SystemExit *silently* — letting it propagate would reproduce the exact
        # failure this module exists to fix: a card missing F1_KAFKA_API_KEY
        # would leave an empty dashboard and not one word about why.
        logger.error("%s", e)
        state.record_error("CARD_INCOMPLETE", str(e))
        return
    except Exception as e:  # a bad SR URL, a malformed bootstrap, ...
        logger.error("Could not start the race feed: %s", e)
        state.record_error("FEED_START_FAILED", f"Could not start the race feed: {e}")
        return
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
                # UNKNOWN_TOPIC stays quiet until the attendee builds LAB 3/4;
                # auth, unreachable-broker and authorization failures do not.
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
            # race_standings is a Flink upsert table keyed by car_number, so
            # car_number lives in the Avro *key* (subject race_standings-key) and
            # is absent from the value. Decode the key and merge it back in, or
            # update_standing() drops every record for want of a car_number.
            if topic == STANDINGS_TOPIC and "car_number" not in value:
                car_number = _decode_standings_key(deserialize, msg.key(), reporter)
                if car_number is None:
                    continue
                value["car_number"] = car_number
            routes[topic](value)
            reporter.clear()
    except Exception as e:
        # Same reasoning as the setup guard: a fatal client error raised out of
        # poll() would otherwise print a bare traceback (or nothing) from a daemon
        # thread while the dashboard kept serving a frozen board.
        logger.error("Race feed stopped: %s", e)
        state.record_error("FEED_STOPPED", f"The race feed stopped: {e}")
    finally:
        consumer.close()
