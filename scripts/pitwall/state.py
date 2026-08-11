"""Thread-safe in-memory snapshot of the race for the Pit Wall dashboard.

A single ``RaceState`` is shared between the Kafka consumer thread (or the mock
generator) which writes to it, and the FastAPI websocket loop which reads
``snapshot()`` and pushes it to the browser. All access is guarded by one lock;
the write methods are tiny so contention is negligible.

Progressive reveal: ``seen_car_state`` / ``seen_pit_decisions`` flip ``True`` on
the first record from those topics. The frontend keeps the Anomaly (LAB 3) and
Agent (LAB 4) panels locked until then, so the dashboard visibly activates as the
attendee builds each stream.

``connection_error`` carries the reason the feed isn't flowing. Without it a
stale credential card produces a dashboard that loads, renders an empty grid,
reports ``live: false`` and never says why — the whole point of the field is that
``live: false`` should always be accompanied by a cause the user can read.
"""

from __future__ import annotations

import threading
import time
from collections import deque

OUR_CAR_NUMBER = 88
MAX_DECISIONS = 20


class RaceState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._race_id: str | None = None
        self._telemetry: dict | None = None
        self._standings: dict[int, dict] = {}
        self._car_state: dict | None = None
        self._decisions: deque[dict] = deque(maxlen=MAX_DECISIONS)
        self.seen_car_state = False
        self.seen_pit_decisions = False
        self._last_msg_ts = 0.0
        self._connection_error: dict | None = None

    def _accept_race_locked(self, record: dict) -> bool:
        """Switch atomically to a newer race and reject delayed older records."""
        race_id = record.get("race_id")
        if not race_id:
            return self._race_id is None
        if self._race_id is not None and race_id < self._race_id:
            return False
        if race_id == self._race_id:
            return True

        self._race_id = race_id
        self._telemetry = None
        self._standings.clear()
        self._car_state = None
        self._decisions.clear()
        self.seen_car_state = False
        self.seen_pit_decisions = False
        self._last_msg_ts = 0.0
        return True

    def record_error(self, code: str, detail: str) -> None:
        """Publish the reason the feed isn't flowing, for ``snapshot()``/``/healthz``.

        Called by the consumer's error classifier, not by the routing methods.
        """
        with self._lock:
            self._connection_error = {"code": code, "detail": detail, "ts": time.time()}

    def clear_error(self) -> None:
        """Drop the published error — records are arriving again.

        Deliberately cleared by data rather than kept with its timestamp: a
        transient broker blip at startup would otherwise sit on the dashboard for
        the rest of the race. A genuine auth failure is re-reported on every
        reconnect attempt, so it reappears within seconds. The warn-once terminal
        log survives either way, so nothing is actually lost.
        """
        with self._lock:
            self._connection_error = None

    def update_telemetry(self, record: dict) -> None:
        with self._lock:
            if not self._accept_race_locked(record):
                return
            self._telemetry = record
            self._last_msg_ts = time.time()

    def update_standing(self, record: dict) -> None:
        car = record.get("car_number")
        if car is None:
            return
        with self._lock:
            if not self._accept_race_locked(record):
                return
            self._standings[car] = record
            self._last_msg_ts = time.time()

    def update_car_state(self, record: dict) -> None:
        with self._lock:
            if not self._accept_race_locked(record):
                return
            self._car_state = record
            self.seen_car_state = True
            self._last_msg_ts = time.time()

    def add_decision(self, record: dict) -> None:
        with self._lock:
            if not self._accept_race_locked(record):
                return
            self._decisions.append(record)
            self.seen_pit_decisions = True
            self._last_msg_ts = time.time()

    def snapshot(self) -> dict:
        """Return a JSON-serializable view of the current race state."""
        with self._lock:
            standings = sorted(
                self._standings.values(),
                key=lambda s: s.get("position") or 999,
            )
            lap = max(
                (s.get("lap") or 0 for s in self._standings.values()),
                default=(self._telemetry or {}).get("lap", 0),
            )
            return {
                "race_id": self._race_id,
                "our_car": OUR_CAR_NUMBER,
                "lap": lap,
                "telemetry": self._telemetry,
                "standings": standings,
                "car_state": self._car_state,
                "decisions": list(self._decisions),
                "reveal": {
                    "car_state": self.seen_car_state,
                    "pit_decisions": self.seen_pit_decisions,
                },
                "live": (time.time() - self._last_msg_ts) < 10 if self._last_msg_ts else False,
                "connection_error": self._connection_error,
                "ts": time.time(),
            }
