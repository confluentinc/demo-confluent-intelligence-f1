"""
Terraform utilities for project root detection, state file reading, and cleanup.
"""

import json
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def cleanup_terraform_artifacts(env_path: Path) -> None:
    """
    Remove terraform artifacts from a directory after a *successful* destroy.

    Removes *.tfstate*, *.tfvars*, .terraform/, .terraform.lock.hcl.

    Only ever call this when the destroy succeeded — deleting state after a
    failed destroy orphans whatever Terraform did not manage to delete, leaving
    live cloud resources that nothing points at.
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


def run_terraform_output(state_path: Path) -> dict[str, any]:
    """
    Run terraform output and return the results as a dictionary.

    Args:
        state_path: Path to the terraform state file

    Returns:
        Dictionary of terraform outputs (keys -> values, unwrapped from terraform format)
    """
    try:
        cmd = ["terraform", "output", "-json", f"-state={state_path}"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        outputs = json.loads(result.stdout)

        # Extract values from terraform output format
        return {key: value["value"] for key, value in outputs.items()}
    except FileNotFoundError:
        logger.error("Terraform binary not found. Please install terraform.")
        raise
    except subprocess.CalledProcessError as e:
        logger.error(f"Terraform output failed: {e.stderr}")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse terraform output JSON: {e}")
        raise


def get_project_root() -> Path:
    """
    Find the project root directory by looking for pyproject.toml.

    Checks the current working directory and its parents first,
    then falls back to the script location.

    Returns:
        Path to project root
    """
    # First try current working directory and its parents
    cwd = Path.cwd().resolve()
    for parent in [cwd, *list(cwd.parents)]:
        if (parent / "pyproject.toml").exists():
            return parent

    # Fall back to script location
    current = Path(__file__).resolve()
    for parent in [current, *list(current.parents)]:
        if (parent / "pyproject.toml").exists():
            return parent

    raise FileNotFoundError("Could not find project root (pyproject.toml not found in any parent directory)")
