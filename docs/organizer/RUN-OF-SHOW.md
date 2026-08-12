# Presenter run of show

Use this page as the presenter checklist. Attendees work only from the canonical
[`Walkthrough.md`](../../Walkthrough.md); do not paste separate SQL or commands
from this file.

Race timing and fleet operations are instructor-managed. Keep those controls out
of attendee instructions.

## Before attendees arrive

- Validate the exact credential-card set for this run.
- Open the presenter account, Flink SQL workspace, and Pit Wall.
- Confirm `car_telemetry`, `race_standings`, and `driver_race_history` are healthy.
- Confirm the shared Lab 5 race-feed URL and watsonx Orchestrate access.
- Put the walkthrough link and updated `f1-race-feed-openapi.json` file where attendees can find them.

## Opening

Frame the workshop around one question: can a pit wall turn live telemetry,
standings, and historical strategy into a useful decision while the race is
still unfolding?

Remind attendees that they use a workshop login, not their normal Confluent
Cloud account, and that all SQL runs in the browser workspace.

## Lab cues

| Lab | Presenter cue | Check before moving on |
|---|---|---|
| 1 | Open the assigned environment and Pit Wall | Each attendee sees the three source tables |
| 2 | Inspect telemetry, standings, CDC history, models, and connections | History reaches 198 rows and live data advances |
| 3 | Build `car_state`; offer Granite forecasting only if time permits | `car_state` produces one 60-second window per lap and the optional forecast is stopped afterward |
| 4 | Create the streaming agent and `pit_decisions` | Decisions appear and the Pit Wall AI panel unlocks |
| 5 | Build the watsonx Orchestrate social agent | The imported tool returns the live race state |
| 6 | Inspect the important decision and recap the pipeline | Attendees can trace source data to the final output |

## Lab 3 notes

The required path uses `ML_DETECT_ANOMALIES`. The Granite `AI_FORECAST` query is
optional and uses a 20-step horizon. With 60-second windows, it projects roughly
20 laps ahead. Ask attendees to stop that temporary query before
Lab 4 so it does not occupy the shared compute pool.

The standings table reads from the latest offset, so late-running Lab 3 jobs can
miss earlier standings versions. Race timing is coordinated by the presenter;
attendees do not run fleet commands.

## Close

Revisit the completed path: telemetry and standings entered Kafka, Flink joined
and scored them, the streaming agent made a pit recommendation, and the social
agent consumed the same live context. Collect questions before organizer reset
or teardown.

For provisioning, validation, fleet operations, reset, and teardown, use the
[`WORKSHOP-GUIDE.md`](WORKSHOP-GUIDE.md).
