# F1 Pit Wall AI use case

River Racing wants to make better pit-wall decisions while the race is still unfolding. During the Silverstone Grand Prix, car telemetry and race standings stream into Confluent Cloud. Historical driver data supplies context from nine earlier races. Flink SQL combines those inputs, detects a front-left tire anomaly, and calls an AI pit-strategy agent once per lap.

## Data Sources

| Source | Path |
|--------|------|
| Car telemetry | Race simulator → Kafka (`car_telemetry`) |
| Race standings | Race simulator → Kafka, keyed by `car_number` (`race_standings`) |
| Driver history | Postgres CDC in the hosted and standalone tracks; bounded Flink seed in self-service (`driver_race_history`) |

## Pipeline Steps

**Step 1: Car-state enrichment and anomaly detection**

Flink SQL joins telemetry to the current standings, groups the result into one 20-second window per lap, and runs `ML_DETECT_ANOMALIES` on `tire_temp_fl_c`. The result lands in `car_state`.

**Step 2: AI pit-strategy agent**

The Streaming Agent evaluates every `car_state` row. It writes `PIT NOW`, `PIT SOON`, or `STAY OUT` to `pit_decisions`, along with a tire recommendation and its reasoning.

---

## Race script: John Doe (#88)

| Laps | Position | Tire | Anomaly | Suggestion | What's Happening |
|------|----------|------|---------|------------|------------------|
| 1–14 | P3 | SOFT | None | STAY OUT | John holds his starting position. |
| 15–19 | P3 → P1 | SOFT | None | STAY OUT | The early stops briefly put John in the lead. |
| 20 | P1 | SOFT | None | STAY OUT | The soft tires are still within the normal decision range. |
| 21–23 | P1 → P8 | SOFT | None | PIT SOON | The aging softs need a stop soon. |
| **24** | **P8** | **SOFT** | **`tire_temp_fl = true`** | **PIT NOW** | **The front-left anomaly triggers the scheduled stop.** |
| 25 | P14 | MEDIUM | None | STAY OUT | The fresh MEDIUM stint is visible after the stop. |
| 26–60 | P14 → P1–P2 | MEDIUM | None | STAY OUT | John passes cars running older medium tires. |

The workshop story ends with John recovering from P8 at the agent's call to P1–P2 at the flag.

Race configuration: 60 laps, 20 seconds per lap, 22 cars, and about 20 seconds lost during a pit stop. The hosted simulator loops continuously.

## Historical data

198 rows in `driver_race_history` (22 drivers × 9 prior GPs). Seeded from `datagen/data/driver_race_history_seed.sql` with `random.seed(42)` for determinism. Postgres composite PK: `(race_id, car_number)`.
