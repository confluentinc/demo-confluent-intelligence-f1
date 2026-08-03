# WORKSHOP GUIDE

How to deploy, run, and reset a multi-participant F1 Pit Wall AI workshop.

---

## Prerequisites

- **wsa** binary: clone `confluentinc/workshop-setup-accelerator` as a sibling directory
- **Terraform**, **Docker Desktop** (running), **AWS CLI** (configured)
- **Confluent Cloud API key + secret** (org-level, with environment admin)
- **AWS Bedrock access key + secret** (IAM user with `bedrock:InvokeModel`)
- **Owner email** (for AWS resource tagging)
- **1Password CLI (`op`)**, signed in — this is where the attendee Confluent Cloud
  passwords live (see the next section). Turn on 1Password desktop app → Settings →
  Developer → **"Integrate with 1Password CLI"**. A shell `OP_SESSION_*` token is not
  enough: `wsa` shells out to `op` from child processes, and those fail with "not
  currently signed in" the moment a session expires mid-run.

You'll be prompted for any missing secrets on first run and they're saved to
`credentials.env` for the next one, so there's nothing to inject by hand. If you'd
rather keep them in 1Password, write your own `.env.tpl` and use `op run --env-file`.
There is no `.env.tpl` in this repo — the one in the `wsa` checkout resolves against
the TMM-owned `Workshop Setup Accelerator` vault, which most people can't read, so
copying that command is a dead end.

---

## One-time org prep: the attendee logins

Attendees sign in to Confluent Cloud, so each account number needs a real CC user
with a password. **`create-workshop` does not create these** — it checks they exist
and refuses to build without them.

Do this **once, ever** — not per workshop. `wsa clean` rotates the passwords at
teardown but never deletes the users, so the next workshop reuses them.

> **Already done for accounts 1-40** (`bheintz+f1wp1..40@confluent.io`, prepped
> 2026-08-03). Skip this section unless you're adding account 41 or higher, or a
> different organizer is running the workshop from a different `email_pattern`.

```bash
# 1. Invite one user per account number, matching wsa-spec-aws.yaml's email_pattern.
for i in $(seq 1 40); do
  confluent iam user invitation create "bheintz+f1wp${i}@confluent.io"
done

# 2. Smoke-test ONE account first — this drives a headless browser, and finding out
#    it works on account 1 beats finding out it doesn't on account 40.
<wsa>/bin/wsa accept-account-invitation -w wsa-spec-aws.yaml \
  --accounts 1 --gmail-credentials ~/.wsa/gmail-credentials.json

# 3. Then the rest. --accounts is REQUIRED here: its default is the spec's
#    account_count (5), NOT everyone you just invited, so omitting it silently
#    leaves accounts 6+ as pending invitations.
<wsa>/bin/wsa accept-account-invitation -w wsa-spec-aws.yaml \
  --accounts 2-40 --gmail-credentials ~/.wsa/gmail-credentials.json

# 4. Verify before you ever run a build. An unaccepted user fails
#    terraform/modules/environment's confluent_user lookup at PLAN time.
confluent iam user list -o json | grep -c 'f1wp'   # expect your account count
op read "op://Workshop Setup Accelerator Users/Account 040/confluent-cloud/password" >/dev/null && echo ok
```

No `op run` wrapper on steps 2-3: `accept-account-invitation` needs no `TF_VAR_*`
at all. It talks to Gmail and 1Password directly, and it writes to your **own**
`Workshop Setup Accelerator Users` vault (created by wsa on first use) — not the
shared TMM vault.

It reads the invitation emails over the Gmail API, so it needs a Google Cloud OAuth
client of type **Desktop app** (wsa hardcodes the `http://localhost:8085/callback`
loopback redirect). See the wsa repo's `user-accounts/user-accounts.md`. Consent must
be given as the mailbox owner — all `+f1wpN` addresses land in that one inbox via
plus-aliasing. Invitation emails are matched with `newer_than:3d`, so accept within
three days of inviting.

> **When acceptance fails partway.** A half-failed account ("login verification
> failed", "setInputValue: element not found") has spent its invite ticket: the
> invitation sits at `INVITE_STATUS_SENT` forever, retrying the accept fails again,
> and it can't be cleaned up — `confluent iam user delete` doesn't work on a pending
> user and there's no `invitation delete`. The repair is to just re-issue the invite
> and accept again:
>
> ```bash
> confluent iam user invitation create "bheintz+f1wp7@confluent.io"
> <wsa>/bin/wsa accept-account-invitation -w wsa-spec-aws.yaml --accounts 7 \
>   --gmail-credentials ~/.wsa/gmail-credentials.json
> ```

Passwords end up in the 1Password vault **`Workshop Setup Accelerator Users`**,
item `Account NNN`, field `confluent-cloud/password`. That vault is the only
place they exist — Terraform never sees them, and wsa writes the placeholder
`(from 1Password)` into `build-output.csv`.

> **Growing the workshop.** `--attendees N` needs no file edits — it sets the
> spec's `account_count` and the shared Postgres slot capacity itself. What it
> can't do is invent logins: going past the account numbers you've already
> invited means running both steps above for the new numbers.
> `create-workshop` stops and names the missing accounts before it builds
> anything.

---

## Step 1: Create the Workshop

```bash
uv run create-workshop --attendees 5
```

This single command runs the full pipeline:

1. Checks that `wsa`, Terraform, Docker, and AWS are available
2. Prompts for any missing secrets (and saves them to `credentials.env`)
3. Confirms every attendee's Console password is in 1Password (see the one-time
   prep above) — this fails the build early rather than shipping unusable cards
4. Validates the workshop spec (`wsa-spec-aws.yaml`)
5. Builds all attendee environments (Confluent Cloud + AWS infrastructure)
6. Writes credential cards to `runs/<name>/credentials/`, resolving each password
   out of 1Password as it goes
7. Prints next-steps

Options:
| Flag | Default | What it does |
|------|---------|--------------|
| `--attendees N` | prompted (or spec default) | Number of attendee environments |
| `-c, --concurrency N` | 4 | Parallel Terraform runs |
| `-n, --name NAME` | the wsa run-id | Label for the card directory |
| `--yes` | off | Skip prompts (fails if secrets are missing) |
| `--force` | off | Allow building over an existing live run |

Once complete, each attendee has a Confluent Cloud login and a credential card.
Races start automatically (ECS services launch at desired count 1).

> **Real-Time Context Engine.** The build enables RTCE on `car_telemetry` and
> `race_standings` (Terraform, `modules/topics`) and mints each attendee a Global
> API key owned by their own service account, so their card carries a ready-to-paste
> `claude mcp add` line. Two things to know:
>
> - It needs the `confluent` CLI logged in as **OrganizationAdmin** — only OrgAdmin
>   or ResourceOwner-on-that-SA can create a Global key for a service account. If
>   it isn't, cards are written without the RTCE section and the run warns; every
>   other lab is unaffected. `--no-rtce-keys` skips it deliberately.
> - **Regenerating cards replaces the key.** Global keys are capped at 2 per
>   principal and a secret can't be re-read, so a second `workshop creds --rtce-keys`
>   deletes the first key. Previously handed-out cards stop working for RTCE.
>
> Set `TF_VAR_enable_rtce=false` if RTCE isn't enabled on your org or you're
> building outside its 11 supported AWS regions (`confluent rtce region list`).

---

## Step 2: Hand Out Credentials

Credential cards are at:

```
runs/<name>/credentials/f1wp001.md    <- the handout: sign-in URL, username, password
runs/<name>/credentials/f1wp001.env   <- companion API keys, for f1-pitwall
...
```

The `.md` is what an attendee reads: it leads with their Confluent Cloud sign-in
details, then the environment/compute-pool IDs they need in the SQL workspace.
The `.env` holds the Kafka, Schema Registry, and Flink API keys. Hand out both,
or use the wsa dispenser for self-serve claim (`wsa dispenser-upload` resolves
the same passwords from 1Password and mails them to the claimant).

> **Dispenser attendees and the RTCE command.** A dispenser claimant never sees
> the `.md` card, so `workshop creds` appends a
> `Real-Time Context Engine / MCP Setup Command` column to that run's
> `build-output.csv`. `wsa dispenser-upload` carries it through to the claim
> email like any other credential — the dispenser's Apps Script groups columns by
> their `Provider / Field` header, so no dispenser change is needed. Pass
> `--no-dispenser-column` to skip the rewrite. (wsa re-adds its own `Claimed By`
> and `Timestamp` columns on every upload, so the extra column is safe.)

> **Order matters.** Cards are only valid for the password that was current when
> they were written. Running `wsa reset-account-password` afterward silently
> invalidates every card — regenerate them with
> `uv run workshop creds --csv <run>/build-output.csv --name <name> --resolve-op`.
> The normal teardown → create cycle is safe: rotation happens at teardown, and
> the next `create-workshop` reads the new value.

---

## Step 3: Run the Workshop

Races are already running after `create-workshop`. Attendees work through the
labs (LAB 1-4 in Flink SQL, LAB 5 in watsonx Orchestrate).

### Commands you'll need during the workshop

| When | Command |
|------|---------|
| Check environment health | `uv run workshop validate --creds-glob 'runs/<name>/credentials/*.env'` |
| Stop all races | `uv run workshop stop-races` |
| Start all races | `uv run workshop start-races` |

---

## Step 4: Reset for Another Run

When you want a clean slate (e.g., a second cohort, or attendees need to redo
the labs from scratch):

```bash
uv run workshop reset-races
```

This command:

1. **Stops** all race feeds (scales every ECS simulator to 0)
2. **Waits** for all simulator tasks to drain
3. **Deletes** running Flink statements in every attendee environment
4. **Drops** lab objects (`car_state`, `pit_decisions`, `pit_strategy_agent`)
5. **Removes** lab topics and Schema Registry subjects
6. **Truncates** source topics (`car_telemetry`, `race_standings`)
7. **Leaves feeds stopped** so attendees can submit LAB 3 first

After the reset, the workflow is:

```
1. Attendees submit LAB 3         (in their SQL workspace)
2. Instructor starts the races    uv run workshop start-races
3. Attendees continue to LAB 4+
```

This ordering matters: `race_standings` reads from `latest`, so LAB 3 must be
running before standings start flowing, or the first laps are silently lost.

Use `--keep-source` to skip truncating the source topics (useful if you just
want to clear the lab objects without resetting race data).

---

## Step 5: Tear Down

When the workshop is over:

```bash
uv run teardown-workshop
```

This command:

1. Finds the most recent live workshop run
2. Asks for confirmation
3. Destroys all attendee environments (Terraform destroy)
4. Rotates every attendee's Console password and clears the dispenser sheet, so
   nobody can sign back in with an old card
5. Offers to delete the credential card directory

The CC user accounts themselves survive teardown — that's why the one-time prep
is one-time.

Use `--yes` to skip the confirmation prompt. Use `--run-id <id>` to target a
specific run instead of the newest one.

---

## Quick Reference

```bash
# Create
uv run create-workshop --attendees 5

# During the workshop
uv run workshop start-races
uv run workshop stop-races
uv run workshop validate --creds-glob 'runs/*/credentials/*.env'

# Reset for a new run
uv run workshop reset-races
# → attendees submit LAB 3 → then:
uv run workshop start-races

# Tear down
uv run teardown-workshop
```
