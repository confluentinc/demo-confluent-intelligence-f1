# Organizer Guide — F1 Pit Wall AI Workshop

**Running the instructor-led workshop? You're in the right place.**

Direct attendees to the [hosted attendee walkthrough](../tracks/HOSTED-WORKSHOP.md) for their instructions.

### How it works

To set up the workshop, you provision **shared AWS infrastructure once**, plus **one isolated Confluent Cloud environment per attendee**. Workshop Setup Accelerator (WSA) is a separate project that's required for setup - it helps to organize and vend individual user accounts for the workshop. Each user will have all of the credentials they need provided by WSA. 

## 1. Prerequisites (one-time)

Do these once. After that, running another workshop is just [step 2](#2-create-the-workshop) onward.

### 1.1 Tools and access

You need Confluent Cloud **`OrganizationAdmin`** (required for user invitations and the Global API keys that Real-Time Context Engine uses) and **AWS credentials for `us-east-1` with Bedrock access**.

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

### 1.2 Build the WSA binary

Provisioning is owned by the **Workshop Setup Accelerator (`wsa`)**, which this repo drives through `uv run` wrappers. Clone it **next to this repo** and build the binary:

```bash
git clone git@github.com:confluentinc/workshop-setup-accelerator.git ../workshop-setup-accelerator
make -C ../workshop-setup-accelerator build
```

This repo requires **WSA ≥ 0.3.0** (the spec sets `wsa_version: ">=0.3.0"` and WSA
strict-decodes it). Confirm the built binary is current:

```bash
../workshop-setup-accelerator/bin/wsa --version
```

If you have an older checkout, `git pull` and rerun `make build` — a stale binary refuses
the spec at load time. The wrappers find the binary in `$WSA_HOME`, the sibling checkout,
or `$PATH`; set `WSA_HOME` if yours lives elsewhere.

### 1.3 Workshop secrets

Copy the example and fill in the five required values:

```bash
cp credentials.env.example credentials.env
chmod 600 credentials.env
```

`credentials.env` is gitignored. Both build and teardown read these values.

### 1.4 Attendee logins

Attendees share a **pool of accepted Confluent Cloud users**. Set them up now; invitation acceptance has latency, so don't leave it to the day of.

**1Password.** Install the 1Password CLI, sign in, and enable the desktop app's CLI integration. You must own a vault named `Workshop Setup Accelerator Users`; WSA stores each accepted user's password in item `Account NNN`, field `confluent-cloud/password`.

```bash
op whoami
op vault get 'Workshop Setup Accelerator Users' >/dev/null
```

**Pick an email pattern** that routes plus-addressed mail to you, e.g. `organizer+f1wp{N}@example.com`. You'll use this exact pattern everywhere below and in [step 2](#2-create-the-workshop). Invite one user per account number:

```bash
for i in $(seq 1 5); do
  confluent iam user invitation create "organizer+f1wp${i}@example.com"
done
```

**Gmail OAuth (for automated invitation acceptance).** Create a Google OAuth client of type **Desktop app** for the mailbox that receives the invitations, enable the Gmail API and Google Sheets API, and save the client JSON as `~/.wsa/gmail-credentials.json` (WSA uses a localhost callback on port 8085). Then export the pattern and accept the invitations — accept one first, then the rest:

```bash
export WSA_EMAIL_PATTERN='organizer+f1wp{N}@example.com'

../workshop-setup-accelerator/bin/wsa accept-account-invitation -w wsa-spec-aws.yaml \
  --accounts 1 --gmail-credentials ~/.wsa/gmail-credentials.json
../workshop-setup-accelerator/bin/wsa accept-account-invitation -w wsa-spec-aws.yaml \
  --accounts 2-5 --gmail-credentials ~/.wsa/gmail-credentials.json
```

Invitations are matched within a three-day window; if acceptance fails after consuming an invitation, reissue it for that address and retry. Confirm before building:

```bash
confluent iam user list -o json | grep -c 'organizer+f1wp'    # must equal attendee count
op read 'op://Workshop Setup Accelerator Users/Account 005/confluent-cloud/password' >/dev/null
```

Match your full alias — a broad `f1wp` search can count another organizer's users. If a later build reports "No Confluent Cloud password in 1Password" for an account you're sure was accepted, see [Troubleshooting](#troubleshooting).

### 1.5 Account dispenser

WSA 0.3.0's dispenser is an on-screen **Apps Script web app**. `create-workshop` populates it automatically on every build, so adopting it is a one-time deploy:

1. **Deploy the web app.** In the sibling WSA checkout, follow `account-dispenser/webapp/SETUP-webapp.md` — create the inventory Sheet, paste in `WebApp.gs` + `Index.html`, and deploy **from a personal Google account** (Confluent Workspace blocks anonymous `Anyone` access, so a Confluent account can't publish a claimable page). You get back an `/exec` URL — the claim link.
2. **Point this repo at that Sheet.** Put its ID in the gitignored `wsa.env`:

   ```bash
   WSA_DISPENSER_SPREADSHEET_ID=<spreadsheet-id>
   ```

3. That's it. Every `create-workshop` writes the cards, then curates the run's `build-output.csv` down to four attendee columns and uploads those rows into the Sheet's `AccountInventory` tab — the web app serves them live.

During the event, attendees open the `/exec` URL, enter name + email, and see four fields on screen — **Console URL**, **Console Username**, **Console Password**, and one paste-ready **Env File** block. They log in to the Console with the first three, and save the Env File block verbatim as a local `credentials.env` (no command — just paste it into a file). That block carries every API key (including the RTCE Global key), so `uv run setup-rtce` works straight afterward with no separate MCP-command field to copy. Re-claiming with the same email returns the same account, so a refresh is safe. The email backup still works but is best-effort — the web app avoids the Gmail `421` deferral that used to delay the email-only flow.

---

## 2. Create the workshop

One command does the whole build — preflight, secrets, shared AWS layer, per-attendee environments, and credential cards:

```bash
uv run create-workshop --attendees 5 --email-pattern 'organizer+f1wp{N}@example.com'
```

It prompts for anything you don't pass (attendee count, prefix, email pattern, missing secrets, final confirmation), then validates the spec, builds, and writes the cards.

The **email pattern must contain `{N}`** and match the accepted users from [1.4](#14-attendee-logins). WSA 0.3.0 reads it from `WSA_EMAIL_PATTERN` (the wrappers export it for you) rather than the spec, so the committed spec stays reusable. A value you pass or type is remembered in `credentials.env`; you can also set `WSA_EMAIL_PATTERN` (or `WORKSHOP_EMAIL_PATTERN`) in `wsa.env` to skip the prompt.

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

Every simulator is provisioned **stopped** (desired count 0) so pre-provisioned accounts don't stream data before the workshop — you start the fleet on the day (see [Run the workshop](#4-run-the-workshop-day-of)). Once started, each loops after lap 60 until you stop the fleet or tear down.

<details>
<summary>Rehearsing, or building by hand (power users)</summary>

`create-workshop` wraps the `workshop` subcommands, which wrap `wsa`. You rarely need them directly, but they exist:

```bash
uv run workshop spec-validate     # wsa preflight: spec + local tooling (runs inside create-workshop)
uv run workshop build --accounts 10-11 --account-count 2 --prefix f1reh \
  --email-pattern 'organizer+f1wp{N}@example.com'   # shared layer, then N × attendee, then cards
```

</details>

---

## 3. Verify the build

Run the API-key health check against **only this run's** cards before distributing anything:

```bash
uv run workshop validate --creds-glob 'runs/<run-name>/credentials/*.env'
```

---

## 4. Run the workshop (day-of)

Attendees work only from the [hosted attendee walkthrough](../tracks/HOSTED-WORKSHOP.md). Race timing and fleet operations are yours — keep them out of attendee instructions.

### Fleet controls

Simulators are provisioned **stopped** — no data flows until you start them. Start the fleet on the day, ideally once attendees have their LAB 3 statement RUNNING (so `car_state` catches every lap from the first — `race_standings` is read from `latest`). After that, stop/start pauses and resumes the whole fleet:

```bash
uv run workshop start-races     # scale every attendee simulator to 1 (start the race feed)
uv run workshop stop-races      # scale every attendee simulator to 0 (Kafka + lab state preserved)
```

These match `river-racing` simulator clusters in `us-east-1` — a second workshop in the same AWS account can fall within that scope. Use `--filter <prefix>` to operate a single environment (a substring match; starts/stops the simulator without clearing state):

```bash
uv run workshop start-races --filter f1wp050
```

## 5. Tear down

```bash
uv run teardown-workshop                       # tears down the newest run
uv run teardown-workshop --run-id <run-id>     # target a specific run when several exist
```

Teardown destroys attendee stacks and shared infrastructure, rotates attendee Console passwords, clears the dispenser when configured, and offers to delete the local card directory. The accepted Confluent user identities remain, ready for reuse.

> **Password rotation and dispenser clearing need `~/.wsa/gmail-credentials.json`.** Read the
> teardown output: infrastructure removal can succeed even when Google authorization fails,
> which would leave old passwords or Sheet rows **live**. `--yes` skips confirmation but does
> not supply missing credentials.

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
