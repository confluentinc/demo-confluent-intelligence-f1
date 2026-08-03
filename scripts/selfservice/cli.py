"""``selfservice`` — provision the whole workshop for one person, Confluent-only.

  uv run selfservice up              # apply terraform/self-service, write a
                                     # credential card, seed driver_race_history
  uv run selfservice up --with-labs  # ...and prebuild LAB 3 + LAB 4
  uv run selfservice down            # tear the environment down

Unlike ``uv run deploy`` (which also stands up EC2 Postgres + an ECS simulator +
CDC), self-service provisions Confluent Cloud only. The user runs the simulator
locally with ``uv run f1-race`` and seeds ``driver_race_history`` with a bounded
Flink INSERT — no Docker, no AWS infrastructure. AWS Bedrock *credentials* are
still needed (they back the LAB 4 LLM model); mint them with
``uv run api-keys create``.

``--automated`` and ``--with-labs`` are orthogonal: ``--automated`` means "don't
prompt", not "build the labs".
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import dotenv_values, set_key

from scripts.common import deployment_meta as meta
from scripts.common.credentials import (
    generate_confluent_api_keys,
    load_or_create_credentials_file,
    set_active_card,
)
from scripts.common.login_checks import check_terraform_installed, ensure_confluent_login
from scripts.common.simulator_control import create_lab_objects
from scripts.common.terraform import cleanup_terraform_artifacts, get_project_root, run_terraform_output
from scripts.common.terraform_runner import run_terraform, run_terraform_destroy
from scripts.common.ui import prompt_with_default
from scripts.selfservice.seed import ensure_driver_race_history
from scripts.workshop import creds as creds_mod

TRACK = meta.SELFSERVICE
RUN_NAME = TRACK.name
REGION = "us-east-1"

# credentials.env keys (shared with `uv run deploy` so the file is reusable).
REQUIRED = {
    "TF_VAR_confluent_cloud_api_key": "Confluent Cloud API Key",
    "TF_VAR_confluent_cloud_api_secret": "Confluent Cloud API Secret",
    "TF_VAR_owner_email": "Owner email (tags the Confluent environment)",
    "TF_VAR_aws_bedrock_access_key": "AWS Bedrock Access Key",
    "TF_VAR_aws_bedrock_secret_key": "AWS Bedrock Secret Key",
}

# `uv run f1-race` (not Terraform) paces this track's race, but the value is
# recorded here so the two agree across re-runs and so `f1-race` needs no flag.
DEFAULT_SECONDS_PER_LAP = "20"

_PREFIX_SOURCES = {
    "state": "already deployed — cannot be changed without tearing down first",
    "saved": f"reused from runs/{RUN_NAME}/deployment.env",
    "explicit": "from the exported TF_VAR_prefix",
    "derived": "derived from your identity, stable across re-runs",
}


def _tf_env(cfg: dict[str, str]) -> dict[str, str]:
    """TF_VAR_* environment for the self-service tier."""
    env = {
        "TF_VAR_prefix": cfg["prefix"],
        "TF_VAR_owner_email": cfg["owner_email"],
        "TF_VAR_region": REGION,
        "TF_VAR_confluent_cloud_api_key": cfg["api_key"],
        "TF_VAR_confluent_cloud_api_secret": cfg["api_secret"],
        "TF_VAR_aws_bedrock_access_key": cfg["aws_bedrock_key"],
        "TF_VAR_aws_bedrock_secret_key": cfg["aws_bedrock_secret"],
    }
    if cfg.get("aws_session_token"):
        env["TF_VAR_aws_session_token"] = cfg["aws_session_token"]
    return env


def export_selfservice_tf_env(creds: dict) -> None:
    """Export the TF_VAR_* set the self-service tier needs, from credentials.env.

    Terraform evaluates the config even on destroy, so teardown needs the same
    variables as apply. Shared by `selfservice down` and `uv run destroy` — the
    signature is deliberately unchanged for that second caller.

    The secrets still come from credentials.env, but the **prefix** no longer
    does. `creds["TF_VAR_prefix"]` is written by `uv run deploy`, so reading it
    here pointed a self-service teardown at standalone's resource names whenever
    both tracks were used on one machine. `resolve_prefix` reads this track's own
    state first, then its own metadata, so teardown targets what it deployed.
    """
    root = get_project_root()
    prefix, _source, _error = meta.resolve_prefix(root, TRACK, owner_email=creds.get("TF_VAR_owner_email", ""))
    cfg = {
        # TF_VAR_prefix is a required variable, so it must never be exported empty
        # (Terraform would prompt, and a destroy is often unattended). Every real
        # teardown resolves from state; this only covers the degenerate case of no
        # state, no metadata, and no derivable identity.
        "prefix": prefix or TRACK.name,
        "owner_email": creds.get("TF_VAR_owner_email", ""),
        "api_key": creds.get("TF_VAR_confluent_cloud_api_key", ""),
        "api_secret": creds.get("TF_VAR_confluent_cloud_api_secret", ""),
        "aws_bedrock_key": creds.get("TF_VAR_aws_bedrock_access_key", ""),
        "aws_bedrock_secret": creds.get("TF_VAR_aws_bedrock_secret_key", ""),
        "aws_session_token": creds.get("TF_VAR_aws_session_token", ""),
    }
    for k, v in _tf_env(cfg).items():
        os.environ[k] = v


def _explicit_prefix() -> str | None:
    """A prefix the user asked for deliberately, or None.

    Only an **exported** `TF_VAR_prefix` counts — see the note in
    `export_selfservice_tf_env` on why credentials.env's copy is not consulted.
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
    problem = meta.validate_prefix(prefix)
    if not problem:
        return prefix
    print(f"\nError: {problem}")
    if not prefix:
        print("$USER looks like a shared account and no owner email was available, so")
        print("there was nothing to derive one from. Export TF_VAR_prefix=<name> and re-run,")
        print("or run `uv run selfservice up` without --automated and answer the prompt.")
    sys.exit(1)


def _seconds_per_lap(root, creds: dict) -> str:
    """Pacing for `uv run f1-race`, validated before anything is provisioned."""
    raw = (
        os.environ.get("TF_VAR_seconds_per_lap")
        or meta.load_meta(root, TRACK).get(meta.KEY_SECONDS_PER_LAP)
        or creds.get("TF_VAR_seconds_per_lap")
        or DEFAULT_SECONDS_PER_LAP
    )
    value, problem = meta.validate_seconds_per_lap(raw)
    if problem:
        print(f"\nError: {problem}")
        print(f"Fix TF_VAR_seconds_per_lap (exported, credentials.env, or runs/{RUN_NAME}/deployment.env).")
        sys.exit(1)
    return str(value)


def _collect_config(root, creds_file, creds: dict, automated: bool) -> dict[str, str]:
    """Gather secrets/config, prompting interactively unless --automated.

    Everything the apply needs is resolved *and validated* here — the automated
    path used to accept an unvalidated prefix straight from credentials.env.
    """
    if automated:
        cfg = {
            "api_key": creds.get("TF_VAR_confluent_cloud_api_key", ""),
            "api_secret": creds.get("TF_VAR_confluent_cloud_api_secret", ""),
            "owner_email": creds.get("TF_VAR_owner_email", ""),
            "aws_bedrock_key": creds.get("TF_VAR_aws_bedrock_access_key", ""),
            "aws_bedrock_secret": creds.get("TF_VAR_aws_bedrock_secret_key", ""),
            "aws_session_token": creds.get("TF_VAR_aws_session_token", ""),
        }
        missing = [label for key, label in REQUIRED.items() if not creds.get(key)]
        if missing:
            print(f"Error: credentials.env is missing required values: {', '.join(missing)}")
            sys.exit(1)
        prefix, source = _resolve_prefix(root, cfg["owner_email"], _explicit_prefix())
        cfg["prefix"] = _validated_prefix_or_exit(prefix)
        print(f"  Prefix: {prefix}  ({_PREFIX_SOURCES[source]})")
        cfg["seconds_per_lap"] = _seconds_per_lap(root, creds)
        _persist(root, creds_file, cfg, interactive=False)
        return cfg

    generate = input("\nGenerate new Confluent Cloud API keys? (y/n) [n]: ").strip().lower()
    if generate == "y":
        # The only step that needs a Confluent CLI session. Terraform's Confluent
        # provider authenticates with TF_VAR_confluent_cloud_api_key/_secret, so a
        # user who already has an OrganizationAdmin key never logs in at all.
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
        "owner_email": prompt_with_default("Owner email", creds.get("TF_VAR_owner_email", "")),
        "aws_bedrock_key": prompt_with_default(
            "AWS Bedrock Access Key", creds.get("TF_VAR_aws_bedrock_access_key", "")
        ),
        "aws_bedrock_secret": prompt_with_default(
            "AWS Bedrock Secret Key", creds.get("TF_VAR_aws_bedrock_secret_key", "")
        ),
        "aws_session_token": "",
    }

    # Suggested, not imposed. The old default was credentials.env's TF_VAR_prefix
    # falling back to the literal `solo`, so everyone who accepted it deployed
    # under the same name — and after a `uv run deploy` it silently inherited
    # standalone's prefix instead.
    suggested, source = _resolve_prefix(root, cfg["owner_email"], _explicit_prefix())
    print(f"  (prefix {suggested!r} — {_PREFIX_SOURCES[source]})")
    while True:
        prefix = prompt_with_default(f"Environment prefix (alphanumeric, max {meta.MAX_PREFIX_LEN})", suggested)
        problem = meta.validate_prefix(prefix)
        if not problem:
            break
        print(f"  {problem}")
    # An answered prompt is explicit, so re-resolve: refuse a value that
    # contradicts live state rather than orphaning the deployed resources.
    resolved, _source = _resolve_prefix(root, cfg["owner_email"], prefix)
    cfg["prefix"] = _validated_prefix_or_exit(resolved)
    cfg["seconds_per_lap"] = _seconds_per_lap(root, creds)

    if cfg["aws_bedrock_key"].startswith("ASIA"):
        cfg["aws_session_token"] = prompt_with_default(
            "AWS Session Token (required for temporary credentials)", creds.get("TF_VAR_aws_session_token", "")
        )

    _persist(root, creds_file, cfg, interactive=True)
    return cfg


def _persist(root, creds_file, cfg: dict[str, str], interactive: bool) -> None:
    """Record the resolved inputs before anything is applied.

    `TF_VAR_prefix` is deliberately **not** written to credentials.env: that one
    root file feeding both tracks' Terraform inputs is what made a self-service
    teardown target standalone's names. This track's prefix lives only in its own
    metadata, which `export_selfservice_tf_env` reads.
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
    if not interactive:
        return
    for key, value in {
        "TF_VAR_confluent_cloud_api_key": cfg["api_key"],
        "TF_VAR_confluent_cloud_api_secret": cfg["api_secret"],
        "TF_VAR_owner_email": cfg["owner_email"],
        "TF_VAR_aws_bedrock_access_key": cfg["aws_bedrock_key"],
        "TF_VAR_aws_bedrock_secret_key": cfg["aws_bedrock_secret"],
    }.items():
        set_key(str(creds_file), key, value)
    if cfg["aws_session_token"]:
        set_key(str(creds_file), "TF_VAR_aws_session_token", cfg["aws_session_token"])


def up(args: argparse.Namespace) -> None:
    print("=== F1 Workshop — Self-Service (Confluent-only, run one person) ===\n")
    root = get_project_root()

    # Before resolving the prefix: reading a deployed prefix out of Terraform
    # state shells out to the terraform binary.
    if not check_terraform_installed():
        print("Error: Terraform not found. Install from https://developer.hashicorp.com/terraform/install")
        sys.exit(1)

    creds_file, creds = load_or_create_credentials_file(root)
    cfg = _collect_config(root, creds_file, creds, args.automated)

    print("\n--- Summary ---")
    print(f"  Region:  {REGION}")
    print(f"  Prefix:  {cfg['prefix']}   (Confluent env: RIVER-RACING-{cfg['prefix']}-ENV)")
    print(f"  Owner:   {cfg['owner_email']}")
    print(f"  Pacing:  {cfg['seconds_per_lap']}s/lap for `uv run f1-race`")
    print(f"  Labs:    {'prebuilt (LAB 3 + LAB 4)' if args.with_labs else 'not built (you write them)'}")
    print("  Creates: Confluent env + cluster + Flink pool + topics + LLM models (no AWS infra)")
    if not args.automated and input("\nReady to provision? (y/n): ").strip().lower() != "y":
        print("Cancelled.")
        sys.exit(0)

    os.environ.setdefault("AWS_RETRY_MODE", "adaptive")
    os.environ.setdefault("AWS_MAX_ATTEMPTS", "10")
    for k, v in _tf_env(cfg).items():
        os.environ[k] = v

    ss_path = root / "terraform" / "self-service"
    print("\n=== Provisioning Confluent environment (terraform/self-service) ===")
    if not run_terraform(ss_path):
        print("\nProvisioning failed. `uv run selfservice down` to clean up, then retry.")
        sys.exit(1)

    out = run_terraform_output(ss_path / "terraform.tfstate")

    # Credential card (reuse the workshop generator so the card format matches).
    creds_dir = root / "runs" / RUN_NAME / "credentials"
    creds_dir.mkdir(parents=True, exist_ok=True)
    fields = creds_mod._card_fields(cfg["prefix"], cfg["owner_email"], out, social_feed_url="", region=REGION)
    creds_mod._write_env(creds_dir, fields)
    creds_mod._write_md(creds_dir, fields)
    card_path = creds_dir / f"{cfg['prefix']}.env"
    # Point credentials.env at the new card so f1-race / f1-sql / f1-pitwall
    # find it with no flags, in any terminal.
    set_active_card(root, card_path)
    meta.save_meta(root, TRACK, **{meta.KEY_CARD: str(card_path.relative_to(root))})
    print(f"\nCredential card: {card_path}  (recorded as F1_CARD in credentials.env)")

    # Seed driver_race_history. Idempotent: it counts before inserting, verifies
    # afterwards, and only then records the environment it verified.
    print("\n=== driver_race_history ===")
    seeded = ensure_driver_race_history(dotenv_values(card_path), root, TRACK)

    labs_ok = True
    if args.with_labs:
        # No ECS here — the race is the local `uv run f1-race`, which the user
        # starts *after* this returns. So the labs are necessarily RUNNING before
        # any `race_standings` row is produced, which is the ordering LAB 3's
        # temporal join needs (see scripts/common/simulator_control.py).
        print("\n=== Prebuilding the labs (--with-labs) ===")
        labs_ok = create_lab_objects(out, root)
        if not labs_ok:
            print("\nLab build FAILED — see the error above. The environment is up; fix the")
            print("SQL problem and re-run `uv run selfservice up --with-labs`.")

    print("\n=== Ready ===\n")
    if args.with_labs and labs_ok:
        print("LAB 3 and LAB 4 are already running — they start filling as soon as the race does.")
        print("  `car_state` stays empty for ~3.5 min while anomaly detection fills its")
        print("  first 20 windows of context. The anomaly fires around lap 32.\n")
    print("1. Start the live race feed (leave running in its own terminal):")
    print(f"     uv run f1-race          # {cfg['seconds_per_lap']}s/lap, from this deployment's config")
    print("2. Open the SQL shell for the labs:")
    print("     uv run f1-sql")
    print("3. Open the live dashboard:")
    print("     uv run f1-pitwall")
    if not (args.with_labs and labs_ok):
        print("\nWork through labs/instructor-led: LAB 1 → LAB 4, then LAB 6.")
    print("Optional LAB 5 (watsonx Orchestrate) — see docs/SELF-SERVICE.md.")
    print("\nTear down when finished:  uv run selfservice down")

    if not (seeded and labs_ok):
        # Exit nonzero so a scripted run can tell a half-built environment from a
        # complete one — an empty driver_race_history used to be entirely silent.
        sys.exit(1)


def down(args: argparse.Namespace) -> None:
    print("=== F1 Workshop — Self-Service teardown ===\n")
    root = get_project_root()
    _creds_file, creds = load_or_create_credentials_file(root)

    ss_path = root / "terraform" / "self-service"
    if not (ss_path / "terraform.tfstate").exists():
        print("No self-service state found — nothing to destroy.")
        return

    # Destroy needs the same TF_VARs as apply (provider creds + required inputs).
    export_selfservice_tf_env(creds)

    if not args.yes and input("Destroy the self-service Confluent environment? (y/n): ").strip().lower() != "y":
        print("Cancelled.")
        return

    if run_terraform_destroy(ss_path):
        # Destroying the environment removes its cluster + Schema Registry (and
        # therefore all topics + subjects), so no separate SR cleanup is needed.
        #
        # retire_track does the rest: clears the scoped F1_CARD pointer AND
        # deletes the now-dead card files, the seed marker, and this track's
        # metadata. Clearing the pointer alone used to leave two cards and no
        # pointer on a two-track machine, which hard-exits *every* attendee tool
        # with "Multiple credential cards found". Only on success — removing any
        # of it after a failed destroy would hide live resources.
        removed = meta.retire_track(root, TRACK)
        if removed:
            print("\nRemoved: " + ", ".join(removed))
        # Clean slate: no state, no .terraform, no lock left behind. Only on
        # success — keeping state after a failure is what makes a retry possible.
        cleanup_terraform_artifacts(ss_path)
        print("\n=== Teardown complete ===")
    else:
        print(f"\nTeardown failed — state kept at {ss_path / 'terraform.tfstate'}")
        print("Inspect terraform/self-service and re-run `uv run selfservice down`.")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(prog="selfservice", description="F1 self-service (solo) mode")
    sub = parser.add_subparsers(dest="command", required=True)

    p_up = sub.add_parser("up", help="Provision the Confluent environment + credential card + seed data")
    p_up.add_argument("--automated", action="store_true", help="Use credentials.env values without prompting")
    p_up.add_argument(
        "--with-labs",
        action="store_true",
        help=(
            "Also build the LAB 3 / LAB 4 Flink objects from demo-reference/ so the "
            "environment is ready to demo. Omit to write them yourself."
        ),
    )
    p_up.set_defaults(func=up)

    p_down = sub.add_parser("down", help="Tear down the self-service environment")
    p_down.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    p_down.set_defaults(func=down)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
