"""Static contracts for the retained video-only RTCE UPSERT demo."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "demo-reference"
SETUP = (REFERENCE / "rtce_upsert_verification_setup.sql").read_text()
FEED = (REFERENCE / "rtce_upsert_verification_feed.sql").read_text()
RUNBOOK = (REFERENCE / "rtce_upsert_verification.md").read_text()

STANDINGS_FIELDS = (
    "car_number",
    "driver",
    "team",
    "lap",
    "position",
    "gap_to_leader_sec",
    "gap_to_ahead_sec",
    "last_lap_time_sec",
    "pit_stops",
    "tire_compound",
    "tire_age_laps",
    "in_pit_lane",
    "event_time",
)


def test_serving_table_has_raw_compacted_upsert_contract() -> None:
    assert "CREATE TABLE IF NOT EXISTS `race_standings_rtce`" in SETUP
    assert "`key` STRING NOT NULL" in SETUP
    assert "PRIMARY KEY (`key`) NOT ENFORCED" in SETUP
    for option in (
        "'changelog.mode' = 'upsert'",
        "'kafka.cleanup-policy' = 'compact'",
        "'key.format' = 'raw'",
        "'value.fields-include' = 'all'",
        "'value.format' = 'avro-registry'",
    ):
        assert option in SETUP


def test_serving_value_contains_the_complete_standings_schema() -> None:
    for field in STANDINGS_FIELDS:
        assert f"`{field}`" in SETUP


def test_feed_derives_the_same_key_as_the_sink_primary_key() -> None:
    key_expression = "CAST(`car_number` AS STRING)"
    assert f"{key_expression} AS `key`" in FEED
    assert f"GROUP BY {key_expression}" in FEED
    assert "INSERT INTO `race_standings_rtce`" in FEED
    assert "PRIMARY KEY (`key`) NOT ENFORCED" in SETUP


def test_feed_reduces_every_value_field_with_last_value() -> None:
    for field in STANDINGS_FIELDS:
        assert f"LAST_VALUE(`{field}`) AS `{field}`" in FEED


def test_feed_applies_one_hour_state_ttl_to_the_aggregation_input() -> None:
    assert "STATE_TTL('standings' = '1h')" in FEED
    assert "FROM `race_standings` AS `standings`" in FEED


def test_demo_has_no_destructive_or_delete_policy_fixture() -> None:
    names = {path.name for path in REFERENCE.glob("rtce_upsert_verification*")}
    assert names == {
        "rtce_upsert_verification.md",
        "rtce_upsert_verification_feed.sql",
        "rtce_upsert_verification_setup.sql",
    }
    combined = SETUP + FEED + RUNBOOK
    assert "kafka.cleanup-policy' = 'delete" not in combined
    assert not re.search(r"\bDROP\s+(?:TABLE|TOPIC)\b", combined, re.IGNORECASE)
    assert "There is intentionally no cleanup SQL file" in RUNBOOK


def _terraform_default(path: str, variable: str) -> int:
    text = (ROOT / path).read_text()
    block = re.search(rf'variable "{variable}" \{{(.*?)\n\}}', text, re.DOTALL)
    assert block, f"missing {variable} in {path}"
    value = re.search(r"^\s*default\s*=\s*(\d+)\s*$", block.group(1), re.MULTILINE)
    assert value, f"missing numeric default for {variable} in {path}"
    return int(value.group(1))


def test_current_flink_pool_sources_of_truth_default_to_ten() -> None:
    spec = yaml.safe_load((ROOT / "wsa-spec-aws.yaml").read_text())
    assert spec["terraform_vars"]["flink_max_cfu"] == "10"
    assert _terraform_default("terraform/aws/variables.tf", "flink_max_cfu") == 10
    assert _terraform_default("terraform/self-service/variables.tf", "flink_max_cfu") == 10
    assert _terraform_default("terraform/modules/flink/variables.tf", "max_cfu") == 10
