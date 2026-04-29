# Databricks Genie Setup Guide — F1 Demo

## Prerequisites

- Databricks workspace with Unity Catalog
- Tableflow tables materialized (`pit_decisions`, `driver_race_history`)
- Genie enabled on workspace

## Create Genie Space

1. Go to **Databricks** → **Genie**
2. Click **Create Space**
3. Name: `F1 Pit Strategy Analytics`
4. Add tables:
   - `f1_demo.pit_decisions`
   - `f1_demo.driver_race_history`
5. Add description: *"Analyze F1 pit strategy decisions made by River Racing's AI agent during the Silverstone Grand Prix, and validate them against historical season-to-date race results."*

## Demo Questions

### Question 1: Position Gain (Tabular)

**Ask:** *"How many positions did we gain after following the agent's recommendation?"*

**Expected SQL:**
```sql
SELECT
  p.lap AS pit_lap,
  p.position AS position_at_pit,
  f.position AS final_position,
  p.position - f.position AS positions_gained
FROM pit_decisions p
JOIN pit_decisions f
  ON f.car_number = p.car_number AND f.lap = 57
WHERE p.suggestion = 'PIT NOW'
```

**Expected result:** +5 positions (P8 at pit, P3 at finish)

### Question 2: Tire Strategy Correlation (Bar Chart)

**Ask:** *"For each tire sequence, what's the average positions gained across all races?"*

**Expected SQL:**
```sql
SELECT
  CONCAT(stint_1_tire, '-', stint_2_tire,
         CASE WHEN stint_3_tire = 'n/a' THEN '' ELSE '-' || stint_3_tire END) AS tire_sequence,
  COUNT(*) AS races,
  ROUND(AVG(positions_gained), 2) AS avg_positions_gained
FROM driver_race_history
GROUP BY tire_sequence
ORDER BY avg_positions_gained DESC
```

**Expected result:**

| tire_sequence | races | avg_positions_gained |
|---|---|---|
| SOFT-MEDIUM | 54 | +1.52 |
| MEDIUM-HARD | 45 | +0.38 |
| SOFT-HARD | 45 | -0.49 |
| SOFT-MEDIUM-HARD | 27 | -0.56 |
| SOFT-MEDIUM-MEDIUM | 18 | -1.67 |
| SOFT-SOFT-MEDIUM | 9 | -3.56 |

**Narrative:** `SOFT-MEDIUM` (1-stop) is the winning strategy across the field. The agent's lap-33 MEDIUM pit recommendation today follows this proven pattern.

### Question 3: James River's Per-Strategy Record

**Ask:** *"What's James River's average position gain by tire strategy?"*

**Expected SQL:**
```sql
SELECT
  CONCAT(stint_1_tire, '-', stint_2_tire,
         CASE WHEN stint_3_tire = 'n/a' THEN '' ELSE '-' || stint_3_tire END) AS tire_sequence,
  COUNT(*) AS races,
  ROUND(AVG(positions_gained), 2) AS avg_positions_gained
FROM driver_race_history
WHERE driver = 'James River'
GROUP BY tire_sequence
ORDER BY avg_positions_gained DESC
```

**Expected result:** `SOFT-MEDIUM` averages +2.75 over 4 races; every other strategy averages a loss. Validates the agent's call.

## Tips

- Genie uses natural language — no SQL needed from the presenter
- The bar chart renders automatically when the result has category + numeric columns
- Both questions use data from Tableflow (no ETL, no notebooks)
