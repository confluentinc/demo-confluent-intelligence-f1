"""Dashboard/feed state switches races atomically and rejects delayed records."""

from scripts.pitwall.state import RaceState
from scripts.social_feed.server import RaceFeed
from scripts.social_feed.state import FeedState

OLD_RACE = "20260811T010000000000Z-aaaaaaaa"
NEW_RACE = "20260811T020000000000Z-bbbbbbbb"


def test_pitwall_switch_clears_old_race_and_rejects_delayed_records() -> None:
    state = RaceState()
    state.update_standing({"race_id": OLD_RACE, "car_number": 88, "lap": 30, "position": 4})
    state.update_car_state({"race_id": OLD_RACE, "car_number": 88, "lap": 30})
    state.add_decision({"race_id": OLD_RACE, "lap": 30, "suggestion": "PIT SOON"})

    state.update_telemetry({"race_id": NEW_RACE, "car_number": 88, "lap": 1})
    state.update_standing({"race_id": OLD_RACE, "car_number": 1, "lap": 60, "position": 1})
    snapshot = state.snapshot()

    assert snapshot["race_id"] == NEW_RACE
    assert snapshot["lap"] == 1
    assert snapshot["standings"] == []
    assert snapshot["car_state"] is None
    assert snapshot["decisions"] == []
    assert snapshot["reveal"] == {"car_state": False, "pit_decisions": False}


def test_social_feed_switch_clears_digest_and_rejects_delayed_records() -> None:
    feed = FeedState("f1wp050")
    feed.update_standing({"race_id": OLD_RACE, "car_number": 88, "lap": 30, "position": 5})
    feed.update_standing({"race_id": OLD_RACE, "car_number": 88, "lap": 31, "position": 4})
    feed.update_car_state(
        {"race_id": OLD_RACE, "car_number": 88, "lap": 31, "anomaly_tire_temp_fl": True}
    )
    feed.add_decision({"race_id": OLD_RACE, "lap": 31, "suggestion": "PIT NOW"})

    feed.update_standing(
        {
            "race_id": NEW_RACE,
            "car_number": 88,
            "lap": 1,
            "position": 10,
            "event_time": 1_786_413_600_000,
        }
    )
    feed.add_decision({"race_id": OLD_RACE, "lap": 31, "suggestion": "PIT NOW"})
    snapshot = feed.snapshot()

    assert snapshot["race_id"] == NEW_RACE
    assert snapshot["lap"] == 1
    assert snapshot["our_position"] == 10
    assert snapshot["standings"][0]["race_id"] == NEW_RACE
    response = RaceFeed.model_validate(snapshot).model_dump()
    assert response["race_id"] == NEW_RACE
    assert response["standings"][0]["event_time"].endswith("+00:00")
    assert snapshot["tire"] is None
    assert snapshot["latest_pit_decision"] is None
    assert snapshot["headline_events"] == []


def test_social_feed_replay_does_not_claim_to_be_live(monkeypatch) -> None:
    now = 1_786_430_000.0
    monkeypatch.setattr("scripts.social_feed.state.time.time", lambda: now)
    feed = FeedState("f1wp050")

    feed.update_standing(
        {
            "race_id": NEW_RACE,
            "car_number": 88,
            "lap": 12,
            "position": 4,
            "event_time": (now - 3600) * 1000,
        }
    )
    assert feed.snapshot()["live"] is False

    feed.update_standing(
        {
            "race_id": NEW_RACE,
            "car_number": 88,
            "lap": 13,
            "position": 4,
            "event_time": (now - 5) * 1000,
        }
    )
    assert feed.snapshot()["live"] is True
