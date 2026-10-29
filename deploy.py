#!/usr/bin/env python3
"""
`uv run deploy` — provision ONE F1 environment on this machine. It asks which:

  1. Self-service — Confluent Cloud only; the race runs locally (`uv run f1-race`).
     See scripts/selfservice/provision.py.
  2. Standalone   — the full architecture, in two tiers for one attendee prefix:
       terraform/aws-shared  — shared VPC/subnets, Postgres, ECR + simulator image
       terraform/aws         — one Confluent environment + ECS simulator, wired to
                               the shared outputs
  3. Pre-provisioned — self-service inside an environment a customer's platform
     team created and filled. It mints the developer's own keys and writes their
     card; it runs no Terraform. See scripts/preprovisioned/provision.py.

For a real multi-attendee workshop, use `wsa` with wsa-spec-aws.yaml instead —
the standalone track is the manual single-environment equivalent. Nothing here is
read by `wsa`, which is why the cost and naming defaults below can differ from the
workshop's without changing workshop behavior.

Usage:
  uv run deploy                                       # prompts for the track and inputs
  uv run deploy --track self-service                  # skip the track prompt
  uv run deploy --track pre-provisioned               # in your platform team's environment
  uv run deploy --automated --track standalone        # no prompts; a *bare* environment
  uv run deploy --automated --track standalone --with-labs  # LAB 3 + LAB 4 prebuilt

`--automated` and `--with-labs` are deliberately orthogonal: `--automated` means
"don't prompt", not "build the labs". A smoke test wants a bare environment.
Tear any track down with `uv run destroy`.
"""

import argparse
import json
import os
import sys

from dotenv import set_key

from scripts.common import deploy_common as common
from scripts.common import deployment_meta as meta
from scripts.common.credentials import load_or_create_credentials_file
from scripts.common.login_checks import (
    check_aws_configured,
    check_docker_running,
    check_terraform_installed,
    docker_installed,
    docker_start_hint,
    start_docker,
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
from scripts.preprovisioned import provision as preprovisioned
from scripts.selfservice import provision as selfservice

TRACK = meta.STANDALONE

# Credential cards for this flow land in runs/<RUN_NAME>/credentials/ (the
# self-service track writes to runs/selfservice/).
RUN_NAME = TRACK.name

REGION = common.REGION

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

# Race pacing. terraform/aws defaults to 20 (a 60-lap race takes 20 minutes, with
# the lap-24 anomaly about 8 minutes in), matching the fixed 20-second TUMBLE window in the
# LAB 3 SQL — one window per lap. Changing this requires a matching SQL-window
# change (see CLAUDE.md). Below ~10s/lap anomaly detection can't accumulate its 12
# windows before lap 24 and the anomaly never fires.
DEFAULT_SECONDS_PER_LAP = common.DEFAULT_SECONDS_PER_LAP
MIN_SECONDS_PER_LAP = meta.MIN_SECONDS_PER_LAP

_PREFIX_SOURCES = common.prefix_sources(TRACK, "derived from your identity, stable across re-runs")


def _seconds_per_lap_default(root, creds: dict) -> str:
    return common.seconds_per_lap_default(root, TRACK, creds)


def _resolve_prefix(root, owner_email: str, explicit: str | None) -> tuple[str, str]:
    return common.resolve_prefix(root, TRACK, owner_email, explicit)


_explicit_prefix = common.explicit_prefix
_validated_prefix_or_exit = common.validated_prefix_or_exit


def _validated_pacing_or_exit(raw: str) -> str:
    return common.validated_pacing_or_exit(raw, TRACK)


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
        print("or re-run `uv run deploy --track standalone` without --automated to confirm the rename.")
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
            "llm_provider": common.llm_provider_for(root, TRACK, creds),
        }
        required = {
            "TF_VAR_confluent_cloud_api_key": cfg["api_key"],
            "TF_VAR_confluent_cloud_api_secret": cfg["api_secret"],
            "TF_VAR_owner_email": cfg["owner_email"],
        }
        if cfg["llm_provider"] == "bedrock":
            required["TF_VAR_aws_bedrock_access_key"] = cfg["aws_bedrock_key"]
            required["TF_VAR_aws_bedrock_secret_key"] = cfg["aws_bedrock_secret"]
        missing = [key for key, value in required.items() if not value]
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

    common.offer_key_generation(creds, creds_file)
    llm_provider = common.llm_provider_for(root, TRACK, creds)

    print("\n--- Configuration ---\n")

    def ask(label: str, env_key: str, secret: bool = False) -> str:
        # Saved the moment it is entered, so backing out later keeps it and the
        # next run offers it as the default.
        value = prompt_with_default(label, creds.get(env_key, ""), secret=secret)
        if value != creds.get(env_key):
            set_key(str(creds_file), env_key, value)
            creds[env_key] = value
        return value

    cfg = {
        "api_key": ask("Confluent Cloud API Key", "TF_VAR_confluent_cloud_api_key"),
        "api_secret": ask("Confluent Cloud API Secret", "TF_VAR_confluent_cloud_api_secret", secret=True),
        "owner_email": ask("Owner email (for AWS resource tagging)", "TF_VAR_owner_email"),
        "llm_provider": llm_provider,
    }

    # Offered as the prompt default rather than imposed: derived from $USER (or a
    # hash of the owner email on a shared login), reused from state/metadata once
    # the track exists. The old prompt defaulted to credentials.env — empty on a
    # fresh checkout, so its "e.g. demo or your initials" example nudged everyone
    # toward the same name.
    suggested, source = _resolve_prefix(root, cfg["owner_email"], _explicit_prefix())
    print(f"  (prefix {suggested!r} — {_PREFIX_SOURCES[source]})")
    if source == "state":
        # Locked by the deployed environment: any other answer would be refused,
        # so don't ask.
        cfg["prefix"] = _validated_prefix_or_exit(suggested)
    else:
        while True:
            prefix = prompt_with_default(
                f"Prefix for this deployment (alphanumeric, max {meta.MAX_PREFIX_LEN})", suggested
            )
            problem = meta.validate_prefix(prefix)
            if not problem:
                break
            print(f"  {problem}")
        # An answered prompt is explicit, so re-resolve: refuse a value that
        # contradicts live state instead of orphaning the deployed resources.
        resolved, _source = _resolve_prefix(root, cfg["owner_email"], prefix)
        cfg["prefix"] = _validated_prefix_or_exit(resolved)

    # `uv run api-keys create` saves these to credentials.env; when both are
    # there, use them rather than re-asking for values the user just generated.
    saved_bedrock_key = creds.get("TF_VAR_aws_bedrock_access_key", "")
    saved_bedrock_secret = creds.get("TF_VAR_aws_bedrock_secret_key", "")
    if llm_provider != "bedrock":
        # CSP Hosted inference needs no AWS keys; pass along whatever is saved.
        cfg["aws_bedrock_key"] = saved_bedrock_key
        cfg["aws_bedrock_secret"] = saved_bedrock_secret
    elif saved_bedrock_key and saved_bedrock_secret:
        cfg["aws_bedrock_key"] = saved_bedrock_key
        cfg["aws_bedrock_secret"] = saved_bedrock_secret
        print(f"  Bedrock keys:  {saved_bedrock_key[:8]}... (from credentials.env)")
    else:
        cfg["aws_bedrock_key"] = ask("AWS Bedrock Access Key", "TF_VAR_aws_bedrock_access_key")
        cfg["aws_bedrock_secret"] = ask("AWS Bedrock Secret Key", "TF_VAR_aws_bedrock_secret_key", secret=True)
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
    if llm_provider == "bedrock" and cfg["aws_bedrock_key"].startswith("ASIA"):
        cfg["aws_session_token"] = ask(
            "AWS Session Token (required for temporary credentials)", "TF_VAR_aws_session_token"
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
    `export TF_VAR_seconds_per_lap=15; uv run deploy --automated --track standalone` sticks, which it
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
        # A user choice, so credentials.env (the source of truth) records it on
        # every path — same rule as the self-service track.
        common.LLM_PROVIDER_KEY: cfg["llm_provider"],
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

    print("\n2. Building lab objects from docs/demo-reference/ (car_state -> agent -> pit_decisions)...")
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


def deploy_standalone(args: argparse.Namespace, root, creds_file, creds: dict) -> None:
    print("\n=== F1 Demo — Standalone Deploy (shared + one attendee) ===\n")

    # Everything is resolved and validated here — a bad prefix or bad pacing must
    # fail before the Docker/AWS probes, let alone before an apply.
    cfg = _collect_config(root, creds_file, creds, args.automated)
    common.check_bedrock_or_exit(cfg, args.automated)

    if not docker_installed():
        print("\nError: Docker is not installed (needed to build the simulator image).")
        print(docker_start_hint())
        sys.exit(1)
    if not check_docker_running():
        print("\nDocker is not running (needed to build the simulator image).")
        started = False
        if not args.automated and input("Try to start it now? (y/n) [y]: ").strip().lower() in ("", "y", "yes"):
            started = start_docker()
        if not started:
            print("\nError: Docker is not ready.")
            print(docker_start_hint())
            sys.exit(1)
    print("\n  Docker running")

    if not check_aws_configured():
        print("\nError: AWS CLI not configured. Run: aws configure")
        sys.exit(1)
    print("  AWS CLI configured")

    shared_path = root / "terraform" / "aws-shared"
    shared_prefix = _shared_prefix(cfg["prefix"])
    deployed_shared = _deployed_shared_prefix(shared_path / "terraform.tfstate")

    common.print_summary(
        "Deployment Summary",
        {
            "Region": REGION,
            "Owner": cfg["owner_email"],
            "Prefix": f"{cfg['prefix']}   (Confluent env: RIVER-RACING-{cfg['prefix']}-ENV)",
            "Shared": f"{shared_prefix}   (ECR repo {shared_prefix}-simulator)",
            "Postgres": os.environ.get("TF_VAR_postgres_instance_type") or DEFAULT_POSTGRES_INSTANCE_TYPE,
            "LLM": common.LLM_PROVIDERS[cfg["llm_provider"]],
            "Pacing": f"{cfg['seconds_per_lap']}s/lap (~{60 * int(cfg['seconds_per_lap']) // 60}-min race)",
            "Labs": "prebuilt (LAB 3 + LAB 4)" if args.with_labs else "not built (attendee writes them)",
            "Deploys": "aws-shared -> aws",
        },
        creds_file,
    )

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
        "TF_VAR_llm_provider": cfg["llm_provider"],
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
    card = common.write_card(root, TRACK, cfg, attendee_out, rtce_keys=False).relative_to(root)

    # --- 4. Optional: prebuild the labs ---
    labs_ok = True
    if args.with_labs:
        labs_ok = _build_labs_and_restart(attendee_out, root)

    print("\n=== Deployment Complete ===\n")
    print(f"Your credential card:  {card}")
    print(f"Flink SQL Workspace:   {common.flink_workspace_url(attendee_out)}")
    print("It's recorded as F1_CARD in credentials.env, so the tools below need no flags.\n")
    if args.with_labs and labs_ok:
        print("LAB 3 and LAB 4 are already running and the ECS race simulator is live.")
        print("  `car_state` emits one row per 20s lap; anomaly detection warms up over")
        print("  12 laps (~4 min) and flags the front-left tire anomaly at lap 24.\n")
    else:
        print("The ECS race simulator is provisioned stopped so you can create `car_state`")
        print("first, then start the race from lap 1 with `uv run race start`.\n")
    print("1. Follow the walkthrough in the Confluent Cloud SQL Workspace:")
    print("     docs/tracks/STANDALONE-DEMO.md")
    print("2. Start or control this deployment's race feed:")
    print("     uv run race start | status | stop | restart")
    print("3. Open the live dashboard (second terminal):")
    print("     uv run f1-pitwall")
    print("\nTear down all resources:  uv run destroy")
    if not args.automated:
        # The ECS race is never started from here: with labs it is already
        # running, and without them `car_state` has to exist first.
        common.offer_start(root, None)
    if not labs_ok:
        sys.exit(1)


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="Deploy one F1 environment: self-service, standalone, or pre-provisioned"
    )
    parser.add_argument(
        "--track",
        choices=list(common.TRACKS),
        help=(
            "self-service (Confluent-only), standalone (Confluent + AWS), or pre-provisioned "
            "(Confluent-only, in an environment your platform team created). Prompted when omitted."
        ),
    )
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
            "Also build the LAB 3 / LAB 4 Flink objects from docs/demo-reference/ — a "
            "ready-to-demo environment in one command. Omit to get a bare environment "
            "(what the workshop hands attendees)."
        ),
    )
    parser.add_argument(
        "--bedrock",
        action="store_true",
        default=False,
        help="Back the pit-strategy agent with AWS Bedrock (your own AWS keys) instead of CSP Hosted inference.",
    )
    args = parser.parse_args(argv)
    if args.bedrock:
        os.environ[common.LLM_PROVIDER_KEY] = "bedrock"

    print("=== F1 Pit Wall — Deploy ===\n")
    root = get_project_root()

    # Picking the track only looks for files. Then, for the tracks that run
    # Terraform, check for it before anything else, including resolving the
    # prefix: reading an existing deployment's prefix out of Terraform state
    # shells out to the terraform binary. The pre-provisioned track runs none —
    # it mints keys and writes a card — so a developer there needs no Terraform.
    track = common.pick_track(root, args.track, args.automated)
    if track is not meta.PREPROVISIONED and not check_terraform_installed():
        print("Error: Terraform not found. Install from https://developer.hashicorp.com/terraform/install")
        sys.exit(1)

    creds_file, creds = load_or_create_credentials_file(root)
    if track is meta.SELFSERVICE:
        selfservice.deploy(args, root, creds_file, creds)
    elif track is meta.PREPROVISIONED:
        preprovisioned.deploy(args, root, creds_file, creds)
    else:
        deploy_standalone(args, root, creds_file, creds)


if __name__ == "__main__":
    main()
