---
name: f1-workshop-commands
description: Command reference for the F1 Pit Wall workshop — create/teardown a workshop, workshop subcommands, credential cards, race control (start/stop/reset), standalone deploy, self-service, f1-pitwall, f1-social-feed, setup-mcp, tests and lint. Load this when running or explaining any `uv run` command in this repo.
---

# F1 Pit Wall workshop — command reference

```bash
# Organizer: one-command workshop creation and teardown.
# Prompts for any missing secrets (or use `op run` / env vars to inject them).
uv run create-workshop --attendees 5 --email-pattern 'organizer+f1wp{N}@example.com'
                                                    # preflight + secrets + build + cards
uv run create-workshop --attendees 20 --concurrency 4  # larger workshop
uv run teardown-workshop                            # tears down the newest run
uv run workshop reset-races                         # stop feeds + reset all attendee envs
#   Lifecycle: create-workshop → attendees write LAB 3 → start-races → ... →
#   reset-races → attendees write LAB 3 → start-races → ... → teardown-workshop

# Organizer: full workshop (many attendees) — power-user subcommands.
# `workshop` wraps the `wsa` CLI (which still owns provisioning) and locates the
# sibling checkout itself, so these run from THIS repo with -w injected.
# Secrets: `build` and `clean` call `ensure_secrets` themselves (env → credentials.env
#   → prompt), so nothing needs injecting. There is NO `.env.tpl` in this repo; exported
#   values still win, so `op run --env-file=<your-own> --` works if you prefer a vault.
uv run workshop spec-validate    # wsa pre-flight: spec + local tooling
uv run workshop build --accounts 1-20 --concurrency 4 \
  --email-pattern 'organizer+f1wp{N}@example.com'
#   ONE command: applies terraform/aws-shared, then N × terraform/aws, THEN writes every
#   credential card from that run's build-output.csv — no run-id to copy by hand.
#   --no-cards to skip the card step, -n/--name for the card directory label.
uv run workshop clean            # newest non-cleaned run in wsa-output/
#   --run-id to target another run; --accounts-only / --shared-only;
#   --no-password-reset --no-dispenser-clear if this run never used the dispenser/Gmail reset.
#   Also uploads to the dispenser sheet when WSA_DISPENSER_SPREADSHEET_ID is set
#   in wsa.env (silent no-op otherwise); --no-dispenser-upload skips it.
# Raw wsa stays fully supported for flags the wrapper doesn't expose — but it does NOT
#   self-serve secrets, so export the TF_VAR_* set first:
set -a; . ./credentials.env; set +a
<sibling>/bin/wsa build -w <path-to-this-repo>/wsa-spec-aws.yaml ...

# Organizer: cards from an existing wsa run (`workshop build` already does this)
uv run workshop creds --csv <wsa-repo>/wsa-output/<run-id>/build-output.csv --name <name>
# TWO different "validate"s — never conflate them:
#   workshop spec-validate = wsa's pre-flight on the spec + local prerequisites, BEFORE a build
#   workshop validate      = API-key health checks against provisioned environments, AFTER one
uv run workshop validate --creds-glob 'runs/*/credentials/*.env'   # no AWS/login needed

# Attendee, self-serve (wsa dispenser claim email -> local credentials.env)
uv run f1-onboard                # prompts field-by-field, or --paste to parse a pasted email

# Flink SQL from a credential card, no Console login. NOT what LAB 1-6 teaches
# (that's the browser SQL workspace) — this is the standalone/self-service path.
# The card is resolved automatically — see "Credential card resolution" in the
# f1-credentials skill.
uv run f1-sql
uv run f1-sql --creds runs/<name>/credentials/f1wp001.env   # override

# Attendee: live race dashboard (consumes their own Kafka topics, no login)
uv run f1-pitwall                                           # → http://localhost:8000
uv run f1-pitwall --mock                                    # offline demo/dev, no Confluent env

# Organizer: shared race-feed service for LAB 5 (OpenAPI tool for watsonx Orchestrate)
uv run f1-social-feed --creds-glob 'runs/*/credentials/*.env'   # → :8080, serves /race-feed/{prefix}
uv run f1-social-feed --mock                                    # offline demo/dev, no Confluent env
# Same OpenAPI tool, but sourced from the Real-Time Context Engine (MCP) instead of Kafka:
RTCE_API_KEY=... RTCE_API_SECRET=... uv run f1-social-feed-rtce --creds-glob 'runs/*/credentials/*.env'
RTCE_API_KEY=... RTCE_API_SECRET=... uv run f1-social-feed-rtce --probe --creds <card>.env  # validate RTCE contract

# Standalone demo: single environment (smoke test / presenter) — shared then attendee
uv run deploy                  # prompts → credentials.env → terraform/aws-shared → terraform/aws
uv run deploy --automated      # same, no prompts (reads credentials.env)
uv run deploy --with-labs      # also build LAB 3 + LAB 4 from docs/demo-reference/ and restart the
                               #   race behind them — ready to demo. Omit for a bare environment
                               #   (what the workshop hands attendees).
                               # Prefix is derived from $USER (+ track suffix) and pinned in
                               #   runs/<track>/deployment.env — see "Deployment identity"
                               #   in the f1-credentials skill.
                               # Postgres defaults to t3.small here (aws-shared's own default,
                               #   which wsa uses, stays t3.large).
uv run destroy                 # pick which local deployment(s) to tear down, confirm, destroy
                               #   groups: "deploy" (aws + aws-shared) / "self-service"
                               #   A wsa workshop is unreachable (wsa keeps state in its own
                               #   run dir) — tear one down with `wsa clean`. Hand-applied
                               #   aws-shared state IS reachable, behind a typed confirmation.

# Self-service (solo): Confluent-only, NO AWS infra (no Postgres/CDC/ECS/ECR/Docker)
uv run selfservice up          # apply terraform/self-service → credential card → seed driver_race_history
uv run selfservice up --automated   # no prompts (reads credentials.env)
uv run selfservice up --with-labs   # also prebuild LAB 3 + LAB 4 from docs/demo-reference/
uv run selfservice down        # tear down terraform/self-service (--yes to skip the prompt)
uv run f1-race                 # local simulator (ECS stand-in); --once, --seconds-per-lap N, --20
                               #   Pacing: flag > runs/<track>/deployment.env > 20. Minimum 10s/lap.
                               #   Sets PRE_RACE_WARMUP_LAPS=0 (the ECS path keeps the default 4).

# Optional: register the Confluent MCP server with a local coding agent, from a card
uv run setup-mcp               # Claude Code, project-local scope (default)
uv run setup-mcp --client codex     # Codex CLI — user-global ~/.codex/config.toml
uv run setup-mcp --client both --dry-run   # write confluent-mcp.env (0600) + print, change nothing
                               #   Needs Node >= 20 (v24 LTS has the prebuilt native binaries).

# Control ALL attendee race feeds (organizer fan-out over every matching ECS service)
uv run workshop start-races    # scale every attendee simulator to 1
uv run workshop stop-races     # scale every attendee simulator to 0

# Control just THIS deployment's race feed (standalone track — one ECS service)
uv run race status             # desired vs running task count, plus the aws-logs-tail command
uv run race start / stop / restart   # scale and wait for the transition
                               #   Those four actions are the whole surface: no `logs` action
                               #   (status prints the command) and no pacing flag (pacing is
                               #   TF_VAR_seconds_per_lap + redeploy, or f1-race's own flag).

uv run reset                   # blank slate for a new race: drops lab objects (car_state,
                               #   pit_decisions, agent) AND truncates car_telemetry so LAB 3
                               #   doesn't replay finished races. race_standings is compacted,
                               #   so it can't be truncated (harmless — see scripts/reset.py).
                               #   Stops the feed FIRST (scales this deployment's ECS service to
                               #   0, or refuses when a local `f1-race` is producing — --force
                               #   overrides) and leaves it stopped, so LAB 3 can be submitted
                               #   before standings resume. Prints `=== Reset INCOMPLETE ===`
                               #   and exits nonzero if any step failed.
                               #   --keep-source skips the truncation AND leaves the feed
                               #   running (unless --with-labs needs it stopped).
                               #   --track standalone|selfservice — required only when both
                               #   tracks have Terraform state in this checkout.
uv run reset --with-labs       # same, then REBUILDS the lab objects from docs/demo-reference/
                               #   and restarts this deployment's race — one command to a
                               #   ready-to-demo environment. Standalone/solo demos only:
                               #   plain `reset` leaves the labs dropped because building
                               #   them is LAB 3/LAB 4. Scales only THIS deployment's ECS
                               #   service (not the instructor fan-out), and submits the labs
                               #   BEFORE restarting the race since race_standings reads `latest`.

uv run api-keys create         # Create AWS IAM user + keys for Bedrock access

# Read attendee Terraform outputs
cd terraform/aws && terraform output -json attendee_credentials

# Logs for one attendee simulator (`uv run race status` prints this line for you)
aws logs tail /ecs/<prefix>-<hex>-simulator --follow

# Tests / lint
uv run pytest                  # testpaths + the runtime extras are declared in pyproject.toml
uv run ruff check datagen/ scripts/ deploy.py
```
