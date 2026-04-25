"""
Terraform utilities for project root detection and state file reading.
"""

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


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
