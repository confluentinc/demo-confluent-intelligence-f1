"""
Login verification utilities.

Provides functions for:
- Checking Confluent CLI login status
- Checking AWS CLI configuration
- Checking Terraform installation
"""

import subprocess


def check_confluent_login() -> bool:
    """
    Check if user is logged into Confluent CLI.

    Returns:
        True if logged in, False otherwise
    """
    try:
        result = subprocess.run(["confluent", "environment", "list"], capture_output=True, text=True, check=True)
        return "ID" in result.stdout and "env-" in result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
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
