"""
Login verification utilities.

Provides functions for:
- Checking Confluent CLI login status
- Keeping the Confluent CLI session alive (prompt once, then auto-login)
- Checking AWS CLI configuration
- Checking Docker daemon availability
- Checking Terraform installation
"""

import getpass
import os
import subprocess
from pathlib import Path

from dotenv import dotenv_values, set_key

USERNAME_KEY = "TF_VAR_confluent_username"
PASSWORD_KEY = "TF_VAR_confluent_password"


def check_confluent_login() -> bool:
    """Return True if the Confluent CLI has an active session.

    Uses the exit code rather than parsing stdout: `confluent environment list`
    exits 1 with "Error: not logged in" when there is no session, and exits 0 for
    a valid session even in an org with zero environments (e.g. right after
    `uv run destroy`), which stdout parsing would misread as logged out.
    """
    try:
        result = subprocess.run(["confluent", "environment", "list"], capture_output=True, text=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def _try_confluent_login(username: str, password: str) -> bool:
    """Attempt a non-interactive Confluent CLI login via env vars. Returns True on success.

    Passes `--save` so the CLI stores the credentials locally and silently
    re-authenticates when the 8-hour token expires — that is what keeps the user
    logged in across later script runs instead of re-prompting.
    """
    try:
        env = {**os.environ, "CONFLUENT_CLOUD_EMAIL": username, "CONFLUENT_CLOUD_PASSWORD": password}
        result = subprocess.run(["confluent", "login", "--save"], capture_output=True, text=True, env=env)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def _prompt_and_save_login(creds_file, creds: dict) -> bool:
    """Prompt for Confluent Cloud login once and persist it for future auto-login.

    Credentials are only written after a login actually succeeds, so a typo never
    gets saved. Returns True if logged in; False if the user skipped or gave up
    (callers decide whether that is fatal).
    """
    print("\nConfluent Cloud login (saved to credentials.env so you're not asked again):")
    creds_file = Path(creds_file)
    creds_file.touch(exist_ok=True)  # set_key needs the file to exist
    for attempt in range(3):
        username = input("  Email (press Enter to skip): ").strip()
        if not username:
            print("  Skipped. Run `confluent login` manually if your session expires.")
            return False
        password = getpass.getpass("  Password: ")

        if _try_confluent_login(username, password):
            set_key(str(creds_file), USERNAME_KEY, username)
            set_key(str(creds_file), PASSWORD_KEY, password)
            creds[USERNAME_KEY] = username
            creds[PASSWORD_KEY] = password
            print("  Logged in and saved.")
            return True

        remaining = 2 - attempt
        if remaining:
            print(f"  Login failed. {remaining} attempt{'s' if remaining > 1 else ''} remaining.")
        else:
            print("  Login failed. Run `confluent login` manually.")
            print("  (SSO accounts cannot auto-login — use `confluent login --no-browser`.)")
    return False


def ensure_confluent_login(creds: dict | None = None, creds_file=None, interactive: bool = False) -> bool:
    """
    Ensure the Confluent CLI has an active session.

    Order: existing session → auto-login from saved credentials → (interactive
    only) prompt once and save. Together with `--save`, the user supplies their
    Confluent Cloud login a single time and later script runs re-authenticate
    without asking again.

    Args:
        creds: credentials.env values; loaded from the project root when None.
        creds_file: path to credentials.env, required to persist a prompted login.
        interactive: prompt for credentials when none are saved.

    Returns True if logged in, False if login could not be established.
    """
    if check_confluent_login():
        return True

    if creds is None:
        creds = dict(dotenv_values(Path(__file__).resolve().parents[2] / "credentials.env"))

    # dotenv_values yields None for bare keys, so coerce before stripping.
    username = (creds.get(USERNAME_KEY) or "").strip()
    password = (creds.get(PASSWORD_KEY) or "").strip()

    if username and password:
        print("  Not logged into Confluent Cloud — attempting auto-login...")
        if _try_confluent_login(username, password):
            print("  Logged in successfully")
            return True
        print(f"  Auto-login failed. Check {USERNAME_KEY} / {PASSWORD_KEY} in credentials.env")
        if interactive and creds_file:
            return _prompt_and_save_login(creds_file, creds)
        return False

    if interactive and creds_file:
        return _prompt_and_save_login(creds_file, creds)

    print("Error: Not logged into Confluent Cloud.")
    print("Run: confluent login --save")
    print(f"(Or set {USERNAME_KEY} and {PASSWORD_KEY} in credentials.env for auto-login)")
    return False


def check_terraform_installed() -> bool:
    """
    Check if Terraform CLI is installed.

    Returns:
        True if installed, False otherwise
    """
    try:
        subprocess.run(["terraform", "version"], capture_output=True, text=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def check_docker_running() -> bool:
    """Return True when the Docker CLI can reach a running Docker daemon."""
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def check_aws_configured() -> bool:
    """
    Check if AWS CLI is configured with valid credentials.

    Returns:
        True if configured, False otherwise
    """
    try:
        result = subprocess.run(["aws", "sts", "get-caller-identity"], capture_output=True, text=True, check=True)
        return "Account" in result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
