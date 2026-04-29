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
- anomaly_tire_temp_fl = true (tire anomaly detected, failure risk imminent — act immediately)
- tire_age_laps > 35 on SOFT compound (tires are past the cliff, no pace recoverable)

PIT SOON — pit within the next 1-3 laps (strategic):
- tire_age_laps > 28 on SOFT with temperatures rising and positions falling
- Car directly ahead just pitted onto fresher rubber (undercut window closing)
- Tire temperatures consistently 10C above expected range for compound and age

STAY OUT — continue on current tires:
- Tire temps and pressures are nominal for compound type and age
- Tire age is within the expected stint window
- Current track position is strong and pitting would surrender too many places

COMPETITOR CONTEXT:
Current top-10 standings are provided at the end of each input. Use them to identify:
- Which competitors have already pitted (and are now on fresher rubber)
- Who is still on old tires and likely to pit soon
- Whether James is at risk of being undercut, or has an overcut opportunity

TIRE STRATEGY at Silverstone:
- SOFT: fast but degrades, ideal as a first stint (~20-30 laps)
- MEDIUM: balanced choice, best after a SOFT first stint (~25-35 laps), allows 1-stop strategy
- HARD: very durable but slow, only consider if more than 40 laps remain at the second stop
- James River historical best: SOFT then MEDIUM (1-stop) averages +2.75 positions gained over 4 prior races

REMINDER: For any STAY OUT decision, write N/A for Recommended Compound, Recommended Stint Laps, and Recommended Reason.'
-- USING TOOLS `race_standings_tool`  -- uncomment when RTCE is active
WITH ('max_iterations' = '10');

-- 4. Create pit-decisions table — one record per lap, driven by car-state.
--
-- competitor_grid aggregates the top-10 race-standings rows per lap into a single
-- string that is passed to the agent in lieu of an RTCE tool call.
--
-- REGEXP_EXTRACT patterns use \*{0,2} around each label to tolerate optional
-- markdown bold markers (**Label:**) that some LLMs emit despite being instructed
-- otherwise — matching the error-tolerant parsing pattern from Lab4.
--
-- raw_response preserves the full agent output for debugging when parsed fields are null.

SET 'sql.state-ttl' = '1 d';

CREATE TABLE `pit-decisions` (
  PRIMARY KEY (car_number) NOT ENFORCED
)
WITH ('changelog.mode' = 'append')
AS
WITH competitor_grid AS (
  SELECT
    lap,
    LISTAGG(
      CONCAT(
        'P', CAST(`position` AS STRING), ': Car #', CAST(car_number AS STRING),
        ' (', driver, ') — ', tire_compound, ' age=', CAST(tire_age_laps AS STRING),
        ' laps, ', CAST(pit_stops AS STRING), ' stop(s)',
        CASE WHEN in_pit_lane THEN ' [IN PIT LANE]' ELSE '' END
      ),
      '\n'
    ) AS standings_top10
  FROM `race-standings`
  WHERE `position` <= 10
  GROUP BY lap
)
SELECT
  cs.car_number,
  cs.lap,
  cs.position,
  cs.tire_compound AS tire_compound_current,
  TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Suggestion:\*{0,2}\s*([A-Z ]+)', 1)) AS suggestion,
  TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Condition Summary:\*{0,2}\s*([^\n]+)', 1)) AS condition_summary,
  TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Race Context:\*{0,2}\s*([^\n]+)', 1)) AS race_context,
  NULLIF(TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Recommended Compound:\*{0,2}\s*([^\n]+)', 1)), 'N/A') AS recommended_tire_compound,
  CAST(NULLIF(TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Recommended Stint Laps:\*{0,2}\s*([^\n]+)', 1)), 'N/A') AS INT) AS recommended_stint_laps,
  NULLIF(TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Recommended Reason:\*{0,2}\s*([^\n]+)', 1)), 'N/A') AS recommended_reason,
  TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Reasoning:\*{0,2}\s*([\s\S]+?)$', 1)) AS reasoning,
  CAST(CURRENT_TIMESTAMP AS STRING) AS `timestamp`,
  CAST(response AS STRING) AS raw_response
FROM `car-state` cs
JOIN competitor_grid cg ON cs.lap = cg.lap,
LATERAL TABLE(AI_RUN_AGENT(
  `pit_strategy_agent`,
  CONCAT(
    'CAR STATE — Lap ', CAST(cs.lap AS STRING), ' of 57 | Silverstone British Grand Prix\n',
    'Driver: James River (#', CAST(cs.car_number AS STRING), ') | Current Position: P', CAST(cs.position AS STRING), '\n',
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
    '  Laps Remaining: ', CAST(57 - cs.lap AS STRING), '\n',
    '\nCOMPETITOR STANDINGS (Top 10):\n', cg.standings_top10
  ),
  MAP['debug', 'true']
));
