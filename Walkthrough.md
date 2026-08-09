# F1 Pit Wall AI Workshop Walkthrough

Use this file during the workshop. It contains every attendee step, all SQL, the
watsonx Orchestrate agent instructions, and troubleshooting.

## Before the session

You need a browser, a terminal, this repository, and `uv` for the Pit Wall
dashboard. Clone the repository and install the locked dependencies before the
session:

```bash
git clone https://github.com/confluentinc/demo-confluent-intelligence-f1.git
cd demo-confluent-intelligence-f1
uv venv
uv sync
```

If the instructor distributes an `.env` credential card, save it somewhere in
this repository and keep it private. If you claim an account by email, Lab 1
shows how to create the local credential file.

The instructor provides two session-specific values:

- Your Confluent Cloud workshop credentials and environment prefix
- The shared race-feed URL and watsonx Orchestrate access used in Lab 5

## Workshop timing

The live race runs during Labs 1 and 2 so you can inspect the source streams.
At the transition to Lab 3, the instructor resets and pauses every simulator.
Submit the Lab 3 `CREATE TABLE car_state` statement and wait until its status is
**Running**. The instructor then starts all races together.

This timing keeps the room synchronized. The standings table starts at the
latest offset, so a Lab 3 job can't reconstruct standings versions produced
before that job started. A race left running in the background still works for
new events, but someone who joins after lap 32 may wait for the next race loop
to see the anomaly.

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

### Overview

Your instructor has pre-provisioned a dedicated Confluent Cloud environment for
you and a **live race feed** already streaming into it. You'll sign in to
Confluent Cloud with a workshop account and write all of your Flink SQL in the
browser's SQL workspace.

#### What you'll accomplish

1. Get your credential card
2. Sign in to Confluent Cloud and open a SQL workspace
3. Confirm your environment is live
4. Open your live **Pit Wall** dashboard

> **Heads-up for LAB 5:** LAB 5 uses
> **IBM watsonx Orchestrate**. Your instructor provides access during the
> workshop — there's nothing to sign up for or set up in advance.

### Steps

#### Step 1: Get your credential card

There are two ways to get it, depending on how this session is run:

**A — Instructor-distributed card.** Your instructor hands out (via email or a
shared link) a card named for your prefix, e.g. **`f1wp###.md`**, plus a
companion **`f1wp###.env`**. The card has your sign-in details; the `.env` is
for the dashboard in Step 4.

**B — Self-serve claim.** If you claimed your account yourself (a Google Form
link from your instructor), you'll receive an email listing your environment's
values by name (Console Username, Console Password, Prefix, Kafka API Key, ...).
Your sign-in details are in that email. For the dashboard, run the onboarding
wizard and either answer its prompts one at a time or paste the whole email in
with `--paste`:

```bash
uv run f1-onboard            # prompts field-by-field
uv run f1-onboard --paste     # paste your claim email, then a blank line
```

This writes a local `credentials.env` in the same shape as an
instructor-distributed `.env`.

Either way you end up with two things: **a Confluent Cloud username and
password**, and **a file of API keys** that looks like this:

```
F1_PREFIX=f1wp###
F1_FLINK_REST_ENDPOINT=https://flink.us-east-1.aws.confluent.cloud
F1_ENVIRONMENT_ID=env-xxxxx
F1_COMPUTE_POOL_ID=lfcp-xxxxx
F1_FLINK_API_KEY=...
F1_FLINK_API_SECRET=...
... (Kafka + Schema Registry keys too)
```

Keep both private — between them they grant full access to your environment.

> **LAB 5** also needs a **race-feed base URL**, but that's *not* on your card —
> it's one shared service your instructor gives you the URL for. See
> LAB 5.

> Your prefix (`f1wp###` above) is unique to you.

#### Step 2: Sign in and open a SQL workspace

Your username is a **workshop account we created for you** — something like
`...+f1wp###@confluent.io`. It is *not* your own work email, and signing in with
your own address won't find your environment.

1. Open the sign-in link on your card (**confluent.cloud**) and log in with the
   username and password you were given.
2. You'll land in your environment, **`RIVER-RACING-f1wp###-ENV`**. It's the only
   one you can see.
3. Open the **Flink** tab and click **Open SQL workspace**.
4. Set the workspace's **catalog** to your environment and **database** to your
   cluster (`RIVER-RACING-f1wp###-CLUSTER`), using the dropdowns above the editor.

You'll write every SQL statement in the rest of this workshop here. Type a
statement into a cell and press **Run** (or Shift-Enter).

#### Step 3: Confirm your environment is live

In your workspace:

```sql
SHOW TABLES;
```

You should see three tables — `car_telemetry`, `race_standings`, and
`driver_race_history`. Then check the live feed:

```sql
SELECT * FROM race_standings;
```

You'll see 22 cars with live positions. It's a streaming query, so it keeps
running — use **Stop** when you've seen enough.

> If `SHOW TABLES` errors or returns nothing, check the catalog/database
> dropdowns first — see [Troubleshooting](#troubleshooting) or ask
> your instructor.

#### Step 4: Open your Pit Wall dashboard

In a terminal (keep the browser workspace open), launch the live race dashboard
— it uses the `.env` file from Step 1:

```bash
uv run f1-pitwall
```

A browser opens at **http://localhost:8000** showing your race: a Silverstone
track map with all 22 cars, the live leaderboard, and car #88's tyre/fuel gauges.

You'll notice two panels are **locked**:

- 🔒 **ANOMALY DETECTION** — activates when you build `car_state` in **LAB 3**
- 🔒 **AI PIT STRATEGIST** — activates when you build `pit_decisions` in **LAB 4**

That's the goal of the labs: you'll bring those panels to life yourself. Keep the
dashboard open in a window you can watch as you work.

> The dashboard only *reads* your topics — it never runs Flink SQL, so it won't
> interfere with your labs. Stop it any time with Ctrl-C.

### Conclusion

You're connected to your environment, data is flowing, and your Pit Wall is live.
Continue to LAB 2.

## Lab 2 — Explore the Environment

### Overview

Everything that feeds the pipeline is already running. Before you build anything,
explore the pre-provisioned pieces and confirm live data is flowing — all from
the SQL workspace you opened in LAB 1.

#### What you'll accomplish

1. Inspect the source tables and their shapes
2. Confirm the live race feed is producing
3. Find the historical CDC data and the pre-deployed LLM models

#### Prerequisites

LAB 1 — you're signed in with a SQL workspace open.

### Steps

#### Step 1: The tables

```sql
SHOW TABLES;
```

| Table | Source | Format |
|-------|--------|--------|
| `car_telemetry` | Race simulator — car #88 sensors, ~5 readings/lap | Avro |
| `race_standings` | Race simulator — all 22 cars, keyed by `car_number` (upsert) | Avro |
| `driver_race_history` | CDC from the shared Postgres (198 historical rows) | JSON |

> **Self-service track?** If you provisioned with `uv run selfservice up`,
> `driver_race_history` is **Avro**, not JSON — there is no CDC connector there, so
> the same 198 rows are seeded by a bounded Flink `INSERT`. The other two tables and
> every query in this lab are identical.

Look at the telemetry stream (Ctrl-C to stop and return to the prompt):

```sql
SELECT car_number, lap, tire_temp_fl_c, tire_pressure_fl_psi, engine_temp_c
FROM car_telemetry;
```

You should see readings arriving live. Now the standings:

```sql
SELECT car_number, `position`, gap_to_leader_sec, tire_compound, tire_age_laps
FROM race_standings;
```

One row per car with live position, gaps, and tire context.

> The simulator produces `race_standings` straight to
> Kafka as keyed Avro, so the table is already a clean upsert (versioned) table —
> exactly what the temporal join in LAB 3 needs.

#### Step 2: The historical CDC data

The `f1-postgres-cdc` connector streams each driver's prior-race tire strategies
and results from the shared Postgres into your cluster. Confirm it landed:

```sql
SELECT COUNT(*) FROM driver_race_history;
```

This converges to **198** rows. (It's a streaming count, so you'll see the value
climb to 198 and settle — Ctrl-C once it stops.) This is historical context the
agent's reasoning can draw on in LAB 4.

> **Self-service track?** There is no connector and no Postgres to look at:
> `uv run selfservice up` writes the same 198 rows with a bounded Flink `INSERT`
> before handing the environment over, so the count is already complete.

#### Step 3: The pre-deployed AI models

The agent in LAB 4 uses a Bedrock-backed model that's already created for you:

```sql
SHOW MODELS;
```

You should see `llm_textgen_model` (Bedrock / Claude, for the agent) and
`llm_embedding_model`. You can also confirm the Bedrock connections exist:

```sql
SHOW CONNECTIONS;
```

These are pre-created so you don't have to set up the Bedrock connection
yourself.

### Conclusion

Source data is flowing and the AI models are ready. Now build the intelligence
layer in LAB 3.

## Lab 3 start gate

Stop any streaming `SELECT` from Lab 2. Wait for the instructor to confirm that
the fleet reset has finished and the race is paused. Run the Lab 3
`CREATE TABLE car_state` statement below. Once the statement shows **Running**,
tell the instructor you are ready and leave it running.



## Lab 3 — Stream Processing: Enrichment + Anomaly Detection

### Overview

Build the intelligence layer. You'll combine the live telemetry and standings
into a single `car_state` stream and detect the front-left tire-temperature
anomaly that signals a failing tire — using Flink's built-in
`ML_DETECT_ANOMALIES` — then use IBM Granite to forecast the next three
tire-temperature windows.

#### What you'll accomplish

1. Tumble `car_telemetry` into 10-second windows
2. Temporal-join with `race_standings` to add position, gaps, and tire context
3. Run `ML_DETECT_ANOMALIES` on `tire_temp_fl_c`
4. Produce the `car_state` table
5. Run `AI_FORECAST` with IBM Granite TinyTimeMixer (`ttm`)

#### Prerequisites

LAB 2 — data is flowing, models exist.

### Steps

#### Step 1: Create the `car_state` table

In your SQL workspace, paste the whole statement below into a cell and run it:

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

The shell will report the statement as **running** and print its name — this is a
continuous job, so leave it running for the next step.

#### Why it's built this way

- **Temporal join before the windows.** The `FOR SYSTEM_TIME AS OF t.event_time`
  join uses `race_standings` as a versioned (upsert) table. It must happen on the
  raw `event_time` rowtime, before the `TUMBLE`/`OVER` aggregations — otherwise
  `window_time` loses its rowtime attribute and the join silently returns zero
  rows.
- **Only `tire_temp_fl_c` runs through `ML_DETECT_ANOMALIES`.** The other sensors
  are noisier and only produce false positives that distract from the story.
- **`actual_value > upper_bound` filter.** This keeps only the *overheating*
  spike as an anomaly, not the cold drop after the pit stop (which is a recovery,
  not a problem).
- **`minTrainingSize` / `enableStl` tune the ARIMA model.** `ML_DETECT_ANOMALIES`
  fits a statistical model per partition, so it needs 20 windows of history before
  it will judge anything, and `maxTrainingSize` keeps that history a *rolling* 50
  windows rather than the whole race. STL seasonal-trend decomposition is off: the
  synthetic data has no seasonality for it to find, only added variance.

> **Bonus — the same job with a foundation model.** Flink also ships
> `AI_DETECT_ANOMALIES`, which swaps ARIMA for a pretrained time-series model you
> pick by name: IBM Granite `ttm` (TinyTimeMixer — small enough to run on CPUs),
> `flowstate`, `patchtstfm`, or Google `timesfm-2.5` (the default if you omit the
> parameter). Nothing else in the statement changes when you swap models — that is
> the point of the feature. It is gated behind an Early Access Program, so on most
> orgs you will see *"Function AI_DETECT_ANOMALIES does not exist or you do not have
> permission to access it."*
>
> **On the build we tested, this variant does not flag the anomaly.** It runs, and
> it forecasts well, but it leaves `is_anomaly` and both bounds NULL — so the `CASE`
> never becomes true and `anomaly_tire_temp_fl` stays `false` all race.
>
> **Read this one; don't run it during the lab.** `car_state` already exists by now,
> so swapping the CTE means dropping and recreating the table — which also strands
> its Schema Registry subjects and takes LAB 4 down with it. If you want to see it
> for yourself, do it after LAB 6, and ask your instructor to reset the environment
> afterwards.
>
> <details>
> <summary>Foundation-model variant — the <code>anomaly</code> CTE to use instead</summary>
>
> ```sql
> anomaly AS (
>   SELECT
>     *,
>     AI_DETECT_ANOMALIES(tire_temp_fl_c, window_time,
>       -- Swap this one line to compare model backends: 'ttm' (IBM Granite
>       -- TinyTimeMixer), 'flowstate', 'patchtstfm', or 'timesfm-2.5' (the default
>       -- when 'model' is omitted). Same function, same output — the point of
>       -- foundation-model support is that this is a one-word change.
>       JSON_OBJECT('model' VALUE 'ttm',
>                   'minContextSize' VALUE 20,
>                   'maxContextSize' VALUE 50,
>                   'confidencePercentage' VALUE 99.99))
>       OVER (PARTITION BY car_number ORDER BY window_time RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
>       AS anomaly_tire_temp_fl_result
>   FROM windowed
> )
> ```
>
> Note the different config keys: `minContextSize`/`maxContextSize` rather than
> `minTrainingSize`/`maxTrainingSize`, and no `enableStl` — that option is
> ARIMA-specific and does not exist here. (`ttm` is pretrained, so there is no
> training phase at all; `minContextSize` is just an emission gate.) Everything
> above and below the CTE stays exactly as written; the replacement CTE above is
> all you need for the optional experiment.
> </details>

#### Step 2: Verify

```sql
SELECT car_number, lap, `position`, tire_compound, tire_age_laps,
       anomaly_tire_temp_fl, tire_temp_fl_c
FROM `car_state`;
```

You should see a row every 10 seconds — six per lap at the default 60s/lap pace.
Around **lap 32**, `anomaly_tire_temp_fl` flips to `true` and `tire_temp_fl_c`
spikes to ~145°C. (Ctrl-C to stop the query.)

> `ML_DETECT_ANOMALIES` needs 20 windows of history before it fires
> (`minTrainingSize`) — 20 × 10 seconds, so about 3½ minutes of live data — and it
> won't flag anything before then. If
> `car_state` stays empty, see
> [Troubleshooting](#troubleshooting).

#### Step 3: Forecast tire temperature with IBM Granite

Open a new SQL cell and run the query below. It uses the same 10-second tire
temperature windows, but asks the built-in `AI_FORECAST` function for the next
three values. The `model` option selects IBM Granite TinyTimeMixer directly;
there is no connection or model to register.

```sql
WITH windowed AS (
  SELECT
    window_start, window_end, window_time, car_number,
    MAX(lap) AS lap,
    AVG(tire_temp_fl_c) AS tire_temp_fl_c
  FROM TABLE(
    TUMBLE(TABLE `car_telemetry`, DESCRIPTOR(event_time), INTERVAL '10' SECOND)
  )
  GROUP BY window_start, window_end, window_time, car_number
),
forecasted AS (
  SELECT
    *,
    AI_FORECAST(
      tire_temp_fl_c,
      window_time,
      JSON_OBJECT(
        'model' VALUE 'ttm',
        'horizon' VALUE 3,
        'minContextSize' VALUE 20,
        'maxContextSize' VALUE 50,
        'rmseWindowSize' VALUE 5
      )
    ) OVER (
      PARTITION BY car_number
      ORDER BY window_time
      RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS forecast_result
  FROM windowed
)
SELECT
  lap,
  window_time AS forecast_generated_at,
  tire_temp_fl_c AS current_tire_temperature_c,
  forecast_result.forecast AS tire_temperature_forecast,
  forecast_result.metadata AS forecast_metadata
FROM forecasted
WHERE CARDINALITY(forecast_result.forecast) > 0;
```

Each result contains a three-element forecast array. Each element has a future
timestamp and predicted mean. The metadata identifies `ttm`, confirming that
Granite served the forecast. `minContextSize = 20` means the model needs about
3½ minutes of 10-second windows before it can forecast.

This SELECT is only an experiment. After you see forecast rows, stop the
statement in the SQL workspace so LAB 4 has the full compute pool available.
The checked-in copy is
`granite_tire_forecast.sql`.

#### Step 4 (optional): Publish `car_state` to the Real-Time Context Engine

`car_telemetry` is already published to Confluent's Real-Time Context Engine,
so an AI agent can query the sensor stream over MCP without a Kafka client or a
consumer group. `car_state` isn't, for a simple reason: you just created it, so
it didn't exist when your environment was built.

`race_standings` is intentionally not published. It is a compacted upsert topic,
and RTCE currently rejects queries against it in this workshop environment. The
Flink temporal join in Step 1 still uses its upsert semantics normally.

Turning it on is one toggle:

1. In the Console, go to your cluster → **Topics** → `car_state`.
2. Open the **Real-Time Context Engine** panel and switch it from **Off** to
   **On**.
3. Give it a description. This one matters: the agent *reads* the description to
   decide whether the topic answers a question. Something like:

   > Per-10-second enriched state for River Racing car #88: track position, tire
   > compound and age, front-left tire temperature, and an anomaly flag that fires
   > when the front-left tire is running abnormally hot.

Confluent materializes the topic into a lookup-optimized table, and the three MCP
tools (`list_topics`, `get_metadata`, `query_data`) pick it up automatically.

> Enablement is **per topic**, and a topic needs a registered schema — which the
> `CREATE TABLE` above gave you. If your credential card has an "Ask an AI agent
> about the live race" section, it holds a one-line `claude mcp add` command with
> your endpoint and credentials already filled in.

### Conclusion

`car_state` is the live, enriched, anomaly-aware view of the car, and Granite
can forecast its source telemetry without changing that proven anomaly path.
Feed `car_state` to the AI agent in
LAB 4.

## Lab 4 — Streaming Agent: Pit Decisions

### Overview

Now the payoff: an **AI Streaming Agent** evaluates every `car_state` row — one per
10-second window — and recommends `PIT NOW` / `PIT SOON` / `STAY OUT`, with a
recommended compound and natural-language reasoning. The agent uses the
pre-deployed `llm_textgen_model` (AWS Bedrock / Claude) — no connection setup
required.

#### What you'll accomplish

1. Create the `pit_strategy_agent`
2. Run the agent over `car_state` to produce `pit_decisions`
3. Watch the agent call `PIT NOW` at the anomaly

#### Prerequisites

LAB 3 — `car_state` is running and shows the
lap-32 anomaly.

### Steps

#### Step 1: Create the agent

Paste the full statement below into one SQL workspace cell and run it. The\ncomments are included so this file stays synchronized with the canonical SQL.\n\n```sql\n-- Job 2a: Pit Strategy Agent — CREATE AGENT
-- Input: car_state
-- Output: used by streaming_agent_pit_decisions.sql
--
-- llm_textgen_model is pre-deployed via Terraform — no CREATE CONNECTION or
-- CREATE MODEL needed. Run this statement first, then run
-- streaming_agent_pit_decisions.sql.

-- 1. RTCE Connection — for later when RTCE is fully enabled.
--    Competitor context is currently provided via a direct JOIN with race_standings
--    (see CREATE TABLE pit_decisions below). Uncomment once your RTCE endpoint is active.
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
--    (from the JOIN with race_standings in the CREATE TABLE below).
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
WITH ('max_iterations' = '10');\n```\n\nConfirm it was created:

```sql
SHOW AGENTS;
```

#### Step 2: Produce `pit_decisions`

Run the second statement, from
`demo-reference/streaming_agent_pit_decisions.sql`.
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

#### What to expect

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

### Conclusion

You've built an end-to-end real-time AI pit strategist. Next, put that live feed
in front of a business user: build a social-media agent in
LAB 5.

## Lab 5 — Social Media Agent (IBM watsonx Orchestrate)

### Overview

Change hats: you're now River Racing's **social-media manager**. The same live
feed your Flink pipeline produces — standings, the tire anomaly, the AI pit call —
is great content. In this lab you'll build a **no-code agent in IBM watsonx
Orchestrate** that reads that live feed and drafts on-brand social posts about the
race, on demand, in chat.

No SQL and no code here: you build the agent in the Orchestrate **Agent Builder**
UI and point it at a race-feed tool your instructor is hosting.

#### What you'll accomplish

1. Open watsonx Orchestrate Agent Builder
2. Add the **race-feed** tool (an OpenAPI import)
3. Create the **Social Media Manager** agent with a River Racing persona
4. Chat with it to draft live race posts — including the lap-32 drama

#### Prerequisites

- LAB 4 — `car_state` and `pit_decisions` exist
  and a race is running, so the feed has tire/anomaly and pit-call data to post
  about. (Standings alone work even before LAB 3/4 — those fields just stay empty.)
- **IBM watsonx Orchestrate access** — provided by your instructor during the
  workshop; there's nothing to sign up for in advance.
- The **race-feed base URL** — your instructor shares this. It's one shared
  service that serves everyone; you select your own race with your prefix.

> The canonical agent persona, instructions, and example prompts also live in
> [Appendix A](#appendix-a-river-racing-social-agent-instructions).

### Steps

#### Step 1: Add the race-feed tool

In watsonx Orchestrate, open **Agent Builder** and go to **Tools → Add tool →
Import from OpenAPI**. Give it the spec URL — the **race-feed base URL** your
instructor shared, with `/openapi.json` appended:

```
<race-feed-base-url>/openapi.json
```

Import the **`get_race_feed`** operation (`GET /race-feed/{prefix}`). It takes one
parameter, `prefix`, and returns the current race digest — standings, our tire
status, the latest pit recommendation, and a list of recent **headline events**.

> **Why an OpenAPI tool?** Orchestrate agents pull data by calling tools. The
> feed service tails your Kafka topics (`race_standings`, `car_state`,
> `pit_decisions`) and serves a compact, post-ready digest — so the agent always
> writes from live data, never guesses.

#### Step 2: Create the agent

**Agents → Create agent.** Name it `River Racing Social`, and paste the
instructions from
[Appendix A](#appendix-a-river-racing-social-agent-instructions)
into the agent's instructions field. The short version of what they tell the agent:

- You're the social-media manager for River Racing — John Doe, car #88, at
  Silverstone.
- **Always call `get_race_feed` first** (with your `prefix`) and post only from
  what it returns — never invent positions or events.
- Lead with the most recent **headline event**; flag any `PIT NOW` / `PIT SOON`.
- Voice: upbeat, fan-facing, under 280 chars, 1–3 emoji, end with 2–3 hashtags
  (`#RiverRacing #JohnDoe #F1 #BritishGP #Silverstone`).
- These are **drafts** for a human to review — don't claim to have posted them.

Attach the `get_race_feed` tool to the agent, and set the `prefix` value to **your
prefix** (e.g. `f1wp001`) — the same one on your credential card.

#### Step 3: Draft a post

In the agent preview chat, try:

```
Draft a hype post about where we are in the race right now.
```

The agent calls `get_race_feed`, then writes a post grounded in the live feed —
for example, around the lap-32 anomaly:

> 🚨 Drama at Silverstone! A front-left tire issue forces the #88 into the pits —
> John Doe boxes from P8. Fresh mediums on, time to charge back. 💪
> #RiverRacing #JohnDoe #BritishGP

Then try a few more (also in the reference doc):

- "We just made a big move — write a post celebrating it."
- "The pit wall just made a call. Draft a post about our strategy."
- "Write a 3-tweet recap thread of John's race so far."

Iterate on the instructions — tone, emoji, hashtags — and watch the drafts change.

#### What to expect

| Race moment | What the feed shows | A good post leads with… |
|-------------|---------------------|--------------------------|
| Early laps | Stable top order, John in the pack | "We're in the fight at Silverstone" |
| ~Lap 32 | `tire.anomaly = true`, `PIT NOW` | The tire drama + box call |
| Recovery | Climbing positions in `headline_events` | The fightback up the order |

> If the agent says the feed is quiet, the race may not be running or LAB 3/4
> aren't built yet — standings post fine, but tire/pit content needs the full
> pipeline live. See [Troubleshooting](#troubleshooting).

### Conclusion

You've taken a real-time streaming pipeline all the way to a business user: a
no-code agent that turns live race data into publishable content. Wrap up in
LAB 6.

## Lab 6 — Wrap-Up

### Overview

Review what your pipeline produced and reflect on what you built.

#### Prerequisites

LAB 5 — your Orchestrate social agent
is drafting posts (and `pit_decisions` from LAB 4
is populating).

### Steps

#### Step 1: See every non-trivial call

```sql
SELECT lap, `position`, suggestion, condition_summary, reasoning
FROM `pit_decisions`
WHERE suggestion <> 'STAY OUT';
```

Rows stream in roughly in lap order (`pit_decisions` follows `car_state`, which is
produced window by window), so
you'll see the `PIT SOON` warnings as the tire degrades, then the decisive
`PIT NOW` at the anomaly around lap 32 — each with the agent's reasoning.

> **Why no `ORDER BY lap`?** In a continuous Flink query, `ORDER BY` is only
> supported on a time-attribute column — sorting an unbounded stream on a plain
> field like `lap` raises *"Sort on a non-time-attribute field is not supported."*

#### Step 2: Inspect the key decision

```sql
SELECT lap, `position`, tire_compound_current, tire_age_laps,
       anomaly_tire_temp_fl, suggestion,
       recommended_tire_compound, recommended_stint_laps, reasoning
FROM `pit_decisions`
WHERE anomaly_tire_temp_fl = true;
```

This is the moment the AI earned its keep: an overheating front-left tire, a
`PIT NOW` call, and a recommended MEDIUM compound for the rest of the race.

#### Step 3 (optional): Trace the pipeline

You built a three-stage streaming pipeline:

```
car_telemetry + race_standings  →  car_state  →  pit_decisions
```

Confirm each stage is still live from your shell:

```sql
SHOW TABLES;        -- car_state and pit_decisions now sit alongside the sources
SHOW AGENTS;        -- pit_strategy_agent
```

> For the same graph visually, open the **Stream Lineage** view in your
> environment — every table you built shows up as a node.

### What you built

- A continuously enriched, anomaly-aware `car_state` view joining a live event
  stream to a versioned (upsert) standings table by event time.
- A Flink **Streaming Agent** that calls an LLM per lap and turns raw telemetry
  into an explainable pit-strategy decision — all in SQL, no application code.
- A no-code **IBM watsonx Orchestrate** agent that reads the same live feed and
  drafts on-brand social posts — the streaming pipeline reaching a business user.

### Reset (if you want to run it again)

Your race feed loops continuously, so you can re-run the labs anytime. To clear
your lab objects (`car_state`, `pit_decisions`, `pit_strategy_agent`) and start
fresh, drop them in your SQL workspace:

```sql
DROP TABLE IF EXISTS `pit_decisions`;
DROP TABLE IF EXISTS `car_state`;
DROP AGENT IF EXISTS `pit_strategy_agent`;
```

…or ask your instructor to reset the workshop fleet.

### Done 🏁

Thanks for racing with River Racing. Sign out when you're finished — your
instructor will tear down the environments afterward, and your workshop account's
password is rotated at teardown.

## Appendix A: River Racing social-agent instructions

### Agent profile

- **Name:** `River Racing Social` (or `Social Media Manager`)
- **Description:** Drafts on-brand social posts about River Racing's race from the
  live race feed.

### Instructions (paste into the agent's instructions field)

```
You are the social-media manager for the River Racing Formula 1 team. Our driver
is John Doe (car #88) racing the British Grand Prix at Silverstone (60 laps).

Your job: when asked, draft short, high-energy social posts about what is
happening in OUR race, grounded in live data.

DATA
- Always call the get_race_feed tool with prefix "<your-prefix>" to get the current
  race situation before writing. Never invent positions, gaps, lap numbers, or
  events — use only what the tool returns.
- The headline_events list is your best source of post hooks (overtakes, the
  tire anomaly, the pit call). Lead with the most recent meaningful event.
- If latest_pit_decision is PIT NOW or PIT SOON, that is newsworthy — say so.
- If the tool returns live = false or empty events, say the race feed is quiet
  rather than making something up.

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

### Optional knowledge (RAG)

Attach a short brand-voice doc (sponsors, tone do's/don'ts, approved hashtags) as
a knowledge source if you want richer, more consistent posts. Not required.



### Example chat prompts (for the lab + demo)

- "Draft a hype post about where we are in the race right now."
- "We just made a big move — write a post celebrating it."
- "The pit wall just made a call. Draft a post about our strategy."
- "Write a 3-tweet recap thread of John's race so far."

### Expected behavior

The agent calls `get_race_feed`, then drafts a post citing real data, e.g. around
the lap-32 anomaly:

> 🚨 Drama at Silverstone! A front-left tire issue forces the #88 into the pits —
> John Doe boxes from P8. Fresh mediums on, time to charge back. 💪
> #RiverRacing #JohnDoe #BritishGP

…and later in the climb:

> 📈 What a fightback! John Doe is up to P2 on fresh rubber and the fastest car on
> track. The #88 is flying. 🏎️ #RiverRacing #F1 #Silverstone

## Troubleshooting

### Can't sign in to Confluent Cloud

- Use the **Console Username** on your card — a workshop account ending in
  `+f1wp###@confluent.io`. Your own work email won't find your environment.
- Copy the password exactly; they're generated and contain symbols. A trailing
  space from a copy-paste is the usual culprit.
- If it still fails, the account's password may have been rotated after your card
  was printed. Ask your instructor for a fresh one.

### Signed in, but I see no environment (or the wrong one)

You're granted access to exactly one environment, `RIVER-RACING-f1wp###-ENV`. If
the environment list is empty, your access grant didn't land — tell your
instructor, it's a provisioning problem on their side, not something you can fix.

### `SHOW TABLES` is empty in the SQL workspace

Check the **catalog** and **database** dropdowns above the editor. They must be
set to your environment and your cluster (`RIVER-RACING-f1wp###-CLUSTER`) — a
workspace pointed somewhere else runs fine and shows nothing.

### `car_telemetry` / `race_standings` look idle

The race simulator runs as an always-on service that replays the race
back-to-back (`RACE_LOOP=true`). Between races there's a short pause, and laps
arrive at the configured pace (default 60s/lap). Re-run your `SELECT` after a
few seconds. If nothing arrives for several minutes, tell your instructor — only
they can inspect or restart the simulators (`uv run workshop start-races`, run
from the organizer's machine with AWS access; you have Confluent API keys only).

> Tip: a streaming `SELECT` keeps running until you hit **Stop**.

### `race_standings` has data but `car_state` is empty

`car_state`'s temporal join needs **both** `car_telemetry` and `race_standings`
to have data with advancing watermarks, and `ML_DETECT_ANOMALIES` withholds
output until it has ~20 windows. Give it a couple of minutes of live data.

Also confirm the join is written on the raw `event_time` (as in the LAB 3 SQL).
If you moved the `FOR SYSTEM_TIME AS OF` join after the `TUMBLE`, `window_time`
loses its rowtime attribute and the join silently returns zero rows.

### No anomaly around lap 32

- `ML_DETECT_ANOMALIES` needs at least 20 data points (`minTrainingSize=20`), so
  it can't fire on the early laps.
- Verify the spike is present:
  ```sql
  SELECT lap, tire_temp_fl_c FROM `car_state` WHERE lap BETWEEN 30 AND 34;
  ```
  You should see `tire_temp_fl_c` jump to ~145°C.

### The agent outputs odd or empty fields

- Inspect the raw model output:
  ```sql
  SELECT lap, suggestion, raw_response FROM `pit_decisions` ORDER BY lap DESC LIMIT 5;
  ```
- If `suggestion` is null but `raw_response` has text, the LLM emitted a slightly
  different label format — the `\*{0,2}` markers in the regex tolerate optional
  markdown bold, but unusual phrasing can still slip through. Re-running usually
  resolves it.
- If `raw_response` is empty/erroring across the board, the shared Bedrock quota
  may be throttling many attendees at once — flag it to your instructor.

### `SHOW MODELS;` / `SHOW AGENTS;` returns nothing

This almost always means the workspace is pointed at the wrong catalog. The
models are pre-deployed per environment, so they only appear when the catalog is
your own `RIVER-RACING-f1wp###-ENV`.

## Lab 5 — Orchestrate agent / race-feed tool

- **Tool import fails.** Make sure you imported the spec URL your instructor gave
  you, ending in `/openapi.json` (not the `/race-feed/...` path itself). The
  service must be reachable from Orchestrate — if your instructor is still
  bringing it up, give it a minute.
- **Agent returns "no race feed for prefix …" (404).** The `prefix` on the tool
  must match your credential card (e.g. `f1wp001`). Set it on the tool, or pass it
  in chat, exactly as written on your card.
- **Posts have standings but no tire/anomaly/pit content.** Those fields
  (`tire`, `latest_pit_decision`) are empty until you've built LAB 3 / LAB 4 and a
  race is running. Standings-only posts are expected before then.
- **Agent says the feed is quiet / `live` is false.** No record has arrived
  recently — the race may be between loops or stopped. Re-ask after a few seconds,
  or ask your instructor to confirm the feed is running (that's theirs to restart,
  not yours).
- **Agent invents positions or events.** Re-check the instructions tell it to
  *always call `get_race_feed` first* and post only from the returned data (see
  [Appendix A](#appendix-a-river-racing-social-agent-instructions)).

### I want to start over

Drop your lab objects and re-run LAB 3 → LAB 4:

```sql
DROP TABLE IF EXISTS `pit_decisions`;
DROP TABLE IF EXISTS `car_state`;
DROP AGENT IF EXISTS `pit_strategy_agent`;
```

The source tables and the live feed are untouched, so you can rebuild
immediately.
