"""
Credential loading and management utilities.

Two distinct files share the ".env" name and must not be confused:

- ``credentials.env`` at the project root — deploy secrets (``TF_VAR_*``), the
  single source of truth for the deploy flows. It also carries an ``F1_CARD``
  pointer at whichever credential card was last provisioned.
- a credential **card**, ``runs/<name>/credentials/<prefix>.env`` (``F1_*``
  keys) — what ``f1-sql`` / ``f1-pitwall`` / ``f1-race`` authenticate with.

A workshop attendee makes ``./credentials.env`` itself a card by pasting their
dispenser Env File block into it, so the resolver below accepts either shape.

Provides functions for:
- Loading credentials from credentials.env files
- Resolving which credential card the attendee tools should use
- Generating Confluent Cloud API keys via CLI
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path

from dotenv import dotenv_values

# Set by deploy.py / `selfservice up` in credentials.env, read by resolve_card().
CARD_POINTER_KEY = "F1_CARD"

# Recognizing a card that arrived as ONE flattened line. The wsa dispenser renders
# the Env File block with spaces instead of newlines, so an attendee who pastes it
# verbatim into credentials.env gets `F1_A=v1 F1_B=v2 ...` on a single line. dotenv
# reads one KEY=VALUE per line, so it returns just the first key (whose value
# swallows the rest) — `_parse_card` detects that and re-extracts every token.
# Safe because no F1_ value contains a space (IDs, URLs, base64 keys, generated
# passwords), so `\S*` captures each value losslessly.
_F1_ASSIGNMENT = re.compile(r"F1_[A-Z0-9_]+=")
_F1_TOKEN = re.compile(r"F1_([A-Z0-9_]+)=(\S*)")

# ``WORKSHOP_EMAIL_PATTERN`` replaced this old setting name. Keep ignoring the
# legacy key so an organizer's pre-upgrade credentials.env cannot shadow a real
# attendee card while they migrate it.
LEGACY_NON_CARD_KEYS = {"F1_WORKSHOP_EMAIL_PATTERN"}

# One-off override, e.g. F1_CREDS=... uv run f1-sql
CARD_ENV_VAR = "F1_CREDS"

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_or_create_credentials_file(root: Path) -> tuple[Path, dict[str, str]]:
    """
    Load existing credentials.env or create an empty one.

    Args:
        root: Project root directory

    Returns:
        Tuple of (credentials file path, credentials dictionary)
    """
    creds_file = root / "credentials.env"

    if creds_file.exists():
        return creds_file, dotenv_values(creds_file)

    creds_file.write_text("TF_VAR_confluent_username=''\nTF_VAR_confluent_password=''\n")
    return creds_file, {}


def _is_card(values: dict[str, str | None]) -> bool:
    """A parsed .env is a credential card if it carries F1_* keys of its own."""
    return any(
        key.startswith("F1_") and key not in {CARD_POINTER_KEY, *LEGACY_NON_CARD_KEYS}
        for key in values
    )


def resolve_card(explicit: str | None = None, root: Path | None = None) -> Path:
    """
    Work out which credential card to use, so attendees never have to name it.

    Order, first hit wins:
      1. an explicit --creds value
      2. $F1_CREDS
      3. credentials.env — either its F1_CARD pointer, or the file itself when
         it holds F1_* keys (an attendee's pasted Env File block)
      4. the only card lying around — under runs/*/credentials/, or a loose
         *.env at the project root (an instructor-handed f1wp001.env)

    Exits with an actionable message when nothing is found or the choice is
    ambiguous, rather than raising.
    """
    root = root or PROJECT_ROOT

    if explicit:
        return Path(explicit)

    from_env = os.environ.get(CARD_ENV_VAR)
    if from_env:
        return Path(from_env)

    creds_file = root / "credentials.env"
    if creds_file.exists():
        values = dotenv_values(creds_file)
        pointer = values.get(CARD_POINTER_KEY)
        if pointer:
            # A pointer left behind by `destroy` names a card that no longer
            # exists — fall through rather than failing on a dead environment.
            card = Path(pointer)
            if not card.is_absolute():
                card = root / card
            if card.exists():
                return card
        if _is_card(values):
            return creds_file

    # Loose root-level .env files are only candidates if they actually carry
    # F1_* keys — the root holds unrelated .env files (deploy TF_VARs, MCP
    # config) that must never be mistaken for a card.
    loose = [p for p in root.glob("*.env") if _is_card(dotenv_values(p))]
    candidates = sorted(set(root.glob("runs/*/credentials/*.env")) | set(loose))
    if len(candidates) == 1:
        return candidates[0]

    if not candidates:
        sys.exit(
            "No credential card found.\n"
            "  Run `uv run deploy`, or (workshop attendee) save your dispenser Env File\n"
            "  block as `credentials.env`, or pass `--creds <path>` explicitly."
        )

    listed = "\n".join(f"    {c.relative_to(root)}" for c in candidates)
    sys.exit(
        "Multiple credential cards found — I won't guess which environment you meant:\n"
        f"{listed}\n"
        "  Pass `--creds <path>`, or re-run `uv run deploy` to set F1_CARD in credentials.env."
    )


def _parse_card(path: Path) -> dict[str, str]:
    """Parse a credential card, tolerating a flattened single-line file.

    A normal card (one ``KEY=VALUE`` per line) is returned exactly as dotenv reads
    it. A card whose newlines were flattened to spaces — the wsa dispenser Env File
    block, pasted verbatim into ``credentials.env`` — makes dotenv surface far fewer
    ``F1_`` keys than the file actually assigns; in that case re-extract every
    ``F1_KEY=value`` token instead, keeping any non-``F1_`` keys dotenv found.
    """
    values = dict(dotenv_values(path))
    try:
        text = path.read_text()
    except OSError:
        return values
    f1_keys = sum(1 for k in values if k.startswith("F1_"))
    if len(_F1_ASSIGNMENT.findall(text)) <= f1_keys:
        return values  # not flattened — leave the normal parse untouched
    recovered = {f"F1_{name}": value for name, value in _F1_TOKEN.findall(text)}
    non_f1 = {k: v for k, v in values.items() if not k.startswith("F1_")}
    return {**non_f1, **recovered}


def load_card(explicit: str | None = None, root: Path | None = None) -> tuple[Path, dict[str, str]]:
    """Resolve a credential card and parse it. Exits if the path is bad.

    Parsing goes through ``_parse_card`` so every attendee tool (f1-pitwall, f1-sql,
    setup-rtce, setup-mcp, f1-race) handles a card that WSA's dispenser flattened
    onto one line — see ``_parse_card``.
    """
    path = resolve_card(explicit, root=root)
    if not path.exists():
        sys.exit(f"Credential file not found: {path}")
    return path, _parse_card(path)


def set_active_card(root: Path, card: Path) -> None:
    """
    Record `card` as the active one in credentials.env, so the attendee tools
    pick it up with no flags and no exported shell variable.

    Rewrites in place — the surrounding TF_VAR_* lines and comments are left
    exactly as they were.
    """
    creds_file = root / "credentials.env"
    try:
        rel = card.resolve().relative_to(root.resolve())
        value = str(rel)
    except ValueError:
        value = str(card)

    line = f"{CARD_POINTER_KEY}={value}\n"
    existing = creds_file.read_text().splitlines(keepends=True) if creds_file.exists() else []

    for i, current in enumerate(existing):
        if current.lstrip().startswith(f"{CARD_POINTER_KEY}="):
            existing[i] = line
            break
    else:
        if existing and not existing[-1].endswith("\n"):
            existing[-1] += "\n"
        existing.append(line)

    creds_file.write_text("".join(existing))


def clear_active_card(root: Path, only_if_under: Path | None = None) -> None:
    """
    Drop the F1_CARD pointer — the deployment it named is gone.

    ``only_if_under`` scopes the clear to one run directory, so tearing down the
    self-service environment doesn't unset a pointer aimed at the standalone
    deployment (or vice versa).
    """
    creds_file = root / "credentials.env"
    if not creds_file.exists():
        return

    if only_if_under is not None:
        pointer = dotenv_values(creds_file).get(CARD_POINTER_KEY)
        if not pointer:
            return
        card = Path(pointer)
        if not card.is_absolute():
            card = root / card
        if not card.is_relative_to(only_if_under):
            return

    kept = [
        line
        for line in creds_file.read_text().splitlines(keepends=True)
        if not line.lstrip().startswith(f"{CARD_POINTER_KEY}=")
    ]
    creds_file.write_text("".join(kept))


def generate_confluent_api_keys(prefix: str = "f1-demo") -> tuple[str | None, str | None]:
    """
    Generate Confluent API keys using CLI.

    Creates a service account and generates API keys with OrganizationAdmin role.

    Args:
        prefix: Prefix for service account name

    Returns:
        Tuple of (api_key, api_secret) or (None, None) if generation fails
    """
    try:
        timestamp = str(int(time.time()))[-6:]
        sa_name = f"{prefix}-setup-sa-{timestamp}"

        print(f"Creating service account: {sa_name}...")
        sa_result = subprocess.run(
            [
                "confluent",
                "iam",
                "service-account",
                "create",
                sa_name,
                "--description",
                f"Service account for {prefix} setup",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        sa_id = None
        for line in sa_result.stdout.split("\n"):
            if "| ID" in line and "sa-" in line:
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 2 and "ID" in parts[0]:
                    sa_id = parts[1]
                    break

        if not sa_id:
            print("Error: Failed to extract service account ID.")
            return None, None

        print("Creating API key with Cloud Resource Management scope...")
        key_result = subprocess.run(
            [
                "confluent",
                "api-key",
                "create",
                "--service-account",
                sa_id,
                "--resource",
                "cloud",
                "--description",
                f"{prefix} setup key",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        api_key = api_secret = None
        for line in key_result.stdout.split("\n"):
            if "API Key" in line and "|" in line:
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 2 and "API Key" in parts[0]:
                    api_key = parts[1]
            elif "API Secret" in line and "|" in line:
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 2 and "API Secret" in parts[0]:
                    api_secret = parts[1]

        if api_key and api_secret:
            print("Assigning OrganizationAdmin role...")
            try:
                subprocess.run(
                    [
                        "confluent",
                        "iam",
                        "rbac",
                        "role-binding",
                        "create",
                        "--principal",
                        f"User:{sa_id}",
                        "--role",
                        "OrganizationAdmin",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                print("API keys generated successfully!")
                return api_key, api_secret
            except subprocess.CalledProcessError:
                print("Warning: Role assignment failed, but API keys were created.")
                return api_key, api_secret

    except subprocess.CalledProcessError as e:
        print(f"Error generating API keys: {e}")

    return None, None
