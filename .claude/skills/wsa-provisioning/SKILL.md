---
name: wsa-provisioning
description: How this repo drives the wsa CLI (workshop-setup-accelerator) for organizer provisioning — binary discovery, the wsa-spec-aws.yaml contract, account_count vs --attendees, Terraform path contract, secrets, the dispenser upload, and the clean/teardown gotchas that can silently leave attendee passwords live. Load when running or debugging `workshop build`/`clean`/`spec-validate` or editing wsa-spec-aws.yaml.
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
- **Terraform contract:** `shared_infra_path: terraform/aws-shared/`,
  `terraform_path: terraform/aws/`. Every `credentials:` field with
  `source: terraform` must match a flat root `output` in
  `terraform/aws/outputs.tf` by name.
- **Secrets:** Terraform inputs (Confluent + Bedrock keys) are plain `.env`/shell
  `TF_VAR_*` exports — not the TMM 1Password vault. But `op` **is** in
  `tools_required`, because the attendee Console passwords live in wsa's
  `Workshop Setup Accelerator Users` vault (see "Attendee Console access" in the
  `f1-credentials` skill).
- **Dispenser:** attendees can claim via the Google Form/Sheet and self-serve
  `uv run f1-onboard` their claim-email values into a local `credentials.env`,
  or an instructor can run
  `uv run workshop creds --csv <run>/build-output.csv --name <name>` and hand
  out `runs/<name>/credentials/<prefix>.{env,md}` directly — same downstream
  tools either way. The upload itself is automatic: `build` calls
  `_upload_dispenser` right after the cards (the order the RTCE column depends
  on). It no-ops silently without `WSA_DISPENSER_SPREADSHEET_ID`, skips loudly
  without the OAuth client JSON, and never fails the build. `--no-dispenser-upload`
  opts out.
- **Clean:** `wsa clean -w wsa-spec-aws.yaml` — pass `--no-password-reset
  --no-dispenser-clear` if this run never used the dispenser/Gmail reset.
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
