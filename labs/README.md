# F1 Pit Wall AI Workshop — Labs

Build a real-time AI pit-strategy system for **River Racing** at the Silverstone
Grand Prix. Live car telemetry and race standings stream into Confluent Cloud,
where you'll use **Flink SQL** to detect tire anomalies and run an **AI Streaming
Agent** (AWS Bedrock / Claude) that recommends when to pit — explaining its
reasoning in natural language.

**Team:** River Racing | **Driver:** John Doe (#88) | **Circuit:** Silverstone | **60 laps**

## Format: instructor-led

Your instructor has pre-provisioned all infrastructure — a dedicated Confluent
Cloud environment per attendee, a CDC connector, the LLM models, and a **live
race simulator already feeding your cluster**. You do **not** install Terraform
or manage cloud accounts, and you do **not** log in to the Confluent Cloud
Console.

Instead you get a **credential card** (a small `.env` file) and run Flink SQL
through the workshop's command-line shell:

```bash
uv run f1-sql
```

Labs 1–4 happen in that shell; LAB 5 is a no-code agent you build in the IBM
watsonx Orchestrate web UI. [LAB 1](instructor-led/LAB1_claim_account/LAB1.md)
walks you through launching the shell.

> **Running solo?** These same labs apply to an environment you provisioned
> yourself, with a couple of differences depending on which way you did it:
>
> - **`uv run selfservice up`** ([docs/SELF-SERVICE.md](../docs/SELF-SERVICE.md)) —
>   Confluent Cloud only. Start your own live feed with `uv run f1-race` instead of
>   relying on an instructor's simulator. There is no CDC connector, so
>   `driver_race_history` is an **Avro** table seeded by a bounded Flink `INSERT`
>   rather than JSON arriving from Postgres.
> - **`uv run deploy`** ([docs/STANDALONE-DEMO.md](../docs/STANDALONE-DEMO.md)) —
>   the same AWS shape as the workshop below (ECS simulator, Postgres CDC, JSON
>   `driver_race_history`); you control your own feed with `uv run race start|stop`.
>
> LAB 5 is optional either way — it needs an external IBM watsonx Orchestrate
> account.

## Architecture

```
Race Simulator (ECS Fargate, one per attendee)
  ├── car_telemetry   (car #88, Avro)      ─┐
  └── race_standings  (22 cars, Avro, keyed)─┤
                                             │
Shared Postgres ─ CDC Debezium ─ driver_race_history (per-attendee connector)
                                             │
                              LAB 3 — Flink SQL (you write this)
                              10s tumbling window + temporal join
                              ML_DETECT_ANOMALIES(tire_temp_fl_c)
                                             │
                                         car_state
                                             │
                              LAB 4 — Flink SQL (you write this)
                              CREATE AGENT + AI_RUN_AGENT
                                             │
                                       pit_decisions
                                             │
                              LAB 5 — IBM watsonx Orchestrate (no-code)
                              reads the live feed via an OpenAPI tool
                              (the shared f1-social-feed service)
                                             │
                                   drafted social posts
```

That's the instructor-led (and standalone-demo) shape. On the self-service track the
top two boxes collapse into one local process — `uv run f1-race` produces both
topics — and the Postgres/CDC row disappears entirely, with `driver_race_history`
seeded once by Flink. Labs 3–5 are byte-for-byte the same either way.

## Labs

| Lab | Time | What you do |
|-----|------|-------------|
| [LAB 1 — Open your environment](instructor-led/LAB1_claim_account/LAB1.md) | 5 min | Launch the `f1-sql` shell with your credential card |
| [LAB 2 — Explore the environment](instructor-led/LAB2_explore_environment/LAB2.md) | 10 min | Inspect the tables, the live feed, and the pre-deployed models — all in SQL |
| [LAB 3 — Stream processing](instructor-led/LAB3_stream_processing/LAB3.md) | 15 min | Window + temporal join + `ML_DETECT_ANOMALIES` → `car_state` |
| [LAB 4 — Streaming agent](instructor-led/LAB4_streaming_agent/LAB4.md) | 20 min | `CREATE AGENT` + `AI_RUN_AGENT` → `pit_decisions` |
| [LAB 5 — Social media agent](instructor-led/LAB5_orchestrate_integration/LAB5.md) | 15 min | Build a no-code **IBM watsonx Orchestrate** agent that drafts posts from the live feed |
| [LAB 6 — Wrap-up](instructor-led/LAB6_wrap_up/LAB6.md) | 5 min | Query the agent's calls and review the outcome |

Stuck? See the [troubleshooting guide](shared/troubleshooting.md).
