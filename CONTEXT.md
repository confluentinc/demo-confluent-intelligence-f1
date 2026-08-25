# Project context

## Race event sequencing

Car #88's scheduled stop is internally applied on lap 24, but the source must publish
the pre-stop SOFT state and front-left anomaly for that lap. The post-stop MEDIUM state
begins on lap 25. This lets the anomaly trigger the only `PIT NOW` decision instead of
being hidden by the stop. `datagen/simulator.py` and `scripts/pitwall/mock.py` share
the same publication helper; keep them aligned with the agent contract: `PIT SOON` on
laps 21–23, `PIT NOW` only on the lap-24 anomaly, then `STAY OUT` after the stop.

The simulator also phase-locks lap 1 to the next 20-second wall-clock epoch boundary
before emitting anything, then schedules every later lap from an absolute deadline
(`race_start + (lap-1)*20s`) instead of accumulating per-lap sleeps. This keeps exactly
one source lap per Flink 20-second `TUMBLE` window with no cumulative drift, but means
`f1-race` can wait up to one full lap interval (~20s) before lap 1 appears.
