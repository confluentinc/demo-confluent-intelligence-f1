# Self-service workshop walkthrough

![F1 Pit Wall Confluent Intelligence architecture](../assets/architecture.png)

> [!NOTE]
>
> Use this path when you have your own Confluent Cloud login and password, and will provision your own environment. **If your instructor gave you a workshop login and password,** use the [hosted workshop walkthrough](./HOSTED-WORKSHOP.md).

## Before you start

You need a [Confluent Cloud account](https://confluent.cloud/signup), as well as AWS Bedrock API keys in `us-east-1` region. If you don't have these, you can easily create them yourself with `uv run api-keys create` if you are logged into the AWS CLI.

## Clone, install, and provision

1. Install the prerequisites: 
    <details>
    <summary>for Mac</summary>

    ```bash
    brew install git uv
    brew tap hashicorp/tap
    brew install hashicorp/tap/terraform
    brew install --cask confluent-cli
    ```

    </details>

    <details>
    <summary>for Windows</summary>

    ```powershell
    winget install --id Git.Git -e
    winget install --id astral-sh.uv -e
    winget install --id Hashicorp.Terraform -e
    ```

    The Confluent CLI has no reliable winget package. Download the latest `confluent_*_windows_amd64.zip` from the [Confluent CLI releases](https://github.com/confluentinc/cli/releases/latest), unzip it, and add the folder containing `confluent.exe` to your `PATH`. Then close and reopen the terminal so every tool is on your `PATH`.

    </details>

2. Clone the repo:

    ```bash
    git clone https://github.com/confluentinc/demo-confluent-intelligence-f1.git
    cd demo-confluent-intelligence-f1
    ```

3. Sign into Confluent Cloud:

    ```bash
    confluent login
    ```

4. Finally, provision the environment with the following command:

    ```bash
    uv run selfservice up
    ```

    The command asks for your Confluent credentials, email, and AWS Bedrock credentials. It creates the workshop infrastructure you'll need for following steps. 

## Enhance the data with built-in ML functions and start the race

The prerequisites set up some basic source topics for car telemetry and race details. However, we want to enhance these with Confluent's built-in AI capabilities. In this section, we will create a new `car-state` topic that uses Confluent's built-in `ML-DETECT_ANOMALIES` function to identify any anomalies in the car's tire temperature. If there are any anomalous tire temperatures reported, that is a clear sign that the car needs to pit. 

1. Open the [SQL workspace](https://confluent.cloud/workspaces/) in the Confluent Cloud Console. Select `RIVER-RACING-DEMO-ENV` as the catalog and `RIVER-RACING-DEMO-CLUSTER` as the database.

2. Paste this statement into one SQL cell and run it. Wait for it to show **Running**.

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
        TUMBLE(TABLE enriched, DESCRIPTOR(event_time), INTERVAL '20' SECOND)
      )
      GROUP BY window_start, window_end, window_time, car_number
    ),
    anomaly AS (
      SELECT
        *,
        ML_DETECT_ANOMALIES(tire_temp_fl_c, window_time,
          JSON_OBJECT('minTrainingSize' VALUE 12,
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

3. Now run this command in your local terminal to start the race: 

    ```bash
    uv run f1-race
    ```

    Leave it running. `car_state` emits one row per 20-second window. After 12 windows it has enough history to detect temperature anomalies of the left front tire.

4. In a **second terminal**, open the Pit Wall dashboard:

    ```bash
    uv run f1-pitwall
    ```

    A browser opens at http://localhost:8000 with the Silverstone track map, the live leaderboard, and car #88's tyre/fuel gauges.

    ![Pit Wall dashboard showing a nominal front-left tire temperature](../assets/self-service/pitwall-nominal.png)

5. Verify the stream in a new SQL cell:

    ```sql
    SELECT car_number, lap, `position`, tire_compound, tire_age_laps, anomaly_tire_temp_fl, tire_temp_fl_c
    FROM `car_state`;
    ```

## (Optional) Forecast tire temperature with Granite Time Series models

You can also try the built-in IBM Granite TinyTimeMixer model to forecast future tire temperatures. 

Run the following statement in your SQL Workspace **only after `car_state` produces rows**. Stop the query after you inspect the result so Lab 4 can use the compute pool.

```sql
WITH windowed AS (
  SELECT
    window_start,
    window_end,
    window_time,
    car_number,
    MAX(lap) AS lap,
    AVG(tire_temp_fl_c) AS tire_temp_fl_c
  FROM TABLE(
    TUMBLE(TABLE `car_telemetry`, DESCRIPTOR(event_time), INTERVAL '20' SECOND)
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
        'horizon' VALUE 20,
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
  forecast_result.forecast[1].`timestamp` AS next_point_at,
  forecast_result.forecast[1].mean AS next_point_c,
  forecast_result.forecast AS full_forecast,
  forecast_result.metadata AS forecast_metadata
FROM forecasted
WHERE CARDINALITY(forecast_result.forecast) > 0;
```

This query uses IBM Granite **TinyTimeMixer** (`'model' VALUE 'ttm'`), a compact pre-trained time-series foundation model. You can swap the `model` value to try other built-in foundation forecasters; Google's TimesFM 2.5 is the default. See [Forecast Data Trends](https://docs.confluent.io/cloud/current/ai/builtin-functions/forecast.html) in the Confluent Cloud documentation.

## Create the agent to provide pit decision guidance 

With the `car-state` data in place, we can take another step towards using AI to inform race decisions. In this section, we will create a `pit-strategy-agent` that takes in car state and race data, and uses that to determine whether the car should stay out, pit soon, or pit now. 

1. Paste and run the CREATE AGENT statement in your SQL Workspace. This statement defines the agent and its instructions. 

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
    Recommended Stint Laps: 36
    Recommended Reason: Mediums will carry John to the flag across the remaining 36 laps and give him the pace to recover positions lost during the stop.
    Reasoning: The FL anomaly flag indicates the SOFT has gone past its operating limit with blowout risk. Pitting now onto mediums avoids tire failure. Based on historical data, John averages +2.75 positions on SOFT-MEDIUM — this is his strongest strategy.

    ---

    You are the AI pit wall strategist for River Racing at the 2026 British Grand Prix (Silverstone, 60 laps).
    Driver: John Doe, Car #88.

    DECISION FRAMEWORK — use this priority order to reason about the Suggestion.
    This is guidance for judgment, not a rule to execute mechanically.

    PIT NOW vs PIT SOON — these mean different things and are not interchangeable
    labels for "pit now would be reasonable." PIT NOW means urgent: a validated
    problem exists and the car should come in immediately for safety. PIT SOON
    means strategic: the tires are aging into their normal pit window and a stop
    is coming, even if stopping on this exact lap would itself be the sound
    strategic choice. A strategy-driven stop stays PIT SOON for its entire
    window; it does not become PIT NOW just because the ideal moment has arrived.

    1. Anomaly signal: anomaly_tire_temp_fl comes from a trained statistical anomaly
       detector (ML_DETECT_ANOMALIES) that has already evaluated tire_temp_fl_c
       against expected bounds for this car at this point in the race. PIT NOW is
       reserved for this signal being true — treat it as strong, validated evidence
       of a real tire problem serious enough to justify an urgent stop. When it is
       false, there is no statistical evidence of a problem: a raw temperature
       reading that merely looks high to you has already been checked and found
       within the expected range for this stage of the stint, so weigh that check
       heavily before treating anything as urgent on the strength of a number alone.
       Nothing else in this input — not tire age, not race context, not competitor
       behavior — should produce PIT NOW; those inform PIT SOON or STAY OUT instead.
    2. Pit history: a car that has already pitted this race is usually better off
       staying out and running its current tires to the end, absent a new problem.
    3. Tire age and compound: a SOFT tire that has run past roughly 20 laps starts
       trending into a strategic pit window. This is PIT SOON, not PIT NOW, for as
       long as that window lasts — even on the lap where pitting would be ideal —
       since pace typically falls off after that point but there is no validated
       safety issue driving urgency.
    4. Otherwise: STAY OUT is the reasonable default when nothing above points to a
       reason to change strategy.

    Weigh these in order of severity, and keep the PIT NOW/PIT SOON distinction
    above intact regardless of how you weigh them — you are reasoning about a race
    strategy call, not executing a lookup table, but the two labels still mean
    different things. Use the TIRE DATA, RACE CONTEXT, and COMPETITOR CONTEXT
    below to inform your Reasoning field regardless of which Suggestion you land on.

    Correct STAY OUT despite a high-looking temperature example:
    Suggestion: STAY OUT
    Condition Summary: Front-left tire temperature reads 118C, which may look elevated, but the anomaly detector has not flagged it as unusual for this stage of the tire life.
    Race Context: Currently P5. Field is spread out, no pit activity nearby.
    Recommended Compound: N/A
    Recommended Stint Laps: N/A
    Recommended Reason: N/A
    Reasoning: No anomaly has been detected, so there is no validated evidence of a tire problem despite the reading looking high in isolation. Pit stops are at zero and tire age is still well under the strategic pit window, so there is no other reason to come in. Staying out preserves track position.

    STRICT OUTPUT VALUES — the Suggestion field must be exactly one of these three
    literal strings, with no other characters, punctuation, or words on that line:
    PIT NOW
    PIT SOON
    STAY OUT
    No variants, synonyms, qualifiers, or explanatory text are permitted on the
    Suggestion line (e.g. never "PIT NOW - tire failure risk" or "Stay out for now").
    Downstream systems match this field with exact string equality.

    SELF-CHECK before responding: re-read your Suggestion against the TIRE DATA and
    RACE CONTEXT above and confirm your Reasoning genuinely explains it. If the
    Reasoning and the Suggestion disagree with each other, fix whichever one is
    actually wrong.

    COMPETITOR CONTEXT:
    Current top-10 standings are provided at the end of each input. Use them to identify:
    - Which competitors have already pitted (and are now on fresher rubber)
    - Who is still on old tires and likely to pit soon
    - Whether John is at risk of being undercut, or has an overcut opportunity

    TIRE STRATEGY at Silverstone (60-lap race):
    - SOFT: High-grip compound. Optimal window is laps 1-19. Still competitive laps 20-22 with some pace loss and position drops — but no failure risk unless the anomaly sensor fires. Performance cliff begins around lap 18-20.
    - MEDIUM: Balanced compound, best for a 30-40 lap second stint after a SOFT first stint. Enables clean 1-stop strategy.
    - HARD: Very durable but slow. Only consider if 40+ laps remain at the second stop.
    - John Doe historical best: SOFT first stint → MEDIUM second stint (1-stop) averages +2.75 positions over 4 prior races. The pit wall warns at laps 21-23, calls PIT NOW only when the lap-24 anomaly fires, then lets the fresh MEDIUM stint run.

    REMINDER: For any STAY OUT decision, write N/A for Recommended Compound, Recommended Stint Laps, and Recommended Reason.'
    WITH ('max_iterations' = '10');
    ```

2. Confirm that the agent is created and registered properly.

    ```sql
    SHOW AGENTS;
    ```

## Create the pit decisions table

Next we create the `pit_decisions` table, which holds the Streaming Agent's recommendations and output. As part of this command, we invoke the Streaming Agent using `AI_RUN_AGENT`. [See documentation for AI_RUN_AGENT here.](https://docs.confluent.io/cloud/current/flink/reference/functions/model-inference-functions.html#ai-run-agent)

1. Create the `pit-decisions` table by running the following statement in your SQL Workspace. 

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
      TRIM(REGEXP_EXTRACT(CAST(response AS STRING), '\*{0,2}Suggestion:\*{0,2}\s*([^\n]+)', 1)) AS suggestion,
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

## 5. Review the results

1. Run the following statement in your SQL Workspace to see the details included in the `pit-decisions` table. 

  ```sql
  SELECT lap, `position`, suggestion, condition_summary, reasoning
  FROM `pit_decisions`;
  ```

![Flink SQL workspace showing pit_decisions results progressing from PIT SOON to PIT NOW](../assets/self-service/pit-decisions-query.png)

2. Run the following statement in your SQL Workspace to see how the pit decision changes when tire temperature anomalies occur. 

    ```sql
    SELECT lap, `position`, tire_compound_current, tire_age_laps,
          anomaly_tire_temp_fl, suggestion,
          recommended_tire_compound, recommended_stint_laps, reasoning
    FROM `pit_decisions`
    WHERE anomaly_tire_temp_fl = true;
    ```

![Flink SQL workspace showing the lap 24 PIT NOW row with the agent's full reasoning](../assets/self-service/pit-decisions-anomaly-query.png)

> [!NOTE]
>
> At lap 24, the result should include the only `PIT NOW`, for the front-left tire anomaly. Laps 21–23 show `PIT SOON`; lap 25 returns to `STAY OUT` on fresh MEDIUMs. The Pit Wall unlocks its anomaly and AI strategist panels as the tables begin producing:

![Pit Wall dashboard showing the lap 24 anomaly and unlocked AI Pit Strategist panel](../assets/self-service/pitwall-anomaly.png)


## (Optional) Connect race feed to WatsonX Orchestrate

To run the local race-feed service for watsonx Orchestrate:

```bash
uv run f1-social-feed --creds runs/selfservice/credentials/DEMO.env
```

Expose port 8080 through an approved HTTPS tunnel, set `servers[0].url` in `docs/assets/orchestrate/f1-race-feed-openapi.json` to that public URL, and follow [Lab 5 in the hosted walkthrough](./HOSTED-WORKSHOP.md#lab-5-social-media-agent-ibm-watsonx-orchestrate).

## (Optional) Expose `car_telemetry` to AI agents with Real-Time Context Engine

Real-Time Context Engine (RTCE) exposes a Kafka topic as an MCP tool, so any MCP-compatible AI agent can query the topic. `uv run selfservice up` minted an RTCE API key onto your credential card, but RTCE is enabled **per topic in the Console** — nothing is pre-enabled, so turn it on for `car_telemetry` yourself.

1. Under your environment's cluster, open **Topics → `car_telemetry`**, open the **Real-Time Context Engine** panel, click **Enable**. Once enabled, the **Topics** list shows Real-Time Context Engine **On**:

    ![Topics list with the Real-Time Context Engine column](../assets/self-service/rtce-topics-list.png)

    The panel shows the enablement details — environment, cluster, cloud, and region — that an MCP client needs to reach it:

    ![Real-Time Context Engine enabled for car_telemetry](../assets/self-service/rtce-enabled.png)

2. Point your coding agent at it in one command:

    ```bash
    uv run setup-rtce
    ```

    This registers RTCE as an MCP server with Claude Code (or prints a config snippet for Codex CLI), using the `F1_RTCE_MCP_ENDPOINT`/`F1_RTCE_API_KEY`/`F1_RTCE_API_SECRET` fields already on your card. Then just ask, in plain English: *"What are car 88's front-left tire temperatures over the last few laps?"*

    Once Real-Time Context Engine is enabled on `car_telemetry`, and you've run `uv run setup-rtce` to connect Claude or Codex, you can ask your LLM questions like:

    - *At what lap in the race does the engine temperature peak?*
    - *What is the front right tire temperature at lap 30?*

### Optional: Lightning Queries (low-latency REST)

Lightning Queries read an RTCE-enabled topic over low-latency REST, so enable RTCE on `car_telemetry` in the Console first (see above) if you haven't already.

1. From the repo directory, print a ready-to-run query:

    ```bash
    uv run setup-rtce --lightning
    ```

    Copy the printed `curl` command into your terminal and run it. It returns the last 10 telemetry rows by lap; edit the SQL in `query` to filter for car 88 or select other columns. The command reads your existing credential file and derives the region and cloud from its RTCE endpoint. Use `--creds path/to/file.env` if you have multiple credential files.

    Lightning Queries require a **Global API key**, the same key used by RTCE's MCP interface. The printed command contains its authentication token; keep it private.

## Run the workshop again or tear it down

Stop `f1-race` with Ctrl-C when you finish. Use reset only when you intend to erase the race history and repeat the workshop from a clean slate:

```bash
uv run reset
```

After reset, recreate `car_state`, wait for it to show **Running**, then start `uv run f1-race` again.

When you're ready to tear down all resources associated with the lab, run:

```bash
uv run selfservice down
```

---

**← Back to Overview**: [Main README](../../README.md)
