---
name: f1-terraform-layout
description: The three Terraform tiers in this repo and how they relate — aws-shared (applied once) vs aws (per attendee) vs self-service (Confluent-only), the inlined Bedrock textgen connection/model each tier carries, per-attendee resource naming and CDC slot isolation, the max_replication_slots=105 sizing decision, and what flink_max_cfu / seconds_per_lap / race_loop actually do. Load before editing anything under terraform/, adding a variable to the aws tier, or reasoning about per-attendee isolation and Flink CFU cost.
---

# Terraform layout (F1 Pit Wall workshop)

Two AWS tiers plus a Confluent-only one. `aws-shared` is applied once; `aws` is
applied per attendee (by `wsa`, or once by `deploy.py`). The `aws` tier consumes
`aws-shared` outputs as variables (injected by wsa, or by `deploy.py` reading the
shared state). `self-service` stands alone.

`terraform/self-service/` is Confluent-**only** — no AWS (Postgres/CDC/ECS/ECR),
and its `driver_race_history` table starts empty: `uv run selfservice up` seeds it
with a bounded Flink INSERT and the local `f1-race` simulator feeds the topics.

Each tier inlines its own Bedrock textgen connection + `CREATE MODEL
llm_textgen_model` statement (`terraform/aws/main.tf`,
`terraform/self-service/main.tf`) — keep the two copies in sync by hand. There
used to be a shared `terraform/modules/llm/` module that also created a Titan
embedding connection/model, but no lab ever referenced `llm_embedding_model`
and it failed to provision (CreateModel error on the embedding connection);
both tiers dropped it and inlined just the working textgen half.

**Naming:** per-attendee CC resources use `RIVER-RACING-${prefix}` (e.g.
`RIVER-RACING-f1wp001-ENV`); ECS resources use the lowercased
`river-racing-${prefix}-<hex>-simulator` (the instructor scripts filter on
`river-racing`).

**Per-attendee isolation:** separate CC environment/cluster/Flink pool; CDC
connector uses `slot.name=f1_cdc_${prefix}` + `publication.name=f1_pub_${prefix}`
so many connectors share one Postgres. Bedrock credentials are shared across all
attendees. `aws-shared` fixes `max_replication_slots` at 105 (the accelerator's
95-account maximum plus 10 spare slots), so resizing a resumed run does not
replace or silently reconfigure the shared database.

**Key `aws` variables:** `prefix`, `owner_email`, `region`,
`confluent_cloud_api_key/_secret`, `aws_bedrock_access_key/_secret`,
`aws_session_token` (optional), and the shared inputs `shared_vpc_id`,
`shared_subnet_ids`, `shared_postgres_host`, `shared_postgres_password`,
`shared_ecr_image_uri`.

> The `shared_*` naming contract is stated in the root `CLAUDE.md` because it also
> governs `wsa-spec-aws.yaml` and `scripts/workshop/wsa.py`, outside `terraform/`.

`flink_max_cfu` (default 10), `seconds_per_lap` (default 20 → 20-minute race; must
match the fixed 20s TUMBLE window in the LAB 3 SQL), `race_loop` (default true)
tune cost/pacing.

The Flink maximum is an autoscaling ceiling, not reserved capacity. Pools are
enforced against actual use independently, and billing is based on consumed
CFU-minutes; configured maxima are not added together as an organization-wide
reservation. Existing generated run snapshots and deployed pools keep their
recorded values unless they are explicitly regenerated or upgraded.
