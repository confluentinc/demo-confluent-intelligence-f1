"""Tests for simulator Avro serialization and direct Kafka production."""

import json
from unittest.mock import MagicMock, patch

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


def test_anomaly_lap_publishes_pre_stop_soft_state_then_mediums_next_lap():
    """The lap-24 anomaly is evaluated before the scheduled stop is exposed."""
    before_stop = {
        "lap": 23,
        "tire_compound": "SOFT",
        "tire_age_laps": 23,
        "pit_stops": 0,
        "in_pit_lane": False,
    }
    after_stop = {
        "lap": 24,
        "tire_compound": "MEDIUM",
        "tire_age_laps": 1,
        "pit_stops": 1,
        "in_pit_lane": False,
    }

    reported, post_pit = sim._source_state_for_lap(before_stop, after_stop, 24)

    assert reported["lap"] == 24
    assert reported["tire_compound"] == "SOFT"
    assert reported["tire_age_laps"] == 24
    assert reported["pit_stops"] == 0
    assert post_pit is False

    reported, post_pit = sim._source_state_for_lap(before_stop, after_stop, 25)
    assert reported is after_stop
    assert reported["tire_compound"] == "MEDIUM"
    assert reported["pit_stops"] == 1
    assert post_pit is True


def test_produce_standings_emits_one_record_per_car_to_standings_topic():
    """All standings are produced to the race_standings topic with an event_time."""
    producer = MagicMock()
    avro_serializer = MagicMock(return_value=b"value-bytes")
    key_fn = MagicMock(side_effect=lambda cn: f"key-{cn}".encode())
    standings = [{"car_number": 44}, {"car_number": 1}]

    sim._produce_standings(producer, avro_serializer, key_fn, standings)

    assert producer.produce.call_count == 2
    for call in producer.produce.call_args_list:
        assert call.args[0] == config.STANDINGS_TOPIC
    # Every standing gets an epoch-millis event_time stamped in.
    assert all(isinstance(s["event_time"], int) for s in standings)
    key_fn.assert_any_call(44)
    key_fn.assert_any_call(1)


def test_standings_key_fn_handles_primitive_schema():
    """A primitive int key schema yields the raw car_number as payload."""
    sr_client = MagicMock()
    sr_client.get_latest_version.return_value.schema.schema_str = json.dumps("int")
    avro_serializer = MagicMock()

    key_fn = sim._build_standings_key_fn(sr_client, avro_serializer)
    key_fn(44)

    payload = avro_serializer.call_args.args[0]
    assert payload == 44


def test_standings_key_fn_handles_record_schema():
    """A record key schema wraps the car_number in its first field."""
    sr_client = MagicMock()
    sr_client.get_latest_version.return_value.schema.schema_str = json.dumps(
        {"type": "record", "name": "Key", "fields": [{"name": "car_number", "type": "int"}]}
    )
    avro_serializer = MagicMock()

    key_fn = sim._build_standings_key_fn(sr_client, avro_serializer)
    key_fn(44)

    payload = avro_serializer.call_args.args[0]
    assert payload == {"car_number": 44}


def test_next_epoch_boundary_on_boundary_returns_itself():
    """An exact multiple of seconds_per_lap is already a boundary."""
    assert sim._next_epoch_boundary(1000.0, 20) == 1000.0


def test_next_epoch_boundary_rounds_up_mid_window():
    """A mid-window timestamp rounds up to the next boundary."""
    assert sim._next_epoch_boundary(1003.2, 20) == 1020.0
    assert sim._next_epoch_boundary(1019.999, 20) == 1020.0


def test_lap_deadline_has_constant_spacing_with_no_drift():
    """Consecutive lap deadlines are always exactly seconds_per_lap apart."""
    race_start = 1000.0
    spl = 20
    for n in range(2, 61):
        assert sim._lap_deadline(race_start, n, spl) - sim._lap_deadline(race_start, n - 1, spl) == 20


def test_lap_deadline_first_and_last_lap():
    """Lap 1 starts at race_start; lap 60 starts 59 laps later."""
    race_start = 1000.0
    spl = 20
    assert sim._lap_deadline(race_start, 1, spl) == 1000.0
    assert sim._lap_deadline(race_start, 60, spl) == 1000.0 + 59 * 20
