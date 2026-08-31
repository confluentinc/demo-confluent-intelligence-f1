from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACKS = (
    "docs/tracks/HOSTED-WORKSHOP.md",
    "docs/tracks/SELF-SERVICE.md",
    "docs/tracks/STANDALONE-DEMO.md",
)
SQL_SOURCES = (
    ("docs/demo-reference/enrichment_anomaly.sql", "CREATE TABLE `car_state`"),
    ("docs/demo-reference/streaming_agent_create_agent.sql", "CREATE AGENT `pit_strategy_agent`"),
    ("docs/demo-reference/streaming_agent_pit_decisions.sql", "CREATE TABLE `pit_decisions`"),
    ("docs/demo-reference/granite_tire_forecast.sql", "WITH windowed AS"),
)


def _statement(relative_path: str, marker: str) -> str:
    source = (ROOT / relative_path).read_text()
    return source[source.index(marker) :].strip()


def test_attendee_walkthrough_sql_matches_the_canonical_sources():
    for source_path, marker in SQL_SOURCES:
        statement = _statement(source_path, marker)
        for track_path in TRACKS:
            assert statement in (ROOT / track_path).read_text()


def test_only_the_lap_24_anomaly_can_issue_pit_now():
    decision_sql = (ROOT / "docs/demo-reference/streaming_agent_pit_decisions.sql").read_text()
    assert decision_sql.count("WHEN cs.anomaly_tire_temp_fl THEN 'PIT NOW'") == 2
    assert decision_sql.count("WHEN cs.pit_stops > 0 THEN 'STAY OUT'") == 2
    assert decision_sql.count("cs.tire_compound = 'SOFT' AND cs.tire_age_laps >= 21") == 2
    assert "tire_age_laps >= 22 THEN 'PIT NOW'" not in decision_sql
