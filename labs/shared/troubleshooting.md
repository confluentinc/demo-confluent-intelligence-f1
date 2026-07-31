# Troubleshooting

## `f1-sql` won't connect

- The shell prints which card it used, right under the `Connected` line — check it's
  yours. If it says it found none (or more than one), name yours explicitly:
  `uv run f1-sql --creds <your-prefix>.env`.
- The card must contain `F1_FLINK_*`, `F1_ORGANIZATION_ID`, `F1_ENVIRONMENT_ID`,
  and `F1_COMPUTE_POOL_ID`. If a key is missing, ask your instructor to
  regenerate it (`uv run workshop creds`).
- A `401/403` means the API keys in your card are wrong or revoked — get a fresh
  card from your instructor.

## `car_telemetry` / `race_standings` look idle

The race simulator runs as an always-on service that replays the race
back-to-back (`RACE_LOOP=true`). Between races there's a short pause, and laps
arrive at the configured pace (default 60s/lap). Re-run your `SELECT` after a
few seconds. If nothing arrives for several minutes, tell your instructor — only
they can inspect or restart the simulators (`uv run workshop start-races`, run
from the organizer's machine with AWS access; you have Confluent API keys only).

> Tip: a streaming `SELECT` keeps running. Press Ctrl-C to stop it and return to
> the `f1-sql>` prompt.

## `race_standings` has data but `car_state` is empty

`car_state`'s temporal join needs **both** `car_telemetry` and `race_standings`
to have data with advancing watermarks, and `ML_DETECT_ANOMALIES` withholds
output until it has ~20 windows. Give it a couple of minutes of live data.

Also confirm the join is written on the raw `event_time` (as in the LAB 3 SQL).
If you moved the `FOR SYSTEM_TIME AS OF` join after the `TUMBLE`, `window_time`
loses its rowtime attribute and the join silently returns zero rows.

## No anomaly around lap 32

- `ML_DETECT_ANOMALIES` needs at least 20 data points (`minTrainingSize=20`), so
  it can't fire on the early laps.
- Verify the spike is present:
  ```sql
  SELECT lap, tire_temp_fl_c FROM `car_state` WHERE lap BETWEEN 30 AND 34;
  ```
  You should see `tire_temp_fl_c` jump to ~145°C.

## The agent outputs odd or empty fields

- Inspect the raw model output:
  ```sql
  SELECT lap, suggestion, raw_response FROM `pit_decisions` ORDER BY lap DESC LIMIT 5;
  ```
- If `suggestion` is null but `raw_response` has text, the LLM emitted a slightly
  different label format — the `\*{0,2}` markers in the regex tolerate optional
  markdown bold, but unusual phrasing can still slip through. Re-running usually
  resolves it.
- If `raw_response` is empty/erroring across the board, the shared Bedrock quota
  may be throttling many attendees at once — flag it to your instructor.

## `SHOW MODELS;` / `SHOW AGENTS;` returns nothing

This almost always means your card points at the wrong environment. Quit (`\q`)
and relaunch `f1-sql` with your own `<prefix>.env`. The models are pre-deployed
per environment, so they only appear when you're connected to yours.

## LAB 5 — Orchestrate agent / race-feed tool

- **Tool import fails.** Make sure you imported the spec URL your instructor gave
  you, ending in `/openapi.json` (not the `/race-feed/...` path itself). The
  service must be reachable from Orchestrate — if your instructor is still
  bringing it up, give it a minute.
- **Agent returns "no race feed for prefix …" (404).** The `prefix` on the tool
  must match your credential card (e.g. `f1wp001`). Set it on the tool, or pass it
  in chat, exactly as written on your card.
- **Posts have standings but no tire/anomaly/pit content.** Those fields
  (`tire`, `latest_pit_decision`) are empty until you've built LAB 3 / LAB 4 and a
  race is running. Standings-only posts are expected before then.
- **Agent says the feed is quiet / `live` is false.** No record has arrived
  recently — the race may be between loops or stopped. Re-ask after a few seconds,
  or ask your instructor to confirm the feed is running (that's theirs to restart,
  not yours).
- **Agent invents positions or events.** Re-check the instructions tell it to
  *always call `get_race_feed` first* and post only from the returned data (see
  [`demo-reference/orchestrate_social_agent.md`](../../demo-reference/orchestrate_social_agent.md)).

## I want to start over

Drop your lab objects and re-run LAB 3 → LAB 4:

```sql
DROP TABLE IF EXISTS `pit_decisions`;
DROP TABLE IF EXISTS `car_state`;
DROP AGENT IF EXISTS `pit_strategy_agent`;
```

The source tables and the live feed are untouched, so you can rebuild
immediately.
