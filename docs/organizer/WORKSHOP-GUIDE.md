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
and uploads them to the optional dispenser.

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

Every simulator starts at desired count 1. The race loops after lap 60 until the
organizer stops the fleet or tears down the workshop.

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

Races start after provisioning. These commands operate the attendee fleet:

| Command | Simulator tasks | Kafka and lab state | Result |
|---|---:|---|---|
| `uv run workshop stop-races` | Scale to 0 | Preserved | Pause production |
| `uv run workshop start-races` | Scale to 1 | Preserved | Start a new process after a stop |
| `uv run workshop reset-races` | Stop and drain | Clears source data and lab objects | Prepare another lab run |
| `uv run teardown-workshop` | Deleted | Deleted | Remove the workshop |

The fleet commands match `river-racing` simulator clusters in `us-east-1`. A
second workshop in the same AWS account can fall within that scope. Use `--filter`
to operate one test environment:

```bash
uv run workshop start-races --filter f1wp050
uv run workshop stop-races --filter f1wp050
```

The filter is a substring match. It starts or stops the simulator without clearing
Kafka or Flink state.

### Presenter and test accounts

Keep attendee capacity separate from presenter testing. If all provisioned
accounts will be distributed, provision one additional accepted user for the
presenter or use the standalone track. Never share a live presenter login with an
attendee.

Choose the highest-numbered non-attendee account for rehearsals. Stop its simulator
after testing, and run the fleet reset before the event. This keeps experimental
statements in one environment without reducing attendee capacity.

## 5. Reset for another run

```bash
uv run workshop reset-races \
  --creds-glob 'runs/<run-name>/credentials/*.env'
```

Reset performs these operations:

1. Stops matching simulator services and waits for tasks to drain.
2. Stops attendee Flink statements.
3. Drops `car_state`, `pit_decisions`, and `pit_strategy_agent` when present.
4. Removes derived topics and Schema Registry subjects.
5. Advances the `car_telemetry` low watermark and clears source records.
6. Leaves the compacted `race_standings` keys for the next simulator to overwrite.
7. Leaves every simulator stopped.

Wait for `=== Reset complete ===`. If the command reports `Reset INCOMPLETE`, keep
the races stopped, repair the named environment, and rerun reset.

`race_standings` uses the latest startup mode, so race timing must be coordinated
with the Lab 3 window. The attendee walkthrough intentionally contains no fleet
controls. Use `--keep-source` only when the existing source data should remain.

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
uv run workshop stop-races
uv run workshop start-races
uv run workshop reset-races --creds-glob 'runs/<run-name>/credentials/*.env'
uv run teardown-workshop --run-id <run-id>
```
