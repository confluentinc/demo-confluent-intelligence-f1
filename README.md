# F1 Pit Wall AI: River Racing at Silverstone

This repo contains an instructor-led workshop for building a real-time AI pit
strategy system on Confluent Cloud. Live telemetry and race standings flow into
Kafka, Flink SQL detects a tire anomaly, and an AI Streaming Agent recommends
when River Racing should pit.

The main path provisions shared AWS infrastructure plus one isolated Confluent
Cloud environment per attendee. Organizers run the infrastructure; attendees
work in the browser SQL workspace and never run Terraform.

## Workshop path

Start with [PREREQUISITES.md](PREREQUISITES.md). Complete the external account,
tooling, and credential setup before trying a build. In particular, every
attendee invitation must be accepted before `wsa build`; otherwise Terraform
fails during planning.

The organizer lifecycle has six phases:

| Phase | Command or guide |
|---|---|
| Prepare the organization | [PREREQUISITES.md](PREREQUISITES.md) |
| Create | `uv run create-workshop` |
| Verify | `uv run workshop validate` |
| Run | `uv run workshop start-races` / `uv run workshop stop-races` |
| Reset | `uv run workshop reset-races` |
| Teardown | `uv run teardown-workshop` |

[WORKSHOP-GUIDE.md](WORKSHOP-GUIDE.md) covers the complete organizer lifecycle.
Use [RUN-OF-SHOW.md](RUN-OF-SHOW.md) during delivery, and send attendees to
[labs/README.md](labs/README.md).

## What attendees build

The workshop uses two ingestion paths:

```text
Per-attendee ECS simulator -> car_telemetry + race_standings
Shared Postgres -> per-attendee CDC connector -> driver_race_history
                                          |
                                          v
                         LAB 3 Flink SQL -> car_state
                                          |
                                          v
                    LAB 4 streaming agent -> pit_decisions
```

The canonical Flink SQL lives in [`demo-reference/`](demo-reference/) and is
reproduced in the lab guides. Keep those SQL files, the matching lab sections,
and `RUN-OF-SHOW.md` synchronized.

For the scenario, data sources, and intended pit-wall outcome, see
[docs/USE-CASE.md](docs/USE-CASE.md).

## Other tracks

The standalone and self-service tracks remain available for solo demos and
smoke tests, but they are secondary to the workshop handoff. See
[docs/OTHER-TRACKS.md](docs/OTHER-TRACKS.md) for their entry points and detailed
guides.

## Development checks

```bash
uv run pytest
uv run ruff check .
terraform fmt -check -recursive terraform
```

See [docs/constraints.md](docs/constraints.md) before changing the scenario or
Flink design. It records the workshop behavior that must stay fixed.
