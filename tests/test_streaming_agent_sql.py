from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_car_state_uses_one_30_second_window_per_race_lap():
    for relative_path in (
        "demo-reference/enrichment_anomaly.sql",
        "demo-reference/enrichment_anomaly_ai.sql",
        "README.md",
    ):
        sql = (ROOT / relative_path).read_text()
        assert "TUMBLE(TABLE enriched, DESCRIPTOR(event_time), INTERVAL '30' SECOND)" in sql


def test_forecast_uses_the_same_one_per_lap_window():
    forecast = (ROOT / "demo-reference/granite_tire_forecast.sql").read_text()
    walkthrough = (ROOT / "README.md").read_text()

    window = "TUMBLE(TABLE `car_telemetry`, DESCRIPTOR(event_time), INTERVAL '30' SECOND)"
    assert window in forecast
    assert window in walkthrough


def test_agent_consumes_the_one_row_per_lap_car_state_stream_directly():
    for relative_path in (
        "demo-reference/streaming_agent_pit_decisions.sql",
        "README.md",
    ):
        sql = (ROOT / relative_path).read_text()
        agent_call = sql.index("LATERAL TABLE(AI_RUN_AGENT")
        section = sql[:agent_call]
        assert "FROM `car_state`" in section
        assert "WITH one_per_lap AS" not in section
