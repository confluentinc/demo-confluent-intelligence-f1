"""
Terraform execution wrapper utilities.

Provides functions for:
- Running terraform init and apply
- Running terraform destroy
"""

import subprocess
import sys
import time
from pathlib import Path

# Transient Confluent/SR/AWS API errors (e.g. "connection reset by peer" while a
# Flink statement registers schemas) fail the apply but leave the resource
# tainted, so an immediate re-apply recovers cleanly. Retry before giving up.
APPLY_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 30


def run_terraform(env_path: Path, auto_approve: bool = True, max_attempts: int = APPLY_ATTEMPTS) -> bool:
    """
    Run terraform init and apply in the specified directory.

    Args:
        env_path: Path to terraform directory
        auto_approve: Whether to auto-approve terraform apply
        max_attempts: Total apply attempts before giving up

    Returns:
        True if successful, False otherwise
    """
    print(f"\nInitializing Terraform in {env_path.name}...")

    try:
        subprocess.run(["terraform", "init"], cwd=env_path, check=True)
    except subprocess.CalledProcessError:
        print(f"Terraform init failed in {env_path.name}")
        return False
    except FileNotFoundError:
        print("Error: Terraform not found. Please install Terraform first.")
        sys.exit(1)

    apply_cmd = ["terraform", "apply"]
    if auto_approve:
        apply_cmd.append("-auto-approve")

    for attempt in range(1, max_attempts + 1):
        suffix = f" (attempt {attempt}/{max_attempts})" if attempt > 1 else ""
        print(f"Running terraform apply in {env_path.name}...{suffix}")
        try:
            subprocess.run(apply_cmd, cwd=env_path, check=True)
            print(f"Deployment successful: {env_path.name}")
            return True
        except subprocess.CalledProcessError:
            if attempt < max_attempts:
                print(
                    f"\nterraform apply failed in {env_path.name} — retrying in {RETRY_DELAY_SECONDS}s "
                    "(transient Confluent/AWS API errors usually clear on re-apply)..."
                )
                time.sleep(RETRY_DELAY_SECONDS)

    print(f"Terraform failed in {env_path.name} after {max_attempts} attempts")
    return False


def run_terraform_destroy(env_path: Path, auto_approve: bool = True) -> bool:
    """
    Run terraform destroy in the specified directory.

    Args:
        env_path: Path to terraform directory
        auto_approve: Whether to auto-approve terraform destroy

    Returns:
        True if successful, False otherwise
    """
    print(f"\nInitializing Terraform in {env_path.name}...")

    try:
        subprocess.run(["terraform", "init"], cwd=env_path, check=True)

        destroy_cmd = ["terraform", "destroy"]
        if auto_approve:
            destroy_cmd.append("-auto-approve")

        print(f"Running terraform destroy in {env_path.name}...")
        subprocess.run(destroy_cmd, cwd=env_path, check=True)

        print(f"Destroy successful: {env_path.name}")
        return True

    except subprocess.CalledProcessError:
        print(f"Terraform destroy failed in {env_path.name}")
        return False
    except FileNotFoundError:
        print("Error: Terraform not found. Please install Terraform first.")
        sys.exit(1)
