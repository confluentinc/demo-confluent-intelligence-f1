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
| **Orchestrator** | `uv run workshop build` (wraps `wsa`) | `uv run deploy` | `uv run selfservice up` |
| **Start here** | [Workshop setup](#workshop-multi-attendee-instructor-led) | [Standalone setup](#standalone-demo-full-aws-shape-one-environment) | [Run it yourself](#run-it-yourself-start-here) |

In all modes, the canonical Flink SQL lives in [`demo-reference/`](demo-reference/)
and is reproduced step-by-step in the lab guides under
[labs/](labs/README.md). Attendees in the workshop format start at
**[labs/README.md](labs/README.md)**.

## Run it yourself (start here)

**Recommended: self-service.** One person, ~5 minutes, **Confluent Cloud only** —
environment, cluster, Flink pool, topics, and the Bedrock LLM models. No EC2
Postgres, no CDC connector, no ECR image, no ECS simulator, no Docker. You run the
race simulator locally, and `driver_race_history` is seeded with a Flink `INSERT`.
The only external credential is a pair of AWS Bedrock keys (`uv run api-keys create`
mints a least-privilege pair).

```bash
uv run selfservice up               # provision Confluent + credential card + seed data
uv run selfservice up --with-labs   # ...and prebuild LAB 3 + LAB 4, ready to demo
uv run f1-race                      # start the live feed (leave it running)
uv run f1-sql                       # run the labs
uv run f1-pitwall                   # live dashboard → http://localhost:8000
uv run selfservice down             # tear it all down
```

Needs `uv`, Terraform ≥ 1.3, and Bedrock keys. The Confluent CLI is **optional** —
Terraform authenticates with a Confluent Cloud API key, and the CLI is only used if
you ask `selfservice up` to mint that key for you.

Then work through [labs/](labs/README.md) LAB 1 → LAB 4 and LAB 6 (LAB 5 needs an IBM
watsonx Orchestrate account). Full walkthrough:
**[docs/SELF-SERVICE.md](docs/SELF-SERVICE.md)**.

### Standalone demo (full AWS shape, one environment)

Use this when you want exactly what attendees get, including the ECS simulator
service and Postgres CDC: one presenter, one environment, ~25–30 min, Docker and AWS
credentials required.

```bash
uv run deploy                # prompts → credentials.env → terraform/aws-shared → terraform/aws
uv run deploy --with-labs    # ...and build LAB 3 + LAB 4, ready to demo
uv run deploy --automated    # no prompts (reads credentials.env)
uv run race status           # this deployment's feed: desired vs running tasks
uv run destroy               # pick which local deployment(s) to tear down, then destroy
                             #   (workshop teardown is `workshop clean`, not this script)
```

End-to-end walkthrough (deploy → labs → reset → teardown):
**[docs/STANDALONE-DEMO.md](docs/STANDALONE-DEMO.md)**.

### Reset between runs

Both solo tracks share one reset:

```bash
uv run reset                 # drop the lab objects, clear car_telemetry, stop the feed
uv run reset --with-labs     # ...then rebuild LAB 3 + LAB 4 and restart the race
uv run reset --keep-source   # keep the accumulated race data (and leave the feed running)
```

`reset` stops the race feed before clearing — scaling this deployment's ECS service
to zero, or refusing if a local `uv run f1-race` is still producing (`--force`
overrides) — and leaves it stopped so LAB 3 is running before standings resume. Add
`--track standalone|selfservice` only if this checkout has both.

## Workshop (multi-attendee, instructor-led)

Provisioning is owned by **[`wsa`](https://github.com/confluentinc/workshop-setup-accelerator)**
(Confluent's shared workshop CLI). It applies the shared layer once, then the
per-attendee layer N times, injecting the shared outputs into each. Attendees never
run Terraform and **never log in to the Confluent Console** — they claim scoped API
keys (via the wsa dispenser or an instructor-distributed card) and run Flink SQL
through the bundled shell.

Organizer prerequisites are the standalone demo's, plus a `wsa` checkout: `uv`,
Terraform ≥ 1.3, Docker (the shared layer builds the simulator image and pushes it to
ECR), AWS credentials that can create EC2 — the shared Postgres is an EC2 instance, not
RDS — along with ECR and ECS, and the Confluent + Bedrock secrets exported as `TF_VAR_*`.
Unlike `uv run deploy`, this path leaves Postgres at the `t3.large` module default.
**Attendees need none of this** — only `uv` and their credential card.

`wsa` lives in a sibling checkout of `workshop-setup-accelerator`, but you drive it
from this repo — the wrapper finds the binary and passes `wsa-spec-aws.yaml` for you:

```bash
op run --env-file=.env.tpl -- uv run workshop spec-validate       # pre-flight the spec + tooling
op run --env-file=.env.tpl -- uv run workshop build --accounts 1-20 --concurrency 4
op run --env-file=.env.tpl -- uv run workshop clean               # newest run, resolved for you
```

`workshop build` provisions **and** writes every attendee's credential card from the
run's `build-output.csv`, so there is no run-id to copy. Set `account_count` in
[`wsa-spec-aws.yaml`](wsa-spec-aws.yaml) to your attendee count and export the shared
Confluent/Bedrock secrets as `TF_VAR_*` (see the spec's `env_vars:`). Each attendee
gets an isolated Confluent environment, a CDC connector with its own replication slot,
the LLM models, and an always-on race feed.

Note the two differently-scoped checks: `workshop spec-validate` is wsa's pre-flight
on the spec and your local tooling **before** a build; `workshop validate` probes
**provisioned** environments afterwards using each attendee's own API keys.

```bash
uv run workshop validate --creds-glob 'runs/*/credentials/*.env'   # fleet health check
uv run workshop stop-races      # scale every attendee simulator to 0
uv run workshop start-races     # ...and back to 1 (synchronized restart)
```

Attendees who claimed through the dispenser
(`<sibling>/bin/wsa dispenser-upload --sheets-credentials sheets-credentials.json`)
build their own card from the claim email:

```bash
uv run f1-onboard   # prompts field-by-field, or --paste to parse a pasted email
```

Either way, attendees run Flink SQL with their card, which the tools resolve on their
own:

```bash
uv run f1-sql                                               # card found automatically
uv run f1-sql --creds runs/<name>/credentials/f1wp001.env   # or name one explicitly
```

For flags the wrapper doesn't expose, raw `wsa` is still fully supported:
`<sibling>/bin/wsa build -w <this-repo>/wsa-spec-aws.yaml ...`.