# RUN OF SHOW

Every command a workshop attendee runs, in order, LAB 1 → LAB 6. Each lab has a
short **What / Run / Expect**, and a `> [!NOTE]` **talk track** for the presenter.

**Access model:** each attendee gets a **credential card** carrying a Confluent Cloud
username/password plus a companion `.env` of API keys. They sign in at confluent.cloud
and run **all SQL in the browser's Flink SQL workspace**; the `.env` is only for
`f1-pitwall`. So: a browser window and one terminal.

> [!TIP]
> **Presenter fleet controls** (your machine, needs AWS — not on an attendee card):
> `uv run workshop start-races` / `stop-races` / `reset-races` act on the **whole room**.
> Demo the labs on `f1wp001`; hand out `f1wp002`+.

---

## LAB 1 — Open your environment

**What:** connect to your pre-provisioned environment and confirm the live feed.

**Run — get your card (one of two paths):**

```bash
# Path A — instructor handed you f1wp###.md (sign-in) + f1wp###.env (dashboard). Done.
# Path B — you claimed via a Google Form: sign-in details are in the email; for the
#          dashboard, turn that email into a .env:
uv run f1-onboard            # prompts field-by-field
uv run f1-onboard --paste    # paste the whole email, then a blank line
```

**Run — sign in (browser):** open **confluent.cloud**, log in with the Console
Username/Password on the card, open your environment's **Flink → SQL workspace**, and
set catalog = `RIVER-RACING-f1wp###-ENV`, database = `...-CLUSTER`.

**Run — the dashboard (terminal):**

```bash
uv run f1-pitwall    # live dashboard at http://localhost:8000
```

**Run — confirm data is flowing (in the workspace):**

```sql
SHOW TABLES;
SELECT * FROM race_standings;   -- 22 live cars; Stop when you've seen enough
```

**Expect:** exactly one environment visible; three tables (`car_telemetry`,
`race_standings`, `driver_race_history`); 22 cars on the dashboard.

> [!NOTE]
> **Talk track:** "That username is a workshop account we made for you — not your own
> email. You'll see exactly one environment, yours. Everything you write today goes in
> that SQL workspace. The dashboard is your Pit Wall. Notice two panels are locked:
> ANOMALY DETECTION and AI PIT STRATEGIST. You'll light those up yourselves in Labs 3
> and 4."

---

## LAB 2 — Explore the environment

**What:** inspect the source tables, the historical CDC data, and the pre-deployed models.

**Run (same workspace — Stop each streaming query when you've seen enough):**

```sql
SHOW TABLES;

SELECT car_number, lap, tire_temp_fl_c, tire_pressure_fl_psi, engine_temp_c
FROM car_telemetry;

SELECT car_number, `position`, gap_to_leader_sec, tire_compound, tire_age_laps
FROM race_standings;

SELECT COUNT(*) FROM driver_race_history;   -- converges to 198

SHOW MODELS;        -- llm_textgen_model (Bedrock/Claude) + llm_embedding_model
SHOW CONNECTIONS;
```

**Expect:** live telemetry rows; one row per car; count climbs to 198; two models listed.

> [!NOTE]
> **Talk track:** "Everything upstream is already running — telemetry, standings, 198
> rows of historical strategy via CDC, and a Bedrock-backed Claude model, all
> pre-provisioned. You don't build any of that. You build the *intelligence* on top."

---

## LAB 3 — Stream processing: enrichment + anomaly detection

**What:** join telemetry to standings, tumble into 10s windows, and flag the front-left
tire-temp anomaly with `ML_DETECT_ANOMALIES` → `car_state`. Leave it
running.

**Run (paste the whole statement into a workspace cell and run it):**

```sql
CREATE TABLE `car_state`
WITH ('changelog.mode' = 'append')
AS
WITH enriched AS (
  SELECT
    t.car_number, t.event_time, t.lap,
    t.tire_temp_fl_c, t.tire_temp_fr_c, t.tire_temp_rl_c, t.tire_temp_rr_c,
    t.tire_pressure_fl_psi, t.tire_pressure_fr_psi,
    t.tire_pressure_rl_psi, t.tire_pressure_rr_psi,
    t.engine_temp_c, t.brake_temp_fl_c, t.brake_temp_fr_c,
    t.battery_charge_pct, t.fuel_remaining_kg,
    r.`position`, r.gap_to_ahead_sec, r.gap_to_leader_sec,
    r.pit_stops, r.tire_compound, r.tire_age_laps
  FROM `car_telemetry` t
  JOIN `race_standings` FOR SYSTEM_TIME AS OF t.event_time AS r
    ON t.car_number = r.car_number
),
windowed AS (
  SELECT
    window_start, window_end, window_time, car_number,
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
  GROUP BY window_start, window_end, window_time, car_number
),
anomaly AS (
  SELECT
    *,
    ML_DETECT_ANOMALIES(tire_temp_fl_c, window_time,
      JSON_OBJECT('minTrainingSize' VALUE 20,
                  'maxTrainingSize' VALUE 50,
                  'confidencePercentage' VALUE 99.99,
                  'enableStl' VALUE FALSE))
      OVER (PARTITION BY car_number ORDER BY window_time RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
      AS anomaly_tire_temp_fl_result
  FROM windowed
)
SELECT
  car_number, lap,
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

**Run — verify:**

```sql
SELECT car_number, lap, `position`, tire_compound, tire_age_laps,
       anomaly_tire_temp_fl, tire_temp_fl_c
FROM `car_state`;
```

**Expect:** a row every 10 seconds; around **lap 32** `anomaly_tire_temp_fl` flips
`true` and `tire_temp_fl_c` spikes to ~145°C.

> [!NOTE]
> **Talk track:** "The temporal join runs on the raw `event_time` *before* the window —
> move it after the TUMBLE and it silently returns zero rows. And `ML_DETECT_ANOMALIES`
> needs 20 windows of history — about 3½ minutes of live data — before it fires, so don't
> expect the lap-32 flag instantly." The ANOMALY DETECTION panel on the Pit Wall unlocks
> here.
>
> **Granite extension:** attendees now run `AI_FORECAST` with `'model' VALUE 'ttm'`
> after verifying `car_state`, then stop that temporary SELECT before LAB 4. This is
> the runnable Granite story. `AI_DETECT_ANOMALIES` remains opt-in because it forecasts
> but does not populate `is_anomaly` or the bounds in this environment, so replacing
> ARIMA would silently remove the lap-32 result. Canonical forecast SQL:
> `labs/instructor-led/LAB3_stream_processing/granite_tire_forecast.sql`.

### Optional: publish `car_state` to the Real-Time Context Engine

Console → cluster → **Topics** → `car_state` → **Real-Time Context Engine** panel →
**Off** → **On**, plus a description. Skippable — the labs don't depend on it.

> [!NOTE]
> **Talk track:** "`car_telemetry` was RTCE-enabled when we built your environment,
> so your coding agent can already query live sensor data over MCP — no Kafka client,
> no consumer group. `car_state` isn't, because you created it thirty seconds ago.
> One toggle fixes that." The description field is the point
> worth landing: the *agent* reads it to decide whether the topic answers a
> question, so it's prompt text, not a comment.
>
> Attendees who want to try it have a one-line `claude mcp add` on their credential
> card, endpoint and Basic token pre-filled (`workshop creds --rtce-keys`). It needs
> a local MCP client — Claude Code, Claude Desktop, or Cursor — so treat it as
> opt-in rather than a step you wait for the room on.

---

## LAB 4 — Streaming agent: pit decisions

**What:** create an AI agent over `car_state` that calls `PIT NOW / PIT SOON / STAY OUT`
with reasoning → `pit_decisions`.

**Run — step 1, create the agent (paste the whole statement, end with `;`):**

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
WITH ('max_iterations' = '10');
```

```sql
SHOW AGENTS;   -- confirm pit_strategy_agent exists
```

**Run — step 2, produce `pit_decisions` (paste the whole statement, end with `;`):**

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

**Expect:** the AI PIT STRATEGIST panel unlocks; the agent calls **PIT NOW** at the
lap-32 anomaly. Arc: P8 at the call → P2 at the finish (**+6 positions**).

> [!NOTE]
> **Talk track:** "`earliest-offset` makes the agent replay every lap already in
> `car_state`, so it catches up even for laps before you ran this. The prompt pins a
> strict 3-step algorithm — the anomaly forces PIT NOW — but the LLM writes the
> human-readable reasoning. Watch the banner flip to a flashing red PIT NOW at lap 32."

---

## LAB 5 — Social media agent (IBM watsonx Orchestrate)

**What:** a **no-code** Orchestrate agent that reads the same live feed and drafts
on-brand social posts. **No SQL, no shell** — all in the Orchestrate web UI.

**Do:**
1. **Tools → Add tool → Import from OpenAPI**, spec URL = the **race-feed base URL your
   instructor shares**, with `/openapi.json` appended. Import the **`get_race_feed`**
   operation (`GET /race-feed/{prefix}`).
2. **Agents → Create agent** `River Racing Social`; paste the persona/instructions from
   [`demo-reference/orchestrate_social_agent.md`](demo-reference/orchestrate_social_agent.md).
   Attach `get_race_feed`; set `prefix` = **your prefix** (e.g. `f1wp001`).
3. **Chat**, e.g.:
   ```
   Draft a hype post about where we are in the race right now.
   ```

**Expect:** the agent calls the feed, then writes a post grounded in live data — around
lap 32, the tire-drama / box call.

> [!NOTE]
> **Talk track:** "Same pipeline, new audience — a business user. Instructor provides the
> race-feed URL and Orchestrate access. The agent posts only from what the feed returns,
> so it needs Labs 3 and 4 built and a race running for tire and pit content; standings-
> only posts work without them."

---

## LAB 6 — Wrap-up

**What:** review the agent's calls and the key decision.

**Run (in the workspace):**

```sql
SELECT lap, `position`, suggestion, condition_summary, reasoning
FROM `pit_decisions`
WHERE suggestion <> 'STAY OUT';

SELECT lap, `position`, tire_compound_current, tire_age_laps,
       anomaly_tire_temp_fl, suggestion,
       recommended_tire_compound, recommended_stint_laps, reasoning
FROM `pit_decisions`
WHERE anomaly_tire_temp_fl = true;

SHOW TABLES;   -- car_state + pit_decisions now alongside the sources
SHOW AGENTS;   -- pit_strategy_agent
```

**Optional — clear your lab objects to run again:**

```sql
DROP TABLE IF EXISTS `pit_decisions`;
DROP TABLE IF EXISTS `car_state`;
DROP AGENT IF EXISTS `pit_strategy_agent`;
```

Quit the shell with `\q` when finished.

> [!NOTE]
> **Talk track:** "You built a three-stage streaming pipeline — enrich + detect, an AI
> agent that explains its calls, and a no-code agent for a business user — entirely in
> SQL and a UI, no application code."

---

> [!IMPORTANT]
> **Presenter — resetting for a new cohort.** After `uv run workshop reset-races`, the
> feeds are left **stopped**. Attendees must submit **LAB 3** *before* you run
> `uv run workshop start-races` — `race_standings` starts from `latest`, so any laps
> produced before LAB 3 is running are lost, and `car_state` silently misses its first laps.
