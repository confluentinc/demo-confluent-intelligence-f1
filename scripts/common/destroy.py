"""
Destroy what YOU deployed on this machine — `uv run deploy` and/or
`uv run selfservice up`. Pick what to tear down, confirm, then destroy.

Deployments are offered as groups, not raw Terraform tiers, because `aws` and
`aws-shared` are never created independently: `uv run deploy` always applies
both, and destroying the shared half alone permanently strands the other (the
`aws` destroy needs `aws-shared`'s outputs — see _inject_shared_vars). Keeping
them a unit removes that footgun by construction.

The multi-attendee workshop is deliberately OUT OF SCOPE. `wsa` clones/stages
the Terraform into its own run directory (see wsa-spec-aws.yaml), so a
workshop's state never lands in this working tree and this script cannot reach
it. Tear a workshop down with `wsa clean`. The one way workshop shared infra
could show up here is if an organizer hand-applied terraform/aws-shared/ in
this directory — _looks_like_workshop_shared() catches that and demands a typed
confirmation before touching it.
"""

import json
import os
import sys
from pathlib import Path

from .credentials import load_or_create_credentials_file
from .terraform import cleanup_terraform_artifacts, get_project_root, run_terraform_output
from .terraform_runner import run_terraform_destroy


def _inject_shared_vars(root: Path) -> None:
    """Set TF_VAR_shared_* from aws-shared outputs so the aws destroy has the
    required variable values (Terraform evaluates the config even on destroy)."""
    shared_state = root / "terraform" / "aws-shared" / "terraform.tfstate"
    if not shared_state.exists():
        return
    try:
        shared = run_terraform_output(shared_state)
    except Exception:
        return
    mapping = {
        "TF_VAR_shared_vpc_id": shared.get("vpc_id", ""),
        "TF_VAR_shared_subnet_ids": json.dumps(shared.get("subnet_ids", [])),
        "TF_VAR_shared_postgres_host": shared.get("postgres_host", ""),
        "TF_VAR_shared_postgres_password": shared.get("postgres_password", ""),
        "TF_VAR_shared_ecr_image_uri": shared.get("ecr_image_uri", ""),
    }
    for k, v in mapping.items():
        if v:
            os.environ[k] = v


# Deployment groups offered to the user. Order within a group is destroy order.
GROUPS = [
    ("deploy", ["aws", "aws-shared"], "single-environment demo (Confluent + Postgres/CDC/ECS)"),
    ("self-service", ["self-service"], "solo, Confluent-only (no AWS infra)"),
]


def _looks_like_workshop_shared(root: Path) -> bool:
    """Detect shared infra that was NOT created by `uv run deploy`.

    `uv run deploy` always applies aws-shared *and* aws together, so shared
    state without matching aws state is anomalous — most likely an organizer
    hand-applied the shared tier for a workshop, in which case destroying it
    would rip Postgres and the simulator image out from under live attendees.

    (The attendee count is deliberately not reported: it's a root variable, not
    an output in aws-shared/outputs.tf, so it isn't reliably in state.)
    """
    tf = root / "terraform"
    if not (tf / "aws-shared" / "terraform.tfstate").exists():
        return False
    return not (tf / "aws" / "terraform.tfstate").exists()


def _select_groups(available: list[tuple[str, list[str], str]]) -> list[tuple[str, list[str], str]]:
    """Prompt for which deployments to destroy. Enter accepts all.

    Always lists what was found — with one deployment there's nothing to choose,
    but the user still needs to see which tiers it covers before confirming.
    """
    print("\nFound deployments:")
    for i, (name, tiers, desc) in enumerate(available, 1):
        print(f"  {i}. {name:<15} {desc}")
        print(f"     {' + '.join(tiers)}")

    if len(available) == 1:
        return available

    raw = input(f"\nDestroy which? [Enter = all, or e.g. 2 or 1,{len(available)}]: ").strip()
    if not raw:
        return available

    picked = []
    for token in raw.replace(",", " ").split():
        if not token.isdigit() or not (1 <= int(token) <= len(available)):
            print(f"Invalid selection: {token!r}")
            sys.exit(1)
        choice = available[int(token) - 1]
        if choice not in picked:
            picked.append(choice)
    return picked


def main():
    """Main entry point for destroy."""
    print("=== F1 Demo - Destroy ===\n")

    root = get_project_root()
    print(f"Project root: {root}")

    # Load credentials into environment
    _creds_file, creds = load_or_create_credentials_file(root)
    for key, value in creds.items():
        if value:
            os.environ[key] = value

    # Offer only the groups that actually have state on disk.
    available = []
    for name, tiers, desc in GROUPS:
        present = [t for t in tiers if (root / "terraform" / t / "terraform.tfstate").exists()]
        if not present:
            continue
        # A "deploy" that is only the shared half was not produced by `uv run
        # deploy` (which always applies both). Don't advertise it as the demo —
        # this is the path _looks_like_workshop_shared() guards.
        if present == ["aws-shared"]:
            name, desc = "shared-infra", "shared infra ONLY — may back a workshop (see warning below)"
        available.append((name, present, desc))

    if not available:
        all_tiers = [t for _n, tiers, _d in GROUPS for t in tiers]
        print(f"Nothing to destroy — no terraform state in: {', '.join(all_tiers)}")
        print("(A wsa-provisioned workshop is torn down with `wsa clean`, not this script.)")
        sys.exit(0)

    selected = _select_groups(available)
    envs_to_destroy = [t for _n, tiers, _d in selected for t in tiers]

    print(f"\nWill destroy: {', '.join(envs_to_destroy)}")
    print("WARNING: This will permanently destroy all resources!")

    if input("\nProceed? (y/n): ").strip().lower() != "y":
        print("Cancelled.")
        sys.exit(0)

    # Shared infra that didn't come from `uv run deploy` may be serving a live
    # workshop. Make that impossible to do by reflex.
    if "aws-shared" in envs_to_destroy and _looks_like_workshop_shared(root):
        print("\n" + "!" * 72)
        print("STOP: terraform/aws-shared has state but terraform/aws does not.")
        print("This shared tier was not created by `uv run deploy`.")
        print("If it backs a workshop, destroying it kills the Postgres and")
        print("simulator image every attendee depends on. Use `wsa clean` instead.")
        print("!" * 72)
        if input("\nType 'destroy-shared' to continue anyway: ").strip() != "destroy-shared":
            print("Cancelled.")
            sys.exit(0)

    print("\n=== Starting Destroy ===")
    failed = []
    for env in envs_to_destroy:
        env_path = root / "terraform" / env
        state_file = env_path / "terraform.tfstate"

        # Both non-shared tiers have required variables that Terraform evaluates
        # even on destroy — populate them before running.
        if env == "aws":
            # shared_* variables come from the aws-shared outputs.
            _inject_shared_vars(root)
        elif env == "self-service":
            # Reuse the same TF_VAR set `uv run selfservice down` builds.
            from scripts.selfservice.cli import export_selfservice_tf_env

            export_selfservice_tf_env(creds)

        print(f"\n-> Destroying {env}...")
        if run_terraform_destroy(env_path):
            # Only wipe artifacts on success. Deleting state after a failed
            # destroy strands whatever Terraform did not delete.
            cleanup_terraform_artifacts(env_path)
        else:
            failed.append(env)
            print(f"\nDestroy failed at {env} — state kept at {state_file}")
            print("Re-run `uv run destroy` to retry. Continuing with remaining tiers...")

    if failed:
        print(f"\nDestroy incomplete — these tiers still have live resources: {', '.join(failed)}")
        sys.exit(1)

    print("\nDestroy process completed!")


if __name__ == "__main__":
    main()
