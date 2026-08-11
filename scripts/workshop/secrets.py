"""Shared secret collection for workshop organizer commands.

``create-workshop``, ``teardown-workshop``, and the ``workshop build`` /
``workshop clean`` wrappers all need the same five ``TF_VAR_*`` environment
variables that wsa's Terraform runs consume — the destroy as much as the apply.
This module collects them once, following ``deploy.py``'s precedence:

  1. ``os.environ``  (so an ``op run --env-file=<template>`` wrapper still works)
  2. ``credentials.env`` at the project root
  3. Interactive prompt (unless ``interactive=False``, which raises)

Collected values are exported into ``os.environ`` (for the child
``wsa build`` subprocess) and persisted back to ``credentials.env``
so subsequent runs pick them up without re-prompting.
"""

from __future__ import annotations

import os
import stat
from getpass import getpass
from pathlib import Path

from dotenv import set_key

from scripts.common.credentials import load_or_create_credentials_file

REQUIRED_SECRETS = [
    ("TF_VAR_confluent_cloud_api_key", "Confluent Cloud API Key"),
    ("TF_VAR_confluent_cloud_api_secret", "Confluent Cloud API Secret"),
    ("TF_VAR_owner_email", "Owner email (for AWS resource tagging)"),
    ("TF_VAR_aws_bedrock_access_key", "AWS Bedrock Access Key"),
    ("TF_VAR_aws_bedrock_secret_key", "AWS Bedrock Secret Key"),
]

_SECRET_KEYS = {"TF_VAR_confluent_cloud_api_secret", "TF_VAR_aws_bedrock_secret_key"}


def _prompt_value(env_key: str, label: str) -> str:
    if env_key in _SECRET_KEYS:
        while True:
            value = getpass(f"  {label}: ").strip()
            if value:
                return value
            print("    This field is required.")
    while True:
        value = input(f"  {label}: ").strip()
        if value:
            return value
        print("    This field is required.")


def collect_secrets(
    root: Path,
    interactive: bool = True,
) -> tuple[Path, dict[str, str], set[str]]:
    """Collect the five required secrets.

    Returns (creds_file, secrets_dict, prompted_keys). ``prompted_keys``
    tracks which secrets were interactively entered — only those should be
    persisted to disk (secrets from ``os.environ`` stay off disk).

    Raises ``SystemExit`` when ``interactive=False`` and a value is missing.
    """
    creds_file, creds = load_or_create_credentials_file(root)

    secrets: dict[str, str] = {}
    prompted: set[str] = set()
    missing_labels: list[str] = []

    for env_key, label in REQUIRED_SECRETS:
        value = os.environ.get(env_key, "").strip()
        if not value:
            value = (creds.get(env_key) or "").strip()
        if value:
            secrets[env_key] = value
        else:
            missing_labels.append(label)
            secrets[env_key] = ""

    if any(not v for v in secrets.values()):
        if not interactive:
            missing = [label for key, label in REQUIRED_SECRETS if not secrets.get(key)]
            raise SystemExit(
                "Missing required secrets (use --yes only when secrets are in the "
                "environment or credentials.env):\n  " + "\n  ".join(missing)
            )

        if missing_labels:
            print(f"\n{len(missing_labels)} secret(s) not found in environment or credentials.env:")
            for env_key, label in REQUIRED_SECRETS:
                if not secrets[env_key]:
                    secrets[env_key] = _prompt_value(env_key, label)
                    prompted.add(env_key)

    return creds_file, secrets, prompted


def export_secrets(secrets: dict[str, str]) -> None:
    """Set all collected secrets into ``os.environ``."""
    for key, value in secrets.items():
        if value:
            os.environ[key] = value


def persist_secrets(
    creds_file: Path, secrets: dict[str, str], only_keys: set[str] | None = None
) -> None:
    """Write secrets to ``credentials.env`` and chmod 0600.

    When ``only_keys`` is provided, only those keys are written — secrets
    that came from ``os.environ`` stay off disk (the user deliberately
    kept them in their vault / 1Password).
    """
    creds_file.touch(exist_ok=True)
    for key, value in secrets.items():
        if value and (only_keys is None or key in only_keys):
            set_key(str(creds_file), key, value)
    try:
        creds_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def ensure_secrets(root: Path, interactive: bool = True) -> Path:
    """Collect, export, and persist. Returns the creds_file path."""
    creds_file, secrets, prompted = collect_secrets(root, interactive=interactive)
    export_secrets(secrets)
    persist_secrets(creds_file, secrets, only_keys=prompted)
    return creds_file
