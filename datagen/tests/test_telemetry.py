"""Tests for telemetry metric generation."""

from datagen.telemetry import generate_telemetry


def test_telemetry_lap1_normal():
    """Fresh tires at lap 1 — all temps in normal range."""
    data = generate_telemetry(lap=1, tire_age=1, tire_compound="SOFT", post_pit=False)
    assert 90 <= data["tire_temp_fl_c"] <= 100
    assert 90 <= data["tire_temp_fr_c"] <= 100
    assert data["fuel_remaining_kg"] > 40


def test_telemetry_lap32_anomaly():
    """Lap 32 — tire_temp_fl must spike above 140."""
    data = generate_telemetry(lap=32, tire_age=32, tire_compound="SOFT", post_pit=False)
    assert data["tire_temp_fl_c"] >= 140, "Front-left tire temp must spike at lap 32"
    assert data["tire_temp_fr_c"] < 120, "Other tire temps must stay normal"


def test_telemetry_after_pit():
    """After pit stop — fresh tires, temps drop back to normal."""
    data = generate_telemetry(lap=35, tire_age=2, tire_compound="MEDIUM", post_pit=True)
    assert 90 <= data["tire_temp_fl_c"] <= 100
    assert data["fuel_remaining_kg"] < 40


def test_fuel_decreases_linearly():
    """Fuel should decrease by ~0.7 kg per lap."""
    lap5 = generate_telemetry(lap=5, tire_age=5, tire_compound="SOFT", post_pit=False)
    lap10 = generate_telemetry(lap=10, tire_age=10, tire_compound="SOFT", post_pit=False)
    assert lap5["fuel_remaining_kg"] > lap10["fuel_remaining_kg"]


def test_no_anomaly_on_other_metrics():
    """At lap 32, only tire_temp_fl spikes. Engine, brakes, battery, pressures stay normal."""
    data = generate_telemetry(lap=32, tire_age=32, tire_compound="SOFT", post_pit=False)
    assert 115 <= data["engine_temp_c"] <= 125
    assert 19 <= data["tire_pressure_fl_psi"] <= 23
    assert 30 <= data["battery_charge_pct"] <= 85
