"""Thread-safe per-attendee race feed for the social-media agent.

The ``f1-social-feed`` service runs one Kafka consumer per attendee and one
``FeedState`` per attendee, all held in a ``FeedStore`` keyed by prefix. The
FastAPI route reads ``FeedState.snapshot()`` and serves it as the OpenAPI tool
response that a watsonx Orchestrate agent calls to draft social posts.

Where the Pit Wall dashboard streams everything at ~4 Hz to animate a browser,
this service serves a *digest*: the leader battle, our driver's position and tire
state, the latest pit call, and a short list of human-readable ``headline_events``
derived by diffing successive snapshots (position changes, anomaly onset, new pit
calls). That list is what gives an LLM something concrete and timely to post about.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone

OUR_CAR_NUMBER = 88
OUR_DRIVER = "John Doe"
TEAM = "River Racing"

MAX_DECISIONS = 10
MAX_EVENTS = 12
# Standings refresh once per lap (up to 60s live), so a feed is "live" if we've
# seen any record within two laps' worth of wall-clock time.
LIVE_WINDOW_SEC = 130
# Leaders to surface plus our own car (added if it's running outside the top N).
TOP_N = 6


def _round(value, digits: int = 1):
    return round(value, digits) if isinstance(value, (int, float)) else value


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _standing_entry(s: dict) -> dict:
    return {
        "position": s.get("position"),
        "car_number": s.get("car_number"),
        "driver": s.get("driver"),
        "team": s.get("team"),
        "gap_to_leader_sec": _round(s.get("gap_to_leader_sec")),
        "last_lap_time_sec": _round(s.get("last_lap_time_sec"), 3),
        "pit_stops": s.get("pit_stops"),
        "tire_compound": s.get("tire_compound"),
    }


class FeedState:
    """Latest race digest for a single attendee, plus derived headline events."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self._lock = threading.Lock()
        self._standings: dict[int, dict] = {}
        self._car_state: dict | None = None
        self._decisions: deque[dict] = deque(maxlen=MAX_DECISIONS)
        self._events: deque[str] = deque(maxlen=MAX_EVENTS)
        self._last_position: int | None = None
        self._last_suggestion: str | None = None
        self._last_anomaly = False
        self._last_msg_ts = 0.0

    def _event(self, lap, text: str) -> None:
        prefix = f"Lap {lap} — " if lap else ""
        self._events.append(f"{prefix}{text}")

    def update_standing(self, record: dict) -> None:
        car = record.get("car_number")
        if car is None:
            return
        with self._lock:
            self._standings[car] = record
            self._last_msg_ts = time.time()
            if car == OUR_CAR_NUMBER:
                pos = record.get("position")
                if pos is not None and self._last_position is not None and pos != self._last_position:
                    lap = record.get("lap")
                    if pos < self._last_position:
                        self._event(lap, f"{OUR_DRIVER} gains to P{pos} (up {self._last_position - pos})")
                    else:
                        self._event(lap, f"{OUR_DRIVER} drops to P{pos} (down {pos - self._last_position})")
                if pos is not None:
                    self._last_position = pos

    def update_car_state(self, record: dict) -> None:
        with self._lock:
            self._car_state = record
            self._last_msg_ts = time.time()
            anomaly = bool(record.get("anomaly_tire_temp_fl"))
            if anomaly and not self._last_anomaly:
                self._event(record.get("lap"), "⚠️ Front-left tire overheating — anomaly detected")
            self._last_anomaly = anomaly

    def add_decision(self, record: dict) -> None:
        with self._lock:
            self._decisions.append(record)
            self._last_msg_ts = time.time()
            suggestion = record.get("suggestion")
            if suggestion and suggestion != "STAY OUT" and suggestion != self._last_suggestion:
                self._event(record.get("lap"), f"Pit wall calls {suggestion}")
            self._last_suggestion = suggestion

    def snapshot(self) -> dict:
        """JSON-serializable digest for the OpenAPI ``/race-feed/{prefix}`` route."""
        with self._lock:
            standings_sorted = sorted(self._standings.values(), key=lambda s: s.get("position") or 999)
            lap = max(
                (s.get("lap") or 0 for s in self._standings.values()),
                default=(self._car_state or {}).get("lap", 0),
            )
            our = self._standings.get(OUR_CAR_NUMBER)
            top = standings_sorted[:TOP_N]
            if our is not None and our not in top:
                top = [*top, our]

            cs = self._car_state
            tire = None
            if cs is not None:
                tire = {
                    "compound": cs.get("tire_compound"),
                    "age_laps": cs.get("tire_age_laps"),
                    "front_left_temp_c": _round(cs.get("tire_temp_fl_c")),
                    "anomaly": bool(cs.get("anomaly_tire_temp_fl")),
                }

            latest = self._decisions[-1] if self._decisions else None
            pit = None
            if latest is not None:
                pit = {
                    "lap": latest.get("lap"),
                    "suggestion": latest.get("suggestion"),
                    "reasoning": latest.get("reasoning"),
                    "recommended_tire_compound": latest.get("recommended_tire_compound"),
                }

            return {
                "prefix": self.prefix,
                "lap": lap,
                "driver": OUR_DRIVER,
                "team": TEAM,
                "car_number": OUR_CAR_NUMBER,
                "our_position": (our or {}).get("position"),
                "standings": [_standing_entry(s) for s in top],
                "tire": tire,
                "latest_pit_decision": pit,
                "headline_events": list(self._events),
                "live": (time.time() - self._last_msg_ts) < LIVE_WINDOW_SEC if self._last_msg_ts else False,
                "updated_at": _iso(time.time()),
            }


class FeedStore:
    """All attendees' feeds, keyed by prefix (e.g. ``f1wp001``)."""

    def __init__(self) -> None:
        self._feeds: dict[str, FeedState] = {}

    def get_or_create(self, prefix: str) -> FeedState:
        feed = self._feeds.get(prefix)
        if feed is None:
            feed = FeedState(prefix)
            self._feeds[prefix] = feed
        return feed

    def get(self, prefix: str) -> FeedState | None:
        return self._feeds.get(prefix)

    def prefixes(self) -> list[str]:
        return sorted(self._feeds)
