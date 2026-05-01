"""
Login verification utilities.

Provides functions for:
- Checking Confluent CLI login status
- Checking AWS CLI configuration
- Checking Terraform installation
"""

import subprocess


def check_confluent_login() -> bool:
    """Return True if the Confluent CLI has an active session."""
    try:
        result = subprocess.run(["confluent", "environment", "list"], capture_output=True, text=True, check=True)
        return "ID" in result.stdout and "env-" in result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _try_confluent_login(username: str, password: str) -> bool:
    """Attempt a non-interactive Confluent CLI login via env vars. Returns True on success."""
    try:
        env = {**__import__("os").environ, "CONFLUENT_CLOUD_EMAIL": username, "CONFLUENT_CLOUD_PASSWORD": password}
        result = subprocess.run(["confluent", "login"], capture_output=True, text=True, env=env)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def ensure_confluent_login(creds: dict) -> bool:
    """
    Ensure the Confluent CLI has an active session.

    If already logged in, returns True immediately. If not, attempts auto-login
    using TF_VAR_confluent_username / TF_VAR_confluent_password from creds.
    Falls back to an error message telling the user to run 'confluent login'.

    Returns True if logged in, False if login could not be established.
    """
    if check_confluent_login():
        return True

    username = creds.get("TF_VAR_confluent_username", "").strip()
    password = creds.get("TF_VAR_confluent_password", "").strip()

    if username and password:
        print("  Not logged into Confluent Cloud — attempting auto-login...")
        if _try_confluent_login(username, password):
            print("  Logged in successfully")
            return True
        print("Error: Auto-login failed. Check TF_VAR_confluent_username / TF_VAR_confluent_password in credentials.env")
        return False

    print("Error: Not logged into Confluent Cloud.")
    print("Run: confluent login")
    print("(Or set TF_VAR_confluent_username and TF_VAR_confluent_password in credentials.env for auto-login)")
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
