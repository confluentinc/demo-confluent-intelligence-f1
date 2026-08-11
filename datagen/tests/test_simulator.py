"""Tests for simulator Avro serialization and direct Kafka production."""

import json
import random
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from confluent_kafka.schema_registry.avro import AvroSerializer

from datagen import config
from datagen import simulator as sim


def test_create_kafka_producer_returns_sr_client_and_serializer():
    """_create_kafka_producer must return (Producer, SchemaRegistryClient, AvroSerializer)."""
    mock_sr_client = MagicMock()

    with patch.object(sim, "SchemaRegistryClient", return_value=mock_sr_client):
        with patch.object(sim, "AvroSerializer") as mock_avro_cls:
            mock_avro_cls.return_value = MagicMock(spec=AvroSerializer)
            with patch.object(sim, "Producer"):
                _producer, sr_client, serializer = sim._create_kafka_producer()

                mock_avro_cls.assert_called_once_with(
                    mock_sr_client,
                    schema_str=None,
                    conf={
                        "auto.register.schemas": False,
                        "use.latest.version": True,
                    },
                )
                assert sr_client is mock_sr_client
                assert serializer is mock_avro_cls.return_value


def test_sr_client_configured_with_basic_auth():
    """SchemaRegistryClient must use basic.auth.user.info from config."""
    with patch.object(sim, "config") as mock_config:
        mock_config.SR_URL = "https://psrc-test.us-east-1.aws.confluent.cloud"
        mock_config.SR_API_KEY = "test-key"
        mock_config.SR_API_SECRET = "test-secret"
        mock_config.KAFKA_BOOTSTRAP = "localhost:9092"
        mock_config.KAFKA_API_KEY = ""
        mock_config.KAFKA_API_SECRET = ""

        with patch.object(sim, "SchemaRegistryClient") as mock_sr_cls:
            with patch.object(sim, "AvroSerializer"):
                with patch.object(sim, "Producer"):
                    sim._create_kafka_producer()

                    mock_sr_cls.assert_called_once_with(
                        {
                            "url": "https://psrc-test.us-east-1.aws.confluent.cloud",
                            "basic.auth.user.info": "test-key:test-secret",
                        }
                    )


def test_event_time_is_epoch_millis():
    """event_time must be an int (epoch milliseconds), not an ISO string."""
    from datagen.telemetry import generate_telemetry

    telemetry = generate_telemetry(lap=1, tire_age=1, tire_compound="SOFT", post_pit=False)
    telemetry["car_number"] = 44
    telemetry["lap"] = 1
    telemetry["event_time"] = sim._now_millis()

    assert isinstance(telemetry["event_time"], int)
    assert telemetry["event_time"] > 1_000_000_000_000


def test_produce_standings_emits_one_record_per_car_to_standings_topic():
    """All standings are produced to the race_standings topic with an event_time."""
    producer = MagicMock()
    avro_serializer = MagicMock(return_value=b"value-bytes")
    key_fn = MagicMock(side_effect=lambda race_id, cn: f"key-{race_id}-{cn}".encode())
    standings = [{"car_number": 44}, {"car_number": 1}]

    sim._produce_standings(producer, avro_serializer, key_fn, standings, "race-1")

    assert producer.produce.call_count == 2
    for call in producer.produce.call_args_list:
        assert call.args[0] == config.STANDINGS_TOPIC
    # Every standing gets an epoch-millis event_time stamped in.
    assert all(isinstance(s["event_time"], int) for s in standings)
    assert all(s["race_id"] == "race-1" for s in standings)
    key_fn.assert_any_call("race-1", 44)
    key_fn.assert_any_call("race-1", 1)


def test_standings_key_fn_rejects_legacy_primitive_schema():
    """A new simulator must never run against the pre-race_id key schema."""
    sr_client = MagicMock()
    sr_client.get_latest_version.return_value.schema.schema_str = json.dumps("int")
    avro_serializer = MagicMock()

    with pytest.raises(RuntimeError, match="controlled race_standings DROP/CREATE") as exc:
        sim._build_standings_key_fn(sr_client, avro_serializer)

    assert "race_id and car_number" in str(exc.value)
    avro_serializer.assert_not_called()


def test_standings_key_fn_rejects_record_missing_race_id():
    sr_client = MagicMock()
    sr_client.get_latest_version.return_value.schema.schema_str = json.dumps(
        {"type": "record", "name": "Key", "fields": [{"name": "car_number", "type": "int"}]}
    )

    with pytest.raises(RuntimeError, match=r"fields \['car_number'\]"):
        sim._build_standings_key_fn(sr_client, MagicMock())


def test_standings_key_fn_handles_composite_record_schema():
    """The upsert key contains both race identity and car identity."""
    sr_client = MagicMock()
    sr_client.get_latest_version.return_value.schema.schema_str = json.dumps(
        {
            "type": "record",
            "name": "Key",
            "fields": [
                {"name": "race_id", "type": "string"},
                {"name": "car_number", "type": "int"},
            ],
        }
    )
    avro_serializer = MagicMock()

    key_fn = sim._build_standings_key_fn(sr_client, avro_serializer)
    key_fn("race-1", 44)

    payload = avro_serializer.call_args.args[0]
    assert payload == {"race_id": "race-1", "car_number": 44}


def test_standings_key_fn_accepts_optional_additional_fields():
    sr_client = MagicMock()
    sr_client.get_latest_version.return_value.schema.schema_str = json.dumps(
        {
            "type": "record",
            "name": "Key",
            "fields": [
                {"name": "race_id", "type": "string"},
                {"name": "car_number", "type": "int"},
                {"name": "source", "type": ["null", "string"], "default": None},
            ],
        }
    )
    avro_serializer = MagicMock()

    key_fn = sim._build_standings_key_fn(sr_client, avro_serializer)
    key_fn("race-1", 44)

    assert avro_serializer.call_args.args[0] == {"race_id": "race-1", "car_number": 44}


def test_seed_random_resets_each_race(monkeypatch):
    monkeypatch.setattr(config, "RACE_SEED", 42)
    sim._seed_random()
    first = [random.random() for _ in range(5)]
    sim._seed_random()
    assert [random.random() for _ in range(5)] == first


def test_race_ids_are_sortable_by_utc_start_and_have_unique_suffixes():
    earlier = sim._new_race_id(datetime(2026, 8, 11, 1, tzinfo=timezone.utc), "aaaaaaaa")
    later = sim._new_race_id(datetime(2026, 8, 11, 2, tzinfo=timezone.utc), "bbbbbbbb")
    assert earlier == "20260811T010000000000Z-aaaaaaaa"
    assert earlier < later
    assert sim._new_race_id() != sim._new_race_id()
