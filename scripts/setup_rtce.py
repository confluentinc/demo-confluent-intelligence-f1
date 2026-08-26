"""
Register Confluent's Real-Time Context Engine (RTCE) as an MCP server with a
local coding agent, using this deployment's credential card.

    uv run setup-rtce                   # resolved card, prompts for client(s)
    uv run setup-rtce --creds <card>.env
    uv run setup-rtce --client codex     # print the Codex config snippet
    uv run setup-rtce --dry-run          # print the argv, touch no agent config

RTCE exposes `car_telemetry` as an MCP tool, so a coding agent can query the
live race feed directly — no Kafka client, no consumer group. The card fields
this reads (F1_RTCE_MCP_ENDPOINT / F1_RTCE_API_KEY / F1_RTCE_API_SECRET) only
exist when the card was minted with RTCE keys (`selfservice up`, or
`workshop creds --rtce-keys`) — regenerate the card if they're empty.

Claude Code: registered live via `claude mcp add --transport http`, the same
command `scripts/workshop/creds.py::_rtce_command` already puts on the printed
card — this script just runs it instead of asking you to paste it. Safe to
rerun (remove-then-add, one entry: "rtce").

Codex CLI: no confirmed flag sets an arbitrary Basic-auth header (its `mcp add
--url` only takes `--bearer-token-env-var`, and RTCE requires Basic, not
Bearer), so this script prints a `~/.codex/config.toml` snippet to paste by
hand rather than risk corrupting your existing config with an unverified edit.
"""

from __future__ import annotations

import argparse
import base64
import shlex
import subprocess
import sys

from scripts.common.credentials import load_card
from scripts.common.terraform import get_project_root

_SERVER_NAME = "rtce"

_EXPECTED_CARD_KEYS = ("F1_RTCE_MCP_ENDPOINT", "F1_RTCE_API_KEY", "F1_RTCE_API_SECRET")


def _run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(argv, **kwargs)


def basic_token(key: str, secret: str) -> str:
    return base64.b64encode(f"{key}:{secret}".encode()).decode()


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


def _prompt_for_clients() -> list[str]:
    print("Which coding agent(s) should RTCE be registered with?")
    print("  1) Claude Code")
    print("  2) Codex CLI (prints a config snippet — no automatic edit)")
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
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args()
    if args.client is None:
        clients = _prompt_for_clients()
    else:
        clients = ["claude", "codex"] if args.client == "both" else [args.client]

    project_root = get_project_root()
    card_path, card = load_card(args.creds, root=project_root)
    print(f"Credential card: {card_path}")

    if warn_on_empty_card_fields(card):
        sys.exit(1)

    endpoint = card["F1_RTCE_MCP_ENDPOINT"]
    token = basic_token(card["F1_RTCE_API_KEY"], card["F1_RTCE_API_SECRET"])

    results = []
    for client in clients:
        if client == "claude":
            results.append(register_claude(endpoint, token, dry_run=args.dry_run))
        else:
            print_codex_instructions(endpoint, token)
            results.append(True)

    if not all(results):
        sys.exit(1)

    if "claude" in clients and not args.dry_run:
        print("Restart Claude Code to pick the server up.")


if __name__ == "__main__":
    main()
