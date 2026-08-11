from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from scripts.participant import feed, rtce
from scripts.participant import reset as participant_reset
from scripts.social_feed_rtce import poller
from scripts.social_feed_rtce.rtce_client import RTCEClient
from scripts.workshop.onboard import _parse_pasted_email


def test_active_feed_boundary() -> None:
    now = 2_000.0
    assert feed.timestamp_is_recent(int((now - 90) * 1000), now=now)
    assert not feed.timestamp_is_recent(int((now - 90.001) * 1000), now=now)
    assert not feed.timestamp_is_recent(None, now=now)


def test_rtce_registration_parser_accepts_nested_cli_shape() -> None:
    row = {"spec": {"topic_name": "car_state"}, "status": {"phase": "READY"}}
    assert rtce._topic_name(row) == "car_state"
    assert rtce._topic_status(row) == "READY"


def test_rtce_enable_is_idempotent() -> None:
    card = {
        "F1_ENVIRONMENT_ID": "env-1",
        "F1_CLUSTER_ID": "lkc-1",
        "F1_RTCE_API_KEY": "key",
        "F1_RTCE_API_SECRET": "secret",
    }
    with patch.object(
        rtce,
        "list_registrations",
        return_value=[{"spec": {"topic_name": "car_state"}, "status": {"phase": "ONLINE"}}],
    ), patch.object(rtce, "_run_cli") as run:
        rtce.enable_topics(card, ["car_state"])
    run.assert_not_called()


def test_rtce_discovers_snake_case_and_builds_bounded_latest_query() -> None:
    client = RTCEClient("https://example.invalid", "token")
    client._tools = {
        "list_topics": "list_topics",
        "get_metadata": "get_metadata",
        "query_data": "query_data",
    }
    client._call = AsyncMock(return_value=SimpleNamespace(content=[]))
    asyncio.run(client.query("car_state", order_by='"EVENT_TIME" DESC', limit=5, max_rows=5))
    name, arguments = client._call.await_args.args
    assert name == "query_data"
    assert arguments["max_result_rows"] == 5
    assert arguments["query"].endswith('ORDER BY "EVENT_TIME" DESC LIMIT 5')


def test_tool_kind_accepts_current_and_legacy_names() -> None:
    assert RTCEClient._tool_kind("query_data") == "query_data"
    assert RTCEClient._tool_kind("queryData") == "query_data"
    assert RTCEClient._tool_kind("list_topics") == "list_topics"


def test_reset_refuses_before_mutation_when_feed_is_recent(monkeypatch: pytest.MonkeyPatch) -> None:
    card = {"F1_KAFKA_API_SECRET": "never-print-this"}
    monkeypatch.setattr(participant_reset, "load_card", lambda _: ("credentials.env", card))
    monkeypatch.setattr(participant_reset, "active_feed", lambda *args, **kwargs: (True, 12.0))
    cancel = Mock()
    monkeypatch.setattr(participant_reset, "cancel_lab_statements", cancel)
    monkeypatch.setattr("sys.argv", ["f1-reset", "--creds", "credentials.env"])
    with pytest.raises(SystemExit, match="Reset refused"):
        participant_reset.main()
    cancel.assert_not_called()


def test_secret_redaction() -> None:
    card = {"F1_KAFKA_API_KEY": "public-ish-key", "F1_KAFKA_API_SECRET": "private-secret"}
    rendered = participant_reset._redact("bad public-ish-key:private-secret", card)
    assert "public-ish-key" not in rendered
    assert "private-secret" not in rendered
    assert rendered.count("[redacted]") == 2


def test_timestamp_recent_uses_wall_clock() -> None:
    stamp = int((time.time() - 2) * 1000)
    assert feed.timestamp_is_recent(stamp)


def test_onboard_parses_three_separate_rtce_values() -> None:
    parsed = _parse_pasted_email(
        """Real-Time Context Engine / F1_RTCE_MCP_ENDPOINT: https://mcp.example.test/path
Real-Time Context Engine / F1_RTCE_API_KEY: rtce-key
Real-Time Context Engine / F1_RTCE_API_SECRET: rtce-secret"""
    )
    assert parsed["rtce_mcp_endpoint"] == "https://mcp.example.test/path"
    assert parsed["rtce_api_key"] == "rtce-key"
    assert parsed["rtce_api_secret"] == "rtce-secret"


def test_rtce_poller_orders_by_event_time_and_dedupes_per_race() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        async def query(self, topic: str, where: str, **kwargs):
            self.calls.append((topic, kwargs))
            if topic == "car_state":
                return [{"race_id": "race-b", "lap": 1, "event_time": "2026-08-11T10:00:00Z"}]
            return [{"race_id": "race-b", "lap": 1, "event_time": "2026-08-11T10:00:01Z"}]

    race_feed = SimpleNamespace(prefix="f1wp050", update_car_state=Mock(), add_decision=Mock())
    client = Client()
    seen: dict = {}
    asyncio.run(poller.poll_once(client, race_feed, seen))
    asyncio.run(poller.poll_once(client, race_feed, seen))

    assert all(kwargs["order_by"] == '"EVENT_TIME" DESC' for _, kwargs in client.calls)
    assert all(kwargs["limit"] == poller.QUERY_LIMIT for _, kwargs in client.calls)
    race_feed.add_decision.assert_called_once()
    assert seen["decision_id"] == ("race-b", "2026-08-11T10:00:01Z")
