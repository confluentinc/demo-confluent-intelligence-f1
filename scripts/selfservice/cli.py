"""``selfservice`` — provision the whole workshop for one person, Confluent-only.

  uv run selfservice up      # apply terraform/self-service, write a credential
                             # card, seed driver_race_history
  uv run selfservice down    # tear the environment down

Unlike ``uv run deploy`` (which also stands up EC2 Postgres + an ECS simulator +
CDC), self-service provisions Confluent Cloud only. The user runs the simulator
locally with ``uv run f1-race`` and seeds ``driver_race_history`` with a bounded
Flink INSERT — no Docker, no AWS infrastructure. AWS Bedrock *credentials* are
still needed (they back the LAB 4 LLM model); mint them with
``uv run api-keys create``.
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import dotenv_values, set_key

from scripts.common.credentials import generate_confluent_api_keys, load_or_create_credentials_file
from scripts.common.login_checks import check_terraform_installed, ensure_confluent_login
from scripts.common.terraform import cleanup_terraform_artifacts, get_project_root, run_terraform_output
from scripts.common.terraform_runner import run_terraform, run_terraform_destroy
from scripts.common.ui import prompt_with_default
from scripts.selfservice.seed import seed_driver_race_history
from scripts.workshop import creds as creds_mod

RUN_NAME = "selfservice"
REGION = "us-east-1"

# credentials.env keys (shared with `uv run deploy` so the file is reusable).
REQUIRED = {
    "TF_VAR_confluent_cloud_api_key": "Confluent Cloud API Key",
    "TF_VAR_confluent_cloud_api_secret": "Confluent Cloud API Secret",
    "TF_VAR_owner_email": "Owner email (tags the Confluent environment)",
    "TF_VAR_aws_bedrock_access_key": "AWS Bedrock Access Key",
    "TF_VAR_aws_bedrock_secret_key": "AWS Bedrock Secret Key",
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
    variables as apply. Shared by `selfservice down` and `uv run destroy`.
    """
    cfg = {
        "prefix": creds.get("TF_VAR_prefix", "") or "solo",
        "owner_email": creds.get("TF_VAR_owner_email", ""),
        "api_key": creds.get("TF_VAR_confluent_cloud_api_key", ""),
        "api_secret": creds.get("TF_VAR_confluent_cloud_api_secret", ""),
        "aws_bedrock_key": creds.get("TF_VAR_aws_bedrock_access_key", ""),
        "aws_bedrock_secret": creds.get("TF_VAR_aws_bedrock_secret_key", ""),
        "aws_session_token": creds.get("TF_VAR_aws_session_token", ""),
    }
    for k, v in _tf_env(cfg).items():
        os.environ[k] = v


def _collect_config(creds_file, creds: dict, automated: bool) -> dict[str, str]:
    """Gather secrets/config, prompting interactively unless --automated."""
    if automated:
        cfg = {
            "api_key": creds.get("TF_VAR_confluent_cloud_api_key", ""),
            "api_secret": creds.get("TF_VAR_confluent_cloud_api_secret", ""),
            "owner_email": creds.get("TF_VAR_owner_email", ""),
            "aws_bedrock_key": creds.get("TF_VAR_aws_bedrock_access_key", ""),
            "aws_bedrock_secret": creds.get("TF_VAR_aws_bedrock_secret_key", ""),
            "aws_session_token": creds.get("TF_VAR_aws_session_token", ""),
            "prefix": creds.get("TF_VAR_prefix", "") or "solo",
        }
        missing = [label for key, label in REQUIRED.items() if not creds.get(key)]
        if missing:
            print(f"Error: credentials.env is missing required values: {', '.join(missing)}")
            sys.exit(1)
        return cfg

    generate = input("\nGenerate new Confluent Cloud API keys? (y/n) [n]: ").strip().lower()
    if generate == "y":
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
    while True:
        prefix = prompt_with_default(
            "Environment prefix (alphanumeric, max 12 chars)", creds.get("TF_VAR_prefix", "") or "solo"
        )
        if prefix and prefix.isalnum() and len(prefix) <= 12:
            break
        print("  Must be alphanumeric, max 12 characters.")
    cfg["prefix"] = prefix
    if cfg["aws_bedrock_key"].startswith("ASIA"):
        cfg["aws_session_token"] = prompt_with_default(
            "AWS Session Token (required for temporary credentials)", creds.get("TF_VAR_aws_session_token", "")
        )

    persist = {
        "TF_VAR_confluent_cloud_api_key": cfg["api_key"],
        "TF_VAR_confluent_cloud_api_secret": cfg["api_secret"],
        "TF_VAR_owner_email": cfg["owner_email"],
        "TF_VAR_prefix": cfg["prefix"],
        "TF_VAR_aws_bedrock_access_key": cfg["aws_bedrock_key"],
        "TF_VAR_aws_bedrock_secret_key": cfg["aws_bedrock_secret"],
    }
    for k, v in persist.items():
        set_key(str(creds_file), k, v)
    if cfg["aws_session_token"]:
        set_key(str(creds_file), "TF_VAR_aws_session_token", cfg["aws_session_token"])
    return cfg


def up(args: argparse.Namespace) -> None:
    print("=== F1 Workshop — Self-Service (Confluent-only, run one person) ===\n")
    root = get_project_root()

    if not check_terraform_installed():
        print("Error: Terraform not found. Install from https://developer.hashicorp.com/terraform/install")
        sys.exit(1)

    creds_file, creds = load_or_create_credentials_file(root)
    cfg = _collect_config(creds_file, creds, args.automated)

    print("\n--- Summary ---")
    print(f"  Region:  {REGION}")
    print(f"  Prefix:  {cfg['prefix']}")
    print(f"  Owner:   {cfg['owner_email']}")
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
    run_root = root / "runs" / RUN_NAME
    creds_dir = run_root / "credentials"
    creds_dir.mkdir(parents=True, exist_ok=True)
    fields = creds_mod._card_fields(cfg["prefix"], cfg["owner_email"], out, social_feed_url="", region=REGION)
    creds_mod._write_env(creds_dir, fields)
    creds_mod._write_md(creds_dir, fields)
    card_path = creds_dir / f"{cfg['prefix']}.env"
    print(f"\nCredential card: {card_path}")

    # Seed driver_race_history (once — the topic is append-only).
    marker = run_root / ".seeded"
    if marker.exists():
        print("driver_race_history already seeded (delete runs/selfservice/.seeded to re-seed).")
    else:
        print("Seeding driver_race_history (198 rows) via Flink INSERT...")
        card = dotenv_values(card_path)
        if seed_driver_race_history(card):
            marker.write_text("seeded\n")
            print("  seeded 198 rows")
        else:
            print("  seeding failed — re-run `uv run selfservice up` to retry.")

    rel = f"runs/{RUN_NAME}/credentials/{cfg['prefix']}.env"
    print("\n=== Ready ===\n")
    print("1. Start the live race feed (leave running in its own terminal):")
    print(f"     uv run f1-race --creds {rel}")
    print("2. Open the SQL shell for the labs:")
    print(f"     uv run f1-sql --creds {rel}")
    print("3. Open the live dashboard:")
    print(f"     uv run f1-pitwall --creds {rel}")
    print("\nWork through labs/instructor-led: LAB 1 → LAB 4, then LAB 6.")
    print("Optional LAB 5 (watsonx Orchestrate) — see docs/SELF-SERVICE.md.")
    print("\nTear down when finished:  uv run selfservice down")


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
        marker = root / "runs" / RUN_NAME / ".seeded"
        marker.unlink(missing_ok=True)
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
    p_up.set_defaults(func=up)

    p_down = sub.add_parser("down", help="Tear down the self-service environment")
    p_down.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    p_down.set_defaults(func=down)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
