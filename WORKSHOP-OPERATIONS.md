# F1 Workshop Operations

This is the operator reference for the multi-attendee workshop. It explains the
live topology, race controls, reset behavior, test-account policy, RTCE UPSERT
test status, and full teardown/rebuild path. Commands below target the current
50-attendee run, `f7zxf`, unless a section says otherwise.

## Which documents are authoritative?

Use the root documents during workshop preparation and delivery:

- `WORKSHOP-GUIDE.md` is the organizer lifecycle guide: one-time account setup,
  provisioning, credential cards, the dispenser, validation, reset, and
  teardown.
- `RUN-OF-SHOW.md` is the day-of presenter sheet. It has every attendee command
  in LAB 1 through LAB 6 order, expected results, and presenter notes.
- This file, `WORKSHOP-OPERATIONS.md`, covers live infrastructure operations and
  the current deployment's known state.
- `README.md` is the entry point for choosing workshop, standalone, or
  self-service mode. It is not the day-of script.

The files under `labs/instructor-led/` remain the attendee handouts. Keep them.
They contain more explanation and troubleshooting than the run of show. When
lab SQL changes, update the canonical SQL in `demo-reference/`, the matching lab
guide, and `RUN-OF-SHOW.md` in the same change. This prevents the presenter and
attendees from running different statements.

In short: use `WORKSHOP-GUIDE.md` to prepare and operate the event, then present
from `RUN-OF-SHOW.md`. Send attendees to `labs/instructor-led/`.

## High-level architecture

The workshop has one shared infrastructure layer and 50 isolated attendee
stacks.

Shared once for the workshop:

- AWS VPC and public subnets
- One EC2 Postgres instance containing the 198 historical strategy rows
- One ECR repository and simulator image
- Shared AWS and Bedrock credentials used during provisioning

Created once per attendee:

- One Confluent Cloud environment
- One Kafka cluster, Schema Registry context, and Flink compute pool
- `car_telemetry`, `race_standings`, and `driver_race_history`
- One CDC connector reading the shared Postgres database through its own
  replication slot
- Bedrock connections and models
- One ECS cluster, ECS service, and Fargate simulator task
- Attendee-scoped API keys and Console access

There is no single simulator broadcasting into 50 environments. Each simulator
runs the same image and produces the same race scenario, but it authenticates to
one attendee's Kafka cluster. This gives every attendee an isolated source feed,
isolated topics, and independent lab state. The simulators start a few seconds
apart and can have different random variation.

The data path inside each attendee stack is:

```text
Per-attendee ECS simulator
  -> car_telemetry
  -> race_standings

Shared Postgres
  -> per-attendee CDC connector
  -> driver_race_history

Attendee LAB 3 Flink SQL
  -> car_state

Attendee LAB 4 Flink SQL
  -> pit_decisions
```

The build sets every ECS service to `desired_count = 1`, so all simulators start
as soon as provisioning finishes. Each simulator runs a 60-lap race at one
minute per lap. After lap 60, it waits 30 seconds, performs about five minutes of
pre-race warm-up, and begins another race at lap 1. It continues until the
presenter stops the fleet or tears down the workshop.

## Current live state

On August 4, 2026, the live deployment had:

- 50 simulator clusters and 50 simulator services
- 50 services with desired count 1
- 50 running tasks, with none pending
- Active lap production in account `f1wp050`
- A confirmed lap-60 completion followed by the configured 30-second restart
  delay and a new race

All 50 attendee environments also passed the workshop health validator. The
source pipeline is live. Attendees create the derived `car_state` and
`pit_decisions` pipelines during LAB 3 and LAB 4.

## Stop, start, and reset are different operations

| Command | Simulator tasks | Kafka and lab state | Result |
|---|---:|---|---|
| `workshop stop-races` | Scale to 0 | Preserved | Pauses production |
| `workshop start-races` | Scale to 1 | Preserved | Starts a new simulator process after a stop |
| `workshop reset-races` | Stops and drains | Clears race data and lab objects as described below | Operational clean slate |
| `teardown-workshop` | Deleted | Deleted | Removes the workshop |

### Pause every race

```bash
cd /Users/brenner/code/demo-confluent-intelligence-f1
uv run workshop stop-races
```

The command searches the `us-east-1` AWS account for ECS clusters whose names
contain `river-racing` and end in `-simulator`. It calls the ECS UpdateService API
for each service and sets `desiredCount` to 0. ECS then terminates the running
tasks.

This is a fleet-wide pause. It preserves Kafka records, lab tables, Flink
statements, Schema Registry subjects, and CDC data. A later start creates a new
simulator process and a new race, while the accumulated topic and lab state
remains.

The cluster-name filter is broader than a workshop run ID. If someone creates a
second `river-racing` workshop in the same region and AWS account, the fleet
command will control that workshop too.

### Start every race

```bash
uv run workshop start-races
```

This finds the same ECS services and sets `desiredCount` to 1. If the services
were stopped, ECS launches one simulator task per attendee. Each task initializes
a fresh race and returns to continuous loop mode.

That one command initiates all 50 starts. It does not wait for every task to
become healthy or for lap 1 to arrive. Allow about five minutes for warm-up and
check that the command reports 50 updated services. Running `start-races` while
a service already has a healthy task leaves that task running. Use `stop-races`
first when you need a synchronized restart.

### Reset the workshop to an operational clean slate

Use the current run's credential cards explicitly:

```bash
uv run workshop reset-races \
  --creds-glob 'runs/f7zxf/credentials/*.env'
```

The explicit glob matters. This checkout also has credential cards from older,
destroyed runs. The default `runs/*/credentials/*.env` glob would include those
cards and make the reset report failures for environments that no longer exist.

The reset command:

1. Scales all matching simulator services to 0 and waits for the tasks to drain.
2. Stops Flink statements in each of the 50 attendee environments.
3. Drops `car_state`, `pit_decisions`, and `pit_strategy_agent` when present.
4. Deletes the derived lab topics and their Schema Registry subjects.
5. Clears `car_telemetry` with Kafka's delete-records API.
6. Attempts to clear `race_standings`; Kafka retains it because it is a compacted
   topic. The next simulator overwrites all 22 keys during new-race startup.
7. Leaves every simulator stopped.

Wait for this exact success banner:

```text
=== Reset complete ===
```

If the command prints `Reset INCOMPLETE`, leave the races stopped, fix the named
environment, and rerun the reset.

This is an operational clean slate for the labs, not a literal return to a newly
provisioned account. The following baseline resources intentionally remain:

- The environments, clusters, compute pools, models, connections, and API keys
- The three Terraform-owned source tables
- The 198 historical `driver_race_history` rows
- The latest compacted `race_standings` values until the next race overwrites them
- Any SQL text or tabs retained by a user's browser workspace

The reset code advances the Kafka low watermark for `car_telemetry`, but it does
not explicitly recreate or clear RTCE's materialized view. Verify RTCE after the
restart if the absence of earlier materialized rows matters to the presentation.

Stopping alone does not provide this clean slate. Use `reset-races`.

## Recommended day-of sequence

Finish all presenter testing first. Then run the fleet reset 30 to 45 minutes
before the event:

```bash
cd /Users/brenner/code/demo-confluent-intelligence-f1
uv run workshop reset-races \
  --creds-glob 'runs/f7zxf/credentials/*.env'
```

At the workshop opening:

```bash
uv run workshop start-races
```

The five-minute simulator warm-up overlaps naturally with account claiming and
Console login. Get attendees into LAB 3 well before lap 32 so the anomaly model
has enough training windows before the injected tire-temperature spike.

The reset tool prints the technically strict sequence of submitting LAB 3 before
starting the race because `race_standings` reads from its latest offsets. The
full workshop's LAB 1 and LAB 2 exercises also need live source data. For the
current run of show, start the races at the opening and keep the early labs on
schedule. If exact lap-1 processing in LAB 3 matters more than live data in the
first two labs, leave the fleet stopped until all LAB 3 statements are running.

## Presenter and test accounts

Use `f1wp050` for pre-event tests. The final deployment tests already used that
account, so continuing there keeps experimental statements in one place. The
fleet reset removes its lab state before attendees arrive.

`RUN-OF-SHOW.md` currently suggests using `f1wp001` as the presenter account and
handing out `f1wp002` onward. That leaves 49 attendee accounts. With 50 attendees,
all 50 current accounts must remain available for attendees.

If the presenter needs a dedicated environment during the live event, provision
a 51st accepted workshop login and attendee stack, or use a separate standalone
presenter deployment. Do not share a live presenter account with an attendee.

### Test just one attendee account

If the fleet is stopped while the workshop is idle, use `f1wp050` for a
pre-event test. Start only its simulator with:

```bash
uv run workshop start-races --filter f1wp050
```

The filter is a substring match on ECS cluster names. `f1wp050` matches that
account's one simulator cluster, rather than the 50-cluster fleet. The command
scales its service to one task; it returns once ECS accepts the update, so allow
a short time for the task and the simulator warm-up to begin producing data.

Stop that same test feed when finished:

```bash
uv run workshop stop-races --filter f1wp050
```

For a fresh simulator process in that account, stop it, wait for the task to
drain, then run the filtered start command. This starts a new race from lap 0.
It does not clear Kafka topics, Flink statements, or other lab state. The
existing `workshop reset-races` command resets the entire attendee fleet, so do
not use it merely to prepare one test account.

## RTCE UPSERT verification status

RTCE UPSERT is currently blocked for this workshop organization, not by the
table data or by the simulator. Internal RTCE materializer source defines
`MT_UPSERT_NOT_SUPPORTED` as an organization allowlist rejection. The internal
Upsert Launch Plan says support was rolled back after recovery and scalability
problems and is now gated by the org-level LaunchDarkly flag
`lightning.cheetahdb.upsert.allowlist`.

Request that flag for the `f7zxf` organization before treating any topic-level
change as a fix. Changing only `kafka.cleanup-policy` from `compact` to `delete`
does not turn a Flink table with `changelog.mode = upsert` into an append table;
the current materializer determines its mode from `changelog.mode`. A true RTCE
UPSERT test still requires an upsert table, a compacted topic, and a raw Kafka
key.

### 2026-08-04 f1wp050 follow-up

Two disposable, schema-backed topics now exist in `f1wp050`; their definitions
and the repeatable lookup → Flink UPSERT → lookup procedure are in
`demo-reference/rtce_upsert_verification.md`.

- `rtce_standings_delete_test` copies the live `race_standings` schema, retains
  Flink `changelog.mode = upsert`, and changes only Kafka cleanup to `delete`.
- `rtce_standings_raw_compact_test` uses the documented raw STRING Kafka key,
  Flink upsert mode, and compact cleanup.

Both topics reached RTCE `ACTIVE`, returned full schema metadata, and their
baseline rows were visible through Flink SQL. Early queries returned
`DP_TABLE_NOT_AVAILABLE` or `DP_INVALID_TABLE` while provisioning settled.
Later, correctly formed queries against both topics returned the terminal,
non-retryable `MT_UPSERT_NOT_SUPPORTED` error. A live `car_telemetry` query
succeeded in the same probe, confirming the MCP endpoint, credentials, and
append materialization path are healthy.

The test topics are not fed by the simulator. Their baseline records were
written directly through Flink SQL, then written again after RTCE reached
`ACTIVE` to rule out a post-enable-data requirement. The later terminal error
confirms that a stopped simulator is not the cause.

After the org is allowlisted, use a newly named compact/raw-key topic that has
never previously had RTCE enabled. Internal test guidance calls this out, and a
separate open blocker (`CHEETAH-1418`) documents stale data-provider state when
a Kafka topic is recreated under a previously enabled name. Then run the saved
baseline lookup → same-key Flink UPSERT → lookup procedure and require one row
containing the updated value before using it in the demo.

## Full teardown and rebuild

### Tear down this exact workshop

Use the run ID rather than relying on the newest-run selection:

```bash
cd /Users/brenner/code/demo-confluent-intelligence-f1
uv run teardown-workshop --run-id f7zxf --concurrency 4
```

The command shows the run and asks for confirmation. It then destroys all 50
attendee stacks and the shared AWS layer. It also resets the attendee Console
passwords, clears the dispenser sheet, and offers to delete the local credential
cards. The accepted Confluent Cloud user identities remain for reuse.

Password rotation and dispenser clearing require the Google OAuth client at
`~/.wsa/gmail-credentials.json`. Read the teardown output. If the file is missing
or Google authorization fails, infrastructure destruction can still succeed
while old passwords or dispenser rows remain active.

### Rebuild 50 attendee stacks

After teardown finishes successfully:

```bash
uv run create-workshop \
  --attendees 50 \
  --concurrency 4
```

Keep the interactive confirmation for a high-impact rebuild. Use `--yes` only
after confirming that every required secret is already available. Do not use
`--force` to stack another run on top of `f7zxf`; the normal guard against a
second live workshop is useful.

The 50-account path has been exercised successfully. The `f7zxf` build completed
all 50 attendee stacks with four concurrent Terraform workers in 1 hour 51
minutes, after which all 50 passed validation. A rebuild is a proven path, but it
is still a large destructive operation with several external dependencies.

Plan for these checks:

- Docker Desktop, AWS credentials, Terraform, the Confluent credentials, and
  Bedrock credentials must work before the build begins.
- All 50 accepted attendee Console users and their current passwords must exist
  in 1Password. Touch ID authorization can pause or degrade password resolution.
- The build must export the attendee count so shared Postgres has at least 60
  replication slots. `create-workshop --attendees 50` handles this automatically.
- Card generation must produce 50 nonblank Console passwords and 50 RTCE key
  pairs. Inspect the warnings rather than assuming a successful Terraform build
  means the cards are complete.
- The final 50-row dispenser upload must happen after the final RTCE keys are
  minted. Re-upload if any cards or RTCE keys are regenerated.
- Run the provisioned-environment validator against only the new run's cards.

```bash
uv run workshop validate \
  --creds-glob 'runs/<new-run-id>/credentials/*.env'
```

Teardown permanently removes all attendee environments, topics, lab work, and
the shared AWS infrastructure. Rebuild is operationally safe when the
prerequisites pass, but allow at least two hours and keep time for card,
dispenser, and live-feed validation afterward.
