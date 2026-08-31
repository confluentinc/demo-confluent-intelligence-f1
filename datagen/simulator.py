"""F1 Race Simulator — produces car telemetry and race standings to Kafka.

Simulates a 60-lap race at Silverstone in ~20 minutes of real time (three laps
per minute at the default SECONDS_PER_LAP=20).
Two Kafka outputs (both Avro via Schema Registry, produced directly to
Confluent Cloud — there is no IBM MQ hop):
  - Car telemetry (car #88 only) → topic 'car_telemetry'
  - Race standings (all 22 cars)  → topic 'race_standings', keyed by car_number

The race_standings topic backs a Flink upsert table (PRIMARY KEY car_number),
so each record's key is Avro-encoded against the registered '<topic>-key'
subject and the value against '<topic>-value'.
"""

import json
import logging
import math
import os
import random
import time
from datetime import datetime, timezone

from confluent_kafka import Producer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import MessageField, SerializationContext

from datagen import config
from datagen.drivers import GRID
from datagen.race_script import RaceState
from datagen.telemetry import ANOMALY_LAP, generate_telemetry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _seed_random():
    """Apply RACE_SEED for reproducible telemetry/lap noise, if set.

    Called at the start of every race so that looped races (RACE_LOOP=true)
    stay reproducible instead of drifting after the first iteration.
    """
    seed = os.environ.get("RACE_SEED")
    if seed:
        random.seed(int(seed))
        logger.info(f"Deterministic mode: random.seed({seed})")


def _now_millis():
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _next_epoch_boundary(now: float, seconds_per_lap: int) -> float:
    """Round `now` up to the next SECONDS_PER_LAP wall-clock epoch boundary."""
    return math.ceil(now / seconds_per_lap) * seconds_per_lap


def _lap_deadline(race_start: float, lap: int, seconds_per_lap: int) -> float:
    """Absolute wall-clock start time for `lap` (1-indexed) = race_start + (lap-1)*spl."""
    return race_start + (lap - 1) * seconds_per_lap


def _build_standings_key_fn(sr_client, avro_serializer):
    """Return a function that Avro-encodes a race_standings message key.

    Confluent Cloud Flink registers the key schema for a single-column
    PRIMARY KEY either as a primitive ("int") or as a record wrapping the
    key column. We inspect the registered '<topic>-key' schema once and
    build the right payload shape for either case.
    """
    ctx = SerializationContext(config.STANDINGS_TOPIC, MessageField.KEY)
    latest = sr_client.get_latest_version(f"{config.STANDINGS_TOPIC}-key")
    parsed = json.loads(latest.schema.schema_str)
    is_record = isinstance(parsed, dict) and parsed.get("type") == "record"
    field_name = parsed["fields"][0]["name"] if is_record else None

    def key_fn(car_number):
        payload = {field_name: car_number} if is_record else car_number
        return avro_serializer(payload, ctx)

    return key_fn


def _create_kafka_producer():
    """Create the Kafka producer plus the Avro serializer used for all topics.

    A single AvroSerializer instance serves car_telemetry-value,
    race_standings-value and race_standings-key — the subject is resolved
    per-call from the SerializationContext (auto.register disabled,
    use.latest.version enabled), so the latest registered schema is fetched
    for whichever subject the context names.
    """
    sr_client = SchemaRegistryClient(
        {
            "url": config.SR_URL,
            "basic.auth.user.info": f"{config.SR_API_KEY}:{config.SR_API_SECRET}",
        }
    )

    avro_serializer = AvroSerializer(
        sr_client,
        schema_str=None,
        conf={
            "auto.register.schemas": False,
            "use.latest.version": True,
        },
    )

    producer = Producer(
        {
            "bootstrap.servers": config.KAFKA_BOOTSTRAP,
            "security.protocol": "SASL_SSL",
            "sasl.mechanisms": "PLAIN",
            "sasl.username": config.KAFKA_API_KEY,
            "sasl.password": config.KAFKA_API_SECRET,
        }
    )

    return producer, sr_client, avro_serializer


def _produce_telemetry(producer, avro_serializer, telemetry):
    """Produce one car_telemetry reading (car #88) to Kafka."""
    producer.produce(
        config.KAFKA_TOPIC,
        key=str(config.OUR_CAR_NUMBER).encode("utf-8"),
        value=avro_serializer(
            telemetry,
            SerializationContext(config.KAFKA_TOPIC, MessageField.VALUE),
        ),
    )


def _produce_standings(producer, avro_serializer, key_fn, standings):
    """Produce a race_standings record for every car, keyed by car_number."""
    value_ctx = SerializationContext(config.STANDINGS_TOPIC, MessageField.VALUE)
    ts = _now_millis()
    for standing in standings:
        standing["event_time"] = ts
        producer.produce(
            config.STANDINGS_TOPIC,
            key=key_fn(standing["car_number"]),
            value=avro_serializer(standing, value_ctx),
        )
    producer.poll(0)


def _source_state_for_lap(car88_before, car88, lap):
    """Return the state the source should publish for car #88 on this lap.

    The race engine applies the scheduled lap-24 stop while advancing its
    internal state. Publish the pre-stop SOFT snapshot for that one lap so the
    anomaly is visible to Flink and triggers the pit call; the next lap exposes
    the post-stop MEDIUM state.
    """
    if lap != ANOMALY_LAP:
        return car88, car88["pit_stops"] > 0

    source = car88_before.copy()
    source["lap"] = lap
    source["tire_age_laps"] += 1
    source["in_pit_lane"] = False
    return source, False


def _run_warmup_laps(producer, avro_serializer):
    """Produce pre-race telemetry windows (lap=0) as a producer/schema smoke test.

    These do **not** prime the anomaly function, despite the name. It withholds
    output for its first 12 windows, and none of these rows ever reach it: only
    telemetry is produced here, so there is no `race_standings` version to match
    at these timestamps and LAB 3's *inner* temporal join drops every warmup row
    before the window aggregation (the closing `lap > 0` filter is redundant for
    them). What they do buy is confirmation that the producer and its Avro
    schemas work before lap 1. See "Anomaly warmup" in CLAUDE.md.
    """
    for i in range(config.PRE_RACE_WARMUP_LAPS):
        readings_per_lap = config.SECONDS_PER_LAP // config.TELEMETRY_INTERVAL_SEC
        for _ in range(readings_per_lap):
            telemetry = generate_telemetry(lap=0, tire_age=0, tire_compound="SOFT", post_pit=False)
            telemetry["car_number"] = config.OUR_CAR_NUMBER
            telemetry["lap"] = 0
            telemetry["event_time"] = _now_millis()
            _produce_telemetry(producer, avro_serializer, telemetry)
            producer.poll(0)
            time.sleep(config.TELEMETRY_INTERVAL_SEC)

        producer.flush(timeout=5)
        logger.info(
            f"Warmup window {i + 1}/{config.PRE_RACE_WARMUP_LAPS} produced over "
            f"{config.SECONDS_PER_LAP}s ({readings_per_lap} telemetry readings); "
            f"waiting {config.PRE_RACE_LAP_DELAY_SEC}s before the next window "
            f"({config.SECONDS_PER_LAP + config.PRE_RACE_LAP_DELAY_SEC}s cadence)."
        )
        time.sleep(config.PRE_RACE_LAP_DELAY_SEC)

    logger.info("Warm-up complete. Starting race at lap 1.")


def run_race():
    """Run a single full 60-lap race simulation."""
    logger.info("=== F1 RACE SIMULATOR — SILVERSTONE ===")
    logger.info(f"Total laps: {config.TOTAL_LAPS}")
    logger.info(f"Seconds per lap: {config.SECONDS_PER_LAP}")
    logger.info(f"Our car: #{config.OUR_CAR_NUMBER}")

    _seed_random()

    # Initialize Kafka producer + serializers
    producer, sr_client, avro_serializer = _create_kafka_producer()
    standings_key_fn = _build_standings_key_fn(sr_client, avro_serializer)

    # Initialize race state
    race = RaceState(GRID)

    # Track car 88's tire state for telemetry generation.
    car88_tire_age = 0
    car88_tire_compound = "SOFT"
    car88_post_pit = False

    try:
        _run_warmup_laps(producer, avro_serializer)

        race_start = _next_epoch_boundary(time.time(), config.SECONDS_PER_LAP)
        wait = race_start - time.time()
        if wait > 0:
            logger.info(f"Phase-locking to 20s epoch boundary; waiting {wait:.2f}s before lap 1")
            time.sleep(wait)

        for lap in range(1, config.TOTAL_LAPS + 1):
            lap_start = _lap_deadline(race_start, lap, config.SECONDS_PER_LAP)
            behind = time.time() - lap_start
            if behind > 0.5:
                logger.warning(
                    f"Lap {lap} started {behind:.2f}s behind its scheduled boundary"
                )

            # Preserve the pre-stop snapshot for the scheduled pit lap. The simulator
            # applies the hard-coded stop while advancing its internal race state, but
            # the source stream must first expose the anomaly that triggers the call.
            car88_before = race.get_car(config.OUR_CAR_NUMBER).copy()

            # Advance race state (positions, tires, gaps)
            race.advance_lap()
            car88 = race.get_car(config.OUR_CAR_NUMBER)

            car88_source, car88_post_pit = _source_state_for_lap(car88_before, car88, lap)
            car88_tire_age = car88_source["tire_age_laps"]
            car88_tire_compound = car88_source["tire_compound"]

            logger.info(
                f"Lap {lap:2d}/{config.TOTAL_LAPS} | "
                f"P{car88['position']:2d} | "
                f"{car88['tire_compound']:6s} age {car88['tire_age_laps']:2d} | "
                f"gap {car88['gap_to_leader_sec']:5.1f}s"
            )

            # Produce race standings to Kafka (all 22 cars, keyed by car_number)
            standings = race.get_standings()
            if lap == ANOMALY_LAP:
                standings = [
                    car88_source if standing["car_number"] == config.OUR_CAR_NUMBER else standing
                    for standing in standings
                ]
            _produce_standings(producer, avro_serializer, standings_key_fn, standings)

            # Produce car telemetry to Kafka (multiple readings per lap)
            readings_per_lap = config.SECONDS_PER_LAP // config.TELEMETRY_INTERVAL_SEC
            for i in range(readings_per_lap):
                telemetry = generate_telemetry(
                    lap=lap,
                    tire_age=car88_tire_age,
                    tire_compound=car88_tire_compound,
                    post_pit=car88_post_pit,
                )
                telemetry["car_number"] = config.OUR_CAR_NUMBER
                telemetry["lap"] = lap
                telemetry["event_time"] = _now_millis()

                _produce_telemetry(producer, avro_serializer, telemetry)
                producer.poll(0)

                # Sleep between telemetry readings
                elapsed = time.time() - lap_start
                target = (i + 1) * config.TELEMETRY_INTERVAL_SEC
                sleep_time = max(0, target - elapsed)
                time.sleep(sleep_time)

            # Ensure all messages are delivered
            producer.flush(timeout=5)

            # Pace to the next lap's absolute epoch deadline (no cumulative drift)
            remaining = _lap_deadline(race_start, lap + 1, config.SECONDS_PER_LAP) - time.time()
            if remaining > 0:
                time.sleep(remaining)

        logger.info("=== RACE COMPLETE ===")
        car88_final = race.get_car(config.OUR_CAR_NUMBER)
        logger.info(f"Car 88 final position: P{car88_final['position']}")

    finally:
        producer.flush()


def main():
    """Run one race, or loop races back-to-back when RACE_LOOP=true."""
    if not config.RACE_LOOP:
        run_race()
        return

    logger.info(f"RACE_LOOP enabled — replaying races with {config.RESTART_DELAY_SEC}s between each.")
    while True:
        run_race()
        logger.info(f"Race finished. Restarting in {config.RESTART_DELAY_SEC}s...")
        time.sleep(config.RESTART_DELAY_SEC)


if __name__ == "__main__":
    main()
