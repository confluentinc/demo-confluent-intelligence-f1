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
import sys
from pathlib import Path

import yaml

from scripts.common.login_checks import (
    check_aws_configured,
    check_docker_running,
    check_terraform_installed,
)
from scripts.common.terraform import get_project_root
from scripts.workshop import wsa as wsa_mod
from scripts.workshop.secrets import ensure_secrets


def _read_spec_account_count(root: Path) -> int:
    spec = yaml.safe_load((root / wsa_mod.SPEC_FILE).read_text())
    return int(spec.get("account_count", 5))


def _read_spec_email_pattern(root: Path) -> str:
    spec = yaml.safe_load((root / wsa_mod.SPEC_FILE).read_text())
    return str(spec.get("email_pattern", ""))


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
    ns.concurrency = args.concurrency
    ns.name = args.name
    ns.region = args.region
    ns.social_feed_url = args.social_feed_url
    ns.no_cards = False
    return ns


def _print_next_steps(name: str, attendees: int, root: Path) -> None:
    cards_dir = f"runs/{name}/credentials/"
    email_pattern = _read_spec_email_pattern(root)

    print(f"""
=== Workshop Ready ===

  Credential cards:  {cards_dir}
  Attendees:         {attendees}

Races are already running (ECS auto-starts each simulator).

  Stop all races:      uv run workshop stop-races
  Start all races:     uv run workshop start-races
  Reset for new run:   uv run workshop reset-races
  Validate env health: uv run workshop validate --creds-glob '{cards_dir}*.env'

  Tear down entirely:  uv run teardown-workshop

  All subcommands:     uv run workshop --help""")

    if email_pattern:
        print(f"""
Note: wsa-spec-aws.yaml's email_pattern is {email_pattern}.
Edit that file if a different organizer is running this workshop.""")
    print()


def create(args: argparse.Namespace) -> None:
    """The full create-workshop orchestration."""
    root = get_project_root()
    interactive = not args.yes

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
    if attendees > spec_count:
        raise SystemExit(
            f"--attendees {attendees} exceeds account_count ({spec_count}) "
            f"in {wsa_mod.SPEC_FILE}.\n"
            f"Edit that file's account_count field to provision more attendees.\n"
            f"Also check terraform/aws-shared/variables.tf's attendee_count "
            f"(Postgres replication slots) — it must be >= account_count."
        )

    print(f"\n  Attendees:   {attendees}")

    # --- 4. Collect secrets ---
    print("\n=== Secrets ===")
    ensure_secrets(root, interactive=interactive)

    # --- 5. Spec validation ---
    print("\n=== Spec validation (wsa validate) ===\n")
    wsa_mod.spec_validate(argparse.Namespace())

    # --- 6. Build ---
    print("\n=== Building workshop ({} attendee{}) ===\n".format(attendees, "s" if attendees != 1 else ""))
    ns = _build_namespace(attendees, args)
    wsa_mod.build(ns)

    # --- 7. Next steps ---
    run = wsa_mod.resolve_run(output_dir, ns.run_id)
    name = args.name or (run.run_id if run else "workshop")
    _print_next_steps(name, attendees, root)


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--attendees",
        type=int,
        default=None,
        help="Number of attendee environments (default: prompted, or spec's account_count)",
    )
    p.add_argument("-c", "--concurrency", type=int, default=4, help="Parallel Terraform runs (default: 4)")
    p.add_argument(
        "-n", "--name", default="", help="Card directory label — runs/<name>/credentials/ (default: the run-id)"
    )
    p.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    p.add_argument("--social-feed-url", default="", help="LAB 5 race-feed base URL, stamped onto every card")
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
