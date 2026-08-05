---
name: f1-credentials
description: How attendee logins, passwords, deployment prefixes, and credential cards actually resolve in this repo — the three moving parts of Confluent Cloud Console access (invited users, 1Password passwords, grant_console_access RBAC), the derived per-track deployment identity in deployment_meta.py, and resolve_card()'s precedence order. Load before touching credential cards, `workshop creds`, `deployment.env`, `f1-onboard`, or anything that authenticates as an attendee.
---

# Secrets & credentials (F1 Pit Wall workshop)

The `## Secrets & Credentials` table in `CLAUDE.md` lists which file holds what and
which script writes it. This skill covers the parts that are **not** derivable from
reading those scripts.

---

## Attendee Console access

Workshop attendees sign in to Confluent Cloud as **pool accounts on the organizer's
configured plus-address pattern** (for example,
`organizer+f1wp{N}@example.com`, supplied through `--email-pattern` or
`WORKSHOP_EMAIL_PATTERN`)
— not as themselves. Three moving parts, none of which a build creates:

1. **The users.** Invited by hand (`confluent iam user invitation create`), then
   accepted + first password set by `wsa accept-account-invitation` (headless browser
   + Gmail API). One-time per account number, **forever** — `wsa clean` rotates
   passwords but never deletes users. See `PREREQUISITES.md`.
2. **The password.** Lives only in the 1Password vault `Workshop Setup Accelerator
   Users`, item `Account NNN`, field `confluent-cloud/password`. Terraform never sees
   it; wsa writes the literal `(from 1Password)` into `build-output.csv`.
   `workshop creds --resolve-op` (always on via `workshop build`) substitutes the real
   value into the card — see `_resolve_op_password` in `scripts/workshop/creds.py`,
   which reconstructs a wsa-internal ref and will break silently if wsa changes it.
   `create.py`'s `_check_console_accounts` fails the build early when a password is
   missing, because **`wsa validate` reports every `source: op` field as OK without
   ever touching the vault**.
3. **The RBAC.** `terraform/modules/environment` binds the user as `EnvironmentAdmin`
   on their own environment, gated by `grant_console_access` (default **false**;
   `wsa-spec-aws.yaml` sets it true). Off for standalone/self-service, whose
   `owner_email` may not resolve as a CC user. The `data "confluent_user"` lookup
   fails at **plan** time if the invite wasn't accepted — that's the hard ordering
   dependency between Phase 0 and `wsa build`.

Cards are only valid for the password current when they were written; regenerating
after a `reset-account-password` is required.

---

## Deployment identity (`scripts/common/deployment_meta.py`)

Two tracks, `standalone` (`terraform/aws`) and `selfservice`
(`terraform/self-service`, suffix `s`), each with its own `runs/<track>/deployment.env`
so one checkout can hold both without either clobbering the other's Terraform inputs.
The prefix is **derived**, not prompted-with-a-shared-example: `$USER` (or a short
hash of the owner email when `$USER` is generic/shared), truncated to 8, plus the
track suffix, max 12 alphanumerics. Deterministic on purpose — `race`, `reset`,
`destroy` and screen-shares all resolve the same names on every rerun. `resolve_prefix`
refuses a value that contradicts live state, so **a deployed prefix can't be renamed
in place**; tear down first.

The shared tier's name is `f1-<prefix>` unless `F1_SHARED_PREFIX` overrides it. It is
not cosmetic: the ECR repo is `force_delete`d and recreated, the image rebuilt, and the
attendee task definition revised (restarting a running race). `deploy.py` detects the
mismatch from `aws-shared`'s `ecr_image_uri`, warns, and under `--automated` **refuses**
— pin the existing name with `export F1_SHARED_PREFIX=<deployed>`.

---

## Credential card resolution

`f1-sql` / `f1-pitwall` / `f1-race` no longer require `--creds`. `resolve_card()` in
`scripts/common/credentials.py` picks the card, first hit wins:

1. `--creds <path>`
2. `$F1_CREDS`
3. `credentials.env` — its `F1_CARD=<path>` pointer (skipped if the target is gone), or
   the file itself when it holds `F1_*` keys (what `f1-onboard` writes)
4. the only card under `runs/*/credentials/*.env`

Ambiguity is an error, never a guess: several cards and no pointer exits listing them.
`deploy.py` and `selfservice up` call `set_active_card()`; `destroy` and
`selfservice down` call `clear_active_card(only_if_under=...)`, scoped so tearing down
one deployment leaves another's pointer alone. The organizer fan-out tools
(`f1-social-feed`, `f1-social-feed-rtce`, `workshop validate`) deliberately keep
explicit `--creds` / `--creds-glob` — operating over many cards is their whole job.

Gitignored. Do not commit. The `aws` tier's flat outputs (`environment_id`,
`kafka_api_key`, `sr_api_key`, ...) are what `wsa-spec-aws.yaml`'s
`credentials:` fields point at (`source: terraform`); `wsa` turns those into
the dispenser CSV and each attendee's claim email. The nested
`attendee_credentials` map output still exists for the single-environment
smoke-test flow (`terraform output -json attendee_credentials`, `deploy.py`).
