"""Tests for race state management."""
from datagen.race_script import RaceState
from datagen.drivers import GRID


def test_initial_positions():
    """All 22 cars start in their grid positions."""
    state = RaceState(GRID)
    standings = state.get_standings()
    assert len(standings) == 22
    assert standings[0]["position"] == 1


def test_car44_starts_p3():
    """James River starts in P3."""
    state = RaceState(GRID)
    car44 = state.get_car(44)
    assert car44["position"] == 3


def test_pit_stop_changes_tire():
    """After pitting, tire compound and age reset."""
    state = RaceState(GRID)
    for _ in range(18):
        state.advance_lap()
    car1 = state.get_car(1)
    assert car1["tire_compound"] == "MEDIUM"
    assert car1["tire_age_laps"] < 5


def test_car44_drops_to_p8_by_lap32():
    """James River drops from P3 to P8 by lap 32 due to tire degradation."""
    state = RaceState(GRID)
    for _ in range(32):
        state.advance_lap()
    car44 = state.get_car(44)
    assert car44["position"] == 8, f"Expected P8, got P{car44['position']}"


def test_car44_recovers_to_p3_by_lap57():
    """After pit at lap 33, James River recovers to P3 by end of race."""
    state = RaceState(GRID)
    for _ in range(57):
        state.advance_lap()
    car44 = state.get_car(44)
    assert car44["position"] == 3, f"Expected P3, got P{car44['position']}"


def test_standings_dict_has_expected_keys():
    """get_car() should return the expected set of keys (no 'timestamp' — that's added by simulator)."""
    state = RaceState(GRID)
    state.advance_lap()
    car44 = state.get_car(44)
    expected_keys = {
        "car_number", "driver", "team", "lap", "position",
        "gap_to_leader_sec", "gap_to_ahead_sec", "last_lap_time_sec",
        "pit_stops", "tire_compound", "tire_age_laps", "in_pit_lane",
    }
    assert set(car44.keys()) == expected_keys
    assert "timestamp" not in car44
    assert "event_time" not in car44
