-- Job 2: Streaming Agent — Pit Strategy
-- Created via Flink SQL during the demo with MCP assistance
-- Input: car-state
-- Output: pit-decisions

-- 1. AI Connection
CREATE CONNECTION ai_connection
  WITH ('type' = 'openai', 'endpoint' = '...', 'api-key' = '...');

-- 2. Model
CREATE MODEL pit_strategy_model
  USING CONNECTION ai_connection;

-- 3. RTCE Connection (for competitor standings)
CREATE CONNECTION rtce_connection
  WITH ('type' = 'mcp_server', 'endpoint' = '...', 'transport-type' = 'STREAMABLE_HTTP');

-- 4. RTCE Tool
CREATE TOOL race_standings_tool
  USING CONNECTION rtce_connection
  WITH (
    'type' = 'mcp',
    'description' = 'Look up current race standings for any car by car_number. Returns position, gap, pit stops, tire compound, and tire age. Use this to check competitor positions and pit status when making strategy decisions.'
  );

-- 5. Agent
CREATE AGENT pit_strategy_agent
  USING MODEL pit_strategy_model
  USING PROMPT 'You are an F1 pit wall strategist for River Racing. Analyze car state data and recommend pit strategy.

For each lap, evaluate:
1. Anomaly flags - any sensor showing anomalous behavior
2. Tire condition - compound, age, temperatures
3. Race position and gaps to competitors
4. Use the race_standings_tool to check what competitors are doing

Respond with:
- suggestion: PIT NOW, PIT SOON, or STAY OUT
- condition_summary: brief description of car condition
- race_context: current race situation
- recommended_tire_compound: SOFT, MEDIUM, or HARD (null if STAY OUT)
- recommended_stint_laps: expected laps on new tires (null if STAY OUT)
- recommended_reason: why this compound (null if STAY OUT)
- expected_position_after_pit: position after pit stop (null if STAY OUT)
- expected_position_recovered: position after recovery (null if STAY OUT)
- expected_positions_gained: net positions gained (null if STAY OUT)
- reasoning: full explanation of your decision'
  USING TOOLS race_standings_tool
  WITH ('max_iterations' = '5');

-- 6. Agent invocation — processes every car-state record
INSERT INTO `pit-decisions`
SELECT
  car_number,
  lap,
  position,
  tire_compound AS tire_compound_current,
  AI_RUN_AGENT(pit_strategy_agent, *)
FROM `car-state`;
