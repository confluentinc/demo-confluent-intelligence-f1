"""`teardown-workshop` — one-command workshop teardown.

Wraps ``workshop clean`` with secret collection and confirmation:

    uv run teardown-workshop          # confirms, then tears down the newest run
    uv run teardown-workshop --yes    # no confirmation prompt

Deletes the credential card directory after a successful teardown so
stale cards don't cause ambiguity in ``resolve_card()`` later.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from scripts.common.credentials import clear_active_card
from scripts.common.terraform import get_project_root
from scripts.workshop import wsa as wsa_mod
from scripts.workshop.secrets import ensure_secrets


def teardown(args: argparse.Namespace) -> None:
    root = get_project_root()
    interactive = not args.yes

    # --- 1. wsa binary ---
    wsa_mod.find_wsa(root)

    # --- 2. Resolve which run to tear down ---
    run_id = args.run_id
    if run_id:
        run = wsa_mod.resolve_run(root / wsa_mod.OUTPUT_DIR, run_id)
        if run is None:
            raise SystemExit(f"No run found with id {run_id!r} under {root / wsa_mod.OUTPUT_DIR}")
    else:
        run = wsa_mod.newest_run(root / wsa_mod.OUTPUT_DIR)
        if run is None:
            raise SystemExit(
                "No live workshop run found. Nothing to tear down.\n"
                f"(Checked {root / wsa_mod.OUTPUT_DIR} for non-cleaned runs.)"
            )
        run_id = run.run_id

    # --- 3. Confirm ---
    print(f"  Run:       {run_id}")
    print(f"  Finished:  {run.finished.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Directory: {run.path}")

    if interactive:
        answer = input("\nTear down this workshop run? All attendee environments will be destroyed. (y/n): ").strip()
        if answer.lower() != "y":
            print("Cancelled.")
            sys.exit(0)

    # --- 4. Secrets (needed for terraform destroy) ---
    ensure_secrets(root, interactive=interactive)

    # --- 5. Clean ---
    ns = argparse.Namespace(
        run_id=run_id,
        accounts="",
        concurrency=args.concurrency,
        no_password_reset=False,
        no_dispenser_clear=False,
        accounts_only=False,
        shared_only=False,
    )
    wsa_mod.clean(ns)

    # --- 6. Clean up credential cards for this run ---
    _cleanup_cards(root, run, interactive)


def _cleanup_cards(root: Path, run: wsa_mod.Run, interactive: bool) -> None:
    """Offer to delete the card directory scoped to this run only.

    Only targets ``runs/<run_id>/`` — never ``runs/standalone/`` or
    ``runs/selfservice/``, which belong to other deployment tracks.
    """
    # Match by run_id — the card directory name matches the run_id or --name
    cards_dir = root / "runs" / run.run_id / "credentials"
    if not cards_dir.is_dir():
        return

    cards = list(cards_dir.glob("*.env"))
    if not cards:
        return

    if not interactive:
        print(f"\n  Card directory kept: {cards_dir.relative_to(root)}")
        print("  Delete it manually, or pass without --yes to be prompted.")
        return

    answer = input(
        f"\nDelete credential cards at {cards_dir.relative_to(root)}? "
        f"({len(cards)} file{'s' if len(cards) != 1 else ''}) (y/n): "
    )
    if answer.strip().lower() != "y":
        print(f"  Kept: {cards_dir.relative_to(root)}")
        return

    try:
        clear_active_card(root, only_if_under=cards_dir)
        shutil.rmtree(cards_dir.parent)
        print(f"  Deleted: {cards_dir.parent.relative_to(root)}")
    except OSError as e:
        print(f"  Could not delete {cards_dir}: {e}")


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run-id", default="", help="Which run to tear down (default: newest non-cleaned run)")
    p.add_argument("-c", "--concurrency", type=int, help="Parallel Terraform runs")
    p.add_argument("--yes", action="store_true", help="Skip confirmation prompt")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="teardown-workshop",
        description="Tear down a workshop run — destroy all attendee environments",
    )
    add_arguments(parser)
    args = parser.parse_args()
    teardown(args)


if __name__ == "__main__":
    main()
