"""
Destroy all F1 demo resources (demo first, then core).
"""

import json
import os
import shutil
import sys
from pathlib import Path

from .credentials import load_or_create_credentials_file
from .terraform import get_project_root, run_terraform_output
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


def cleanup_terraform_artifacts(env_path: Path) -> None:
    """
    Remove terraform artifacts from a directory after successful destroy.

    Removes *.tfstate*, *.tfvars*, .terraform/, .terraform.lock.hcl.
    """
    try:
        for tfstate_file in env_path.glob("*.tfstate*"):
            tfstate_file.unlink()

        for tfvars_file in env_path.glob("*.tfvars*"):
            tfvars_file.unlink()

        terraform_dir = env_path / ".terraform"
        if terraform_dir.exists():
            shutil.rmtree(terraform_dir)

        lock_file = env_path / ".terraform.lock.hcl"
        if lock_file.exists():
            lock_file.unlink()

    except Exception:
        pass


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

    # Attendee resources first (they reference the shared layer), then shared.
    envs_to_destroy = ["aws", "aws-shared"]

    # Check what's actually deployed
    deployed = []
    for env in envs_to_destroy:
        state_file = root / "terraform" / env / "terraform.tfstate"
        if state_file.exists():
            deployed.append(env)

    if not deployed:
        print("Nothing to destroy (no terraform state files found).")
        sys.exit(0)

    print(f"Will destroy: {', '.join(deployed)}")
    print("\nWARNING: This will permanently destroy all resources!")

    confirm = input("\nProceed? (y/n): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        sys.exit(0)

    print("\n=== Starting Destroy ===")
    for env in envs_to_destroy:
        env_path = root / "terraform" / env
        state_file = env_path / "terraform.tfstate"

        if not state_file.exists():
            print(f"\nSkipping {env}: no state found")
            continue

        # The aws tier has required shared_* variables that Terraform evaluates
        # even on destroy — populate them from the aws-shared outputs.
        if env == "aws":
            _inject_shared_vars(root)

        print(f"\n-> Destroying {env}...")
        success = run_terraform_destroy(env_path)
        cleanup_terraform_artifacts(env_path)
        if not success:
            print(f"\nDestroy failed at {env} (state cleaned up). Continuing with remaining...")

    print("\nDestroy process completed!")


if __name__ == "__main__":
    main()
