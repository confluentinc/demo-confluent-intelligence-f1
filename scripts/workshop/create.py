"""`create-workshop` — one-command workshop provisioning.

Wraps the existing ``workshop build`` pipeline with preflight checks,
interactive secret collection, and clear next-steps output, so the
organizer never has to remember which subcommands to chain:

    uv run create-workshop --attendees 5

If secrets are already in the environment (e.g. via ``op run``), they
are used directly; otherwise the user is prompted once and the values
are persisted to ``credentials.env`` for future runs.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import requests
import yaml
from dotenv import set_key

from scripts.common.credentials import (
    generate_confluent_api_keys,
    load_or_create_credentials_file,
)
from scripts.common.login_checks import (
    check_aws_configured,
    check_docker_running,
    check_terraform_installed,
    ensure_confluent_login,
)
from scripts.common.terraform import get_project_root
from scripts.workshop import wsa as wsa_mod
from scripts.workshop.secrets import ensure_secrets


def _read_spec_account_count(root: Path) -> int:
    spec = yaml.safe_load((root / wsa_mod.SPEC_FILE).read_text())
    return int(spec.get("account_count", 5))


def _read_spec_prefix(root: Path) -> str:
    spec = yaml.safe_load((root / wsa_mod.SPEC_FILE).read_text())
    return str(spec.get("terraform_vars", {}).get("prefix", "f1wp{NNN}"))


def _expand_prefix(pattern: str, n: int) -> str:
    """Expand a wsa prefix pattern for account number ``n``.

    Matches wsa's own placeholder substitution — longest token first so ``{NNN}``
    is replaced before ``{NN}``/``{N}`` (otherwise ``{NNN}`` becomes ``1NN``).
    """
    return (
        pattern.replace("{NNN}", f"{n:03d}")
        .replace("{NN}", f"{n:02d}")
        .replace("{N}", str(n))
    )


_PLACEHOLDER_RE = re.compile(r"\{N+\}")


def _validate_prefix(pattern: str, attendees: int) -> str | None:
    """Return an error string if ``pattern`` won't produce valid per-account names.

    Enforces the same contract Terraform does (``terraform/aws/variables.tf``:
    ``^[A-Za-z0-9]{1,12}$`` on the expanded prefix) plus the requirement that a
    multi-attendee workshop keep a ``{N}``-family placeholder so names stay unique.
    """
    if attendees > 1 and not _PLACEHOLDER_RE.search(pattern):
        return (
            "no {N}/{NN}/{NNN} placeholder — every attendee would resolve to the "
            "same name and collide. Add a placeholder, e.g. f1wp{NNN}."
        )
    for n in range(1, attendees + 1):
        expanded = _expand_prefix(pattern, n)
        if not re.fullmatch(r"[A-Za-z0-9]{1,12}", expanded):
            return (
                f"account {n} would be named '{expanded}', which isn't valid — a prefix "
                "must be letters and digits only, at most 12 characters once the account "
                "number is added (Terraform enforces ^[A-Za-z0-9]{1,12}$)."
            )
    return None


def _normalize_prefix(value: str) -> str:
    """Append the ``{NNN}`` account placeholder when the user gave only a base prefix.

    Lets both the prompt and ``--prefix`` accept either ``f1ws`` or the full
    ``f1ws{NNN}``. A value that already contains a placeholder is returned unchanged.
    """
    value = value.strip()
    if value and "{" not in value:
        return value + "{NNN}"
    return value


def _prefix_base(pattern: str) -> str:
    """The prefix with its trailing ``{N}``-family placeholder stripped (for display)."""
    return re.sub(r"\{N+\}$", "", pattern)


def _describe_prefix(pattern: str) -> str:
    """Preview the per-attendee resource names the prefix drives, for account 1."""
    p = _expand_prefix(pattern, 1)
    pl = p.lower()
    return (
        f"    Confluent environment   RIVER-RACING-{p}-ENV\n"
        f"    Confluent cluster       RIVER-RACING-{p}-CLUSTER\n"
        f"    ECS simulator service   river-racing-{pl}-<hex>-simulator\n"
        f"    Postgres CDC slot       f1_cdc_{pl}   (publication f1_pub_{pl})\n"
        f"    Credential card file    {p}.env"
    )


def _prompt_prefix(root: Path, attendees: int, interactive: bool, override: str = "") -> str:
    """Resolve the prefix template: --prefix / spec default / interactive prompt.

    The prompt asks for the *base* only (letters/digits) and appends the ``{NNN}``
    account number automatically, so the attendee number is never something the
    organizer has to type or reason about. A full template (``f1ws{NNN}``) is still
    accepted from either the prompt or ``--prefix``.
    """
    default_template = _read_spec_prefix(root)
    default_base = _prefix_base(default_template)
    override = override.strip()

    if override or not (interactive and sys.stdin.isatty()):
        chosen = _normalize_prefix(override) if override else default_template
        err = _validate_prefix(chosen, attendees)
        if err:
            raise SystemExit(f"Prefix '{chosen}' is invalid: {err}")
        return chosen

    print("\n=== Environment prefix ===\n")
    print(
        "  Each attendee's resources are named <prefix><NNN>, where NNN is their\n"
        "  account number (001, 002, …), added automatically. Enter the prefix only:\n"
        "  letters and digits, up to 9 characters. Press Enter to keep the default.\n"
    )
    print(f"  Default '{default_base}' names account 1:")
    print(_describe_prefix(default_template))
    while True:
        raw = input(f"\n  Prefix [{default_base}]: ").strip()
        chosen = default_template if not raw else _normalize_prefix(raw)
        err = _validate_prefix(chosen, attendees)
        if err:
            print(f"  Invalid: {err}")
            continue
        if chosen != default_template:
            print(f"\n  Using '{_prefix_base(chosen)}' — account 1 names:")
            print(_describe_prefix(chosen))
        return chosen


def _list_confluent_environments(api_key: str, api_secret: str) -> dict[str, str]:
    """Return ``{display_name: environment_id}`` for every environment in the org.

    Queries the Confluent Cloud REST API directly (not the CLI) so the check runs
    off the same api-key/secret the Terraform build authenticates with — no separate
    CLI login, and a key that can list environments is a key that can create them.

    Raises ``requests`` exceptions on HTTP/network failure; the caller decides which
    are fatal (401/403) vs. skippable (network).
    """
    out: dict[str, str] = {}
    url = "https://api.confluent.cloud/org/v2/environments?page_size=100"
    auth = (api_key, api_secret)
    while url:
        resp = requests.get(url, auth=auth, timeout=30)
        if resp.status_code in (401, 403):
            raise SystemExit(
                "  The Confluent Cloud API key can't list environments "
                f"(HTTP {resp.status_code}).\n"
                "  It needs OrganizationAdmin to create the workshop environments — "
                "check TF_VAR_confluent_cloud_api_key / _secret."
            )
        resp.raise_for_status()
        body = resp.json()
        for env in body.get("data", []):
            name, env_id = env.get("display_name"), env.get("id")
            if name and env_id:
                out[name] = env_id
        url = (body.get("metadata") or {}).get("next") or ""
    return out


def _check_env_name_collisions(prefix_pattern: str, attendees: int) -> None:
    """Refuse early if any ``RIVER-RACING-<prefix>-ENV`` the build will create exists.

    Environment names are deterministic (``terraform/aws``:
    ``RIVER-RACING-${prefix}-ENV``) and each ``wsa build`` starts from empty
    Terraform state, so an environment left over from a prior build that outlived
    its state makes ``confluent_environment`` return 409 Conflict ("Environment
    name is already in use") — a failure the per-account retries can never clear.
    Catch it here with the offending env IDs instead of a fan-out retry storm.
    """
    api_key = os.environ.get("TF_VAR_confluent_cloud_api_key", "").strip()
    api_secret = os.environ.get("TF_VAR_confluent_cloud_api_secret", "").strip()
    if not (api_key and api_secret):
        print("  env-name check:  skipped (no Confluent API key available)")
        return

    expected = {
        f"RIVER-RACING-{_expand_prefix(prefix_pattern, n)}-ENV": n
        for n in range(1, attendees + 1)
    }

    try:
        existing = _list_confluent_environments(api_key, api_secret)
    except requests.RequestException as e:
        # Network/timeout only (401/403 already raised SystemExit inside). Don't
        # block a build on a flaky check — the build will surface a real failure.
        print(f"  env-name check:  skipped ({e})")
        return

    collisions = sorted((name, existing[name]) for name in expected if name in existing)
    if not collisions:
        print(f"  env-name check:  ok ({attendees} name{'s' if attendees != 1 else ''} free)")
        return

    listed = "\n".join(f"    {name}   {env_id}" for name, env_id in collisions)
    deletes = "\n".join(f"    confluent environment delete {env_id}" for _, env_id in collisions)
    raise SystemExit(
        f"\n{len(collisions)} Confluent environment name(s) the build needs already exist:\n"
        f"{listed}\n\n"
        "These are orphaned from a prior build — environment names are deterministic\n"
        "and each build starts from empty Terraform state, so Terraform can't reuse\n"
        "them and will fail with 409 Conflict on every retry.\n\n"
        "Delete them, then re-run create-workshop:\n"
        f"{deletes}"
    )


def _export_attendee_count(attendees: int) -> None:
    """Keep the shared-infra attendee-count compatibility variable authoritative.

    The shared Postgres host is fixed at 105 replication slots and no longer
    derives capacity from this value. The accelerator's Terraform runner still
    inherits ``TF_VAR_*`` values, so keep the variable aligned with
    ``--attendees`` for compatibility with the shared-infra contract and older
    tooling.

    Deliberately an assignment, not ``setdefault``: --attendees is authoritative,
    so a stale export must not quietly cap the slot count. A conflicting value is
    reported rather than swallowed.
    """
    var = "TF_VAR_attendee_count"
    existing = os.environ.get(var, "").strip()
    if existing and existing != str(attendees):
        print(f"  note: overriding exported {var}={existing} with --attendees {attendees}")
    os.environ[var] = str(attendees)


def _build_namespace(attendees: int, args: argparse.Namespace) -> argparse.Namespace:
    """Construct the namespace ``wsa_mod.build`` expects.

    Uses ``add_build_arguments`` on a throwaway parser to pick up every
    default, then overrides the fields we control. This way a new flag
    added to ``wsa.py`` gets its default automatically instead of causing
    an ``AttributeError``.
    """
    p = argparse.ArgumentParser()
    wsa_mod.add_build_arguments(p)
    ns = p.parse_args([])

    ns.accounts = f"1-{attendees}"
    ns.account_count = attendees
    ns.concurrency = args.concurrency
    ns.name = args.name
    ns.region = args.region
    ns.social_feed_url = args.social_feed_url
    ns.email_pattern = args.email_pattern
    ns.email_pattern_interactive = False
    ns.no_cards = False
    ns.no_dispenser_upload = getattr(args, "no_dispenser_upload", False)
    return ns


def _print_next_steps(
    name: str,
    attendees: int,
    root: Path,
    email_pattern: str = "",
    run_id: str = "",
) -> None:
    cards_dir = f"runs/{name}/credentials/"
    run_flag = f" --run-id {run_id}" if run_id else ""

    print(f"""
=== Workshop Ready ===

  Credential cards:  {cards_dir}
  Attendees:         {attendees}

Race preparation passed. Every simulator is stopped and ready for the organizer.

  Check race status:    uv run workshop race-status{run_flag}
  Start all races:      uv run workshop start-races{run_flag}
  Stop all races:       uv run workshop stop-races{run_flag}
  Reset for new run:    uv run workshop reset-races{run_flag}
  Rehearse preparation: uv run workshop prepare-races{run_flag}
  Validate env health: uv run workshop validate --creds-glob '{cards_dir}*.env'

  Tear down entirely:  uv run teardown-workshop

  All subcommands:     uv run workshop --help""")

    if wsa_mod.dispenser_configured(root):
        print(
            "\nThe dispenser sheet now holds this run's accounts — attendees can claim\n"
            "via the Google Form (turn on 'Accepting responses' if it is off)."
        )

    if email_pattern:
        print(f"\n  Attendee emails:   {email_pattern}")
    print()


def _offer_key_generation(root: Path) -> None:
    """Offer to generate a Confluent Cloud API key (ported from deploy.py).

    Runs before ensure_secrets so generated keys land in credentials.env and are
    picked up by collect_secrets without a second prompt. The Confluent CLI login
    is needed only here — a user who already has an OrganizationAdmin key answers
    'n' and never logs in.
    """
    creds_file, creds = load_or_create_credentials_file(root)
    generate = input("\nGenerate new Confluent Cloud API keys? (y/n) [n]: ").strip().lower()
    if generate != "y":
        return
    if not ensure_confluent_login(creds, creds_file=creds_file, interactive=True):
        raise SystemExit("Confluent CLI login required to generate API keys.")
    api_key, api_secret = generate_confluent_api_keys()
    if api_key and api_secret:
        set_key(str(creds_file), "TF_VAR_confluent_cloud_api_key", api_key)
        set_key(str(creds_file), "TF_VAR_confluent_cloud_api_secret", api_secret)


def create(args: argparse.Namespace) -> None:
    """The full create-workshop orchestration."""
    root = get_project_root()
    interactive = not args.yes
    email_pattern = ""
    if not interactive:
        # Fail before tool and cloud preflight when --yes would otherwise build
        # accounts from the neutral pattern committed in the public spec.
        email_pattern = wsa_mod.resolve_email_pattern(
            root,
            override=getattr(args, "email_pattern", ""),
            interactive=False,
        )

    # --- 1. Fast preflight: wsa binary, terraform, docker, AWS ---
    print("=== Preflight checks ===\n")

    binary = wsa_mod.find_wsa(root)
    print(f"  wsa binary:  {binary}")

    if not check_terraform_installed():
        raise SystemExit("Terraform is not installed. Install it: https://developer.hashicorp.com/terraform/install")
    print("  terraform:   ok")

    if not check_docker_running():
        raise SystemExit("Docker is not running. Start Docker Desktop (needed for the simulator image build).")
    print("  docker:      ok")

    if not check_aws_configured():
        raise SystemExit("AWS CLI is not configured. Run: aws configure")
    print("  aws cli:     ok")

    # --- 2. Refuse to stack a second live run ---
    output_dir = root / wsa_mod.OUTPUT_DIR
    existing = wsa_mod.newest_run(output_dir)
    if existing and not args.force:
        raise SystemExit(
            f"A live workshop run already exists: {existing.run_id} "
            f"(finished {existing.finished.strftime('%Y-%m-%d %H:%M UTC')})\n"
            f"  Cards: {existing.path}\n\n"
            "Tear it down first:  uv run teardown-workshop\n"
            "Or force a new one:  uv run create-workshop --force ..."
        )

    # --- 3. Validate --attendees ---
    # --attendees is authoritative: it drives the accounts wsa builds and the spec's
    # account_count via a derived spec, so no committed file needs editing to grow a
    # workshop. TF_VAR_attendee_count is retained only for shared-infra compatibility.
    # The spec value survives only as the interactive default. Over-reaching is
    # caught by wsa's shared precheck, which verifies the Console password of every
    # account actually exists.
    spec_count = _read_spec_account_count(root)

    if args.attendees is None:
        if interactive and sys.stdin.isatty():
            raw = input(f"\n  Number of attendees [{spec_count}]: ").strip()
            attendees = int(raw) if raw else spec_count
        else:
            attendees = spec_count
    else:
        attendees = args.attendees

    if attendees < 1:
        raise SystemExit("--attendees must be at least 1.")

    print(f"\n  Attendees:   {attendees}")

    # --- 3b. Environment prefix (drives every per-attendee resource name) ---
    prefix = _prompt_prefix(root, attendees, interactive, override=args.prefix)

    # --- 3c. Attendee login pattern ---
    if not email_pattern:
        email_pattern = wsa_mod.resolve_email_pattern(
            root,
            override=getattr(args, "email_pattern", ""),
            interactive=True,
        )
    args.email_pattern = email_pattern

    # --- 4. Collect secrets ---
    print("\n=== Secrets ===")
    if interactive and sys.stdin.isatty():
        _offer_key_generation(root)
    ensure_secrets(root, interactive=interactive)

    # --- 4b. Refuse orphaned environment-name collisions before the build ---
    _check_env_name_collisions(prefix, attendees)

    # --- 4c. Attendee Console logins must already exist in 1Password ---
    invitation_spec = wsa_mod._derive_spec(root, email_pattern=email_pattern)
    wsa_mod._check_console_accounts(
        root,
        list(range(1, attendees + 1)),
        spec_path=invitation_spec,
    )

    # --- 5. Spec validation ---
    print("\n=== Spec validation (wsa validate) ===\n")
    wsa_mod.spec_validate(
        argparse.Namespace(email_pattern=email_pattern, email_pattern_interactive=False)
    )

    # --- 6. Build ---
    _export_attendee_count(attendees)
    print("\n=== Building workshop ({} attendee{}) ===\n".format(attendees, "s" if attendees != 1 else ""))
    ns = _build_namespace(attendees, args)
    ns.prefix = prefix
    wsa_mod.build(ns)

    # --- 7. Next steps ---
    run = wsa_mod.resolve_run(output_dir, ns.run_id)
    name = args.name or (run.run_id if run else "workshop")
    if run is not None and (root / "runs" / run.run_id / "manifest.json").is_file():
        from scripts.workshop.lifecycle import prepare_races

        print("\n=== Race preparation smoke test ===\n")
        prepare_races(argparse.Namespace(run_id=run.run_id, accounts=""))
    _print_next_steps(
        name,
        attendees,
        root,
        email_pattern=email_pattern,
        run_id=run.run_id if run else "",
    )


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--attendees",
        type=int,
        default=None,
        help="Number of attendee environments. Authoritative — also sets the derived "
        "spec's account_count; shared Postgres already supports up to 95 accounts "
        "(default: prompted, or the spec's account_count)",
    )
    p.add_argument("-c", "--concurrency", type=int, default=10, help="Parallel Terraform runs (default: 10)")
    p.add_argument(
        "-n", "--name", default="", help="Card directory label — runs/<name>/credentials/ (default: the run-id)"
    )
    p.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    p.add_argument(
        "--prefix",
        default="",
        help="Environment prefix, e.g. f1ws (letters/digits; the {NNN} account number is "
        "appended automatically). Default: prompted, or the spec value. Mainly for --yes "
        "runs; the interactive flow prompts with a resource-name preview.",
    )
    p.add_argument(
        "--email-pattern",
        default="",
        help="Attendee login pattern, e.g. organizer+f1wp{N}@example.com. Default: "
        f"${wsa_mod.EMAIL_PATTERN_ENV}, credentials.env, then an interactive prompt.",
    )
    p.add_argument("--social-feed-url", default="", help="LAB 5 race-feed base URL, stamped onto every card")
    p.add_argument(
        "--no-dispenser-upload",
        action="store_true",
        help="Skip pushing the accounts into the dispenser Google Sheet after cards are "
        "written (already a no-op when WSA_DISPENSER_SPREADSHEET_ID is unset)",
    )
    p.add_argument("--yes", action="store_true", help="Skip all prompts (fail if secrets are missing)")
    p.add_argument("--force", action="store_true", help="Proceed even if a previous workshop run is still live")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="create-workshop",
        description="One-command workshop setup: preflight → secrets → validate → build → cards",
    )
    add_arguments(parser)
    args = parser.parse_args()
    create(args)


if __name__ == "__main__":
    main()
