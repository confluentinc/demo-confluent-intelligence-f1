# Solo-Track Simplification Plan

Date: July 30, 2026
Branch: `workshop-transform` | Comparison baseline: `origin/initial-codebase`

Consolidates two independent analyses (the former `STANDALONE-SIMPLIFICATION-PLAN.md`
and `STANDALONE-SIMPLIFICATION-PLAN-REVISED.md`, both superseded by this file).
Where they disagreed, the code decided; every claim below carries the file:line it
was verified at. Claims without a citation are decisions, not findings.

## Goal

Make the three tracks feel like single auto-configuring commands:

```text
First look:        uv run selfservice up
Full architecture: uv run deploy
Instructor event:  uv run workshop build
```

No hand-copied paths, no run-ids, no flag archaeology. WSA workshop behavior stays
byte-identical. IBM MQ, dbt, Tableflow, and Genie stay retired.

## Decision: fix in place, do not rewrite

Rebuilding WSA integration on top of `initial-codebase` was considered and rejected
on measurement:

- `initial-codebase` is the **pre-retirement** tree. It still carries `dbt/` (9 files),
  `terraform/modules/mq/` (4), `terraform/modules/tableflow/` (3), `terraform/core/` (4),
  `docs/SETUP-GENIE.md`, `docs/SETUP-TABLEFLOW.md`. Starting there means re-doing the
  retirement first.
- The transform added **6,711 insertions across 62 files**: `terraform/aws-shared`,
  `terraform/self-service`, `modules/llm`, pitwall, social_feed, social_feed_rtce,
  sql_shell, onboard, creds, validate, six lab guides, the spec.
- `race_standings` went from 1 → 6 references in `datagen/simulator.py`, which now
  resolves the registered key subject as primitive-or-record (`simulator.py:59,80`).
  A wrong key encoding is a **silent** zero-row temporal join. That work is done and
  tested; rebuilding it risks a failure mode that does not announce itself.
- Decisively: **no repo-local Python shells out to `wsa`** — every mention is a
  docstring (`scripts/workshop/__init__.py:3-6`, `onboard.py:1-9`, `validate.py:8`).
  The `<path-to-this-repo>` / `<run-id>` ugliness is an *empty seam*, not a property
  of this branch. A rewrite reproduces it identically, because `wsa` is an external
  Go CLI in a sibling repo. Item 0 closes it in ~100 lines.

## Scope boundary — state-dependent, not permanent

`wsa` reads `terraform/aws/`, `terraform/aws-shared/`, `terraform/modules/`
(transitively), and stages `data/` + `datagen/`. It runs **no repo Python**.

The boundary depends on which mode `wsa-spec-aws.yaml` is in:

| Spec state | Rule for `terraform/`, `datagen/`, `data/` |
|---|---|
| **local-copy mode** (`terraform_repo`/`terraform_ref` omitted) — the current uncommitted working-tree state | **Hard block.** Local uncommitted edits flow straight into a `wsa build`. |
| **git-ref mode** (as at HEAD `c6e5e3e`) | Additive edits allowed — optional inputs whose defaults preserve today's behavior, proven by a diff check. `wsa` builds from the pushed ref. |

Assume hard block until the spec goes back to a git ref. Record anything blocked by
this as *blocked pending spec state* rather than silently dropping it.

| Class | Paths |
|---|---|
| **Blocked (current state)** | `terraform/aws/`, `terraform/aws-shared/`, `terraform/modules/`, `datagen/`, `data/`, `wsa-spec-aws.yaml` |
| **Safe** | `deploy.py`, `scripts/reset.py`, `scripts/common/destroy.py`, `scripts/selfservice/`, `scripts/workshop/`, `terraform/self-service/`, docs, `.gitignore`, `pyproject.toml` |
| **Shared, additive only** | `scripts/workshop/sql_shell.py`, `scripts/pitwall/`, `scripts/common/{credentials,login_checks,terraform_runner}.py` |

`reset.py` and `destroy.py` are **Safe** despite looking shared: `wsa` keeps its state
in its own run dir — confirmed `wsa-output/c0hy1/terraform/{aws,aws-shared,self-service}/terraform.tfstate`,
and `internal/output/output.go:16,38` resolves `wsa-output/` relative to CWD — while
`reset.py:428` requires state in the main checkout, which only `uv run deploy` writes.
Reset already exits "No Terraform state" on a workshop machine; workshops tear down
with `wsa clean`.

**Do not touch `wsa-spec-aws.yaml`.** Its 2-line uncommitted edit is in-flight user work.

## Decisions

- **Standalone keeps CDC + ECS.** It is the full-architecture demo: the Debezium
  connector is a visible Stream Lineage node and the ECS feed survives closing the
  laptop. The fast path already exists as `uv run selfservice up`.
- **Deterministic identity-derived prefixes, not random suffixes.** See item 8.
- **`--automated` and `--with-labs` stay orthogonal.** `--automated` means "don't
  prompt"; conflating it with "build the labs" denies a bare environment to smoke
  tests. `uv run deploy --automated --with-labs` is the ready-to-demo one-liner.
- **No root `Walkthrough.md`.** `docs/STANDALONE-DEMO.md` is the walkthrough
  (added `343d8a8`, linked at `README.md:86`). Expand it; a symlink or second copy
  only adds drift.
- **Keep `setup-mcp`** (item 19) — it registers the Confluent MCP server with a
  coding agent, which `f1-social-feed-rtce` does not do. **Drop `setup-rtce`**: it
  never existed, and its own spec required checking an unknown API contract first.
- **Fold the instructor fan-out under `workshop`** so solo and organizer surfaces are
  separate namespaces (item 20). Entry points went 6 → 14; a solo user should not
  have to sort past `start-all-races`.

---

## Item 0 — Close the WSA seam (the headline simplification)

The organizer flow today is two hand-wired commands with three paths to fill in:

```bash
op run --env-file=.env.tpl -- ./bin/wsa build -w <path-to-this-repo>/wsa-spec-aws.yaml --accounts 1-20 --concurrency 4
uv run workshop creds --csv <wsa-repo>/wsa-output/<run-id>/build-output.csv --name <name>
```

Every input is auto-discoverable, verified against `wsa`'s source:

- `wsa-output/` resolves **relative to CWD** (`internal/output/output.go:16,38`) — which
  is why it already exists in this repo.
- each run dir gets `build-report.json` carrying `run_id`, `work_dir`, `clone_dir`
  (`output.go:31-35`) — machine-readable, no mtime guessing or path parsing.
- `build-output.csv` sits in that run dir (`output.go:20,53-56`). Neither existing run
  has one because neither build completed — the location is confirmed from source, not
  inferred from the filesystem.
- cleaned runs are renamed with a `-cleaned` suffix (`output.go:26`), so discovery skips them.
- `-w/--workshop-spec` is required (`cmd/wsa/main.go:168,173`) but the spec is *in this
  repo*, so a wrapper supplies it.

Add `scripts/workshop/wsa.py` behind existing subcommands:

```bash
uv run workshop build --accounts 1-20      # runs wsa, then writes cards, prints where they landed
uv run workshop validate                   # wsa validate against the local spec
uv run workshop clean                      # wsa clean; pass --no-password-reset --no-dispenser-clear through
```

It must: locate the sibling checkout (`../workshop-setup-accelerator/bin/wsa`,
overridable by `$WSA_HOME`, with an actionable error if absent); pass this repo's
spec; stream wsa's output rather than buffering it; on success resolve the newest
non-`-cleaned` `build-report.json` and feed its CSV into the existing
`workshop creds` without a hand-copied run-id.

Both of `workshop creds`' arguments are `required=True` (`scripts/workshop/creds.py:255-256`),
so auto-resolving only `--csv` still leaves a prompt for the one thing this item is
removing. **Default `--name` to the `run_id` from `build-report.json`** — it is already
in hand from the same file the CSV came from, and `--name` only chooses the output
directory `runs/<name>/credentials/` (`creds.py:225`), so a run-id is a perfectly good
value. Keep `--name` as an override for organizers who want a human label
(`runs/london-june/`), and leave `creds.py`'s own `required=True` alone — the wrapper
supplies both arguments, so direct `workshop creds` callers keep today's behaviour.

Keep raw `wsa` fully supported and documented — this wraps it, it does not replace it.
`op run --env-file=` stays the caller's business; do not swallow it.

---

## P0 — Prevent damage and credential leaks

### 1. `wsa-output/` is unignored and holds live secrets

1.4 GB, untracked, and `git check-ignore` matches nothing. It contains
`terraform/aws-shared/terraform.tfstate` and `terraform/aws/terraform.tfstate`, which
carry the literal Postgres password and every Kafka/SR/Flink secret. One `git add -A`
commits them. Because wsa writes relative to CWD, this is where it lands by design.

→ add `wsa-output/` to `.gitignore`.

Verified clean: `git log --all --diff-filter=A -- 'wsa-output/**'` is empty, and no
`credentials.env` or `runs/**/credentials/*` appears in history. This is prevention,
not remediation — **no history rewrite needed**.

### 2. Destroy must stop within a dependency chain

`destroy.py:186-198` records the failure and continues, so a failed `aws` destroy still
tears down `aws-shared`, removing the Postgres, simulator image, and shared outputs that
the surviving ECS service and CDC connector depend on.

⚠️ **Not a one-line `break`.** `destroy.py:146` flattens selected groups into a single
tier list (`envs_to_destroy = [t for _n, tiers, _d in selected for t in tiers]`), so
group boundaries are gone by the time the loop runs — a naive `break` would also skip
an independently-selected `self-service` teardown, which is exactly wrong. Restructure
to iterate **groups outer, tiers inner**: break the inner loop on failure, continue the
outer.

Sequence this **first, as its own change**, before item 8 rewires
`export_selfservice_tf_env(creds)` at `destroy.py:181-183`.

Acceptance: a mocked `aws` failure proves no shared destroy command starts, while a
separately-selected self-service teardown still completes.

### 3. Unbreak test discovery and prune dead ignore rules

`.gitignore:137-141` allowlists `tests/` so narrowly that `tests/test_sql_shell.py`
exists on disk and is invisible to git (confirmed: absent from `git ls-files`).
`:68` `confluent-mcp.env`, `:78` `generated/`, `:108` `scripts/.race-task-arn`, and
`:143-146` the Node block are written by files this branch deleted — but item 19
restores `setup-mcp`, so **re-audit these after item 19 lands** rather than pruning
blind. Keep whatever the restored command writes.

→ admit `tests/test_*.py` while keeping credential fixtures ignored; add `wsa-output/`.
Must land with or before item 21.

---

## P1 — Race control, reset, teardown inputs

### 4. Deployment-scoped race control

`scale_simulator` / `wait_for_drain` (`reset.py:282-334`) already read *this
deployment's* ECS names from `terraform/aws` state — they are just gated behind
`if args.with_labs`. Extract them plus `create_lab_objects` (`reset.py:362-385`) into
`scripts/common/simulator_control.py`; add `scripts/race_control.py` and a
`race = "scripts.race_control:main"` entry point, gated on state exactly as
`reset.py:428-435` does.

```bash
uv run race start | stop | restart | status
```

Must require local `terraform/aws/terraform.tfstate`, read `ecs_cluster_name` /
`ecs_service_name` from it, update exactly that service, wait for stop and restart
transitions, show desired vs running counts in `status`, and return nonzero on AWS
errors or timeouts. Must not import the instructor fan-out. Remove the standalone
docs' references to `start-all-races` / `stop-all-races`.

### 5. Plain `reset` does not do what it promises, and its advice is a footgun

The docstring (`reset.py:5-8`) promises it clears the source topics. It does not:
`truncate_topics` runs while the always-on ECS service keeps producing.
`running_simulator_count` (`:150-176`) then counts **every** cluster matching the
account-wide `river-racing` filter and tells you (`:494-495`) to run
`uv run stop-all-races` — which scales every attendee's feed to zero.

- Promote stop → truncate to unconditional; **delete** `running_simulator_count`.
- **Do not auto-restart on plain reset.** `race_standings` has no `scan.startup.mode`
  override (`terraform/modules/topics/main.tf:87-139`), so it starts from `latest`.
  Restarting before the user has hand-written LAB 3 means their first laps have no
  version to join and `car_state` silently loses them. Leave it stopped; print
  `uv run race start`.
- `--keep-source` skips the truncation *and* the stop.
- Check `scale_simulator`'s return value (`:471` discards the bool).

### 6. Reset must wait, and must fail honestly

`drop_flink_objects` (`:244-279`) POSTs and returns, unlike `create_lab_objects`, which
waits and documents why (`:362-366,385`). Under `--with-labs` the CREATEs race the
DROPs → "table already exists". Several failures print warnings and the command still
prints `Reset complete` and exits 0.

→ wait for every `DROP TABLE` / `DROP AGENT` to reach a terminal success phase before
deleting topics or SR subjects; return **nonzero** on any failed drop, topic op,
permission error, or timeout. Reconcile `LAB_DROPS` order (`:55-57`) with
`docs/STANDALONE-DEMO.md:387-389` — a comment fix, since `delete_flink_statements`
runs first.

### 7. Reset refuses self-service, but the docs send every self-service user to it

`docs/SELF-SERVICE.md:100-104` says to use it; `reset.py:428-435` exits 1 and names
that exact scenario.

→ detect `terraform/self-service/terraform.tfstate`, resolve the self-service card,
skip every ECS step, and refuse to clear source data while a local `f1-race` is
producing (explicit `--force` to override). With `--with-labs`, rebuild the labs then
print the exact `uv run f1-race` command. Fix the doc in the same pass (item 23).

### 8. Per-track deployment config + deterministic unique prefixes

Two problems, one fix.

**Collision.** `deploy.py:143-145` prompts `"Attendee prefix (alphanumeric, max 12
chars, e.g. demo or your initials)"` defaulting to `creds.get("TF_VAR_prefix","")` —
empty on a fresh checkout, so the example nudges everyone to the same value, and after
any run it inherits whatever the *other* track last wrote (`cli.py:72,94,130` do the
same). Observed result: two live environments both named `RIVER-RACING-PROD-ENV`
(`env-j767vm` from `terraform/aws`, `env-dp6w81` from `terraform/self-service`).

**Teardown.** One root `credentials.env` supplies both tracks' Terraform inputs, so
`destroy.py:181-183` rebuilds self-service's TF vars from whatever prefix is in that
file. Deploy standalone after a self-service run and self-service teardown targets the
wrong names.

→ **Default the prefix deterministically from identity**, not randomly and not from an
example: sanitized `$USER` (lowercase alnum, truncated to 8), falling back to a short
hash of the Confluent owner email when `$USER` is generic (`root`, `ubuntu`,
`ec2-user`, empty). Two people get different names automatically; the *same* person
gets the same name on every rerun, so `race`, `reset`, `destroy`, and screen-shares stay
stable and readable. Suffix the track (`<id>` standalone, `<id>s` self-service) so the
two tracks in one checkout never collide.

→ **Persist each track's resolved inputs in its own run directory** —
`runs/standalone/deployment.env`, `runs/selfservice/deployment.env` — recording base
prefix, resolved prefix, resolved card path, and pacing. Destroy and re-runs read from
there. `F1_CARD` stays the root-level active-card selector.

Rules: generate only when the track has no state and no saved metadata; reuse on every
rerun; never switch under existing state; validate alnum and ≤12 chars *before* any
cloud call — `cli.py --automated` has no validation today. Automated apply and destroy
must verify the saved resolved prefix matches existing state before proceeding.

If destroy needs a secret unrecoverable from state or card, save a track-scoped
reference or prompt for it — do not borrow the other track's value.

### 8b. Move the identifier to the END of every resource name

Today the identifier sits mid-name: `local.name_prefix = "RIVER-RACING-${var.prefix}"`
(`terraform/aws/main.tf:13`, `terraform/self-service/main.tf:16`), composed into
`"${local.name_prefix}-ENV"` / `"-CLUSTER"` → `RIVER-RACING-kevin-ENV`. Wanted:
identifier last → `RIVER-RACING-ENV-kevin`, `RIVER-RACING-CLUSTER-kevin`.

Scope is larger than the two locals, in two layers:

1. **Root composition** — 6 call sites each in `terraform/aws/main.tf:20,27,38,39,114,115`
   and `terraform/self-service/main.tf:23,30,41,42,117,118`. Mechanical: replace the
   `name_prefix` local with a `name_suffix` local and compose
   `"RIVER-RACING-ENV-${var.prefix}"`.
2. **Module-side composition** — `name_prefix` is also *passed into* `modules/{cluster,
   flink,postgres}`, which append to it, e.g. `modules/cluster/main.tf:31`:
   `"${var.name_prefix}-app-${random_id.suffix.hex}"`. This layer needs a real
   restructure, not a rename: the module receives **one pre-composed string** and appends
   to it, so whatever it builds puts the identifier before the appended part. Passing
   `name_prefix = var.prefix` yields `kevin-app-<hex>` (identifier *first*); passing
   `name_prefix = "RIVER-RACING"` drops the identifier from the name entirely. Getting
   identifier-last inside modules means passing the base and the identifier as **two**
   variables and composing `"${var.name_base}-app-${hex}-${var.prefix}"` at each site.

So there is a cheap partial (env + cluster names read correctly, service-account and
API-key names still mid-name) and a complete version that touches shared modules.

⚠️ **These are not cosmetic names. They are the Flink catalog and database.**

`terraform/modules/llm/main.tf:68` builds
``CREATE MODEL `${var.environment_name}`.`${var.cluster_name}`.`llm_textgen_model` ``
(same at `:89` for the embedding model), and `:71-72` / `:92-93` set
`sql.current-catalog = var.environment_name`, `sql.current-database = var.cluster_name`
— both fed from the root call sites catalogued above (`aws/main.tf:114-115`,
`self-service/main.tf:117-118`). **Environment display name = Flink catalog. Cluster
display name = Flink database.** Renaming them renames the catalog and database every
lab statement and both `CREATE MODEL`s resolve against.

Two consequences that make this fresh-deploy-only:

- **Existing credential cards go stale.** `scripts/reset.py:352-353` maps
  `tf["environment_name"] → F1_CATALOG` and `tf["cluster_name"] → F1_DATABASE`, and
  `wsa-spec-aws.yaml:126,132` pull those same root outputs into the dispenser CSV.
  `scripts/workshop/sql_shell.py:65-66` reads them straight from the card, so any card
  issued before the rename points `f1-sql` at a catalog that no longer exists. Cards
  generated *after* the rename are correct automatically — nothing hardcodes the names,
  which is why fresh deploys are clean and in-place renames are not. Failure mode is a
  clean exit, not a crash: `sql_shell.py:68-69` catches `KeyError` and tells the user to
  regenerate, though a *stale* value passes that check and fails later at the API.
  (The card keys are built dynamically — `creds.py:_write_env` emits `f"F1_{k.upper()}"`,
  and `creds.py:121-122,144-146` map `environment_name → catalog`, `cluster_name →
  database`. Grepping for the literal `F1_CATALOG` finds only `reset.py`; the wsa and
  `deploy.py` paths go through that mapping. Nothing there needs editing for this item —
  the new names flow through from the Terraform outputs on their own.)
- **The `CREATE MODEL` statements are inside the renamed strings.** Changing
  `var.environment_name` changes the `statement` attribute of the
  `confluent_flink_statement` resources, which forces them to be recreated — against a
  cluster where `llm_textgen_model` already exists. Expect a "model already exists"
  failure on an in-place rename unless the models are dropped first.

⚠️ **Two further blockers, both must clear first.**

- **`terraform/aws/` and `terraform/modules/` are blocked** while the spec is in
  local-copy mode. `terraform/self-service/` is Safe and can move immediately — but
  doing only that makes the two solo tracks *inconsistent*, which is worse than
  consistent-and-ugly. Recommend doing all of it in one pass once the spec is back on a
  git ref, and treating the module layer as part of that pass.
- **Confirm this is not a destructive rename.** Run `terraform plan` against an existing
  deployment before applying. If `display_name` on `confluent_kafka_cluster` forces
  replacement rather than updating in place, applying this **destroys the cluster and
  every topic and message in it**. Do not assume either way from the provider docs —
  plan it.

**Do not rename `var.prefix` / `TF_VAR_prefix`.** After this item the identifier is
positionally a *suffix*, so the variable name reads wrong — leave it anyway. `wsa`
injects by that exact name and `wsa-spec-aws.yaml:57` sets `prefix: "f1wp{NNN}"`;
renaming the variable silently breaks wsa injection. Change only the *composition*.
Same call for item 8's `F1_BASE_PREFIX` / `F1_RESOLVED_PREFIX` card keys — if they are
introduced after this item lands, pick neutral names once; if before, leave them.

WSA impact: attendee names become `RIVER-RACING-ENV-f1wp001`, and their cards' catalog
/database follow automatically. The instructor fan-out is unaffected —
`scripts/instructor/_common.py:14` and `datagen.tf:18` filter the separate lowercased
ECS pattern (`river-racing-${prefix}-simulator`), which this item leaves alone. Also
update the **comment** at `wsa-spec-aws.yaml:57` documenting the old pattern when the
rename lands (that file is otherwise hands-off). Do this between workshops, never
against a live one.

### 9. Restore the ready-to-demo path: `--with-labs`

On `initial-codebase`, automated deploy built the full Flink graph; now a presenter
still hand-submits LAB 3 and LAB 4.

→ add `--with-labs` to `uv run deploy` and `uv run selfservice up`, reusing the
extracted `create_lab_objects` from item 4. Create in dependency order
`car_state` → `pit_strategy_agent` → `pit_decisions`, wait until each is usable, **then**
start the race — the ordering `reset.py:498-506` already gets right. (Calling
`reset --with-labs` after a fresh deploy works but is semantically odd: it drops and
truncates nothing.)

Self-service `--with-labs` finishes with 198 verified history rows and all three
objects, and prints the exact `uv run f1-race` command rather than starting a hidden
background process.

---

## P2 — Cost, collisions, friction

### 10. Two people running `uv run deploy` in one AWS account collide, hard

`deploy.py:39` pins `SHARED_PREFIX = "f1-workshop"` and
`terraform/aws-shared/datagen.tf:24-26` names the repo `"${lower(var.prefix)}-simulator"`
— an account-global `f1-workshop-simulator`. The second person's ECR create fails
`RepositoryAlreadyExistsException`. Unlike the environment-name collision (Confluent
permits duplicate display names — that is confusion), **this one is a hard failure**.

→ derive it from the resolved prefix (`f1-<prefix>`), overridable by env var. Fixable in
`deploy.py` alone; `TF_VAR_prefix` is already overridden at `:208`.

⚠️ On a machine with existing `aws-shared` state this forces one ECR repo recreate +
image rebuild. Warn before migrating so it does not surprise someone mid-demo-prep.

### 11. A `t3.large` + 30 GB gp3 running 24/7 to hold 198 rows

`postgres_instance_type` is already a variable defaulting to `t3.large`
(`terraform/aws-shared/variables.tf:24-28`), and `deploy.py:208-211` already overrides
`attendee_count`.

→ add `"TF_VAR_postgres_instance_type": "t3.small"` there. One line, zero Terraform
edits, ~$60 → ~$15/mo. (`initial-codebase` used `t3.micro`; `t3.small`'s 2 GB is safer
with a logical-replication slot, and it is a one-word knob either way.) The workshop
keeps `t3.large`. Document the override and allow an env setting to replace it. This is
a **cost** win, not a time win.

### 12. Standalone forces a Confluent user login it does not need

`deploy.py:91` calls `ensure_confluent_login` before even asking whether to generate
keys — but Terraform's Confluent provider authenticates with
`TF_VAR_confluent_cloud_api_key/_secret`, and the CLI session is only needed by
`generate_confluent_api_keys`. A user with an existing OrganizationAdmin key is forced
through login anyway, and `_prompt_and_save_login` writes their Confluent **password**
in plaintext to `credentials.env` even though `confluent login --save` already uses the
OS credential store where supported.

→ move the check inside the key-generation branch. Change the call sites (`deploy.py`,
`cli.py`), not `login_checks.py`, to keep the shared helper's workshop behavior
identical. Update prerequisites so an existing API key does not imply a CLI login.

### 13. `--automated` crashes on bad pacing and never persists good pacing

`deploy.py:190` calls `int(seconds_per_lap)` unguarded — the interactive path validates
at `:159`, the automated path does not, so a stale `TF_VAR_seconds_per_lap=fast`
produces a raw traceback *after* every prereq check. `set_key` happens only in the
interactive branch (`:176`), so the
`export TF_VAR_seconds_per_lap=15; uv run deploy --automated` recipe at
`docs/STANDALONE-DEMO.md:394-397` applies but does not stick.

→ validate (integer, ≥10) and persist in both paths, into the track metadata from
item 8, before any Terraform/Docker/AWS/Confluent work runs.

### 14. `f1-race`: kill the dead warmup, guard pacing

`race.py:33` has no minimum; `--seconds-per-lap 1` → `readings_per_lap = 1 // 2 = 0`
(`datagen/simulator.py:150,218`) → no telemetry ever, while the log cheerfully prints
lap progress.

→ apply the `MIN_SECONDS_PER_LAP = 10` guard from `deploy.py:51`; honor the persisted
pacing from item 8; restore the `--20` shorthand from the old `start_race.py:29-33` as
an alias for `--seconds-per-lap 20`.

→ set `PRE_RACE_WARMUP_LAPS=0`. `_run_warmup_laps` (`datagen/simulator.py:140-167`)
produces 4 telemetry windows at `lap=0` and **no standings**, and LAB 3's first CTE is
an *inner* temporal join against `race_standings` — so on a cold start those rows have
no version to join and never reach `ML_DETECT_ANOMALIES`. Even if they did, 4 windows
against `minTrainingSize=20` changes nothing, and at every supported pacing real race
data supplies 20 windows long before the lap-32 anomaly (2 windows/lap at 20 s/lap →
20 by lap 10). Cost: ~140 s at 20 s/lap, ~300 s at workshop pacing.

`PRE_RACE_WARMUP_LAPS` is env-driven (`datagen/config.py:33`, default `4`), so `f1-race`
can set `0` with **zero blocked-file edits**. Standalone's ECS path takes it from
`terraform/aws/datagen.tf` — **blocked pending spec state** (see Scope boundary), so
standalone keeps the warmup for now. The stale rationale lives in a blocked docstring;
correct it in `CLAUDE.md` and the docs instead (item 26).

---

## P3 — Papercuts

### 15. Teardown leaves a card file behind, then every tool deadlocks

`destroy.py:194` and `cli.py:249` clear the `F1_CARD` *pointer* but never delete the
card *file*. With both tracks used on one machine, tearing one down leaves two cards
and no pointer → `resolve_card` hits `credentials.py:116-121` and hard-exits **every**
tool with "Multiple credential cards found", though exactly one live environment exists.

→ delete that track's `.env` and `.md` cards alongside `clear_active_card`, and remove
the self-service `.seeded` marker. Only after a **successful** destroy. Fix in
`destroy.py`/`cli.py`, not `resolve_card` — that file every attendee depends on.

### 16. `driver_race_history` can be silently empty — two compounding bugs

(a) The seeder treats `RUNNING` as success (`cli.py:203-211`; the shared wait returns at
`RUNNING` per `sql_shell.py:99`) and writes `.seeded`, so a bounded `INSERT` that later
fails is never retried. (b) `.seeded` survives `uv run destroy` — only `selfservice down`
unlinks it (`cli.py:247-248`), while `destroy.py:55` advertises `self-service` as a
teardown group. Sequence: `selfservice up` → `uv run destroy` → `selfservice up` prints
"already seeded" over an empty table. LAB 2's `COUNT(*)` returns 0 and LAB 4's history
join returns nothing, **with no error anywhere**.

→ wait for `COMPLETED`; treat timeout and every other terminal phase as failure; verify
198 rows; write the marker **tied to the current environment ID** only after
verification; unlink it in `destroy.py` too.

### 17. Pit Wall swallows auth failures forever

`consumer.py:143-146` justifies suppression for `UNKNOWN_TOPIC` but catches *every*
error including `SASL_AUTHENTICATION_FAILED`, and `app.py:27` sets INFO so the
`logger.debug` never prints. A stale card yields a dashboard that loads, renders empty,
reports `live: false`, and never says why.

→ keep `UNKNOWN_TOPIC` quiet until LAB 3/4 creates those topics; warn once per distinct
error code for auth, unreachable brokers, authorization, and deserialization failures.
Expose the last connection error in the health endpoint or page state.

### 18. `f1-sql` quality of life

No `import readline` anywhere in `scripts/` — `input()` at `sql_shell.py:208` gets no
arrow-key editing in a shell users live in for four labs. → `import readline` (stdlib,
additive). Add `--file <path>` reusing the same statement classification, comment
handling, wait logic, and error reporting as `--exec` (`reset.py:369-380` had to
reimplement exactly this).

Fix the remediation strings: `sql_shell.py:69`, `consumer.py:74`, `consumer.py:88` all
say "Regenerate it with `uv run workshop creds`" — an organizer command. Name every
path (`uv run deploy`, `selfservice up`, `f1-onboard`, `workshop creds`) so the
workshop message stays correct while solo users get useful instructions.

### 19. Restore `setup-mcp` (card-based, Claude + Codex)

Keep `f1-social-feed-rtce` (LAB 5) and restore the MCP registration it does not cover:

```bash
uv run setup-mcp                      # resolved card
uv run setup-mcp --creds <card>
uv run setup-mcp --client claude|codex|both
```

The old script (`origin/initial-codebase:scripts/setup_mcp.py`) requires Node ≥ 24
(`:107-130`), warns when the ABI has no prebuilt `@confluentinc/kafka-javascript` binary
(`:121-125`), npm-installs the MCP package locally (`:185-205`), then registers via
`claude mcp remove ... -s local` + `claude mcp add` (`:229-254`).

Two changes: read credentials from `resolve_card()` instead of the deleted
`terraform/core/terraform.tfstate` (`:217`), and add Codex alongside Claude Code. Keep
the Node/ABI preflight — it is real. Must replace only its own registration and stay
safe to rerun. Re-audit the `.gitignore` entries from item 3 after this lands.

⚠️ Verify Codex's MCP config format from current docs before implementing; do not infer
it. The Claude Code path is already proven by the old script.

### 20. Separate solo and organizer namespaces

Entry points went 6 → 14. Move the instructor fan-out under the organizer namespace —
`workshop start-races` / `workshop stop-races` — keeping `start-all-races` /
`stop-all-races` as thin deprecated aliases so WSA docs and muscle memory keep working.
`scripts/instructor/` behavior stays untouched; this is naming only.

### 21. Test and tooling hygiene

- Add `pytest` to the dev group (`pyproject.toml:67-69` is `dev = ["ruff"]`). For **one**
  command to cover everything the group also needs `confluent-kafka[avro]`, `httpx`, and
  `fastavro` (today passed as `--with` flags for `datagen/tests/`). Either add all four
  and document `uv run pytest`, or keep two documented commands — do not claim one
  command covers the suite while `datagen/tests/` still needs flags.
- Depends on item 3, or `tests/test_sql_shell.py` stays invisible.
- Retry Terraform only on transient errors (`terraform_runner.py:17-18`: 3 attempts ×
  30 s adds 60 s of dead time to every permanent auth/validation failure). Classify
  network/service-transient as retryable; return auth, validation, missing-variable,
  and resource-collision immediately. Preserve state after every failure.
- Replace `scripts/setup.sh` / `scripts/teardown.sh` with thin wrappers around
  `uv run deploy` / `uv run destroy`, or delete them after updating every reference —
  they bypass the orchestration that injects shared outputs and teardown guards.

### 22. Add a small CI check

```bash
uv run pytest
uv run ruff check .
terraform fmt -check -recursive terraform    # read-only, safe under the block
```

No apply. `terraform validate` needs initialized providers — add it only when CI can
cache or install them predictably. Nothing in `.github/` runs any of these today, which
is what lets duplication drift silently.

New regression coverage: destroy stop-on-chain-failure (and that an independent group
still proceeds), scoped ECS selection and wait behavior, reset ordering and failed
drops, self-service reset selection, prefix derivation and reuse, cross-track metadata
isolation, dead-card cleanup (two cards, no pointer), seed completion and marker
cleanup, automated pacing validation, Pit Wall error classification, SQL `--file`
execution, and item 0's run-dir discovery.

---

## P4 — Docs

### 23. `docs/SELF-SERVICE.md`

`:100-104` the reset contradiction (pairs with item 7). `:47` claims the prefix defaults
to `solo` when `cli.py:130` inherits from `credentials.env` first (item 8 makes the doc
true). `:27-31` lists the Confluent CLI as required when `selfservice up` only touches
it inside the key-generation branch (item 12).

### 24. Self-service users are routed into labs describing infrastructure they lack

`docs/SELF-SERVICE.md:90-98` sends them to instructor-led labs; `labs/README.md:38-42`
asserts ECS Fargate + Postgres CDC; `LAB2.md:31` calls `driver_race_history` "CDC from
the shared Postgres" and **JSON** when self-service creates it as **Avro**
(`terraform/self-service/main.tf:164`); `LAB2.md:53-56` walks an `f1-postgres-cdc`
connector that does not exist there.

→ **additive** per-track notes placed next to the existing text. The instructor-led CDC
text stays correct for WSA and standalone — do not rewrite workshop behavior.

### 25. Stale references

- `demo-reference/enrichment_anomaly.sql:2` still says "Deployed via DBT as a
  streaming_table materialization". `dbt/` is gone, and users `cat` and `--exec` this
  file (`docs/STANDALONE-DEMO.md:199`, LAB 3).
- `docs/USE-CASE.md:32` says laps `34–57`; the race is 60 (`STANDALONE-DEMO.md:278`
  says `34–60`).

### 26. Warmup rationale

Correct it in `CLAUDE.md`'s gotcha section (the `datagen/simulator.py:140-147` docstring
is blocked) and note that `f1-race` now skips it while standalone's ECS path does not,
with the reason.

### 27. Expand `docs/STANDALONE-DEMO.md` and reorder the README

`README.md:35-80` puts the `wsa` / sibling-checkout section first and longest; the two
solo tracks come third and fourth and fork with **no recommendation**. A newcomer's
first content is provisioning machinery that does not apply to them.

→ keep the comparison table at `:14-23`, lead with "run it yourself", recommend
`uv run selfservice up` for a first look, and move `wsa` behind a link — now much
shorter thanks to item 0.

`docs/STANDALONE-DEMO.md` must cover: `uv run deploy`, `--with-labs`, `--automated`;
scoped race status/stop/start/restart/logs/pacing; manual and prebuilt LAB 3/4 flows;
`uv run setup-mcp`; LAB 5 via `f1-social-feed-rtce`; reset behavior for both solo
tracks; safe teardown and the item-10 ECR migration warning. No MQ, dbt, Tableflow, or
Genie.

---

## Deferred

Real findings, not available actions while `terraform/` is blocked:

- **Postgres exposure** — SG opens 5432 **and 22** to `0.0.0.0/0`
  (`modules/postgres/main.tf:16-47`) with a literal password in
  `aws-shared/outputs.tf:28-31`. Mechanism when the block lifts: backward-compatible
  optional module inputs defaulting to today's behavior, so standalone gets a generated
  password and no SSH ingress while WSA is unchanged. Deserves its own security change
  with a rollout plan.
- **30 GB gp3 volume** — `modules/postgres/main.tf:62-65`.
- **~110 near-identical lines** between `terraform/aws/main.tf:10-122` and
  `terraform/self-service/main.tf:13-125`, including the whole `attendee_credentials`
  output. Extracting `modules/confluent-base` means editing wsa's `terraform_path`.
- **Standalone ECS warmup** — `terraform/aws/datagen.tf` (item 14).
- **Region parameterization** — pinned in 10+ places and not free anyway:
  `modules/llm/main.tf:32` uses the `us.`-prefixed inference profile.
- **`.terraform.lock.hcl` tracking policy.**

## Verify live, do not infer

1. `driver_race_history` on the AWS path has no explicit `scan.startup.mode`.
   `docs/technical-discoveries.md:20` implies Flink tables default to `latest` (→
   `COUNT(*)` = 0), but `docs/STANDALONE-DEMO.md:173-178` documents the count climbing
   to 198. Connector-created tables may not behave like Flink-DDL tables. One live
   `SELECT COUNT(*)` settles it; then fix whichever doc is wrong.
2. `SHOW CREATE TABLE` for `car_telemetry`, `race_standings`, and `driver_race_history`
   before any table or topic schema change.
3. Codex's MCP config format (item 19) from current docs.

---

## Acceptance criteria

1. `uv run workshop build --accounts 1-N` provisions and writes credential cards with
   **no** hand-typed spec path, run-id, or CSV path; raw `wsa` still works.
2. `wsa-output/` cannot be staged by `git add -A`; `git status --porcelain` shows no
   `wsa-output/`, `credentials.env`, or `runs/**/credentials/*`.
3. A failed `terraform/aws` destroy prevents `aws-shared` from being destroyed, while an
   independently-selected `self-service` teardown still completes.
4. Two people running `uv run deploy` in one AWS account get different ECR repositories
   and different resolved prefixes, without either typing a prefix.
5. The same person re-running `uv run deploy` gets the **same** resolved prefix.
6. Standalone and self-service coexist in one checkout with distinct Confluent
   environment names, and neither overwrites the other's destroy inputs.
6b. (When item 8b lands) every Confluent resource name ends with the identifier —
    `RIVER-RACING-ENV-kevin`, not `RIVER-RACING-kevin-ENV` — across both solo tracks and
    the shared modules. Verified on a **fresh** deploy, not an in-place rename: a newly
    generated card's `F1_CATALOG`/`F1_DATABASE` carry the new names and `uv run f1-sql`
    connects with them, and both `CREATE MODEL` statements succeeded in the renamed
    catalog/database. Separately, a `terraform plan` against a pre-rename deployment was
    inspected and its blast radius recorded in the item (forced cluster replacement
    and/or `CREATE MODEL` recreation), whether or not that deployment is ever migrated.
7. `uv run race stop` changes exactly one ECS service; workshop services untouched.
8. `uv run reset` works on both solo tracks and exits **nonzero** on partial cleanup.
9. Plain standalone reset clears source data, leaves its service stopped, and prints
   `uv run race start`.
10. `uv run reset --with-labs` rebuilds LAB 3/4 before restarting the scoped race.
11. Self-service reset works after `uv run f1-race` stops, and refuses while it runs.
12. Destroying either track removes its dead cards; destroying self-service also removes
    its seed marker; a failed destroy removes neither.
13. `uv run deploy --automated --with-labs` finishes with `car_state`,
    `pit_strategy_agent`, `pit_decisions` present and a fresh scoped race running. Bare
    `--automated` deliberately does **not** build labs.
14. `uv run selfservice up --with-labs` finishes with 198 verified rows and the three
    lab objects.
15. A default `uv run f1-race` reaches lap 1 without the ~140 s warmup.
16. Pit Wall reports authentication and broker failures instead of staying silently empty.
17. `uv run setup-mcp` works from either solo card, for Claude and Codex.
18. Self-service guides describe Flink seeding and Avro while WSA and standalone retain
    CDC instructions.
19. `docs/STANDALONE-DEMO.md` documents every supported solo command; the README leads
    with a recommended solo route.
20. `uv run pytest` discovers all tracked tests and passes.
21. WSA safety diff clean: `git diff --stat terraform/ datagen/ data/` is **empty** —
    *except* for item **8b**, the one item gated on the spec returning to git-ref mode
    (it edits `terraform/aws/main.tf`, `terraform/self-service/main.tf`, and
    `terraform/modules/`). Every other item in this plan is deliberately zero-Terraform —
    item 11, for instance, is a `TF_VAR` override in `deploy.py`, not a `.tf` edit. While
    the spec is in local-copy mode this criterion is "empty, full stop"; once 8b lands,
    restate it as "no diff outside the files 8b names." And
    `git diff wsa-spec-aws.yaml` still shows only the user's own 2-line
    `terraform_repo`/`terraform_ref` change (it is `M` at session start, so it will never
    be clean — check it separately, do not fold it in).

## Implementation order

Short, independently verifiable changes:

1. **Item 0** — the wsa seam wrapper. Highest simplicity-per-line, touches nothing else.
2. Items 1, 3 — `wsa-output/` ignore, test discovery.
3. Item 2 — destroy group ordering (alone, before item 8 touches the same file).
4. Items 15, 16 — dead-card and seed-marker cleanup.
5. Item 4, then 5–7 — scoped race control, then reset reworked around it.
6. Item 8, then 13 — prefix derivation, per-track metadata, pacing validation.
   (Item **8b**, identifier-at-end renaming, is gated on the spec returning to git-ref
   mode *and* a `terraform plan` proving the rename is non-destructive. Do it as its own
   pass across both roots and the shared modules — not piecemeal.)
7. Item 9 — `--with-labs` on both tracks.
8. Items 14, 11, 12 — local warmup/pacing, Postgres size, login gating.
9. Items 17, 18 — Pit Wall errors, `f1-sql` history + `--file`.
10. Item 19, then re-audit item 3's ignore rules — `setup-mcp`.
11. Item 20 — namespace split.
12. Items 23–27 — docs, README reorder.
13. Items 21, 22 — retry classification, CI.

Before each change, capture the current config or output it depends on. Run the item-21
WSA safety diff before any commit. Do not commit, push, or edit the user's
`wsa-spec-aws.yaml` work unless explicitly requested.
