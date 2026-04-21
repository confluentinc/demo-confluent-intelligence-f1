# Tableflow — Example Queries for Genie

## Question 1: Position Gain After Agent Recommendation

```sql
SELECT
  p.lap AS pit_lap,
  p.position AS position_at_pit,
  f.position AS final_position,
  p.position - f.position AS positions_gained
FROM pit_decisions p
JOIN pit_decisions f
  ON f.car_number = p.car_number AND f.lap = 57
WHERE p.suggestion = 'PIT NOW';
```

Expected: +5 positions (P8 → P3)

## Question 2: Tire Compound Distribution (Pie Chart)

```sql
SELECT
  d.driver,
  d.team,
  p.tire_compound_current,
  COUNT(*) AS laps
FROM pit_decisions p
JOIN drivers d ON p.car_number = d.car_number
GROUP BY d.driver, d.team, p.tire_compound_current;
```

Expected: SOFT 32 laps (56%) / MEDIUM 25 laps (44%)
