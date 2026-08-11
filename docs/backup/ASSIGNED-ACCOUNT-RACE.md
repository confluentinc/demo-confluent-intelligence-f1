# Assigned-account race recovery

Use this page when your instructor has already assigned you a workshop account
but its race feed looks idle. You keep the same credential card and Confluent
environment. This doesn't provision cloud resources and needs no AWS access.

## Start the safe fallback

From the repository root, run:

```bash
uv run f1-race --creds credentials.env --fallback
```

The command checks the last `car_telemetry` record before starting anything. If
telemetry arrived within 90 seconds, it exits successfully and tells you the
account already has a usable feed. Continue the labs normally.

If the feed is idle, the command starts the same continuous race locally with
seed 42, no warm-up laps, and a 30-second gap between races. Leave that terminal
open. Use another terminal for the dashboard and lab commands. Each loop gets a
fresh `race_id`, so you can start Lab 3 or Lab 4 at any point; the tire incident
comes back on the next loop if you miss it.

## Optional account reset

Stop the local fallback with Ctrl-C, then wait 90 seconds. Run:

```bash
uv run f1-reset --creds credentials.env
```

`f1-reset` refuses to run while recent telemetry exists. Once idle, it cancels
only active lab statements that refer to `car_state`, `pit_decisions`, or
`pit_strategy_agent`, then clears existing append-only workshop topics. It
doesn't call AWS and leaves compacted data alone; the next `race_id` provides
the clean boundary there.

## Recovery notes

- Missing credentials: run `uv run f1-onboard --paste` with the claim email, or ask the instructor for a new `.env` card. Never paste keys into chat.
- Stale Confluent CLI context: the race fallback doesn't need a CLI login. RTCE registration does; run `confluent login`, then retry `f1-rtce` with the same card.
- RTCE still materializing: run `uv run f1-rtce --creds credentials.env status`. Wait until both lab topics report online before probing.
- Flink statement failure: open the failed statement in the SQL workspace and read its error. Fix or delete that statement, then rerun only the affected lab.
- Duplicate producer warning: stop every local `f1-race` terminal. Wait 90 seconds and run the fallback command once; it will start only if the account is idle.
- watsonx connection failure: retry the Basic Auth connection once using the endpoint, key, and secret as separate card values. If it still fails, import `<race-feed-base-url>/openapi.json` in Agent Builder.

The OpenAPI route is the workshop fallback for Lab 5. It uses the same assigned
account and participant instructions; there is no second proxy to install.
