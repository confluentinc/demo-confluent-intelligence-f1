"""Generates driver_race_history_seed.sql with 198 rows (22 drivers x 9 races).

Designed correlation: drivers running SOFT-MEDIUM (1-stop) gain the most positions
on average. John Doe's record on SOFT-MEDIUM is +2.75; on other strategies, -2.4.
This matches the simulator's lap-24 anomaly-triggered 1-stop pit onto MEDIUM today.

Strategy distribution per race (22 drivers):
   6x SOFT-MEDIUM        (1-stop)  — winners, avg +2.5
   5x MEDIUM-HARD        (1-stop)  — safe,    avg +0.5
   5x SOFT-HARD          (1-stop)  — long,    avg -0.5
   3x SOFT-MEDIUM-HARD   (2-stop)  — over,    avg -0.5
   2x SOFT-MEDIUM-MEDIUM (2-stop)  — wasted,  avg -1.5
   1x SOFT-SOFT-MEDIUM   (2-stop)  — burnt,   avg -1.8

Run:   python datagen/data/generate_driver_race_history.py > datagen/data/driver_race_history_seed.sql
"""

import random

random.seed(42)

# 9 races (real F1 calendar order), British GP is race 10 (today's demo)
RACES = [
    ("bahrain_2026", "Bahrain", "2026-03-08"),
    ("saudi_arabia_2026", "Saudi Arabia", "2026-03-15"),
    ("australia_2026", "Australia", "2026-03-29"),
    ("japan_2026", "Japan", "2026-04-12"),
    ("china_2026", "China", "2026-04-26"),
    ("miami_2026", "Miami", "2026-05-10"),
    ("italy_2026", "Italy", "2026-05-24"),
    ("monaco_2026", "Monaco", "2026-05-31"),
    ("spain_2026", "Spain", "2026-06-14"),
]

# Drivers — same 22 as datagen/drivers.py and data/drivers_seed.sql.
# tier: 1=top (P1-6), 2=upper midfield (P5-12), 3=lower midfield (P10-17), 4=back (P15-22)
DRIVERS = [
    (1, "Max Verstappen", "Titan Dynamics", 1),
    (4, "Lando Norris", "Apex Motorsport", 1),
    (88, "John Doe", "River Racing", 1),  # special-cased below
    (81, "Oscar Piastri", "Apex Motorsport", 2),
    (16, "Charles Leclerc", "Scuderia Rossa", 2),
    (63, "George Russell", "Sterling GP", 2),
    (44, "Lewis Hamilton", "Scuderia Rossa", 2),
    (14, "Fernando Alonso", "Aston Verde", 2),
    (10, "Pierre Gasly", "Alpine Force", 2),
    (55, "Carlos Sainz", "Williams Heritage", 3),
    (23, "Alexander Albon", "Williams Heritage", 3),
    (30, "Liam Lawson", "Racing Bulls", 3),
    (18, "Lance Stroll", "Aston Verde", 3),
    (77, "Sophie Laurent", "River Racing", 3),
    (12, "Kimi Antonelli", "Sterling GP", 3),
    (31, "Esteban Ocon", "Haas Velocity", 3),
    (6, "Isack Hadjar", "Racing Bulls", 4),
    (87, "Oliver Bearman", "Haas Velocity", 4),
    (27, "Nico Hulkenberg", "Sauber Spirit", 4),
    (5, "Gabriel Bortoleto", "Sauber Spirit", 4),
    (22, "Yuki Tsunoda", "Titan Dynamics", 4),
    (7, "Jack Doohan", "Alpine Force", 4),
]

# John's hand-crafted arc — must match the demo narrative
JOHN_ARC = [
    # (gp_index, sequence,                start, finish)
    (0, ["SOFT", "MEDIUM"], 3, 1),  # Bahrain        +2
    (1, ["MEDIUM", "HARD"], 3, 2),  # Saudi Arabia   +1
    (2, ["SOFT", "SOFT", "MEDIUM"], 5, 9),  # Australia      -4
    (3, ["SOFT", "HARD"], 4, 6),  # Japan          -2
    (4, ["SOFT", "MEDIUM"], 6, 3),  # China          +3
    (5, ["SOFT", "MEDIUM", "HARD"], 2, 5),  # Miami          -3
    (6, ["SOFT", "MEDIUM"], 4, 2),  # Italy          +2
    (7, ["SOFT", "MEDIUM", "HARD"], 3, 7),  # Monaco         -4
    (8, ["SOFT", "MEDIUM"], 5, 1),  # Spain          +4
]

# Strategy templates: (name, stints, position_delta_bias)
# delta_bias is a (mean, stddev) for how the strategy shifts finishing position
# relative to starting grid. Negative delta = gained positions.
STRATEGIES = [
    ("SOFT-MEDIUM", ["SOFT", "MEDIUM"], (-2.5, 1.5)),  # winner
    ("MEDIUM-HARD", ["MEDIUM", "HARD"], (-0.5, 1.5)),
    ("SOFT-HARD", ["SOFT", "HARD"], (0.5, 1.5)),
    ("SOFT-MEDIUM-HARD", ["SOFT", "MEDIUM", "HARD"], (0.5, 1.8)),
    ("SOFT-MEDIUM-MEDIUM", ["SOFT", "MEDIUM", "MEDIUM"], (1.5, 1.8)),
    ("SOFT-SOFT-MEDIUM", ["SOFT", "SOFT", "MEDIUM"], (1.8, 2.0)),
]

# Per-race strategy quotas (must sum to 22)
STRATEGY_QUOTA = {
    "SOFT-MEDIUM": 6,
    "MEDIUM-HARD": 5,
    "SOFT-HARD": 5,
    "SOFT-MEDIUM-HARD": 3,
    "SOFT-MEDIUM-MEDIUM": 2,
    "SOFT-SOFT-MEDIUM": 1,
}
assert sum(STRATEGY_QUOTA.values()) == 22

TIER_GRID_RANGE = {
    1: (1, 6),  # top tier qualifies P1-6
    2: (4, 12),
    3: (10, 17),
    4: (15, 22),
}


def quoted(s: str) -> str:
    """SQL-escape a string by doubling single quotes."""
    return "'" + s.replace("'", "''") + "'"


def build_grid(race_idx: int) -> dict:
    """Return {car_number: starting_grid_position} for one race.

    John gets his hand-crafted starting position. Others are biased by tier.
    """
    rng = random.Random(1000 + race_idx)
    john_start = JOHN_ARC[race_idx][2]
    grid = {88: john_start}
    used_positions = {john_start}

    # Sort drivers by tier; within tier, randomize per race
    others = [d for d in DRIVERS if d[0] != 88]
    rng.shuffle(others)
    others.sort(key=lambda d: d[3])

    for car_number, _, _, tier in others:
        lo, hi = TIER_GRID_RANGE[tier]
        # Find an available position in tier range
        candidates = [p for p in range(lo, hi + 1) if p not in used_positions]
        if not candidates:
            # Fall back to any free position
            candidates = [p for p in range(1, 23) if p not in used_positions]
        pos = rng.choice(candidates)
        grid[car_number] = pos
        used_positions.add(pos)

    return grid


def assign_strategies(race_idx: int) -> dict:
    """Return {car_number: strategy_name} for one race honoring the quota.

    John's strategy is fixed by JOHN_ARC. Others are biased so:
      - top tier prefers SOFT-MEDIUM (the winning pattern)
      - back tier prefers SOFT-MEDIUM-MEDIUM / SOFT-SOFT-MEDIUM (losing patterns)
    """
    rng = random.Random(2000 + race_idx)

    john_seq = JOHN_ARC[race_idx][1]
    john_strategy = "-".join(john_seq)
    strategies = {88: john_strategy}

    # Remaining quota after John
    quota = dict(STRATEGY_QUOTA)
    quota[john_strategy] -= 1
    assert quota[john_strategy] >= 0

    # Strategy preferences by tier (weighted draws)
    tier_weights = {
        1: {
            "SOFT-MEDIUM": 4,
            "MEDIUM-HARD": 3,
            "SOFT-HARD": 2,
            "SOFT-MEDIUM-HARD": 1,
            "SOFT-MEDIUM-MEDIUM": 0,
            "SOFT-SOFT-MEDIUM": 0,
        },
        2: {
            "SOFT-MEDIUM": 3,
            "MEDIUM-HARD": 3,
            "SOFT-HARD": 2,
            "SOFT-MEDIUM-HARD": 2,
            "SOFT-MEDIUM-MEDIUM": 1,
            "SOFT-SOFT-MEDIUM": 0,
        },
        3: {
            "SOFT-MEDIUM": 2,
            "MEDIUM-HARD": 2,
            "SOFT-HARD": 3,
            "SOFT-MEDIUM-HARD": 2,
            "SOFT-MEDIUM-MEDIUM": 2,
            "SOFT-SOFT-MEDIUM": 1,
        },
        4: {
            "SOFT-MEDIUM": 1,
            "MEDIUM-HARD": 1,
            "SOFT-HARD": 2,
            "SOFT-MEDIUM-HARD": 2,
            "SOFT-MEDIUM-MEDIUM": 3,
            "SOFT-SOFT-MEDIUM": 2,
        },
    }

    others = [d for d in DRIVERS if d[0] != 88]
    rng.shuffle(others)
    # Process top-tier first so they get first pick of winning strategies
    others.sort(key=lambda d: d[3])

    for car_number, _, _, tier in others:
        # Build available choices weighted by tier preference, filtered by quota
        choices = []
        weights = []
        for strat, q in quota.items():
            if q > 0:
                w = tier_weights[tier][strat]
                if w > 0:
                    choices.append(strat)
                    weights.append(w)
        if not choices:
            # Fallback to any strategy with remaining quota
            choices = [s for s, q in quota.items() if q > 0]
            weights = [1] * len(choices)
        strat = rng.choices(choices, weights=weights, k=1)[0]
        strategies[car_number] = strat
        quota[strat] -= 1

    assert all(q == 0 for q in quota.values()), f"Quota leftover: {quota}"
    return strategies


def compute_finish(grid: dict, strategies: dict, race_idx: int) -> dict:
    """Return {car_number: finishing_pos}."""
    rng = random.Random(3000 + race_idx)

    # John is fixed
    john_finish = JOHN_ARC[race_idx][3]
    finishes = {88: john_finish}

    # Compute "race score" for non-John drivers
    # Lower score = better finish. Score = starting_grid + strategy_delta + tier_bias + noise
    scores = []
    for car_number, _driver, _team, tier in DRIVERS:
        if car_number == 88:
            continue
        start = grid[car_number]
        strat = strategies[car_number]
        mean_delta, std_delta = next(s[2] for s in STRATEGIES if s[0] == strat)
        delta = rng.gauss(mean_delta, std_delta)
        # Slight tier bias (top tier holds positions better)
        tier_bias = (tier - 2.5) * 0.3
        noise = rng.gauss(0, 0.5)
        scores.append((car_number, start + delta + tier_bias + noise))

    # Sort by score, assign positions 1-22 skipping John's slot
    scores.sort(key=lambda x: x[1])
    used = {john_finish}
    pos = 1
    for car_number, _ in scores:
        while pos in used:
            pos += 1
        finishes[car_number] = pos
        used.add(pos)
        pos += 1

    return finishes


def build_race_rows(race_idx: int) -> list[dict]:
    """Return the historical rows for one race as structured dicts.

    This is the single source of truth for the seed data. ``main()`` renders
    these as Postgres INSERT VALUES; the self-service seeder
    (``scripts/selfservice/seed.py``) renders the same rows as a bounded Flink
    INSERT so the ``driver_race_history`` table can be populated without a
    Postgres/CDC pipeline.
    """
    race_id, gp_name, race_date = RACES[race_idx]
    grid = build_grid(race_idx)
    strategies = assign_strategies(race_idx)
    finishes = compute_finish(grid, strategies, race_idx)

    rows = []
    for car_number, driver, team, _tier in DRIVERS:
        start = grid[car_number]
        finish = finishes[car_number]
        strat_name = strategies[car_number]
        stints = next(s[1] for s in STRATEGIES if s[0] == strat_name)
        rows.append(
            {
                "race_id": race_id,
                "gp_name": gp_name,
                "race_date": race_date,
                "car_number": car_number,
                "driver": driver,
                "team": team,
                "starting_grid": start,
                "finishing_pos": finish,
                "positions_gained": start - finish,
                "pit_stops": len(stints) - 1,
                "stint_1_tire": stints[0],
                "stint_2_tire": stints[1],
                "stint_3_tire": stints[2] if len(stints) > 2 else "n/a",
            }
        )
    return rows


def build_all_rows() -> list[dict]:
    """Return all 198 historical rows (22 drivers x 9 races) as dicts."""
    rows: list[dict] = []
    for race_idx in range(len(RACES)):
        rows.extend(build_race_rows(race_idx))
    return rows


def emit_race(race_idx: int) -> list[str]:
    """Return list of Postgres INSERT VALUES tuples for one race."""
    return [
        f"  ({quoted(r['race_id'])}, {quoted(r['gp_name'])}, DATE {quoted(r['race_date'])}, "
        f"{r['car_number']}, {quoted(r['driver'])}, {quoted(r['team'])}, "
        f"{r['starting_grid']}, {r['finishing_pos']}, {r['positions_gained']}, {r['pit_stops']}, "
        f"{quoted(r['stint_1_tire'])}, {quoted(r['stint_2_tire'])}, {quoted(r['stint_3_tire'])})"
        for r in build_race_rows(race_idx)
    ]


def main() -> None:
    print("-- Auto-generated by datagen/data/generate_driver_race_history.py — do not hand-edit.")
    print("-- Run the script to regenerate. Seed RNG=42 for reproducibility.")
    print()
    print("CREATE TABLE driver_race_history (")
    print("  race_id           VARCHAR(50)  NOT NULL,")
    print("  gp_name           VARCHAR(50)  NOT NULL,")
    print("  race_date         DATE         NOT NULL,")
    print("  car_number        INT          NOT NULL,")
    print("  driver            VARCHAR(100) NOT NULL,")
    print("  team              VARCHAR(100) NOT NULL,")
    print("  starting_grid     INT          NOT NULL,")
    print("  finishing_pos     INT          NOT NULL,")
    print("  positions_gained  INT          NOT NULL,")
    print("  pit_stops         INT          NOT NULL CHECK (pit_stops BETWEEN 1 AND 2),")
    print("  stint_1_tire      VARCHAR(10)  NOT NULL,")
    print("  stint_2_tire      VARCHAR(10)  NOT NULL,")
    print("  stint_3_tire      VARCHAR(10)  NOT NULL DEFAULT 'n/a',")
    print("  PRIMARY KEY (race_id, car_number)")
    print(");")
    print()
    print("CREATE INDEX idx_driver_race_history_car_number ON driver_race_history(car_number);")
    print("CREATE INDEX idx_driver_race_history_driver ON driver_race_history(driver);")
    print()
    print("INSERT INTO driver_race_history")
    print("  (race_id, gp_name, race_date, car_number, driver, team,")
    print("   starting_grid, finishing_pos, positions_gained, pit_stops,")
    print("   stint_1_tire, stint_2_tire, stint_3_tire)")
    print("VALUES")
    all_rows = []
    for race_idx in range(len(RACES)):
        all_rows.extend(emit_race(race_idx))
    print(",\n".join(all_rows) + ";")


if __name__ == "__main__":
    main()
