"""
Credential loading and management utilities.

Two distinct files share the ".env" name and must not be confused:

- ``credentials.env`` at the project root — deploy secrets (``TF_VAR_*``), the
  single source of truth for the deploy flows. It also carries an ``F1_CARD``
  pointer at whichever credential card was last provisioned.
- a credential **card**, ``runs/<name>/credentials/<prefix>.env`` (``F1_*``
  keys) — what ``f1-sql`` / ``f1-pitwall`` / ``f1-race`` authenticate with.

``f1-onboard`` blurs the two: it writes an ``F1_*`` card to ``./credentials.env``
by default, so the resolver below accepts either shape.

Provides functions for:
- Loading credentials from credentials.env files
- Resolving which credential card the attendee tools should use
- Generating Confluent Cloud API keys via CLI
"""

import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import dotenv_values

# Set by deploy.py / `selfservice up` in credentials.env, read by resolve_card().
CARD_POINTER_KEY = "F1_CARD"

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
    return any(k.startswith("F1_") and k != CARD_POINTER_KEY for k in values)


def resolve_card(explicit: str | None = None, root: Path | None = None) -> Path:
    """
    Work out which credential card to use, so attendees never have to name it.

    Order, first hit wins:
      1. an explicit --creds value
      2. $F1_CREDS
      3. credentials.env — either its F1_CARD pointer, or the file itself when
         it holds F1_* keys (what f1-onboard writes)
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
            "  Run `uv run deploy` (or `uv run f1-onboard` if you were given one),\n"
            "  or pass `--creds <path>` explicitly."
        )

    listed = "\n".join(f"    {c.relative_to(root)}" for c in candidates)
    sys.exit(
        "Multiple credential cards found — I won't guess which environment you meant:\n"
        f"{listed}\n"
        "  Pass `--creds <path>`, or re-run `uv run deploy` to set F1_CARD in credentials.env."
    )


def load_card(explicit: str | None = None, root: Path | None = None) -> tuple[Path, dict[str, str]]:
    """Resolve a credential card and parse it. Exits if the path is bad."""
    path = resolve_card(explicit, root=root)
    if not path.exists():
        sys.exit(f"Credential file not found: {path}")
    return path, dict(dotenv_values(path))


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
