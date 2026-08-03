#!/usr/bin/env python3
"""
Standalone deploy for a SINGLE F1 environment — the full-architecture demo.

Provisions the two-tier layout for one attendee prefix:
  1. terraform/aws-shared  — shared VPC/subnets, Postgres, ECR + simulator image
  2. terraform/aws         — one Confluent environment + ECS simulator, wired to
                             the shared outputs

For a real multi-attendee workshop, use `wsa` with wsa-spec-aws.yaml instead —
this script is the manual single-environment equivalent. Nothing here is read by
`wsa`, which is why the cost and naming defaults below can differ from the
workshop's without changing workshop behavior.

Usage:
  uv run deploy                        # prompts, defaults resolved from identity
  uv run deploy --automated            # no prompts; a *bare* environment
  uv run deploy --automated --with-labs  # ready-to-demo: LAB 3 + LAB 4 prebuilt

`--automated` and `--with-labs` are deliberately orthogonal: `--automated` means
"don't prompt", not "build the labs". A smoke test wants a bare environment.
"""

import argparse
import json
import os
import sys

from dotenv import set_key

from scripts.common import deployment_meta as meta
from scripts.common.credentials import (
    generate_confluent_api_keys,
    load_or_create_credentials_file,
    set_active_card,
)
from scripts.common.login_checks import (
    check_aws_configured,
    check_docker_running,
    check_terraform_installed,
    ensure_confluent_login,
)
from scripts.common.simulator_control import (
    create_lab_objects,
    scale_simulator,
    wait_for_drain,
    wait_for_running,
)
from scripts.common.terraform import get_project_root, run_terraform_output
from scripts.common.terraform_runner import run_terraform
from scripts.common.ui import prompt_with_default
from scripts.workshop import creds as creds_mod

TRACK = meta.STANDALONE

# Credential cards for this flow land in runs/<RUN_NAME>/credentials/ (mirrors
# `uv run selfservice up`, which writes to runs/selfservice/).
RUN_NAME = TRACK.name

REGION = "us-east-1"

# The shared tier's *own* naming prefix — not the attendee prefix. `var.prefix` in
# terraform/aws-shared names the ECR repository (`${lower(var.prefix)}-simulator`,
# terraform/aws-shared/datagen.tf) and the Postgres security group, and ECR
# repository names are **account-global**. Pinning this to a constant meant the
# second person to run `uv run deploy` in a shared AWS account hit a hard
# `RepositoryAlreadyExistsException`, so it is derived from the resolved attendee
# prefix instead. Set F1_SHARED_PREFIX to pin a specific name (for example
# `F1_SHARED_PREFIX=f1-workshop` to keep pre-existing shared state as-is).
LEGACY_SHARED_PREFIX = "f1-workshop"
SHARED_PREFIX_ENV = "F1_SHARED_PREFIX"

# A t3.large + 30 GB gp3 running 24/7 to hold 198 historical rows costs ~$60/mo;
# t3.small is ~$15/mo and its 2 GB is still comfortable with one logical
# replication slot per attendee. The variable already exists in
# terraform/aws-shared/variables.tf (defaulting to t3.large) — the WSA workshop
# keeps that default, because wsa never reads this file. Export
# TF_VAR_postgres_instance_type to override.
DEFAULT_POSTGRES_INSTANCE_TYPE = "t3.small"

# Race pacing. terraform/aws defaults to 60 (a 60-lap race takes an hour, with
# the lap-32 anomaly ~32 minutes in) because that's the right pace for an
# instructor-led workshop. A standalone demo wants to reach the payoff sooner,
# so prompt for it and default lower. Below ~10s/lap anomaly detection can't
# accumulate its 20 windows before lap 32 and the anomaly never fires.
DEFAULT_SECONDS_PER_LAP = "20"
MIN_SECONDS_PER_LAP = meta.MIN_SECONDS_PER_LAP

# How resolve_prefix arrived at a value, phrased for the person reading the run.
_PREFIX_SOURCES = {
    "state": "already deployed — cannot be changed without tearing down first",
    "saved": f"reused from runs/{RUN_NAME}/deployment.env",
    "explicit": "from the exported TF_VAR_prefix",
    "derived": "derived from your identity, stable across re-runs",
}


def _seconds_per_lap_default(root, creds: dict) -> str:
    """Pacing to use when the user doesn't pick one.

    Precedence: an explicit TF_VAR_seconds_per_lap in the environment (so
    `export TF_VAR_seconds_per_lap=15; uv run deploy` still works), then this
    track's saved metadata (so the same value sticks across re-runs — including
    `--automated` re-runs, which used to persist nothing), then a legacy value in
    credentials.env, then the demo default.
    """
    return (
        os.environ.get("TF_VAR_seconds_per_lap")
        or meta.load_meta(root, TRACK).get(meta.KEY_SECONDS_PER_LAP)
        or creds.get("TF_VAR_seconds_per_lap")
        or DEFAULT_SECONDS_PER_LAP
    )


def _explicit_prefix() -> str | None:
    """A prefix the user asked for *deliberately*, or None.

    Only an **exported** `TF_VAR_prefix` counts. credentials.env's `TF_VAR_prefix`
    is deliberately NOT consulted: it is a single root file that both solo tracks
    used to write, so whichever ran last left its prefix in it — reading it back
    is precisely how standalone ended up deploying under self-service's name, and
    how two live environments both ended up called RIVER-RACING-PROD-ENV. Worse
    for item 10, two people whose credentials.env both said `solo` would derive
    the same account-global ECR repository name and one of them would fail hard.
    The saved per-track metadata replaces that file as the memory of "what this
    track used".
    """
    return (os.environ.get("TF_VAR_prefix") or "").strip() or None


def _resolve_prefix(root, owner_email: str, explicit: str | None) -> tuple[str, str]:
    """(prefix, source) for this track, or exit. Never renames a deployed track."""
    prefix, source, error = meta.resolve_prefix(root, TRACK, owner_email=owner_email, explicit=explicit)
    if error:
        print(f"\nError: {error}")
        sys.exit(1)
    # Live state wins over saved metadata, but say so rather than silently
    # correcting: metadata that disagrees with state means an interrupted run or a
    # hand-edited file, and the operator should know which name is authoritative.
    saved = meta.load_meta(root, TRACK).get(meta.KEY_RESOLVED_PREFIX)
    if source == "state" and saved and saved != prefix:
        print(f"\nNote: runs/{RUN_NAME}/deployment.env records {saved!r}, but the deployed")
        print(f"      environment is {prefix!r}. Using the deployed name and updating the record.")
    return prefix, source


def _validated_prefix_or_exit(prefix: str) -> str:
    """Reject an unusable prefix *before* any cloud call."""
    problem = meta.validate_prefix(prefix)
    if not problem:
        return prefix
    print(f"\nError: {problem}")
    if not prefix:
        print("$USER looks like a shared account and no owner email was available, so")
        print("there was nothing to derive one from. Export TF_VAR_prefix=<name> and re-run,")
        print("or run `uv run deploy` without --automated and answer the prompt.")
    sys.exit(1)


def _validated_pacing_or_exit(raw: str) -> str:
    """Reject bad pacing *before* Terraform, Docker, AWS, or Confluent work.

    The automated path used to call `int()` on this straight, so a stale
    `TF_VAR_seconds_per_lap=fast` produced a raw traceback after every prereq
    check had already run.
    """
    value, problem = meta.validate_seconds_per_lap(raw)
    if problem:
        print(f"\nError: {problem}")
        print("Fix TF_VAR_seconds_per_lap (exported, credentials.env, or")
        print(f"runs/{TRACK.name}/deployment.env) and re-run.")
        sys.exit(1)
    return str(value)


def _shared_prefix(prefix: str) -> str:
    """The naming prefix for the shared tier, derived from the attendee prefix."""
    return os.environ.get(SHARED_PREFIX_ENV) or f"f1-{prefix}"


def _deployed_shared_prefix(state_path) -> str | None:
    """The naming prefix the already-applied shared tier used, or None.

    aws-shared has no `prefix` output, but `ecr_image_uri` embeds the repository
    name (`<registry>/<prefix>-simulator:<tag>`), so the name that state was built
    with is recoverable from it. None means "no state" or "can't tell" — callers
    treat that as nothing to migrate, not as a match.
    """
    if not state_path.exists():
        return None
    try:
        out = run_terraform_output(state_path)
    except Exception:
        return None
    repo = str(out.get("ecr_image_uri") or "").rsplit("/", 1)[-1].split(":", 1)[0]
    suffix = "-simulator"
    return repo[: -len(suffix)] if repo.endswith(suffix) and len(repo) > len(suffix) else None


def _confirm_shared_rename(deployed: str, wanted: str, automated: bool) -> None:
    """Warn (and require a decision) before renaming existing shared infra.

    Renaming the shared prefix is not cosmetic: the ECR repository is destroyed
    and recreated (`force_delete = true`), the simulator image is rebuilt and
    pushed, `shared_ecr_image_uri` changes so the attendee task definition is
    revised, and the ECS service therefore restarts — which restarts a running
    race. Nobody should discover that mid-demo-prep, so `--automated` refuses
    rather than warning-and-doing-it-anyway: an unattended run is exactly the one
    that would surprise someone.

    This does not reintroduce the collision item 10 fixes. Two people on two
    machines have no prior shared state, so both still derive their own
    `f1-<prefix>` repository; only an in-place migration is gated.
    """
    print("\n" + "!" * 72)
    print(f"Shared infrastructure is currently named {deployed!r}; this run wants {wanted!r}.")
    print("Applying the new name will:")
    print(f"  - delete and recreate the ECR repository ({deployed}-simulator -> {wanted}-simulator)")
    print("  - rebuild and push the simulator image (a few minutes)")
    print("  - revise the attendee ECS task definition, restarting a running race")
    print(f"\nKeep the current name (no changes):  export {SHARED_PREFIX_ENV}={deployed}")
    print("!" * 72)
    if automated:
        print("\nRefusing to migrate unattended. Pin the current name with the export above,")
        print("or re-run `uv run deploy` without --automated to confirm the rename.")
        sys.exit(1)
    if input("\nProceed with the rename? (y/n): ").strip().lower() != "y":
        print("Cancelled.")
        sys.exit(0)


def _collect_config(root, creds_file, creds: dict, automated: bool) -> dict[str, str]:
    """Gather and validate every input, before any Terraform/Docker/AWS/Confluent work.

    Both paths end with the prefix and pacing validated and persisted to this
    track's metadata, so a re-run — automated or not — reuses exactly what the
    live deployment was built with.
    """
    if automated:
        print("\n--- Automated mode: using credentials.env values ---\n")
        cfg = {
            "api_key": creds.get("TF_VAR_confluent_cloud_api_key", ""),
            "api_secret": creds.get("TF_VAR_confluent_cloud_api_secret", ""),
            "owner_email": creds.get("TF_VAR_owner_email", ""),
            "aws_bedrock_key": creds.get("TF_VAR_aws_bedrock_access_key", ""),
            "aws_bedrock_secret": creds.get("TF_VAR_aws_bedrock_secret_key", ""),
            "aws_session_token": creds.get("TF_VAR_aws_session_token", ""),
        }
        missing = [
            key
            for key, value in {
                "TF_VAR_confluent_cloud_api_key": cfg["api_key"],
                "TF_VAR_confluent_cloud_api_secret": cfg["api_secret"],
                "TF_VAR_owner_email": cfg["owner_email"],
                "TF_VAR_aws_bedrock_access_key": cfg["aws_bedrock_key"],
                "TF_VAR_aws_bedrock_secret_key": cfg["aws_bedrock_secret"],
            }.items()
            if not value
        ]
        if missing:
            print(f"Error: credentials.env is missing required values: {', '.join(missing)}")
            sys.exit(1)

        # No prompt to fall back on, so the derived prefix has to be usable, and
        # the saved one has to still match whatever is deployed — resolve_prefix
        # reads state first and reports a conflict rather than renaming live
        # resources.
        prefix, source = _resolve_prefix(root, cfg["owner_email"], _explicit_prefix())
        cfg["prefix"] = _validated_prefix_or_exit(prefix)
        print(f"  Prefix:        {prefix}  ({_PREFIX_SOURCES[source]})")
        cfg["seconds_per_lap"] = _validated_pacing_or_exit(_seconds_per_lap_default(root, creds))
        print(f"  Seconds/lap:   {cfg['seconds_per_lap']}")
        _persist(root, creds_file, cfg, interactive=False)
        return cfg

    generate = input("\nGenerate new Confluent Cloud API keys? (y/n) [n]: ").strip().lower()
    if generate == "y":
        # The Confluent CLI session is needed *here and only here*. Terraform's
        # Confluent provider authenticates with TF_VAR_confluent_cloud_api_key/
        # _secret, so a user who already has an OrganizationAdmin key never needs
        # to log in — and shouldn't be pushed through a prompt that writes their
        # Confluent password into credentials.env in plaintext.
        if not ensure_confluent_login(creds, creds_file=creds_file, interactive=True):
            sys.exit(1)
        api_key, api_secret = generate_confluent_api_keys()
        if api_key and api_secret:
            set_key(str(creds_file), "TF_VAR_confluent_cloud_api_key", api_key)
            set_key(str(creds_file), "TF_VAR_confluent_cloud_api_secret", api_secret)
            creds["TF_VAR_confluent_cloud_api_key"] = api_key
            creds["TF_VAR_confluent_cloud_api_secret"] = api_secret

    print("\n--- Configuration ---\n")
    cfg = {
        "api_key": prompt_with_default("Confluent Cloud API Key", creds.get("TF_VAR_confluent_cloud_api_key", "")),
        "api_secret": prompt_with_default(
            "Confluent Cloud API Secret", creds.get("TF_VAR_confluent_cloud_api_secret", "")
        ),
        "owner_email": prompt_with_default(
            "Owner email (for AWS resource tagging)", creds.get("TF_VAR_owner_email", "")
        ),
    }

    # Offered as the prompt default rather than imposed: derived from $USER (or a
    # hash of the owner email on a shared login), reused from state/metadata once
    # the track exists. The old prompt defaulted to credentials.env — empty on a
    # fresh checkout, so its "e.g. demo or your initials" example nudged everyone
    # toward the same name.
    suggested, source = _resolve_prefix(root, cfg["owner_email"], _explicit_prefix())
    print(f"  (prefix {suggested!r} — {_PREFIX_SOURCES[source]})")
    while True:
        prefix = prompt_with_default(f"Prefix for this deployment (alphanumeric, max {meta.MAX_PREFIX_LEN})", suggested)
        problem = meta.validate_prefix(prefix)
        if not problem:
            break
        print(f"  {problem}")
    # An answered prompt is explicit, so re-resolve: refuse a value that
    # contradicts live state instead of orphaning the deployed resources.
    resolved, _source = _resolve_prefix(root, cfg["owner_email"], prefix)
    cfg["prefix"] = _validated_prefix_or_exit(resolved)

    cfg["aws_bedrock_key"] = prompt_with_default(
        "AWS Bedrock Access Key", creds.get("TF_VAR_aws_bedrock_access_key", "")
    )
    cfg["aws_bedrock_secret"] = prompt_with_default(
        "AWS Bedrock Secret Key", creds.get("TF_VAR_aws_bedrock_secret_key", "")
    )
    while True:
        raw = prompt_with_default(
            f"Seconds per lap (60-lap race, so 20 = ~20 min; minimum {MIN_SECONDS_PER_LAP})",
            _seconds_per_lap_default(root, creds),
        )
        value, problem = meta.validate_seconds_per_lap(raw)
        if value is not None:
            cfg["seconds_per_lap"] = str(value)
            break
        print(f"  {problem}")

    cfg["aws_session_token"] = ""
    if cfg["aws_bedrock_key"].startswith("ASIA"):
        cfg["aws_session_token"] = prompt_with_default(
            "AWS Session Token (required for temporary credentials)",
            creds.get("TF_VAR_aws_session_token", ""),
        )

    _persist(root, creds_file, cfg, interactive=True)
    return cfg


def _persist(root, creds_file, cfg: dict[str, str], interactive: bool) -> None:
    """Record the resolved inputs, before anything is applied.

    The per-track metadata is authoritative: it is what a re-run and a teardown
    read, and the other track cannot clobber it. Saved *before* the apply on
    purpose — a failed apply that is retried has to reuse the same names.

    Prefix and pacing are also mirrored into credentials.env in **both** paths.
    Prefix because `uv run destroy` sources the `aws` tier's Terraform variables
    from that file; pacing so that
    `export TF_VAR_seconds_per_lap=15; uv run deploy --automated` sticks, which it
    never did before (set_key only ran in the interactive branch). Self-service no
    longer writes `TF_VAR_prefix` there, so that key now belongs to this track.
    """
    meta.save_meta(
        root,
        TRACK,
        **{
            meta.KEY_BASE_PREFIX: meta.derive_base_prefix(cfg["owner_email"]),
            meta.KEY_RESOLVED_PREFIX: cfg["prefix"],
            meta.KEY_SECONDS_PER_LAP: cfg["seconds_per_lap"],
            meta.KEY_REGION: REGION,
        },
    )

    mirror = {
        "TF_VAR_prefix": cfg["prefix"],
        "TF_VAR_seconds_per_lap": cfg["seconds_per_lap"],
    }
    if interactive:
        # Only the interactive path prompted for these, so only it has anything
        # new to write back.
        mirror.update(
            {
                "TF_VAR_confluent_cloud_api_key": cfg["api_key"],
                "TF_VAR_confluent_cloud_api_secret": cfg["api_secret"],
                "TF_VAR_owner_email": cfg["owner_email"],
                "TF_VAR_aws_bedrock_access_key": cfg["aws_bedrock_key"],
                "TF_VAR_aws_bedrock_secret_key": cfg["aws_bedrock_secret"],
            }
        )
        if cfg["aws_session_token"]:
            mirror["TF_VAR_aws_session_token"] = cfg["aws_session_token"]
    for key, value in mirror.items():
        set_key(str(creds_file), key, value)


def _build_labs_and_restart(tf: dict, root) -> bool:
    """Prebuild LAB 3 + LAB 4 against a fresh deployment, then (re)start the race.

    Stop first. The ECS service is created with `desired_count = 1` and
    `RACE_LOOP=true` (terraform/aws/datagen.tf), so the feed is already live the
    moment Terraform returns. `race_standings` has no `scan.startup.mode`
    override (terraform/modules/topics/main.tf) so it starts from `latest`, and it
    is the versioned side of LAB 3's temporal join: standings produced before the
    LAB 3 statement is RUNNING are never seen, leaving those laps with no version
    to join against, and `car_state` silently loses them. Same ordering
    `uv run reset --with-labs` uses, for the same reason.
    """
    print("\n=== Prebuilding the labs (--with-labs) ===")
    print("\n1. Stopping the race simulator (it starts with the ECS service)...")
    if not (scale_simulator(tf, REGION, 0) and wait_for_drain(tf, REGION)):
        # Not fatal: the labs are still worth building. But say so, because the
        # laps produced in the meantime have standings this deployment will never
        # see, so car_state will be missing them.
        print("  Warning: could not confirm the simulator stopped. The labs will still be")
        print("  built, but car_state may be missing the laps produced before they started.")
        print("  `uv run reset --with-labs` gives a clean start.")

    print("\n2. Building lab objects from demo-reference/ (car_state -> agent -> pit_decisions)...")
    if not create_lab_objects(tf, root):
        print("\nLab build FAILED — see the error above. The environment is up and the")
        print("simulator is stopped; fix the SQL problem, then `uv run reset --with-labs`.")
        return False

    print("\n3. Starting the race simulator...")
    if not scale_simulator(tf, REGION, 1):
        print("  The labs are built but the feed is stopped. Start it with `uv run race start`.")
        return False
    wait_for_running(tf, REGION)
    return True


def main():
    parser = argparse.ArgumentParser(description="Deploy a single F1 environment (Confluent + Postgres/CDC/ECS)")
    parser.add_argument(
        "--automated",
        action="store_true",
        default=False,
        help="Use credentials.env values without prompting.",
    )
    parser.add_argument(
        "--with-labs",
        action="store_true",
        default=False,
        help=(
            "Also build the LAB 3 / LAB 4 Flink objects from demo-reference/ and restart "
            "the race behind them — a ready-to-demo environment in one command. Omit to "
            "get a bare environment (what the workshop hands attendees)."
        ),
    )
    args = parser.parse_args()

    print("=== F1 Demo — Standalone Deploy (shared + one attendee) ===\n")

    root = get_project_root()

    # First, and before resolving the prefix: reading an existing deployment's
    # prefix out of Terraform state shells out to the terraform binary.
    if not check_terraform_installed():
        print("Error: Terraform not found. Install from https://developer.hashicorp.com/terraform/install")
        sys.exit(1)
    print("  Terraform installed")

    creds_file, creds = load_or_create_credentials_file(root)

    # Everything is resolved and validated here — a bad prefix or bad pacing must
    # fail before the Docker/AWS probes, let alone before an apply.
    cfg = _collect_config(root, creds_file, creds, args.automated)

    if not check_docker_running():
        print("\nError: Docker is not ready (needed to build the simulator image).")
        print("Start Docker Desktop or run `colima start`, then verify with: docker info")
        sys.exit(1)
    print("\n  Docker running")

    if not check_aws_configured():
        print("\nError: AWS CLI not configured. Run: aws configure")
        sys.exit(1)
    print("  AWS CLI configured")

    shared_path = root / "terraform" / "aws-shared"
    shared_prefix = _shared_prefix(cfg["prefix"])
    deployed_shared = _deployed_shared_prefix(shared_path / "terraform.tfstate")

    print("\n--- Deployment Summary ---")
    print(f"  Region:   {REGION}")
    print(f"  Owner:    {cfg['owner_email']}")
    print(f"  Prefix:   {cfg['prefix']}   (Confluent env: RIVER-RACING-{cfg['prefix']}-ENV)")
    print(f"  Shared:   {shared_prefix}   (ECR repo {shared_prefix}-simulator)")
    print(f"  Postgres: {os.environ.get('TF_VAR_postgres_instance_type') or DEFAULT_POSTGRES_INSTANCE_TYPE}")
    print(f"  CC Key:   {cfg['api_key'][:8]}...")
    print(f"  Bedrock:  {cfg['aws_bedrock_key'][:8]}..." if cfg["aws_bedrock_key"] else "  Bedrock:  (not set)")
    print(f"  Pacing:   {cfg['seconds_per_lap']}s/lap (~{60 * int(cfg['seconds_per_lap']) // 60}-min race)")
    print(f"  Labs:     {'prebuilt (LAB 3 + LAB 4)' if args.with_labs else 'not built (attendee writes them)'}")
    print("  Deploys:  aws-shared -> aws")

    if deployed_shared and deployed_shared != shared_prefix:
        _confirm_shared_rename(deployed_shared, shared_prefix, args.automated)

    if not args.automated:
        if input("\nReady to deploy? (y/n): ").strip().lower() != "y":
            print("Cancelled.")
            sys.exit(0)

    # AWS provider resilience to transient network failures.
    os.environ.setdefault("AWS_RETRY_MODE", "adaptive")
    os.environ.setdefault("AWS_MAX_ATTEMPTS", "10")

    # --- 1. Shared infrastructure ---
    print("\n=== Deploying shared infrastructure (aws-shared) ===")
    shared_env = {
        "TF_VAR_region": REGION,
        "TF_VAR_owner_email": cfg["owner_email"],
        "TF_VAR_prefix": shared_prefix,
        "TF_VAR_attendee_count": "1",
        # setdefault semantics: an exported value wins, so the cost default is a
        # default and not an override.
        "TF_VAR_postgres_instance_type": os.environ.get("TF_VAR_postgres_instance_type")
        or DEFAULT_POSTGRES_INSTANCE_TYPE,
    }
    for k, v in shared_env.items():
        os.environ[k] = v
    if not run_terraform(shared_path):
        print("\nShared deployment failed. Stopping.")
        sys.exit(1)

    shared = run_terraform_output(shared_path / "terraform.tfstate")

    # --- 2. Attendee environment ---
    print("\n=== Deploying attendee environment (aws) ===")
    attendee_path = root / "terraform" / "aws"
    attendee_env = {
        "TF_VAR_prefix": cfg["prefix"],
        "TF_VAR_owner_email": cfg["owner_email"],
        "TF_VAR_region": REGION,
        "TF_VAR_confluent_cloud_api_key": cfg["api_key"],
        "TF_VAR_confluent_cloud_api_secret": cfg["api_secret"],
        "TF_VAR_aws_bedrock_access_key": cfg["aws_bedrock_key"],
        "TF_VAR_aws_bedrock_secret_key": cfg["aws_bedrock_secret"],
        "TF_VAR_aws_session_token": cfg["aws_session_token"],
        "TF_VAR_seconds_per_lap": cfg["seconds_per_lap"],
        "TF_VAR_shared_vpc_id": shared["vpc_id"],
        "TF_VAR_shared_subnet_ids": json.dumps(shared["subnet_ids"]),
        "TF_VAR_shared_postgres_host": shared["postgres_host"],
        "TF_VAR_shared_postgres_password": shared["postgres_password"],
        "TF_VAR_shared_ecr_image_uri": shared["ecr_image_uri"],
    }
    for k, v in attendee_env.items():
        os.environ[k] = v
    if not run_terraform(attendee_path):
        print("\nAttendee deployment failed. Shared infra is still running — `uv run destroy` to clean up.")
        sys.exit(1)

    # --- 3. Credential card ---
    # f1-sql / f1-pitwall authenticate with a card, not a Confluent login, so
    # write one here from the fresh outputs (same generator the workshop and
    # self-service flows use, so the card format is identical everywhere).
    attendee_out = run_terraform_output(attendee_path / "terraform.tfstate")
    creds_dir = root / "runs" / RUN_NAME / "credentials"
    creds_dir.mkdir(parents=True, exist_ok=True)
    fields = creds_mod._card_fields(cfg["prefix"], cfg["owner_email"], attendee_out, social_feed_url="", region=REGION)
    creds_mod._write_env(creds_dir, fields)
    creds_mod._write_md(creds_dir, fields)
    card = f"runs/{RUN_NAME}/credentials/{cfg['prefix']}.env"

    # Point credentials.env at the new card so f1-sql / f1-pitwall find it with
    # no flags, in any terminal — nothing for the user to export or remember.
    set_active_card(root, root / card)
    meta.save_meta(root, TRACK, **{meta.KEY_CARD: card})

    # --- 4. Optional: prebuild the labs ---
    labs_ok = True
    if args.with_labs:
        labs_ok = _build_labs_and_restart(attendee_out, root)

    print("\n=== Deployment Complete ===\n")
    print("The race simulator runs as an always-on ECS service (RACE_LOOP=true) — the feed is")
    print(f"already live. Your credential card:  {card}")
    print("It's recorded as F1_CARD in credentials.env, so the tools below need no flags.\n")
    if args.with_labs and labs_ok:
        print("LAB 3 and LAB 4 are already running — `car_state` and `pit_decisions` are filling.")
        print("  `car_state` stays empty for ~3.5 min while anomaly detection fills its")
        print("  first 20 windows of context. The anomaly fires around lap 32.\n")
    print("1. Open the SQL shell for the labs:")
    print("     uv run f1-sql")
    print("2. Open the live dashboard (second terminal):")
    print("     uv run f1-pitwall")
    print("3. Control this deployment's race feed:")
    print("     uv run race status | stop | start | restart")
    print("\nWalkthrough: docs/STANDALONE-DEMO.md")
    print("Tear down all resources:  uv run destroy")
    if not labs_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
