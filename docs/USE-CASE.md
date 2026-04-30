# Use Case — F1 Pit Wall AI Demo

River Racing is an ambitious F1 team that wants to use AI to make smarter pit wall decisions in real time. During the Silverstone Grand Prix, three live data feeds stream into Confluent Cloud — car sensor telemetry, FIA race standings, and historical driver data — and are transformed into an always-up-to-date view of their car's condition and competitive context. An AI Pit Strategy Agent monitors every sensor for anomalies, evaluates race position, and recommends whether to pit now, pit soon, or stay out, explaining its reasoning in natural language.

## Data Sources

| Source | Path |
|--------|------|
| Car telemetry | Race simulator → Direct to Kafka (`car_telemetry`) |
| Race standings | FIA timing feed → IBM MQ → MQ Connector → `race_standings` |
| Driver history | Postgres → CDC Debezium → `driver_race_history` |

## Pipeline Steps

**Step 1: Car State Enrichment + Anomaly Detection**
Car sensor data (tire temps, pressures, engine, brake, battery, fuel) is aggregated in 10-second tumbling windows and passed through `AI_DETECT_ANOMALIES` on `tire_temp_fl_c`. The result is enriched with live race standings via a temporal join to produce a comprehensive `car_state` stream.

**Step 2: AI Pit Strategy Agent**
The `car_state` stream feeds a Streaming Agent that evaluates every lap. It assesses anomaly flags, tire condition, and race position, and produces a pit suggestion (`PIT NOW` / `PIT SOON` / `STAY OUT`) with recommended tire compound and reasoning, written to `pit_decisions`.

**Tableflow:** Enabled on `pit_decisions` and `driver_race_history` → Delta Lake → Databricks Genie (Unity Catalog) for post-race analytics.

---

## Race Script — Sean Falconer (#44)

| Laps | Position | Tire | Anomaly | Suggestion | What's Happening |
|------|----------|------|---------|------------|------------------|
| 1–17 | P3 | SOFT (fresh) | None | STAY OUT | Competitive, stable, good pace |
| 18–25 | P3 → P1 | SOFT (aging) | None | STAY OUT | Leaders pit — Sean briefly leads the race |
| 26–31 | P1 → P8 | SOFT (critical) | None | PIT SOON | Tire cliff bites — falling 5 places in 6 laps |
| **32** | **P8** | **SOFT (dead)** | **tire_temp_fl = true** | **PIT NOW** | **Anomaly fires. Agent recommends pit.** |
| 33 | P12 | In pit lane | None | STAY OUT | Pit stop. Fresh MEDIUMs. |
| 34–54 | P12 → P5 | MEDIUM (fresh) | None | STAY OUT | Fastest car on track, steady climb |
| 55–57 | P5 → P2 | MEDIUM | None | STAY OUT | Leaders' MEDIUMs past cliff — Sean jumps ahead |

**Net result: P8 → P2 = +6 positions gained from the moment the agent made the call.**

Race configuration: 57 laps, 10 seconds/lap simulated, ~9.5 min total, 22 cars, pit stop ~20s time loss.

## Databricks Genie Expected Answers

- **"How many positions did we gain?"** → +6 (P8 at lap 32 → P2 at finish)
- **"What's Sean Falconer's average position gain per tire sequence?"** → `SOFT-MEDIUM` averages **+2.75** over 4 prior races; all other strategies average **−2.4** over 5 races

The SOFT-MEDIUM correlation validates the agent's lap-33 MEDIUM recommendation as following River's most successful historical pattern.

## Historical Data

198 rows in `driver_race_history` (22 drivers × 9 prior GPs). Seeded from `data/driver_race_history_seed.sql` with `random.seed(42)` for determinism. Postgres composite PK: `(race_id, car_number)`.
