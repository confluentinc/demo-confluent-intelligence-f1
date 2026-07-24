#!/usr/bin/env python3
"""
Standalone deploy for a SINGLE F1 workshop environment (organizer smoke-test).

Provisions the two-tier layout for one attendee prefix:
  1. terraform/aws-shared  — shared VPC/subnets, Postgres, ECR + simulator image
  2. terraform/aws         — one Confluent environment + ECS simulator, wired to
                             the shared outputs

For a real multi-attendee workshop, use `wsa` with wsa-spec-aws.yaml instead —
this script is the manual single-environment equivalent.

Usage: uv run deploy [--automated]
"""

import argparse
import json
import os
import sys

from dotenv import set_key

from scripts.common.credentials import (
    generate_confluent_api_keys,
    load_or_create_credentials_file,
)
from scripts.common.login_checks import (
    check_aws_configured,
    check_terraform_installed,
    ensure_confluent_login,
)
from scripts.common.terraform import get_project_root, run_terraform_output
from scripts.common.terraform_runner import run_terraform
from scripts.common.ui import prompt_with_default

SHARED_PREFIX = "f1-workshop"


def main():
    parser = argparse.ArgumentParser(description="Deploy a single F1 workshop environment")
    parser.add_argument(
        "--automated",
        action="store_true",
        default=False,
        help="Use credentials.env values without prompting.",
    )
    args = parser.parse_args()

    print("=== F1 Workshop — Standalone Deploy (shared + one attendee) ===\n")

    root = get_project_root()

    if not check_terraform_installed():
        print("Error: Terraform not found. Install from https://developer.hashicorp.com/terraform/install")
        sys.exit(1)
    print("  Terraform installed")

    creds_file, creds = load_or_create_credentials_file(root)

    if not ensure_confluent_login(creds):
        sys.exit(1)
    print("  Confluent CLI logged in")

    if not check_aws_configured():
        print("\nError: AWS CLI not configured. Run: aws configure")
        sys.exit(1)
    print("  AWS CLI configured")

    if args.automated:
        print("\n--- Automated mode: using credentials.env values ---\n")
        api_key = creds.get("TF_VAR_confluent_cloud_api_key", "")
        api_secret = creds.get("TF_VAR_confluent_cloud_api_secret", "")
        owner_email = creds.get("TF_VAR_owner_email", "")
        prefix = creds.get("TF_VAR_prefix", "")
        aws_bedrock_key = creds.get("TF_VAR_aws_bedrock_access_key", "")
        aws_bedrock_secret = creds.get("TF_VAR_aws_bedrock_secret_key", "")
        aws_session_token = creds.get("TF_VAR_aws_session_token", "")

        missing = [
            k
            for k, v in {
                "TF_VAR_confluent_cloud_api_key": api_key,
                "TF_VAR_confluent_cloud_api_secret": api_secret,
                "TF_VAR_owner_email": owner_email,
                "TF_VAR_prefix": prefix,
                "TF_VAR_aws_bedrock_access_key": aws_bedrock_key,
                "TF_VAR_aws_bedrock_secret_key": aws_bedrock_secret,
            }.items()
            if not v
        ]
        if missing:
            print(f"Error: credentials.env is missing required values: {', '.join(missing)}")
            sys.exit(1)
    else:
        generate = input("\nGenerate new Confluent Cloud API keys? (y/n) [n]: ").strip().lower()
        if generate == "y":
            api_key, api_secret = generate_confluent_api_keys()
            if api_key and api_secret:
                set_key(str(creds_file), "TF_VAR_confluent_cloud_api_key", api_key)
                set_key(str(creds_file), "TF_VAR_confluent_cloud_api_secret", api_secret)

        print("\n--- Configuration ---\n")
        api_key = prompt_with_default("Confluent Cloud API Key", creds.get("TF_VAR_confluent_cloud_api_key", ""))
        api_secret = prompt_with_default(
            "Confluent Cloud API Secret", creds.get("TF_VAR_confluent_cloud_api_secret", "")
        )
        owner_email = prompt_with_default("Owner email (for AWS resource tagging)", creds.get("TF_VAR_owner_email", ""))
        while True:
            prefix = prompt_with_default(
                "Attendee prefix (alphanumeric, max 12 chars, e.g. demo or your initials)",
                creds.get("TF_VAR_prefix", ""),
            )
            if prefix and prefix.isalnum() and len(prefix) <= 12:
                break
            print("  Must be alphanumeric, max 12 characters.")
        aws_bedrock_key = prompt_with_default("AWS Bedrock Access Key", creds.get("TF_VAR_aws_bedrock_access_key", ""))
        aws_bedrock_secret = prompt_with_default(
            "AWS Bedrock Secret Key", creds.get("TF_VAR_aws_bedrock_secret_key", "")
        )
        aws_session_token = ""
        if aws_bedrock_key.startswith("ASIA"):
            aws_session_token = prompt_with_default(
                "AWS Session Token (required for temporary credentials)",
                creds.get("TF_VAR_aws_session_token", ""),
            )

        for k, v in {
            "TF_VAR_confluent_cloud_api_key": api_key,
            "TF_VAR_confluent_cloud_api_secret": api_secret,
            "TF_VAR_owner_email": owner_email,
            "TF_VAR_prefix": prefix,
            "TF_VAR_aws_bedrock_access_key": aws_bedrock_key,
            "TF_VAR_aws_bedrock_secret_key": aws_bedrock_secret,
        }.items():
            set_key(str(creds_file), k, v)
        if aws_session_token:
            set_key(str(creds_file), "TF_VAR_aws_session_token", aws_session_token)

    region = "us-east-1"

    print("\n--- Deployment Summary ---")
    print(f"  Region:   {region}")
    print(f"  Owner:    {owner_email}")
    print(f"  Prefix:   {prefix}")
    print(f"  CC Key:   {api_key[:8]}...")
    print(f"  Bedrock:  {aws_bedrock_key[:8]}..." if aws_bedrock_key else "  Bedrock:  (not set)")
    print("  Deploys:  aws-shared -> aws")

    if not args.automated:
        if input("\nReady to deploy? (y/n): ").strip().lower() != "y":
            print("Cancelled.")
            sys.exit(0)

    # AWS provider resilience to transient network failures.
    os.environ.setdefault("AWS_RETRY_MODE", "adaptive")
    os.environ.setdefault("AWS_MAX_ATTEMPTS", "10")

    # --- 1. Shared infrastructure ---
    print("\n=== Deploying shared infrastructure (aws-shared) ===")
    shared_path = root / "terraform" / "aws-shared"
    shared_env = {
        "TF_VAR_region": region,
        "TF_VAR_owner_email": owner_email,
        "TF_VAR_prefix": SHARED_PREFIX,
        "TF_VAR_attendee_count": "1",
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
        "TF_VAR_prefix": prefix,
        "TF_VAR_owner_email": owner_email,
        "TF_VAR_region": region,
        "TF_VAR_confluent_cloud_api_key": api_key,
        "TF_VAR_confluent_cloud_api_secret": api_secret,
        "TF_VAR_aws_bedrock_access_key": aws_bedrock_key,
        "TF_VAR_aws_bedrock_secret_key": aws_bedrock_secret,
        "TF_VAR_aws_session_token": aws_session_token,
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

    print("\n=== Deployment Complete ===\n")
    print("The race simulator runs as an always-on ECS service (RACE_LOOP=true) — the feed is")
    print("already live. View attendee credentials with:")
    print("  cd terraform/aws && terraform output -json attendee_credentials")
    print("\nTo tear down all resources:  uv run destroy")


if __name__ == "__main__":
    main()
