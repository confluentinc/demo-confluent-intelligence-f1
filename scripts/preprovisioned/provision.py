"""Pre-provisioned track of `uv run deploy` — the self-service workshop inside a
customer's own Confluent Cloud organization.

  confluent login                                    # first, in your terminal (SSO)
  uv run deploy --track pre-provisioned              # mint your keys, write a
                                                     # credential card, check
                                                     # driver_race_history
  uv run deploy --track pre-provisioned --with-labs  # ...and prebuild LAB 3 + LAB 4
  uv run destroy                                     # delete your keys; the environment stays

The customer's platform team creates one environment per developer, binds the
developer to it directly as EnvironmentAdmin, and builds everything inside it
(platform-team/): the cluster, the Flink pool, the tables, the model and the
seed. The developer runs no Terraform. This deploy finds what was built, mints
the developer's keys, and writes the card:

- a **Global** key, minted with the Confluent CLI as the logged-in developer. It
  is the key the deploy asks for the developer's user ID, email and environment,
  the one that mints the other two, and the card's Flink and RTCE key;
- a **Kafka** and a **Schema Registry** key owned by the developer, minted over
  REST with the Global key.

Their IDs go to runs/pre-provisioned/deployment.env the moment each exists; that
record *is* this track's deployment, for re-runs and for `uv run destroy`. A run
that stops before the card is written deletes the keys it minted, so a developer
who deploys before the platform team is ready doesn't burn one of their two
Global keys per attempt. A re-run whose card keys all still exist reuses the card;
otherwise it mints three new keys and deletes the recorded ones that are left.

There are no service accounts and no `offer_key_generation` here — that mints an
OrganizationAdmin service account, which a developer in a customer org neither can
nor should. Everything after the card — the seed check, `--with-labs`,
`uv run f1-race` — is the self-service flow unchanged.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import dotenv_values

from scripts.common import deploy_common as common
from scripts.common import deployment_meta as meta
from scripts.common.credentials import set_active_card
from scripts.common.login_checks import ensure_confluent_login
from scripts.common.simulator_control import create_lab_objects
from scripts.common.ui import prompt_with_default
from scripts.preprovisioned import keys, lookup
from scripts.selfservice.seed import ensure_driver_race_history

TRACK = meta.PREPROVISIONED

# Fixed: the platform team names everything `RIVER-RACING-DEMO-*` (one developer
# per environment leaves nothing to namespace). Here it only names the credential
# card and fills its F1_PREFIX.
PREFIX = "DEMO"
CLUSTER_NAME = lookup.CLUSTER_NAME

# Where the developer sees, and can delete, the keys this deploy mints.
KEY_LOCATION = "Console → API keys → My account"

# The card field that carries each recorded key: (key, secret).
CARD_FIELDS = {
    meta.KEY_GLOBAL_KEY_ID: ("F1_FLINK_API_KEY", "F1_FLINK_API_SECRET"),
    meta.KEY_KAFKA_KEY_ID: ("F1_KAFKA_API_KEY", "F1_KAFKA_API_SECRET"),
    meta.KEY_SR_KEY_ID: ("F1_SR_API_KEY", "F1_SR_API_SECRET"),
}
KEY_LABELS = {
    meta.KEY_GLOBAL_KEY_ID: "Global",
    meta.KEY_KAFKA_KEY_ID: "Kafka",
    meta.KEY_SR_KEY_ID: "Schema Registry",
}


# --- re-runs: reuse the card while its keys exist ---------------------------------


def _reusable_card(root: Path) -> Path | None:
    """The last deploy's card, if every key it carries still exists; else None.

    "Still exists" is asked of the API with the card's own Global key
    (`GET /iam/v2/api-keys/{id}`), so a revoked Global key fails the check too.
    The card must carry exactly the keys deployment.env records. A network
    failure stops the run rather than counting as "gone": minting would then
    delete keys that are fine.
    """
    saved = meta.load_meta(root, TRACK)
    ids = meta.recorded_key_ids(root, TRACK)
    card_path = root / saved[meta.KEY_CARD] if saved.get(meta.KEY_CARD) else None
    if len(ids) < len(CARD_FIELDS) or card_path is None or not card_path.is_file():
        return None
    card = {k: v or "" for k, v in dotenv_values(card_path).items()}
    if any(card.get(key) != ids[field] or not card.get(secret) for field, (key, secret) in CARD_FIELDS.items()):
        return None

    print("\nChecking the keys on your credential card...")
    auth = (card["F1_FLINK_API_KEY"], card["F1_FLINK_API_SECRET"])
    try:
        # The Global key first: if it's gone, it can vouch for nothing else.
        alive = all(keys.key_exists(auth, ids[field]) for field in reversed(meta.KEY_ID_FIELDS))
    except requests.RequestException as e:
        print(f"\nError: could not reach the Confluent Cloud API to check them: {e}")
        sys.exit(1)
    if not alive:
        print("  At least one of them no longer exists — minting new keys.")
        return None
    return card_path


# --- identity and environment ----------------------------------------------


def _require_cli_login(creds: dict) -> None:
    """Stop unless the Confluent CLI has a session: it mints the Global key, as the developer."""
    if ensure_confluent_login(creds, interactive=False):
        return
    print("\nThis track mints your Global key with the Confluent CLI, as you. Run")
    print("`confluent login` in this terminal (it opens your SSO sign-in), then re-run.")
    sys.exit(1)


def _lookup(auth: tuple[str, str]) -> tuple[str, list[lookup.Environment]]:
    """(user ID, visible environments) for the Global key."""
    print("\nLooking up your account and environment with the Global key...")
    user_id = lookup.resolve_user_id(*auth)
    envs = lookup.visible_environments(*auth)
    print(f"  User:         {user_id}")
    return user_id, envs


def _check_user(saved: dict[str, str], old: dict[str, str], user_id: str) -> None:
    """Refuse to replace one user's recorded keys with another user's.

    The recorded keys belong to the user the deployment was built as; a new key
    of someone else's couldn't delete them, and the card would change hands.
    """
    built_as = saved.get(meta.KEY_USER_ID)
    if old and built_as and built_as != user_id:
        print(f"\nError: this deployment's keys belong to {built_as}, but the Confluent CLI is logged in as {user_id}.")
        print("  Log in as that account (`confluent login`), or delete its keys first with `uv run destroy`.")
        sys.exit(1)


def _email(auth: tuple[str, str], user_id: str, automated: bool) -> str:
    """The developer's email, from their user; asked for only if the API won't say."""
    email = lookup.user_email(*auth, user_id)
    if email:
        print(f"  Email:        {email}")
        return email
    if automated:
        print(f"\nError: Confluent Cloud didn't return the email of {user_id}, and --automated can't ask.")
        print("  Run `uv run deploy --track pre-provisioned` without --automated to type it in.")
        sys.exit(1)
    return prompt_with_default("Your email (the one you sign in to Confluent Cloud with)")


def _organization_hint() -> str:
    """Point at `confluent login --organization` when the CLI user may be in the wrong org.

    The Global key, and so every lookup, belongs to the organization the CLI is
    logged in to. Empty when the CLI reports a single organization.
    """
    try:
        result = subprocess.run(
            ["confluent", "organization", "list", "-o", "json"], capture_output=True, text=True, timeout=60
        )
        orgs = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        orgs = None
    if not isinstance(orgs, list):
        return (
            "\n\nIf you belong to several Confluent Cloud organizations, the CLI may be logged in to\n"
            "the wrong one: run `confluent login --organization <id>`, then re-run."
        )
    orgs = [o for o in orgs if isinstance(o, dict)]
    if len(orgs) <= 1:
        return ""
    listed = "\n".join(
        f"    {o.get('id', '')}  {o.get('name', '')}{'  (logged in)' if o.get('is_current') else ''}" for o in orgs
    )
    return (
        f"\n\nYou belong to {len(orgs)} Confluent Cloud organizations. If your environment is in\n"
        f"another one, run `confluent login --organization <id>`, then re-run:\n{listed}"
    )


def _confirm(env: lookup.Environment) -> bool:
    return input(f"Deploy into {env.name} ({env.id})? (y/n) [y]: ").strip().lower() in ("", "y", "yes")


def _pick(candidates: tuple[lookup.Environment, ...]) -> lookup.Environment:
    print(f"\n  Your key sees {len(candidates)} environments:")
    for i, env in enumerate(candidates, 1):
        print(f"    {i}. {env.name}  ({env.id})")
    while True:
        choice = input(f"Which one is yours? (1-{len(candidates)}): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(candidates):
            env = candidates[int(choice) - 1]
            print(f"  Environment:  {env.name} ({env.id})")
            return env
        print(f"  Please enter a number between 1 and {len(candidates)}.")


def _resolve_environment(
    saved: str, locked: bool, envs: list[lookup.Environment], email: str, automated: bool
) -> lookup.Environment:
    """The environment to deploy into. Never silently retargets a live deployment.

    `saved` is the environment ID a previous run recorded; `locked` says its
    keys were minted there. Precedence: that environment when locked, then the
    saved one — if the key still sees it — then `lookup.choose_environment`. A
    decided environment is shown and, interactively, confirmed; a pick-list is
    prompted, or fails under --automated.
    """
    by_id = {env.id: env for env in envs}
    if locked:
        env = by_id.get(saved)
        if env is None:
            print(f"\nError: your keys from the last deploy are for {saved}, which your Global key no longer sees.")
            print("  Ask your platform team to restore your direct EnvironmentAdmin binding, then re-run,")
            print("  or delete those keys first with `uv run destroy`.")
            sys.exit(1)
        print(f"  Environment:  {env.name} ({env.id})  (already deployed — cannot change without tearing down first)")
        return env

    if saved and saved in by_id:
        choice, source = lookup.Choice(env=by_id[saved]), f"reused from runs/{TRACK.name}/deployment.env"
    else:
        if saved:
            print(f"  runs/{TRACK.name}/deployment.env records {saved}, which your key no longer sees — looking again.")
        try:
            choice = lookup.choose_environment(envs, email)
        except lookup.NoEnvironmentError as e:
            raise lookup.NoEnvironmentError(f"{e}{_organization_hint()}") from None
        source = "the only environment your key sees" if len(envs) == 1 else f"named after {email}"

    if choice.env is not None:
        print(f"  Environment:  {choice.env.name} ({choice.env.id})  ({source})")
        if automated or _confirm(choice.env):
            return choice.env
        if len(envs) == 1:
            print("Cancelled — your key sees no other environment. Ask your platform team if this one isn't yours.")
            sys.exit(0)
        return _pick(tuple(sorted(envs, key=lambda e: e.name.lower())))

    if automated:
        print(f"\nError: your Global key sees {len(choice.candidates)} environments, and none is uniquely")
        print(f"named after {email}:")
        for env in choice.candidates:
            print(f"    {env.name}  ({env.id})")
        print("--automated can't choose. Run `uv run deploy --track pre-provisioned` once without")
        print("--automated and pick yours; later --automated runs reuse the saved choice.")
        sys.exit(1)
    return _pick(choice.candidates)


# --- minting -------------------------------------------------------------------------


def _explain(saved: dict[str, str], replacing: bool, automated: bool) -> None:
    """Say what the run creates (and, re-minting, deletes) before it does anything."""
    env_id, name = saved.get(meta.KEY_ENVIRONMENT_ID), saved.get(meta.KEY_ENVIRONMENT_NAME)
    env = f"{name} ({env_id})" if env_id and name else "your environment"
    print("\nThis creates your Global, Kafka and Schema Registry keys. It creates nothing in")
    print(f"{env} except the driver_race_history rows if they're missing;")
    print("your platform team provisioned the rest.")
    if replacing:
        print("The new keys replace your card's; the old ones still left are deleted afterwards.")
    if not automated and input("Continue? (y/n) [y]: ").strip().lower() not in ("", "y", "yes"):
        print("Cancelled.")
        sys.exit(0)


def _record(root: Path, minted: dict[str, str], field: str, key_id: str) -> None:
    """Note a key the moment it exists, in this run's list and in deployment.env."""
    minted[field] = key_id
    meta.save_meta(root, TRACK, **{field: key_id})


def _roll_back(root: Path, auth: tuple[str, str], minted: dict[str, str], old: dict[str, str]) -> None:
    """Delete the keys this run minted (Global last) and restore the previous run's record.

    Called when a run stops before writing the card: nothing else was created,
    so this leaves things as they were — and the developer keeps both Global-key
    slots for the re-run.
    """
    print("\nDeleting the keys this run minted (nothing else was created)...")
    for field in meta.KEY_ID_FIELDS:
        key_id = minted.get(field)
        if key_id is None:
            continue
        if keys.delete_api_key(auth, key_id) == "failed":
            print(f"  Could not delete {KEY_LABELS[field]} key {key_id}: delete it under {KEY_LOCATION}.")
        else:
            print(f"  Deleted {KEY_LABELS[field]} key {key_id}.")
    meta.drop_meta(root, TRACK, *minted)
    if old:
        meta.save_meta(root, TRACK, **old)  # the previous card's keys, still on that card


def _delete_replaced(auth: tuple[str, str], old: dict[str, str], minted: dict[str, str]) -> None:
    """Delete whichever keys the previous card carried that still exist (Global last)."""
    stale = {field: key_id for field, key_id in old.items() if minted.get(field) != key_id}
    if not stale:
        return
    print("\nDeleting the keys your previous card carried...")
    for field, key_id in stale.items():
        result = keys.delete_api_key(auth, key_id)
        if result == "deleted":
            print(f"  Deleted {KEY_LABELS[field]} key {key_id}.")
        elif result == "failed":
            print(f"  Could not delete {KEY_LABELS[field]} key {key_id}: delete it under {KEY_LOCATION}.")


def _outputs(
    env: lookup.Environment,
    found: lookup.Resources,
    user_id: str,
    global_key: tuple[str, str],
    kafka_key: tuple[str, str],
    sr_key: tuple[str, str],
) -> dict:
    """The card's inputs, shaped like terraform/self-service's outputs so `write_card` is unchanged.

    The Flink and RTCE fields hold the Global key; `service_account_id` carries
    the user ID (a principal ID — the card itself never shows it).
    """
    return {
        "organization_id": found.organization_id,
        "environment_id": env.id,
        "environment_name": env.name,
        "cluster_id": found.cluster_id,
        "cluster_name": found.cluster_name,
        "user_id": user_id,
        "service_account_id": user_id,
        "cluster_bootstrap": found.cluster_bootstrap,
        "compute_pool_id": found.compute_pool_id,
        "flink_rest_endpoint": found.flink_rest_endpoint,
        "flink_api_key": global_key[0],
        "flink_api_secret": global_key[1],
        "attendee_credentials": {
            "environment_id": env.id,
            "environment_name": env.name,
            "environment_url": f"https://confluent.cloud/environments/{env.id}",
            "cluster_id": found.cluster_id,
            "cluster_bootstrap": found.cluster_bootstrap,
            "compute_pool_id": found.compute_pool_id,
            "service_account_id": user_id,
            "kafka_api_key": kafka_key[0],
            "kafka_api_secret": kafka_key[1],
            "schema_registry_url": found.schema_registry_url,
            "sr_api_key": sr_key[0],
            "sr_api_secret": sr_key[1],
            "flink_api_key": global_key[0],
            "flink_api_secret": global_key[1],
        },
        "rtce_api_key": global_key[0],
        "rtce_api_secret": global_key[1],
    }


def _build(
    root: Path,
    auth: tuple[str, str],
    minted: dict[str, str],
    saved: dict[str, str],
    old: dict[str, str],
    seconds_per_lap: str,
    automated: bool,
) -> Path:
    """Everything after the Global key exists, up to and including the card."""
    print("  Waiting for it to become active (a new key can take a couple of minutes)...")
    if not keys.wait_until(lambda: keys.key_exists(auth, auth[0])):
        print(f"\nError: your new Global key still isn't accepted after {keys.PROPAGATION_TIMEOUT}s. Re-run shortly.")
        sys.exit(1)

    user_id, envs = _lookup(auth)
    _check_user(saved, old, user_id)
    email = _email(auth, user_id, automated)
    env = _resolve_environment(saved.get(meta.KEY_ENVIRONMENT_ID, ""), bool(old), envs, email, automated)
    meta.save_meta(
        root,
        TRACK,
        **{
            meta.KEY_RESOLVED_PREFIX: PREFIX,
            meta.KEY_SECONDS_PER_LAP: seconds_per_lap,
            meta.KEY_ENVIRONMENT_ID: env.id,
            meta.KEY_ENVIRONMENT_NAME: env.name,
            meta.KEY_USER_ID: user_id,
        },
    )

    print(f"\nLooking for what your platform team built in {env.name}...")
    found = lookup.find_resources(*auth, env)
    meta.save_meta(root, TRACK, **{meta.KEY_REGION: found.region})
    print(f"  Cluster:      {found.cluster_name} ({found.cluster_id}), {found.cloud} {found.region}")
    print(f"  Flink pool:   {lookup.POOL_NAME} ({found.compute_pool_id})")
    print(f"  Schema Reg.:  {found.schema_registry_id}")

    print("\nMinting your Kafka and Schema Registry keys...")
    kafka = keys.create_api_key(auth, user_id, found.cluster_id, env.id, "RIVER-RACING-DEMO-kafka-key")
    _record(root, minted, meta.KEY_KAFKA_KEY_ID, kafka[0])
    sr = keys.create_api_key(auth, user_id, found.schema_registry_id, env.id, "RIVER-RACING-DEMO-sr-key")
    _record(root, minted, meta.KEY_SR_KEY_ID, sr[0])
    print(f"  Kafka key:    {kafka[0]}")
    print(f"  SR key:       {sr[0]}")
    print("  Waiting for them to become active (up to 3 minutes)...")
    if not keys.wait_until(lambda: keys.kafka_key_works(found.cluster_rest_endpoint, found.cluster_id, *kafka)):
        print(f"\nError: your new Kafka key still can't list topics after {keys.PROPAGATION_TIMEOUT}s. Re-run shortly.")
        sys.exit(1)
    if not keys.wait_until(lambda: keys.schema_registry_key_works(found.schema_registry_url, *sr)):
        print(
            f"\nError: your new Schema Registry key still can't list subjects after {keys.PROPAGATION_TIMEOUT}s. "
            "Re-run shortly."
        )
        sys.exit(1)

    out = _outputs(env, found, user_id, auth, kafka, sr)
    cfg = {"prefix": PREFIX, "owner_email": email}
    return common.write_card(root, TRACK, cfg, out, rtce_keys=True, region=found.region)


def _provision(root: Path, creds: dict, seconds_per_lap: str, automated: bool) -> Path:
    """Mint the developer's three keys and write the card; returns the card's path.

    A run that stops before the card exists — for any reason, Ctrl-C included —
    deletes the keys it minted and puts the previous record back. Once the card
    is written, the keys the previous card carried are deleted.
    """
    saved = meta.load_meta(root, TRACK)
    old = meta.recorded_key_ids(root, TRACK)
    _require_cli_login(creds)
    _explain(saved, bool(old), automated)

    print("\nMinting your Global key with the Confluent CLI...")
    auth = keys.mint_global_key(previous=old.get(meta.KEY_GLOBAL_KEY_ID, ""))
    minted: dict[str, str] = {}
    _record(root, minted, meta.KEY_GLOBAL_KEY_ID, auth[0])
    print(f"  Global key:   {auth[0]}")
    try:
        card_path = _build(root, auth, minted, saved, old, seconds_per_lap, automated)
    except requests.RequestException as e:
        _roll_back(root, auth, minted, old)
        print(f"\nError: could not reach the Confluent Cloud API: {e}")
        sys.exit(1)
    except BaseException:
        _roll_back(root, auth, minted, old)
        raise
    _delete_replaced(auth, old, minted)
    return card_path


def _outputs_from_card(card: dict[str, str]) -> dict[str, str]:
    """The Terraform-output-shaped keys `create_lab_objects` and the Ready output read, from the card."""
    return {
        "organization_id": card.get("F1_ORGANIZATION_ID", ""),
        "environment_id": card.get("F1_ENVIRONMENT_ID", ""),
        "environment_name": card.get("F1_CATALOG", ""),
        "cluster_name": card.get("F1_DATABASE", ""),
        "compute_pool_id": card.get("F1_COMPUTE_POOL_ID", ""),
        "flink_rest_endpoint": card.get("F1_FLINK_REST_ENDPOINT", ""),
        "flink_api_key": card.get("F1_FLINK_API_KEY", ""),
        "flink_api_secret": card.get("F1_FLINK_API_SECRET", ""),
        "rtce_api_key": card.get("F1_RTCE_API_KEY", ""),
    }


# --- deploy ------------------------------------------------------------------------


def deploy(args: argparse.Namespace, root: Path, creds_file: Path, creds: dict) -> None:
    print("\n=== F1 Workshop — Pre-provisioned (your environment, your organization's Confluent Cloud) ===")
    if getattr(args, "bedrock", False):
        print("\nNote: --bedrock doesn't apply here; the model is your platform team's (CSP Hosted).")
    seconds_per_lap = common.validated_pacing_or_exit(common.seconds_per_lap_default(root, TRACK, creds), TRACK)

    card_path = _reusable_card(root)
    if card_path is not None:
        print(f"  Reusing {card_path.relative_to(root)}: its keys all still exist, so this run creates none.")
        meta.save_meta(root, TRACK, **{meta.KEY_SECONDS_PER_LAP: seconds_per_lap})
        set_active_card(root, card_path)
    else:
        card_path = _provision(root, creds, seconds_per_lap, args.automated)
        print(f"\nCredential card: {card_path}  (recorded as F1_CARD in credentials.env)")

    card = {k: v or "" for k, v in dotenv_values(card_path).items()}
    out = _outputs_from_card(card)

    print("\n=== driver_race_history ===")
    seeded = ensure_driver_race_history(card, root, TRACK)

    labs_ok = True
    if args.with_labs:
        # As in self-service: the race is the local `uv run f1-race`, started
        # after this returns, so the labs are RUNNING before any standings row.
        print("\n=== Prebuilding the labs (--with-labs) ===")
        labs_ok = create_lab_objects(out, root)
        if not labs_ok:
            print("\nLab build FAILED — see the error above. Your keys and card are fine; fix the")
            print("SQL problem and re-run `uv run deploy --track pre-provisioned --with-labs`.")

    labs_live = args.with_labs and labs_ok
    catalog = out["environment_name"]
    print("\n=== Ready ===\n")
    print(f"Flink SQL Workspace:  {common.flink_workspace_url(out)}")
    print(f"  Catalog: {catalog}   (your environment — not RIVER-RACING-{PREFIX}-ENV)")
    print(f"  Database: {out['cluster_name'] or CLUSTER_NAME}\n")
    if labs_live:
        print("LAB 3 and LAB 4 are already running — they start filling as soon as the race does.")
        print("  `car_state` stays empty for ~4 min while anomaly detection fills its")
        print("  first 12 windows of context. The anomaly fires around lap 24.\n")
    print("1. Start the live race feed (leave running in its own terminal):")
    print(f"     uv run f1-race          # {seconds_per_lap}s/lap, from this deployment's config")
    if not labs_live:
        print("   Create `car_state` first (section 1) — laps raced before it is Running are lost.")
    print("2. Open the live dashboard:")
    print("     uv run f1-pitwall")
    if out["rtce_api_key"]:
        print("3. (optional) Connect an AI agent to the live feed via RTCE:")
        print("     uv run setup-rtce")
    if not labs_live:
        print(f"\nContinue in docs/tracks/SELF-SERVICE.md at section 1 (your catalog is {catalog}).")
    print("\nTear down when finished:  uv run destroy")
    print("  It deletes your three keys and this card; your environment and everything in it stay.")

    if not args.automated and seeded:
        common.offer_start(root, ["uv", "run", "f1-race"] if labs_live else None)

    if not (seeded and labs_ok):
        # Same contract as self-service: nonzero for a half-built deployment.
        sys.exit(1)
