-- Job 2: Streaming Agent — Pit Strategy
-- Input: car-state (+ JOIN with race-standings for competitor context)
-- Output: pit-decisions
--
-- llm_textgen_model is pre-deployed via Terraform — no CREATE CONNECTION or
-- CREATE MODEL needed. Run the statements below sequentially in the Flink SQL
-- Workspace.

-- 1. RTCE Connection — for later when RTCE is fully enabled.
--    Competitor context is currently provided via a direct JOIN with race-standings
--    (see CREATE TABLE pit-decisions below). Uncomment once your RTCE endpoint is active.
--
-- CREATE CONNECTION `rtce-connection`
-- WITH (
--   'type' = 'MCP_SERVER',
--   'endpoint' = '<YOUR_RTCE_ENDPOINT>',
--   'transport-type' = 'STREAMABLE_HTTP'
-- );

-- 2. RTCE Tool — for later when RTCE is fully enabled.
--    Uncomment once rtce-connection is active, then add USING TOOLS `race_standings_tool`
--    to the CREATE AGENT below and replace the competitor standings JOIN with tool calls.
--
-- CREATE TOOL `race_standings_tool`
-- USING CONNECTION `rtce-connection`
-- WITH (
--   'type' = 'mcp',
--   'description' = 'Look up current race standings for any car by car_number. Returns
--                    position, gap to leader, gap to car ahead, pit stops, tire compound,
--                    and tire age laps. Use to assess undercut/overcut opportunities and
--                    check whether competitors have already pitted.'
-- );

-- 3. Pit Strategy Agent
--    Competitor standings are provided as structured text in each input message
--    (from the JOIN with race-standings in the CREATE TABLE below).
--    When RTCE is enabled: uncomment USING TOOLS and update the prompt accordingly.
CREATE AGENT `pit_strategy_agent`
USING MODEL `llm_textgen_model`
USING PROMPT 'OUTPUT FORMAT — respond with exactly these 7 labeled lines in this order. No markdown, no asterisks, no bold, plain text only.

Suggestion: [PIT NOW | PIT SOON | STAY OUT]
Condition Summary: [one sentence describing current car condition]
Race Context: [one sentence on race situation based on competitor standings in the input]
Recommended Compound: [SOFT | MEDIUM | HARD | N/A if STAY OUT]
Recommended Stint Laps: [integer expected laps on new tires | N/A if STAY OUT]
Recommended Reason: [one sentence explaining compound choice | N/A if STAY OUT]
Reasoning: [2-4 sentences full explanation of your decision]

Correct STAY OUT example:
Suggestion: STAY OUT
Condition Summary: Front-left tire temperature is nominal at 107C with 18 laps of age on SOFT compound.
Race Context: Currently P3. No competitors in top 10 have pitted yet. Leader is 8.2s ahead.
Recommended Compound: N/A
Recommended Stint Laps: N/A
Recommended Reason: N/A
Reasoning: Tire temps and pressures are within normal operating windows for a SOFT at this age. Track position P3 is strong. Pitting now would surrender 4-6 seconds and drop James behind cars currently behind us.

Correct PIT NOW example:
Suggestion: PIT NOW
Condition Summary: Front-left tire temperature anomaly at 145C, 20C above expected upper bound — failure risk imminent.
Race Context: Currently P8. P4 and P5 already pitted 3 laps ago and are pushing on fresh mediums.
Recommended Compound: MEDIUM
Recommended Stint Laps: 25
Recommended Reason: Mediums will last the remaining 25 laps and give James the pace to recover positions lost during the stop.
Reasoning: The FL anomaly flag indicates the SOFT has gone past its operating limit with blowout risk. Pitting now onto mediums avoids tire failure. Based on historical data, James averages +2.75 positions on SOFT-MEDIUM — this is his strongest strategy.

---

You are the AI pit wall strategist for River Racing at the 2026 British Grand Prix (Silverstone, 57 laps).
Driver: James River, Car #44.

DECISION RULES:

PIT NOW — pit this lap (urgent):
- anomaly_tire_temp_fl = true (sensor-detected thermal anomaly — failure risk imminent, act immediately)
CRITICAL: Tire age alone is NEVER a valid reason for PIT NOW. If anomaly_tire_temp_fl = false, your maximum recommendation is PIT SOON, not PIT NOW.

PIT SOON — pit within the next 1-3 laps (strategic):
- tire_age_laps >= 26 on SOFT compound (performance cliff approaching, positions will begin falling)
- Car directly ahead just pitted onto fresher rubber AND tire_age_laps >= 20 (undercut window closing)

STAY OUT — default recommendation when none of the above apply:
- anomaly_tire_temp_fl = false (no sensor-detected failure risk)
- Tire age is within the expected stint window (SOFT compound: tire_age_laps < 26)
- Tire temps and pressures nominal for compound type and age
- Track position is strong and pitting would surrender meaningful places

COMPETITOR CONTEXT:
Current top-10 standings are provided at the end of each input. Use them to identify:
- Which competitors have already pitted (and are now on fresher rubber)
- Who is still on old tires and likely to pit soon
- Whether James is at risk of being undercut, or has an overcut opportunity

TIRE STRATEGY at Silverstone (57-lap race):
- SOFT: High-grip compound. Optimal window is laps 1-25. Still competitive laps 26-32 with some pace loss and position drops — but no failure risk unless the anomaly sensor fires. Performance cliff begins around lap 26-28.
- MEDIUM: Balanced compound, best for a 25-30 lap second stint after a SOFT first stint. Enables clean 1-stop strategy.
- HARD: Very durable but slow. Only consider if 40+ laps remain at the second stop.
- James River historical best: SOFT first stint → MEDIUM second stint (1-stop) averages +2.75 positions over 4 prior races. Winning execution: run SOFT until the anomaly signal fires or tire_age_laps >= 26, then switch to MEDIUM and overtake on fresher rubber.
- IMPORTANT: Do NOT recommend pitting before tire_age_laps = 26 on SOFT unless anomaly_tire_temp_fl = true. The anomaly detection system is the authoritative signal for tire failure.

REMINDER: For any STAY OUT decision, write N/A for Recommended Compound, Recommended Stint Laps, and Recommended Reason.'
-- USING TOOLS `race_standings_tool`  -- uncomment when RTCE is active
WITH ('max_iterations' = '10');

-- 4. Create pit-decisions table — one record per lap, driven by car-state.
--
-- NOTE: The original competitor_grid CTE (GROUP BY lap on race-standings) was removed.
-- It caused a retract changelog (CURRENT_TIMESTAMP non-determinism error) because
-- race-standings is an upsert table, and the 22 cars' event_times spread across
-- multiple 10-second TUMBLE windows making snapshot aggregation unreliable.
-- The agent still receives full car state + position/gap context from car-state itself.
-- When RTCE is enabled, add USING TOOLS to the CREATE AGENT above for live standings.
--
-- REGEXP_EXTRACT patterns use \*{0,2} to tolerate optional markdown bold markers
-- (**Label:**) that some LLMs emit despite being instructed otherwise.
--
-- raw_response preserves the full agent output for debugging when parsed fields are null.
--
-- DEPLOYMENT ORDER: Run CREATE AGENT first (above), then start the race simulator,
-- then run this CREATE TABLE. Uses earliest-offset so it processes all race laps.

CREATE TABLE `pit-decisions`
WITH ('changelog.mode' = 'append')
AS
SELECT
  cs.car_number,
  cs.lap,
  cs.`position`,
  cs.tire_compound AS tire_compound_current,
  cs.tire_age_laps,
  cs.anomaly_tire_temp_fl,
  TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Suggestion:\*{0,2}\s*([A-Z ]+)', 1)) AS suggestion,
  TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Condition Summary:\*{0,2}\s*([^\n]+)', 1)) AS condition_summary,
  TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Race Context:\*{0,2}\s*([^\n]+)', 1)) AS race_context,
  NULLIF(TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Recommended Compound:\*{0,2}\s*([^\n]+)', 1)), 'N/A') AS recommended_tire_compound,
  CAST(NULLIF(TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Recommended Stint Laps:\*{0,2}\s*([^\n]+)', 1)), 'N/A') AS INT) AS recommended_stint_laps,
  NULLIF(TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Recommended Reason:\*{0,2}\s*([^\n]+)', 1)), 'N/A') AS recommended_reason,
  TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Reasoning:\*{0,2}\s*([\s\S]+?)$', 1)) AS reasoning,
  CAST(response AS STRING) AS raw_response
FROM `car-state` /*+ OPTIONS('scan.startup.mode'='earliest-offset') */ cs,
LATERAL TABLE(AI_RUN_AGENT(
  `pit_strategy_agent`,
  CONCAT(
    'CAR STATE — Lap ', CAST(cs.lap AS STRING), ' of 57 | Silverstone British Grand Prix\n',
    'Driver: James River (#', CAST(cs.car_number AS STRING), ') | Current Position: P', CAST(cs.`position` AS STRING), '\n',
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
    '  Laps Remaining: ', CAST(57 - cs.lap AS STRING)
  ),
  MAP['debug', 'true']
));
