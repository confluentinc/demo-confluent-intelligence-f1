# F1 Pit Wall AI: River Racing at Silverstone

Build a real-time AI pit-strategy system on Confluent Cloud. Live telemetry and race standings flow into Kafka, Flink SQL detects a tire anomaly, and a Streaming Agent recommends when River Racing should pit.

<div align="center">
  <img src="./docs/F1%20Demo%20Architecture%20Diagram.png" alt="F1 Pit Wall Confluent Intelligence architecture" style="width:100%;max-width:1400px;">
</div>

The main workshop path provisions shared AWS infrastructure plus one isolated Confluent Cloud environment per attendee. Organizers run the infrastructure; attendees work in the browser SQL workspace and never run Terraform.

> [!NOTE]
>
> Running the workshop? Start with the organizer prerequisites. Joining as an attendee? Open the walkthrough and follow the instructor's timing.

## 🚀 Quickstart

<table>
<tr>
<th width="25%">Path</th>
<th width="75%">Start here</th>
</tr>
<tr>
<td><strong>Workshop organizer</strong></td>
<td>Complete the <a href="./docs/organizer/PREREQUISITES.md">organizer prerequisites</a>, then use the <a href="./docs/organizer/WORKSHOP-GUIDE.md">workshop guide</a> to create and validate the attendee environments. Keep the <a href="./docs/organizer/RUN-OF-SHOW.md">run of show</a> open during delivery.</td>
</tr>
<tr>
<td><strong>Workshop attendee</strong></td>
<td>Use the single-file <a href="./Walkthrough.md">F1 Pit Wall workshop walkthrough</a>. It contains every attendee step and all SQL used in the labs.</td>
</tr>
<tr>
<td><strong>Backup or solo run</strong></td>
<td>If the pre-provisioned environments fail, switch to the <a href="./docs/backup/LOCAL-SELF-SERVICE.md">local self-service guide</a>. Solo demos and smoke-test entry points live under <a href="./docs/OTHER-TRACKS.md">Other Tracks</a>.</td>
</tr>
</table>

Every attendee invitation must be accepted before `wsa build`; otherwise Terraform fails during planning.

### Organizer lifecycle

| Phase | Command or guide |
|---|---|
| Prepare the organization | [Organizer prerequisites](docs/organizer/PREREQUISITES.md) |
| Create | `uv run create-workshop` |
| Verify | `uv run workshop validate` |
| Run | `uv run workshop start-races` / `uv run workshop stop-races` |
| Reset | `uv run workshop reset-races` |
| Teardown | `uv run teardown-workshop` |

## What attendees build

The simulator writes `car_telemetry` and `race_standings`. A shared Postgres CDC connector supplies `driver_race_history`. During the labs, attendees build `car_state`, detect the front-left tire anomaly, create the streaming pit-strategy agent, and write its output to `pit_decisions`.

The SQL reference copies live in [`demo-reference/`](demo-reference/), while [Walkthrough.md](Walkthrough.md) keeps every attendee statement inline. The former split lab files remain under [`docs/deprecated/`](docs/deprecated/) for historical reference.

Read [docs/USE-CASE.md](docs/USE-CASE.md) for the scenario, source data, and intended pit-wall outcome.

## Standalone AWS prerequisites (macOS)

```bash
brew install git uv awscli
brew tap hashicorp/tap
brew install hashicorp/tap/terraform
brew install --cask confluent-cli
brew install --cask docker-desktop
```

## Repository structure

```text
demo-confluent-intelligence-f1/
├── Walkthrough.md          # Complete attendee workshop
├── demo-reference/         # Maintainer copies of the Flink SQL
├── docs/
│   ├── backup/             # Self-service fallback
│   ├── deprecated/         # Retired split lab guides
│   └── organizer/          # Prerequisites, workshop guide, and run of show
├── scripts/                # Workshop and self-service commands
└── terraform/              # Shared and per-attendee infrastructure
```

## Development checks

```bash
uv run pytest
uv run ruff check .
terraform fmt -check -recursive terraform
```

Read [docs/constraints.md](docs/constraints.md) before changing the scenario or Flink design. It records the workshop behavior that must stay fixed.

## Navigation

- **Overview:** [Main README](./README.md)
- **Workshop:** [Attendee walkthrough](./Walkthrough.md)
- **Backup:** [Local self-service guide](./docs/backup/LOCAL-SELF-SERVICE.md)
