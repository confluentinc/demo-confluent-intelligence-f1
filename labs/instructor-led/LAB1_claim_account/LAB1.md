# LAB 1 — Open Your Environment

## Overview

Your instructor has pre-provisioned a dedicated Confluent Cloud environment for
you and a **live race feed** already streaming into it. You won't log in to the
Confluent Console — instead you'll connect to your environment with the workshop
SQL shell, using a credential card your instructor gives you.

### What you'll accomplish

1. Get your credential card
2. Launch the `f1-sql` shell
3. Confirm your environment is live
4. Open your live **Pit Wall** dashboard

> **Heads-up for LAB 5:** [LAB 5](../LAB5_orchestrate_integration/LAB5.md) uses
> **IBM watsonx Orchestrate**. Your instructor provides access during the
> workshop — there's nothing to sign up for or set up in advance.

## Steps

### Step 1: Get your credential card

There are two ways to get connected, depending on how this session is run:

**A — Instructor-distributed card.** Your instructor hands out (via email or a
shared link) a small file named for your prefix, e.g. **`f1wp###.env`**. Save
it and skip to Step 2.

**B — Self-serve claim.** If you claimed your account yourself (a Google Form
link from your instructor), you'll receive an email listing your environment's
values by name (Prefix, Environment ID, Kafka API Key, ...). Run the onboarding
wizard and either answer its prompts one at a time or paste the whole email in
with `--paste`:

```bash
uv run f1-onboard            # prompts field-by-field
uv run f1-onboard --paste     # paste your claim email, then a blank line
```

This writes a local `credentials.env` in the same shape as an
instructor-distributed card — use it exactly the same way in the steps below
(just swap `f1wp###.env` for `credentials.env`).

Either way, the file contains the API keys that connect you to *your*
environment — keep it private. It looks like this:

```
F1_PREFIX=f1wp###
F1_FLINK_REST_ENDPOINT=https://flink.us-east-1.aws.confluent.cloud
F1_ENVIRONMENT_ID=env-xxxxx
F1_COMPUTE_POOL_ID=lfcp-xxxxx
F1_FLINK_API_KEY=...
F1_FLINK_API_SECRET=...
... (Kafka + Schema Registry keys too)
```

> **LAB 5** also needs a **race-feed base URL**, but that's *not* on your card —
> it's one shared service your instructor gives you the URL for. See
> [LAB 5](../LAB5_orchestrate_integration/LAB5.md).

> Your prefix (`f1wp###` above) is unique to you. You never sign in with an
> email and password — the keys in this file are your access.

### Step 2: Launch the SQL shell

From the workshop materials directory (your instructor will tell you where, or
you'll be in a prepared environment already), run:

```bash
uv run f1-sql --creds f1wp###.env
```

You should see:

```
Connected to RIVER-RACING-f1wp001-ENV / RIVER-RACING-f1wp001-CLUSTER
f1-sql>
```

The shell runs Flink SQL against your environment. End every statement with `;`.
Type `\help` for help, `\q` to quit.

### Step 3: Confirm your environment is live

At the `f1-sql>` prompt:

```sql
SHOW TABLES;
```

You should see three tables — `car_telemetry`, `race_standings`, and
`driver_race_history`. Then check the live feed:

```sql
SELECT * FROM race_standings;
```

You'll see 22 cars with live positions. (Press Ctrl-C to stop a streaming query
and return to the prompt.)

> If `SHOW TABLES` errors or returns nothing, your card may be wrong or your
> environment not ready — see [troubleshooting](../../shared/troubleshooting.md)
> or ask your instructor.

### Step 4: Open your Pit Wall dashboard

Open a **second terminal** (keep `f1-sql` running in the first) and launch the
live race dashboard with the same credential card:

```bash
uv run f1-pitwall --creds f1wp###.env
```

A browser opens at **http://localhost:8000** showing your race: a Silverstone
track map with all 22 cars, the live leaderboard, and car #88's tyre/fuel gauges.

You'll notice two panels are **locked**:

- 🔒 **ANOMALY DETECTION** — activates when you build `car_state` in **LAB 3**
- 🔒 **AI PIT STRATEGIST** — activates when you build `pit_decisions` in **LAB 4**

That's the goal of the labs: you'll bring those panels to life yourself. Keep the
dashboard open in a window you can watch as you work.

> The dashboard only *reads* your topics — it never runs Flink SQL, so it won't
> interfere with your labs. Stop it any time with Ctrl-C.

## Conclusion

You're connected to your environment, data is flowing, and your Pit Wall is live.
Continue to [LAB 2 — Explore the environment](../LAB2_explore_environment/LAB2.md).
