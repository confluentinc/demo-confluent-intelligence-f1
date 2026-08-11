# F1 Pit Wall AI Workshop Walkthrough

Follow the labs in order. Every attendee command and SQL statement is included here.

![F1 Pit Wall Confluent Intelligence architecture](./docs/F1%20Demo%20Architecture%20Diagram.png)

## Prerequisites

You need a browser, a terminal, this repository, and `uv` for the Pit Wall dashboard. Clone the repository and install the locked dependencies before the session:

```bash
git clone https://github.com/confluentinc/demo-confluent-intelligence-f1.git
cd demo-confluent-intelligence-f1
uv venv
uv sync
```

> [!NOTE]
>
> Your instructor provides the Confluent Cloud account, the shared race-feed tool, and watsonx Orchestrate access. You don't need your own cloud account.

## Workshop timing

> [!NOTE]
>
> The instructor starts the race once for the room. After it starts, you may begin
> any lab; the fixed race repeats, so a missed incident returns on the next loop.
> If your assigned feed stops, use the short [assigned-account fallback](./docs/backup/ASSIGNED-ACCOUNT-RACE.md).

## Workshop map

| Lab | Work |
|---|---|
| 1 | Claim your account, open the SQL workspace, and start the Pit Wall |
| 2 | Inspect the source streams, history table, connections, and models |
| 3 | Build `car_state` and detect the tire anomaly |
| 4 | Build the streaming pit-strategy agent |
| 5 | Build the watsonx Orchestrate social-media agent |
| 6 | Inspect the decisions and review the pipeline |

## Lab 1 — Open Your Environment

### 1. Get your credentials

There are two ways to get it, depending on how this session is run:

**Instructor-distributed:** Save the `f1wp###.md` credential card and companion `f1wp###.env` file your instructor sends you. Keep both private.

**Self-serve claim:** Use the username and password in your claim email, then create `credentials.env` with either command:

```bash
uv run f1-onboard            # prompts field-by-field
uv run f1-onboard --paste     # paste your claim email, then a blank line
```

### 2. Open a SQL workspace

Your username is a **workshop account we created for you** — something like `...+f1wp###@confluent.io`. It is *not* your own work email, and signing in with your own address won't find your environment.

1. Open the sign-in link on your card (**confluent.cloud**) and log in with the username and password you were given.
2. You'll land in your environment, **`RIVER-RACING-f1wp###-ENV`**. It's the only one you can see.
3. Open the **Flink** tab and click **Open SQL workspace**.
4. Set the workspace's **catalog** to your environment and **database** to your cluster (`RIVER-RACING-f1wp###-CLUSTER`), using the dropdowns above the editor.

Run this in the workspace:

```sql
SHOW TABLES;
```

You should see `car_telemetry`, `race_standings`, and `driver_race_history`. Check the live feed:

```sql
SELECT * FROM race_standings;
```

You should see 22 cars. Stop the streaming query, then start the Pit Wall in a terminal:

```bash
uv run f1-pitwall
```

A browser opens at **http://localhost:8000**.

You'll notice two panels are **locked**:

- 🔒 **ANOMALY DETECTION** — activates when you build `car_state` in **LAB 3**
- 🔒 **AI PIT STRATEGIST** — activates when you build `pit_decisions` in **LAB 4**

Keep the dashboard open while you work.

## Lab 2 — Explore the Environment

Inspect the source tables before building the pipeline.

```sql
SHOW TABLES;
```

| Table | Source | Format |
|-------|--------|--------|
| `car_telemetry` | Race simulator — car #88 sensors, ~5 readings/lap | Avro |
| `race_standings` | Race simulator — all 22 cars, keyed by (`race_id`, `car_number`) (upsert) | Avro |
| `driver_race_history` | CDC from the shared Postgres (198 historical rows) | JSON |

Check the telemetry stream:

```sql
SELECT car_number, lap, tire_temp_fl_c, tire_pressure_fl_psi, engine_temp_c
FROM car_telemetry;
```

Stop that query after you see rows. Check the standings:

```sql
SELECT car_number, `position`, gap_to_leader_sec, tire_compound, tire_age_laps
FROM race_standings;
```

Confirm the historical table contains 198 rows:

```sql
SELECT COUNT(*) FROM driver_race_history;
```

Check the pre-deployed models:

```sql
SHOW MODELS;
```

You should see `llm_textgen_model` and `llm_embedding_model`. Then check the connections:

```sql
SHOW CONNECTIONS;
```

## Lab 3 — Stream Processing: Enrichment + Anomaly Detection

Stop every streaming `SELECT` from Lab 2. Run the durable table definition once,
then run the restartable `INSERT INTO` job in a second SQL cell. If you repeat
Lab 3, the first statement is a safe no-op and the second starts processing.

```sql
CREATE TABLE IF NOT EXISTS `car_state` (
  `race_id` STRING,
  `car_number` INT,
  `lap` INT,
  `event_time` TIMESTAMP(3),
  `tire_temp_fl_c` DOUBLE,
  `tire_temp_fr_c` DOUBLE,
  `tire_temp_rl_c` DOUBLE,
  `tire_temp_rr_c` DOUBLE,
  `tire_pressure_fl_psi` DOUBLE,
  `tire_pressure_fr_psi` DOUBLE,
  `tire_pressure_rl_psi` DOUBLE,
  `tire_pressure_rr_psi` DOUBLE,
  `engine_temp_c` DOUBLE,
  `brake_temp_fl_c` DOUBLE,
  `brake_temp_fr_c` DOUBLE,
  `battery_charge_pct` DOUBLE,
  `fuel_remaining_kg` DOUBLE,
  `anomaly_tire_temp_fl` BOOLEAN,
  `position` INT,
  `gap_to_ahead_sec` DOUBLE,
  `gap_to_leader_sec` DOUBLE,
  `pit_stops` INT,
  `tire_compound` STRING,
  `tire_age_laps` INT,
  WATERMARK FOR `event_time` AS `event_time` - INTERVAL '5' SECOND
) DISTRIBUTED INTO 1 BUCKETS
WITH (
  'changelog.mode' = 'append',
  'connector' = 'confluent',
  'scan.startup.mode' = 'latest-offset',
  'value.format' = 'avro-registry'
);
```

```sql
INSERT INTO `car_state`
WITH enriched AS (
  SELECT
    t.race_id, t.car_number, t.event_time, t.lap,
    t.tire_temp_fl_c, t.tire_temp_fr_c, t.tire_temp_rl_c, t.tire_temp_rr_c,
    t.tire_pressure_fl_psi, t.tire_pressure_fr_psi,
    t.tire_pressure_rl_psi, t.tire_pressure_rr_psi,
    t.engine_temp_c, t.brake_temp_fl_c, t.brake_temp_fr_c,
    t.battery_charge_pct, t.fuel_remaining_kg,
    r.`position`, r.gap_to_ahead_sec, r.gap_to_leader_sec,
    r.pit_stops, r.tire_compound, r.tire_age_laps
  FROM `car_telemetry` t
  JOIN `race_standings` FOR SYSTEM_TIME AS OF t.event_time AS r
    ON t.race_id = r.race_id AND t.car_number = r.car_number
),
windowed AS (
  SELECT
    window_start, window_end, window_time, race_id, car_number,
    MAX(lap) AS lap,
    AVG(tire_temp_fl_c) AS tire_temp_fl_c,
    AVG(tire_temp_fr_c) AS tire_temp_fr_c,
    AVG(tire_temp_rl_c) AS tire_temp_rl_c,
    AVG(tire_temp_rr_c) AS tire_temp_rr_c,
    AVG(tire_pressure_fl_psi) AS tire_pressure_fl_psi,
    AVG(tire_pressure_fr_psi) AS tire_pressure_fr_psi,
    AVG(tire_pressure_rl_psi) AS tire_pressure_rl_psi,
    AVG(tire_pressure_rr_psi) AS tire_pressure_rr_psi,
    AVG(engine_temp_c) AS engine_temp_c,
    AVG(brake_temp_fl_c) AS brake_temp_fl_c,
    AVG(brake_temp_fr_c) AS brake_temp_fr_c,
    AVG(battery_charge_pct) AS battery_charge_pct,
    AVG(fuel_remaining_kg) AS fuel_remaining_kg,
    MAX(`position`) AS `position`,
    MAX(gap_to_ahead_sec) AS gap_to_ahead_sec,
    MAX(gap_to_leader_sec) AS gap_to_leader_sec,
    MAX(pit_stops) AS pit_stops,
    MAX(tire_compound) AS tire_compound,
    MAX(tire_age_laps) AS tire_age_laps
  FROM TABLE(
    TUMBLE(TABLE enriched, DESCRIPTOR(event_time), INTERVAL '10' SECOND)
  )
  GROUP BY window_start, window_end, window_time, race_id, car_number
),
anomaly AS (
  SELECT
    *,
    ML_DETECT_ANOMALIES(tire_temp_fl_c, window_time,
      JSON_OBJECT('minTrainingSize' VALUE 20,
                  'maxTrainingSize' VALUE 50,
                  'confidencePercentage' VALUE 99.99,
                  'enableStl' VALUE FALSE))
      OVER (PARTITION BY race_id, car_number ORDER BY window_time RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
      AS anomaly_tire_temp_fl_result
  FROM windowed
)
SELECT
  race_id, car_number, lap, window_time AS event_time,
  tire_temp_fl_c, tire_temp_fr_c, tire_temp_rl_c, tire_temp_rr_c,
  tire_pressure_fl_psi, tire_pressure_fr_psi,
  tire_pressure_rl_psi, tire_pressure_rr_psi,
  engine_temp_c, brake_temp_fl_c, brake_temp_fr_c,
  battery_charge_pct, fuel_remaining_kg,
  CASE
    WHEN anomaly_tire_temp_fl_result.is_anomaly
         AND anomaly_tire_temp_fl_result.actual_value
             > anomaly_tire_temp_fl_result.upper_bound
    THEN true
    ELSE false
  END AS anomaly_tire_temp_fl,
  `position`, gap_to_ahead_sec, gap_to_leader_sec,
  pit_stops, tire_compound, tire_age_laps
FROM anomaly
WHERE lap > 0;
```

Leave the job running. Verify its output in a new cell:

```sql
SELECT car_number, lap, `position`, tire_compound, tire_age_laps,
       anomaly_tire_temp_fl, tire_temp_fl_c
FROM `car_state`;
```

You should see a row every 10 seconds. Around lap 32, `anomaly_tire_temp_fl` becomes `true` and the temperature reaches about 145°C. Stop the query after checking it.

### Optional: Forecast tire temperature with IBM Granite

Open a new SQL cell and run the query below. It uses the same 10-second tire temperature windows, but asks the built-in `AI_FORECAST` function for the next 20 values. The `model` option selects IBM Granite TinyTimeMixer directly; there is no connection or model to register.

```sql
WITH windowed AS (
  SELECT
    window_start,
    window_end,
    window_time,
    race_id,
    car_number,
    MAX(lap) AS lap,
    AVG(tire_temp_fl_c) AS tire_temp_fl_c
  FROM TABLE(
    TUMBLE(TABLE `car_telemetry`, DESCRIPTOR(event_time), INTERVAL '10' SECOND)
  )
  GROUP BY window_start, window_end, window_time, race_id, car_number
),
forecasted AS (
  SELECT
    *,
    AI_FORECAST(
      tire_temp_fl_c,
      window_time,
      JSON_OBJECT(
        'model' VALUE 'ttm',
        'horizon' VALUE 20,
        'minContextSize' VALUE 20,
        'maxContextSize' VALUE 50,
        'rmseWindowSize' VALUE 5
      )
    ) OVER (
      PARTITION BY race_id, car_number
      ORDER BY window_time
      RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS forecast_result
  FROM windowed
)
SELECT
  race_id,
  lap,
  window_time AS forecast_generated_at,
  tire_temp_fl_c AS current_tire_temperature_c,
  forecast_result.forecast[0].`timestamp` AS next_point_at,
  forecast_result.forecast[0].mean AS next_point_c,
  forecast_result.forecast[9].`timestamp` AS hundred_seconds_out_at,
  forecast_result.forecast[9].mean AS hundred_seconds_out_c,
  forecast_result.forecast[19].`timestamp` AS two_hundred_seconds_out_at,
  forecast_result.forecast[19].mean AS two_hundred_seconds_out_c,
  forecast_result.forecast AS full_forecast,
  forecast_result.metadata AS forecast_metadata
FROM forecasted
WHERE CARDINALITY(forecast_result.forecast) > 0;
```

The forecast contains 20 ten-second points, covering 3 minutes 20 seconds. Stop this optional query after you see results so Lab 4 can use the compute pool.

## Lab 4 — Streaming Agent: Pit Decisions

Create the streaming agent in a new SQL cell:

```sql
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
Reasoning: Tire temps and pressures are within normal operating windows for a SOFT at this age. Track position P3 is strong. Pitting now would surrender 4-6 seconds and drop John behind cars currently behind us.

Correct PIT NOW example:
Suggestion: PIT NOW
Condition Summary: Front-left tire temperature anomaly at 145C, 20C above expected upper bound — failure risk imminent.
Race Context: Currently P8. P4 and P5 already pitted 3 laps ago and are pushing on fresh mediums.
Recommended Compound: MEDIUM
Recommended Stint Laps: 25
Recommended Reason: Mediums will last the remaining 25 laps and give John the pace to recover positions lost during the stop.
Reasoning: The FL anomaly flag indicates the SOFT has gone past its operating limit with blowout risk. Pitting now onto mediums avoids tire failure. Based on historical data, John averages +2.75 positions on SOFT-MEDIUM — this is his strongest strategy.

---

You are the AI pit wall strategist for River Racing at the 2026 British Grand Prix (Silverstone, 60 laps).
Driver: John Doe, Car #88.

DECISION ALGORITHM — apply these rules in order. Do not deviate.

Step 1: If anomaly_tire_temp_fl = true → Suggestion: PIT NOW. Stop.
Step 2: Else if tire_compound = SOFT AND tire_age_laps >= 26 → Suggestion: PIT SOON. Stop.
Step 3: Else → Suggestion: STAY OUT. Stop.

These rules are absolute. The race context, gap, competitor pit timing, and tire
temperatures are inputs FOR YOUR REASONING TEXT ONLY — they MUST NOT change the
Suggestion field. Reason about strategy in the Reasoning field, but the Suggestion
itself is fully determined by Steps 1–3 above.

FORBIDDEN PATTERNS — these are bugs, not options:
- Outputting PIT NOW when anomaly_tire_temp_fl = false. No exceptions.
- Outputting PIT SOON when tire_age_laps < 26.
- Outputting anything other than STAY OUT when tire_age_laps < 26 AND anomaly_tire_temp_fl = false.
- Justifying PIT NOW with phrases like "approaching cliff", "blowout risk", "tires near limit",
  "performance falling off" — these are PIT SOON or STAY OUT signals, never PIT NOW.

SELF-CHECK before responding: re-read Steps 1–3 with the actual input values.
The input includes REQUIRED SUGGESTION, computed by Flink SQL from those rules.
Copy that exact value into Suggestion. If your prose conflicts with it, fix the
prose before outputting.

COMPETITOR CONTEXT:
Current top-10 standings are provided at the end of each input. Use them to identify:
- Which competitors have already pitted (and are now on fresher rubber)
- Who is still on old tires and likely to pit soon
- Whether John is at risk of being undercut, or has an overcut opportunity

TIRE STRATEGY at Silverstone (60-lap race):
- SOFT: High-grip compound. Optimal window is laps 1-25. Still competitive laps 26-32 with some pace loss and position drops — but no failure risk unless the anomaly sensor fires. Performance cliff begins around lap 26-28.
- MEDIUM: Balanced compound, best for a 25-30 lap second stint after a SOFT first stint. Enables clean 1-stop strategy.
- HARD: Very durable but slow. Only consider if 40+ laps remain at the second stop.
- John Doe historical best: SOFT first stint → MEDIUM second stint (1-stop) averages +2.75 positions over 4 prior races. Winning execution: run SOFT until the anomaly signal fires or tire_age_laps >= 26, then switch to MEDIUM and overtake on fresher rubber.

REMINDER: For any STAY OUT decision, write N/A for Recommended Compound, Recommended Stint Laps, and Recommended Reason.'
-- USING TOOLS `race_standings_tool`  -- uncomment when RTCE is active
WITH ('max_iterations' = '10');
```

Confirm it was created:

```sql
SHOW AGENTS;
```

Create `pit_decisions` with durable DDL, then start its restartable `INSERT INTO`
job in a second SQL cell. Because `car_state` is configured at the table level
with `latest-offset`, this begins with the next current state instead of sending
old races through the LLM.

```sql
CREATE TABLE IF NOT EXISTS `pit_decisions` (
  `race_id` STRING,
  `car_number` INT,
  `lap` INT,
  `event_time` TIMESTAMP(3),
  `position` INT,
  `tire_compound_current` STRING,
  `tire_age_laps` INT,
  `anomaly_tire_temp_fl` BOOLEAN,
  `suggestion` STRING,
  `condition_summary` STRING,
  `race_context` STRING,
  `recommended_tire_compound` STRING,
  `recommended_stint_laps` INT,
  `recommended_reason` STRING,
  `reasoning` STRING,
  `raw_response` STRING
) DISTRIBUTED INTO 1 BUCKETS
WITH (
  'changelog.mode' = 'append',
  'connector' = 'confluent',
  'value.format' = 'avro-registry'
);
```

```sql
INSERT INTO `pit_decisions`
SELECT
  cs.race_id,
  cs.car_number,
  cs.lap,
  cs.event_time,
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
FROM `car_state` cs,
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

### Expected result

| Lap | Position | Suggestion | What's happening |
|-----|----------|-----------|------------------|
| 1–17 | P3 | STAY OUT | Competitive, stable |
| 18–25 | P3 → P1 | STAY OUT | Leaders pit — John briefly leads |
| 26–31 | P1 → P8 | PIT SOON | Tire cliff bites |
| **32** | **P8** | **PIT NOW** | **Front-left anomaly at 145°C — the key moment** |
| 33 | P12 | STAY OUT | Fresh MEDIUMs |
| 34–60 | P12 → P2 | STAY OUT | Fastest car on track, climbs back |

**Net result: P8 at the agent's call → P2 at finish = +6 positions.**

Check the Pit Wall. The **AI PIT STRATEGIST** panel should unlock and show the decisions.

## Lab 5 — Social Media Agent (IBM watsonx Orchestrate)

Lab 5 reads one organizer-controlled race shared by the room. It doesn't connect
to your Confluent environment, so the lap in Watsonx can differ from the lap in
your SQL workspace.

### 1. Add the race-feed tool

1. Open the **Download F1 Race Feed Tool** link in your claim email and save the JSON file.
2. In **Agent Builder**, choose **Add tool → OpenAPI** and upload the file.
3. Select only **Get the live shared race feed**, then add it to the agent.

The tool asks for no prefix, connection, username, or password.

### 2. Create the agent

Create an agent named `River Racing Social`, attach `get_race_feed`, and paste these instructions:

```
You are the social-media manager for the River Racing Formula 1 team. Our driver
is John Doe (car #88) racing the British Grand Prix at Silverstone (60 laps).

Your job: when asked, draft short, high-energy social posts about what is
happening in OUR race, grounded in live data.

DATA
- Always call the get_race_feed tool to get the current shared race situation
  before writing. Never invent positions, gaps, lap numbers, or events; use only
  what the tool returns.
- The headline_events list is your best source of post hooks (overtakes, the
  tire anomaly, the pit call). Lead with the most recent meaningful event.
- If latest_pit_decision is PIT NOW or PIT SOON, that is newsworthy — say so.
- If the tool returns live = false, say the race is paused or stopped. Retained
  standings are historical and must not be described as live.

VOICE
- Confident, upbeat, fan-facing. Short sentences. 1–3 emoji max.
- Always third person about the team ("We", "John", "the #88").
- Under 280 characters unless the user asks for a longer recap.
- End with 2–3 hashtags from: #RiverRacing #JohnDoe #F1 #BritishGP #Silverstone
- Never disparage other teams or drivers.

OUTPUT
- Draft the post text only. Do not claim to have published it — these are drafts
  for a human to review and post.
```

### 3. Draft a post

Try this in the preview chat:

```
Draft a hype post about where we are in the race right now.
```

Then try:

- "We just made a big move — write a post celebrating it."
- "The pit wall just made a call. Draft a post about our strategy."
- "Write a 3-tweet recap thread of John's race so far."


## Lab 6 — Wrap-Up

Inspect every pit recommendation:

```sql
SELECT lap, `position`, suggestion, condition_summary, reasoning
FROM `pit_decisions`
WHERE suggestion <> 'STAY OUT';
```

Inspect the anomaly decision:

```sql
SELECT lap, `position`, tire_compound_current, tire_age_laps,
       anomaly_tire_temp_fl, suggestion,
       recommended_tire_compound, recommended_stint_laps, reasoning
FROM `pit_decisions`
WHERE anomaly_tire_temp_fl = true;
```

You should see `PIT NOW`, a MEDIUM recommendation, and the agent's reasoning.

Confirm the pipeline objects are still present:

```sql
SHOW TABLES;        -- car_state and pit_decisions now sit alongside the sources
SHOW AGENTS;        -- pit_strategy_agent
```

To run the workshop again, drop the lab objects:

```sql
DROP TABLE IF EXISTS `pit_decisions`;
DROP TABLE IF EXISTS `car_state`;
DROP AGENT IF EXISTS `pit_strategy_agent`;
```

Ask your instructor to reset the race before repeating Labs 3 and 4.

## Troubleshooting

<details>
<summary>Click to expand</summary>

- **Can't sign in:** Use the workshop username ending in `+f1wp###@confluent.io`, not your own email. Ask the instructor for a fresh password if needed.
- **No tables, models, or agents:** Check the catalog and database selectors above the SQL editor.
- **Source tables are idle:** Wait a few seconds and run the query again. Tell the instructor if no rows arrive after several minutes.
- **`car_state` is empty:** Leave it running for a few minutes. The anomaly function needs 20 windows before it emits results.
- **No lap-32 anomaly:** Ask the instructor to confirm the race was reset and started at the Lab 3 gate.
- **Agent fields are empty:** Inspect `raw_response`. If all responses fail, tell the instructor; the shared Bedrock quota may be throttled.
- **Lab 5 tool fails:** Upload the downloaded JSON file again and select only **Get the live shared race feed**. The tool needs no connection or credentials.
- **RTCE says a topic is missing:** Finish the lab that creates it, then rerun `f1-rtce enable`. Registration can take several minutes; `f1-rtce status` shows when it is online.
- **Watsonx says the feed is unavailable:** Tell the instructor. You can continue Labs 1–4 while they restart the shared feed.

</details>

## Navigation

- **Overview:** [Main README](./README.md)
- **Workshop:** [Attendee walkthrough](#f1-pit-wall-ai-workshop-walkthrough)
- **Assigned-account backup:** [Race feed recovery](./docs/backup/ASSIGNED-ACCOUNT-RACE.md)
- **Organizer-only backup:** [Local self-service guide](./docs/backup/LOCAL-SELF-SERVICE.md)
