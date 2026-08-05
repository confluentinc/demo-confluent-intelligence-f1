"""Warm-up status is explicit about the time before the race begins."""

from unittest.mock import Mock, patch

from datagen import simulator


def test_warmup_log_reports_production_time_wait_and_total_cadence(caplog) -> None:
    producer = Mock()

    with (
        patch.object(simulator.config, "PRE_RACE_WARMUP_LAPS", 1),
        patch.object(simulator.config, "SECONDS_PER_LAP", 20),
        patch.object(simulator.config, "TELEMETRY_INTERVAL_SEC", 10),
        patch.object(simulator.config, "PRE_RACE_LAP_DELAY_SEC", 15),
        patch.object(simulator, "generate_telemetry", return_value={}),
        patch.object(simulator, "_now_millis", return_value=0),
        patch.object(simulator, "_produce_telemetry"),
        patch.object(simulator.time, "sleep"),
    ):
        with caplog.at_level("INFO", logger=simulator.__name__):
            simulator._run_warmup_laps(producer, Mock())

    assert producer.flush.call_args.kwargs == {"timeout": 5}
    assert "produced over 20s (2 telemetry readings); waiting 15s before the next window (35s cadence)." in caplog.text
