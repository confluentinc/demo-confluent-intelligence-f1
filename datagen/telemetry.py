"""Generates realistic car telemetry metrics with a single anomaly at lap 32."""
import random

# Starting values
FUEL_START_KG = 44.0
FUEL_BURN_PER_LAP = 0.7

# Tire temperature baselines and gradients
TIRE_TEMP_BASE = {"fl": 95.0, "fr": 95.0, "rl": 93.0, "rr": 93.0}
TIRE_TEMP_GRADIENT_PER_LAP = {"fl": 0.42, "fr": 0.45, "rl": 0.39, "rr": 0.35}

# Tire pressure baselines
TIRE_PRESSURE_BASE = {"fl": 22.0, "fr": 22.0, "rl": 21.0, "rr": 21.0}
TIRE_PRESSURE_DROP_PER_LAP = 0.05

ANOMALY_LAP = 32
ANOMALY_TEMP = 145.0


def _noise(amplitude=0.5):
    return random.uniform(-amplitude, amplitude)


def generate_telemetry(lap, tire_age, tire_compound, post_pit):
    """Generate one telemetry reading for car #44.

    Args:
        lap: Current race lap (1-57)
        tire_age: Laps on current tire set (resets after pit)
        tire_compound: SOFT, MEDIUM, or HARD
        post_pit: Whether car has pitted (resets tire baselines)
    """
    # Tire temperatures — gradual rise with tire age
    tire_temps = {}
    for pos in ["fl", "fr", "rl", "rr"]:
        base = TIRE_TEMP_BASE[pos]
        gradient = TIRE_TEMP_GRADIENT_PER_LAP[pos]
        tire_temps[pos] = base + (gradient * tire_age) + _noise(1.0)

    # ANOMALY: front-left tire temp spikes at lap 32 (only if pre-pit)
    if lap == ANOMALY_LAP and not post_pit:
        tire_temps["fl"] = ANOMALY_TEMP + _noise(2.0)

    # Tire pressures — slow linear decline with tire age
    tire_pressures = {}
    for pos in ["fl", "fr", "rl", "rr"]:
        base = TIRE_PRESSURE_BASE[pos]
        tire_pressures[pos] = base - (TIRE_PRESSURE_DROP_PER_LAP * tire_age) + _noise(0.1)

    # Engine temp — stable with minor fluctuation
    engine_temp = 120.0 + _noise(2.0)

    # Brake temps — cyclical (simulates braking zones), consistent pattern
    brake_base = 450.0
    brake_temps = {
        "fl": brake_base + _noise(25.0),
        "fr": brake_base + 10 + _noise(25.0),
    }

    # Battery — regular charge/discharge cycle
    battery_base = 60.0
    battery_cycle = 20.0 * (0.5 + 0.5 * (((lap * 3 + tire_age) % 7) / 7.0))
    battery_pct = battery_base + battery_cycle + _noise(2.0)
    battery_pct = max(35.0, min(85.0, battery_pct))

    # Fuel — perfectly linear decrease
    fuel = FUEL_START_KG - (FUEL_BURN_PER_LAP * lap) + _noise(0.05)
    fuel = max(0.5, fuel)

    # Speed, throttle, brake — realistic ranges
    speed = 280.0 + random.uniform(0, 40)
    throttle = random.uniform(60, 100)
    brake = random.uniform(0, 15) if random.random() < 0.3 else 0.0

    return {
        "tire_temp_fl_c": round(tire_temps["fl"], 1),
        "tire_temp_fr_c": round(tire_temps["fr"], 1),
        "tire_temp_rl_c": round(tire_temps["rl"], 1),
        "tire_temp_rr_c": round(tire_temps["rr"], 1),
        "tire_pressure_fl_psi": round(tire_pressures["fl"], 1),
        "tire_pressure_fr_psi": round(tire_pressures["fr"], 1),
        "tire_pressure_rl_psi": round(tire_pressures["rl"], 1),
        "tire_pressure_rr_psi": round(tire_pressures["rr"], 1),
        "engine_temp_c": round(engine_temp, 1),
        "brake_temp_fl_c": round(brake_temps["fl"], 1),
        "brake_temp_fr_c": round(brake_temps["fr"], 1),
        "battery_charge_pct": round(battery_pct, 1),
        "fuel_remaining_kg": round(fuel, 1),
        "drs_active": random.random() > 0.6,
        "speed_kph": round(speed, 1),
        "throttle_pct": round(throttle, 1),
        "brake_pct": round(brake, 1),
    }
