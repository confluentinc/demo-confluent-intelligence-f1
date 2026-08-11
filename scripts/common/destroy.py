"""
Destroy what YOU deployed on this machine — `uv run deploy` and/or
`uv run selfservice up`. Pick what to tear down, confirm, then destroy.

Deployments are offered as groups, not raw Terraform tiers, because `aws` and
`aws-shared` are never created independently: `uv run deploy` always applies
both, and destroying the shared half alone permanently strands the other (the
`aws` destroy needs `aws-shared`'s outputs — see _inject_shared_vars). Keeping
them a unit removes that footgun by construction.

That grouping is also the unit of failure: a tier that fails to destroy aborts the
rest of *its* group and nothing else. Selected groups are therefore walked
group-by-group rather than as one flat list of tiers — flattening them would make
a stop-on-failure either too weak (destroying the shared tier that the surviving
`aws` resources depend on) or too strong (skipping a `self-service` teardown that
shares nothing with the AWS tiers).

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
from .deployment_meta import TRACKS, retire_track
from .terraform import cleanup_terraform_artifacts, get_project_root, run_terraform_output
from .terraform_runner import run_terraform_destroy

# Stand-in values for a destroy that has no aws-shared state to read. Terraform
# evaluates variables even on destroy and terraform/aws declares these five with
# no default, so leaving one unset aborts the run with "No value for required
# variable" before a single resource is deleted — exactly the case where you most
# need the teardown to work. The values are never used for anything; they only
# have to parse as the declared types, which is why shared_subnet_ids carries one
# dummy element rather than [] (config that indexes it still has to evaluate).
_SHARED_VAR_PLACEHOLDERS = {
    "TF_VAR_shared_vpc_id": "vpc-00000000000000000",
    "TF_VAR_shared_subnet_ids": '["subnet-00000000000000000"]',
    "TF_VAR_shared_postgres_host": "destroyed.invalid",
    "TF_VAR_shared_postgres_password": "unused",
    "TF_VAR_shared_ecr_image_uri": "destroyed.invalid/unused:latest",
}


def _inject_shared_vars(root: Path) -> None:
    """Set TF_VAR_shared_* so the aws destroy can evaluate its config.

    Placeholders go in first and real aws-shared outputs overwrite them, rather
    than the reverse. That ordering is the whole point: this function used to
    return early when aws-shared had no state, which meant a *partial* deployment
    — an `aws` tier whose shared half was never applied or was torn down first —
    could never be destroyed at all. It also closes a quieter hole, where an
    aws-shared output that came back empty left its variable unset even though
    shared state existed.

    Placeholders use setdefault so an operator who exported a real value keeps it.
    """
    for key, value in _SHARED_VAR_PLACEHOLDERS.items():
        os.environ.setdefault(key, value)

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

# Which deployment track each Terraform tier belongs to, so a successful destroy
# can clean up everything that track left behind (see _retire_tier). Derived from
# the Track definitions rather than re-listed here, so the run directory, the tier
# name, and the prefix suffix can never drift apart. `aws-shared` owns no card and
# no track — it is shared infrastructure.
TIER_TRACKS = {track.tier: track for track in TRACKS.values()}


def _retire_tier(root: Path, tier: str) -> None:
    """Clean up what a destroyed tier leaves behind on disk. Success path only.

    Clearing the `F1_CARD` pointer is not enough on its own. The card *files*
    used to stay put, so on a machine that has used both solo tracks, tearing
    one down left two cards and no pointer — and `resolve_card()` refuses to
    guess between them, so **every** attendee tool hard-exited with "Multiple
    credential cards found" while exactly one live environment existed. Deleting
    the dead track's cards restores the single-candidate case resolution needs.

    Also removes the self-service `.seeded` marker, which used to survive
    `uv run destroy` (only `selfservice down` unlinked it). The next
    `selfservice up` then printed "already seeded" over an empty
    `driver_race_history`: LAB 2's COUNT(*) returns 0 and LAB 4's history join
    returns nothing, with no error anywhere.

    Called per tier on that tier's own success, not once per group: if `aws`
    destroyed cleanly its card is dead no matter what `aws-shared` goes on to do.
    Never call this after a failure — the stale card is the only record of a
    deployment that still has live resources.
    """
    track = TIER_TRACKS.get(tier)
    if track is None:  # aws-shared: shared infrastructure, no card, no metadata
        return

    removed = retire_track(root, track)
    if removed:
        print(f"  Removed dead {track.name} files: {', '.join(removed)}")


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
    failed: list[str] = []
    skipped: list[str] = []

    # Groups outer, tiers inner — deliberately, not a flat walk of
    # `envs_to_destroy`. Within a group the tiers form a dependency chain: `aws`
    # consumes `aws-shared`'s Postgres, simulator image, and outputs, so tearing
    # the shared half down after the attendee half failed strands the surviving
    # ECS service and CDC connector against infrastructure that no longer exists,
    # and no later `uv run destroy` can clean them up. So a failure aborts the
    # rest of *that* group.
    #
    # Across groups there is no dependency at all — `self-service` is
    # Confluent-only. Breaking out of a flattened tier list would silently skip
    # an independently-selected self-service teardown, which is why the group
    # boundary has to still exist here.
    for group_name, tiers, _desc in selected:
        for position, env in enumerate(tiers):
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
                _retire_tier(root, env)
                continue

            failed.append(env)
            print(f"\nDestroy failed at {env} — state kept at {state_file}")

            remaining = tiers[position + 1 :]
            if remaining:
                skipped.extend(remaining)
                print(f"NOT destroying {', '.join(remaining)} — {env} still has live resources that")
                print(f"depend on it. Tearing down the rest of '{group_name}' now would strand them")
                print(f"permanently. Fix the {env} failure and re-run `uv run destroy`.")
            else:
                print("Re-run `uv run destroy` to retry.")
            break  # abort this group only; other groups are independent

    if failed:
        print(f"\nDestroy incomplete — these tiers still have live resources: {', '.join(failed)}")
        if skipped:
            print(f"Not attempted (they back the failed tier): {', '.join(skipped)}")
        sys.exit(1)

    print("\nDestroy process completed!")


if __name__ == "__main__":
    main()
