# LAB 4 — Streaming Agent: Pit Decisions (deprecated)

> Retained for reference. Use the canonical [`Walkthrough.md`](../../Walkthrough.md).

## Overview

Now the payoff: an **AI Streaming Agent** evaluates every `car_state` row — one per
10-second window — and recommends `PIT NOW` / `PIT SOON` / `STAY OUT`, with a
recommended compound and natural-language reasoning. The agent uses the
pre-deployed `llm_textgen_model` (AWS Bedrock / Claude) — no connection setup
required.

### What you'll accomplish

1. Create the `pit_strategy_agent`
2. Run the agent over `car_state` to produce `pit_decisions`
3. Watch the agent call `PIT NOW` at the anomaly

### Prerequisites

[LAB 3](LAB3-stream-processing-deprecated.md) — `car_state` is running and shows the
lap-32 anomaly.

## Steps

### Step 1: Create the agent

The agent's full prompt lives in
[`demo-reference/streaming_agent_create_agent.sql`](../../demo-reference/streaming_agent_create_agent.sql).
Open it and paste the whole file into a workspace cell — comment header and
all — as one statement, then run it.

The prompt pins the agent to a strict decision algorithm:

```
Step 1: If anomaly_tire_temp_fl = true        → PIT NOW
Step 2: Else if SOFT AND tire_age_laps >= 26  → PIT SOON
Step 3: Else                                  → STAY OUT
```

Flink SQL computes the canonical suggestion from those rules, while the LLM
writes the human-readable **condition summary**, **race context**, **recommended
compound/stint**, and **reasoning** for each window. The computed suggestion is
also sent to the model so its prose stays aligned.

Confirm it was created:

```sql
SHOW AGENTS;
```

### Step 2: Produce `pit_decisions`

Run the second statement, from
[`demo-reference/streaming_agent_pit_decisions.sql`](../../demo-reference/streaming_agent_pit_decisions.sql).
It formats each `car_state` row into a prompt, calls `AI_RUN_AGENT`, and parses
the agent's labeled response into columns:

```sql
CREATE TABLE `pit_decisions`
WITH ('changelog.mode' = 'append')
AS
SELECT
  cs.car_number,
  cs.lap,
  cs.`position`,
  cs.tire_compound AS tire_compound_current,
  cs.tire_age_laps,
  cs.anomaly_tire_temp_fl,
  CASE
    WHEN cs.anomaly_tire_temp_fl THEN 'PIT NOW'
    WHEN cs.tire_compound = 'SOFT' AND cs.tire_age_laps >= 26 THEN 'PIT SOON'
    ELSE 'STAY OUT'
  END AS suggestion,
  TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Condition Summary:\*{0,2}\s*([^\n]+)', 1)) AS condition_summary,
  TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Race Context:\*{0,2}\s*([^\n]+)', 1)) AS race_context,
  NULLIF(TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Recommended Compound:\*{0,2}\s*([^\n]+)', 1)), 'N/A') AS recommended_tire_compound,
  CAST(NULLIF(TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Recommended Stint Laps:\*{0,2}\s*([^\n]+)', 1)), 'N/A') AS INT) AS recommended_stint_laps,
  NULLIF(TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Recommended Reason:\*{0,2}\s*([^\n]+)', 1)), 'N/A') AS recommended_reason,
  TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Reasoning:\*{0,2}\s*([\s\S]+?)$', 1)) AS reasoning,
  CAST(response AS STRING) AS raw_response
FROM `car_state` /*+ OPTIONS('scan.startup.mode'='earliest-offset') */ cs,
LATERAL TABLE(AI_RUN_AGENT(
  `pit_strategy_agent`,
  CONCAT(
    'CAR STATE — Lap ', CAST(cs.lap AS STRING), ' of 60 | Silverstone British Grand Prix\n',
    'Driver: John Doe (#', CAST(cs.car_number AS STRING), ') | Current Position: P', CAST(cs.`position` AS STRING), '\n',
    'REQUIRED SUGGESTION — copy exactly: ',
    CASE
      WHEN cs.anomaly_tire_temp_fl THEN 'PIT NOW'
      WHEN cs.tire_compound = 'SOFT' AND cs.tire_age_laps >= 26 THEN 'PIT SOON'
      ELSE 'STAY OUT'
    END, '\n',
    '\nTIRE DATA:\n',
    '  Compound: ', cs.tire_compound, ' | Age: ', CAST(cs.tire_age_laps AS STRING), ' laps\n',
    '  FL Temp: ', CAST(ROUND(cs.tire_temp_fl_c, 1) AS STRING), 'C',
    '  FR: ', CAST(ROUND(cs.tire_temp_fr_c, 1) AS STRING), 'C',
    '  RL: ', CAST(ROUND(cs.tire_temp_rl_c, 1) AS STRING), 'C',
    '  RR: ', CAST(ROUND(cs.tire_temp_rr_c, 1) AS STRING), 'C\n',
    '  FL Pressure: ', CAST(ROUND(cs.tire_pressure_fl_psi, 1) AS STRING), 'psi',
    '  FR: ', CAST(ROUND(cs.tire_pressure_fr_psi, 1) AS STRING), 'psi',
    '  RL: ', CAST(ROUND(cs.tire_pressure_rl_psi, 1) AS STRING), 'psi',
    '  RR: ', CAST(ROUND(cs.tire_pressure_rr_psi, 1) AS STRING), 'psi\n',
    '  FL Tire Anomaly Detected: ', CAST(cs.anomaly_tire_temp_fl AS STRING), '\n',
    '\nCAR SYSTEMS:\n',
    '  Engine Temp: ', CAST(ROUND(cs.engine_temp_c, 1) AS STRING), 'C',
    '  Brake FL: ', CAST(ROUND(cs.brake_temp_fl_c, 1) AS STRING), 'C',
    '  Brake FR: ', CAST(ROUND(cs.brake_temp_fr_c, 1) AS STRING), 'C\n',
    '  Battery: ', CAST(ROUND(cs.battery_charge_pct, 1) AS STRING), '%',
    '  Fuel Remaining: ', CAST(ROUND(cs.fuel_remaining_kg, 1) AS STRING), 'kg\n',
    '\nRACE CONTEXT:\n',
    '  Gap to Leader: ', CAST(ROUND(cs.gap_to_leader_sec, 2) AS STRING), 's',
    '  Gap to Car Ahead: ', CAST(ROUND(cs.gap_to_ahead_sec, 2) AS STRING), 's\n',
    '  Pit Stops Taken: ', CAST(cs.pit_stops AS STRING), '\n',
    '  Laps Remaining: ', CAST(60 - cs.lap AS STRING)
  ),
  MAP['debug', 'true']
));
```

> `earliest-offset` makes the agent process every lap already in `car_state`,
> even laps that happened before you started this statement.

### What to expect

| Lap | Position | Suggestion | What's happening |
|-----|----------|-----------|------------------|
| 1–17 | P3 | STAY OUT | Competitive, stable |
| 18–25 | P3 → P1 | STAY OUT | Leaders pit — John briefly leads |
| 26–31 | P1 → P8 | PIT SOON | Tire cliff bites |
| **32** | **P8** | **PIT NOW** | **Front-left anomaly at 145°C — the key moment** |
| 33 | P12 | STAY OUT | Fresh MEDIUMs |
| 34–60 | P12 → P2 | STAY OUT | Fastest car on track, climbs back |

**Net result: P8 at the agent's call → P2 at finish = +6 positions.**

> 🏁 **Check your Pit Wall.** The **AI PIT STRATEGIST** panel has now unlocked.
> Watch the agent's calls stream into the decision feed and the banner flip to a
> flashing red **PIT NOW** at lap 32 — the full pipeline you built, live in the
> browser. (Run a fresh race with your instructor to watch the whole arc unfold.)

## Conclusion

You've built an end-to-end real-time AI pit strategist. Next, put that live feed
in front of a business user: build a social-media agent in
[LAB 5 — Social agent](LAB5-social-media-agent-deprecated.md).
