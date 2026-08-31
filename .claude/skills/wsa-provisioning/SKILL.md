---
name: wsa-provisioning
description: How this repo drives the wsa CLI (workshop-setup-accelerator) for organizer provisioning — binary discovery, the wsa >= 0.3.0 phases-model wsa-spec-aws.yaml contract, wsa_version, account_count vs --attendees, the WSA_EMAIL_PATTERN convention, the phase→TF_VAR contract, secrets, the on-screen web-app dispenser, and the clean/teardown gotchas that can silently leave attendee passwords live. Load when running or debugging `workshop build`/`clean`/`spec-validate` or editing wsa-spec-aws.yaml.
---

# WSA (organizer provisioning)

Provisioning and teardown are owned by `wsa` (confluentinc/workshop-setup-accelerator),
not a repo-local orchestrator. It still lives in a **sibling checkout**
(`workshop-setup-accelerator/`, per that repo's `ONBOARDING.md` "Local layout"), but
you no longer invoke it from there: `uv run workshop spec-validate|build|clean`
(`scripts/workshop/wsa.py`) finds the binary and injects `-w <this-repo>/wsa-spec-aws.yaml`.

- **Binary discovery:** four candidates in order — `$WSA_HOME/bin/wsa`, a sibling
  `../workshop-setup-accelerator/bin/wsa`, a sibling of the **main** checkout
  (for linked-worktree use), and one on `$PATH`. Set `$WSA_HOME` if yours is
  elsewhere.
- **One command, not two:** `workshop build` runs `wsa build` and then feeds that
  run's `build-output.csv` into `workshop creds` in-process, so the run-id is never
  copied by hand. `workshop clean` resolves the newest non-cleaned run from
  `wsa-output/` instead of taking a `--run-id`.
- **wsa >= 0.3.0 (phases model):** the spec declares `wsa_version: ">=0.3.0"` and
  wsa **refuses to load a spec outside that range** and **strict-decodes** it (any
  unknown/legacy key fails by name). This is a hard cutover — the pre-0.3.0 keys
  `terraform_path`, `shared_infra_path`, `email_pattern`, and `extra_dirs` are gone.
  The sibling checkout's source is already 0.3.0, but its **built `bin/wsa` may be
  stale** — an organizer must `cd ../workshop-setup-accelerator && make build`
  (then `./bin/wsa --version` → `0.3.0`) before the migrated spec will load.
- **Spec:** `wsa-spec-aws.yaml` (repo root) — `account_count: 5`, but only as the
  interactive default. `create-workshop --attendees N` is authoritative: it writes
  `account_count: N` into the derived spec. The shared Postgres host is fixed at 105
  replication slots (95 supported accounts plus 10 spare); the exported
  `TF_VAR_attendee_count=N` remains only for shared-infra compatibility. No file
  needs editing to grow a workshop within that supported range. The old
  ceiling that refused `--attendees > account_count` is gone; the real guard is
  `_check_console_accounts`, which verifies each Console password exists in 1Password
  and now bails after 3 misses. `account_count` reaches wsa only as its `>= 1` check,
  the "(N accounts)" banner, and the default account list `--accounts` supersedes.
- **Email pattern is operator config, not a spec field:** 0.3.0 reads the attendee
  login pattern from `WSA_EMAIL_PATTERN` (env / the repo's `wsa.env`), never the spec.
  We keep our own `WORKSHOP_EMAIL_PATTERN` convention: `resolve_email_pattern`
  (`scripts/workshop/wsa.py`) resolves it — `--email-pattern` override → the env
  (`WORKSHOP_EMAIL_PATTERN`, then wsa's `WSA_EMAIL_PATTERN`) → `credentials.env` — and
  every wrapper (`build`, `spec-validate`, `create-workshop`) **exports it as
  `WSA_EMAIL_PATTERN` before invoking wsa**, so wsa's own run-dir snapshot carries it
  and `clean` matches automatically. Unset + non-interactive is a hard error
  ("email pattern is not set"); interactive prompts. Zero operator-facing change.
- **Terraform contract (phases):** the spec's `phases:` list is applied in
  declaration order and destroyed in reverse — `shared` (`scope: once`,
  `terraform/aws-shared/`) then `accounts` (`scope: per_account`, `terraform/aws/`).
  wsa injects each phase's outputs into later phases as `TF_VAR_<phase>_<output>`, so
  the once-phase **must stay named `shared`** to feed `terraform/aws`'s `variable
  "shared_*"` blocks as `TF_VAR_shared_<output>` — that is the one cross-tier contract,
  and it's why the migration needs **zero Terraform changes**. Separately, every
  `credentials:` field with `source: terraform` must match a flat root `output` in
  `terraform/aws/outputs.tf` by name.
- **Secrets:** Terraform inputs (Confluent + Bedrock keys) are plain `.env`/shell
  `TF_VAR_*` exports — not the TMM 1Password vault. But `op` **is** in
  `tools_required`, because the attendee Console passwords live in wsa's
  `Workshop Setup Accelerator Users` vault (see "Attendee Console access" in the
  `f1-credentials` skill).
- **Dispenser (on-screen web app, 0.3.0):** the primary attendee claim path is now
  wsa's **Apps Script web app** (`account-dispenser/webapp/` in the wsa repo — we do
  **not** copy it here). Attendees open its `/exec` URL, enter name + email, and see
  their credentials **in the browser** (including the RTCE MCP command, grouped by the
  `" / "` slash header via `buildCredentialGroups_` in `WebApp.gs`); claim email is now
  best-effort backup. This fixes the Gmail `421` deferral that delayed the old
  email-only flow. Our pipeline is unchanged: the web app reads the **same**
  `AccountInventory` tab with the same `Provider / Field` slash headers that
  `wsa dispenser-upload` already writes, so `_upload_dispenser` and the RTCE column need
  no code change. Adopting it is **ops**: deploy the Apps Script under a **personal
  Google account** (Confluent Workspace blocks anonymous `Anyone` web-app access),
  container-bound to the inventory Sheet whose ID is this repo's
  `WSA_DISPENSER_SPREADSHEET_ID`, then hand out the `/exec` URL. Attendees can still
  self-serve `uv run f1-onboard` their claim-email values into a local `credentials.env`,
  or an instructor can run
  `uv run workshop creds --csv <run>/build-output.csv --name <name>` and hand
  out `runs/<name>/credentials/<prefix>.{env,md}` directly — same downstream
  tools either way. The upload itself is automatic: `build` calls
  `_upload_dispenser` right after the cards (the order the RTCE column depends
  on). It no-ops silently without `WSA_DISPENSER_SPREADSHEET_ID`, skips loudly
  without the OAuth client JSON, and never fails the build. `--no-dispenser-upload`
  opts out.
- **Clean:** `wsa clean -w wsa-spec-aws.yaml` tears down phases in reverse order
  (`accounts` → `shared`). 0.3.0 replaced `--accounts-only` / `--shared-only` with
  `--phases <name>`; our wrapper **keeps** the `--accounts-only` / `--shared-only`
  flag names (`scripts/workshop/wsa.py`) and translates each to `--phases accounts` /
  `--phases shared` internally, so `teardown-workshop` and its namespace need no
  change. Pass `--no-password-reset --no-dispenser-clear` if this run never used the
  dispenser/Gmail reset.
  `workshop clean` decides that for you, and the reason matters: **wsa only warns
  when the Google OAuth client is missing** (`main.go:1432,1507`), so a teardown
  that reports success can leave every attendee's password live and their
  credentials sitting in the dispenser sheet. `find_google_credentials` resolves
  one JSON for both flags (`--google-credentials`, `$WSA_GOOGLE_CREDENTIALS`,
  `~/.wsa/gmail-credentials.json`, wsa checkout root — an explicit path that
  doesn't exist is fatal, never a fallback), and `dispenser_configured` treats a
  missing or `<placeholder>` `WSA_DISPENSER_SPREADSHEET_ID` as "no dispenser".
  Whatever can't run is skipped *explicitly*, with the consequence named on
  stderr. That env var belongs in **this** repo's gitignored `wsa.env`: wsa reads
  `wsa.env` from its CWD, which the wrapper pins here.
