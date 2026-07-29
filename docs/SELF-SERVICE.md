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
`llm_embedding_model`, and the same canonical SQL in [`demo-reference/`](../demo-reference/).

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) and Terraform ≥ 1.3
- The **Confluent CLI** installed, with rights to create an environment (or a
  Confluent Cloud API key/secret with those rights). You do not need to log in
  first — the scripts prompt for your Confluent Cloud email/password once, save
  them to the gitignored `credentials.env`, and re-authenticate automatically on
  later runs. SSO accounts must log in manually with `confluent login --no-browser`.
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

You'll be prompted for your Confluent API key/secret, owner email, a short prefix
(default `solo`), and your Bedrock keys; answers are saved to `credentials.env` for
reuse. `--automated` skips the prompts and reads `credentials.env`.

This applies `terraform/self-service` (environment, cluster, Flink pool, the two live
topics, the Bedrock connections + LLM models, and an empty `driver_race_history`
table), writes a credential card to `runs/selfservice/credentials/<prefix>.env`, and
seeds the 198 historical rows into `driver_race_history` with a Flink `INSERT`.

The card's path is recorded as `F1_CARD` in `credentials.env`, so the commands below
find it on their own. Pass `--creds <path>` to override.

## 2. Start the live race feed

Leave this running in its own terminal — it's the local stand-in for the ECS
simulator:

```bash
uv run f1-race
```

By default it loops races back-to-back at 20 seconds per lap (~20-minute race). Tune
it:

```bash
uv run f1-race --seconds-per-lap 60   # 60-min race
uv run f1-race --once                 # single race, no loop
```

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

Then work through the guides under [`labs/`](../labs/README.md):

- **LAB 1–2** — explore the environment and source data. (Where the guides say "the
  simulator is already running", that's your `uv run f1-race` terminal.)
- **LAB 3** — enrichment + `ML_DETECT_ANOMALIES` → `car_state`. The anomaly fires
  around lap 32; the dashboard's **Anomaly Detection** panel unlocks.
- **LAB 4** — `CREATE AGENT` + `AI_RUN_AGENT` → `pit_decisions`. The **AI Pit
  Strategist** panel unlocks and calls `PIT NOW` at the anomaly.
- **LAB 6** — wrap-up queries over `pit_decisions`.

To re-run the stream-processing labs from scratch, `uv run reset` clears the lab
objects (`car_state`, `pit_decisions`, `pit_strategy_agent`) and empties
`car_telemetry`, so LAB 3 sees only the race you run next rather than replaying
finished ones. Stop `f1-race` first, reset, then start it again. `--keep-source`
leaves the accumulated race data in place.

## LAB 5 (optional) — watsonx Orchestrate social agent

LAB 5 is a no-code IBM watsonx Orchestrate agent that drafts social posts from the
live feed. It's optional for self-service because it needs an external IBM watsonx
Orchestrate account. To try it:

1. Run the race-feed HTTP service locally against your card:

   ```bash
   uv run f1-social-feed --creds runs/selfservice/credentials/solo.env   # → http://localhost:8080
   ```

2. In watsonx Orchestrate's Agent Builder, import the OpenAPI spec as a tool and set
   the tool's `prefix` to your prefix (`solo`). Because Orchestrate needs to reach the
   spec over the internet, expose `http://localhost:8080/openapi.json` with a tunnel
   (e.g. `ngrok`, Cloudflare Tunnel) and import that public URL.
3. Build the agent using [`demo-reference/orchestrate_social_agent.md`](../demo-reference/orchestrate_social_agent.md)
   and the [LAB 5 guide](../labs/instructor-led/LAB5_orchestrate_integration/LAB5.md).

## Tear down

```bash
uv run selfservice down
```

This destroys the Confluent environment (and with it the cluster, Schema Registry,
topics, and subjects). Bedrock keys and `credentials.env` are left untouched.
