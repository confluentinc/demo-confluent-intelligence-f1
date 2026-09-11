# Organizer Guide — F1 Pit Wall AI Workshop

**Running the instructor-led workshop? This is the only page you need.** It takes you
from an empty machine to a room full of attendees writing Flink SQL, and back to a
clean teardown — in order, start to finish.

Attendees never see this guide. Send them straight to the
[hosted attendee walkthrough](../tracks/HOSTED-WORKSHOP.md).

### How it works

You provision **shared AWS infrastructure once**, plus **one isolated Confluent Cloud
environment per attendee**. Attendees log in to the Confluent Cloud Console with a
workshop credential card and write every lab in the browser Flink SQL workspace — they
never run Terraform or any command in this repo. One command, `create-workshop`, does
the provisioning; you drive the race and clean up afterward.

### The lifecycle

| Phase | You run | When |
|---|---|---|
| **[1. Prerequisites](#1-prerequisites-one-time)** | Set up your machine, org, and logins | Once, before your first workshop |
| **[2. Create](#2-create-the-workshop)** | `uv run create-workshop` | Before each event |
| **[3. Verify](#3-verify-the-build)** | `uv run workshop validate` | After every build |
| **[4. Distribute](#4-distribute-credentials)** | Hand out cards or the claim link | Before attendees arrive |
| **[5. Run](#5-run-the-workshop-day-of)** | `uv run workshop start-races` / `stop-races` | During the event |
| **[6. Reset](#6-reset-between-runs)** | `uv run workshop reset-races` | Between back-to-back sessions |
| **[7. Teardown](#7-tear-down)** | `uv run teardown-workshop` | When you're done |

> **The one rule that fails a build:** every attendee invitation must be **accepted**
> before you create the workshop (see [1.4](#14-attendee-logins)). The attendee Terraform
> reads each Confluent user during `plan`; a pending invitation fails the build before any
> resources are created.

---

## 1. Prerequisites (one-time)

Do these once. After that, running another workshop is just [step 2](#2-create-the-workshop)
onward.

### 1.1 Tools and access

You need Confluent Cloud **`OrganizationAdmin`** (required for user invitations and the
Global API keys that Real-Time Context Engine uses) and **AWS credentials for `us-east-1`
with Bedrock access**.

Install the CLI tools (macOS):

```bash
brew install git uv awscli
brew tap hashicorp/tap
brew install hashicorp/tap/terraform
brew install --cask confluent-cli
brew install --cask docker-desktop
```

Confirm everything is wired up — run this in the shell you'll build from:

```bash
confluent organization describe                               # OrganizationAdmin
aws sts get-caller-identity                                   # AWS session
aws bedrock list-foundation-models --region us-east-1 >/dev/null   # Bedrock access
terraform version                                             # 1.3 or later
docker info >/dev/null
uv --version
```

> **Build from the shell where `aws sts get-caller-identity` above succeeds.** AWS
> credentials (an SSO session, `AWS_PROFILE`, or exported keys) live in that shell's
> environment — not in this repo or `credentials.env`. A fresh terminal tab with no AWS
> session fails Terraform's `aws-shared` phase with `No valid credential sources found`,
> even if the shared infra already deployed successfully from another shell.

### 1.2 Build the WSA binary

Provisioning is owned by the **Workshop Setup Accelerator (`wsa`)**, which this repo drives
through `uv run` wrappers. Clone it next to this repo and build the binary:

```bash
git clone git@github.com:confluentinc/workshop-setup-accelerator.git ../workshop-setup-accelerator
make -C ../workshop-setup-accelerator build
```

This repo requires **WSA ≥ 0.3.0** (the spec sets `wsa_version: ">=0.3.0"` and WSA
strict-decodes it). Confirm the built binary is current:

```bash
../workshop-setup-accelerator/bin/wsa --version    # must report 0.3.0 or newer
```

If you have an older checkout, `git pull` and rerun `make build` — a stale binary refuses
the spec at load time. The wrappers find the binary in `$WSA_HOME`, the sibling checkout,
or `$PATH`; set `WSA_HOME` if yours lives elsewhere.

### 1.3 Workshop secrets

Copy the example and fill in the five required values (or export them from a secret
manager — process-environment values win over the file):

```bash
cp credentials.env.example credentials.env
chmod 600 credentials.env
```

`credentials.env` is gitignored. Both build and teardown read these values.

### 1.4 Attendee logins

Attendees share a **pool of accepted Confluent Cloud users** — one per account number.
Set them up now; invitation acceptance has latency, so don't leave it to the day of.

**1Password.** Install the 1Password CLI, sign in, and enable the desktop app's CLI
integration. You must own a vault named `Workshop Setup Accelerator Users`; WSA stores each
accepted user's password in item `Account NNN`, field `confluent-cloud/password`.

```bash
op whoami
op vault get 'Workshop Setup Accelerator Users' >/dev/null
```

**Pick an email pattern** that routes plus-addressed mail to you, e.g.
`organizer+f1wp{N}@example.com`. You'll use this exact pattern everywhere below and in
[step 2](#2-create-the-workshop). Invite one user per account number:

```bash
for i in $(seq 1 5); do
  confluent iam user invitation create "organizer+f1wp${i}@example.com"
done
```

**Gmail OAuth (for automated invitation acceptance).** Create a Google OAuth client of
type **Desktop app** for the mailbox that receives the invitations, enable the Gmail API
and Google Sheets API, and save the client JSON as `~/.wsa/gmail-credentials.json` (WSA
uses a localhost callback on port 8085). Then export the pattern and accept the
invitations — accept one first, then the rest:

```bash
export WSA_EMAIL_PATTERN='organizer+f1wp{N}@example.com'

../workshop-setup-accelerator/bin/wsa accept-account-invitation -w wsa-spec-aws.yaml \
  --accounts 1 --gmail-credentials ~/.wsa/gmail-credentials.json
../workshop-setup-accelerator/bin/wsa accept-account-invitation -w wsa-spec-aws.yaml \
  --accounts 2-5 --gmail-credentials ~/.wsa/gmail-credentials.json
```

Invitations are matched within a three-day window; if acceptance fails after consuming an
invitation, reissue it for that address and retry. Confirm before building:

```bash
confluent iam user list -o json | grep -c 'organizer+f1wp'    # must equal attendee count
op read 'op://Workshop Setup Accelerator Users/Account 005/confluent-cloud/password' >/dev/null
```

Match your full alias — a broad `f1wp` search can count another organizer's users. If a
later build reports "No Confluent Cloud password in 1Password" for an account you're sure
was accepted, see [Troubleshooting](#troubleshooting).

### 1.5 watsonx Orchestrate

This optional part of the workshop is the only part outside Confluent/AWS — `wsa` and Terraform never touch IBM Cloud,
so this is manual. You need an IBM watsonx Orchestrate instance IBM has already approved
for this use, plus admin/owner access to the IBM Cloud account or resource group holding it.

**Don't hand out your own admin login** — it likely reaches other billed services. Create
**one dedicated, scoped-down login** and give every attendee that same login, the same way
they share one Confluent Cloud pool account. (Individually inviting attendees hits the same
acceptance latency as [1.4](#14-attendee-logins) and isn't worth it for read-only,
ephemeral use.)

1. **Invite one dedicated user** — a mailbox you control that isn't your personal IBMid
   (e.g. a shared alias like `f1-workshop@yourcompany.com`). IBM Cloud console →
   **Manage → Access (IAM) → Users → Invite users**. The web UI needs a real IBMid login,
   not an API key / Service ID.
2. **Scope it to only this instance.** IBM Cloud console → **Manage → Access (IAM) →
   Access groups** → create a group whose policy is limited to the specific watsonx
   Orchestrate service instance (that one resource, not "All resources"), with a role
   sufficient for Agent Builder (Editor, or your plan's non-admin working role). Add the
   invited user. If the shared login leaks, the blast radius is this one instance.
3. **Accept the invite.** Log into the shared mailbox once, open the IBM invite email, and
   set a password. That email + password is the login you'll hand out.
4. **Pre-import the race-feed tool, once, as the organizer** (before attendees arrive):
   - Point `docs/assets/orchestrate/f1-race-feed-openapi.json`'s `servers[0].url` at your
     live `f1-social-feed` endpoint (it ships with a placeholder).
   - Log in with the shared login, open watsonx Orchestrate → **Agent Builder → Tools →
     Add tool → Import → OpenAPI**, and upload that file. Choose the `get_race_feed`
     operation.

   This removes "Add the race-feed tool" from every attendee's Lab 5 — they attach the
   already-imported `get_race_feed` tool to their own agent instead of each re-importing
   the spec (which would leave the shared Tools list with one duplicate per attendee).
5. **Dry-run concurrent access.** Log in with the shared login from two browsers/devices at
   once and confirm both stay live — nothing here can verify that a shared IBMid supports
   concurrent sessions on your plan.
6. **Distribute the login** wherever attendees look for Lab 5 access (credential card,
   slide, verbal) — same trust model as a Wi-Fi password.
7. **After the workshop, revoke it** — remove the user from the Access group or reset its
   password, so it isn't live for the next workshop unless you intend to reuse it.

Two things matter once attendees are in Agent Builder, both covered in the pasteable
instructions ([orchestrate_social_agent.md](../demo-reference/orchestrate_social_agent.md),
mirrored in [HOSTED-WORKSHOP.md](../tracks/HOSTED-WORKSHOP.md) Lab 5): the `prefix` value is
a per-attendee substitution (not a literal), and each attendee should give their agent a
unique name since everyone shares one project.

### 1.6 Account dispenser (optional but recommended)

**Skip this if you're handing out credential cards directly** — the workshop works fine
without it. Set it up if you want attendees to self-claim an account and see their
credentials **in the browser** (one `/exec` link you hand out, no cards to distribute).

WSA 0.3.0's dispenser is an on-screen **Apps Script web app**. `create-workshop` populates
it automatically on every build, so adopting it is a one-time deploy:

1. **Deploy the web app.** In the sibling WSA checkout, follow
   `account-dispenser/webapp/SETUP-webapp.md` — create the inventory Sheet, paste in
   `WebApp.gs` + `Index.html`, and deploy **from a personal Google account** (Confluent
   Workspace blocks anonymous `Anyone` access, so a Confluent account can't publish a
   claimable page). You get back an `/exec` URL — the claim link.
2. **Point this repo at that Sheet.** Put its ID in the gitignored `wsa.env`:

   ```bash
   WSA_DISPENSER_SPREADSHEET_ID=<spreadsheet-id>
   ```

3. That's it. Every `create-workshop` writes the cards, then uploads the same rows into the
   Sheet's `AccountInventory` tab — the web app serves them live.

During the event, attendees open the `/exec` URL, enter name + email, and see their full
credential set on screen (including the RTCE MCP setup command). Re-claiming with the same
email returns the same account, so a refresh is safe. The email backup still works but is
best-effort — the web app avoids the Gmail `421` deferral that used to delay the email-only
flow.

---

## 2. Create the workshop

One command does the whole build — preflight, secrets, shared AWS layer, per-attendee
environments, and credential cards:

```bash
uv run create-workshop --attendees 5 --email-pattern 'organizer+f1wp{N}@example.com'
```

It prompts for anything you don't pass (attendee count, prefix, email pattern, missing
secrets, final confirmation), then validates the spec, builds, and writes the cards.

The **email pattern must contain `{N}`** and match the accepted users from
[1.4](#14-attendee-logins). WSA 0.3.0 reads it from `WSA_EMAIL_PATTERN` (the wrappers export
it for you) rather than the spec, so the committed spec stays reusable. A value you pass or
type is remembered in `credentials.env`; you can also set `WSA_EMAIL_PATTERN` (or
`WORKSHOP_EMAIL_PATTERN`) in `wsa.env` to skip the prompt.

Useful flags:

| Flag | Purpose |
|---|---|
| `--attendees N` | Create N attendee environments |
| `--prefix PREFIX` | Override the resource prefix |
| `--email-pattern PATTERN` | Override the accepted-user address pattern |
| `--concurrency N` | Parallel Terraform workers (default 10) |
| `--name NAME` | Name the credential-card directory |
| `--yes` | Skip prompts (missing values fail early) |
| `--force` | Allow a build when another live run exists — inspect first |

> **For a real build (~8+ attendees), add `--concurrency 2`.** WSA's default of 10 parallel
> Terraform runs drives enough Confluent Cloud API traffic to trigger `429 Too Many
> Requests` and `Compute pool or principal not found` errors. See
> [Troubleshooting](#troubleshooting).

**What it creates.** The shared layer holds the AWS network, one Postgres instance, an ECR
image, and shared Bedrock credentials. Each attendee gets a separate Confluent Cloud
environment, Kafka cluster, Schema Registry context, Flink pool, CDC connector, ECS
simulator, topics, models, connections, and scoped API keys. Terraform also creates a
**Global API key** per attendee service account (for RTCE's MCP interface and Lightning
Queries) when `TF_VAR_enable_rtce=true`; it does **not** enable RTCE on any topic —
attendees do that themselves in the Console during the RTCE lab, so there's nothing
topic-side for you to provision.

Every simulator starts at desired count 1 and loops after lap 60 until you stop the fleet or
tear down.

<details>
<summary>Rehearsing, or building by hand (power users)</summary>

`create-workshop` wraps the `workshop` subcommands, which wrap `wsa`. You rarely need them
directly, but they exist:

```bash
uv run workshop spec-validate     # wsa preflight: spec + local tooling (runs inside create-workshop)
uv run workshop build --accounts 10-11 --account-count 2 --prefix f1reh \
  --email-pattern 'organizer+f1wp{N}@example.com'   # shared layer, then N × attendee, then cards
```

`workshop build` is one command: it applies `terraform/aws-shared`, then each
`terraform/aws`, then writes every credential card from that run — no run-id to copy by
hand. For a two-account rehearsal, pick your highest-numbered non-attendee accounts and an
unused prefix so you never eat into attendee capacity. Don't confuse `spec-validate`
(before a build) with [`workshop validate`](#3-verify-the-build) (after one).

</details>

---

## 3. Verify the build

Run the API-key health check against **only this run's** cards before distributing anything:

```bash
uv run workshop validate --creds-glob 'runs/<run-name>/credentials/*.env'
```

Fix every reported environment first. Avoid the broad default glob when more than one run
exists locally — old run directories may point at infrastructure that's already destroyed.
(No AWS or Console login is needed for this check.)

---

## 4. Distribute credentials

The build writes two files per attendee under `runs/<run-name>/credentials/`:

```text
f1wp001.md     # the handout: Console URL, username, password, then env/workspace IDs
f1wp001.env    # API keys for the local support tools
```

Hand out both files, **or** use the [dispenser](#16-account-dispenser-optional-but-recommended)
and give attendees the `/exec` claim link instead.

Cards capture the password current at generation time. If WSA rotates an account password
afterward, regenerate the card before handing it out:

```bash
uv run workshop creds --csv <run>/build-output.csv --name <run-name> --resolve-op
```

**Keep a presenter account separate from attendee capacity.** If every provisioned account
will be distributed, provision one extra accepted user for yourself (or use the standalone
track). Never share a live presenter login with an attendee.

---

## 5. Run the workshop (day-of)

Attendees work only from the [hosted attendee walkthrough](../tracks/HOSTED-WORKSHOP.md).
Race timing and fleet operations are yours — keep them out of attendee instructions.

### Before attendees arrive

- Validate this run's exact credential-card set ([step 3](#3-verify-the-build)).
- Have the claim link or cards ready ([step 4](#4-distribute-credentials)).
- Open your presenter account, the Flink SQL workspace, and the Pit Wall.
- Confirm `car_telemetry`, `race_standings`, and `driver_race_history` are healthy.
- Confirm the shared Lab 5 race-feed URL and that the `get_race_feed` tool is already
  imported in Agent Builder's Tools list. If it's missing (new instance, prior teardown),
  import it now per [1.5](#15-watsonx-orchestrate) rather than leaving attendees to do
  it individually.

### Opening

Frame the workshop around one question: **can a pit wall turn live telemetry, standings,
and historical strategy into a useful decision while the race is still unfolding?** Remind
attendees they use a workshop login (not their own Confluent account) and that all SQL runs
in the browser workspace. Hand out the claim link so each attendee pulls up their own
credentials.

### Lab cues

| Lab | Presenter cue | Check before moving on |
|---|---|---|
| 1 | Open the assigned environment and Pit Wall | Each attendee sees the three source tables |
| 2 | Inspect telemetry, standings, CDC history, models, connections | History reaches 198 rows and live data advances |
| 3 | Build `car_state`; offer Granite forecasting only if time permits | `car_state` produces one 20-second window per lap; the optional forecast is stopped afterward |
| 4 | Create the streaming agent and `pit_decisions` | Decisions appear and the Pit Wall AI panel unlocks |
| 5 | Build the watsonx Orchestrate social agent | The imported tool returns the live race state |
| 6 | Inspect the important decision and recap the pipeline | Attendees can trace source data to the final output |

**Lab 3 note.** The required path uses `ML_DETECT_ANOMALIES`. The Granite `AI_FORECAST`
query is optional, uses a 20-step horizon (~20 laps ahead with one-per-lap windows), and
should be **stopped before Lab 4** so it doesn't occupy the shared compute pool.
`race_standings` reads from the latest offset, so a late Lab 3 job can miss earlier
standings versions — race timing is yours to coordinate; attendees never run fleet commands.

### Fleet controls

Simulators are already running after the build. To pause or resume the whole fleet:

```bash
uv run workshop stop-races      # scale every attendee simulator to 0 (Kafka + lab state preserved)
uv run workshop start-races     # scale every simulator back to 1
```

These match `river-racing` simulator clusters in `us-east-1` — a second workshop in the same
AWS account can fall within that scope. Use `--filter <prefix>` to operate a single
environment (a substring match; starts/stops the simulator without clearing state):

```bash
uv run workshop start-races --filter f1wp050
```

### Close

Revisit the completed path: telemetry and standings entered Kafka, Flink joined and scored
them, the streaming agent made a pit recommendation, and the social agent consumed the same
live context. Collect questions before you reset or tear down.

---

## 6. Reset between runs

Running the same environments for a second, back-to-back session? Reset clears lab state
without a full rebuild:

```bash
uv run workshop reset-races --creds-glob 'runs/<run-name>/credentials/*.env'
```

Reset stops matching simulators and waits for them to drain, stops attendee Flink
statements, drops `car_state` / `pit_decisions` / `pit_strategy_agent`, removes derived
topics and Schema Registry subjects, advances the `car_telemetry` low watermark to clear
source records, and **leaves every simulator stopped** (so the next Lab 3 can be submitted
before standings resume). `race_standings` is compacted and left for the next simulator to
overwrite.

Wait for `=== Reset complete ===`. If it reports `Reset INCOMPLETE`, keep the races stopped,
repair the named environment, and rerun. Use `--keep-source` only when existing source data
should remain.

---

## 7. Tear down

```bash
uv run teardown-workshop                       # tears down the newest run
uv run teardown-workshop --run-id <run-id>     # target a specific run when several exist
```

Teardown destroys attendee stacks and shared infrastructure, rotates attendee Console
passwords, clears the dispenser when configured, and offers to delete the local card
directory. The accepted Confluent user identities remain, ready for reuse.

> **Password rotation and dispenser clearing need `~/.wsa/gmail-credentials.json`.** Read the
> teardown output: infrastructure removal can succeed even when Google authorization fails,
> which would leave old passwords or Sheet rows **live**. `--yes` skips confirmation but does
> not supply missing credentials.

### Rebuild after teardown

Confirm teardown finished, accepted users still match your email pattern, 1Password has a
current password for every account, and no environment uses the planned prefix. Then rebuild
with [step 2](#2-create-the-workshop). Migrating an existing shared deployment to the
generated Postgres password is a separate one-time task — follow the
[targeted migration runbook](../../terraform/aws-shared/POSTGRES-PASSWORD-MIGRATION.md).

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `No valid credential sources found` on the `aws-shared` phase | Wrong shell — AWS session lives in the shell's env, not the repo. Build from the shell where `aws sts get-caller-identity` succeeds ([1.1](#11-tools-and-access)). |
| Spec refused at load time | Stale WSA binary. `cd ../workshop-setup-accelerator && make build` and confirm `bin/wsa --version` ≥ 0.3.0 ([1.2](#12-build-the-wsa-binary)). |
| `terraform plan` fails reading a Confluent user | A pending invitation. Every attendee invite must be **accepted** before the build ([1.4](#14-attendee-logins)). |
| "No Confluent Cloud password in 1Password" for an accepted account | The item may exist but lack the `confluent-cloud` section WSA reads (distinct from the item's built-in login password). Compare against a working account's item structure rather than assuming the invitation never completed. |
| `429 Too Many Requests` / `Compute pool or principal not found` at scale | WSA's default concurrency (10) overwhelms the Confluent Cloud API at full attendee count. Rebuild with `--concurrency 2`, and resume the partially-failed build rather than restarting from scratch. |
| Old passwords or dispenser rows still live after teardown | Google authorization failed during teardown. Re-run with `~/.wsa/gmail-credentials.json` available ([7](#7-tear-down)). |

Behavior that must stay fixed is in [CONSTRAINTS.md](../maintainers/CONSTRAINTS.md).

---

## Quick command reference

```bash
uv run create-workshop --attendees 5 --email-pattern 'organizer+f1wp{N}@example.com'
uv run workshop validate    --creds-glob 'runs/<run-name>/credentials/*.env'
uv run workshop start-races
uv run workshop stop-races
uv run workshop reset-races --creds-glob 'runs/<run-name>/credentials/*.env'
uv run teardown-workshop    --run-id <run-id>
```

## Related references

- [Hosted attendee walkthrough](../tracks/HOSTED-WORKSHOP.md) — what attendees follow.
- [Self-service walkthrough](../tracks/SELF-SERVICE.md) — attendees on their own Confluent accounts.
- [Standalone AWS walkthrough](../tracks/STANDALONE-DEMO.md) — the full deployment, solo.
- [Constraints](../maintainers/CONSTRAINTS.md) — workshop behavior that must stay fixed.
