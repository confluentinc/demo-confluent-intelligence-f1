# F1 Pit Wall AI — River Racing at Silverstone

A real-time AI pit-strategy system built on Confluent Cloud. Live car telemetry and
race standings stream into a Kafka cluster, where **Flink SQL** detects tire
anomalies and an **AI Streaming Agent** (AWS Bedrock / Claude) recommends when to pit.

**Team:** River Racing | **Driver:** John Doe (#88) | **Circuit:** Silverstone | **60 laps**

## Three ways to run it

This repo supports three formats from the same codebase. They differ in scale,
audience, and how much infrastructure they stand up.

| | **Workshop** (multi-attendee) | **Standalone demo** (single environment) | **Self-service** (solo) |
|--|--|--|--|
| **Audience** | Instructor-led, run at Confluent events | One presenter or smoke test | One person experiencing the labs |
| **Provisions** | Shared infra + N attendee environments | Shared infra + one environment | **Confluent Cloud only** — no AWS infra |
| **Simulator** | ECS Fargate service | ECS Fargate service | Local process (`uv run f1-race`) |
| **Historical data** | Postgres + CDC connector | Postgres + CDC connector | Seeded via a Flink `INSERT` |
| **Needs Docker / AWS infra** | Yes | Yes | **No** (Bedrock API keys only) |
| **Setup time** | ~25–30 min | ~25–30 min | **~5 min** |
| **Orchestrator** | `wsa` (reads `wsa-spec-aws.yaml`) | `uv run deploy` | `uv run selfservice up` |
| **Start here** | [Workshop setup](#workshop-multi-attendee) | [Standalone setup](#standalone-demo-single-environment) | [Self-service setup](#self-service-solo) |

In all modes, the canonical Flink SQL lives in [`demo-reference/`](demo-reference/)
and is reproduced step-by-step in the lab guides under
[labs/](labs/README.md). Attendees in the workshop format start at
**[labs/README.md](labs/README.md)**.

**Prerequisites (workshop & standalone):** `uv`, Terraform ≥ 1.3, Docker (builds the
simulator image once), the Confluent CLI logged in, AWS credentials configured, and
AWS Bedrock keys. **Self-service** needs only `uv`, Terraform ≥ 1.3, the Confluent
CLI logged in, and AWS Bedrock keys — no Docker and no AWS infrastructure.

## Workshop (multi-attendee)

Provisioning is owned by **[`wsa`](https://github.com/confluentinc/workshop-setup-accelerator)**
(Confluent's shared workshop CLI), not a repo-local orchestrator. It applies the
shared layer once, then the per-attendee layer N times, injecting the shared
outputs into each. Attendees never run Terraform and **never log in to the
Confluent Console** — they claim scoped API keys (via the wsa dispenser or an
instructor-distributed card) and run Flink SQL through the bundled shell.

Run `wsa` from a **sibling checkout** of `workshop-setup-accelerator` (see its
`ONBOARDING.md` "Local layout"), pointed at this repo's `wsa-spec-aws.yaml`:

```bash
op run --env-file=.env.tpl -- ./bin/wsa validate -w <this-repo>/wsa-spec-aws.yaml
op run --env-file=.env.tpl -- ./bin/wsa build -w <this-repo>/wsa-spec-aws.yaml --accounts 1-20
./bin/wsa dispenser-upload --sheets-credentials sheets-credentials.json   # self-serve claim (optional)
op run --env-file=.env.tpl -- ./bin/wsa clean -w <this-repo>/wsa-spec-aws.yaml
```

Set `account_count` in `wsa-spec-aws.yaml` to your attendee count and export the
shared Confluent/Bedrock secrets as `TF_VAR_*` (see the spec's `env_vars:`). Each
attendee gets an isolated Confluent environment, a CDC connector with its own
replication slot, the LLM models, and an always-on race feed.

Turn wsa's `build-output.csv` into the credential cards our tools expect, then
run fleet-wide health checks:

```bash
uv run workshop creds --csv <wsa-repo>/wsa-output/<run-id>/build-output.csv --name <name>
uv run workshop validate --creds-glob 'runs/*/credentials/*.env'
```

Attendees who claimed via the wsa dispenser instead self-serve their own card
from the claim email:

```bash
uv run f1-onboard   # prompts field-by-field, or --paste to parse a pasted email
```

Either way, attendees run Flink SQL with their credential card. `f1-onboard` writes it
to `./credentials.env`, which the tools pick up on their own:

```bash
uv run f1-sql                                               # card found automatically
uv run f1-sql --creds runs/<name>/credentials/f1wp001.env   # or name one explicitly
```

## Standalone demo (single environment)

Provisions the shared layer and a single environment for one presenter or a quick
smoke test. End-to-end walkthrough (deploy → labs → teardown):
**[docs/STANDALONE-DEMO.md](docs/STANDALONE-DEMO.md)**.

```bash
uv run deploy              # prompts → credentials.env → terraform/aws-shared → terraform/aws
uv run deploy --automated  # same, no prompts (reads credentials.env)
uv run destroy             # pick which local deployment(s) to tear down, then destroy
                           # (workshop teardown is `wsa clean`, not this script)
```

## Self-service (solo)

The fastest way for one person to experience the labs. Provisions **Confluent Cloud
only** — environment, cluster, Flink pool, topics, and the Bedrock LLM models — with
**no** EC2 Postgres, CDC connector, ECR image, or ECS simulator. You run the race
simulator locally and the historical `driver_race_history` table is seeded with a
Flink `INSERT`, so setup takes ~5 minutes and needs no Docker or AWS infrastructure
(only AWS Bedrock API keys, e.g. from `uv run api-keys create`).

```bash
uv run selfservice up      # provision Confluent + write a credential card + seed data
uv run f1-race             # start the live feed
uv run f1-sql              # run the labs
uv run f1-pitwall          # live dashboard
uv run selfservice down    # tear it all down
```

Then work through [labs/](labs/README.md) LAB 1 → LAB 4 and LAB 6. Full walkthrough:
**[docs/SELF-SERVICE.md](docs/SELF-SERVICE.md)**.

## Control the race feeds

The simulator runs as an always-on ECS service (`RACE_LOOP=true`), so feeds start
automatically. To pause or synchronously restart everyone:

```bash
uv run stop-all-races    # scale every simulator to 0
uv run start-all-races   # scale every simulator back to 1
uv run reset             # blank slate: drop lab objects + clear car_telemetry
                         #   (--keep-source to leave the race data in place)
uv run reset --with-labs # blank slate AND rebuild the labs + restart the race,
                         #   for demos where nobody is writing LAB 3/4 by hand
```

`stop-all-races` / `start-all-races` fan out over every attendee. `reset` is
single-environment (it reads `terraform/aws` state), and organizer-only — an attendee
machine has no Terraform state, so it exits before doing anything.