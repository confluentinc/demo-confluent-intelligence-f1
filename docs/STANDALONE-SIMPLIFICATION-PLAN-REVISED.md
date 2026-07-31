# Standalone and Self-Service Simplification Plan, Revised

Date: July 30, 2026  
Current branch: `workshop-transform`  
Comparison baseline: `origin/initial-codebase`

## Goal

Make the standalone demo and self-service solo tracks easy to deploy, control, reset, and destroy. WSA workshop behavior must stay the same.

Keep the direct-Kafka architecture, CDC and ECS in standalone, the Confluent-only self-service path, credential cards, Pit Wall, the SQL shell, and the RTCE-backed LAB 5 service. Leave IBM MQ, dbt, Tableflow, and Genie retired.

The first plan covered the main safety failures, naming collisions, automated deployment, MCP restoration, and the missing walkthrough. The comparison plan found more day-to-day breakage around stale cards, seed markers, hidden errors, ignored tests, and incorrect self-service documentation. Those findings belong here.

## Decisions

### Standalone remains the full architecture

`uv run deploy` keeps Postgres, CDC, ECR, and ECS. The CDC connector is visible in Stream Lineage, and the ECS race continues when the presenter's laptop closes.

`uv run selfservice up` remains the fast, Confluent-only route for a first run.

### WSA has a behavior boundary

Shared Python and Terraform files may gain optional inputs or reusable helpers. WSA inputs, outputs, names, defaults, commands, and runtime behavior must remain unchanged.

Most work can avoid WSA Terraform entirely. When a solo fix needs a shared file, preserve the workshop default and add a test or diff check that proves it.

### Solo names get persistent three-character suffixes

Every new standalone and self-service deployment gets a lowercase alphanumeric suffix such as `a7k`. The suffix is generated once, saved with that deployment, and reused by apply, reset, race control, cards, and destroy.

A base prefix of `prod` resolves to `proda7k`. Keeping the resolved prefix alphanumeric avoids changing Postgres slot rules and the existing Terraform validation.

### Race control uses one scoped command

The primary interface will be:

```bash
uv run race start
uv run race stop
uv run race restart
uv run race status
```

For compatibility with the old branch, `uv run start-race` and `uv run stop-race` may remain as thin aliases. Both must target the ECS service named in the local `terraform/aws` state.

The workshop commands keep their current account-wide behavior:

```bash
uv run start-all-races
uv run stop-all-races
```

## What the comparison added

The second plan contributed several findings that were absent or less specific in the first:

- `wsa-output/` is 1.4 GB and has no ignore rule.
- Successful teardown clears `F1_CARD` but leaves the dead card file, which can make every card-consuming command stop on an ambiguous choice.
- `uv run destroy` removes a self-service environment without removing `runs/selfservice/.seeded`.
- Automated pacing accepts invalid values and does not consistently save valid overrides.
- Plain reset should leave the standalone feed stopped; restarting before LAB 3 exists loses early standings versions.
- Pit Wall hides authentication and broker errors at debug level forever.
- `f1-sql` lacks command history and a `--file` input.
- The self-service guide sends users through CDC and JSON instructions even though that track uses a Flink insert and Avro.
- The canonical LAB 3 SQL still claims dbt deploys it.
- The 60-lap race still has a `34–57` reference.
- The README puts the WSA path before the simpler solo routes.
- A live check is needed to settle the AWS-path startup behavior of `driver_race_history`.

All of those are included below.

## P0: prevent damage and false success

### 1. Stop destroy at the dependency boundary

`uv run destroy` currently continues to `terraform/aws-shared` after `terraform/aws` fails.

Change the standalone group to fail closed:

1. Destroy `terraform/aws`.
2. If it fails, preserve both states and return nonzero.
3. Destroy `terraform/aws-shared` only after the attendee layer succeeds.
4. Clean local artifacts only after the corresponding destroy succeeds.

Self-service remains independent and may continue only when the user selected it as a separate group.

Acceptance check: a mocked `terraform/aws` failure must prove that no shared destroy command starts.

### 2. Restore scoped race control

Extract the existing single-service ECS logic from `scripts/reset.py` into a neutral helper such as `scripts/common/simulator_control.py`.

Add `scripts/race_control.py` and the `race` entry point. It must:

- require local `terraform/aws/terraform.tfstate`;
- read `ecs_cluster_name` and `ecs_service_name` from that state;
- update exactly that service;
- wait for stop and restart transitions;
- show desired and running task counts in `status`;
- return nonzero on AWS errors or timeouts.

Do not import or call the instructor fan-out helper.

### 3. Make reset synchronous and track-aware

`uv run reset` must support standalone and self-service while refusing workshop credential cards.

For standalone:

- If source data will be cleared, stop the scoped ECS service and wait for zero running tasks.
- Stop Flink statements.
- Submit each `DROP TABLE` and `DROP AGENT`, then wait for a terminal success phase.
- Delete lab topics and Schema Registry subjects only after the drops finish.
- Clear source data and report failures as failures.
- Plain reset leaves the race stopped. The next instruction is `uv run race start`.
- `--with-labs` rebuilds LAB 3/4 objects before restarting the scoped race.
- `--keep-source` skips source truncation and skips the ECS stop.

Leaving the feed stopped matters. `race_standings` starts at the latest offset, so restarting before the user creates LAB 3 loses the first standings versions needed by the temporal join.

For self-service:

- Resolve `terraform/self-service` and the active self-service card.
- Skip ECS operations.
- Require the user to stop `uv run f1-race` before source truncation; refuse when a repo-owned local race process can be identified.
- Rebuild labs with `--with-labs`, then tell the user to restart `uv run f1-race`.

Every timeout, permission failure, failed drop, failed topic deletion, or failed truncation must produce a nonzero exit status. The command must never print `Reset complete` after partial cleanup.

## P1: remove collisions and stale local state

### 4. Generate and persist solo deployment names

Store track-specific metadata under:

```text
runs/standalone/deployment.env
runs/selfservice/deployment.env
```

Each file records at least:

```text
F1_BASE_PREFIX=prod
F1_DEPLOYMENT_SUFFIX=a7k
F1_RESOLVED_PREFIX=proda7k
F1_CARD=runs/<track>/credentials/proda7k.env
```

Rules:

- Generate the suffix only when the track has no state and no saved metadata.
- Reuse it on every rerun.
- Reject a user prefix longer than nine characters.
- Validate automated inputs before any cloud call.
- Never switch the suffix underneath existing Terraform state.

Standalone should derive its shared AWS prefix from the resolved prefix, for example `f1-proda7k`. That removes the account-global `f1-workshop-simulator` ECR collision.

Warn before migrating existing standalone state: changing the fixed shared prefix can replace the ECR repository and rebuild the image.

### 5. Separate credentials by track

Stop using one root `TF_VAR_prefix` and one mutable set of Terraform inputs for both solo deployments.

Keep root `credentials.env` for shared secrets and the active-card pointer. Save track-specific identity, pacing, and other non-secret deployment settings in each run directory. If destroy needs a secret that cannot be recovered from state or a card, save a track-scoped reference or prompt for it rather than borrowing the other track's value.

Automated apply and destroy must verify that the saved resolved prefix matches the existing state before proceeding.

### 6. Remove dead cards and seed markers after teardown

After a successful track destroy:

- remove that track's `.env` and `.md` credential cards;
- clear `F1_CARD` only when it points into that track;
- remove the self-service `.seeded` marker;
- preserve deployment metadata only if it helps diagnose or retry a partial destroy.

Do none of this after a failed destroy.

Add the two-card scenario to credential tests: when standalone and self-service cards exist, destroying one leaves the remaining live card resolvable without an explicit flag.

### 7. Ignore WSA build output

Add:

```gitignore
wsa-output/
```

The current directory is 1.4 GB, untracked, and not ignored. This change prevents an accidental `git add -A` from staging copied source, Terraform state, provider data, and generated credentials.

Leave the current uncommitted `wsa-spec-aws.yaml` edit untouched.

## P1: restore the ready-to-demo route

### 8. Restore full automated deployment

Use these command contracts:

```bash
uv run deploy
# Interactive provisioning. LAB 3 and LAB 4 remain hands-on.

uv run deploy --with-labs
# Interactive provisioning, LAB 3/4 creation, then a fresh scoped race.

uv run deploy --automated
# Saved inputs, no prompts, implies --with-labs.
```

`--with-labs` creates these objects in dependency order:

```text
car_state
pit_strategy_agent
pit_decisions
```

Wait until each object is usable. Then clear old source data and restart only the local standalone service at lap 0.

Self-service gets matching options:

```bash
uv run selfservice up
uv run selfservice up --with-labs
uv run selfservice up --automated
```

For self-service, automated mode finishes with 198 verified history rows and all LAB 3/4 objects. It prints the exact `uv run f1-race` command rather than starting a hidden background process.

### 9. Validate and save pacing

Interactive and automated paths must accept only integer pacing values of at least 10 seconds per lap.

Persist a valid override in the track's deployment metadata. Invalid automated input should return a short error before Terraform, Docker, AWS, or Confluent checks run.

`uv run f1-race` should use the saved self-service pacing as its default, still allowing:

```bash
uv run f1-race --seconds-per-lap 60
uv run f1-race --once
uv run f1-race --20
```

The `--20` spelling restores the old shorthand. It remains an alias for `--seconds-per-lap 20`.

A later convenience command may change standalone pacing with a targeted Terraform apply:

```bash
uv run race pacing 15
```

That command is optional for the first implementation wave. If added, it must target only the ECS task definition and service.

### 10. Reduce standalone Postgres size

Make `uv run deploy` pass:

```text
TF_VAR_postgres_instance_type=t3.small
```

The workshop keeps `t3.large`. `t3.small` gives a single Dockerized Postgres more headroom than the initial branch's `t3.micro` without paying for workshop capacity.

Document the override and allow an environment setting to replace it.

## P1: fix simulator and seed behavior

### 11. Remove ineffective solo warm-up

The simulator currently sends telemetry-only warm-up rows. LAB 3 joins telemetry to standings before anomaly detection, so those rows never train `ML_DETECT_ANOMALIES`.

Set the warm-up count to zero for standalone and self-service. Keep the WSA default unchanged through an optional variable or environment setting.

The default local race should reach lap 1 without the current 140-second delay. At the 10-second minimum, real data still produces the required 20 windows before lap 32.

### 12. Verify self-service seeding

For the bounded history insert:

1. Wait for `COMPLETED`, not `RUNNING`.
2. Treat timeout and every other terminal phase as failure.
3. Query or otherwise verify 198 rows.
4. Write a marker tied to the current environment ID only after verification.

Destroy through either `uv run selfservice down` or `uv run destroy` must remove that marker after the environment is gone.

## P2: repair the solo user experience

### 13. Stop forcing Confluent user login

Standalone and self-service should use supplied Cloud API credentials for Terraform.

Require a Confluent CLI user session only when the user chooses to generate a new Cloud API key. Do not save a second plaintext copy of the user's password in `credentials.env`; rely on the CLI's own saved-login mechanism when the user opts into it.

Update prerequisites so users with an existing OrganizationAdmin API key do not think a CLI user login is mandatory.

### 14. Surface Pit Wall connection failures

Keep `UNKNOWN_TOPIC` quiet until LAB 3/4 creates those topics. Warn once per distinct error code for authentication failures, unreachable brokers, authorization problems, and deserialization errors that make the dashboard unusable.

The dashboard should expose its last connection error in the health endpoint or page state instead of loading an empty screen indefinitely.

### 15. Improve `f1-sql`

Add command history and line editing through the platform's standard readline support.

Add:

```bash
uv run f1-sql --file demo-reference/enrichment_anomaly.sql
```

The file path should reuse the same statement classification, comment handling, wait logic, and error reporting as `--exec`.

Credential error messages must name all valid recovery paths:

```text
uv run deploy
uv run selfservice up
uv run f1-onboard
uv run workshop creds
```

The workshop path remains correct while solo users get useful instructions.

### 16. Restore MCP and RTCE setup

Keep `f1-social-feed-rtce` and add:

```bash
uv run setup-mcp
uv run setup-mcp --creds <card>

uv run setup-rtce
uv run setup-rtce --creds <card>
```

`setup-mcp` comes from the old branch but must read a selected credential card instead of deleted `terraform/core` state. It should replace only its own local registration and remain safe to rerun.

`setup-rtce` is a new command. It should derive the endpoint from the card, obtain the required Global API credentials through the repo's credential flow, probe the endpoint, and save only LAB 5 configuration. Check the current Confluent API and CLI contract before implementation.

Keep the `.gitignore` entries needed by the restored MCP setup. Prune only entries that remain unused after these commands return.

## P2: correct documentation and entry points

### 17. Restore the top-level walkthrough

Create one source of truth. `Walkthrough.md` should be the canonical guide or a symlink to the expanded `docs/STANDALONE-DEMO.md`.

It must cover:

- `uv run deploy`, `--with-labs`, and `--automated`;
- scoped race status, stop, start, restart, logs, and pacing;
- manual and prebuilt LAB 3/4 flows;
- `uv run setup-mcp` and `uv run setup-rtce`;
- LAB 5 through `f1-social-feed-rtce`;
- reset behavior for both solo tracks;
- safe teardown and migration warnings.

Do not restore the old MQ, dbt, Tableflow, or Genie instructions.

### 18. Fix self-service lab statements

Update `docs/SELF-SERVICE.md` and add track-specific notes to shared lab guides:

- self-service history comes from a bounded Flink insert, not Postgres CDC;
- the self-service `driver_race_history` table uses Avro, not JSON;
- self-service has no `f1-postgres-cdc` connector;
- the prefix comes from self-service metadata rather than whichever track last wrote `TF_VAR_prefix`;
- reset works only after the local race stops;
- Confluent CLI login is optional when API credentials already exist.

Keep the instructor-led CDC text intact for WSA and standalone. Put the self-service note next to it rather than rewriting workshop behavior.

Remove the stale dbt line from `demo-reference/enrichment_anomaly.sql`. Correct `34–57` to `34–60` in `docs/USE-CASE.md`.

### 19. Reorder the README

Keep the three-track comparison table, then recommend:

```text
First look:        uv run selfservice up
Full architecture: uv run deploy
Instructor event:  wsa
```

Move the long WSA setup behind the solo quick starts or a dedicated link. A newcomer should reach a runnable command before sibling checkouts, 1Password, and attendee-account ranges.

### 20. Retire misleading shell fallbacks

Replace `scripts/setup.sh` and `scripts/teardown.sh` with thin compatibility wrappers around the supported Python commands, or remove them after every reference is updated.

Do not leave scripts that run raw Terraform without shared-output injection and teardown guards.

## P3: tests, lint, and repository cleanup

### 21. Make tests discoverable

Add `pytest` to the dev dependency group and document one command:

```bash
uv run pytest
```

Change `.gitignore` to admit `tests/test_*.py` while keeping generated test credentials and infrastructure ignored. Track the existing SQL-shell test.

Add regression coverage for:

- destroy stop-on-failure;
- scoped ECS selection and wait behavior;
- reset ordering and failed drops;
- self-service reset selection;
- suffix creation and reuse;
- cross-track metadata isolation;
- dead-card cleanup;
- self-service marker cleanup and seed completion;
- automated pacing validation;
- Pit Wall error classification;
- SQL file execution and history-safe parsing.

### 22. Clean stale ignore rules after MCP restoration

Review `confluent-mcp.env`, `generated/`, `scripts/.race-task-arn`, Node package files, and test exceptions after the new entry points land.

Keep entries still written by supported commands. Remove the rest. Add `wsa-output/` regardless.

### 23. Retry only transient Terraform failures

Capture Terraform output, classify known network and service-transient failures, and retry those. Authentication, validation, missing-variable, and resource-collision errors should return immediately.

Preserve state after every failure.

### 24. Add a small CI check

Add CI for:

```bash
uv run pytest
uv run ruff check .
terraform fmt -check -recursive terraform
```

Do not run apply. Terraform validation may require initialized providers, so add it only when CI can cache or install them predictably.

## Deferred work

These findings should not delay the solo repair:

- Extracting the duplicated Confluent base from `terraform/aws` and `terraform/self-service`.
- Reducing the hard-coded 30 GB Postgres volume.
- Removing public Postgres and SSH ingress.
- Replacing the static Postgres password.
- Full region parameterization, including Bedrock inference-profile naming.
- Tracking Terraform lock files.

The Postgres exposure deserves a separate security change. It touches modules used by WSA and needs a rollout plan even if defaults stay compatible.

## Live checks before implementation claims

Two behaviors need a real environment:

1. Query `driver_race_history` on the AWS path before changing its startup options. Connector-created table behavior may differ from the Flink-DDL default; reconcile the code and docs from the observed count.
2. Use `SHOW CREATE TABLE` for `car_telemetry`, `race_standings`, and `driver_race_history` before any table or topic schema change.

Do not infer either result from comments.

## Acceptance criteria

1. Two standalone deployments in one AWS account get different ECR repositories and resolved prefixes.
2. Standalone and self-service coexist in one checkout without sharing identity or destroy inputs.
3. `uv run race stop` changes exactly one ECS service; WSA services remain untouched.
4. A failed attendee destroy prevents the shared destroy from starting.
5. Plain standalone reset clears source data, leaves its service stopped, and reports `uv run race start`.
6. `uv run reset --with-labs` rebuilds LAB 3/4 before restarting the scoped race.
7. Self-service reset works after `uv run f1-race` stops.
8. Destroying either track removes its dead cards; destroying self-service also removes its seed marker.
9. `uv run deploy --automated` creates LAB 3/4 and starts a fresh scoped race.
10. `uv run selfservice up --automated` verifies 198 history rows and creates LAB 3/4.
11. Default solo races reach lap 1 without the ineffective warm-up.
12. Pit Wall reports authentication and broker failures instead of staying silently empty.
13. `uv run setup-mcp` and `uv run setup-rtce` work from either solo card.
14. The self-service guides describe Flink seeding and Avro while WSA and standalone retain CDC instructions.
15. `Walkthrough.md` documents every supported solo command.
16. `wsa-output/` cannot be staged by a normal `git add -A`.
17. `uv run pytest` discovers all tracked tests and passes.
18. A WSA safety diff shows no unintended workshop behavior change.

## Implementation order

1. Add `wsa-output/` ignore protection and fix test discovery.
2. Fix destroy ordering, dead-card cleanup, and self-service marker cleanup.
3. Add scoped race control and rework reset around it.
4. Add suffix persistence, track-specific metadata, and automated pacing validation.
5. Restore `--with-labs` and automated ready-to-demo provisioning.
6. Fix solo warm-up, seed verification, and local pacing.
7. Surface Pit Wall errors and add SQL-shell history plus `--file`.
8. Restore `setup-mcp`, add `setup-rtce`, and verify LAB 5.
9. Correct self-service labs, reorder README, and restore `Walkthrough.md`.
10. Replace or remove shell fallbacks; prune stale ignore rules.
11. Tighten Terraform retries and add the small CI check.

Before each change, capture the current config or output it depends on. Do not commit, push, or edit the user's `wsa-spec-aws.yaml` work unless explicitly requested.
