# Organizer workshop guide

This guide covers the organizer lifecycle for a multi-attendee F1 Pit Wall AI
workshop. Complete [PREREQUISITES.md](PREREQUISITES.md) first. Use
[RUN-OF-SHOW.md](RUN-OF-SHOW.md) during the event.

## 1. Create the workshop

```bash
uv run create-workshop
```

The command prompts for the attendee count, resource prefix, organizer email
pattern, missing credentials, and final confirmation. It then validates the WSA
spec, builds the shared layer and attendee environments, writes credential cards,
uploads them to the optional dispenser, and runs the race preparation smoke test.
Preparation finishes with every simulator stopped.

The email pattern must contain `{N}` and must match the accepted Confluent Cloud
users prepared earlier. A neutral example is
`organizer+f1wp{N}@example.com`. A value supplied at the prompt or with
`--email-pattern` is remembered in ignored `credentials.env` and written to the
derived `.wsa-spec-generated.yaml`; the committed spec remains reusable.

Useful options:

| Flag | Purpose |
|---|---|
| `--attendees N` | Create N attendee environments |
| `--prefix PREFIX` | Override the resource prefix |
| `--email-pattern PATTERN` | Override the accepted-user address pattern |
| `--concurrency N` | Set parallel Terraform workers; default is 10 |
| `--name NAME` | Name the credential-card directory |
| `--yes` | Skip prompts; missing values and placeholders cause an early failure |
| `--force` | Allow a build when another live run exists |

Use `--force` only after inspecting the existing environments. Two simultaneous
runs with the same prefix compete for the same resource names.

### What the build creates

The shared layer contains the AWS network, one Postgres instance, an ECR image,
and shared Bedrock credentials. Each attendee receives a separate Confluent Cloud
environment, Kafka cluster, Schema Registry context, Flink pool, CDC connector,
ECS simulator, topics, models, connections, and scoped API keys.

Every simulator is provisioned at desired count 0. The preparation smoke test
starts the complete cohort, verifies fresh telemetry with a new `race_id`, resets
the accounts, and leaves them stopped. Once the organizer starts the race, each
simulator loops after lap 60 until it is stopped or the workshop is torn down.

### Real-Time Context Engine keys

The build enables RTCE for the source topic when `TF_VAR_enable_rtce=true` and
mints a Global API key for each attendee service account. The Confluent CLI must
be logged in as `OrganizationAdmin`. If key creation fails, the build still writes
the rest of each card and prints a warning.

Regenerating cards with `workshop creds --rtce-keys` replaces the existing RTCE
key because Global keys are capped per principal and their secrets cannot be read
again. Previously distributed RTCE commands then stop working.

## 2. Verify the build

Run the validator against only the new run's cards:

```bash
uv run workshop validate \
  --creds-glob 'runs/<run-name>/credentials/*.env'
```

Fix every reported environment before distributing accounts. Old run directories
may refer to infrastructure that has already been destroyed, so avoid the broad
default glob when more than one run exists locally.

## 3. Distribute credentials

The build writes two files per attendee:

```text
runs/<run-name>/credentials/f1wp001.md
runs/<run-name>/credentials/f1wp001.env
```

The Markdown card contains the Console login and workspace identifiers. The env
file contains API keys for the local support tools. Hand out both files, or use
the WSA dispenser described in [PREREQUISITES.md](PREREQUISITES.md).

Cards capture the password current at generation time. If WSA rotates an account
password afterward, regenerate the card before handing it out:

```bash
uv run workshop creds \
  --csv <run>/build-output.csv --name <run-name> --resolve-op
```

For dispenser uploads, keep the Google Form link out of the repo and store
`WSA_DISPENSER_SPREADSHEET_ID` in ignored `wsa.env`. The response tab needs the
exact headers `Timestamp`, `First Name`, and `Email Address`. `create-workshop`
uploads only after card generation so RTCE fields reach the Sheet.

## 4. Run the workshop

Use the run ID printed by `create-workshop`. Lifecycle commands read exact ECS
cluster and service names from `runs/<run-id>/manifest.json`.

| Command | Simulator tasks | Kafka and lab state | Result |
|---|---:|---|---|
| `uv run workshop race-status --run-id <run-id>` | Unchanged | Unchanged | Show ECS, race, event age, Flink, and RTCE health |
| `uv run workshop start-races --run-id <run-id>` | Scale to 1 | Preserved | Start the prepared cohort and verify fresh telemetry |
| `uv run workshop stop-races --run-id <run-id>` | Scale to 0 | Preserved | Pause production without resetting |
| `uv run workshop reset-races --run-id <run-id>` | Stop and drain | Clears safe append-only state | Leave the cohort stopped and ready |
| `uv run workshop prepare-races --run-id <run-id>` | Smoke start, then scale to 0 | Reset after smoke test | Rehearse the complete cohort |
| `uv run workshop prepare-social-feed --run-id <run-id> --account 50` | Keep account 50 stopped | Build its Lab 3/4 statements | Prepare the shared Watsonx feed |
| `uv run workshop migrate-race-contract --run-id <run-id> --accounts 50` | Stop only the selected account(s) | Rebuild the two race source tables/topics and update the simulator | Guarded schema migration; requires one to three explicit accounts |
| `uv run teardown-workshop` | Deleted | Deleted | Remove the workshop |

Omit `--run-id` only when one manifest exists under `runs/`. Use `--accounts` for
one to three test accounts; selectors accept comma-separated numbers and ranges:

```bash
uv run workshop start-races --run-id f7zxf --accounts 48-50
uv run workshop stop-races --run-id f7zxf --accounts 48-50
```

The manifest keeps another workshop in the same AWS account outside the command's
scope. Each selected account must resolve to exactly one ECS service.

Start the complete cohort when the session begins:

```bash
uv run workshop start-races --run-id <run-id>
```

Attendees can start Lab 3 or Lab 4 at any point after that command succeeds.
Telemetry and standings replay from the earliest workshop data, while each new
loop has a distinct `race_id`. A missed pit incident returns on the next loop.

### Presenter and test accounts

Keep attendee capacity separate from presenter testing. If all provisioned
accounts will be distributed, provision one additional accepted user for the
presenter or use the standalone track. Never share a live presenter login with an
attendee.

### Shared Watsonx feed

Reserve account 50 for the organizer during Lab 5. If you need 50 attendee seats,
provision account 51 and keep account 50 out of the dispenser inventory.

Run the cohort preparation first. It resets active lab statements. Then prepare
account 50 and start only that race:

```bash
uv run workshop prepare-races --run-id <run-id>
uv run workshop prepare-social-feed --run-id <run-id> --account 50
uv run workshop start-races --run-id <run-id> --accounts 50
```

Start the Kafka-backed endpoint in another terminal:

```bash
uv run f1-social-feed \
  --creds runs/<run-id>/credentials/f1wp050.env \
  --public-base-url https://small-underpass-refinery.ngrok-free.dev \
  --fixed-prefix f1wp050

ngrok http 8080 --url https://small-underpass-refinery.ngrok-free.dev
```

The attendee download is
`https://small-underpass-refinery.ngrok-free.dev/watsonx/f1-race-feed-openapi.json`.
Stop ngrok after the lab. Reset account 50 afterward; the reset cancels its Lab
3/4 jobs and leaves the simulator stopped.

Choose the highest-numbered non-attendee account for rehearsals. Stop and reset
that exact subset afterward. A full-cohort start refuses dirty test accounts and
prints the reset command needed before they can rejoin the cohort.

## 5. Reset for another run

```bash
uv run workshop reset-races --run-id <run-id>
```

Reset performs these operations:

1. Stops the manifest-selected simulator services and waits for tasks to drain.
2. Stops attendee Flink statements.
3. Advances the append-only `car_telemetry` low watermark.
4. Leaves compacted state in place; `race_id` isolates it from the next race.
5. Marks each successfully reset account ready and leaves it stopped.

Wait for `=== Reset complete ===`. If the command reports `Reset INCOMPLETE`, keep
the races stopped, repair the named environment, and rerun reset.

Telemetry and standings use earliest-offset replay, so the organizer does not
coordinate race timing with the Lab 3 window. The attendee walkthrough contains
no fleet controls.

## 6. Tear down

Target the exact run when more than one WSA run exists:

```bash
uv run teardown-workshop --run-id <run-id> --concurrency 4
```

The command destroys attendee stacks and shared infrastructure, rotates attendee
Console passwords, clears the dispenser when configured, and offers to delete the
local card directory. The accepted Confluent user identities remain for reuse.

If you are migrating an existing shared deployment to the generated Postgres
password, follow [the targeted migration runbook](../../terraform/aws-shared/POSTGRES-PASSWORD-MIGRATION.md).
The managed CDC connector needs public access to port 5432 because it runs outside
the workshop VPC. The generated password is no longer committed to the repository,
but it is present in Terraform state and EC2 user data; restrict both Terraform
state access and `ec2:DescribeInstanceAttribute` accordingly.

Password rotation and dispenser clearing need
`~/.wsa/gmail-credentials.json`. Read the teardown output: infrastructure removal
can succeed even when Google authorization fails, leaving old passwords or Sheet
rows active. `--yes` skips confirmation but does not supply missing credentials.

### Rebuild after teardown

Confirm that teardown finished, accepted users still match the selected email
pattern, 1Password has a current password for every account, and no environment
uses the planned prefix. Then rebuild:

```bash
uv run create-workshop \
  --attendees <count> \
  --email-pattern 'organizer+f1wp{N}@example.com' \
  --concurrency 4
```

For a large run, inspect card-generation warnings and confirm the final dispenser
upload happened after all RTCE keys were minted. Validate only the new run's cards
before starting the races.

## Quick reference

```bash
uv run create-workshop
uv run workshop validate --creds-glob 'runs/<run-name>/credentials/*.env'
uv run workshop prepare-races --run-id <run-id>
uv run workshop prepare-social-feed --run-id <run-id> --account 50
uv run workshop race-status --run-id <run-id>
uv run workshop start-races --run-id <run-id>
uv run workshop stop-races --run-id <run-id>
uv run workshop reset-races --run-id <run-id>
uv run teardown-workshop --run-id <run-id>
```
