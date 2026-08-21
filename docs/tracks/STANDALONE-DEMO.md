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

The workshop's LAB 5 (IBM watsonx Orchestrate) is optional here — the agent itself needs
an Orchestrate account, but you can stand up and test the feed it reads from. See §8.

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

On pacing: the default is **30s/lap**, which makes a 60-lap race take 30 minutes and
puts the anomaly — the payoff of the whole demo — ~11 minutes in (lap 22). This must
match the fixed 30-second `TUMBLE` window in the LAB 3 SQL (one window per lap), so
changing the pace requires a matching SQL-window change. Below 10s/lap
`ML_DETECT_ANOMALIES` can't accumulate its 12 training windows before lap 22 and the
anomaly never fires.

**Want the pipeline built for you** instead of typing LAB B and LAB C yourself?

```bash
uv run deploy --with-labs
```

Same deploy, plus the three lab objects from `demo-reference/` submitted for you and the
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
SHOW MODELS;        -- llm_textgen_model, llm_embedding_model
SHOW CONNECTIONS;   -- the Bedrock connections behind them
```

---

## 4. LAB B — Enrichment + anomaly detection → `car_state`

This joins telemetry to standings by event time, tumbles into 30-second windows, and
runs `ML_DETECT_ANOMALIES` on the front-left tire temperature.

> **Deployed with `--with-labs`?** This statement is already running — skip the submit
> below and go straight to **Verify**. `SHOW TABLES;` will list `car_state` already.

The canonical SQL is [`demo-reference/enrichment_anomaly.sql`](../../demo-reference/enrichment_anomaly.sql).
Submit it whole, without copy-pasting — from a **separate terminal** (not inside the
shell):

```bash
uv run f1-sql --file demo-reference/enrichment_anomaly.sql
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

`car_state` begins emitting after the first 30-second window closes. During the
first 12 windows, `anomaly_tire_temp_fl` remains `false` while the model builds
its training context. At the default pace, that context is ready around lap 13.

Around **lap 22**, `anomaly_tire_temp_fl` flips to `true` and `tire_temp_fl_c` spikes to
~145°C. The **ANOMALY DETECTION** panel on your dashboard unlocks.

---

## 5. LAB C — Streaming agent → `pit_decisions`

> **Deployed with `--with-labs`?** Both statements below are already applied —
> `SHOW AGENTS;` lists `pit_strategy_agent` and `pit_decisions` exists. Read on for what
> they do, then jump to **What to expect**.

Two statements. First create the agent (its full prompt lives in
[`demo-reference/streaming_agent_create_agent.sql`](../../demo-reference/streaming_agent_create_agent.sql)):

```bash
uv run f1-sql --file demo-reference/streaming_agent_create_agent.sql
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
([`demo-reference/streaming_agent_pit_decisions.sql`](../../demo-reference/streaming_agent_pit_decisions.sql)):

```bash
uv run f1-sql --file demo-reference/streaming_agent_pit_decisions.sql
```

This formats each `car_state` row into a prompt, calls `AI_RUN_AGENT`, and parses the
labeled response into columns. It reads with `scan.startup.mode=earliest-offset`, so it
processes laps that already happened — you don't have to have started it first.

### What to expect

| Lap | Position | Suggestion | What's happening |
|-----|----------|-----------|------------------|
| 1–15 | P3 | STAY OUT | Competitive, stable |
| 16–19 | P3 → P1 | STAY OUT | Leaders pit, John briefly leads |
| 20–21 | P1 → P8 | PIT SOON | Tire cliff bites |
| **22** | **P8** | **PIT NOW** | **Front-left anomaly at 145°C** |
| 24 | P12 | STAY OUT | Fresh MEDIUMs |
| 25–60 | P12 → P2 | STAY OUT | Fastest car on track, climbs back |

P8 at the call → P2 at the flag. The **AI PIT STRATEGIST** panel unlocks and the banner
flips to a flashing red **PIT NOW** at lap 22.

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
second lap-22 anomaly. That's fine for a re-demo. Use the reset below when you want a
genuinely clean run.

**Start the demo over** — one command, nothing to sequence:

```bash
uv run reset --with-labs
```

It stops the simulator, drops `car_state` / `pit_decisions` / the agent along with their
topics and Schema Registry subjects, clears the race data out of `car_telemetry`,
rebuilds all three lab objects from `demo-reference/`, and starts a fresh race from lap
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
under a minute, surfacing the lap-22 anomaly immediately instead of when the live race
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

**Query the environment from your coding agent (MCP)**

```bash
uv run setup-mcp                      # Claude Code, this project only
uv run setup-mcp --client codex       # Codex CLI (user-global ~/.codex/config.toml)
uv run setup-mcp --dry-run            # write confluent-mcp.env, print the commands, change nothing
```

This registers Confluent's `@confluentinc/mcp-confluent` server using **your credential
card**, so the agent gets the same scoped keys the labs use rather than an org-wide one.
Restart Claude Code or Codex after setup so it loads the new server registration.
It writes `confluent-mcp.env` (mode `0600`, gitignored) at the repo root and installs the
MCP package locally. Needs **Node ≥ 20** — v24 LTS is the version with prebuilt native
binaries. Re-running only replaces this script's own server entry.

---

## 8. Optional — LAB 5 with the Real-Time Context Engine

The workshop's LAB 5 builds a no-code IBM watsonx Orchestrate agent that drafts social
posts from the live feed, reading it through an OpenAPI tool. You can reproduce the tool
side solo; the agent side still needs an Orchestrate account.

Two interchangeable backends serve the identical `/race-feed/{prefix}` surface,
so Orchestrate uses the same root `f1-race-feed-openapi.json` file either way:

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
tunnel (`ngrok`, Cloudflare Tunnel), set that HTTPS URL in `servers[0].url` in
`f1-race-feed-openapi.json`, and upload the JSON file. It can't consume RTCE's MCP endpoint directly — it supports only
*local* MCP servers, which is the whole reason this REST shim exists. Agent
configuration (persona, prompts, tool wiring):
[`demo-reference/orchestrate_social_agent.md`](../../demo-reference/orchestrate_social_agent.md)
and [Lab 5 in the walkthrough](../../README.md#lab-5-social-media-agent-ibm-watsonx-orchestrate).

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
× 30-second windows (~6 min of live data). If it's still empty after 8 minutes, check
that telemetry is arriving (`SELECT * FROM car_telemetry;`) and that the table itself was
created (`DESCRIBE car_state;`). If the table is missing, the LAB B statement failed —
re-run the `--file` command and read the error it prints.

**`car_telemetry` looks idle.** The simulator pauses briefly between race loops. If
nothing arrives for several minutes, run `uv run race status` (is a task actually
running?), check the ECS logs (§7), or bounce it with `uv run race restart`.

**No anomaly around lap 22.** Confirm the spike exists at all:

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
