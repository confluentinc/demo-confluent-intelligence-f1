"""
Destroy what YOU deployed on this machine with `uv run deploy` (any track).
Pick what to tear down, confirm, then destroy. `--force` skips every prompt.

After a fully successful teardown you are offered to delete credentials.env too
(default yes; default no while another deployment still needs it). If nothing is
left deployed, the Claude Code MCP registrations `setup-mcp` / `setup-rtce` made
for this project are removed as well — they point at the dead environment.
`--only self-service` scopes the run to one group (`uv run selfservice down`).
`--only pre-provisioned` deletes the three API keys the pre-provisioned track
minted, then its card. That track runs no Terraform: the platform team built
everything in the environment, and the environment and all of it stay.

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

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import dotenv_values

from . import deployment_meta as meta
from .credentials import load_or_create_credentials_file
from .deployment_meta import TRACKS, is_deployed, retire_track
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

# Offered after them, but not a Terraform group: the pre-provisioned track has no
# state. Its whole deployment is the three API keys runs/pre-provisioned/
# deployment.env records, all owned by the developer, and its teardown deletes
# exactly those over REST, then the card. The environment, and everything the
# platform team built in it, stays; the teardown says so before and after, so
# nobody expects (or fears) the environment going with it.
PREPROVISIONED = "pre-provisioned"
PREPROVISIONED_DESC = "solo, in your platform team's environment (your API keys only; the environment stays)"

# Which deployment track each Terraform tier belongs to, so a successful destroy
# can clean up everything that track left behind (see _retire_tier). Derived from
# the Track definitions rather than re-listed here, so the run directory, the tier
# name, and the prefix suffix can never drift apart. `aws-shared` owns no card and
# no track — it is shared infrastructure. The pre-provisioned track has no tier;
# it retires itself (see _destroy_preprovisioned).
TIER_TRACKS = {track.tier: track for track in TRACKS.values() if track is not meta.PREPROVISIONED}


def _retire_tier(root: Path, tier: str) -> None:
    """Clean up what a destroyed tier leaves behind on disk. Success path only.

    Clearing the `F1_CARD` pointer is not enough on its own. The card *files*
    used to stay put, so on a machine that has used both solo tracks, tearing
    one down left two cards and no pointer — and `resolve_card()` refuses to
    guess between them, so **every** attendee tool hard-exited with "Multiple
    credential cards found" while exactly one live environment existed. Deleting
    the dead track's cards restores the single-candidate case resolution needs.

    Also removes the self-service `.seeded` marker, which used to survive
    `uv run destroy` (only the old `destroy` unlinked it). The next
    self-service deploy then printed "already seeded" over an empty
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


# --- the pre-provisioned track: three API keys, no Terraform ---------------------------


def _environment_label(saved: dict[str, str]) -> str:
    """'<name> (<id>)' of the pre-provisioned environment, from deployment.env alone."""
    env_id, name = saved.get(meta.KEY_ENVIRONMENT_ID, ""), saved.get(meta.KEY_ENVIRONMENT_NAME, "")
    if env_id and name:
        return f"{name} ({env_id})"
    return env_id or name or "your environment"


def _key_list(ids: dict[str, str]) -> list[str]:
    """'Kafka key <id>', ... for recorded key IDs, in the order given."""
    from scripts.preprovisioned.provision import KEY_LABELS

    return [f"{KEY_LABELS[field]} key {key_id}" for field, key_id in ids.items()]


def _preprovisioned_group(root: Path) -> tuple[str, list[str], str] | None:
    """The pre-provisioned entry for the deployment list; None when no key is recorded.

    Its middle element lists the keys, where the Terraform groups list tiers.
    `main` shows it but never walks it as tiers.
    """
    ids = meta.recorded_key_ids(root, meta.PREPROVISIONED)
    return (PREPROVISIONED, _key_list(ids), PREPROVISIONED_DESC) if ids else None


def _explain_preprovisioned(root: Path) -> None:
    """Say what the pre-provisioned teardown deletes, and what it keeps, before it runs."""
    label = _environment_label(meta.load_meta(root, meta.PREPROVISIONED))
    ids = meta.recorded_key_ids(root, meta.PREPROVISIONED)
    print(f"\nWill delete your pre-provisioned keys and credential card: {', '.join(_key_list(ids))}.")
    print(f"  Your environment {label} and everything in it stay — they belong to your platform team.")


def _card_global_key(root: Path, saved: dict[str, str]) -> tuple[str, str] | None:
    """(key, secret) of the Global key on the card deployment.env records; None without one.

    That key authenticates every deletion, its own last. It's the card's Flink key.
    """
    from scripts.preprovisioned.provision import CARD_FIELDS

    card = root / saved[meta.KEY_CARD] if saved.get(meta.KEY_CARD) else None
    if card is None or not card.is_file():
        return None
    values = dotenv_values(card)
    key_field, secret_field = CARD_FIELDS[meta.KEY_GLOBAL_KEY_ID]
    key, secret = values.get(key_field), values.get(secret_field)
    return (key, secret) if key and secret else None


def _retire_preprovisioned(root: Path, label: str) -> None:
    """Forget the pre-provisioned deployment: card, deployment.env, F1_CARD. Its keys are gone."""
    removed = retire_track(root, meta.PREPROVISIONED)
    if removed:
        print(f"  Removed dead {meta.PREPROVISIONED.name} files: {', '.join(removed)}")
    print(f"  Kept your environment {label} and everything in it.")


def _delete_by_hand(root: Path, ids: dict[str, str], label: str, force: bool) -> bool:
    """When nothing can authenticate the deletes: list the keys for the Console, offer to forget them.

    The record stays unless the developer says they're gone (never under
    --force): it's all that names them, and a re-deploy deletes what it records.
    """
    from scripts.preprovisioned.provision import KEY_LOCATION

    record = meta.meta_path(root, meta.PREPROVISIONED).relative_to(root)
    print(f"  Delete them yourself under {KEY_LOCATION}:")
    for key in _key_list(ids):
        print(f"    {key}")
    if not force:
        answer = input(f"\n  Once they're deleted there, forget them here (your card and {record})? (y/n) [n]: ")
        if answer.strip().lower() in ("y", "yes"):
            _retire_preprovisioned(root, label)
            return True
    print(f"  Kept {record}. Once they're deleted, re-run `uv run destroy` (without --force) and answer y.")
    return False


def _destroy_preprovisioned(root: Path, force: bool) -> bool:
    """Delete the keys deployment.env records, Global last, then the card. True when all are gone.

    Over REST, authenticated with the card's Global key, which may delete
    itself. Each ID leaves deployment.env the moment its key is gone, "already
    gone" included, so a retry deletes only what's left. Any failure keeps the
    rest of the record and the card, and keeps the Global key too: a retry needs
    it to delete the others. Nothing here touches the environment: the developer's
    own statements in it keep running, as the user, and the platform team's
    teardown copes with them.
    """
    from scripts.preprovisioned import keys
    from scripts.preprovisioned.provision import KEY_LABELS, KEY_LOCATION

    track = meta.PREPROVISIONED
    saved = meta.load_meta(root, track)
    ids = meta.recorded_key_ids(root, track)
    label = _environment_label(saved)
    print("\n-> Deleting your pre-provisioned keys...")

    auth = _card_global_key(root, saved)
    if auth is None:
        print("  Can't delete them: your credential card, whose Global key authenticates the deletes, is missing.")
        return _delete_by_hand(root, ids, label, force)
    try:
        usable = keys.key_exists(auth, auth[0])
    except requests.RequestException as e:
        print(f"  Could not reach the Confluent Cloud API: {e}")
        print("  Nothing was deleted. Re-run `uv run destroy`.")
        return False
    if not usable:
        print(f"  Can't delete them: your card's Global key {auth[0]} is no longer accepted (deleted or revoked?).")
        return _delete_by_hand(root, ids, label, force)

    left: dict[str, str] = {}
    for field, key_id in ids.items():
        key = f"{KEY_LABELS[field]} key {key_id}"
        if field == meta.KEY_GLOBAL_KEY_ID and left:
            print(f"  Not deleting {key} yet: a retry needs it to delete the others.")
            left[field] = key_id
            continue
        result = keys.delete_api_key(auth, key_id)
        if result == "failed":
            print(f"  Could not delete {key}.")
            left[field] = key_id
            continue
        meta.drop_meta(root, track, field)
        print(f"  Deleted {key}." if result == "deleted" else f"  {key}: already gone.")

    if left:
        print(f"\n  Still to delete: {', '.join(_key_list(left))}.")
        print(f"  They stay recorded in {meta.meta_path(root, track).relative_to(root)}, and your card stays.")
        print(f"  Re-run `uv run destroy` to retry, or delete them under {KEY_LOCATION}.")
        return False
    _retire_preprovisioned(root, label)
    return True


# Project-local Claude Code registrations made by `uv run setup-mcp` and
# `uv run setup-rtce` (their _SERVER_NAME), plus the env file setup-mcp writes.
MCP_SERVERS = ("confluent-cloud-mcp-server", "real-time-context-engine")
MCP_ENV_FILE = "confluent-mcp.env"


def _cleanup_mcp(root: Path) -> None:
    """Drop this project's MCP registrations; they point at a destroyed environment.

    Best-effort: a missing `claude` CLI or an absent registration is not an error.
    Codex entries live in the user-global ~/.codex/config.toml and may serve other
    projects, so they are only mentioned, never edited.
    """
    env_file = root / MCP_ENV_FILE
    if env_file.exists():
        env_file.unlink()
        print(f"  Removed {MCP_ENV_FILE}")
    if shutil.which("claude"):
        for name in MCP_SERVERS:
            result = subprocess.run(
                ["claude", "mcp", "remove", name, "-s", "local"], cwd=root, capture_output=True, text=True
            )
            if result.returncode == 0:
                print(f"  Removed Claude Code MCP server '{name}'")
    if (Path.home() / ".codex" / "config.toml").exists():
        print(f"  Codex: remove {', '.join(MCP_SERVERS)} from ~/.codex/config.toml if you registered them.")


def _offer_credentials_cleanup(creds_file: Path, args: argparse.Namespace, still_deployed: bool) -> None:
    """Ask whether to delete credentials.env too.

    Only after a fully successful destroy. It holds the Confluent API key, the
    saved Console login, and the Bedrock keys, so a finished attendee shouldn't
    leave it behind (default yes). It also feeds the other deployment's teardown,
    so while anything is still deployed the default flips to keep it. `--force`
    keeps it unless `--delete-credentials` is also passed.
    """
    if not creds_file.exists():
        return
    if args.delete_credentials:
        delete = True
    elif args.force:
        delete = False
    else:
        if still_deployed:
            print("\nNote: credentials.env also backs a deployment that is still running here.")
        default = "n" if still_deployed else "y"
        answer = (
            input(
                "\nAlso delete credentials.env (API keys, Confluent login, Bedrock keys)? "
                "(y/n — choose n if you plan to re-deploy using these credentials after tearing down) "
                f"[{default}]: "
            )
            .strip()
            .lower()
            or default
        )
        delete = answer == "y"
    if delete:
        creds_file.unlink()
        print(f"Deleted {creds_file.name}.")
    else:
        print(f"Kept {creds_file.name} — `uv run deploy` will reuse it.")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="destroy", description="Tear down what `uv run deploy` created here")
    parser.add_argument(
        "--force",
        "--yes",
        action="store_true",
        help="No prompts: destroy every deployment found (refuses workshop-looking shared infra)",
    )
    parser.add_argument(
        "--only",
        choices=[*(name for name, _tiers, _desc in GROUPS), PREPROVISIONED],
        help="Consider only this deployment group (`uv run selfservice down` passes self-service)",
    )
    parser.add_argument(
        "--delete-credentials",
        action="store_true",
        help="Also delete credentials.env without asking (with --force it is kept otherwise)",
    )
    return parser.parse_args(argv)


def _select_groups(available: list[tuple[str, list[str], str]]) -> list[tuple[str, list[str], str]]:
    """Prompt for which deployments to destroy. Enter accepts all.

    Always lists what was found — with one deployment there's nothing to choose,
    but the user still needs to see which tiers it covers before confirming.
    """
    print("\nFound deployments:")
    for i, (name, tiers, desc) in enumerate(available, 1):
        print(f"  {i}. {name:<16} {desc}")
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


def main(argv: list[str] | None = None):
    """Main entry point for destroy."""
    args = _parse_args(argv)
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
        if args.only and name != args.only:
            continue
        present = [t for t in tiers if (root / "terraform" / t / "terraform.tfstate").exists()]
        if not present:
            continue
        # A "deploy" that is only the shared half was not produced by `uv run
        # deploy` (which always applies both). Don't advertise it as the demo —
        # this is the path _looks_like_workshop_shared() guards.
        if present == ["aws-shared"]:
            name, desc = "shared-infra", "shared infra ONLY — may back a workshop (see warning below)"
        available.append((name, present, desc))
    # ...and the pre-provisioned track, whose deployment is the keys it recorded.
    preprovisioned = _preprovisioned_group(root) if args.only in (None, PREPROVISIONED) else None
    if preprovisioned:
        available.append(preprovisioned)

    if not available:
        all_tiers = [t for n, tiers, _d in GROUPS if not args.only or n == args.only for t in tiers]
        looked = [f"no terraform state in: {', '.join(all_tiers)}"] if all_tiers else []
        if args.only in (None, PREPROVISIONED):
            looked.append(f"no keys recorded in {meta.meta_path(root, meta.PREPROVISIONED).relative_to(root)}")
        print(f"Nothing to destroy — {'; '.join(looked)}")
        print("(A wsa-provisioned workshop is torn down with `wsa clean`, not this script.)")
        sys.exit(0)

    selected = available if args.force else _select_groups(available)
    envs_to_destroy = [t for n, tiers, _d in selected if n != PREPROVISIONED for t in tiers]

    if envs_to_destroy:
        print(f"\nWill destroy: {', '.join(envs_to_destroy)}")
        print("WARNING: This will permanently destroy all resources!")
    if preprovisioned in selected:
        _explain_preprovisioned(root)

    if not args.force and input("\nProceed? (y/n): ").strip().lower() != "y":
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
        if args.force:
            # Unattended is exactly when this must not happen by reflex.
            print("\n--force will not destroy this. Re-run without --force to confirm by hand.")
            sys.exit(1)
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
        if group_name == PREPROVISIONED:
            # No tiers to walk: its "tiers" are the keys, deleted over REST.
            if not _destroy_preprovisioned(root, args.force):
                failed.append(f"{PREPROVISIONED} (API keys)")
            continue
        for position, env in enumerate(tiers):
            env_path = root / "terraform" / env
            state_file = env_path / "terraform.tfstate"

            # Every non-shared tier has required variables that Terraform evaluates
            # even on destroy — populate them before running.
            if env == "aws":
                # shared_* variables come from the aws-shared outputs.
                _inject_shared_vars(root)
            elif env == "self-service":
                # Reuse the TF_VAR set the self-service track of `uv run deploy` builds.
                from scripts.selfservice.provision import export_selfservice_tf_env

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

    # MCP servers only once nothing at all is deployed from here: they may still
    # serve a deployment that wasn't selected. "Deployed" is Terraform state, or —
    # for the pre-provisioned track — keys still recorded in its deployment.env.
    shared_live = (root / "terraform" / "aws-shared" / "terraform.tfstate").exists()
    still_deployed = shared_live or any(is_deployed(root, track) for track in TRACKS.values())
    if not still_deployed:
        _cleanup_mcp(root)
    _offer_credentials_cleanup(_creds_file, args, still_deployed)


if __name__ == "__main__":
    main()
