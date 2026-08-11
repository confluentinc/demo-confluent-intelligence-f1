"""Static contracts for per-race identity and late-starting lab behavior."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOPICS = (ROOT / "terraform/modules/topics/main.tf").read_text()
ANOMALY = (ROOT / "demo-reference/enrichment_anomaly.sql").read_text()
ANOMALY_AI = (ROOT / "demo-reference/enrichment_anomaly_ai.sql").read_text()
PIT = (ROOT / "demo-reference/streaming_agent_pit_decisions.sql").read_text()
WALKTHROUGH = (ROOT / "Walkthrough.md").read_text()


def test_source_topics_carry_race_id_event_time_and_explicit_replay_modes() -> None:
    assert TOPICS.count("`race_id` STRING") >= 2
    assert TOPICS.count("`event_time` TIMESTAMP(3)") >= 2
    assert TOPICS.count("'scan.startup.mode' = 'earliest-offset'") >= 2
    assert "'kafka.retention.time' = '24 h'" in TOPICS
    assert "'kafka.cleanup-policy' = 'delete-compact'" in TOPICS
    assert "'kafka.compaction.time' = '6 h'" in TOPICS


def test_standings_uses_composite_per_race_identity() -> None:
    assert "PRIMARY KEY (`race_id`, `car_number`) NOT ENFORCED" in TOPICS
    assert "DISTRIBUTED BY (`race_id`, `car_number`)" in TOPICS


def test_anomaly_jobs_join_and_partition_with_race_identity() -> None:
    for sql in (ANOMALY, ANOMALY_AI):
        assert "ON t.race_id = r.race_id AND t.car_number = r.car_number" in sql
        assert "PARTITION BY race_id, car_number" in sql
        assert "GROUP BY window_start, window_end, window_time, race_id, car_number" in sql
        assert "CREATE TABLE IF NOT EXISTS `car_state`" in sql
        # RTCE rejects compacted/upsert workshop topics, so car_state keeps
        # composite logical isolation while remaining append-only storage.
        assert "'changelog.mode' = 'append'" in sql
        assert "PRIMARY KEY" not in sql
        assert "RTCE registration" in sql
        assert "'scan.startup.mode' = 'latest-offset'" in sql
        assert "INSERT INTO `car_state`" in sql


def test_pit_decisions_propagate_race_and_event_time_without_inline_options() -> None:
    assert "CREATE TABLE IF NOT EXISTS `pit_decisions`" in PIT
    assert "cs.race_id" in PIT
    assert "cs.event_time" in PIT
    assert "OPTIONS(" not in PIT


def test_walkthrough_embeds_the_same_late_start_race_contract() -> None:
    assert "keyed by (`race_id`, `car_number`)" in WALKTHROUGH
    assert "CREATE TABLE IF NOT EXISTS `car_state`" in WALKTHROUGH
    assert "INSERT INTO `car_state`" in WALKTHROUGH
    assert "'scan.startup.mode' = 'latest-offset'" in WALKTHROUGH
    assert "ON t.race_id = r.race_id AND t.car_number = r.car_number" in WALKTHROUGH
    assert "PARTITION BY race_id, car_number" in WALKTHROUGH
    assert "CREATE TABLE IF NOT EXISTS `pit_decisions`" in WALKTHROUGH
    assert "INSERT INTO `pit_decisions`" in WALKTHROUGH
    assert "cs.race_id" in WALKTHROUGH
    assert "cs.event_time" in WALKTHROUGH
    assert "OPTIONS(" not in WALKTHROUGH
