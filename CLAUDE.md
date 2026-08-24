# F1 Pit Wall AI Workshop

Multi-attendee, instructor-led workshop. Each attendee gets an isolated Confluent
Cloud environment with a live race feed; they use Flink SQL to detect tire
anomalies (`ML_DETECT_ANOMALIES`) and run a Flink Streaming Agent (Bedrock/Claude)
that recommends pit stops, then (LAB 5) build a no-code IBM watsonx Orchestrate
agent that drafts social posts from the same live feed. Organizers provision
everything from a single Confluent org + AWS account with **`wsa`**
(confluentinc/workshop-setup-accelerator, run from a sibling checkout, driven by
this repo's `wsa-spec-aws.yaml`); attendees never run Terraform — they claim an
account through the wsa dispenser (or get an instructor-distributed card), **log
in to the Confluent Cloud Console** with the username/password on that card, and
write every lab statement in the browser **Flink SQL workspace**. `uv run f1-sql`
is no longer taught in LAB 1-6; it stays for the standalone/self-service tracks.
See "Attendee Console access" and "WSA (organizer provisioning)" below.

**Team:** River Racing | **Driver:** John Doe (#88) | **Circuit:** Silverstone | **60 laps**

---

## Commands

Every `uv run` command in this repo — organizer provisioning, credential cards,
race control, reset, standalone deploy, self-service, pitwall, social feed,
setup-mcp, tests and lint — is in the **`f1-workshop-commands`** skill
(`.claude/skills/f1-workshop-commands/SKILL.md`). Load it before running or
explaining any of them. The checked-in references are `README.md` (the attendee
walkthrough), `docs/organizer/RUN-OF-SHOW.md` (presenter cues),
`docs/organizer/WORKSHOP-GUIDE.md` (organizer lifecycle), and the
`[project.scripts]` table in `pyproject.toml` (every entry point).

---

## Architecture

```
Race Simulator (ECS Fargate service, one per attendee, RACE_LOOP=true)
  ├── car_telemetry   (car #88, AVRO)            → Kafka (direct)
  └── race_standings  (22 cars, AVRO, keyed)     → Kafka (direct, upsert)
                                                         │
Shared Postgres → CDC Debezium (per-attendee slot) → driver_race_history
                                                         │
                              LAB 3 — Flink SQL (attendee-written)
                              10s tumbling window + temporal join
                              ML_DETECT_ANOMALIES(tire_temp_fl_c) → car_state
                                                         │
                              LAB 4 — Flink SQL (attendee-written)
                              CREATE AGENT + AI_RUN_AGENT → pit_decisions
                                                         │
                              LAB 5 — IBM watsonx Orchestrate (no-code)
                              OpenAPI tool → f1-social-feed → drafted social posts
```

There is **no IBM MQ and no Job 0** — the simulator produces `race_standings`
straight to Kafka as keyed Avro, so the topic is already a clean upsert/versioned
table. There is **no Tableflow/Databricks/dbt**. The only external SaaS is **IBM
watsonx Orchestrate** in LAB 5, reached read-only via the shared `f1-social-feed`
HTTP service (an OpenAPI tool) — everything upstream is Confluent-only.

---

## Terraform Layout

Three tiers: `aws-shared` (applied once), `aws` (per attendee), and the
Confluent-only `self-service`. The tier relationships, per-attendee resource naming,
CDC slot isolation, and the `flink_max_cfu` / `seconds_per_lap` / `race_loop` knobs are
in the **`f1-terraform-layout`** skill
(`.claude/skills/f1-terraform-layout/SKILL.md`). Load it before editing anything under
`terraform/`.

**The one contract that reaches outside `terraform/`:** every `shared_*` variable name
in `terraform/aws` must exactly match an output name in
`terraform/aws-shared/outputs.tf` (`shared_X` ← output `X`), since `wsa` injects them by
that naming convention as `TF_VAR_shared_X`. Breaking it silently starves the attendee
tier of its shared inputs, and it also governs `wsa-spec-aws.yaml` and
`scripts/workshop/wsa.py`.

---

## Kafka Topics

| Topic | Notes |
|-------|-------|
| `car_telemetry` | AVRO, no PRIMARY KEY, string message key. **RTCE-enabled** |
| `race_standings` | AVRO, PRIMARY KEY(car_number), upsert — produced directly by the simulator. Not RTCE-enabled: this org/region rejects compacted-topic queries with `MT_UPSERT_NOT_SUPPORTED`. |
| `driver_race_history` | 198 historical rows, from the per-attendee CDC connector |
| `car_state` | LAB 3 output, one record per 10s window. RTCE is the attendee's optional Console toggle (LAB 3 Step 4) |
| `pit_decisions` | LAB 4 output — agent decisions |

The first three are created by Terraform (Flink CREATE TABLE, except
`driver_race_history` which the CDC connector creates); `car_state` and
`pit_decisions` do not exist until the attendee writes LAB 3 / LAB 4. Topic
schemas (CREATE TABLE SQL): `terraform/modules/topics/main.tf`.

**Real-Time Context Engine (attendee-facing).** `confluent_rtce_topic` in
`modules/topics` enables RTCE on `car_telemetry` at build time, so an attendee's
MCP client can query the sensor stream with no Kafka client and no consumer group.
`race_standings` is intentionally excluded: although enablement reaches `online`,
every query against the compacted topic fails with `MT_UPSERT_NOT_SUPPORTED`, even
with raw VARCHAR and BYTES keys. Four things that are easy to get wrong:

- **Enablement is per topic and needs a registered schema** — hence the
  `depends_on` the CREATE TABLE statements. `car_state` can't be in Terraform at
  all: it doesn't exist until LAB 3, so it's an attendee Console toggle.
- **`description` is required and is model-readable.** The agent reads it to pick a
  topic. Treat it as prompt text.
- **Querying needs a *Global* API key** (HTTP Basic) — a Cloud or Kafka key is
  refused. The Terraform provider can't create Global keys, so
  `workshop creds --rtce-keys` mints one per attendee via the CLI, which requires
  the `confluent` CLI logged in as **OrganizationAdmin**.
- **Mint against the attendee's service account, never their user account.** Global
  keys cap at 2 per principal. The SA is recreated per build and destroyed at
  teardown so the cap resets for free; the `bheintz+f1wpN` pool users are permanent,
  so user-owned keys would accumulate until a build fails. `_mint_rtce_key` deletes
  the SA's existing Global keys before creating, because a secret can't be re-read —
  so regenerating cards invalidates RTCE on any already handed out.

`TF_VAR_enable_rtce=false` skips the resource for an org or region without RTCE
(`confluent rtce region list` — 11 AWS regions as of 2026-08).

---

## Flink Jobs (the labs)

Jobs 1 & 2 are **not** pre-deployed — attendees write them in LAB 3 / LAB 4. The
canonical SQL is in `docs/demo-reference/` and reproduced in the attendee `README.md`.

| Job | SQL file | Input → Output |
|-----|----------|----------------|
| 1 | `docs/demo-reference/enrichment_anomaly.sql` | `car_telemetry` + `race_standings` → `car_state` |
| 1 (opt-in) | `docs/demo-reference/enrichment_anomaly_ai.sql` | same, Granite `AI_DETECT_ANOMALIES` instead of ARIMA |
| 2a | `docs/demo-reference/streaming_agent_create_agent.sql` | creates `pit_strategy_agent` |
| 2b | `docs/demo-reference/streaming_agent_pit_decisions.sql` | `car_state` → `pit_decisions` |

**Job 1 has two implementations, and only one of them works.** The default is the GA
`ML_DETECT_ANOMALIES` (ARIMA), which flags lap 22 and only lap 22. The
foundation-model `AI_DETECT_ANOMALIES` variant (`'model' VALUE 'ttm'`; `'flowstate'`,
`'patchtstfm'`, and Google's `'timesfm-2.5'` are one-word swaps) is kept as an opt-in
— `F1_ANOMALY_FN=ai` on any `--with-labs` path, or submit `enrichment_anomaly_ai.sql`
directly — but on the build measured 2026-07-31 it **runs without error and never
flags anything**: `is_anomaly`, `upper_bound`, and `lower_bound` all stay NULL, so the
`CASE` can never be true and `car_state` carries `anomaly_tire_temp_fl = false` for the
whole race. It forecasts fine (`actual_value`/`forecast_value`/`rmse` populate). Do not
make it the default again without re-running the probe in
`docs/technical-discoveries.md` item 13b. Both emit the identical `car_state` schema, so
LAB 4/5, the pit wall, and the social feed cannot tell them apart. **Their config keys
differ:** `minTrainingSize`/`maxTrainingSize` vs `minContextSize`/`maxContextSize`, and
`enableStl` exists only on `ML_` — see `docs/technical-discoveries.md` item 13.

`llm_textgen_model` / `llm_embedding_model` are pre-deployed per environment by
`terraform/aws`.

**LAB 5 is not Flink** — it's a no-code IBM watsonx Orchestrate agent that reads
the live feed via an OpenAPI tool served by `scripts/social_feed/` (`f1-social-feed`).
Canonical agent config: `docs/demo-reference/orchestrate_social_agent.md`. Lab order is
now LAB 1–4 (Flink/SQL) → LAB 5 (Orchestrate) → LAB 6 (wrap-up).

The OpenAPI tool has **two interchangeable backends** behind the identical
`/race-feed/{prefix}` + `/openapi.json` surface: `f1-social-feed` (tails Kafka) and
`f1-social-feed-rtce` (MCP client to the Real-Time Context Engine). Orchestrate
imports the same spec either way — only which service you host changes. Orchestrate
can't consume RTCE's MCP endpoint directly (it supports only *local* MCP servers),
which is why the shim re-exposes RTCE as REST/OpenAPI.

---

## Critical Gotchas

**Direct `race_standings` production (the new failure mode):** the simulator now
owns producing schema-valid Avro to `race_standings` with the correct **key**.
`datagen/simulator.py` resolves the registered `race_standings-key` schema and
encodes the key as a primitive int or a record automatically. After the first
deploy, verify the `race_standings-key`/`-value` subjects look right — a wrong key
encoding makes the LAB 3 temporal join silently return zero rows.

**Temporal join ordering:** `FOR SYSTEM_TIME AS OF event_time` must be in an early
CTE on the raw stream. After OVER()/TUMBLE aggregations, `window_time` loses its
rowtime attribute and the join silently returns zero rows.

**No PRIMARY KEY on `car_telemetry`:** only `race_standings` is keyed. The
telemetry producer writes a string message key; do not add a PK that would
register an Avro int key schema.

**Anomaly cadence and warmup:** canonical LAB 3 SQL uses a 30-second TUMBLE to
match the workshop's 30-second laps. Preserve that equality: `car_state` feeds
`AI_RUN_AGENT` directly, so a shorter window causes multiple paid agent calls per
lap. Both anomaly variants emit rows immediately with a false/null-backed flag,
then gain full context after 12 windows (about lap 13; `minTrainingSize`/
`minContextSize` are both 12). The anomaly is injected at lap 22, comfortably past
that warmup. The simulator's lap-0 warmup laps do **not** prime that context because
they carry telemetry but no `race_standings`; LAB 3's inner temporal join drops them
before TUMBLE/OVER. Their only purpose is a producer/schema smoke test. A
non-30-second `SECONDS_PER_LAP` override no longer preserves one-window-per-lap
semantics and must be accompanied by a matching SQL-window change.
`f1-race` therefore sets `PRE_RACE_WARMUP_LAPS=0` (`scripts/selfservice/race.py`,
overridable via the env var); the ECS path keeps the default 4.

**SR hard-delete after DROP TABLE:** dropping a Flink table leaves `<topic>-key`
and `<topic>-value` subjects. `scripts/reset.py` deletes them with `--permanent`.

**`json` format unsupported:** use `json-registry` in Flink CREATE TABLE.

**Deploy jobs before/independent of race start:** default scan startup is
`latest`. `pit_decisions` uses `scan.startup.mode=earliest-offset` so it processes
laps already in `car_state`.

**Per-table `scan.startup.mode` overrides — check the table, not just the .sql:**
`car_telemetry` sets `'scan.startup.mode' = 'earliest-offset'` in its CREATE TABLE
(`terraform/modules/topics/main.tf`), so LAB 3 replays it from the start even with no
inline hint. `race_standings` does **not** — it starts from `latest`. That asymmetry is
why LAB 3 must be RUNNING before the simulator starts producing: standings rows written
beforehand are never seen, those laps have no version for the temporal join, and
`car_state` silently loses its first laps. Reading only the `docs/demo-reference/*.sql` files
will mislead you here; check the CREATE TABLE options too.

Full technical discoveries: `docs/technical-discoveries.md`.

---

## Secrets & Credentials

| File | Purpose | Created by |
|------|---------|------------|
| `credentials.env` | Deploy secrets (`TF_VAR_*`) + the `F1_CARD` pointer | `deploy.py` |
| `runs/<track>/deployment.env` | Per-track deployment inputs: resolved prefix, pacing, region, card path | `deploy.py`, `selfservice up` |
| `runs/<name>/credentials/<prefix>.env` | Credential card (`F1_*`) — what the attendee tools authenticate with | `workshop creds`, `deploy.py`, `selfservice up` |
| `runs/<name>/credentials/<prefix>.md` | The printed handout: Console URL/username/password first, then env IDs | `workshop creds` |
| `confluent-mcp.env` | MCP server env (mode `0600`), rewritten whole each run | `setup-mcp` |

Credential cards are **gitignored — do not commit.**

Console logins (invited pool users, 1Password passwords, the
`grant_console_access` RBAC gate), the derived per-track deployment prefixes in
`scripts/common/deployment_meta.py`, and `resolve_card()`'s precedence order are in the
**`f1-credentials`** skill (`.claude/skills/f1-credentials/SKILL.md`). Load it before
touching credential cards, `workshop creds`, `deployment.env`, or `f1-onboard`.

---

## WSA (organizer provisioning)

Provisioning and teardown are owned by `wsa`
(confluentinc/workshop-setup-accelerator) in a **sibling checkout**, wrapped by
`uv run workshop spec-validate|build|clean`. Binary discovery, the
`wsa-spec-aws.yaml` contract, `account_count` vs `--attendees`, the dispenser
upload, and the teardown gotcha where a missing Google OAuth client leaves
attendee passwords live are all in the **`wsa-provisioning`** skill
(`.claude/skills/wsa-provisioning/SKILL.md`). The checked-in references are
`docs/organizer/WORKSHOP-GUIDE.md`, `wsa-spec-aws.yaml` itself, and
`scripts/workshop/wsa.py`.

---

## File Sync Rule

`docs/demo-reference/*.sql`, `docs/demo-reference/orchestrate_social_agent.md`, and the
corresponding examples in the attendee `README.md` must stay in sync. The organizer
run-of-show links to the walkthrough and must not duplicate attendee SQL.

The split lab files under `docs/deprecated/` are historical references, not part
of the sync set. Do not restore `labs/instructor-led/` or copy SQL into organizer
docs. The default ARIMA `ML_DETECT_ANOMALIES` and optional 20-step Granite
`AI_FORECAST` examples each have a checked-in source under `docs/demo-reference/`
and an attendee copy in the `README.md`. The experimental Granite
`AI_DETECT_ANOMALIES` query is maintainer-only and must not be added to the
attendee walkthrough.

---

## Key File Locations

| File | Purpose |
|------|---------|
| `deploy.py` | Standalone two-tier deploy (shared + one attendee); `--with-labs`, `F1_SHARED_PREFIX` |
| `scripts/reset.py` | Clear a deployment's lab objects + truncate source topics; stops the feed first, `--track`, `--with-labs` |
| `scripts/race_control.py` | `uv run race status\|start\|stop\|restart` — scoped to THIS deployment's one ECS service |
| `scripts/setup_mcp.py` | `uv run setup-mcp` — register `@confluentinc/mcp-confluent` with Claude Code (project-local) or Codex (user-global) from a credential card |
| `scripts/common/deployment_meta.py` | Track definitions, derived prefixes, `runs/<track>/deployment.env`, pacing validation, `retire_track` |
| `scripts/common/simulator_control.py` | Shared `--with-labs` machinery: submits the `docs/demo-reference/` SQL and waits for RUNNING/COMPLETED; owns the `F1_ANOMALY_FN` ARIMA/Granite switch (`anomaly_sql_filename`) |
| `scripts/workshop/create.py` | `create-workshop` — one-command workshop provisioning: preflight, secrets, validate, build, cards, next-steps |
| `scripts/workshop/teardown.py` | `teardown-workshop` — one-command teardown: secrets, confirm, clean, card cleanup |
| `scripts/workshop/reset.py` | `workshop reset-races` — fleet-level reset: stop all feeds, fan out per-card reset, leave feeds stopped |
| `scripts/workshop/secrets.py` | Shared secret collection for organizer commands (env → credentials.env → interactive prompt) |
| `scripts/workshop/wsa.py` | `workshop spec-validate\|build\|clean` — wsa binary discovery, `-w` injection, run-id resolution, in-process card writing |
| `scripts/pitwall/` | `f1-pitwall` live web dashboard — Kafka consumer → FastAPI/websocket → animated browser view; progressive reveal of LAB 3/4 panels; `--mock` offline feed |
| `scripts/social_feed/` | `f1-social-feed` shared HTTP service for LAB 5 — tails each attendee's Kafka topics, serves `GET /race-feed/{prefix}` + auto OpenAPI spec for the watsonx Orchestrate tool; reuses pitwall consumer; `--mock` offline feed |
| `scripts/social_feed_rtce/` | `f1-social-feed-rtce` — same OpenAPI tool, but an MCP client to the Real-Time Context Engine (RTCE) instead of Kafka. Reuses `social_feed`'s `FeedState`+`create_app`; new bits are the RTCE MCP client + poller. Global API key via `RTCE_API_KEY/SECRET`; per-attendee endpoint from card `F1_RTCE_MCP_ENDPOINT`; `--probe` validates the live contract |
| `scripts/workshop/creds.py` | `workshop creds` — wsa's build-output.csv → `runs/<name>/credentials/*.env,.md`; `--resolve-op` pulls Console passwords from 1Password; `--rtce-keys` mints each attendee's RTCE Global API key (`_mint_rtce_key`, replace-not-accumulate) and prints the `claude mcp add` line. Also appends `Real-Time Context Engine / MCP Setup Command` back into build-output.csv so dispenser claim emails carry it (`_add_dispenser_column`, `--no-dispenser-column`) — the `" / "` in that header is what makes the dispenser's Apps Script email it |
| `terraform/modules/environment/main.tf` | The environment, plus the `grant_console_access`-gated `confluent_user` lookup + EnvironmentAdmin binding that makes an attendee login useful |
| `scripts/workshop/onboard.py` | `f1-onboard` — self-serve: wsa claim-email values → local `credentials.env` |
| `scripts/workshop/validate.py` | `workshop validate` — API-key health checks against one or many cards |
| `docs/demo-reference/enrichment_anomaly_ai.sql` | LAB 3's Granite/`AI_DETECT_ANOMALIES` variant — `F1_ANOMALY_FN=ai`. EAP-gated, and currently never flags an anomaly (docs/technical-discoveries.md 13b) |
| `docs/demo-reference/orchestrate_social_agent.md` | Canonical LAB 5 Orchestrate agent config (persona, tool, prompts) |

---

## Git

Standalone repo with its own `.git`. Remote:
`confluentinc/demo-confluent-intelligence-f1`. Use `git push-external` to push
(Confluent airlock policy for org repos).
