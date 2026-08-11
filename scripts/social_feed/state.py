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


def _event_time(value) -> str | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (int, float)):
        return _iso(value / 1000)
    return str(value) if value is not None else None


def _event_timestamp(value) -> float | None:
    """Normalize event-time values without treating ingestion time as event time."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    if isinstance(value, (int, float)):
        raw = float(value)
        return raw / 1000 if raw > 10_000_000_000 else raw
    if isinstance(value, str):
        try:
            return float(value) / (1000 if float(value) > 10_000_000_000 else 1)
        except ValueError:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return None
    return None


def _standing_entry(s: dict) -> dict:
    return {
        "race_id": s.get("race_id"),
        "event_time": _event_time(s.get("event_time")),
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
        self._race_id: str | None = None
        self._standings: dict[int, dict] = {}
        self._car_state: dict | None = None
        self._decisions: deque[dict] = deque(maxlen=MAX_DECISIONS)
        self._events: deque[str] = deque(maxlen=MAX_EVENTS)
        self._last_position: int | None = None
        self._last_suggestion: str | None = None
        self._last_anomaly = False
        self._last_msg_ts = 0.0
        self._last_event_ts = 0.0
        self._consumer_ready = False
        self._connection_error: dict | None = None

    def _accept_race_locked(self, record: dict) -> bool:
        """Switch the whole digest to a newer race and reject delayed old data."""
        race_id = record.get("race_id")
        if not race_id:
            return self._race_id is None
        if self._race_id is not None and race_id < self._race_id:
            return False
        if race_id == self._race_id:
            return True

        self._race_id = race_id
        self._standings.clear()
        self._car_state = None
        self._decisions.clear()
        self._events.clear()
        self._last_position = None
        self._last_suggestion = None
        self._last_anomaly = False
        self._last_msg_ts = 0.0
        self._last_event_ts = 0.0
        return True

    def _mark_record_locked(self, record: dict) -> None:
        self._last_msg_ts = time.time()
        source_ts = _event_timestamp(record.get("event_time"))
        if source_ts is not None:
            self._last_event_ts = max(self._last_event_ts, source_ts)
        self._connection_error = None

    def mark_consumer_ready(self) -> None:
        with self._lock:
            self._consumer_ready = True

    def record_error(self, code: str, detail: str) -> None:
        with self._lock:
            self._connection_error = {"code": code, "detail": detail}

    def clear_error(self) -> None:
        with self._lock:
            self._connection_error = None

    def health(self) -> dict:
        with self._lock:
            if self._connection_error:
                status = "error"
            elif self._consumer_ready:
                status = "ready"
            else:
                status = "starting"
            return {
                "prefix": self.prefix,
                "status": status,
                "error": self._connection_error,
            }

    def _event(self, lap, text: str) -> None:
        prefix = f"Lap {lap} — " if lap else ""
        self._events.append(f"{prefix}{text}")

    def update_standing(self, record: dict) -> None:
        car = record.get("car_number")
        if car is None:
            return
        with self._lock:
            if not self._accept_race_locked(record):
                return
            self._standings[car] = record
            self._mark_record_locked(record)
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
            if not self._accept_race_locked(record):
                return
            self._car_state = record
            self._mark_record_locked(record)
            anomaly = bool(record.get("anomaly_tire_temp_fl"))
            if anomaly and not self._last_anomaly:
                self._event(record.get("lap"), "⚠️ Front-left tire overheating — anomaly detected")
            self._last_anomaly = anomaly

    def add_decision(self, record: dict) -> None:
        with self._lock:
            if not self._accept_race_locked(record):
                return
            self._decisions.append(record)
            self._mark_record_locked(record)
            suggestion = record.get("suggestion")
            if suggestion and suggestion != "STAY OUT" and suggestion != self._last_suggestion:
                self._event(record.get("lap"), f"Pit wall calls {suggestion}")
            self._last_suggestion = suggestion

    def snapshot(self) -> dict:
        """JSON-serializable digest for the OpenAPI ``/race-feed/{prefix}`` route."""
        with self._lock:
            now = time.time()
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
                    "race_id": cs.get("race_id"),
                    "event_time": _event_time(cs.get("event_time")),
                    "compound": cs.get("tire_compound"),
                    "age_laps": cs.get("tire_age_laps"),
                    "front_left_temp_c": _round(cs.get("tire_temp_fl_c")),
                    "anomaly": bool(cs.get("anomaly_tire_temp_fl")),
                }

            latest = self._decisions[-1] if self._decisions else None
            pit = None
            if latest is not None:
                pit = {
                    "race_id": latest.get("race_id"),
                    "event_time": _event_time(latest.get("event_time")),
                    "lap": latest.get("lap"),
                    "suggestion": latest.get("suggestion"),
                    "reasoning": latest.get("reasoning"),
                    "recommended_tire_compound": latest.get("recommended_tire_compound"),
                }

            return {
                "prefix": self.prefix,
                "race_id": self._race_id,
                "lap": lap,
                "driver": OUR_DRIVER,
                "team": TEAM,
                "car_number": OUR_CAR_NUMBER,
                "our_position": (our or {}).get("position"),
                "standings": [_standing_entry(s) for s in top],
                "tire": tire,
                "latest_pit_decision": pit,
                "headline_events": list(self._events),
                "live": bool(
                    self._last_msg_ts
                    and self._last_event_ts
                    and (now - self._last_msg_ts) < LIVE_WINDOW_SEC
                    and -5 <= (now - self._last_event_ts) < LIVE_WINDOW_SEC
                ),
                "updated_at": _iso(now),
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

    def health(self) -> list[dict]:
        return [self._feeds[prefix].health() for prefix in self.prefixes()]
