# Standalone Demo — End-to-End Walkthrough

One person, one Confluent Cloud environment, real AWS infrastructure, deployed with
`uv run deploy`. This is the whole path: provision, run the labs, tear down.

Everything here is copy-paste. Paths and IDs are pulled from Terraform outputs, so
there is nothing to look up or substitute.

**What you build:** live car telemetry + race standings stream into Kafka → Flink SQL
joins them and flags a front-left tire anomaly → an AI Streaming Agent (Bedrock/Claude)
calls the pit stop and explains why.

```
Race simulator (ECS Fargate, always on)
  ├── car_telemetry    (car #88, Avro)      ─┐
  └── race_standings   (22 cars, Avro, keyed)┤
                                             │
Shared Postgres ─ CDC ─ driver_race_history  │
                                             │
                          LAB B  10s window + temporal join
                                 ML_DETECT_ANOMALIES  →  car_state
                                             │
                          LAB C  CREATE AGENT + AI_RUN_AGENT
                                                      →  pit_decisions
```

The workshop's LAB 5 (IBM watsonx Orchestrate) is **not** part of this walkthrough —
it needs instructor-provided Orchestrate access and a publicly reachable feed URL.

---

## 0. Prerequisites

| Need | Check |
|------|-------|
| `uv` | `uv --version` |
| Terraform ≥ 1.3 | `terraform version` |
| Docker running | `docker info` (builds the simulator image once) |
| AWS CLI configured | `aws sts get-caller-identity` |
| Confluent CLI | `confluent version` |
| AWS Bedrock keys | see below |

Mint Bedrock keys if you don't have them (creates a scoped IAM user, `InvokeModel` only):

```bash
uv run api-keys create
```

The keys land in `API-KEYS-AWS.md` (gitignored) and are written straight into
`credentials.env`, so the deploy prompts will already have them filled in. Revoke after
the demo with `uv run api-keys destroy`.

You also need a Confluent Cloud API key pair with OrganizationAdmin. `uv run deploy`
can generate one for you — answer `y` at the first prompt.

---

## 1. Deploy

Every command in this doc runs from the repo root:

```bash
cd "$(git rev-parse --show-toplevel)"
uv run deploy
```

It prompts for the Confluent API key/secret, an owner email (AWS tagging), a prefix
(alphanumeric, ≤12 chars — e.g. your initials), the Bedrock keys, and **seconds per
lap**. Answers are saved to `credentials.env`, so a re-run is `uv run deploy --automated`.

On pacing: the workshop default is 60s/lap, which makes a 60-lap race take an hour and
puts the anomaly — the payoff of the whole demo — ~32 minutes in. This prompt defaults
to **20** instead (~20-minute race, anomaly at ~11 min). Don't go below 10: at that
pace `ML_DETECT_ANOMALIES` can't accumulate its 20 training windows before lap 32 and
the anomaly never fires.

Two Terraform applies run back to back:

1. **`terraform/aws-shared`** — VPC lookup, Postgres (seeded with 198 rows of historical
   race data), ECR repo + the simulator Docker image.
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
SHOW MODELS;        -- llm_textgen_model, llm_embedding_model
SHOW CONNECTIONS;   -- the Bedrock connections behind them
```

---

## 4. LAB B — Enrichment + anomaly detection → `car_state`

This joins telemetry to standings by event time, tumbles into 10-second windows, and
runs `ML_DETECT_ANOMALIES` on the front-left tire temperature.

The canonical SQL is [`demo-reference/enrichment_anomaly.sql`](../demo-reference/enrichment_anomaly.sql).
Submit it whole, without copy-pasting — from a **separate terminal** (not inside the
shell):

```bash
uv run f1-sql --exec "$(cat demo-reference/enrichment_anomaly.sql)"
```

Expected output: `RUNNING  (statement left running)` plus the statement name.

### Why it's built this way

- **Temporal join before the windows.** `FOR SYSTEM_TIME AS OF t.event_time` must run on
  the raw rowtime. After `TUMBLE`/`OVER`, `window_time` loses its rowtime attribute and
  the join silently returns **zero rows** — no error, just nothing.
- **Only `tire_temp_fl_c` goes through `ML_DETECT_ANOMALIES`.** The other sensors are
  noisier and produce false positives.
- **`actual_value > upper_bound`** keeps only the *overheating* spike, not the cold drop
  after the pit stop (a recovery, not a problem).

### Verify

Back in the interactive shell:

```sql
SELECT car_number, lap, `position`, tire_compound, tire_age_laps,
       anomaly_tire_temp_fl, tire_temp_fl_c
FROM `car_state`;
```

**Be patient here.** `ML_DETECT_ANOMALIES` withholds all output until it has 20 training
windows. Windows are 10 seconds of wall-clock event time, so that's **~3.5 minutes of
live data** before `car_state` emits its first row, regardless of lap pacing. Empty
result → wait and re-run.

Around **lap 32**, `anomaly_tire_temp_fl` flips to `true` and `tire_temp_fl_c` spikes to
~145°C. The **ANOMALY DETECTION** panel on your dashboard unlocks.

---

## 5. LAB C — Streaming agent → `pit_decisions`

Two statements. First create the agent (its full prompt lives in
[`demo-reference/streaming_agent_create_agent.sql`](../demo-reference/streaming_agent_create_agent.sql)):

```bash
uv run f1-sql --exec "$(cat demo-reference/streaming_agent_create_agent.sql)"
```

The prompt pins the agent to a strict decision algorithm:

```
Step 1: anomaly_tire_temp_fl = true         → PIT NOW
Step 2: else SOFT and tire_age_laps >= 26   → PIT SOON
Step 3: else                                → STAY OUT
```

…while the LLM writes the condition summary, race context, recommended compound/stint,
and reasoning in its own words. Confirm it exists:

```sql
SHOW AGENTS;
```

Then run the agent over `car_state`
([`demo-reference/streaming_agent_pit_decisions.sql`](../demo-reference/streaming_agent_pit_decisions.sql)):

```bash
uv run f1-sql --exec "$(cat demo-reference/streaming_agent_pit_decisions.sql)"
```

This formats each `car_state` row into a prompt, calls `AI_RUN_AGENT`, and parses the
labeled response into columns. It reads with `scan.startup.mode=earliest-offset`, so it
processes laps that already happened — you don't have to have started it first.

### What to expect

| Lap | Position | Suggestion | What's happening |
|-----|----------|-----------|------------------|
| 1–17 | P3 | STAY OUT | Competitive, stable |
| 18–25 | P3 → P1 | STAY OUT | Leaders pit, John briefly leads |
| 26–31 | P1 → P8 | PIT SOON | Tire cliff bites |
| **32** | **P8** | **PIT NOW** | **Front-left anomaly at 145°C** |
| 33 | P12 | STAY OUT | Fresh MEDIUMs |
| 34–60 | P12 → P2 | STAY OUT | Fastest car on track, climbs back |

P8 at the call → P2 at the flag. The **AI PIT STRATEGIST** panel unlocks and the banner
flips to a flashing red **PIT NOW** at lap 32.

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
(`start-all-races`, `stop-all-races`, and `reset` already default to us-east-1.)

**Pause / resume the race feed** — leaves your Flink jobs and all accumulated data alone:

```bash
uv run stop-all-races     # scale the simulator to 0
uv run start-all-races    # scale it back to 1 (restarts the race from lap 0)
```

These are the instructor fan-out commands — they scale *every* `river-racing*` simulator in
the AWS account. In a standalone deploy that's just yours. Your Flink jobs keep running
across the pause, and the simulator restarts at lap 0, so `car_state` and `pit_decisions`
just get a second pass over laps 1–60 — including a second lap-32 anomaly. That's fine for
a re-demo. Use the reset below when you want a genuinely clean run.

**Start the demo over** — one command, nothing to sequence:

```bash
uv run reset --with-labs
```

It stops the simulator, drops `car_state` / `pit_decisions` / the agent along with their
topics and Schema Registry subjects, clears the race data out of `car_telemetry`,
rebuilds all three lab objects from `demo-reference/`, and starts a fresh race from lap
0. When it prints `Environment is ready`, everything in this walkthrough exists and is
running.

The lab objects have to be rebuilt because `reset` drops them — they're created by the
LAB B/C statements you ran, not by Terraform. Plain `uv run reset` leaves them dropped on
purpose: in the instructor-led workshop, building them *is* LAB 3 and LAB 4.

> The rebuild happens *before* the race restarts, not after — and the order matters
> because of `race_standings`, not the telemetry. `car_telemetry` sets
> `scan.startup.mode=earliest-offset` at the table level, so LAB B replays it from the
> start either way. `race_standings` doesn't, so it starts from `latest`: any standings
> row produced before the LAB B statement is `RUNNING` is never seen, those laps have no
> version for the temporal join to match, and `car_state` silently loses its first laps.

Clearing `car_telemetry` matters more than it looks. The simulator loops races back to
back, so the topic accumulates finished races — and LAB B reads what's already there.
Re-run it against a full topic and `car_state` sprints through several old races in
under a minute, surfacing the lap-32 anomaly immediately instead of when the live race
reaches it. Pass `--keep-source` if you *want* that history retained.

`race_standings` is compacted, so Kafka won't let its records be deleted; `reset` says
so and moves on. It's harmless — the topic keeps only the latest row per car, lap 0 of
the next race overwrites all 22, and the temporal join resolves by event time, so a
finished race's rows can never be matched to newer telemetry.

Or drop them by hand in the shell — but then re-run the LAB B and LAB C `--exec` commands
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
from lap 0.

---

## 8. Tear down

```bash
uv run destroy
```

Pick the **deploy** group — it destroys `terraform/aws` then `terraform/aws-shared`.
Then revoke the Bedrock IAM user:

```bash
uv run api-keys destroy
```

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
`uv run reset --with-labs`, or re-run the `--exec` commands from §4 and §5 in order.

**`car_state` is empty.** Almost always the `ML_DETECT_ANOMALIES` warmup — it needs 20
× 10-second windows (~3.5 min of live data). If it's still empty after 5 minutes, check
that telemetry is arriving (`SELECT * FROM car_telemetry;`) and that the table itself was
created (`DESCRIBE car_state;`). If the table is missing, the LAB B statement failed —
re-run the `--exec` command and read the error it prints.

**`car_telemetry` looks idle.** The simulator pauses briefly between race loops. If
nothing arrives for several minutes, check the ECS logs (§7) or bounce it with
`uv run stop-all-races && uv run start-all-races`.

**No anomaly around lap 32.** Confirm the spike exists at all:

```sql
SELECT lap, tire_temp_fl_c FROM `car_state` WHERE lap BETWEEN 30 AND 34;
```

You should see ~145°C. If `car_state` has fewer than 20 windows of history before lap 32,
the detector never trained — that happens if you set `seconds_per_lap` very low. 20s/lap
gives it plenty of room.

**Agent fields are null but `raw_response` has text.** The LLM emitted a slightly
different label format than the parsing regex expects. Inspect it:

```sql
SELECT lap, suggestion, raw_response FROM `pit_decisions` LIMIT 5;
```

Re-running usually resolves it. If `raw_response` is empty across the board, check the
Bedrock keys and that `bedrock:InvokeModel` is permitted in `us-east-1`.

**`SHOW MODELS;` returns nothing.** You're connected to the wrong environment. The shell
prints its card on startup — quit (`\q`) and relaunch with `--creds <the right card>`.
