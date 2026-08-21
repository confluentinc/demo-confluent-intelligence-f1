# Organizer Guide — F1 Pit Wall AI Workshop

Running this workshop? **Start here.** This page is for organizers who provision and
run the workshop. Attendees never need it — send them straight to the
[attendee walkthrough](../../README.md), which is the repository's `README.md`.

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

The SQL reference copies live in [`demo-reference/`](../../demo-reference/), while the attendee [README.md](../../README.md) keeps every attendee statement inline. The former split lab files remain under [`docs/deprecated/`](../deprecated/) for historical reference.

Read [docs/USE-CASE.md](../USE-CASE.md) for the scenario, source data, and intended pit-wall outcome.

## Repository structure

```text
demo-confluent-intelligence-f1/
├── README.md               # Complete attendee workshop (what attendees follow)
├── demo-reference/         # Maintainer copies of the Flink SQL
├── docs/
│   ├── backup/             # Self-service fallback
│   ├── deprecated/         # Retired split lab guides
│   └── organizer/          # This organizer guide, prerequisites, and run of show
├── scripts/                # Workshop and self-service commands
└── terraform/              # Shared and per-attendee infrastructure
```

## Development checks

```bash
uv run pytest
uv run ruff check .
terraform fmt -check -recursive terraform
```

Read [docs/constraints.md](../constraints.md) before changing the scenario or Flink design. It records the workshop behavior that must stay fixed.

## Related references

- [Attendee walkthrough](../../README.md) — what attendees follow (the repo `README.md`).
- [Local self-service guide](../backup/LOCAL-SELF-SERVICE.md) — backup if the
  pre-provisioned environments fail.
- [Other tracks](../OTHER-TRACKS.md) — solo demos and smoke-test entry points.
- [Scenario and use case](../USE-CASE.md) — source data and intended outcome.
- [Constraints](../constraints.md) — workshop behavior that must stay fixed.
