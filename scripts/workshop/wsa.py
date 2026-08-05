"""`workshop build` / `spec-validate` / `clean` — wrappers around the `wsa` CLI.

Provisioning is owned by `wsa` (confluentinc/workshop-setup-accelerator), a Go
CLI in a sibling checkout. That seam used to cost the organizer two hand-wired
commands with three paths to fill in:

    ./bin/wsa build -w <path-to-this-repo>/wsa-spec-aws.yaml --accounts 1-20
    uv run workshop creds --csv <wsa-repo>/wsa-output/<run-id>/build-output.csv --name <name>

Every one of those inputs is discoverable, so this module discovers them:

  * the `wsa` binary — sibling checkout, `$WSA_HOME`, or `$PATH` (see `find_wsa`)
  * the spec — `wsa-spec-aws.yaml` lives in *this* repo, so we pass it ourselves
  * the run directory — `wsa` writes `wsa-output/<run-id>/build-report.json`
    carrying `run_id` (`internal/output/output.go:16,18,31-35`), so the CSV path
    and the `--name` for the cards both come out of one machine-readable file.
    No mtime guessing, no path parsing, no copied run-id.

`wsa` resolves `wsa-output/` relative to its **CWD**, so every subprocess here
runs with `cwd=` this repo's root: the run directories land next to the spec
they were built from (that is why `wsa-output/` is gitignored here), and the
`wsa.env` / `.env` files `wsa` loads on startup are this repo's, not the
sibling checkout's. A fully successful build also *writes* `WSA_RUN_ID` back
into `./wsa.env` (`main.go:1055-1060`) — harmless, and this module never reads
it, preferring the build report (see `clean`).

This wraps `wsa`; it does not replace it. Anything beyond the flags exposed
below is still a direct `wsa` invocation. `build` and `clean` collect the
`TF_VAR_*` secrets themselves (`ensure_secrets`), because Terraform needs them
for both the apply and the destroy. Exported values win over `credentials.env`,
so a 1Password wrapper still works — write your own template, there is none in
this repo:

    op run --env-file=<your-template> -- uv run workshop build --accounts 1-20

Note the subcommand name: `workshop validate` is the *attendee card* health
check (`validate.py`), which long predates this module. `wsa`'s spec-and-
prerequisites check is therefore `workshop spec-validate` — different input
(the spec, before a build) and different failure mode (bad spec or missing
tooling, not a dead API key).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from scripts.common.terraform import get_project_root
from scripts.workshop import creds as creds_mod
from scripts.workshop.secrets import ensure_secrets

# This repo's wsa spec — `-w/--workshop-spec` is required by every wsa
# subcommand that reads it (cmd/wsa/main.go), and the answer is always this.
SPEC_FILE = "wsa-spec-aws.yaml"

# Spec overrides (create-workshop's attendee count and interactive prefix prompt,
# or `build --prefix` / `--account-count`) can't be passed to `wsa` on the command
# line — `wsa` reads terraform_vars.prefix and account_count from the spec and has
# no --var flag. So we write a derived spec next to the committed one, with only
# those fields changed, and point `-w` at it. It lives at the repo root (same
# directory as SPEC_FILE) so every relative path in the spec — terraform_path,
# shared_infra_path, stage_paths — resolves identically.
# Gitignored; `wsa clean` uses the copy staged inside the run dir, not this file.
GENERATED_SPEC = ".wsa-spec-generated.yaml"

# Sibling checkout layout, per workshop-setup-accelerator's ONBOARDING.md.
SIBLING_DIR = "workshop-setup-accelerator"
BINARY_SUBPATH = Path("bin") / "wsa"

# Names fixed by wsa's internal/output/output.go (lines 16, 18, 20, 26).
OUTPUT_DIR = "wsa-output"
BUILD_REPORT = "build-report.json"
BUILD_CSV = "build-output.csv"
CLEANED_SUFFIX = "-cleaned"


@dataclass(frozen=True)
class Run:
    """One `wsa-output/<run-id>/` directory that holds a parseable build report."""

    path: Path
    run_id: str
    csv: Path
    # When the build finished, for ordering. Falls back through started_at to
    # the report's mtime, so a run is always orderable.
    finished: datetime


def _wsa_candidates(root: Path) -> list[Path]:
    """Ordered places the `wsa` binary might be, most explicit first.

    `$WSA_HOME` wins so a non-standard layout needs no code change. The plain
    sibling lookup (`../workshop-setup-accelerator`) is the documented layout,
    but it breaks inside a git worktree — a worktree's parent is
    `.claude/worktrees/`, not the directory the sibling checkout sits in — so
    the main checkout's parent is tried as well. `$PATH` last, for anyone who
    symlinked the binary into `~/.local/bin`.
    """
    candidates: list[Path] = []

    def add(path: Path | None) -> None:
        if path is not None and path not in candidates:
            candidates.append(path)

    home = os.environ.get("WSA_HOME", "").strip()
    if home:
        add(Path(home).expanduser() / BINARY_SUBPATH)

    add(root.parent / SIBLING_DIR / BINARY_SUBPATH)
    main_checkout = _main_checkout(root)
    if main_checkout is not None:
        add(main_checkout.parent / SIBLING_DIR / BINARY_SUBPATH)

    on_path = shutil.which("wsa")
    if on_path:
        add(Path(on_path))

    return candidates


def _main_checkout(root: Path) -> Path | None:
    """Root of the main checkout when `root` is a linked git worktree.

    `--git-common-dir` points at the main checkout's `.git` even from a
    worktree, so its parent is the directory a sibling `workshop-setup-
    accelerator/` would live next to. Returns None outside a git repo (or on
    a git too old for `--path-format`); callers just lose one candidate.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    common = proc.stdout.strip()
    if not common:
        return None
    return Path(common).parent


def find_wsa(root: Path) -> Path:
    """First executable `wsa` among `_wsa_candidates`, or exit with the fix.

    The error names every path tried, because "wsa not found" with no paths is
    the least actionable message possible when the whole point of the sibling
    layout is that it is implicit.
    """
    candidates = _wsa_candidates(root)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate

    tried = "\n".join(f"    {c}" for c in candidates) or "    (none)"
    raise SystemExit(
        "Could not find the `wsa` binary. Tried:\n"
        f"{tried}\n\n"
        "Fix it one of these ways:\n"
        f"  * clone confluentinc/workshop-setup-accelerator next to this repo "
        f"({root.parent / SIBLING_DIR}) and build it (`make build` there)\n"
        "  * point WSA_HOME at an existing checkout: "
        "export WSA_HOME=~/code/workshop-setup-accelerator\n"
        "  * put `wsa` on your PATH"
    )


def _spec_path(root: Path) -> Path:
    spec = root / SPEC_FILE
    if not spec.exists():
        raise SystemExit(f"wsa spec not found: {spec} (are you running inside this repo?)")
    return spec


def _spec_terraform_prefix(root: Path) -> str:
    """The committed spec's ``terraform_vars.prefix`` template (e.g. ``f1wp{NNN}``)."""
    spec = yaml.safe_load(_spec_path(root).read_text()) or {}
    return str((spec.get("terraform_vars") or {}).get("prefix", ""))


def _spec_account_count(root: Path) -> int | None:
    """The committed spec's ``account_count``, or None when it isn't set."""
    spec = yaml.safe_load(_spec_path(root).read_text()) or {}
    raw = spec.get("account_count")
    return None if raw is None else int(raw)


def _export_attendee_count(root: Path, args: argparse.Namespace) -> None:
    """Pin ``TF_VAR_attendee_count`` before wsa's shared-infra apply.

    ``terraform/aws-shared`` sizes Postgres with ``max_replication_slots =
    attendee_count + 10``, and that number is baked into the EC2 ``user_data``.
    Leaving it unset does not merely under-provision — it silently *changes* the
    value, because Terraform then falls back to its own ``default = 50``. A
    workshop created with ``--attendees 2`` (slots 12) followed by a bare
    ``workshop build`` (slots 60) rewrites ``user_data``, which is an in-place
    update that stops and starts the instance. That is exactly how a live
    Postgres went unreachable mid-build.

    Resolution order, most authoritative first:

    1. An existing ``TF_VAR_attendee_count`` — ``create-workshop`` exports the
       real ``--attendees`` value before calling here, and it wins.
    2. ``--account-count``, when passed directly to ``workshop build``.
    3. The committed spec's ``account_count``.

    Deliberately NOT derived from ``--accounts``: that is a slice of the
    workshop (``1-2`` while running a 50-person event), not its size, and
    sizing Postgres from it would under-provision every CDC slot.
    """
    var = "TF_VAR_attendee_count"
    if os.environ.get(var, "").strip():
        return

    count = getattr(args, "account_count", None) or _spec_account_count(root)
    if count is None:
        # No spec value and none passed: leave it unset rather than invent one.
        # Terraform's default applies, and at least it applies consistently.
        return
    os.environ[var] = str(count)


def _derive_spec(root: Path, prefix: str = "", account_count: int | None = None) -> Path:
    """Write a copy of the committed spec with the given fields overridden.

    ONE function for every override, deliberately: it reads the committed spec and
    writes the whole file, so two separate deriving functions would each clobber
    the other's field. Only the fields passed here change; the file is dumped with
    keys in their original order (comments are dropped — harmless, wsa parses it as
    data). Returned path is what the build's ``-w`` points at.
    """
    spec = yaml.safe_load(_spec_path(root).read_text()) or {}
    if prefix:
        spec.setdefault("terraform_vars", {})["prefix"] = prefix
    if account_count is not None:
        spec["account_count"] = account_count
    out = root / GENERATED_SPEC
    out.write_text(yaml.safe_dump(spec, sort_keys=False))
    return out


def _parse_timestamp(value: object) -> datetime | None:
    """Parse one of wsa's RFC 3339 report timestamps, tolerantly.

    Go emits `Z` suffixes and up to nanosecond precision, neither of which
    `datetime.fromisoformat` accepts before Python 3.11 (this project supports
    3.10). Go's zero time (year 1) means "never set" and is treated as absent.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text[-1] in "Zz":
        text = text[:-1] + "+00:00"
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed.year <= 1:
        return None
    return parsed


def discover_runs(output_dir: Path) -> list[Run]:
    """Every usable run under a `wsa-output/` directory, newest build first.

    Skipped: plain files (macOS drops a `.DS_Store` in here), `*-cleaned`
    directories — wsa renames a run that way once torn down
    (`output.go:26,78-85`) — and directories whose `build-report.json` is
    missing or unparseable, which is what a build that died before writing its
    report leaves behind.

    Ordering uses the report's own `completed_at`, since run-ids are random
    3-5 character strings that sort meaninglessly. `started_at` then the
    report's mtime are the fallbacks.
    """
    if not output_dir.is_dir():
        return []

    runs: list[Run] = []
    for entry in sorted(output_dir.iterdir()):
        if not entry.is_dir() or entry.name.endswith(CLEANED_SUFFIX):
            continue
        report = entry / BUILD_REPORT
        if not report.is_file():
            continue
        try:
            data = json.loads(report.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        finished = _parse_timestamp(data.get("completed_at")) or _parse_timestamp(data.get("started_at"))
        if finished is None:
            finished = datetime.fromtimestamp(report.stat().st_mtime, tz=timezone.utc)
        run_id = str(data.get("run_id") or entry.name)
        runs.append(Run(path=entry, run_id=run_id, csv=entry / BUILD_CSV, finished=finished))

    runs.sort(key=lambda r: r.finished, reverse=True)
    return runs


def newest_run(output_dir: Path) -> Run | None:
    """The most recently completed non-cleaned run, or None if there is none."""
    runs = discover_runs(output_dir)
    return runs[0] if runs else None


def resolve_run(output_dir: Path, run_id: str = "") -> Run | None:
    """The run named by `run_id`, or the newest one when `run_id` is empty.

    Matching on an explicit run-id matters more than it looks: if a build with
    `--run-id X` dies before writing X's report, "newest" is some *earlier*
    run, and anything derived from it (a CSV path, a teardown target) would
    point at the wrong workshop.

    Matching is on the report's own `run_id`, not the directory name, so a
    hand-copied or renamed directory can never make the returned run's id
    disagree with what was asked for — the id then names the cards.
    """
    if not run_id:
        return newest_run(output_dir)
    for run in discover_runs(output_dir):
        if run.run_id == run_id:
            return run
    return None


def _require_run(root: Path, action: str) -> Run:
    output_dir = root / OUTPUT_DIR
    run = newest_run(output_dir)
    if run is None:
        listing = sorted(p.name for p in output_dir.iterdir() if p.is_dir()) if output_dir.is_dir() else []
        available = ", ".join(listing) or "none"
        raise SystemExit(
            f"No usable run found under {output_dir} — nothing to {action}.\n"
            f"Directories there: {available}\n"
            "Run `uv run workshop build` first — a build that never wrote its "
            "build-report.json, or a run already cleaned, does not count. Pass "
            "--run-id to name one explicitly."
        )
    return run


def _stream_wsa(
    binary: Path, root: Path, subcommand: str, extra: list[str], spec_path: Path | None = None
) -> int:
    """Run `wsa <subcommand>` from `root`, streaming its output live.

    Builds take tens of minutes, so the child inherits this process's stdout
    and stderr rather than being captured — that streams by construction and
    leaves wsa's own TTY detection and progress rendering intact.

    `spec_path` overrides the `-w` target (a derived spec with a prefix override);
    defaults to the committed spec.
    """
    cmd = [str(binary), subcommand, "-w", str(spec_path or _spec_path(root)), *extra]
    print(f"$ {' '.join(cmd)}\n", flush=True)
    try:
        return subprocess.run(cmd, cwd=root).returncode
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


def _creds_command(run: Run, name: str) -> str:
    """The literal `workshop creds` line for this run — for failure hints.

    Carries the two flags `_write_cards` always passes. Without them the
    copy-pasted fallback would quietly produce weaker cards than the automatic
    path: no Console passwords and no RTCE key.
    """
    return f"uv run workshop creds --csv {run.csv} --name {name} --resolve-op --rtce-keys"


def _write_cards(root: Path, run: Run, args: argparse.Namespace) -> None:
    """Hand the resolved CSV to `workshop creds` and say where the cards landed.

    Called with a constructed namespace instead of shelling back out, and
    without touching `creds.py`'s own `required=True` arguments — a direct
    `workshop creds` still behaves exactly as it does today. Both `--csv` and
    `--name` are supplied here: leaving `--name` to the user would put back a
    prompt for the one value this wrapper exists to remove, and the run-id is
    a perfectly good directory name since `--name` only chooses
    `runs/<name>/credentials/`.
    """
    name = args.name or run.run_id
    print(f"\n=== Credential cards (run {run.run_id}) ===\n")
    creds_mod.creds(
        argparse.Namespace(
            csv=str(run.csv),
            name=name,
            # Always resolve: an unusable Console password is the single most
            # likely way a card reaches an attendee broken, and `op` is already
            # a required tool for this spec.
            resolve_op=True,
            social_feed_url=args.social_feed_url,
            region=args.region,
            # Same reasoning as resolve_op: the key is useless to hand out later
            # (the secret can't be re-read), so mint it while we're writing the
            # card. Degrades to a card without the RTCE section if the CLI isn't
            # logged in as OrganizationAdmin — creds.py warns and carries on.
            rtce_keys=getattr(args, "rtce_keys", True),
            # Appends the RTCE setup command to this run's build-output.csv, so a
            # later `wsa dispenser-upload` carries it into the claim email. Safe
            # when the dispenser isn't used: it's one extra ignored column.
            dispenser_column=True,
        )
    )
    print(f"\nCards: {root / 'runs' / name / 'credentials'}")
    print(f"Hand out <prefix>.env + <prefix>.md, or upload the dispenser CSV: {run.csv}")


def _is_full_workshop(accounts: str) -> bool:
    """True when this build covers the whole workshop, not a subset.

    The dispenser sheet is replaced wholesale on every upload, so only a build that
    accounts for every attendee may upload automatically. Empty (wsa's own default
    list) and `1-N` are whole workshops; `21-25` is a top-up.
    """
    return not accounts.strip() or re.fullmatch(r"1(-\d+)?", accounts.strip()) is not None


def _upload_dispenser(
    root: Path, run: Run, binary: Path, skip: bool = False, full_workshop: bool = True
) -> None:
    """Push this run's ``build-output.csv`` into the dispenser Google Sheet.

    Must run *after* `_write_cards`: `creds.py`'s `_add_dispenser_column` appends
    the RTCE setup command to the same CSV, and the upload is what carries it into
    each claim email.

    Four ways this does nothing, and none of them are quiet — a skipped upload means
    the sheet still lists the *previous* workshop's accounts, so attendees claim
    credentials that no longer exist. Every branch names what is missing and how to
    fix it.

    Never fatal. The build succeeded and the cards are on disk; a failed upload is
    one command to retry, not a reason to exit nonzero.
    """
    manual = (
        f"  {binary} dispenser-upload --run-id {run.run_id} "
        f"--sheets-credentials <oauth-client.json> --yes"
    )
    stale = (
        "  Attendees claiming from the sheet would get the previous workshop's\n"
        "  credentials, which no longer exist."
    )
    if not dispenser_configured(root):
        print(
            f"\nnote: no {DISPENSER_ID_ENV} set — skipping the dispenser upload. Hand the cards\n"
            f"  in runs/ out directly, or set {DISPENSER_ID_ENV}=<spreadsheet-id> in {root}/wsa.env\n"
            f"  and run:\n{manual}",
            file=sys.stderr,
        )
        return
    if skip:
        print(
            f"\nSkipping the dispenser upload (--no-dispenser-upload).\n{stale}\n"
            f"  When you want it:\n{manual}",
            file=sys.stderr,
        )
        return
    if not full_workshop:
        # An upload is a whole-sheet replace: wsa's ClearAndWrite wipes `Claimed By`
        # and `Timestamp` along with the rows. Doing that automatically behind
        # `build -a 21-25` would leave the sheet holding five accounts and erase every
        # claim already made against the other twenty. Partial builds print the
        # command instead and let the operator decide.
        print(
            "\nSkipping the dispenser upload: this build covers only part of the workshop, "
            "and an\n  upload replaces the whole sheet (clearing any claims already made). "
            f"To do it anyway:\n{manual}",
            file=sys.stderr,
        )
        return

    google_creds = find_google_credentials("", binary)
    if google_creds is None:
        print(
            f"\nwarning: no {GOOGLE_CREDS_NAME} found — skipping the dispenser upload.\n{stale}\n"
            f"  Fix: put the OAuth client JSON at ~/.wsa/{GOOGLE_CREDS_NAME}, then:\n{manual}",
            file=sys.stderr,
        )
        return

    print(f"\n=== Dispenser upload (run {run.run_id}) ===\n")
    cmd = [
        str(binary),
        "dispenser-upload",
        "--run-id",
        run.run_id,
        "--sheets-credentials",
        str(google_creds),
        # The sheet is rewritten every workshop by design; without --yes wsa stops
        # to ask Overwrite?/Append? and a non-interactive create-workshop hangs.
        "--yes",
    ]
    print(f"$ {' '.join(cmd)}\n", flush=True)
    try:
        code = subprocess.run(cmd, cwd=root).returncode
    except KeyboardInterrupt:
        code = 130
    if code != 0:
        print(
            f"\nwarning: dispenser upload failed (exit {code}). Cards are written and valid; "
            f"the sheet is not updated. Retry:\n{manual}",
            file=sys.stderr,
        )


def build(args: argparse.Namespace) -> None:
    """`wsa build` against this repo's spec, then write the credential cards."""
    root = get_project_root()
    binary = find_wsa(root)

    # Terraform needs the TF_VAR_* secrets in the environment. `create-workshop`
    # collects them before calling here; a bare `workshop build` did not, and
    # every account failed on "No value for required variable". Collecting here
    # covers both callers — for the `op run` path it is a no-op, since
    # `collect_secrets` reads os.environ first and only persists what it prompted for.
    ensure_secrets(root)
    _export_attendee_count(root, args)

    extra: list[str] = []
    if args.accounts:
        extra += ["--accounts", args.accounts]
    if args.concurrency is not None:
        extra += ["--concurrency", str(args.concurrency)]
    if args.retries is not None:
        extra += ["--retries", str(args.retries)]
    if args.run_id:
        extra += ["--run-id", args.run_id]
    if args.force:
        extra.append("--force")
    if args.no_dispenser_check:
        extra.append("--no-dispenser-check")
    if args.stream_terraform_logs:
        extra.append("--stream-terraform-logs")

    # Overrides go through a derived spec (wsa has no --var flag). When a requested
    # value equals the committed spec's, leave it alone — no generated file, current
    # behavior. A bare prefix base (`f1ws`, no placeholder) gets `{NNN}` appended so
    # a direct `workshop build --prefix f1ws` can't silently give every account the
    # same name; create-workshop already normalizes and fully validates first.
    prefix = getattr(args, "prefix", "").strip()
    if prefix and "{" not in prefix:
        prefix += "{NNN}"
    if prefix and prefix == _spec_terraform_prefix(root):
        prefix = ""

    # account_count doesn't gate anything in wsa — it only feeds the spec's `>= 1`
    # validation, the "(N accounts)" banner, and the default account list we never
    # use (we always pass --accounts). Overriding it keeps that banner from
    # contradicting the build, which is exactly the confusion it caused before.
    account_count = getattr(args, "account_count", None)
    if account_count is not None and account_count == _spec_account_count(root):
        account_count = None

    spec_path = None
    if prefix or account_count is not None:
        spec_path = _derive_spec(root, prefix=prefix, account_count=account_count)
        changed = ", ".join(
            part
            for part in (
                f"prefix={prefix}" if prefix else "",
                f"account_count={account_count}" if account_count is not None else "",
            )
            if part
        )
        print(f"Spec override: {changed}  (generated spec: {spec_path.name})\n", flush=True)

    code = _stream_wsa(binary, root, "build", extra, spec_path=spec_path)

    # wsa writes build-report.json AND build-output.csv before it reports
    # per-account failures (cmd/wsa/main.go:1019-1053), so a partial build
    # still has usable cards. Don't generate them behind a nonzero exit —
    # that would bury the failure — but resolve the paths so the follow-up is
    # a paste, not a hunt.
    run = resolve_run(root / OUTPUT_DIR, args.run_id)
    if code != 0:
        if run is not None and run.csv.exists():
            print(
                f"\nwsa build failed (exit {code}), but it wrote a CSV first. "
                "To write cards for the accounts that did apply:\n"
                f"  {_creds_command(run, args.name or run.run_id)}",
                file=sys.stderr,
            )
        raise SystemExit(code)

    if run is None:
        raise SystemExit(
            f"wsa build reported success but no build report appeared under {root / OUTPUT_DIR}."
        )
    if not run.csv.exists():
        raise SystemExit(f"wsa build reported success but {run.csv} is missing.")
    if args.no_cards:
        follow_up = _creds_command(run, args.name or run.run_id)
        print(f"\nSkipping card generation (--no-cards). When you want them:\n  {follow_up}")
        return
    _write_cards(root, run, args)
    _upload_dispenser(
        root,
        run,
        binary,
        skip=getattr(args, "no_dispenser_upload", False),
        full_workshop=_is_full_workshop(args.accounts or ""),
    )


def spec_validate(args: argparse.Namespace) -> None:
    """`wsa validate` — spec well-formedness, required tooling, env vars.

    Distinct from `workshop validate`, which health-checks provisioned
    attendee environments through their cards. This one runs *before* a build
    and needs no infrastructure.
    """
    root = get_project_root()
    code = _stream_wsa(find_wsa(root), root, "validate", [])
    if code != 0:
        raise SystemExit(code)


# `wsa clean`'s two post-teardown steps talk to Google APIs with the *same* OAuth
# desktop-client JSON — `--gmail-credentials` to rotate each attendee's Console
# password, `--sheets-credentials` to clear their row from the dispenser sheet
# (wsa's RUNBOOK.md:99). Passing neither does not fail the teardown: wsa prints one
# warning per step and carries on (`main.go:1432,1507`), so a run that reports
# success can leave every attendee's password still working and their credentials
# still sitting in a shared Google Sheet. Resolve the file here, and when it is
# genuinely absent skip both steps *explicitly* — one loud line beats two warnings
# buried in a teardown log.
GOOGLE_CREDS_ENV = "WSA_GOOGLE_CREDENTIALS"
GOOGLE_CREDS_NAME = "gmail-credentials.json"

# Set by the operator once the dispenser Google Sheet exists. wsa reads it from the
# `wsa.env` in its **CWD**, which this module pins to *this* repo — so it lives in
# this repo's gitignored `wsa.env`, not the sibling checkout's.
DISPENSER_ID_ENV = "WSA_DISPENSER_SPREADSHEET_ID"


def find_google_credentials(explicit: str = "", wsa_binary: Path | None = None) -> Path | None:
    """The OAuth client JSON for password reset and dispenser clearing.

    One file serves both flags. First hit wins: an explicit path,
    ``$WSA_GOOGLE_CREDENTIALS``, ``~/.wsa/``, then the wsa checkout root (where
    wsa's own runbook tells you to keep it). Returns None when there is none —
    the caller decides what to skip.
    """
    # An explicit path is a decision, not a hint: falling back to a *different*
    # file after it turns out to be missing is how you rotate the wrong account's
    # password without noticing.
    if explicit:
        chosen = Path(explicit).expanduser()
        if not chosen.is_file():
            raise SystemExit(f"--google-credentials: no such file: {explicit}")
        return chosen

    candidates: list[Path] = []
    if from_env := os.environ.get(GOOGLE_CREDS_ENV):
        candidates.append(Path(from_env).expanduser())
    candidates.append(Path.home() / ".wsa" / GOOGLE_CREDS_NAME)
    if wsa_binary is not None:
        candidates.append(wsa_binary.parent.parent / GOOGLE_CREDS_NAME)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def dispenser_configured(root: Path) -> bool:
    """Whether a dispenser spreadsheet is configured at all.

    Mirrors wsa's own precedence (shell env over `wsa.env`, `main.go:102-109`) so
    that a workshop which never used the dispenser skips the clear deliberately
    instead of collecting a "cannot resolve spreadsheet" warning at every teardown.
    """
    value = os.environ.get(DISPENSER_ID_ENV, "")
    if not value:
        for name in ("wsa.env", ".env"):
            path = root / name
            if not path.is_file():
                continue
            for line in path.read_text().splitlines():
                # wsa's own loader strips an optional `export ` prefix
                # (`internal/envfile/envfile.go`), and a file people think of as
                # shell config collects them. Not matching it here would skip a
                # dispenser clear that wsa itself would have performed.
                key, sep, raw = line.strip().removeprefix("export ").partition("=")
                if sep and key.strip() == DISPENSER_ID_ENV:
                    value = raw.strip().strip("'\"")
                    break
            if value:
                break
    # wsa.env.example ships the literal placeholder, and a copied-but-unedited
    # file is the common case — treat it as "no dispenser".
    return bool(value) and not value.startswith("<")


def clean(args: argparse.Namespace) -> None:
    """`wsa clean` for a run, resolving `--run-id` from the newest build report.

    wsa's own fallback is `WSA_RUN_ID` in `wsa.env`, which it only persists
    after a *fully* successful build (`main.go:1055-1060`) — exactly the case
    you least often need to tear down. Discovery works either way. An explicit
    `--run-id` still wins, so older runs stay reachable.

    `-w` goes along for the ride (it is what CLAUDE.md documents) but clean
    ignores it: the spec it destroys against is the copy inside the run
    directory, so teardown always matches what was built
    (`main.go:1116,1362-1366`).
    """
    root = get_project_root()
    binary = find_wsa(root)

    # Resolve the run *before* asking for secrets, so "nothing to clean" stays a
    # one-line error instead of a prompt followed by one.
    run_id = args.run_id or _require_run(root, "clean").run_id

    # `terraform destroy` requires the same TF_VAR_* secrets the apply did.
    # Without them every account dies on "No value for required variable"
    # before a single resource is touched — and wsa retries that
    # non-retryable error twice more. `teardown-workshop` already collects
    # them; doing it here covers a bare `workshop clean` too (the second call
    # is a no-op).
    ensure_secrets(root)

    extra = ["--run-id", run_id]
    if args.accounts:
        extra += ["--accounts", args.accounts]
    if args.concurrency is not None:
        extra += ["--concurrency", str(args.concurrency)]

    google_creds = find_google_credentials(getattr(args, "google_credentials", ""), binary)
    skip_password = args.no_password_reset
    skip_dispenser = args.no_dispenser_clear or not dispenser_configured(root)
    if google_creds is None and not skip_password:
        print(
            f"\nwarning: no {GOOGLE_CREDS_NAME} found — skipping the password reset. Every "
            "attendee's\n  Console password stays valid after teardown, so old credential cards "
            "keep working.\n  Fix: --google-credentials <path>, or put the OAuth client JSON at "
            f"~/.wsa/{GOOGLE_CREDS_NAME}.",
            file=sys.stderr,
        )
        skip_password = True
    if google_creds is None and not skip_dispenser:
        print(
            f"\nwarning: no {GOOGLE_CREDS_NAME} found — skipping the dispenser clear. Attendee\n"
            "  credentials stay visible in the dispenser sheet after teardown; clear it by hand.",
            file=sys.stderr,
        )
        skip_dispenser = True

    if skip_password:
        extra.append("--no-password-reset")
    else:
        extra += ["--gmail-credentials", str(google_creds)]
    if skip_dispenser:
        extra.append("--no-dispenser-clear")
    else:
        extra += ["--sheets-credentials", str(google_creds)]

    if args.accounts_only:
        extra.append("--accounts-only")
    if args.shared_only:
        extra.append("--shared-only")

    code = _stream_wsa(binary, root, "clean", extra)
    if code != 0:
        raise SystemExit(code)
    print(f"\nTorn down run {run_id}. Local cards under runs/ are untouched — delete them yourself.")


BUILD_EPILOG = """\
Wraps `wsa build`, then feeds the run's build-output.csv straight into
`workshop creds` — no run-id to copy. Secrets come from the environment, then
credentials.env, then a prompt, so there is nothing to inject:

  uv run workshop build --accounts 1-20 --concurrency 4

For flags this wrapper does not expose, call wsa directly (fully supported) —
but export the TF_VAR_* secrets first, wsa does not collect them:
  set -a; . ./credentials.env; set +a
  <sibling>/bin/wsa build -w wsa-spec-aws.yaml ...
"""


def add_build_arguments(p: argparse.ArgumentParser) -> None:
    p.epilog = BUILD_EPILOG
    p.formatter_class = argparse.RawDescriptionHelpFormatter
    p.add_argument("-a", "--accounts", default="", help="Accounts or ranges, e.g. 1-20 or 1,4-10 (default: all)")
    p.add_argument("-c", "--concurrency", type=int, help="Parallel Terraform runs (wsa default: 10)")
    p.add_argument("-r", "--retries", type=int, help="Retries per account on failure (wsa default: 2)")
    p.add_argument("--run-id", default="", help="Group build/clean under this run-id (default: wsa picks a random one)")
    p.add_argument(
        "--prefix",
        default="",
        help="Override the spec's prefix, e.g. f1ws (the {NNN} account number is appended "
        "if omitted; f1ws{NNN} also accepted). Must be 1-12 alphanumerics per account. "
        "Default: the spec value.",
    )
    p.add_argument(
        "--account-count",
        type=int,
        default=None,
        help="Override the spec's account_count. Cosmetic — it only fixes wsa's "
        "\"(N accounts)\" banner, since --accounts decides what actually gets built. "
        "create-workshop sets this from --attendees.",
    )
    p.add_argument("--force", action="store_true", help="Re-run even if this run-id already built successfully")
    p.add_argument(
        "--no-dispenser-check",
        action="store_true",
        help="Skip wsa's pre-flight check for already-claimed accounts",
    )
    p.add_argument(
        "--stream-terraform-logs",
        action="store_true",
        help="Stream per-account Terraform output as it runs",
    )
    p.add_argument(
        "-n",
        "--name",
        default="",
        help="Card directory label — runs/<name>/credentials/ (default: the run-id from build-report.json)",
    )
    p.add_argument("--social-feed-url", default="", help="LAB 5 race-feed base URL, stamped onto every card")
    p.add_argument("--region", default="us-east-1", help="AWS region (used to derive each card's RTCE MCP endpoint)")
    p.add_argument(
        "--no-cards",
        action="store_true",
        help="Build only; print the `workshop creds` command instead of running it",
    )
    p.add_argument(
        "--no-dispenser-upload",
        action="store_true",
        help="Skip pushing this run's accounts into the dispenser Google Sheet "
        "(already a no-op when WSA_DISPENSER_SPREADSHEET_ID is unset)",
    )
    p.add_argument(
        "--no-rtce-keys",
        dest="rtce_keys",
        action="store_false",
        default=True,
        help="Skip minting each attendee's Real-Time Context Engine Global API key "
        "(needs the `confluent` CLI logged in as OrganizationAdmin)",
    )


def configure_spec_validate_parser(p: argparse.ArgumentParser) -> None:
    """Set up the `spec-validate` parser.

    Deliberately adds no arguments — everything wsa validate needs comes from
    the spec and the environment, so there is nothing to pass. Named
    `configure_*` rather than `add_*_arguments` to say so out loud.
    """
    p.description = "Run `wsa validate` against this repo's wsa-spec-aws.yaml (spec + local prerequisites)."


def add_clean_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run-id", default="", help="Run to tear down (default: newest non-cleaned run in wsa-output/)")
    p.add_argument("-a", "--accounts", default="", help="Accounts or ranges to clean, e.g. 1-20 (default: all)")
    p.add_argument("-c", "--concurrency", type=int, help="Parallel Terraform runs (wsa default: 10)")
    p.add_argument("--no-password-reset", action="store_true", help="Skip resetting account passwords after teardown")
    p.add_argument(
        "--no-dispenser-clear",
        action="store_true",
        help="Skip clearing account rows from the dispenser sheet",
    )
    p.add_argument(
        "--accounts-only",
        action="store_true",
        help="Destroy per-account infra only; keep shared infra for reuse",
    )
    p.add_argument("--shared-only", action="store_true", help="Destroy shared infra only (no build report required)")
    p.add_argument(
        "--google-credentials",
        default="",
        help="OAuth client JSON for the password reset and the dispenser clear (one file does both). "
        f"Default: ${GOOGLE_CREDS_ENV}, then ~/.wsa/{GOOGLE_CREDS_NAME}, then the wsa checkout root. "
        "Without it both steps are skipped and teardown says so.",
    )
