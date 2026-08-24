# Organizer Guide — F1 Pit Wall AI Workshop

Running this workshop? **Start here.** This page is for organizers who provision and
run the workshop. Attendees never need it — send them straight to the
[hosted attendee walkthrough](../tracks/HOSTED-WORKSHOP.md).

The main workshop path provisions shared AWS infrastructure plus one isolated
Confluent Cloud environment per attendee. Organizers run the infrastructure;
attendees work in the browser SQL workspace and never run Terraform.

## Start here

1. Complete the [organizer prerequisites](PREREQUISITES.md) (accounts, CLI, AWS,
   Confluent org access).
2. Follow the [workshop guide](WORKSHOP-GUIDE.md) to create and validate the
   attendee environments.
3. Keep the [run of show](RUN-OF-SHOW.md) open during delivery.

> Every attendee invitation must be accepted before `wsa build`; otherwise
> Terraform fails during planning.

## Organizer lifecycle

| Phase | Command or guide |
|---|---|
| Prepare the organization | [Organizer prerequisites](PREREQUISITES.md) |
| Create | `uv run create-workshop` |
| Verify | `uv run workshop validate` |
| Run | `uv run workshop start-races` / `uv run workshop stop-races` |
| Reset | `uv run workshop reset-races` |
| Teardown | `uv run teardown-workshop` |

## Standalone AWS prerequisites (macOS)

For the standalone / solo-demo tracks (not the multi-attendee workshop):

```bash
brew install git uv awscli
brew tap hashicorp/tap
brew install hashicorp/tap/terraform
brew install --cask confluent-cli
brew install --cask docker-desktop
```

## What attendees build

The simulator writes `car_telemetry` and `race_standings`. A shared Postgres CDC connector supplies `driver_race_history`. During the labs, attendees build `car_state`, detect the front-left tire anomaly, create the streaming pit-strategy agent, and write its output to `pit_decisions`.

The SQL reference copies live in [`docs/demo-reference/`](../demo-reference/), while the [hosted attendee walkthrough](../tracks/HOSTED-WORKSHOP.md) keeps every attendee statement inline.

Read the [use case](../reference/USE-CASE.md) for the scenario, source data, and intended pit-wall outcome.

## Repository structure

```text
demo-confluent-intelligence-f1/
├── README.md               # Track chooser
├── docs/
│   ├── backup/             # Compatibility links for old URLs
│   ├── demo-reference/     # Maintainer copies of the Flink SQL
│   ├── maintainers/        # Constraints and implementation notes
│   ├── organizer/          # This organizer guide, prerequisites, and run of show
│   ├── reference/          # Scenario and background material
│   └── tracks/             # Attendee walkthroughs
├── scripts/                # Workshop and self-service commands
└── terraform/              # Shared and per-attendee infrastructure
```

## Development checks

```bash
uv run pytest
uv run ruff check .
terraform fmt -check -recursive terraform
```

Read the [hosted workshop constraints](../maintainers/CONSTRAINTS.md) before changing the scenario or Flink design.

## Related references

- [Hosted attendee walkthrough](../tracks/HOSTED-WORKSHOP.md) — what attendees with instructor-provided logins follow.
- [Self-service workshop walkthrough](../tracks/SELF-SERVICE.md) — what attendees using their own Confluent Cloud accounts follow.
- [Scenario and use case](../reference/USE-CASE.md) — source data and intended outcome.
- [Constraints](../maintainers/CONSTRAINTS.md) — hosted workshop behavior that must stay fixed.
- [Technical notes](../maintainers/TECHNICAL-NOTES.md) — implementation traps and current service behavior.
