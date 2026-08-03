"""`f1-onboard` — self-serve: turn your wsa claim-email values into credentials.env.

  uv run f1-onboard                      # interactive, prompts one field at a time
  uv run f1-onboard --paste               # paste the whole claim email, auto-parse
  uv run f1-onboard --out my-creds.env    # write somewhere other than ./credentials.env

Attendees who claim an account through the wsa dispenser (Google Form -> an
emailed list of labeled values) don't get a ready-made .env file — the
dispenser's email template is generic across every wsa-enabled workshop, not
specific to this one. This wizard takes those labeled values and writes a
local credentials.env in the exact shape `uv run f1-sql --creds`,
`uv run f1-pitwall --creds`, etc. already expect — reusing
scripts.workshop.creds's card-building logic so the instructor-distributed
and self-serve paths never drift apart.

Your Confluent Cloud login isn't needed for any of this — you type that
straight into the browser. The three Console fields are carried anyway so the
written file matches an instructor-issued card exactly.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from scripts.workshop import creds as creds_mod

# Console login fields. Optional, and parsed differently from the rest: a
# generated password draws from "!@#$%^&*-_=+", so the tolerant "-" separator
# used below would eat part of the value. These require a ":" and capture to
# end-of-line. Kept out of the required check so a parse miss prompts instead
# of aborting — none of the local tools authenticate with them.
CONSOLE_FIELDS = [
    ("console_url", "Console URL"),
    ("console_username", "Console Username"),
    ("console_password", "Console Password"),
]

# (internal key, label as it appears in the claim email / CSV column)
FIELDS = [
    ("prefix", "Prefix"),
    ("environment_id", "Environment ID"),
    ("environment_url", "Environment URL"),
    ("environment_name", "Environment Name"),
    ("cluster_id", "Cluster ID"),
    ("cluster_name", "Cluster Name"),
    ("cluster_bootstrap", "Kafka Bootstrap"),
    ("kafka_api_key", "Kafka API Key"),
    ("kafka_api_secret", "Kafka API Secret"),
    ("schema_registry_url", "Schema Registry URL"),
    ("sr_api_key", "SR API Key"),
    ("sr_api_secret", "SR API Secret"),
    ("compute_pool_id", "Compute Pool ID"),
    ("flink_rest_endpoint", "Flink REST Endpoint"),
    ("flink_api_key", "Flink API Key"),
    ("flink_api_secret", "Flink API Secret"),
    ("organization_id", "Organization ID"),
]


def _parse_pasted_email(text: str) -> dict[str, str]:
    """Best-effort extraction of "<label>: <value>" pairs from a pasted claim
    email. Matches on label text regardless of a "Confluent Cloud / " group
    prefix, surrounding markup, or case."""
    found: dict[str, str] = {}
    for key, label in FIELDS:
        # Accepts "Label: value", "Confluent Cloud / Label: value", "Label - value", etc.
        pattern = re.compile(rf"{re.escape(label)}\s*[:\-]\s*(\S+)", re.IGNORECASE)
        m = pattern.search(text)
        if m:
            found[key] = m.group(1).strip()
    for key, label in CONSOLE_FIELDS:
        # ":" only, and capture the rest of the line — see CONSOLE_FIELDS.
        pattern = re.compile(rf"{re.escape(label)}\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
        m = pattern.search(text)
        if m:
            found[key] = m.group(1).strip()
    # Email is a top-level dispenser column, not a "Confluent Cloud" field.
    m = re.search(r"\bEmail\s*[:\-]\s*(\S+@\S+)", text, re.IGNORECASE)
    if m:
        found["email"] = m.group(1).strip()
    return found


def _read_pasted_block() -> str:
    print("Paste your claim email below, then press Enter on an empty line:\n")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "":
            break
        lines.append(line)
    return "\n".join(lines)


def _prompt_fields(prefill: dict[str, str]) -> dict[str, str]:
    values = dict(prefill)
    email = values.get("email", "")
    answer = input(f"Email [{email}]: ").strip()
    values["email"] = answer or email
    for key, label in FIELDS + CONSOLE_FIELDS:
        current = values.get(key, "")
        suffix = f" [{current}]" if current else ""
        optional = " (optional)" if (key, label) in CONSOLE_FIELDS else ""
        answer = input(f"{label}{optional}{suffix}: ").strip()
        values[key] = answer or current
    return values


def _to_terraform_shaped_outputs(values: dict[str, str]) -> dict:
    """Same shape scripts.workshop.creds._card_fields expects: flat identity
    keys plus a nested attendee_credentials dict for the Kafka/SR secrets."""
    return {
        "console_url": values.get("console_url", ""),
        "console_username": values.get("console_username", ""),
        "console_password": values.get("console_password", ""),
        "organization_id": values["organization_id"],
        "environment_id": values["environment_id"],
        "environment_name": values["environment_name"],
        "cluster_id": values["cluster_id"],
        "cluster_name": values["cluster_name"],
        "cluster_bootstrap": values["cluster_bootstrap"],
        "compute_pool_id": values["compute_pool_id"],
        "flink_rest_endpoint": values["flink_rest_endpoint"],
        "attendee_credentials": {
            "environment_url": values["environment_url"],
            "cluster_id": values["cluster_id"],
            "kafka_api_key": values["kafka_api_key"],
            "kafka_api_secret": values["kafka_api_secret"],
            "schema_registry_url": values["schema_registry_url"],
            "sr_api_key": values["sr_api_key"],
            "sr_api_secret": values["sr_api_secret"],
            "flink_api_key": values["flink_api_key"],
            "flink_api_secret": values["flink_api_secret"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build credentials.env from your wsa claim-email values")
    parser.add_argument(
        "--paste",
        action="store_true",
        help="Paste the whole claim email (end with a blank line) instead of answering field-by-field",
    )
    parser.add_argument("--out", default="credentials.env", help="Output path (default: ./credentials.env)")
    parser.add_argument(
        "--social-feed-url", default="", help="LAB 5 race-feed base URL, if your instructor gave you one"
    )
    parser.add_argument("--region", default="us-east-1", help="AWS region (used only to derive the RTCE MCP endpoint)")
    args = parser.parse_args()

    prefill: dict[str, str] = {}
    if args.paste:
        prefill = _parse_pasted_email(_read_pasted_block())
        missing = [label for key, label in FIELDS if key not in prefill]
        if missing:
            print(f"\nCouldn't find: {', '.join(missing)} — fill them in below.\n")

    values = _prompt_fields(prefill)

    missing = [label for key, label in FIELDS if not values.get(key)] + (["Email"] if not values.get("email") else [])
    if missing:
        sys.exit(f"Missing required value(s): {', '.join(missing)}")

    out = _to_terraform_shaped_outputs(values)
    fields = creds_mod._card_fields(values["prefix"], values["email"], out, args.social_feed_url, args.region)

    out_path = Path(args.out)
    lines = [f"F1_{k.upper()}={v}" for k, v in fields.items()]
    out_path.write_text("\n".join(lines) + "\n")

    print(f"\nWrote {out_path}. Next:\n")
    print(f"  uv run f1-sql --creds {out_path}")
    print(f"  uv run f1-pitwall --creds {out_path}")


if __name__ == "__main__":
    main()
