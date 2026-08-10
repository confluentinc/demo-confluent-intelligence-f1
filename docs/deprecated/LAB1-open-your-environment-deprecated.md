# LAB 1 — Open Your Environment (deprecated)

> Retained for reference. Use the canonical [`Walkthrough.md`](../../Walkthrough.md).

## Overview

Your instructor has pre-provisioned a dedicated Confluent Cloud environment for
you and a **live race feed** already streaming into it. You'll sign in to
Confluent Cloud with a workshop account and write all of your Flink SQL in the
browser's SQL workspace.

### What you'll accomplish

1. Get your credential card
2. Sign in to Confluent Cloud and open a SQL workspace
3. Confirm your environment is live
4. Open your live **Pit Wall** dashboard

> **Heads-up for LAB 5:** [LAB 5](LAB5-social-media-agent-deprecated.md) uses
> **IBM watsonx Orchestrate**. Your instructor provides access during the
> workshop — there's nothing to sign up for or set up in advance.

## Steps

### Step 1: Get your credential card

There are two ways to get it, depending on how this session is run:

**A — Instructor-distributed card.** Your instructor hands out (via email or a
shared link) a card named for your prefix, e.g. **`f1wp###.md`**, plus a
companion **`f1wp###.env`**. The card has your sign-in details; the `.env` is
for the dashboard in Step 4.

**B — Self-serve claim.** If you claimed your account yourself (a Google Form
link from your instructor), you'll receive an email listing your environment's
values by name (Console Username, Console Password, Prefix, Kafka API Key, ...).
Your sign-in details are in that email. For the dashboard, run the onboarding
wizard and either answer its prompts one at a time or paste the whole email in
with `--paste`:

```bash
uv run f1-onboard            # prompts field-by-field
uv run f1-onboard --paste     # paste your claim email, then a blank line
```

This writes a local `credentials.env` in the same shape as an
instructor-distributed `.env`.

Either way you end up with two things: **a Confluent Cloud username and
password**, and **a file of API keys** that looks like this:

```
F1_PREFIX=f1wp###
F1_FLINK_REST_ENDPOINT=https://flink.us-east-1.aws.confluent.cloud
F1_ENVIRONMENT_ID=env-xxxxx
F1_COMPUTE_POOL_ID=lfcp-xxxxx
F1_FLINK_API_KEY=...
F1_FLINK_API_SECRET=...
... (Kafka + Schema Registry keys too)
```

Keep both private — between them they grant full access to your environment.

> **LAB 5** also needs a **race-feed base URL**, but that's *not* on your card —
> it's one shared service your instructor gives you the URL for. See
> [LAB 5](LAB5-social-media-agent-deprecated.md).

> Your prefix (`f1wp###` above) is unique to you.

### Step 2: Sign in and open a SQL workspace

Your username is a **workshop account we created for you** — something like
`...+f1wp###@confluent.io`. It is *not* your own work email, and signing in with
your own address won't find your environment.

1. Open the sign-in link on your card (**confluent.cloud**) and log in with the
   username and password you were given.
2. You'll land in your environment, **`RIVER-RACING-f1wp###-ENV`**. It's the only
   one you can see.
3. Open the **Flink** tab and click **Open SQL workspace**.
4. Set the workspace's **catalog** to your environment and **database** to your
   cluster (`RIVER-RACING-f1wp###-CLUSTER`), using the dropdowns above the editor.

You'll write every SQL statement in the rest of this workshop here. Type a
statement into a cell and press **Run** (or Shift-Enter).

### Step 3: Confirm your environment is live

In your workspace:

```sql
SHOW TABLES;
```

You should see three tables — `car_telemetry`, `race_standings`, and
`driver_race_history`. Then check the live feed:

```sql
SELECT * FROM race_standings;
```

You'll see 22 cars with live positions. It's a streaming query, so it keeps
running — use **Stop** when you've seen enough.

> If `SHOW TABLES` errors or returns nothing, check the catalog/database
> dropdowns first — see [troubleshooting](troubleshooting-deprecated.md) or ask
> your instructor.

### Step 4: Open your Pit Wall dashboard

In a terminal (keep the browser workspace open), launch the live race dashboard
— it uses the `.env` file from Step 1:

```bash
uv run f1-pitwall
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
Continue to [LAB 2 — Explore the environment](LAB2-explore-the-environment-deprecated.md).
