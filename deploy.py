#!/usr/bin/env python3
"""
Deployment script for the F1 Pit Wall AI Demo.

Prompts for credentials, writes terraform.tfvars, and deploys core + demo infrastructure.
Usage: uv run deploy [--automated]
"""

import argparse
import os
import sys

from dotenv import set_key

from scripts.common.credentials import (
    generate_confluent_api_keys,
    load_or_create_credentials_file,
)
from scripts.common.login_checks import (
    check_aws_configured,
    check_confluent_login,
    check_terraform_installed,
)
from scripts.common.terraform import get_project_root
from scripts.common.terraform_runner import run_terraform
from scripts.common.tfvars import write_tfvars_for_deployment
from scripts.common.ui import prompt_with_default


def main():
    """Main entry point for deploy."""
    parser = argparse.ArgumentParser(description="Deploy the F1 Pit Wall AI Demo")
    parser.add_argument(
        "--automated",
        action="store_true",
        default=False,
        help="Deploy the full Flink job graph (Jobs 1 & 2) via Terraform in addition to Job 0.",
    )
    args = parser.parse_args()

    print("=== F1 Pit Wall AI Demo - Deploy ===\n")

    root = get_project_root()

    # --- Prerequisite checks ---

    if not check_terraform_installed():
        print("Error: Terraform not found. Install from https://developer.hashicorp.com/terraform/install")
        sys.exit(1)
    print("  Terraform installed")

    if not check_confluent_login():
        print("\nError: Not logged into Confluent Cloud.")
        print("Run: confluent login")
        sys.exit(1)
    print("  Confluent CLI logged in")

    if not check_aws_configured():
        print("\nError: AWS CLI not configured.")
        print("Run: aws configure")
        sys.exit(1)
    print("  AWS CLI configured")

    # --- Load or create credentials file ---

    creds_file, creds = load_or_create_credentials_file(root)

    # --- Generate API keys (optional) ---

    generate = input("\nGenerate new Confluent Cloud API keys? (y/n) [n]: ").strip().lower()
    if generate == "y":
        api_key, api_secret = generate_confluent_api_keys()
        if api_key and api_secret:
            set_key(str(creds_file), "TF_VAR_confluent_cloud_api_key", api_key)
            set_key(str(creds_file), "TF_VAR_confluent_cloud_api_secret", api_secret)
            creds["TF_VAR_confluent_cloud_api_key"] = api_key
            creds["TF_VAR_confluent_cloud_api_secret"] = api_secret

    # --- Prompt for credentials ---

    print("\n--- Configuration ---\n")

    api_key = prompt_with_default(
        "Confluent Cloud API Key",
        creds.get("TF_VAR_confluent_cloud_api_key", ""),
    )
    api_secret = prompt_with_default(
        "Confluent Cloud API Secret",
        creds.get("TF_VAR_confluent_cloud_api_secret", ""),
    )
    owner_email = prompt_with_default(
        "Owner email (for AWS resource tagging)",
        creds.get("TF_VAR_owner_email", ""),
    )
    while True:
        deployment_id = prompt_with_default(
            "Deployment ID (alphanumeric, max 8 chars, e.g. PROD or your initials)",
            creds.get("TF_VAR_deployment_id", ""),
        )
        if deployment_id and deployment_id.isalnum() and len(deployment_id) <= 8:
            break
        print("  Must be alphanumeric, max 8 characters.")
    aws_bedrock_key = prompt_with_default(
        "AWS Bedrock Access Key",
        creds.get("TF_VAR_aws_bedrock_access_key", ""),
    )
    aws_bedrock_secret = prompt_with_default(
        "AWS Bedrock Secret Key",
        creds.get("TF_VAR_aws_bedrock_secret_key", ""),
    )
    aws_session_token = ""
    if aws_bedrock_key.startswith("ASIA"):
        aws_session_token = prompt_with_default(
            "AWS Session Token (required for temporary credentials)",
            creds.get("TF_VAR_aws_session_token", ""),
        )

    # --- Save to credentials.env ---

    set_key(str(creds_file), "TF_VAR_confluent_cloud_api_key", api_key)
    set_key(str(creds_file), "TF_VAR_confluent_cloud_api_secret", api_secret)
    set_key(str(creds_file), "TF_VAR_owner_email", owner_email)
    set_key(str(creds_file), "TF_VAR_deployment_id", deployment_id)
    set_key(str(creds_file), "TF_VAR_aws_bedrock_access_key", aws_bedrock_key)
    set_key(str(creds_file), "TF_VAR_aws_bedrock_secret_key", aws_bedrock_secret)
    if aws_session_token:
        set_key(str(creds_file), "TF_VAR_aws_session_token", aws_session_token)

    # Reload creds dict with saved values
    creds = {
        "TF_VAR_confluent_cloud_api_key": api_key,
        "TF_VAR_confluent_cloud_api_secret": api_secret,
        "TF_VAR_owner_email": owner_email,
        "TF_VAR_deployment_id": deployment_id,
        "TF_VAR_aws_bedrock_access_key": aws_bedrock_key,
        "TF_VAR_aws_bedrock_secret_key": aws_bedrock_secret,
        "TF_VAR_aws_session_token": aws_session_token,
    }

    # --- Write terraform.tfvars ---

    print("\nWriting terraform.tfvars...")
    write_tfvars_for_deployment(root, creds)

    # --- Confirm ---

    print("\n--- Deployment Summary ---")
    print("  Region:     us-east-1")
    print(f"  Owner:      {owner_email}")
    print(f"  Deploy ID:  {deployment_id}")
    print(f"  CC Key:     {api_key[:8]}...")
    print(f"  Bedrock:    {aws_bedrock_key[:8]}..." if aws_bedrock_key else "  Bedrock:    (not set)")
    print("  Deploys:    core -> demo")
    if args.automated:
        print("  Mode:       automated (full pipeline — Jobs 1 & 2 via Terraform)")
    else:
        print("  Mode:       minimal (Jobs 1 & 2 manual via Flink SQL Workspace)")

    confirm = input("\nReady to deploy? (y/n): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        sys.exit(0)

    # --- Deploy ---

    print("\n=== Starting Deployment ===")

    # Load credentials into environment for Terraform
    for key, value in creds.items():
        os.environ[key] = value

    if args.automated:
        os.environ["TF_VAR_automated"] = "true"

    # Make AWS provider resilient to transient network failures (DNS hiccups,
    # VPN reconnects, brief socket timeouts). Without this, a single failed
    # DNS lookup against an AWS endpoint can fail the whole terraform apply.
    # `adaptive` retry mode handles network-layer errors in addition to the
    # default API-throttling retries.
    os.environ.setdefault("AWS_RETRY_MODE", "adaptive")
    os.environ.setdefault("AWS_MAX_ATTEMPTS", "10")

    # Deploy core first
    core_path = root / "terraform" / "core"
    if not run_terraform(core_path):
        print("\nCore deployment failed. Stopping.")
        sys.exit(1)

    # Then deploy demo
    demo_path = root / "terraform" / "demo"
    if not run_terraform(demo_path):
        print("\nDemo deployment failed.")
        print("Core infrastructure is still running. Run 'uv run destroy' to clean up.")
        sys.exit(1)

    # --- Success ---

    print("\n=== Deployment Complete ===")
    print()
    print("Next steps:")
    print("  1. Start the race simulator:  ./scripts/start-race.sh")
    print("  2. Set up MCP for Claude:     uv run setup-mcp")
    if args.automated:
        print("  3. Jobs 1 & 2 already deployed — data flows as soon as the race starts")
    else:
        print("  3. Follow the Walkthrough.md for the demo flow")
    print()
    print("To tear down all resources:     uv run destroy")


if __name__ == "__main__":
    main()
