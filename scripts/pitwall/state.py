"""Thread-safe in-memory snapshot of the race for the Pit Wall dashboard.

A single ``RaceState`` is shared between the Kafka consumer thread (or the mock
generator) which writes to it, and the FastAPI websocket loop which reads
``snapshot()`` and pushes it to the browser. All access is guarded by one lock;
the write methods are tiny so contention is negligible.

Progressive reveal: ``seen_car_state`` / ``seen_pit_decisions`` flip ``True`` on
the first record from those topics. The frontend keeps the Anomaly (LAB 3) and
Agent (LAB 4) panels locked until then, so the dashboard visibly activates as the
attendee builds each stream.
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
        self._telemetry: dict | None = None
        self._standings: dict[int, dict] = {}
        self._car_state: dict | None = None
        self._decisions: deque[dict] = deque(maxlen=MAX_DECISIONS)
        self.seen_car_state = False
        self.seen_pit_decisions = False
        self._last_msg_ts = 0.0

    def update_telemetry(self, record: dict) -> None:
        with self._lock:
            self._telemetry = record
            self._last_msg_ts = time.time()

    def update_standing(self, record: dict) -> None:
        car = record.get("car_number")
        if car is None:
            return
        with self._lock:
            self._standings[car] = record
            self._last_msg_ts = time.time()

    def update_car_state(self, record: dict) -> None:
        with self._lock:
            self._car_state = record
            self.seen_car_state = True
            self._last_msg_ts = time.time()

    def add_decision(self, record: dict) -> None:
        with self._lock:
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
                "ts": time.time(),
            }
