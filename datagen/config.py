"""Configuration for the F1 race simulator."""

import os

# Kafka settings — both car_telemetry and race_standings are produced directly
# to Confluent Cloud (Avro via Schema Registry). There is no IBM MQ hop anymore.
KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")
KAFKA_API_KEY = os.environ.get("KAFKA_API_KEY", "")
KAFKA_API_SECRET = os.environ.get("KAFKA_API_SECRET", "")
KAFKA_TOPIC = "car_telemetry"
# Race standings for all 22 cars, keyed by car_number (upsert table in Flink).
STANDINGS_TOPIC = os.environ.get("STANDINGS_TOPIC", "race_standings")

# Schema Registry settings
SR_URL = os.environ.get("SR_URL", "")
SR_API_KEY = os.environ.get("SR_API_KEY", "")
SR_API_SECRET = os.environ.get("SR_API_SECRET", "")

# Race timing — 60 laps at 60 seconds/lap == a 60-minute race (one lap per minute).
TOTAL_LAPS = 60
SECONDS_PER_LAP = int(os.environ.get("SECONDS_PER_LAP", "60"))
TELEMETRY_INTERVAL_SEC = 2

# Workshop lifecycle: when RACE_LOOP=true the simulator replays the race
# back-to-back (a fresh grid each time) so attendees always have a live feed,
# sleeping RESTART_DELAY_SEC between races. Set RACE_LOOP=false for a single run.
RACE_LOOP = os.environ.get("RACE_LOOP", "false").lower() == "true"
RESTART_DELAY_SEC = int(os.environ.get("RESTART_DELAY_SEC", "30"))

# Pre-race warm-up: number of dummy windows (lap=0) to produce before lap 1.
# ML_DETECT_ANOMALIES withholds output until ~5 data points are available, so
# 4 warm-up windows ensure the function emits starting at real lap 1.
PRE_RACE_WARMUP_LAPS = int(os.environ.get("PRE_RACE_WARMUP_LAPS", "4"))
# Sleep between warm-up windows (seconds) to allow pipeline propagation.
PRE_RACE_LAP_DELAY_SEC = int(os.environ.get("PRE_RACE_LAP_DELAY_SEC", "15"))

# Our car
OUR_CAR_NUMBER = 88
