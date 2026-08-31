"""
Register the Confluent MCP server with a local coding agent, using this
deployment's credential card.

    uv run setup-mcp                        # resolved card, prompts for client(s)
    uv run setup-mcp --creds <card>.env     # explicit card
    uv run setup-mcp --client codex         # Codex CLI instead, no prompt
    uv run setup-mcp --client both
    uv run setup-mcp --dry-run              # print the argv, touch no agent config

Why this exists alongside ``f1-social-feed-rtce``: that shim re-exposes one live
race feed as an OpenAPI tool for watsonx Orchestrate (LAB 5). This script does
something else — it wires the whole Confluent control plane (Kafka topics, Schema
Registry subjects, Flink statements) into the coding agent you already have open,
so you can ask it to inspect and change the environment directly. Neither
replaces the other.

Two things differ from the original version of this script:

1. **Credentials come from the credential card**, resolved by
   ``scripts.common.credentials`` — not from ``terraform/core/terraform.tfstate``,
   which no longer exists. The card is the source of truth for every
   attendee-facing tool and is resolved automatically, so ``--creds`` is an
   override, not a requirement.
2. **Codex CLI is supported** alongside Claude Code.

Safe to rerun. It touches exactly one server entry per agent
(``confluent-cloud-mcp-server``), removing it before re-adding, and rewrites
``confluent-mcp.env`` whole rather than appending — so a second run leaves the
same bytes and the same single registration.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

from scripts.common.credentials import load_card
from scripts.common.terraform import get_project_root

# ---------------------------------------------------------------------------
# Node / ABI preflight
#
# This is not defensive noise. @confluentinc/kafka-javascript ships prebuilt
# native binaries for a fixed set of Node ABIs; on any other ABI npm falls back
# to compiling from source, which needs a toolchain most workshop laptops do not
# have. Getting this wrong produces a confusing native-module load failure hours
# later, inside the agent, with no mention of Node.
#
# Refresh the table when the pinned package version changes:
#   gh release view v<version> --repo confluentinc/confluent-kafka-javascript \
#     --json assets --jq '[.assets[].name | capture("node-v(?<abi>[0-9]+)-").abi]
#                          | unique | sort_by(tonumber)'
#
# Verified against confluent-kafka-javascript v1.10.0 (latest, 2026-07-01), which
# ships assets for ABIs 108, 115, 120, 127, 131, 137 — i.e. ABI -> Node
# 108=18, 115=20, 120=21, 127=22, 131=23, 137=24. Node 25 (141) and Node 26 (147)
# have no prebuilt asset, which is why a very new Node warns rather than passes.
# 108 is deliberately left out of the set below: an asset exists, but Node 18 is
# EOL and _MIN_NODE_MAJOR rejects it before the set is ever consulted.
# ---------------------------------------------------------------------------

_KAFKA_JS_PREBUILT_ABIS = {115, 120, 127, 131, 137}
_PREFERRED_ABI = 137  # Node 24 LTS
_MIN_NODE_MAJOR = 20  # Node 18 (ABI 108) is EOL; don't encourage it

# Where version-managed Node installations live (nvm, fnm, Homebrew).
_NVM_DIR = Path.home() / ".nvm" / "versions" / "node"
_FNM_DIR = Path.home() / ".local" / "share" / "fnm" / "node-versions"
_HOMEBREW_DIRS = [
    Path("/opt/homebrew/opt"),  # Apple Silicon
    Path("/usr/local/opt"),  # Intel
]

_MCP_PACKAGE = "@confluentinc/mcp-confluent"
# The native dependency that constrains which Node versions work.
_KAFKA_JS_PACKAGE = "@confluentinc/kafka-javascript"
_SERVER_NAME = "confluent-cloud-mcp-server"


def _run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Single choke point for every subprocess this script spawns.

    Node probing, ``npm install``, and both agent CLIs all go through here, so a
    test can record the exact argv and stub the results without reaching into the
    global ``subprocess`` module (which other code in the same process shares).
    """
    return subprocess.run(argv, **kwargs)


def _probe_node(node_bin: str) -> tuple[str, int] | None:
    """Return ``(version, abi)`` for a Node binary, or None if it won't run.

    Both facts come from the same binary in two cheap calls; keeping the
    subprocess work here leaves the *decision* about a version pure and directly
    testable (see ``_classify_node``).
    """
    try:
        ver = _run([node_bin, "--version"], capture_output=True, text=True, check=True, timeout=10)
        abi = _run(
            [node_bin, "-e", "process.stdout.write(process.versions.modules)"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return ver.stdout.strip().lstrip("v"), int(abi.stdout.strip())
    except Exception:
        return None


def _abi_score(abi: int) -> int:
    """Rank a Node ABI for our purposes: 2 = ideal, 1 = prebuilt, 0 = builds from source."""
    if abi == _PREFERRED_ABI:
        return 2
    return 1 if abi in _KAFKA_JS_PREBUILT_ABIS else 0


def _find_preferred_node() -> str:
    """
    Return a Node binary that has a prebuilt kafka-javascript binary, preferring
    ABI 137 (Node 24 LTS), and falling back to whatever ``node`` is on PATH.

    Searching nvm/fnm/Homebrew matters because the Node on PATH is often the
    newest one, which is exactly the one without a prebuilt asset. A developer
    who already has Node 24 installed should not have to switch shells.
    """
    candidates: list[str] = []

    if _NVM_DIR.is_dir():  # versions sorted newest-first
        candidates += [str(d / "bin" / "node") for d in sorted(_NVM_DIR.iterdir(), reverse=True)]

    if _FNM_DIR.is_dir():
        candidates += [str(d / "installation" / "bin" / "node") for d in sorted(_FNM_DIR.iterdir(), reverse=True)]

    # The explicit node@24 formula, not the rolling `node` keg.
    candidates += [str(p / "node@24" / "bin" / "node") for p in _HOMEBREW_DIRS]

    best_path, best_score = "node", -1
    for path in ["node", *candidates]:
        if path != "node" and not Path(path).exists():
            continue
        probed = _probe_node(path)
        if probed is None:
            continue
        score = _abi_score(probed[1])
        if score > best_score:
            best_path, best_score = path, score

    return best_path


def _classify_node(version: str, abi: int) -> tuple[bool, list[str]]:
    """
    Judge a Node version. Returns ``(fatal, lines_to_print)``.

    Pure on purpose: the interesting behaviour is the guidance text, and a test
    should be able to assert on it without mocking two subprocess calls in a
    fixed order.
    """
    major = int(version.split(".")[0])

    if major < _MIN_NODE_MAJOR:
        return True, [
            f"Error: Node {version} is too old (minimum: v{_MIN_NODE_MAJOR}, recommended: v24 LTS).",
            "  With nvm: nvm install 24 && nvm use 24",
            "  With fnm: fnm install 24 && fnm use 24",
            "  With Homebrew: brew install node@24",
        ]

    if abi not in _KAFKA_JS_PREBUILT_ABIS:
        return False, [
            f"Warning: Node {version} (ABI {abi}) has no prebuilt {_KAFKA_JS_PACKAGE} binary.",
            "  npm will try to compile it from source, which needs build tools:",
            "    macOS: xcode-select --install",
            "    Linux: sudo apt install build-essential python3",
            "  If the install fails, switch Node:  nvm install 24 && nvm use 24",
        ]

    suffix = " (Node 24 LTS — prebuilt binary available)" if abi == _PREFERRED_ABI else " (prebuilt binary available)"
    return False, [f"Using Node {version}{suffix}"]


def _check_node(node_bin: str) -> None:
    """Print the verdict on ``node_bin``; exit if it can't work at all."""
    probed = _probe_node(node_bin)
    if probed is None:
        print("Error: could not run 'node'. Install Node.js v24 LTS.")
        print("  With nvm: nvm install 24 && nvm use 24")
        print("  With Homebrew: brew install node@24")
        sys.exit(1)

    version, abi = probed
    fatal, lines = _classify_node(version, abi)
    for line in lines:
        print(line)
    if fatal:
        sys.exit(1)
    if node_bin != "node":
        print(f"  ({node_bin})")


# ---------------------------------------------------------------------------
# confluent-mcp.env
#
# mcp-confluent's `-e <file>` path reads a dotenv file. That path is marked
# deprecated upstream in favour of `-c <yaml>`, but is still supported and has
# full parity for a single connection — and it is what the proven registration
# command below passes, so we keep it. Variable names are mcp-confluent's own
# (CONFIGURATION.md, "Legacy env-var configuration").
# ---------------------------------------------------------------------------

# Credential-card key -> the MCP variable(s) it feeds. Ordered, and rendered in
# this order, so two runs against the same card produce byte-identical output.
_CARD_TO_MCP: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("F1_KAFKA_BOOTSTRAP", ("BOOTSTRAP_SERVERS",)),
    ("F1_KAFKA_API_KEY", ("KAFKA_API_KEY",)),
    ("F1_KAFKA_API_SECRET", ("KAFKA_API_SECRET",)),
    ("F1_CLUSTER_ID", ("KAFKA_CLUSTER_ID",)),
    ("F1_ENVIRONMENT_ID", ("KAFKA_ENV_ID", "FLINK_ENV_ID")),
    ("F1_FLINK_API_KEY", ("FLINK_API_KEY",)),
    ("F1_FLINK_API_SECRET", ("FLINK_API_SECRET",)),
    ("F1_FLINK_REST_ENDPOINT", ("FLINK_REST_ENDPOINT",)),
    ("F1_COMPUTE_POOL_ID", ("FLINK_COMPUTE_POOL_ID",)),
    ("F1_ORGANIZATION_ID", ("FLINK_ORG_ID",)),
    # The catalog/database a Flink statement resolves against are the Confluent
    # environment and cluster *display names* — which is what the card stores
    # them as.
    ("F1_CATALOG", ("FLINK_CATALOG_NAME",)),
    ("F1_DATABASE", ("FLINK_DATABASE_NAME",)),
    ("F1_SR_API_KEY", ("SCHEMA_REGISTRY_API_KEY",)),
    ("F1_SR_API_SECRET", ("SCHEMA_REGISTRY_API_SECRET",)),
    ("F1_SCHEMA_REGISTRY_URL", ("SCHEMA_REGISTRY_ENDPOINT",)),
)

# Card fields whose absence degrades the server to near-uselessness. Warned
# about, not fatal: a partially-filled card still gives working Flink or Kafka
# tools, and the user is better served by a named gap than by an abort.
_EXPECTED_CARD_KEYS = (
    "F1_KAFKA_BOOTSTRAP",
    "F1_ENVIRONMENT_ID",
    "F1_ORGANIZATION_ID",
    "F1_FLINK_REST_ENDPOINT",
    "F1_COMPUTE_POOL_ID",
)


def kafka_rest_endpoint(bootstrap: str) -> str:
    """
    Derive the cluster's Kafka REST endpoint from its bootstrap server.

    The card has no REST endpoint field and cannot get one: ``modules/cluster``
    exposes ``cluster_rest_endpoint``, but ``terraform/aws/outputs.tf`` never
    plumbs it to the root, so neither the wsa CSV nor a self-service card carries
    it. Deriving is safe rather than a guess — Confluent Cloud publishes both
    endpoints for an access point on the *same host*, differing only in scheme
    and port. The Terraform provider's attribute reference pairs them literally
    (docs/data-sources/confluent_kafka_cluster.md, ``endpoints`` block):

        bootstrap_endpoint  lkc-abc123-apfoo123.eu-west-3.aws...cloud:9092
        rest_endpoint       https://lkc-abc123-apfoo123.eu-west-3.aws...cloud:443

    and the plain public example is ``https://pkc-00000.us-central1.gcp.confluent.cloud:443``.

    The card's bootstrap may carry a ``SASL_SSL://`` scheme — same handling as
    ``scripts/pitwall/consumer.py::_bootstrap``.
    """
    if not bootstrap:
        return ""
    host = bootstrap.split("://", 1)[-1].split(":", 1)[0]
    return f"https://{host}:443" if host else ""


def kafka_bootstrap_servers(bootstrap: str) -> str:
    """Return the host:port form mcp-confluent accepts for BOOTSTRAP_SERVERS.

    Credential cards retain the SASL_SSL scheme because Kafka clients use it,
    whereas mcp-confluent validates this setting as a comma-separated host:port
    list and configures TLS itself. Keep the card unchanged; normalize only at
    the MCP boundary.
    """
    return bootstrap.split("://", 1)[-1]


def cloud_api_credentials(project_root: Path) -> tuple[str, str]:
    """
    Best-effort Confluent Cloud (control-plane) API key and secret.

    Deliberately not on the credential card: the card holds the per-environment
    Kafka/Schema-Registry/Flink keys an attendee needs, while the Cloud key is an
    org-scoped deploy secret that lives in ``credentials.env`` as a ``TF_VAR_``.
    ``scripts/reset.py`` reaches for the same pair; do the same here, preferring
    anything already exported.

    Missing is normal — an ``f1-onboard`` attendee has a card and no
    ``credentials.env`` — so this degrades to empty strings instead of failing.
    Only mcp-confluent's org-level tools (connectors, listing environments) need
    them; every Kafka/Flink/SR tool uses the card's keys.
    """
    key = os.environ.get("CONFLUENT_CLOUD_API_KEY", "")
    secret = os.environ.get("CONFLUENT_CLOUD_API_SECRET", "")

    creds_file = project_root / "credentials.env"
    if (not key or not secret) and creds_file.exists():
        values = dotenv_values(creds_file)
        key = key or values.get("TF_VAR_confluent_cloud_api_key") or ""
        secret = secret or values.get("TF_VAR_confluent_cloud_api_secret") or ""

    return key, secret


def build_mcp_env(card: dict[str, str], cloud: tuple[str, str] = ("", "")) -> list[tuple[str, str]]:
    """Map a credential card onto mcp-confluent's variables, in a fixed order."""
    pairs: list[tuple[str, str]] = []
    for card_key, mcp_vars in _CARD_TO_MCP:
        value = card.get(card_key, "") or ""
        if card_key == "F1_KAFKA_BOOTSTRAP":
            value = kafka_bootstrap_servers(value)
        pairs += [(var, value) for var in mcp_vars]

    pairs.append(("KAFKA_REST_ENDPOINT", kafka_rest_endpoint(card.get("F1_KAFKA_BOOTSTRAP", "") or "")))
    pairs.append(("CONFLUENT_CLOUD_API_KEY", cloud[0]))
    pairs.append(("CONFLUENT_CLOUD_API_SECRET", cloud[1]))
    return pairs


def write_mcp_env(pairs: list[tuple[str, str]], project_root: Path) -> Path:
    """Write ``confluent-mcp.env``, replacing any previous contents. Returns the path."""
    env_path = project_root / "confluent-mcp.env"
    env_path.write_text("".join(f'{var}="{value}"\n' for var, value in pairs))
    # It holds live Kafka, Schema Registry and Flink secrets in the project root.
    env_path.chmod(0o600)
    return env_path


def warn_on_empty_card_fields(card: dict[str, str]) -> list[str]:
    """Return the expected-but-empty card keys, printing remediation for each."""
    missing = [k for k in _EXPECTED_CARD_KEYS if not card.get(k)]
    if missing:
        print("Warning: the credential card is missing values for: " + ", ".join(missing))
        print("  Regenerate it with whichever command created this environment:")
        print("    uv run deploy | uv run selfservice up | uv run f1-onboard | uv run workshop creds")
    return missing


# ---------------------------------------------------------------------------
# The MCP package itself
# ---------------------------------------------------------------------------


def _ensure_local_mcp(project_root: Path, node_bin: str, dry_run: bool = False) -> Path:
    """
    Return the path to mcp-confluent's ``dist/index.js``, installing the package
    into this project's ``node_modules/`` if it isn't there yet.

    A local install rather than ``npx``: npx can reuse a cache entry whose native
    bindings were compiled against a different Node ABI, which fails at load time
    with an error that says nothing about Node. Installing here compiles against
    the Node we actually selected.
    """
    dist_js = project_root / "node_modules" / _MCP_PACKAGE / "dist" / "index.js"
    if dist_js.exists():
        return dist_js

    if dry_run:
        print(f"[dry-run] would run: npm install {_MCP_PACKAGE}  (in {project_root})")
        return dist_js

    print(f"Installing {_MCP_PACKAGE} locally (compiles native bindings for your Node version)...")

    # Make npm use the same Node we picked, by putting its bin dir first on PATH.
    env = os.environ.copy()
    if node_bin != "node":
        env["PATH"] = str(Path(node_bin).parent) + os.pathsep + env.get("PATH", "")

    result = _run(["npm", "install", _MCP_PACKAGE], cwd=project_root, env=env)
    if result.returncode != 0:
        print("Error: npm install failed. Check that npm and build tools are available.")
        sys.exit(1)
    if not dist_js.exists():
        print(f"Error: {dist_js} not found after npm install.")
        sys.exit(1)

    print(f"Installed {_MCP_PACKAGE}")
    return dist_js


# ---------------------------------------------------------------------------
# Agent registration
#
# Claude Code: `claude mcp remove <name> -s local` + `claude mcp add --scope
# local <name> -- <cmd>`. The `-s` / `--scope` asymmetry is deliberate — that is
# the invocation the original script proved, so it is reproduced verbatim.
#
# Codex CLI: `codex mcp remove <name>` + `codex mcp add <name> -- <cmd>...`,
# writing a `[mcp_servers.<name>]` stdio entry (command/args/env) to
# ~/.codex/config.toml. Confirmed against the Codex source, not inferred:
#   codex-rs/cli/src/mcp_cmd.rs      — McpSubcommand {List,Get,Add,Remove,...};
#                                      AddArgs override_usage is
#                                      "codex mcp add [OPTIONS] <NAME> (--url <URL> | -- <COMMAND>...)"
#   codex-rs/config/src/mcp_types.rs — McpServerTransportConfig::Stdio
#                                      { command, args, env, env_vars, cwd }
# Codex has no scope flag: `add` writes the user-global config, whereas the
# Claude registration is project-local. That difference is surfaced to the user.
# ---------------------------------------------------------------------------


def claude_argv(node_bin: str, dist_js: Path, env_path: Path) -> tuple[list[str], list[str]]:
    """The (remove, add) argv pair for Claude Code."""
    launch = [node_bin, str(dist_js), "-e", str(env_path)]
    return (
        ["claude", "mcp", "remove", _SERVER_NAME, "-s", "local"],
        ["claude", "mcp", "add", "--scope", "local", _SERVER_NAME, "--", *launch],
    )


def codex_argv(node_bin: str, dist_js: Path, env_path: Path) -> tuple[list[str], list[str]]:
    """The (remove, add) argv pair for Codex CLI."""
    launch = [node_bin, str(dist_js), "-e", str(env_path)]
    return (
        ["codex", "mcp", "remove", _SERVER_NAME],
        ["codex", "mcp", "add", _SERVER_NAME, "--", *launch],
    )


# client -> (label, where the entry lands, argv builder, install hint)
CLIENTS = {
    "claude": ("Claude Code", "local scope (this project)", claude_argv, "https://claude.com/product/claude-code"),
    "codex": ("Codex CLI", "user scope (~/.codex/config.toml)", codex_argv, "npm install -g @openai/codex"),
}


def _prompt_for_clients() -> list[str]:
    """
    Ask which coding agent(s) to register with, when ``--client`` was omitted.

    Falls back to ``["claude"]`` (today's old default) on empty input or on a
    non-interactive stdin (EOF/Ctrl-C), so a script piping into this command
    doesn't hang waiting on a prompt it can never answer.
    """
    print("Which coding agent(s) should the Confluent MCP server be registered with?")
    for i, (_name, (label, _scope, _argv, _hint)) in enumerate(CLIENTS.items(), start=1):
        print(f"  {i}) {label}")
    print(f"  {len(CLIENTS) + 1}) Both")

    try:
        choice = input("Choice [1]: ").strip()
    except (EOFError, KeyboardInterrupt, OSError):
        print("\nNo input available — defaulting to Claude Code.")
        return ["claude"]

    names = list(CLIENTS.keys())
    if choice in ("", "1", "claude"):
        return ["claude"]
    if choice in ("2", "codex"):
        return ["codex"]
    if choice in (str(len(CLIENTS) + 1), "both"):
        return names
    print(f"Unrecognized choice '{choice}' — defaulting to Claude Code.")
    return ["claude"]


def register(client: str, node_bin: str, dist_js: Path, env_path: Path, dry_run: bool = False) -> bool:
    """
    Point one coding agent at the MCP server. Returns True on success.

    Rerun-safe by construction: remove-then-add replaces this script's own entry
    and nothing else. Both CLIs exit nonzero when removing a name that isn't
    configured, which is the normal first-run case, so only ``add`` gates the
    result.
    """
    label, scope, argv_builder, install_hint = CLIENTS[client]
    remove_argv, add_argv = argv_builder(node_bin, dist_js, env_path)

    if dry_run:
        print(f"[dry-run] {label} — would run:")
        print(f"    {shlex.join(remove_argv)}")
        print(f"    {shlex.join(add_argv)}")
        return True

    try:
        _run(remove_argv, capture_output=True)
        result = _run(add_argv, capture_output=True, text=True)
    except FileNotFoundError:
        print(f"Error: '{remove_argv[0]}' not found — {label} is not installed.")
        print(f"  Install it ({install_hint}), or pick another client with --client.")
        return False

    if result.returncode != 0:
        print(f"Error: {label} refused the registration:")
        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            print("  " + detail.replace("\n", "\n  "))
        return False

    print(f"Registered '{_SERVER_NAME}' with {label} — {scope}")
    return True


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="uv run setup-mcp",
        description=(
            "Register the Confluent MCP server with a local coding agent, using this "
            "deployment's credential card."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The credential card is resolved automatically (--creds, $F1_CREDS, the F1_CARD\n"
            "pointer in credentials.env, or the only card under runs/*/credentials/).\n"
            "Rerunning is safe: only this script's own 'confluent-cloud-mcp-server' entry\n"
            "is replaced."
        ),
    )
    parser.add_argument(
        "--creds",
        metavar="PATH",
        help="credential card to read (default: resolved automatically)",
    )
    parser.add_argument(
        "--client",
        choices=("claude", "codex", "both"),
        default=None,
        help="which coding agent to register with (default: prompt interactively)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="write confluent-mcp.env and print the commands, but change no agent config",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args()
    if args.client is None:
        clients = _prompt_for_clients()
    else:
        clients = ["claude", "codex"] if args.client == "both" else [args.client]

    node_bin = _find_preferred_node()
    _check_node(node_bin)

    project_root = get_project_root()
    card_path, card = load_card(args.creds, root=project_root)
    print(f"Credential card: {card_path}")
    warn_on_empty_card_fields(card)

    pairs = build_mcp_env(card, cloud_api_credentials(project_root))
    env_path = write_mcp_env(pairs, project_root)
    print(f"Wrote {env_path.name} ({len(pairs)} variables)")

    dist_js = _ensure_local_mcp(project_root, node_bin, dry_run=args.dry_run)

    results = [register(c, node_bin, dist_js.resolve(), env_path.resolve(), dry_run=args.dry_run) for c in clients]
    if not all(results):
        sys.exit(1)

    if not args.dry_run:
        print("Restart your coding agent to pick the server up.")


if __name__ == "__main__":
    main()
