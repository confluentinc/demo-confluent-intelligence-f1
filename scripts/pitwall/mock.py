"""Offline race generator for the Pit Wall dashboard (``--mock``).

Drives ``RaceState`` without Kafka or a Confluent environment so the UI can be
built, demoed and verified end-to-end with no cost. It reuses the real race logic
from ``datagen/`` — the 22-car grid, the cumulative-time race model and the
telemetry curves (including the front-left spike at lap 22) — so the mock arc
matches what attendees see live.

To exercise progressive reveal it withholds the lab-output streams early on:
``car_state`` only starts at ``CARSTATE_REVEAL_LAP`` and ``pit_decisions`` at
``PITDEC_REVEAL_LAP``, so the Anomaly and Agent panels begin locked and then
light up — exactly like building LAB 3 then LAB 4.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from scripts.pitwall.state import OUR_CAR_NUMBER, RaceState

logger = logging.getLogger("f1-pitwall.mock")

# Laps at which the mock starts emitting each lab output (demonstrates reveal).
CARSTATE_REVEAL_LAP = 4
PITDEC_REVEAL_LAP = 6
READINGS_PER_LAP = 5
ANOMALY_TEMP_THRESHOLD = 130.0


def _import_datagen():
    """Import the simulator's race modules, adding the repo root to the path.

    ``datagen`` lives at the repo root and is not part of the installed wheel, so
    we put the repo root on ``sys.path`` before importing. Mock mode is always run
    from a checkout, so the source tree is present.
    """
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from datagen.drivers import GRID
    from datagen.race_script import RaceState as SimRace
    from datagen.telemetry import generate_telemetry

    return GRID, SimRace, generate_telemetry


def _now_millis() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _build_car_state(telemetry: dict, car44: dict, anomaly: bool) -> dict:
    """Synthesize a car_state row (the LAB 3 aggregation output)."""
    return {
        "car_number": OUR_CAR_NUMBER,
        "lap": car44["lap"],
        "tire_temp_fl_c": telemetry["tire_temp_fl_c"],
        "tire_temp_fr_c": telemetry["tire_temp_fr_c"],
        "tire_temp_rl_c": telemetry["tire_temp_rl_c"],
        "tire_temp_rr_c": telemetry["tire_temp_rr_c"],
        "tire_pressure_fl_psi": telemetry["tire_pressure_fl_psi"],
        "engine_temp_c": telemetry["engine_temp_c"],
        "battery_charge_pct": telemetry["battery_charge_pct"],
        "fuel_remaining_kg": telemetry["fuel_remaining_kg"],
        "anomaly_tire_temp_fl": anomaly,
        "position": car44["position"],
        "gap_to_ahead_sec": car44["gap_to_ahead_sec"],
        "gap_to_leader_sec": car44["gap_to_leader_sec"],
        "pit_stops": car44["pit_stops"],
        "tire_compound": car44["tire_compound"],
        "tire_age_laps": car44["tire_age_laps"],
    }


def _build_decision(car44: dict, anomaly: bool) -> dict:
    """Synthesize a pit_decisions row using the agent's decision algorithm."""
    compound = car44["tire_compound"]
    age = car44["tire_age_laps"]
    if anomaly:
        suggestion = "PIT NOW"
        reasoning = (
            "Front-left tire temperature has spiked into anomalous territory — "
            "box this lap before the tire fails and costs us the race."
        )
        rec_compound, rec_stint, rec_reason = (
            "MEDIUM",
            25,
            "Mediums will last to the flag and restore pace to fight back.",
        )
    elif compound == "SOFT" and age >= 26:
        suggestion = "PIT SOON"
        reasoning = "Softs are past their cliff; pace is dropping. Plan a stop in the next few laps."
        rec_compound, rec_stint, rec_reason = ("MEDIUM", 25, "Mediums balance pace and durability for the run home.")
    else:
        suggestion = "STAY OUT"
        reasoning = "Tires are within their working window and pace is competitive. Track position is worth holding."
        rec_compound, rec_stint, rec_reason = (None, None, None)

    return {
        "car_number": OUR_CAR_NUMBER,
        "lap": car44["lap"],
        "position": car44["position"],
        "tire_compound_current": compound,
        "tire_age_laps": age,
        "anomaly_tire_temp_fl": anomaly,
        "suggestion": suggestion,
        "condition_summary": f"FL tire {'ANOMALOUS' if anomaly else 'nominal'}; {compound} at {age} laps.",
        "race_context": f"Running P{car44['position']}, {car44['gap_to_leader_sec']:.1f}s off the lead.",
        "recommended_tire_compound": rec_compound,
        "recommended_stint_laps": rec_stint,
        "recommended_reason": rec_reason,
        "reasoning": reasoning,
        "raw_response": "(mock)",
    }


def _sleep(seconds: float, stop) -> None:
    """Interruptible sleep that returns early when ``stop`` is set."""
    deadline = time.time() + seconds
    while time.time() < deadline and not stop.is_set():
        time.sleep(min(0.1, deadline - time.time()))


def run_mock(state: RaceState, stop) -> None:
    """Replay races into ``state`` until ``stop`` is set."""
    grid, sim_race_cls, generate_telemetry = _import_datagen()
    seconds_per_lap = float(os.environ.get("PITWALL_MOCK_SECONDS_PER_LAP", "1.2"))
    total_laps = int(os.environ.get("PITWALL_MOCK_TOTAL_LAPS", "60"))
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

            ts = _now_millis()
            for standing in race.get_standings():
                standing["event_time"] = ts
                state.update_standing(standing)

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
                reading["event_time"] = _now_millis()
                state.update_telemetry(reading)
                last_reading = reading
                _sleep(seconds_per_lap / READINGS_PER_LAP, stop)

            anomaly = last_reading["tire_temp_fl_c"] > ANOMALY_TEMP_THRESHOLD
            if lap >= CARSTATE_REVEAL_LAP:
                state.update_car_state(_build_car_state(last_reading, car44, anomaly))
            if lap >= PITDEC_REVEAL_LAP:
                state.add_decision(_build_decision(car44, anomaly))

        logger.info("Mock race complete — restarting in 5s")
        _sleep(5, stop)
