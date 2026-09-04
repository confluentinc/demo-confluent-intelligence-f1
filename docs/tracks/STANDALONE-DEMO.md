# Standalone Demo — End-to-End Walkthrough

![F1 Pit Wall Confluent Intelligence architecture](../assets/architecture.png)

> [!NOTE]
>
> **Deploying your own single-environment demo with Terraform, outside a workshop?** You're in the right place. **Did your instructor give you a workshop login?** Use the [hosted workshop walkthrough](./HOSTED-WORKSHOP.md) instead. **Provisioning your own environment without Terraform, via `uv run selfservice up`?** Use the [self-service workshop walkthrough](./SELF-SERVICE.md) instead.

---

## 0. Prerequisites

| Need | Check |
|------|-------|
| `uv` | `uv --version` |
| Terraform ≥ 1.3 | `terraform version` |
| Docker running | `docker info` (builds the simulator image once) |
| AWS CLI configured | `aws sts get-caller-identity` |
| Confluent CLI — **optional** | `confluent version` (needed only if you ask `deploy` to mint a Confluent API key, below) |
| AWS Bedrock keys | see below |

Mint Bedrock keys if you don't have them (creates a scoped IAM user, `InvokeModel` only):

```bash
uv run api-keys create
```

The keys land in `API-KEYS-AWS.md` (gitignored) and are written straight into
`credentials.env`, so the deploy prompts will already have them filled in. Revoke after
the demo with `uv run api-keys destroy`.

You also need a Confluent Cloud API key pair with OrganizationAdmin. `uv run deploy`
can generate one for you — answer `y` at the first prompt, which is the **only** step
that uses the Confluent CLI (it logs in, mints the key, and saves it). Already have a
key? Answer `n` (the default), paste it, and you never log in: Terraform's Confluent
provider authenticates with the key alone.

---

## 1. Deploy

Every command in this doc runs from the repo root:

```bash
cd "$(git rev-parse --show-toplevel)"
uv run deploy
```

It prompts for the Confluent API key/secret, an owner email (AWS tagging), a prefix,
the Bedrock keys, and **seconds per lap**. Answers are saved to `credentials.env`, so a
re-run is `uv run deploy --automated`.

The prefix is **suggested, not blank**: it's derived from your `$USER` (or a hash of
the owner email on a shared login), so two people in one Confluent org or one AWS
account don't collide, and every later `race` / `reset` / `destroy` resolves the same
names. Accept it or type your own (alphanumeric, ≤ 12 chars). It's recorded in
`runs/standalone/deployment.env` — separate from the self-service track's copy, so both
can live in one checkout. A **deployed** prefix can't be renamed in place; tear down
first.

> **One warning about renaming.** The shared tier is named `f1-<prefix>`, and the ECR
> repository inside it is account-global. Changing the prefix with shared state already
> applied deletes and recreates that repository, rebuilds and re-pushes the simulator
> image, and revises the ECS task definition — which restarts a running race. `deploy`
> detects the mismatch and asks; under `--automated` it **refuses** rather than doing it
> unattended. To keep the existing shared infrastructure under a new attendee prefix:
> `export F1_SHARED_PREFIX=<the deployed name>`.

On pacing: the default is **20s/lap**, which makes a 60-lap race take 20 minutes and
puts the anomaly — the payoff of the whole demo — ~8 minutes in (lap 24). This must
match the fixed 20-second `TUMBLE` window in the LAB 3 SQL (one window per lap), so
changing the pace requires a matching SQL-window change. Below 10s/lap
`ML_DETECT_ANOMALIES` can't accumulate its 12 training windows before lap 24 and the
anomaly never fires.

**Want the pipeline built for you** instead of typing LAB B and LAB C yourself?

```bash
uv run deploy --with-labs
```

Same deploy, plus the three lab objects from `docs/demo-reference/` submitted for you and the
race restarted behind them (in that order — see §7 for why). §4 and §5 then become
verification steps rather than build steps. Omit the flag to get exactly what a workshop
attendee gets: a bare environment where building `car_state` and `pit_decisions` *is* the
exercise.

Two Terraform applies run back to back:

1. **`terraform/aws-shared`** — VPC lookup, Postgres (seeded with 198 rows of historical
   race data; `t3.small` here, where the multi-attendee workshop keeps the `t3.large`
   default — override with `TF_VAR_postgres_instance_type`), ECR repo + the simulator
   Docker image.
2. **`terraform/aws`** — Confluent environment, cluster, Schema Registry, Flink pool,
   Bedrock connections + LLM models, topics, the Postgres CDC connector, and an ECS
   service running the simulator.

Takes ~25–30 minutes, mostly Postgres and the Docker build. When it finishes it prints
the path to your credential card.

> If the attendee apply fails, the shared layer is still up. Fix and re-run
> `uv run deploy --automated` — Terraform picks up where it left off.

---

## 2. Point your tools at the environment

Nothing to do here — this section is just so you know what's happening.

`f1-sql` and `f1-pitwall` authenticate with API keys from a **credential card**, not a
Confluent Cloud login. `uv run deploy` wrote that card to
`runs/standalone/credentials/<prefix>.env` and recorded its path as `F1_CARD` in
`credentials.env`, so the tools find it on their own — in any terminal, with no flags
and nothing to export.

Pass `--creds <path>` if you ever need to point at a different environment.

The race feed is already live — the simulator is an always-on ECS service
(`RACE_LOOP=true`) that replays the race back to back.

### Open the dashboard

In a **second terminal**:

```bash
uv run f1-pitwall
```

A browser opens at http://localhost:8000 — Silverstone track map, 22 cars, live
leaderboard, car #88's tyre and fuel gauges. Two panels are locked:

- 🔒 **ANOMALY DETECTION** — unlocks when you build `car_state` (LAB B)
- 🔒 **AI PIT STRATEGIST** — unlocks when you build `pit_decisions` (LAB C)

The dashboard only reads Kafka; it never runs Flink SQL, so it can't interfere. Leave
it up while you work.

### Open the SQL shell

Back in your first terminal:

```bash
uv run f1-sql
```

```
Connected to RIVER-RACING-<prefix>-ENV / RIVER-RACING-<prefix>-CLUSTER
f1-sql>
```

End every statement with `;`. Ctrl-C stops a streaming query. `\q` quits.

---

## 3. LAB A — Explore what's already running

All in the `f1-sql` shell.

```sql
SHOW TABLES;
```

| Table | Source | Format |
|-------|--------|--------|
| `car_telemetry` | Simulator — car #88 sensors, every 2s | Avro |
| `race_standings` | Simulator — 22 cars, keyed by `car_number` (upsert) | Avro |
| `driver_race_history` | CDC from Postgres, 198 historical rows | JSON |

Watch live telemetry (Ctrl-C to stop):

```sql
SELECT car_number, lap, tire_temp_fl_c, tire_pressure_fl_psi, engine_temp_c
FROM car_telemetry;
```

And the standings — one row per car, continuously updated:

```sql
SELECT car_number, `position`, gap_to_leader_sec, tire_compound, tire_age_laps
FROM race_standings;
```

The simulator writes `race_standings` as **keyed** Avro, which makes it a versioned
(upsert) table — exactly what the temporal join in LAB B needs.

Confirm the CDC connector landed the history (a streaming count; it climbs to 198 and
settles — Ctrl-C then):

```sql
SELECT COUNT(*) FROM driver_race_history;
```

Confirm the Bedrock models Terraform pre-created:

```sql
SHOW MODELS;        -- llm_textgen_model
SHOW CONNECTIONS;   -- the Bedrock connection behind it
```

---

## 4. LAB B — Enrichment + anomaly detection → `car_state`

Paste this entire statement into the `f1-sql` shell. Leave it running.

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

Verify it in a second SQL shell:

```sql
SELECT car_number, lap, `position`, tire_compound, tire_age_laps,
       anomaly_tire_temp_fl, tire_temp_fl_c
FROM `car_state`;
```

`car_state` begins emitting after the first 20-second window closes. The race itself may take up to ~20 seconds to emit lap 1 (it aligns to the next 20-second wall-clock boundary before starting), so the first window can land one interval later than expected. The anomaly model needs 12 windows of context; at lap 24, the front-left tire reaches about 145°C and the flag becomes `true`.

### Optional: forecast tire temperature

Run this after `car_state` produces rows. Stop the query after inspecting the result so Lab C can use the compute pool.

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

---

## 5. LAB C — Streaming agent → `pit_decisions`

Run these two statements in order. The first creates the agent.

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

DECISION ALGORITHM — apply these rules in order. Do not deviate.

Step 1: If anomaly_tire_temp_fl = true → Suggestion: PIT NOW. Stop.
Step 2: Else if pit_stops > 0 → Suggestion: STAY OUT. Stop.
Step 3: Else if tire_compound = SOFT AND tire_age_laps >= 21 → Suggestion: PIT SOON. Stop.
Step 4: Else → Suggestion: STAY OUT. Stop.

These rules are absolute. The race context, gap, competitor pit timing, and tire
temperatures are inputs FOR YOUR REASONING TEXT ONLY — they MUST NOT change the
Suggestion field. Reason about strategy in the Reasoning field, but the Suggestion
itself is fully determined by Steps 1–4 above.

FORBIDDEN PATTERNS — these are bugs, not options:
- Outputting PIT NOW when anomaly_tire_temp_fl = false. No exceptions.
- Outputting PIT SOON when tire_age_laps < 21.
- Outputting PIT SOON after pit_stops > 0.
- Outputting anything other than STAY OUT when tire_age_laps < 20 AND anomaly_tire_temp_fl = false.
- Justifying PIT NOW with phrases like "approaching cliff", "blowout risk", "tires near limit",
  "performance falling off" — these are PIT SOON or STAY OUT signals, never PIT NOW.

SELF-CHECK before responding: re-read Steps 1–4 with the actual input values.
The input includes REQUIRED SUGGESTION, computed by Flink SQL from those rules.
Copy that exact value into Suggestion. If your prose conflicts with it, fix the
prose before outputting.

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
-- USING TOOLS `car_telemetry_tool`  -- uncomment when RTCE is active
WITH ('max_iterations' = '10');
```

Confirm it exists, then create the decision stream:

```sql
SHOW AGENTS;
```

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
    WHEN cs.pit_stops > 0 THEN 'STAY OUT'
    WHEN cs.tire_compound = 'SOFT' AND cs.tire_age_laps >= 21 THEN 'PIT SOON'
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
      WHEN cs.pit_stops > 0 THEN 'STAY OUT'
      WHEN cs.tire_compound = 'SOFT' AND cs.tire_age_laps >= 21 THEN 'PIT SOON'
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

`pit_decisions` reads `car_state` from the earliest offset, so it also processes laps that have already arrived.

### What to expect

| Lap | Position | Suggestion | What's happening |
|---|---|---|---|
| 1–20 | P3 → P1 | STAY OUT | Competitive, stable |
| 21–23 | P1 → P8 | PIT SOON | Aging SOFTs need a stop soon |
| **24** | **P8** | **PIT NOW** | **Front-left anomaly at about 145°C triggers the scheduled stop** |
| 25 | P14 | STAY OUT | Fresh MEDIUMs after the stop |
| 26–60 | P14 → P1–P2 | STAY OUT | Recovery on fresh MEDIUMs |

---

## 6. LAB D — Read the results

```sql
SELECT lap, `position`, suggestion, condition_summary, reasoning
FROM `pit_decisions`
WHERE suggestion <> 'STAY OUT';
```

Rows arrive roughly in lap order — the `PIT SOON` warnings as the tire degrades, then the
decisive `PIT NOW`.

> No `ORDER BY lap`: in a continuous Flink query, `ORDER BY` only works on a
> time-attribute column. Sorting an unbounded stream on a plain field errors with
> *"Sort on a non-time-attribute field is not supported."*

The moment itself:

```sql
SELECT lap, `position`, tire_compound_current, tire_age_laps,
       anomaly_tire_temp_fl, suggestion,
       recommended_tire_compound, recommended_stint_laps, reasoning
FROM `pit_decisions`
WHERE anomaly_tire_temp_fl = true;
```

And the pipeline you built:

```sql
SHOW TABLES;   -- car_state + pit_decisions alongside the sources
SHOW AGENTS;   -- pit_strategy_agent
```

You can see the same graph visually in the Confluent Cloud **Stream Lineage** view —
`terraform output -raw environment_url` in `terraform/aws` gives you the link.

---

## 7. Operating the demo

All of these run from the repo root.

**Watch the simulator logs**

```bash
aws logs tail --region us-east-1 "$(cd terraform/aws && terraform output -raw ecs_log_group)" --follow
```

The demo always deploys to **us-east-1**, so pass `--region` explicitly — if your AWS
CLI defaults elsewhere the command finds nothing and says the log group doesn't exist.
(`uv run race` and `uv run reset` already default to us-east-1, and `race status` prints
this exact `aws logs tail` line for you.)

**Pause / resume the race feed** — leaves your Flink jobs and all accumulated data alone:

```bash
uv run race status     # desired vs running task count, plus the log-tail command
uv run race stop       # scale the simulator to 0 and wait for it to drain
uv run race start      # scale it back to 1 (the race restarts from lap 0)
uv run race restart    # stop, then start
```

`race` is scoped to **this** deployment's single ECS service, resolved from
`runs/standalone/deployment.env` — not the instructor fan-out (`uv run workshop
stop-races` / `start-races`), which scales *every* `river-racing*` simulator in the AWS
account and has no place in a solo demo. Those four actions are the whole surface:
there's no `logs` action (status prints the command) and no pacing flag (see "Change the
race pacing" below).

Your Flink jobs keep running across the pause, and the simulator restarts at lap 0, so
`car_state` and `pit_decisions` just get a second pass over laps 1–60 — including a
second lap-24 anomaly. That's fine for a re-demo. Use the reset below when you want a
genuinely clean run.

**Start the demo over** — one command, nothing to sequence:

```bash
uv run reset --with-labs
```

It stops the simulator, drops `car_state` / `pit_decisions` / the agent along with their
topics and Schema Registry subjects, clears the race data out of `car_telemetry`,
rebuilds all three lab objects from `docs/demo-reference/`, and starts a fresh race from lap
0. When it prints `Environment is ready`, everything in this walkthrough exists and is
running.

If any step fails it prints `=== Reset INCOMPLETE ===`, lists what didn't happen, and
**exits non-zero** — a half-reset environment is never reported as a clean slate. Fix the
listed problem and re-run.

The lab objects have to be rebuilt because `reset` drops them — they're created by the
LAB B/C statements you ran, not by Terraform. Plain `uv run reset` leaves them dropped on
purpose: in the instructor-led workshop, building them *is* LAB 3 and LAB 4. It also
leaves the **race feed stopped** on purpose, and tells you to submit LAB 3 before
`uv run race start` — same ordering reason as below.

Reset serves both solo tracks. With a standalone deployment *and* a self-service one in
the same checkout, name the one you mean: `uv run reset --track standalone` (or
`--track selfservice`). With only one track's Terraform state present, it finds it
itself. On the self-service track there's no ECS service to scale, so instead reset
looks for a local `uv run f1-race` that is still producing and refuses rather than
clearing underneath it — `--force` overrides that.

> The rebuild happens *before* the race restarts, not after — and the order matters
> because of `race_standings`, not the telemetry. `car_telemetry` sets
> `scan.startup.mode=earliest-offset` at the table level, so LAB B replays it from the
> start either way. `race_standings` doesn't, so it starts from `latest`: any standings
> row produced before the LAB B statement is `RUNNING` is never seen, those laps have no
> version for the temporal join to match, and `car_state` silently loses its first laps.

Clearing `car_telemetry` matters more than it looks. The simulator loops races back to
back, so the topic accumulates finished races — and LAB B reads what's already there.
Re-run it against a full topic and `car_state` sprints through several old races in
under a minute, surfacing the lap-24 anomaly immediately instead of when the live race
reaches it. Pass `--keep-source` if you *want* that history retained.

`race_standings` is compacted, so Kafka won't let its records be deleted; `reset` says
so and moves on. It's harmless — the topic keeps only the latest row per car, lap 0 of
the next race overwrites all 22, and the temporal join resolves by event time, so a
finished race's rows can never be matched to newer telemetry.

Or drop them by hand in the shell — but then re-run the LAB B and LAB C `--file` commands
from §4 and §5, in that order, or `pit_decisions` won't validate:

```sql
DROP TABLE IF EXISTS `pit_decisions`;
DROP TABLE IF EXISTS `car_state`;
DROP AGENT IF EXISTS `pit_strategy_agent`;
```

**Change the race pacing**

```bash
export TF_VAR_seconds_per_lap=15
uv run deploy --automated
```

Both tiers are re-applied, but the shared tier no-ops and the only change is the ECS
task definition — the simulator restarts on the new pacing and the race begins again
from lap 0. (There is deliberately no `uv run race --seconds-per-lap`: pacing lives in
the ECS task definition, so changing it *is* a redeploy. Only the local self-service
simulator takes it as a flag.)

---

## 8. Optional — LAB 5 with the Real-Time Context Engine

The workshop's LAB 5 builds a no-code IBM watsonx Orchestrate agent that drafts social
posts from the live feed, reading it through an OpenAPI tool. You can reproduce the tool
side solo; the agent side still needs an Orchestrate account.

Two interchangeable backends serve the identical `/race-feed/{prefix}` surface,
so Orchestrate uses the same `docs/assets/orchestrate/f1-race-feed-openapi.json` file either way:

```bash
# A. Straight from Kafka (no extra Confluent features needed)
uv run f1-social-feed --creds runs/standalone/credentials/<prefix>.env   # → :8080

# B. Via the Real-Time Context Engine (MCP), which this shim re-exposes as REST
RTCE_API_KEY=... RTCE_API_SECRET=... uv run f1-social-feed-rtce --probe \
  --creds runs/standalone/credentials/<prefix>.env      # validate the contract first
RTCE_API_KEY=... RTCE_API_SECRET=... uv run f1-social-feed-rtce \
  --creds runs/standalone/credentials/<prefix>.env      # then serve it
```

`uv run deploy` already stamps `F1_RTCE_MCP_ENDPOINT` onto your card, so the RTCE
variant needs no URL from you. The API key/secret are **not** read from the card on
purpose: RTCE authenticates with a *global* Confluent Cloud key, so it's passed to the
process once via `RTCE_API_KEY` / `RTCE_API_SECRET` (or `--api-key` / `--api-secret`).
Run `--probe` first — it validates the live RTCE contract and exits, which is much
easier to debug than a failed tool import. RTCE has to be available on your org; if the
probe fails, use backend A, which needs nothing beyond the topics you already have.

Orchestrate has to reach the API over the internet, so expose port 8080 with a
tunnel (`ngrok`, Cloudflare Tunnel), then edit `docs/assets/orchestrate/f1-race-feed-openapi.json`
(ships with a placeholder `servers[0].url`) to point at that HTTPS URL, and
upload the JSON file. It can't consume RTCE's MCP endpoint directly — it supports only
*local* MCP servers, which is the whole reason this REST shim exists. Agent
configuration (persona, prompts, tool wiring):
[`docs/demo-reference/orchestrate_social_agent.md`](../demo-reference/orchestrate_social_agent.md)
and [Lab 5 in the hosted workshop walkthrough](./HOSTED-WORKSHOP.md#lab-5-social-media-agent-ibm-watsonx-orchestrate).

### Optional: Lightning Queries (low-latency REST)

Terraform enables RTCE on `car_telemetry` by default. Unless you deployed with `enable_rtce=false`, no Console toggle is needed for this query. Topics you create later, such as `car_state`, need their own RTCE enablement.

From the repo directory, print a ready-to-run query:

```bash
uv run setup-rtce --lightning
```

Copy the printed `curl` command into your terminal and run it. It returns the last 10 telemetry rows by lap; edit the SQL in `query` to filter for car 88 or select other columns. The command reads your existing credential file and derives the region and cloud from its RTCE endpoint. Use `--creds path/to/file.env` if you have multiple credential files.

Lightning Queries require a **Global API key**, the same key used by RTCE's MCP interface. The printed command contains its authentication token; keep it private. This command prints the request without registering an MCP client. It reads matching local Terraform outputs, or the existing credential file for hosted attendees. Both modes accept `RTCE_API_KEY` and `RTCE_API_SECRET` overrides. If no key is available, the script offers CLI creation, then hidden manual entry with a link to the creation instructions. It saves fallback keys in the existing credential file.

`uv run deploy` now creates the Global key through Terraform and copies its sensitive outputs into your existing credential file. Both `uv run setup-rtce` and `uv run setup-rtce --lightning` read that pair automatically. Existing deployments without the key can use `RTCE_API_KEY` and `RTCE_API_SECRET`, or enter a pair at the hidden-input fallback prompt. The prompt links to [manual key creation instructions](https://docs.confluent.io/cloud/current/ai/real-time-context-engine/get-started.html#create-an-api-key). Kafka and Flink keys cannot substitute for a Global key.

---

## 9. Tear down

```bash
uv run destroy
```

It lists only the deployments whose Terraform state is actually in this checkout, and
destroys nothing without a confirmation. Pick the **deploy** group: `terraform/aws` then
`terraform/aws-shared`, in that order, as one unit — the two are never created
independently and the `aws` destroy needs `aws-shared`'s outputs, so offering them
separately would just be a way to strand resources. If the `aws` teardown fails, the
shared tier is deliberately **not** attempted; fix the failure and re-run.

Two safety behaviours worth knowing:

- A `wsa` workshop is unreachable from here on purpose — wsa keeps state in its own run
  directory, so it never lands in this working tree. Tear one down with
  `uv run workshop clean`.
- Shared state with **no** matching `aws` state is anomalous (a `deploy` always applies
  both), and most likely an organizer's hand-applied workshop backing. `destroy` refuses
  it until you type `destroy-shared`, because removing it would pull Postgres and the
  simulator image out from under live attendees.

On success, each tier also cleans up what it left on disk: its credential card, the
`F1_CARD` pointer, `runs/<track>/deployment.env`. That's scoped per track, so a
self-service deployment in the same checkout keeps its own. Then revoke the Bedrock IAM
user:

```bash
uv run api-keys destroy
```

> **Redeploying later?** Postgres and the ECR image are the slow parts of `aws-shared`,
> and destroy removes them with everything else — the next `uv run deploy` is another
> ~25–30 minutes. If you kept the shared tier some other way, keep its name too:
> `export F1_SHARED_PREFIX=f1-<old-prefix>`, or the ECR repository is destroyed and
> recreated and the image rebuilt from scratch.

Everything in this demo costs money while it runs (Confluent cluster + Flink pool, EC2
Postgres, ECS Fargate, Bedrock calls per lap). Tear it down when you're finished.

---

## Troubleshooting

**`f1-sql` returns 401/403.** The card it picked belongs to a torn-down environment.
Check which one it chose — `uv run f1-sql` prints it under the `Connected` line — then
re-deploy, or point at the right card with `--creds <path>`.

**`No credential card found` / `Multiple credential cards found`.** The tools read
`F1_CARD` from `credentials.env`; `uv run deploy` sets it. Re-run the deploy, or pass
`--creds runs/standalone/credentials/<prefix>.env` for one command.

**`Table (or view) 'pit_decisions' does not exist`** (or the same for `car_state`).
Nothing created it yet. Both tables come from the LAB B/C statements — not Terraform — so
they're gone after `uv run reset`, which drops them by design. Rebuild everything with
`uv run reset --with-labs`, or re-run the `--file` commands from §4 and §5 in order.

**`car_state` is empty.** Almost always the `ML_DETECT_ANOMALIES` warmup — it needs 12
× 20-second windows (~4 min of live data). Note the race can also take up to ~20 seconds
to emit lap 1, since it aligns to a 20-second wall-clock boundary before starting. If it's still empty after 6 minutes, check
that telemetry is arriving (`SELECT * FROM car_telemetry;`) and that the table itself was
created (`DESCRIBE car_state;`). If the table is missing, the LAB B statement failed —
re-run the `--file` command and read the error it prints.

**`car_telemetry` looks idle.** The simulator pauses briefly between race loops. If
nothing arrives for several minutes, run `uv run race status` (is a task actually
running?), check the ECS logs (§7), or bounce it with `uv run race restart`.

**No anomaly around lap 24.** Confirm the spike exists at all:

```sql
SELECT lap, tire_temp_fl_c FROM `car_state` WHERE lap BETWEEN 20 AND 24;
```

You should see ~145°C. If Lab 3 started too late to collect 12 windows before lap
22, the detector did not have enough training context for the incident.

**Agent fields are null but `raw_response` has text.** The LLM emitted a slightly
different label format than the parsing regex expects. Inspect it:

```sql
SELECT lap, suggestion, raw_response FROM `pit_decisions` LIMIT 5;
```

Re-running usually resolves it. If `raw_response` is empty across the board, check the
Bedrock keys and that `bedrock:InvokeModel` is permitted in `us-east-1`.

**`SHOW MODELS;` returns nothing.** You're connected to the wrong environment. The shell
prints its card on startup — quit (`\q`) and relaunch with `--creds <the right card>`.

---

**← Back to Overview**: [Main README](../../README.md)
