# Phase-lock the race simulator & correct Flink pool selection

Status: **implemented and validated live** (2026-08-25).

## Problem

Two independent defects broke the documented lap sequence:

1. **Timing drift.** `datagen/simulator.py` paced each lap from `lap_start = time.time()`
   captured at the top of every iteration, then slept `SECONDS_PER_LAP - elapsed` at lap
   end. Any per-lap overrun (e.g. `producer.flush(timeout=5)`) pushed the next lap's
   baseline later, so laps accumulated drift and stopped lining up with Flink's 20-second
   TUMBLE windows (anchored to the Unix epoch). A window then straddled two source laps,
   producing a hybrid `car_state` row that corrupted the lap-24 anomaly / lap-25 post-stop
   story.
2. **Ignored compute-pool field.** Both Flink Statements REST payloads sent the nested
   `spec.compute_pool: {"id": ...}` shape, which Cloud silently ignored — statements ran
   on the environment's *default* pool instead of the credential card's pool.

## Fix

Leave the 20s TUMBLE SQL untouched. Phase-lock the simulator to the next 20-second
wall-clock epoch boundary and drive every lap from **absolute** deadlines
(`race_start + (lap-1)*spl`), so exactly one source lap lands per window. Correct both
REST payloads to the scalar `spec.compute_pool_id`.

## Changes

### `datagen/simulator.py`
- Added `import math` and two pure helpers: `_next_epoch_boundary(now, spl)` and
  `_lap_deadline(race_start, lap, spl)`.
- In `run_race()`: after warm-up and before the lap loop, compute
  `race_start = _next_epoch_boundary(time.time(), SECONDS_PER_LAP)` and sleep to it (this
  is the "may wait up to one lap interval before lap 1" behavior).
- `lap_start` is now `_lap_deadline(race_start, lap, spl)` (absolute); a start more than
  0.5s behind schedule logs a warning instead of drifting silently.
- End-of-lap pacing sleeps until the **next** lap's absolute deadline, not
  `SECONDS_PER_LAP - elapsed` — the core drift-elimination.
- Unchanged: standings-before-telemetry order, `_source_state_for_lap` lap-24 override,
  `telemetry.ANOMALY_LAP`, `drivers.pit_lap`, `race_script` advance/pit logic. Wall-clock
  `time.time()` retained (must track the same clock Flink TUMBLE uses; not monotonic).

### Flink REST payloads → `compute_pool_id`
- `scripts/workshop/sql_shell.py` `FlinkSession.submit()`: `"compute_pool_id": self.pool`.
- `scripts/reset.py` `drop_flink_objects()`: `"compute_pool_id": tf["compute_pool_id"]`.

### Docs
Fixed-boundary invariant added to `docs/maintainers/TECHNICAL-NOTES.md` (Race cadence)
and `CONTEXT.md`; a "may wait up to one 20s interval before lap 1" caveat added to
`docs/tracks/SELF-SERVICE.md`, `HOSTED-WORKSHOP.md`, and `STANDALONE-DEMO.md`. No SQL,
no lap-24/lap-25 claims changed.

### Tests
- `datagen/tests/test_simulator.py`: `_next_epoch_boundary` rounding, `_lap_deadline`
  constant-spacing / no-drift over a 60-lap schedule.
- `tests/test_sql_shell.py` + `tests/test_reset_flow.py`: assert each POST body carries
  `spec.compute_pool_id` and no `spec.compute_pool`.

## Verification results (2026-08-25, self-service track, 20s/lap)

- `uv run pytest`: all green. `uv run ruff check`: clean on all changed files (two
  pre-existing unrelated lint findings in `test_race_script.py` / `setup_mcp.py` remain).
- **Compute pool:** statements submitted after the fix land on the card's pool
  (`lfcp-38w5o7m`); pre-fix statements were on the env default (`lfcp-0xd5ny5`).
- **Phase-lock:** Lap 1 fired at exactly `:20` after a 16.5s alignment wait; laps 34/35
  landed at `:20`/`:40` with zero drift and no missed-deadline warnings.
- **`car_state`:** one coherent row per lap 1–29, no hybrid/duplicate/missing rows.
  Lap 24 = SOFT, `pit_stops=0`, anomaly TRUE, tire_temp 144°C (sole anomaly). Lap 25 =
  MEDIUM, `pit_stops=1`, anomaly FALSE.
- **`pit_decisions`:** STAY OUT laps 1–20, PIT SOON 21–23, PIT NOW only at lap 24,
  STAY OUT from lap 25.
