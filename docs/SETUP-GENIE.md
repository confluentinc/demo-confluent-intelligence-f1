# Databricks Genie Setup Guide — F1 Demo

## Prerequisites

- Databricks workspace with Unity Catalog
- Tableflow tables materialized (`pit_decisions`, `drivers`)
- Genie enabled on workspace

## Create Genie Space

1. Go to **Databricks** → **Genie**
2. Click **Create Space**
3. Name: `F1 Pit Strategy Analytics`
4. Add tables:
   - `f1_demo.pit_decisions`
   - `f1_demo.drivers`
5. Add description: *"Analyze F1 pit strategy decisions made by River Racing's AI agent during the Silverstone Grand Prix."*

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

### Question 2: Tire Strategy (Pie Chart)

**Ask:** *"What percentage of the race did our driver spend on each tire compound?"*

**Expected SQL:**
```sql
SELECT
  d.driver,
  d.team,
  p.tire_compound_current,
  COUNT(*) AS laps
FROM pit_decisions p
JOIN drivers d ON p.car_number = d.car_number
GROUP BY d.driver, d.team, p.tire_compound_current
```

**Expected result:** Pie chart — SOFT 56% (32 laps) / MEDIUM 44% (25 laps)

## Tips

- Genie uses natural language — no SQL needed from the presenter
- The pie chart renders automatically when the result has category + count columns
- Both questions use data from Tableflow (no ETL, no notebooks)
