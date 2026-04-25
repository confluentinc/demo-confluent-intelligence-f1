"""
Terraform execution wrapper utilities.

Provides functions for:
- Running terraform init and apply
- Running terraform destroy
"""

import subprocess
import sys
from pathlib import Path


def run_terraform(env_path: Path, auto_approve: bool = True) -> bool:
    """
    Run terraform init and apply in the specified directory.

    Args:
        env_path: Path to terraform directory
        auto_approve: Whether to auto-approve terraform apply

    Returns:
        True if successful, False otherwise
    """
    print(f"\nInitializing Terraform in {env_path.name}...")

    try:
        subprocess.run(["terraform", "init"], cwd=env_path, check=True)

        apply_cmd = ["terraform", "apply"]
        if auto_approve:
            apply_cmd.append("-auto-approve")

        print(f"Running terraform apply in {env_path.name}...")
        subprocess.run(apply_cmd, cwd=env_path, check=True)

        print(f"Deployment successful: {env_path.name}")
        return True

    except subprocess.CalledProcessError:
        print(f"Terraform failed in {env_path.name}")
        return False
    except FileNotFoundError:
        print("Error: Terraform not found. Please install Terraform first.")
        sys.exit(1)


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
