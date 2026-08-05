# Self-Service (Solo) Mode

Experience the whole F1 Pit Wall workshop as one person, with the smallest possible
footprint. Self-service provisions **Confluent Cloud only** and runs the race
simulator locally — there is **no** EC2 Postgres, CDC connector, ECR image, or ECS
Fargate service, and **no Docker**. Setup takes about five minutes.

**Team:** River Racing · **Driver:** John Doe (#88) · **Circuit:** Silverstone · 60 laps

## How it differs from the AWS paths

| | AWS paths (`workshop` / `deploy`) | Self-service (`selfservice`) |
|--|--|--|
| Race simulator | ECS Fargate service | Local process — `uv run f1-race` |
| `driver_race_history` | Postgres + CDC connector | Bounded Flink `INSERT` (198 rows) |
| Infrastructure | Confluent + EC2 + ECS + ECR + VPC | **Confluent Cloud only** |
| Docker required | Yes | **No** |
| Terraform tier | `terraform/aws-shared` + `terraform/aws` | `terraform/self-service` |

Everything the labs touch is identical: the same topics (`car_telemetry`,
`race_standings`, `driver_race_history`), the same `llm_textgen_model` /
`llm_embedding_model`, and the same canonical SQL in [`demo-reference/`](../../demo-reference/).

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) and Terraform ≥ 1.3
- A **Confluent Cloud API key/secret** with rights to create an environment.
  Terraform's Confluent provider authenticates with that key alone, so the
  **Confluent CLI is optional**: it's needed only if you answer `y` to
  `selfservice up`'s *"Generate new Confluent Cloud API keys?"* prompt, which mints
  a key for you. That branch asks for your Confluent Cloud email/password once,
  saves them to the gitignored `credentials.env`, and re-authenticates on later
  runs; SSO accounts must log in manually with `confluent login --no-browser`.
  Answer `n` (the default) and paste a key you already have, and you never log in.
- **AWS Bedrock credentials** (an access key/secret with `bedrock:InvokeModel`).
  These are credentials only — no AWS infrastructure is created. Mint a
  least-privilege key with:

  ```bash
  uv run api-keys create
  ```

## 1. Provision

```bash
uv run selfservice up
```

You'll be prompted for your Confluent API key/secret, owner email, an environment
prefix, and your Bedrock keys; answers are saved to `credentials.env` for reuse.
`--automated` skips the prompts and reads `credentials.env`.

**The prefix is suggested, not fixed.** It's derived from your `$USER` — or, on a
shared/generic login, a short hash of the owner email — plus this track's `s`
suffix, so `kevin` is offered `kevins`. Being deterministic, every later
`f1-race` / `reset` / `selfservice down` resolves the same names, and two people in
one Confluent org don't collide. Accept it or type your own (alphanumeric, max 12
characters); a value that contradicts an already-deployed environment is refused
rather than orphaning those resources. The resolved value lands in
`runs/selfservice/deployment.env` — *not* `credentials.env` — so a self-service and
a standalone deployment can coexist in one checkout without overwriting each
other's Terraform inputs. Renaming a live deployment is not supported: tear it down
first.

This applies `terraform/self-service` (environment, cluster, Flink pool, the two live
topics, the Bedrock connections + LLM models, and an empty `driver_race_history`
table), writes a credential card to `runs/selfservice/credentials/<prefix>.env`, and
seeds the 198 historical rows into `driver_race_history` with a Flink `INSERT`. The
seed counts the table before inserting, so re-running is safe. On a cold Flink pool
that first count can time out (~45 s) — `selfservice up` then exits **non-zero** with
the environment already provisioned; just run it again and the seed completes.

Add `--with-labs` to have LAB 3 and LAB 4 built for you from
[`demo-reference/`](../../demo-reference/), for a ready-to-demo environment. Omit it
(the default) to write them yourself — which is the point of the labs.

The card's path is recorded as `F1_CARD` in `credentials.env`, so the commands below
find it on their own. Pass `--creds <path>` to override.

## 2. Start the live race feed

Leave this running in its own terminal — it's the local stand-in for the ECS
simulator:

```bash
uv run f1-race
```

It loops races back-to-back at the pacing recorded for this deployment
(`runs/selfservice/deployment.env`; 20 seconds per lap unless you changed it, so a
~20-minute race) and prints which source that pacing came from. Tune it:

```bash
uv run f1-race --seconds-per-lap 60   # 60-min race
uv run f1-race --60                   # same thing, shorthand
uv run f1-race --once                 # single race, no loop
```

Anything below 10 s/lap is refused: the simulator produces `SECONDS_PER_LAP // 2`
telemetry readings per lap, so a very fast lap starves `ML_DETECT_ANOMALIES` of the
20 windows it needs before the lap-32 anomaly.

## 3. Run the labs

```bash
uv run f1-sql       # Flink SQL shell
uv run f1-pitwall   # live dashboard → http://localhost:8000
```

Confirm the environment is live:

```sql
SHOW TABLES;                          -- car_telemetry, race_standings, driver_race_history
SELECT * FROM race_standings;         -- 22 cars, updating while f1-race runs
SELECT COUNT(*) FROM driver_race_history;   -- 198
```

Then work through the guides under [`labs/`](../../labs/README.md):

- **LAB 1–2** — explore the environment and source data. Two things read
  differently on this track: where the guides say "the simulator is already
  running", that's your `uv run f1-race` terminal; and there is **no
  `f1-postgres-cdc` connector** to look at, because `driver_race_history` was seeded
  by a Flink `INSERT` rather than change data capture. That also makes it an **Avro**
  (`avro-registry`) table here, where the AWS paths' CDC connector produces **JSON**.
  Same 198 rows, same columns, same queries.
- **LAB 3** — enrichment + `ML_DETECT_ANOMALIES` → `car_state`. The anomaly fires
  around lap 32; the dashboard's **Anomaly Detection** panel unlocks.
- **LAB 4** — `CREATE AGENT` + `AI_RUN_AGENT` → `pit_decisions`. The **AI Pit
  Strategist** panel unlocks and calls `PIT NOW` at the anomaly.
- **LAB 6** — wrap-up queries over `pit_decisions`.

To re-run the stream-processing labs from scratch:

```bash
uv run reset                # drop the lab objects, clear the source data
uv run reset --with-labs    # ...and rebuild LAB 3 + LAB 4 from demo-reference/
```

`reset` drops the lab objects (`car_state`, `pit_decisions`, `pit_strategy_agent`)
and empties `car_telemetry`, so LAB 3 sees only the race you run next instead of
replaying finished ones. (`race_standings` is compacted, so Kafka refuses to delete
its records; reset reports that and moves on — compaction already leaves just the
latest row per car.)

Stop your `uv run f1-race` terminal first: reset looks for a still-producing local
simulator and refuses rather than clearing underneath it, with `--force` to override.
It then leaves the feed **stopped on purpose** — submit LAB 3 *before* restarting
`f1-race`, because `race_standings` reads from `latest` and any rows produced before
the job is running have no version for the temporal join. If any step fails, reset
prints `=== Reset INCOMPLETE ===` and exits non-zero instead of implying a clean
slate.

`--keep-source` keeps the accumulated race data — and leaves the feed running, since
nothing destructive touches the source topics (unless you also pass `--with-labs`,
which needs it stopped). `--track selfservice` is needed only if this checkout also has
a `uv run deploy` standalone deployment; with one track's Terraform state, reset finds
it on its own.

## Optional — talk to the cluster from your coding agent (MCP)

```bash
uv run setup-mcp                      # Claude Code, this project only
uv run setup-mcp --client codex       # Codex CLI (user-global ~/.codex/config.toml)
uv run setup-mcp --client both
uv run setup-mcp --dry-run            # write confluent-mcp.env, print the commands, change nothing
```

This registers Confluent's `@confluentinc/mcp-confluent` server against **your
credential card**, so the agent gets the same scoped Kafka/Flink/Schema-Registry
access as the labs. Restart Claude Code or Codex after setup so it loads the new
server registration.
keys the labs use — no Console login and no org-wide key. It writes
`confluent-mcp.env` (mode `0600`, gitignored) in the repo root and installs the MCP
package locally. Needs **Node ≥ 20** (v24 LTS is what has prebuilt native binaries;
other versions may need a source build). Re-running is safe — only this script's own
server entry is replaced.

## LAB 5 (optional) — watsonx Orchestrate social agent

LAB 5 is a no-code IBM watsonx Orchestrate agent that drafts social posts from the
live feed. It's optional for self-service because it needs an external IBM watsonx
Orchestrate account. To try it:

1. Run the race-feed HTTP service locally against your card:

   ```bash
   uv run f1-social-feed --creds runs/selfservice/credentials/<prefix>.env   # → http://localhost:8080
   ```

   (`<prefix>` is the prefix you provisioned with — the same one in
   `RIVER-RACING-<prefix>-ENV`. `--creds-glob 'runs/*/credentials/*.env'` also works
   if you'd rather not name it.)

2. In watsonx Orchestrate's Agent Builder, import the OpenAPI spec as a tool and set
   the tool's `prefix` to your prefix. Because Orchestrate needs to reach the spec
   over the internet, expose `http://localhost:8080/openapi.json` with a tunnel
   (e.g. `ngrok`, Cloudflare Tunnel) and import that public URL.
3. Build the agent using [`demo-reference/orchestrate_social_agent.md`](../../demo-reference/orchestrate_social_agent.md)
   and the [LAB 5 guide](../../labs/instructor-led/LAB5_orchestrate_integration/LAB5.md).

## Tear down

```bash
uv run selfservice down          # --yes to skip the confirmation prompt
```

This destroys the Confluent environment (and with it the cluster, Schema Registry,
topics, and subjects). On success it also clears up what the deployment left on disk:
the now-dead credential card, the `F1_CARD` pointer, the seed marker, and
`runs/selfservice/deployment.env`. That matters on a machine with both tracks — leaving
a dead card behind makes every attendee tool exit with "Multiple credential cards
found". After a *failed* destroy none of it is removed, because the card is then the
only record of resources that are still live.

Bedrock keys and `credentials.env` are left untouched. Revoke the Bedrock IAM user with
`uv run api-keys destroy` when you're done with it.
