"""F1 Race Simulator — produces car telemetry to Kafka and race standings to MQ.

Simulates a 57-lap race at Silverstone in ~9.5 minutes of real time.
Two outputs:
  - Car telemetry (car #44 only) → Kafka topic 'car-telemetry' via confluent-kafka
  - Race standings (all 22 cars) → IBM MQ queue via pymqi
"""

import json
import logging
import os
import random
import time
from datetime import datetime, timezone

import pymqi
from confluent_kafka import Producer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import MessageField, SerializationContext

from datagen import config
from datagen.drivers import GRID
from datagen.race_script import RaceState
from datagen.telemetry import generate_telemetry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Optional deterministic mode: set RACE_SEED env var (any int) for reproducible
# telemetry noise/sensor values. Unset for live-race-style variability.
_seed = os.environ.get("RACE_SEED")
if _seed:
    random.seed(int(_seed))
    logger.info(f"Deterministic mode: random.seed({_seed})")


def _create_kafka_producer():
    """Create a Kafka producer with Avro serializer for car telemetry."""
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

    return producer, avro_serializer


def _connect_mq():
    """Connect to IBM MQ for race standings (pub/sub topic)."""
    conn_info = f"{config.MQ_HOST}({config.MQ_PORT})"
    qmgr = pymqi.connect(
        config.MQ_QUEUE_MANAGER,
        config.MQ_CHANNEL,
        conn_info,
        config.MQ_USER,
        config.MQ_PASSWORD,
    )
    topic = pymqi.Topic(
        qmgr, topic_string=config.MQ_TOPIC, open_opts=pymqi.CMQC.MQOO_OUTPUT | pymqi.CMQC.MQOO_FAIL_IF_QUIESCING
    )
    return qmgr, topic


def run_race():
    """Run the full 57-lap race simulation."""
    logger.info("=== F1 RACE SIMULATOR — SILVERSTONE ===")
    logger.info(f"Total laps: {config.TOTAL_LAPS}")
    logger.info(f"Seconds per lap: {config.SECONDS_PER_LAP}")
    logger.info(f"Our car: #{config.OUR_CAR_NUMBER}")

    # Initialize connections
    producer, avro_serializer = _create_kafka_producer()
    qmgr, mq_topic = _connect_mq()

    # RFH2 header template — marks messages as JMS TextMessage
    rfh2 = pymqi.RFH2()
    rfh2["Format"] = pymqi.CMQC.MQFMT_STRING
    rfh2["CodedCharSetId"] = 1208
    rfh2["NameValueCCSID"] = 1208
    rfh2.add_folder(b"<mcd><Msd>jms_text</Msd></mcd>")
    rfh2.add_folder(b"<jms><Dst>topic://dev/race-standings</Dst><Dlv>2</Dlv></jms>")

    # Initialize race state
    race = RaceState(GRID)

    # Track car 44's tire state for telemetry generation
    car44_tire_age = 0
    car44_tire_compound = "SOFT"
    car44_post_pit = False

    try:
        for lap in range(1, config.TOTAL_LAPS + 1):
            lap_start = time.time()

            # Advance race state (positions, tires, gaps)
            race.advance_lap()
            car44 = race.get_car(config.OUR_CAR_NUMBER)

            # Update tire tracking for telemetry
            car44_tire_age = car44["tire_age_laps"]
            car44_tire_compound = car44["tire_compound"]
            if car44["pit_stops"] > 0:
                car44_post_pit = True

            logger.info(
                f"Lap {lap:2d}/{config.TOTAL_LAPS} | "
                f"P{car44['position']:2d} | "
                f"{car44['tire_compound']:6s} age {car44['tire_age_laps']:2d} | "
                f"gap {car44['gap_to_leader_sec']:5.1f}s"
            )

            # Produce race standings to MQ (all 22 cars)
            standings = race.get_standings()
            for standing in standings:
                standing["event_time"] = int(datetime.now(timezone.utc).timestamp() * 1000)
                md = pymqi.MD()
                md.Format = pymqi.CMQC.MQFMT_RF_HEADER_2
                md.Encoding = 273
                md.CodedCharSetId = 1208
                pmo = pymqi.PMO()
                mq_topic.pub_rfh2(json.dumps(standing).encode("utf-8"), md, pmo, [rfh2])

            # Produce car telemetry to Kafka (multiple readings per lap)
            readings_per_lap = config.SECONDS_PER_LAP // config.TELEMETRY_INTERVAL_SEC
            for i in range(readings_per_lap):
                telemetry = generate_telemetry(
                    lap=lap,
                    tire_age=car44_tire_age,
                    tire_compound=car44_tire_compound,
                    post_pit=car44_post_pit,
                )
                telemetry["car_number"] = config.OUR_CAR_NUMBER
                telemetry["lap"] = lap
                telemetry["event_time"] = int(datetime.now(timezone.utc).timestamp() * 1000)

                producer.produce(
                    config.KAFKA_TOPIC,
                    key=str(config.OUR_CAR_NUMBER).encode("utf-8"),
                    value=avro_serializer(
                        telemetry,
                        SerializationContext(config.KAFKA_TOPIC, MessageField.VALUE),
                    ),
                )
                producer.poll(0)

                # Sleep between telemetry readings
                elapsed = time.time() - lap_start
                target = (i + 1) * config.TELEMETRY_INTERVAL_SEC
                sleep_time = max(0, target - elapsed)
                time.sleep(sleep_time)

            # Ensure all messages are delivered
            producer.flush(timeout=5)

            # Pace lap timing
            elapsed = time.time() - lap_start
            remaining = config.SECONDS_PER_LAP - elapsed
            if remaining > 0:
                time.sleep(remaining)

        logger.info("=== RACE COMPLETE ===")
        car44_final = race.get_car(config.OUR_CAR_NUMBER)
        logger.info(f"Car 44 final position: P{car44_final['position']}")

    finally:
        producer.flush()
        mq_topic.close()
        qmgr.disconnect()


if __name__ == "__main__":
    run_race()
