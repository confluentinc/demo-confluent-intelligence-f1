"""
Destroy all F1 demo resources (demo first, then core).
"""

import os
import shutil
import sys
from pathlib import Path

from .credentials import load_or_create_credentials_file
from .terraform import get_project_root
from .terraform_runner import run_terraform_destroy


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

    envs_to_destroy = ["demo", "core"]

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

        print(f"\n-> Destroying {env}...")
        success = run_terraform_destroy(env_path)
        cleanup_terraform_artifacts(env_path)
        if not success:
            print(f"\nDestroy failed at {env} (state cleaned up). Continuing with remaining...")

    print("\nDestroy process completed!")
    _cleanup_mcp(root)


def _cleanup_mcp(root: Path) -> None:
    """Remove MCP server config files and deregister from Claude Code."""
    import subprocess

    mcp_env = root / "confluent-mcp.env"
    if mcp_env.exists():
        mcp_env.unlink()
        print("✓ Removed confluent-mcp.env")

    result = subprocess.run(
        ["claude", "mcp", "remove", "confluent-f1-mcp", "-s", "local"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("✓ Removed confluent-f1-mcp from Claude Code")

    node_modules = root / "node_modules"
    if node_modules.exists():
        try:
            shutil.rmtree(node_modules)
            print("✓ Removed node_modules/")
        except Exception:
            pass

    pkg_lock = root / "package-lock.json"
    if pkg_lock.exists():
        pkg_lock.unlink()
        print("✓ Removed package-lock.json")


if __name__ == "__main__":
    main()
