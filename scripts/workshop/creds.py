"""Generate attendee credential cards.

  workshop creds --csv wsa-output/<run-id>/build-output.csv --name <name>
    [--resolve-op] [--social-feed-url URL] [--region us-east-1]

Reads `wsa`'s ``build-output.csv`` (one row per attendee: built-in "Account"/
"Email" columns, then one column per ``wsa-spec-aws.yaml`` `credentials:`
field, headered ``"<Group> / <Label>"``) and writes, under
``runs/<name>/credentials/``:

  <prefix>.env     machine-readable, consumed by `uv run f1-pitwall --creds <file>`
  <prefix>.md      human handout (instructor-distributed runs) — the card
  credentials.csv  one row per attendee (organizer's master sheet)

Attendees log in to the Confluent Cloud Console with the email/password on the
card and write their Flink SQL in the browser workspace; the API keys are what
`f1-pitwall` (and `f1-sql`, if they want a shell) authenticate with.

The Console password is NOT in wsa's CSV — a ``source: op`` field is written as
the literal ``(from 1Password)`` placeholder and only resolved in memory at
``wsa dispenser-upload`` time. Pass ``--resolve-op`` to do the same resolution
here for instructor-distributed cards; see ``_resolve_op_password``. Resolved
passwords are then stored in plaintext under ``runs/<name>/credentials/``
(gitignored) — the ``.md`` card is the intended human carrier.

Attendees claiming through the wsa self-serve dispenser instead reconstruct
the .env themselves with `uv run f1-onboard` from their claim email — this
command is for instructor-distributed or self-hosted runs.

``_card_fields`` also backs ``scripts/selfservice/cli.py``, which calls it
directly against a plain ``terraform output -json`` dict (no wsa CSV
involved) — see ``_row_to_outputs`` for the adapter that lets a wsa CSV row
and a raw terraform-output dict be treated identically.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import subprocess
from pathlib import Path

from scripts.common.terraform import get_project_root

# Must match the credential group `name:` in wsa-spec-aws.yaml.
GROUP = "Confluent Cloud"

# What wsa writes into build-output.csv for a `source: op` field — the real
# value never touches disk (wsa's cmd/wsa/main.go opPlaceholder).
OP_PLACEHOLDER = "(from 1Password)"

# internal field name -> wsa CSV column header ("<group> / <label>")
COLUMNS = {
    "console_url": f"{GROUP} / Console URL",
    "console_username": f"{GROUP} / Console Username",
    "console_password": f"{GROUP} / Console Password",
    "prefix": f"{GROUP} / Prefix",
    "environment_id": f"{GROUP} / Environment ID",
    "environment_url": f"{GROUP} / Environment URL",
    "environment_name": f"{GROUP} / Environment Name",
    "cluster_id": f"{GROUP} / Cluster ID",
    "cluster_name": f"{GROUP} / Cluster Name",
    "cluster_bootstrap": f"{GROUP} / Kafka Bootstrap",
    "kafka_api_key": f"{GROUP} / Kafka API Key",
    "kafka_api_secret": f"{GROUP} / Kafka API Secret",
    "schema_registry_url": f"{GROUP} / Schema Registry URL",
    "sr_api_key": f"{GROUP} / SR API Key",
    "sr_api_secret": f"{GROUP} / SR API Secret",
    "compute_pool_id": f"{GROUP} / Compute Pool ID",
    "flink_rest_endpoint": f"{GROUP} / Flink REST Endpoint",
    "flink_api_key": f"{GROUP} / Flink API Key",
    "flink_api_secret": f"{GROUP} / Flink API Secret",
    "organization_id": f"{GROUP} / Organization ID",
    "service_account_id": f"{GROUP} / Service Account ID",
}

# CSV columns for our own organizer master sheet (unrelated to wsa's CSV shape).
CSV_HEADERS = [
    "prefix",
    "email",
    "console_url",
    "console_username",
    "console_password",
    "environment_id",
    "environment_url",
    "flink_rest_endpoint",
    "compute_pool_id",
    "catalog",
    "database",
    "flink_api_key",
    "flink_api_secret",
    "kafka_bootstrap",
    "kafka_api_key",
    "kafka_api_secret",
    "schema_registry_url",
    "sr_api_key",
    "sr_api_secret",
    "social_feed_url",
    "cluster_id",
    "rtce_mcp_endpoint",
]

# --- Real-Time Context Engine keys -------------------------------------------
#
# RTCE's MCP endpoint authenticates with HTTP Basic over a **Global** Confluent
# Cloud API key. Two facts force the design here:
#
#  1. The Terraform provider can't create Global keys (only resource-scoped and
#     Cloud keys), so `confluent api-key create --resource global` is the only
#     route and this has to happen outside the apply.
#  2. Global keys are capped at 2 per principal. We mint against each attendee's
#     own service account — recreated with a fresh random_id every build and
#     destroyed at teardown — so the cap resets per workshop and the key dies
#     with the SA. Minting against the attendee's *user* account instead would
#     accumulate keys forever, since those pool accounts are permanent.
#
# Authorization is unchanged by the key's scope: it's whatever the owning
# principal already has. The SA carries EnvironmentAdmin + CloudClusterAdmin
# (modules/cluster), which covers both querying and enabling topics.
#
# Requires the `confluent` CLI logged in as OrganizationAdmin — only OrgAdmin or
# ResourceOwner-on-that-SA may create a Global key for a service account.


def _existing_global_keys(sa_id: str) -> list[str]:
    """Global API key IDs already owned by ``sa_id``, or [] if they can't be read.

    Swallows a missing/hung CLI for the same reason ``_mint_rtce_key`` does: this
    runs inside a loop over every attendee, and a degraded card must not abort the
    run. Returning [] just means the create below is attempted without pruning.
    """
    try:
        result = subprocess.run(
            ["confluent", "api-key", "list", "--resource", "global", "--service-account", sa_id, "-o", "json"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return [k for k in (row.get("key") or row.get("api_key") or "" for row in rows) if k]


def _mint_rtce_key(sa_id: str, prefix: str) -> tuple[str, str]:
    """Mint a fresh Global API key for ``sa_id``. Returns ``(key, secret)``.

    Deletes any Global keys the SA already owns first. That's not tidiness — the
    cap is 2 per principal and a secret can never be re-read after creation, so
    regenerating a card *must* replace rather than accumulate, or the third
    regeneration fails with no way to recover the working key.

    Returns ``("", "")`` on any failure: a card without RTCE keys is degraded but
    still usable for every other lab, so this must not abort the run.
    """
    for stale in _existing_global_keys(sa_id):
        subprocess.run(
            ["confluent", "api-key", "delete", stale, "--force"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    try:
        result = subprocess.run(
            [
                "confluent", "api-key", "create",
                "--resource", "global",
                "--service-account", sa_id,
                "--description", f"RTCE MCP access for workshop attendee {prefix}",
                "-o", "json",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "", ""
    if result.returncode != 0:
        return "", ""
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "", ""
    return data.get("api_key", "") or data.get("key", ""), data.get("api_secret", "") or data.get("secret", "")


def _rtce_endpoint(region: str, org_id: str, env_id: str, cluster_id: str) -> str:
    """RTCE MCP endpoint for this cluster (consumed by the organizer's
    ``f1-social-feed-rtce`` shim, not the attendee). Empty unless all IDs resolve.
    Inlined here so credential generation doesn't depend on the ``mcp`` SDK.
    """
    if not (region and org_id and env_id and cluster_id):
        return ""
    return (
        f"https://mcp.{region}.aws.confluent.cloud/mcp/v1/context-engine"
        f"/organizations/{org_id}/environments/{env_id}/kafka-clusters/{cluster_id}"
    )


def _card_fields(
    prefix: str,
    email: str,
    out: dict,
    social_feed_url: str = "",
    region: str = "",
    rtce_keys: bool = False,
) -> dict[str, str]:
    """Flatten the outputs an attendee needs into a single dict.

    ``out`` is a terraform-output-shaped dict: flat top-level keys
    (environment_id, cluster_bootstrap, ...) plus a nested
    ``attendee_credentials`` dict for the Kafka/Schema-Registry secrets —
    exactly what ``terraform output -json`` (self-service) or
    ``_row_to_outputs`` (a wsa CSV row) produce. ``social_feed_url``/``region``
    are passed in rather than read from ``out``. The RTCE endpoint is derived
    from the cluster's IDs.
    """
    ac = out.get("attendee_credentials", {})
    cluster_id = ac.get("cluster_id", "") or out.get("cluster_id", "")
    sa_id = out.get("service_account_id", "") or ac.get("service_account_id", "")
    rtce_key, rtce_secret = _mint_rtce_key(sa_id, prefix) if (rtce_keys and sa_id) else ("", "")
    return {
        "prefix": prefix,
        "email": email,
        # Console login. Empty on the standalone/self-service tracks, which
        # don't set grant_console_access — those cards stay API-key-only.
        "console_url": out.get("console_url", "") or ac.get("environment_url", ""),
        "console_username": out.get("console_username", ""),
        "console_password": out.get("console_password", ""),
        "social_feed_url": social_feed_url,
        "cluster_id": cluster_id,
        "rtce_mcp_endpoint": _rtce_endpoint(
            region, out.get("organization_id", ""), out.get("environment_id", ""), cluster_id
        ),
        "rtce_api_key": rtce_key,
        "rtce_api_secret": rtce_secret,
        "environment_id": out.get("environment_id", ""),
        "environment_url": ac.get("environment_url", ""),
        "flink_rest_endpoint": out.get("flink_rest_endpoint", ""),
        "organization_id": out.get("organization_id", ""),
        "compute_pool_id": out.get("compute_pool_id", ""),
        "catalog": out.get("environment_name", ""),
        "database": out.get("cluster_name", ""),
        "flink_api_key": ac.get("flink_api_key", ""),
        "flink_api_secret": ac.get("flink_api_secret", ""),
        "kafka_bootstrap": out.get("cluster_bootstrap", ""),
        "kafka_api_key": ac.get("kafka_api_key", ""),
        "kafka_api_secret": ac.get("kafka_api_secret", ""),
        "schema_registry_url": ac.get("schema_registry_url", ""),
        "sr_api_key": ac.get("sr_api_key", ""),
        "sr_api_secret": ac.get("sr_api_secret", ""),
    }


# wsa's 1Password layout, reconstructed here so we can resolve passwords for
# instructor-distributed cards the same way `wsa dispenser-upload` does for the
# claim email. Mirrors workshop-setup-accelerator's
# internal/onepassword/onepassword.go (DefaultVault, the "Account %03d" item
# name, the op://vault/item/section/field ref) and internal/spec/spec.go's
# PlatformKey ("Confluent Cloud" -> "confluent-cloud"). If wsa changes that
# convention this breaks silently — the symptom is every password unresolved.
OP_VAULT = "Workshop Setup Accelerator Users"
OP_PLATFORM = "confluent-cloud"


def _resolve_op_password(account: str) -> str:
    """Read one account's Confluent Cloud password out of 1Password.

    ``account`` is wsa's built-in CSV "Account" column (1, 2, 3...). Returns ""
    on any failure — a missing password degrades the card, it shouldn't abort
    the whole run.
    """
    try:
        n = int(account)
    except (TypeError, ValueError):
        return ""
    ref = f"op://{OP_VAULT}/Account {n:03d}/{OP_PLATFORM}/password"
    try:
        result = subprocess.run(["op", "read", ref], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _row_to_outputs(row: dict[str, str]) -> dict:
    """Adapt one wsa build-output.csv row into the terraform-output shape
    ``_card_fields`` expects (see its docstring)."""

    def get(key: str) -> str:
        return row.get(COLUMNS[key], "")

    return {
        "console_url": get("console_url"),
        "console_username": get("console_username"),
        "console_password": get("console_password"),
        "organization_id": get("organization_id"),
        "environment_id": get("environment_id"),
        "environment_name": get("environment_name"),
        "cluster_id": get("cluster_id"),
        "cluster_name": get("cluster_name"),
        "cluster_bootstrap": get("cluster_bootstrap"),
        "compute_pool_id": get("compute_pool_id"),
        "flink_rest_endpoint": get("flink_rest_endpoint"),
        "service_account_id": get("service_account_id"),
        "attendee_credentials": {
            "environment_url": get("environment_url"),
            "cluster_id": get("cluster_id"),
            "kafka_api_key": get("kafka_api_key"),
            "kafka_api_secret": get("kafka_api_secret"),
            "schema_registry_url": get("schema_registry_url"),
            "sr_api_key": get("sr_api_key"),
            "sr_api_secret": get("sr_api_secret"),
            "flink_api_key": get("flink_api_key"),
            "flink_api_secret": get("flink_api_secret"),
        },
    }


def _write_env(creds_dir: Path, f: dict[str, str]) -> None:
    # F1_-namespaced so it can be sourced without clobbering other env vars.
    lines = [f"F1_{k.upper()}={v}" for k, v in f.items()]
    (creds_dir / f"{f['prefix']}.env").write_text("\n".join(lines) + "\n")


def _rtce_command(f: dict[str, str]) -> str:
    """The one-line `claude mcp add` command, or "" when there's no key.

    The Basic token is pre-computed so the attendee copies one line instead of
    base64-encoding a secret by hand. Deliberately not a `uv run` script: the
    attendee never clones this repo.
    """
    if not (f.get("rtce_api_key") and f.get("rtce_mcp_endpoint")):
        return ""
    token = base64.b64encode(f"{f['rtce_api_key']}:{f['rtce_api_secret']}".encode()).decode()
    return (
        f"claude mcp add --transport http rtce {f['rtce_mcp_endpoint']} "
        f'--header "Authorization: Basic {token}"'
    )


def _rtce_section(f: dict[str, str]) -> str:
    """The paste-ready MCP registration block for the .md card, or "" with no key."""
    command = _rtce_command(f)
    if not command:
        return ""
    # Wrapped for the card's fixed width; `claude mcp add` takes it either way.
    wrapped = command.replace(' --header ', ' \\\n  --header ')
    return f"""
## Ask an AI agent about the live race (Real-Time Context Engine)

`car_telemetry` is already published to Confluent's Real-Time Context Engine,
so an AI agent can query the live sensor stream directly — no Kafka client and
no consumer group.

Register it with Claude Code (one line, run it anywhere):

```bash
{wrapped}
```

Then just ask, in plain English:

- "What are car 88's front-left tire temperatures over the last few laps?"
- "How did car 88's tire temperatures change around lap 32?"

Three tools come with it: `list_topics`, `get_metadata`, and `query_data`.
"""


def _write_md(creds_dir: Path, f: dict[str, str]) -> None:
    lab5 = ""
    if f.get("social_feed_url"):
        lab5 = (
            "\n## LAB 5 — Social media agent (watsonx Orchestrate)\n\n"
            "In the Orchestrate Agent Builder, import this OpenAPI spec as a tool, "
            f"then set the tool's `prefix` to `{f['prefix']}`:\n\n"
            f"```\n{f['social_feed_url']}/openapi.json\n```\n"
        )
    # The standalone and self-service tracks don't grant Console access (see
    # modules/environment's grant_console_access) — those cards keep the
    # API-key-and-shell story instead of printing an empty login table.
    if f.get("console_username"):
        pw = f.get("console_password")
        # Backticks only around a real value — they'd render literally around
        # the italicised fallback.
        password = f"`{pw}`" if pw else "_ask your instructor_"
        access = f"""## Your Confluent Cloud login

Sign in with the username below — **not** your own work email. It's a workshop
account we created for you.

| | |
|--|--|
| Sign in at | {f["console_url"]} |
| Username | `{f["console_username"]}` |
| Password | {password} |

## Getting started

1. Open the sign-in link above and log in.
2. Go to your environment's **Flink** tab and open a SQL workspace, then pick
   the compute pool below.
3. Confirm your environment is live:

   ```sql
   SHOW TABLES;            -- car_telemetry, race_standings, driver_race_history
   SELECT * FROM race_standings;   -- 22 cars, updating live
   ```
"""
    else:
        access = f"""## Getting started

1. Save the companion file `{f["prefix"]}.env` somewhere safe.
2. Launch the SQL shell:

   ```bash
   uv run f1-sql --creds {f["prefix"]}.env
   ```

3. Confirm your environment is live:

   ```sql
   SHOW TABLES;            -- car_telemetry, race_standings, driver_race_history
   SELECT * FROM race_standings;   -- 22 cars, updating live
   ```
"""
    md = f"""# F1 Pit Wall Workshop — Your Environment

**Attendee:** `{f["prefix"]}`  ·  **Driver:** John Doe (#88)  ·  **Circuit:** Silverstone

{access}
## Your environment

| | |
|--|--|
| Environment | `{f["environment_id"]}` |
| Compute pool | `{f["compute_pool_id"]}` |
| Catalog / Database | `{f["catalog"]}` / `{f["database"]}` |
| Flink endpoint | `{f["flink_rest_endpoint"]}` |

## The live dashboard

Save the companion file `{f["prefix"]}.env` somewhere safe and run:

```bash
uv run f1-pitwall --creds {f["prefix"]}.env
```

That file also holds your Kafka and Schema Registry API keys if you want to
connect other tools. Keep it — and this card — private; between them they grant
full access to your environment.
{_rtce_section(f)}{lab5}"""
    (creds_dir / f"{f['prefix']}.md").write_text(md)


# The column we append to wsa's build-output.csv so a dispenser claim email
# carries the RTCE setup command. The " / " is load-bearing: the dispenser's
# Apps Script (`account-dispenser/Code.gs`) only emails columns whose header
# splits on " / " into Provider / Field, and skips every other column.
#
# It must also not contain "claimed by" or "timestamp" — wsa's
# `ensureDispenserColumns` substring-matches on those to decide whether to append
# its own two tracking columns, and Code.gs assumes Timestamp sits immediately
# right of Claimed By.
DISPENSER_RTCE_COLUMN = "Real-Time Context Engine / MCP Setup Command"


def _add_dispenser_column(csv_in: Path, rows: list[dict[str, str]], commands: dict[str, str]) -> int:
    """Append (or refresh) the RTCE command column in wsa's build-output.csv.

    Rewrites the file in place, since `wsa dispenser-upload` reads it by a fixed
    path inside the run directory and there's no way to point it elsewhere. Every
    other value is copied through byte-for-byte — notably the ``(from 1Password)``
    password placeholder, which wsa resolves itself at upload time and which must
    NOT be replaced with the value we resolved into the cards.

    Returns the number of rows that got a command.
    """
    if not rows:
        return 0
    fieldnames = [k for k in rows[0] if k != DISPENSER_RTCE_COLUMN] + [DISPENSER_RTCE_COLUMN]
    filled = 0
    for row in rows:
        command = commands.get(row.get(COLUMNS["prefix"], ""), "")
        row[DISPENSER_RTCE_COLUMN] = command
        if command:
            filled += 1
    with csv_in.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    return filled


def creds(args: argparse.Namespace) -> None:
    root = get_project_root()
    csv_arg = Path(args.csv)
    csv_in = csv_arg if csv_arg.is_absolute() else root / csv_arg
    if not csv_in.exists():
        raise SystemExit(f"wsa CSV not found: {csv_in} (run `wsa build` first)")

    creds_dir = root / "runs" / args.name / "credentials"
    creds_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str]] = []
    unresolved: list[str] = []
    no_rtce: list[str] = []
    # Kept verbatim so the RTCE column can be written back into wsa's CSV below.
    raw_rows: list[dict[str, str]] = []
    rtce_commands: dict[str, str] = {}
    with csv_in.open(newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            raw_rows.append(row)
            prefix = row.get(COLUMNS["prefix"], "")
            if not prefix:
                print(f"  skip row for {row.get('Email', '?')} (no prefix — attendee not applied)")
                continue
            out = _row_to_outputs(row)
            # wsa leaves "(from 1Password)" in the CSV; swap in the real value
            # so the printed card is usable, or blank it so the card renders the
            # "ask your instructor" line instead of the placeholder.
            if out["console_password"] == OP_PLACEHOLDER:
                out["console_password"] = _resolve_op_password(row.get("Account", "")) if args.resolve_op else ""
                if not out["console_password"]:
                    unresolved.append(prefix)
            fields = _card_fields(
                prefix,
                row.get("Email", ""),
                out,
                args.social_feed_url,
                args.region,
                rtce_keys=args.rtce_keys,
            )
            if args.rtce_keys and not fields["rtce_api_key"]:
                no_rtce.append(prefix)
            rtce_commands[prefix] = _rtce_command(fields)
            _write_env(creds_dir, fields)
            _write_md(creds_dir, fields)
            rows.append(fields)
            print(f"  card {prefix} -> runs/{args.name}/credentials/{prefix}.{{env,md}}")

    if rows:
        out_csv = creds_dir / "credentials.csv"
        with out_csv.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=CSV_HEADERS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"\nMaster sheet: runs/{args.name}/credentials/credentials.csv ({len(rows)} attendees)")
    else:
        print("\nNo attendee rows with a resolved prefix found in the CSV.")

    if args.dispenser_column and any(rtce_commands.values()):
        filled = _add_dispenser_column(csv_in, raw_rows, rtce_commands)
        print(
            f'\nDispenser: added "{DISPENSER_RTCE_COLUMN}" to {csv_in.name} ({filled} row(s)).\n'
            "  `wsa dispenser-upload` will carry it into each attendee's claim email."
        )

    if unresolved:
        how = (
            "`op read` failed — check you're signed in (`op signin`) and that "
            "`wsa accept-account-invitation` has run for these accounts"
            if args.resolve_op
            else "re-run with --resolve-op to pull them from 1Password"
        )
        print(f"\nWARNING: no Console password on {len(unresolved)} card(s): {', '.join(unresolved)}\n  {how}")

    if no_rtce:
        print(
            f"\nWARNING: no RTCE key on {len(no_rtce)} card(s): {', '.join(no_rtce)}\n"
            "  Those cards drop the Real-Time Context Engine section; every other lab\n"
            "  still works. Global API keys need the `confluent` CLI logged in as\n"
            "  OrganizationAdmin (`confluent login`), and the terraform run must expose\n"
            "  the service_account_id output that wsa-spec-aws.yaml points at."
        )


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--csv", required=True, help="Path to wsa's build-output.csv (wsa-output/<run-id>/build-output.csv)")
    p.add_argument("-n", "--name", required=True, help="Workshop run name — cards go under runs/<name>/credentials/")
    p.add_argument(
        "--resolve-op",
        action="store_true",
        help="Resolve each attendee's Console password from 1Password (requires `op`, signed in). "
        "Without it the cards tell attendees to ask the instructor.",
    )
    p.add_argument("--social-feed-url", default="", help="LAB 5 race-feed base URL, stamped onto every card")
    p.add_argument("--region", default="us-east-1", help="AWS region (used to derive the RTCE MCP endpoint)")
    p.add_argument(
        "--rtce-keys",
        action="store_true",
        help="Mint each attendee a Global API key for the Real-Time Context Engine and print a "
        "ready-to-paste `claude mcp add` line on their card. Requires the `confluent` CLI logged "
        "in as OrganizationAdmin. Replaces any Global key the attendee's service account already "
        "owns (the cap is 2 per principal), so re-running invalidates previously handed-out cards.",
    )
    p.add_argument(
        "--no-dispenser-column",
        dest="dispenser_column",
        action="store_false",
        default=True,
        help=f'Do not write the "{DISPENSER_RTCE_COLUMN}" column back into the wsa CSV. '
        "By default it's appended so `wsa dispenser-upload` includes the RTCE setup command in "
        "each attendee's claim email; this rewrites build-output.csv in place.",
    )
