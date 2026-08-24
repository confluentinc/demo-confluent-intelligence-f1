# F1 Pit Wall AI: River Racing at Silverstone

Build a real-time AI pit-strategy system on Confluent Cloud. Live telemetry and race standings flow into Kafka, Flink SQL detects a tire anomaly, and a Streaming Agent recommends when River Racing should pit.

<div align="center">
  <img src="./docs/assets/architecture.png" alt="F1 Pit Wall Confluent Intelligence architecture" style="width:100%;max-width:1400px;">
</div>

## Choose your path

> [!IMPORTANT]
>
> **Are you attending the upcoming self-service workshop?** Use the **[self-service workshop walkthrough](docs/tracks/SELF-SERVICE.md)**. You'll sign in with your own Confluent Cloud account, provision an environment, and run the race on your computer.

> [!NOTE]
>
> **Did your instructor give you a separate workshop login?** Use the **[hosted workshop walkthrough](docs/tracks/HOSTED-WORKSHOP.md)**. Your environment and race have already been set up for you.

Still unsure? Check the credential instructions from your instructor. Your own Confluent Cloud account means **self-service**; a workshop username such as `...+f1wp###@confluent.io` means **hosted workshop**.

| If this describes you | Start here |
|---|---|
| **I'm attending a self-service workshop.** I'll use my own Confluent Cloud account, provision my environment, and run the race on my computer. | [Self-service workshop walkthrough](docs/tracks/SELF-SERVICE.md) |
| **My instructor gave me a workshop login.** The environment and race are already running for me. | [Hosted workshop walkthrough](docs/tracks/HOSTED-WORKSHOP.md) |
| **I'm running the workshop.** I need to provision attendee environments and control the race. | [Organizer guide](docs/organizer/README.md) |
| **I'm running the demo by myself.** I want the full AWS-backed deployment used by the hosted workshop. | [Standalone AWS walkthrough](docs/tracks/STANDALONE-DEMO.md) |

## What you'll build

The simulator writes `car_telemetry` and `race_standings`. Historical race data lands in `driver_race_history`. During the labs, you'll build `car_state`, detect a front-left tire anomaly, create a streaming pit-strategy agent, and write its recommendations to `pit_decisions`.

Read the [F1 Pit Wall AI use case](docs/reference/USE-CASE.md) for the scenario, source data, and intended pit-wall outcome.

## Repository map

```text
demo-confluent-intelligence-f1/
├── README.md                        # Choose the right path
├── docs/
│   ├── README.md                     # Documentation index
│   ├── tracks/SELF-SERVICE.md       # Self-service workshop
│   ├── tracks/HOSTED-WORKSHOP.md    # Hosted workshop attendee labs
│   ├── organizer/                   # Hosted workshop setup and run of show
│   ├── reference/                   # Scenario and background material
│   ├── maintainers/                 # Constraints and technical notes
│   └── backup/                      # Compatibility links for old URLs
├── scripts/                         # Workshop and self-service commands
└── terraform/                       # Shared and per-attendee infrastructure
```
