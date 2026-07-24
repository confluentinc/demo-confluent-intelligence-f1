# LAB 5 — Social Media Agent (IBM watsonx Orchestrate)

## Overview

Change hats: you're now River Racing's **social-media manager**. The same live
feed your Flink pipeline produces — standings, the tire anomaly, the AI pit call —
is great content. In this lab you'll build a **no-code agent in IBM watsonx
Orchestrate** that reads that live feed and drafts on-brand social posts about the
race, on demand, in chat.

No SQL and no code here: you build the agent in the Orchestrate **Agent Builder**
UI and point it at a race-feed tool your instructor is hosting.

### What you'll accomplish

1. Open watsonx Orchestrate Agent Builder
2. Add the **race-feed** tool (an OpenAPI import)
3. Create the **Social Media Manager** agent with a River Racing persona
4. Chat with it to draft live race posts — including the lap-32 drama

### Prerequisites

- [LAB 4](../LAB4_streaming_agent/LAB4.md) — `car_state` and `pit_decisions` exist
  and a race is running, so the feed has tire/anomaly and pit-call data to post
  about. (Standings alone work even before LAB 3/4 — those fields just stay empty.)
- An **IBM Cloud / watsonx Orchestrate** account (sign-up was a workshop
  prerequisite — see [LAB 1](../LAB1_claim_account/LAB1.md)).
- The **race-feed base URL**, on your credential card as `F1_SOCIAL_FEED_URL`
  (your instructor can also share it directly). One shared service serves
  everyone; you select your own race with your prefix.

> The canonical agent persona, instructions, and example prompts also live in
> [`demo-reference/orchestrate_social_agent.md`](../../../demo-reference/orchestrate_social_agent.md).

## Steps

### Step 1: Add the race-feed tool

In watsonx Orchestrate, open **Agent Builder** and go to **Tools → Add tool →
Import from OpenAPI**. Give it the spec URL — your card's `F1_SOCIAL_FEED_URL`
with `/openapi.json` appended:

```
<F1_SOCIAL_FEED_URL>/openapi.json
```

Import the **`get_race_feed`** operation (`GET /race-feed/{prefix}`). It takes one
parameter, `prefix`, and returns the current race digest — standings, our tire
status, the latest pit recommendation, and a list of recent **headline events**.

> **Why an OpenAPI tool?** Orchestrate agents pull data by calling tools. The
> feed service tails your Kafka topics (`race_standings`, `car_state`,
> `pit_decisions`) and serves a compact, post-ready digest — so the agent always
> writes from live data, never guesses.

### Step 2: Create the agent

**Agents → Create agent.** Name it `River Racing Social`, and paste the
instructions from
[`demo-reference/orchestrate_social_agent.md`](../../../demo-reference/orchestrate_social_agent.md)
into the agent's instructions field. The short version of what they tell the agent:

- You're the social-media manager for River Racing — John Doe, car #88, at
  Silverstone.
- **Always call `get_race_feed` first** (with your `prefix`) and post only from
  what it returns — never invent positions or events.
- Lead with the most recent **headline event**; flag any `PIT NOW` / `PIT SOON`.
- Voice: upbeat, fan-facing, under 280 chars, 1–3 emoji, end with 2–3 hashtags
  (`#RiverRacing #JohnDoe #F1 #BritishGP #Silverstone`).
- These are **drafts** for a human to review — don't claim to have posted them.

Attach the `get_race_feed` tool to the agent, and set the `prefix` value to **your
prefix** (e.g. `f1wp001`) — the same one on your credential card.

### Step 3: Draft a post

In the agent preview chat, try:

```
Draft a hype post about where we are in the race right now.
```

The agent calls `get_race_feed`, then writes a post grounded in the live feed —
for example, around the lap-32 anomaly:

> 🚨 Drama at Silverstone! A front-left tire issue forces the #88 into the pits —
> John Doe boxes from P8. Fresh mediums on, time to charge back. 💪
> #RiverRacing #JohnDoe #BritishGP

Then try a few more (also in the reference doc):

- "We just made a big move — write a post celebrating it."
- "The pit wall just made a call. Draft a post about our strategy."
- "Write a 3-tweet recap thread of John's race so far."

Iterate on the instructions — tone, emoji, hashtags — and watch the drafts change.

### What to expect

| Race moment | What the feed shows | A good post leads with… |
|-------------|---------------------|--------------------------|
| Early laps | Stable top order, John in the pack | "We're in the fight at Silverstone" |
| ~Lap 32 | `tire.anomaly = true`, `PIT NOW` | The tire drama + box call |
| Recovery | Climbing positions in `headline_events` | The fightback up the order |

> If the agent says the feed is quiet, the race may not be running or LAB 3/4
> aren't built yet — standings post fine, but tire/pit content needs the full
> pipeline live. See [troubleshooting](../../shared/troubleshooting.md).

## Conclusion

You've taken a real-time streaming pipeline all the way to a business user: a
no-code agent that turns live race data into publishable content. Wrap up in
[LAB 6 — Wrap-up](../LAB6_wrap_up/LAB6.md).
