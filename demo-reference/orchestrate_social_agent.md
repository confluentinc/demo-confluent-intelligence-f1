# Reference — River Racing Social-Media Agent (IBM watsonx Orchestrate)

Canonical configuration for the **LAB 5** no-code agent. Keep this in sync with
`labs/instructor-led/LAB5_orchestrate_integration/LAB5.md` (see the File Sync Rule
in `CLAUDE.md`). Nothing here is Flink SQL — the agent is built entirely in the
watsonx Orchestrate **Agent Builder** UI and reads live race data from the
`f1-social-feed` service via an OpenAPI tool.

---

## The tool — `get_race_feed`

The shared `f1-social-feed` service exposes one read-only endpoint and publishes
an OpenAPI 3.0 spec that Agent Builder imports directly:

- Spec URL: `<race-feed-base-url>/openapi.json`
- Operation: `GET /race-feed/{prefix}` → `operation_id: get_race_feed`
- Path parameter: `prefix` — the attendee's own prefix (e.g. `f1wp001`)

> **Setting up `<race-feed-base-url>` (organizer).** It is **one shared value for
> the whole workshop**, not a per-attendee credential — every attendee imports the
> same spec URL and differs only by their `prefix` path parameter. Because
> watsonx Orchestrate is SaaS, the service must be reachable over the public
> internet, so:
>
> 1. Host one instance: `uv run f1-social-feed --creds-glob 'runs/*/credentials/*.env'` (binds `:8080`).
> 2. Expose it at a public HTTPS URL — a tunnel (`ngrok http 8080`, `cloudflared`) for a quick session, or a load balancer / reverse proxy for a durable one.
> 3. Share that public base URL with attendees (slide, chat, or the shared claim link). That URL — e.g. `https://f1-feed.example.com` — is `<race-feed-base-url>`.
>
> It is deliberately **not** on the credential card: the card is per-attendee
> Terraform output, while this is a single organizer-hosted endpoint only known
> once you start and expose the service.

Response (digest the agent writes posts from):

| Field | Meaning |
|-------|---------|
| `lap`, `our_position` | Current lap (of 60) and John Doe's position |
| `standings[]` | Leaders + our car (position, driver, team, gap, last lap, compound) |
| `tire` | Our compound, tire age, front-left temp, and the **anomaly** flag (LAB 3) |
| `latest_pit_decision` | Most recent AI call: `PIT NOW` / `PIT SOON` / `STAY OUT` + reasoning (LAB 4) |
| `headline_events[]` | Recent notable moments, newest last — the post hooks |
| `live` | Whether the feed is currently flowing |

> `tire` and `latest_pit_decision` are `null` until the attendee has built LAB 3 /
> LAB 4 and a race is running. `headline_events` is the richest signal — it already
> reads like a feed of post-worthy moments (overtakes, the anomaly, the pit call).

> **Backend is transparent to the agent.** The same tool can be served by
> `f1-social-feed` (tails Kafka) or `f1-social-feed-rtce` (an MCP client to the
> Real-Time Context Engine). The OpenAPI spec and response are identical, so
> nothing in the agent config changes — only which service the organizer hosts.

---

## Agent profile

- **Name:** `River Racing Social` (or `Social Media Manager`)
- **Description:** Drafts on-brand social posts about River Racing's race from the
  live race feed.

## Instructions (paste into the agent's instructions field)

```
You are the social-media manager for the River Racing Formula 1 team. Our driver
is John Doe (car #88) racing the British Grand Prix at Silverstone (60 laps).

Your job: when asked, draft short, high-energy social posts about what is
happening in OUR race, grounded in live data.

DATA
- Always call the get_race_feed tool with prefix "f1wp001" to get the current
  race situation before writing. Never invent positions, gaps, lap numbers, or
  events — use only what the tool returns.
- The headline_events list is your best source of post hooks (overtakes, the
  tire anomaly, the pit call). Lead with the most recent meaningful event.
- If latest_pit_decision is PIT NOW or PIT SOON, that is newsworthy — say so.
- If the tool returns live = false or empty events, say the race feed is quiet
  rather than making something up.

VOICE
- Confident, upbeat, fan-facing. Short sentences. 1–3 emoji max.
- Always third person about the team ("We", "John", "the #88").
- Under 280 characters unless the user asks for a longer recap.
- End with 2–3 hashtags from: #RiverRacing #JohnDoe #F1 #BritishGP #Silverstone
- Never disparage other teams or drivers.

OUTPUT
- Draft the post text only. Do not claim to have published it — these are drafts
  for a human to review and post.
```

## Optional knowledge (RAG)

Attach a short brand-voice doc (sponsors, tone do's/don'ts, approved hashtags) as
a knowledge source if you want richer, more consistent posts. Not required.

---

## Example chat prompts (for the lab + demo)

- "Draft a hype post about where we are in the race right now."
- "We just made a big move — write a post celebrating it."
- "The pit wall just made a call. Draft a post about our strategy."
- "Write a 3-tweet recap thread of John's race so far."

## Expected behavior

The agent calls `get_race_feed`, then drafts a post citing real data, e.g. around
the lap-32 anomaly:

> 🚨 Drama at Silverstone! A front-left tire issue forces the #88 into the pits —
> John Doe boxes from P8. Fresh mediums on, time to charge back. 💪
> #RiverRacing #JohnDoe #BritishGP

…and later in the climb:

> 📈 What a fightback! John Doe is up to P2 on fresh rubber and the fastest car on
> track. The #88 is flying. 🏎️ #RiverRacing #F1 #Silverstone
