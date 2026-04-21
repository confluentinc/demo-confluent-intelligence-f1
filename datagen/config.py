"""Configuration for the F1 race simulator."""
import os

# Kafka settings (car-telemetry producer)
KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")
KAFKA_API_KEY = os.environ.get("KAFKA_API_KEY", "")
KAFKA_API_SECRET = os.environ.get("KAFKA_API_SECRET", "")
KAFKA_TOPIC = "car-telemetry"

# Schema Registry settings
SR_URL = os.environ.get("SR_URL", "")
SR_API_KEY = os.environ.get("SR_API_KEY", "")
SR_API_SECRET = os.environ.get("SR_API_SECRET", "")

# MQ settings (race-standings producer)
MQ_HOST = os.environ.get("MQ_HOST", "localhost")
MQ_PORT = int(os.environ.get("MQ_PORT", "1414"))
MQ_QUEUE_MANAGER = os.environ.get("MQ_QUEUE_MANAGER", "QM1")
MQ_CHANNEL = os.environ.get("MQ_CHANNEL", "DEV.ADMIN.SVRCONN")
MQ_TOPIC = os.environ.get("MQ_TOPIC", "dev/race-standings")
MQ_USER = os.environ.get("MQ_USER", "admin")
MQ_PASSWORD = os.environ.get("MQ_PASSWORD", "passw0rd")

# Race timing
TOTAL_LAPS = 57
SECONDS_PER_LAP = 10
TELEMETRY_INTERVAL_SEC = 2

# Our car
OUR_CAR_NUMBER = 44
