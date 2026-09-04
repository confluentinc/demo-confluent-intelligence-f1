"""
Register Confluent's Real-Time Context Engine (RTCE) as an MCP server with a
local coding agent, using this deployment's credential card.

    uv run setup-rtce                   # resolved card, prompts for client(s)
    uv run setup-rtce --creds <card>.env
    uv run setup-rtce --client codex     # print the Codex config snippet
    uv run setup-rtce --lightning        # print a ready-to-run curl command
    uv run setup-rtce --dry-run          # print the argv, touch no agent config

RTCE exposes `car_telemetry` as an MCP tool, so a coding agent can query the
live race feed directly — no Kafka client, no consumer group. The card fields
this reads (F1_RTCE_MCP_ENDPOINT / F1_RTCE_API_KEY / F1_RTCE_API_SECRET)
come from Terraform outputs during provisioning. Both modes also accept
RTCE_API_KEY / RTCE_API_SECRET overrides. Missing keys trigger an offer to
create one through the Confluent CLI, followed by manual entry if needed.

Claude Code: registered live via `claude mcp add --transport http`, the same
command `scripts/workshop/creds.py::_rtce_command` already puts on the printed
card — this script just runs it instead of asking you to paste it. Safe to
rerun (remove-then-add, one entry: "real-time-context-engine").

Codex CLI: no confirmed flag sets an arbitrary Basic-auth header (its `mcp add
--url` only takes `--bearer-token-env-var`, and RTCE requires Basic, not
Bearer), so there's no `codex` CLI invocation that can express this — unlike
Claude Code above, or `setup-mcp`'s Codex path, which registers a local stdio
server and never needs a header at all. This script instead edits
`~/.codex/config.toml` directly with `tomlkit`, which preserves the rest of
the file (comments, other tables, formatting) on round-trip. If the existing
file can't be parsed, it falls back to printing the snippet instead of
guessing.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import tomlkit
from dotenv import set_key

from scripts.common.credentials import load_card
from scripts.common.terraform import get_project_root

_SERVER_NAME = "real-time-context-engine"

_EXPECTED_CARD_KEYS = ("F1_RTCE_MCP_ENDPOINT", "F1_RTCE_API_KEY", "F1_RTCE_API_SECRET")


def _run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(argv, **kwargs)


def basic_token(key: str, secret: str) -> str:
    return base64.b64encode(f"{key}:{secret}".encode()).decode()


def lightning_command(card: dict[str, str]) -> str:
    """Build a shell-safe request from existing RTCE and deployment fields."""
    endpoint = card.get("F1_RTCE_MCP_ENDPOINT", "")
    match = re.fullmatch(
        r"https://mcp\.([a-z0-9-]+)\.([a-z0-9-]+)\.confluent\.cloud/"
        r"mcp/v1/context-engine/organizations/[^/]+/environments/[^/]+/kafka-clusters/[^/]+/?",
        endpoint,
    )
    if not match:
        raise ValueError("Missing or invalid F1_RTCE_MCP_ENDPOINT in the credential file.")
    required = ("F1_ENVIRONMENT_ID", "F1_CLUSTER_ID", "F1_RTCE_API_KEY", "F1_RTCE_API_SECRET")
    missing = [name for name in required if not card.get(name)]
    if missing:
        raise ValueError(
            "Missing credentials: " + ", ".join(missing)
            + ". Hosted attendees: rerun f1-onboard --paste with the claim email's MCP Setup Command, "
            "or use the instructor-provided .env file. Standalone users: supply an existing Global "
            "key through RTCE_API_KEY and RTCE_API_SECRET."
        )
    region, cloud = match.groups()
    url = f"https://sql.{region}.{cloud}.confluent.cloud/query/v1alpha1"
    payload = json.dumps({
        "catalog_name": card["F1_ENVIRONMENT_ID"],
        "database_name": card["F1_CLUSTER_ID"],
        "query": "SELECT car_number, lap, tire_temp_fl_c FROM car_telemetry ORDER BY lap DESC LIMIT 10",
    }, indent=2)
    token = basic_token(card["F1_RTCE_API_KEY"], card["F1_RTCE_API_SECRET"])
    return " \\\n  ".join((
        "curl -sS -X POST " + shlex.quote(url),
        "-H " + shlex.quote("Authorization: Basic " + token),
        "-H " + shlex.quote("Content-Type: application/json"),
        "-d " + shlex.quote(payload),
    ))


def claude_argv(endpoint: str, token: str) -> tuple[list[str], list[str]]:
    """The (remove, add) argv pair for Claude Code."""
    return (
        ["claude", "mcp", "remove", _SERVER_NAME, "-s", "local"],
        [
            "claude", "mcp", "add", "--transport", "http", _SERVER_NAME, endpoint,
            "--header", f"Authorization: Basic {token}",
        ],
    )


def codex_config_snippet(endpoint: str, token: str) -> str:
    """A ready-to-paste `[mcp_servers.rtce]` block for ~/.codex/config.toml.

    Not written automatically: no TOML-writing dependency is in this project,
    and hand-rolling one risks reformatting or corrupting the rest of the
    file. `env_http_headers` would need the token in an env var anyway, so a
    literal `http_headers` block (this repo's Confluent Basic token, not a
    long-lived secret) is the simplest correct snippet.
    """
    return (
        f'[mcp_servers.{_SERVER_NAME}]\n'
        f'url = "{endpoint}"\n'
        f'http_headers = {{ "Authorization" = "Basic {token}" }}\n'
    )


def register_claude(endpoint: str, token: str, dry_run: bool = False) -> bool:
    remove_argv, add_argv = claude_argv(endpoint, token)

    if dry_run:
        print("[dry-run] Claude Code — would run:")
        print(f"    {shlex.join(remove_argv)}")
        print(f"    {shlex.join(add_argv)}")
        return True

    try:
        _run(remove_argv, capture_output=True)
        result = _run(add_argv, capture_output=True, text=True)
    except FileNotFoundError:
        print("Error: 'claude' not found — Claude Code is not installed.")
        print("  Install it: https://claude.com/product/claude-code")
        return False

    if result.returncode != 0:
        print("Error: Claude Code refused the registration:")
        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            print("  " + detail.replace("\n", "\n  "))
        return False

    print(f"Registered '{_SERVER_NAME}' with Claude Code — local scope (this project)")
    return True


def print_codex_instructions(endpoint: str, token: str) -> None:
    print("Codex CLI has no confirmed flag for a Basic-auth header, so add this")
    print("block to ~/.codex/config.toml by hand:\n")
    print(codex_config_snippet(endpoint, token))


def _codex_config_path() -> Path:
    return Path.home() / ".codex" / "config.toml"


def write_codex_config(endpoint: str, token: str, config_path: Path, dry_run: bool = False) -> bool:
    """Merge an `[mcp_servers.rtce]` table into `config_path`, in place.

    Preserves everything else already in the file. Safe to rerun (replaces any
    existing `rtce` table). Falls back to printing the manual snippet if the
    existing file can't be parsed as TOML, rather than risk corrupting it.
    """
    if dry_run:
        print(f"[dry-run] Codex CLI — would merge into {config_path}:\n")
        print(codex_config_snippet(endpoint, token))
        return True

    try:
        existing_text = config_path.read_text() if config_path.exists() else ""
        doc = tomlkit.parse(existing_text) if existing_text else tomlkit.document()

        mcp_servers = doc.get("mcp_servers")
        if mcp_servers is None:
            mcp_servers = tomlkit.table()
            doc["mcp_servers"] = mcp_servers

        rtce = tomlkit.table()
        rtce["url"] = endpoint
        http_headers = tomlkit.inline_table()
        http_headers["Authorization"] = f"Basic {token}"
        rtce["http_headers"] = http_headers
        mcp_servers[_SERVER_NAME] = rtce

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(tomlkit.dumps(doc))
    except Exception as exc:
        print(f"Error: couldn't auto-edit {config_path} ({exc}) — add this by hand:\n")
        print(codex_config_snippet(endpoint, token))
        return True

    print(f"Registered '{_SERVER_NAME}' in {config_path}")
    return True


def _prompt_for_clients() -> list[str]:
    print("Which coding agent(s) should RTCE be registered with?")
    print("  1) Claude Code")
    print("  2) Codex CLI")
    print("  3) Both")
    try:
        choice = input("Choice [1]: ").strip()
    except (EOFError, KeyboardInterrupt, OSError):
        print("\nNo input available — defaulting to Claude Code.")
        return ["claude"]

    if choice in ("", "1", "claude"):
        return ["claude"]
    if choice in ("2", "codex"):
        return ["codex"]
    if choice in ("3", "both"):
        return ["claude", "codex"]
    print(f"Unrecognized choice '{choice}' — defaulting to Claude Code.")
    return ["claude"]


def warn_on_empty_card_fields(card: dict[str, str]) -> list[str]:
    missing = [k for k in _EXPECTED_CARD_KEYS if not card.get(k)]
    if missing:
        print("Error: the credential card has no RTCE keys — missing: " + ", ".join(missing))
        print("  Regenerate it with RTCE keys enabled:")
        print("    uv run selfservice up   |   uv run workshop creds --rtce-keys")
    return missing


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="uv run setup-rtce",
        description=(
            "Register Confluent's Real-Time Context Engine as an MCP server with a "
            "local coding agent, using this deployment's credential card."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--creds", metavar="PATH", help="credential card to read (default: resolved automatically)")
    parser.add_argument(
        "--client",
        choices=("claude", "codex", "both"),
        default=None,
        help="which coding agent to register with (default: prompt interactively)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the commands/snippet, but change no agent config",
    )
    parser.add_argument(
        "--lightning", action="store_true",
        help="print a ready-to-run Lightning Query curl command; do not configure an MCP client",
    )
    args = parser.parse_args(argv)
    if args.lightning and (args.client or args.dry_run):
        parser.error("--lightning cannot be combined with --client or --dry-run")
    return args


_KEY_HELP = "https://docs.confluent.io/cloud/current/ai/real-time-context-engine/get-started.html#create-an-api-key"


def terraform_rtce_outputs(card: dict[str, str]) -> dict[str, str]:
    """Read only a local state whose environment and cluster match the selected card."""
    for track in ("aws", "self-service"):
        state = get_project_root() / "terraform" / track / "terraform.tfstate"
        if not state.exists():
            continue
        try:
            result = subprocess.run(
                ["terraform", "output", "-json", f"-state={state}"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode:
                continue
            outputs = {k: v["value"] for k, v in json.loads(result.stdout).items()}
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
            continue
        if (outputs.get("environment_id") == card.get("F1_ENVIRONMENT_ID")
                and outputs.get("cluster_id") == card.get("F1_CLUSTER_ID")
                and outputs.get("environment_id") and outputs.get("cluster_id")):
            return outputs
    return {}


def offer_cli_key(service_account_id: str = "") -> tuple[str, str]:
    owner = f"service account {service_account_id}" if service_account_id else "the currently logged-in CLI user"
    print(f"Create a Global API key for {owner}? [y/N] ", end="", file=sys.stderr)
    if input().strip().lower() not in ("y", "yes"):
        return "", ""
    argv = ["confluent", "api-key", "create", "--resource", "global",
            "--description", "RTCE and Lightning Queries", "-o", "json"]
    if service_account_id:
        argv += ["--service-account", service_account_id]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            value = json.loads(result.stdout)
            return value.get("api_key", value.get("key", "")), value.get("api_secret", value.get("secret", ""))
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
        pass
    print("CLI creation failed. Check your Confluent CLI login, permissions, and key quota.", file=sys.stderr)
    return "", ""


def resolve_rtce_credentials(
    card: dict[str, str], card_path: Path | None = None, *, allow_create: bool = True,
) -> dict[str, str]:
    """Use overrides, matching Terraform outputs, saved credentials, then CLI/manual fallback."""
    card = dict(card)
    key, secret = os.environ.get("RTCE_API_KEY"), os.environ.get("RTCE_API_SECRET")
    if key or secret:
        if not (key and secret):
            sys.exit("Set both RTCE_API_KEY and RTCE_API_SECRET to a Global API key pair.")
        card.update(F1_RTCE_API_KEY=key, F1_RTCE_API_SECRET=secret)
    else:
        outputs = terraform_rtce_outputs(card)
        if outputs.get("rtce_api_key") and outputs.get("rtce_api_secret"):
            card.update(F1_RTCE_API_KEY=outputs["rtce_api_key"], F1_RTCE_API_SECRET=outputs["rtce_api_secret"])
    if card.get("F1_RTCE_API_KEY") and card.get("F1_RTCE_API_SECRET"):
        return card
    print(
        "No saved Global API key pair. Provisioning normally supplies it.\n"
        "Hosted attendees: import the claim email with f1-onboard --paste or ask your instructor.\n"
        "Manual creation: Console > Administration > API keys > Add API key; select Global scope.\n"
        "Use an account authorized to read this deployment's topic and Schema Registry.\n"
        + _KEY_HELP,
        file=sys.stderr,
    )
    if not sys.stdin.isatty():
        sys.exit("No interactive terminal. Set RTCE_API_KEY and RTCE_API_SECRET, or supply a credential file.")
    try:
        outputs = terraform_rtce_outputs(card)
        key, secret = offer_cli_key(outputs.get("service_account_id", "")) if allow_create else ("", "")
        if not (key and secret):
            key = getpass.getpass("Global API key: ").strip()
            secret = getpass.getpass("Global API secret: ").strip()
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("Global API key entry cancelled.") from None
    if not (key and secret):
        sys.exit("Both the Global API key and secret are required.")
    card.update(F1_RTCE_API_KEY=key, F1_RTCE_API_SECRET=secret)
    if card_path is not None:
        set_key(str(card_path), "F1_RTCE_API_KEY", key)
        set_key(str(card_path), "F1_RTCE_API_SECRET", secret)
        print("Saved the key pair in your existing credential file for later runs.", file=sys.stderr)
    return card


def main() -> None:
    args = _parse_args()
    if args.lightning:
        card_path, card = load_card(args.creds, root=get_project_root())
        card = resolve_rtce_credentials(card, card_path)
        try:
            print(lightning_command(card))
        except ValueError as exc:
            sys.exit(str(exc))
        return
    if args.client is None:
        clients = _prompt_for_clients()
    else:
        clients = ["claude", "codex"] if args.client == "both" else [args.client]

    project_root = get_project_root()
    card_path, card = load_card(args.creds, root=project_root)
    print(f"Credential card: {card_path}")

    card = resolve_rtce_credentials(card, None if args.dry_run else card_path, allow_create=not args.dry_run)
    if warn_on_empty_card_fields(card):
        sys.exit(1)

    endpoint = card["F1_RTCE_MCP_ENDPOINT"]
    token = basic_token(card["F1_RTCE_API_KEY"], card["F1_RTCE_API_SECRET"])

    results = []
    for client in clients:
        if client == "claude":
            results.append(register_claude(endpoint, token, dry_run=args.dry_run))
        else:
            results.append(write_codex_config(endpoint, token, _codex_config_path(), dry_run=args.dry_run))

    if not all(results):
        sys.exit(1)

    if "claude" in clients and not args.dry_run:
        print("Restart Claude Code to pick the server up.")


if __name__ == "__main__":
    main()
