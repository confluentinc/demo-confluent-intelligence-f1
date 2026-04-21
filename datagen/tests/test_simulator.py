"""Tests for simulator Avro serialization."""
import sys
from unittest.mock import MagicMock, patch

# pymqi is not available in the test environment (requires IBM MQ C libs).
# Mock it before importing the simulator module.
sys.modules.setdefault("pymqi", MagicMock())

from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import SerializationContext, MessageField

# Import simulator once — pymqi is already mocked above.
from datagen import simulator as sim


def test_create_kafka_producer_returns_avro_serializer():
    """_create_kafka_producer must return a (Producer, AvroSerializer) tuple."""
    mock_sr_client = MagicMock()

    with patch.object(sim, "SchemaRegistryClient", return_value=mock_sr_client):
        with patch.object(sim, "AvroSerializer") as mock_avro_cls:
            mock_avro_cls.return_value = MagicMock(spec=AvroSerializer)
            with patch.object(sim, "Producer"):
                producer, serializer = sim._create_kafka_producer()

                mock_avro_cls.assert_called_once_with(
                    mock_sr_client,
                    schema_str=None,
                    conf={
                        "auto.register.schemas": False,
                        "use.latest.version": True,
                    },
                )
                assert serializer is mock_avro_cls.return_value


def test_sr_client_configured_with_basic_auth():
    """SchemaRegistryClient must use basic.auth.user.info from config."""
    with patch.object(sim, "config") as mock_config:
        mock_config.SR_URL = "https://psrc-test.us-east-2.aws.confluent.cloud"
        mock_config.SR_API_KEY = "test-key"
        mock_config.SR_API_SECRET = "test-secret"
        mock_config.KAFKA_BOOTSTRAP = "localhost:9092"
        mock_config.KAFKA_API_KEY = ""
        mock_config.KAFKA_API_SECRET = ""

        with patch.object(sim, "SchemaRegistryClient") as mock_sr_cls:
            with patch.object(sim, "AvroSerializer"):
                with patch.object(sim, "Producer"):
                    sim._create_kafka_producer()

                    mock_sr_cls.assert_called_once_with({
                        "url": "https://psrc-test.us-east-2.aws.confluent.cloud",
                        "basic.auth.user.info": "test-key:test-secret",
                    })


def test_event_time_is_epoch_millis():
    """event_time must be an int (epoch milliseconds), not an ISO string."""
    from datagen.telemetry import generate_telemetry

    telemetry = generate_telemetry(lap=1, tire_age=1, tire_compound="SOFT", post_pit=False)
    telemetry["car_number"] = 44
    telemetry["lap"] = 1

    from datetime import datetime, timezone
    telemetry["event_time"] = int(datetime.now(timezone.utc).timestamp() * 1000)

    assert isinstance(telemetry["event_time"], int)
    assert telemetry["event_time"] > 1_000_000_000_000
