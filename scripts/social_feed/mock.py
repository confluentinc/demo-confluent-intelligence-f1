"""Offline race generator for the social feed (``--mock``).

Drives a single ``FeedState`` (prefix ``f1wp001``) without Kafka or a Confluent
environment, so the service, its OpenAPI spec, and an Orchestrate agent wired to
it can be built and demoed at no cost. Reuses the Pit Wall mock's race logic
(``scripts.pitwall.mock``) — same 22-car grid, same lap-22 front-left spike — so
the offline arc matches what attendees see live.
"""

from __future__ import annotations

import logging
import os

from scripts.pitwall.mock import (
    ANOMALY_TEMP_THRESHOLD,
    CARSTATE_REVEAL_LAP,
    PITDEC_REVEAL_LAP,
    READINGS_PER_LAP,
    _build_car_state,
    _build_decision,
    _import_datagen,
    _sleep,
)
from scripts.pitwall.state import OUR_CAR_NUMBER
from scripts.social_feed.state import FeedState

logger = logging.getLogger("f1-social-feed.mock")

MOCK_PREFIX = "f1wp001"


def run_mock(feed: FeedState, stop) -> None:
    """Replay races into ``feed`` until ``stop`` is set."""
    grid, sim_race_cls, generate_telemetry = _import_datagen()
    seconds_per_lap = float(os.environ.get("SOCIAL_FEED_MOCK_SECONDS_PER_LAP", "1.2"))
    total_laps = int(os.environ.get("SOCIAL_FEED_MOCK_TOTAL_LAPS", "60"))
    logger.info("Mock race feed: %ss/lap, FL anomaly at lap 22", seconds_per_lap)

    while not stop.is_set():
        race = sim_race_cls(grid)
        post_pit = False
        for lap in range(1, total_laps + 1):
            if stop.is_set():
                return
            race.advance_lap()
            car44 = race.get_car(OUR_CAR_NUMBER)
            post_pit = post_pit or car44["pit_stops"] > 0

            for standing in race.get_standings():
                feed.update_standing(standing)

            last_reading = None
            for _ in range(READINGS_PER_LAP):
                reading = generate_telemetry(
                    lap=lap,
                    tire_age=car44["tire_age_laps"],
                    tire_compound=car44["tire_compound"],
                    post_pit=post_pit,
                )
                reading["car_number"] = OUR_CAR_NUMBER
                reading["lap"] = lap
                last_reading = reading
                _sleep(seconds_per_lap / READINGS_PER_LAP, stop)

            anomaly = last_reading["tire_temp_fl_c"] > ANOMALY_TEMP_THRESHOLD
            if lap >= CARSTATE_REVEAL_LAP:
                feed.update_car_state(_build_car_state(last_reading, car44, anomaly))
            if lap >= PITDEC_REVEAL_LAP:
                feed.add_decision(_build_decision(car44, anomaly))

        logger.info("Mock race complete — restarting in 5s")
        _sleep(5, stop)
