"""Pieces `uv run deploy` shares between its tracks.

`deploy.py` asks which track to deploy, then hands off to the standalone
(Confluent + AWS), self-service (Confluent-only) or pre-provisioned
(Confluent-only, in a customer's existing environment) flow. Everything the flows
prompt for, validate, print, or write lives here, so they can't drift apart
again: prefix resolution, pacing, the LLM provider choice, Confluent key
generation, the Bedrock smoke test, the masked summary, the credential card, and
the post-deploy offer to start the dashboard.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values, set_key

from scripts.common import deployment_meta as meta
from scripts.common.credentials import generate_confluent_api_keys, set_active_card
from scripts.common.login_checks import ensure_confluent_login, ensure_saved_confluent_login
from scripts.common.ui import display_value
from scripts.workshop import creds as creds_mod

REGION = "us-east-1"
DEFAULT_SECONDS_PER_LAP = "20"

# Offered in this order; the first is the default for a fresh checkout.
TRACKS = {
    "self-service": (meta.SELFSERVICE, "Self-service — Confluent Cloud only; the race runs on this machine"),
    "standalone": (meta.STANDALONE, "Standalone — Confluent Cloud + AWS (Postgres CDC, ECS simulator)"),
    "pre-provisioned": (
        meta.PREPROVISIONED,
        "Pre-provisioned — self-service in an environment your platform team created for you",
    ),
}

# What backs `llm_textgen_model` (`llm_provider` in terraform/self-service and
# terraform/aws). "confluent" is CSP Hosted Flink Model Inference: no AWS keys at
# all, and the default; `uv run deploy --bedrock` opts into Bedrock. Saved in
# credentials.env — the source of truth for the user's choices.
LLM_PROVIDER_KEY = "TF_VAR_llm_provider"
LLM_PROVIDERS = {
    "confluent": "CSP Hosted Flink Model Inference (Confluent-hosted, no AWS keys)",
    "bedrock": "AWS Bedrock (your own AWS access keys)",
}
DEFAULT_LLM_PROVIDER = "confluent"

# Must match the model both tiers' Bedrock connection invokes (main.tf endpoint).
BEDROCK_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"


def pick_track(root: Path, requested: str | None, automated: bool) -> meta.Track:
    """The track to deploy: --track, else a prompt, else (automated) the only live one."""
    if requested:
        return TRACKS[requested][0]
    live = [track for track, _label in TRACKS.values() if meta.is_deployed(root, track)]
    if automated:
        if len(live) == 1:
            return live[0]
        print(f"Error: --automated needs --track ({' | '.join(TRACKS)})")
        print("       (it can only infer the track when exactly one is already deployed).")
        sys.exit(1)
    # Re-running against an existing deployment is the common case, so default to it.
    labels = {track: label for track, label in TRACKS.values()}
    order = live + [t for t in labels if t not in live]
    print("Which deployment?")
    for i, track in enumerate(order, 1):
        tag = "  [deployed]" if track in live else ""
        print(f"  {i}. {labels[track]}{tag}{'  [default]' if i == 1 else ''}")
    while True:
        choice = input(f"Enter choice (1-{len(order)}) [1]: ").strip() or "1"
        if choice.isdigit() and 1 <= int(choice) <= len(order):
            return order[int(choice) - 1]
        print(f"  Please enter a number between 1 and {len(order)}.")


def explicit_prefix() -> str | None:
    """A prefix the user asked for *deliberately*, or None.

    Only an **exported** `TF_VAR_prefix` counts. credentials.env's copy is not
    consulted: one root file that both tracks wrote is how standalone once
    deployed under self-service's name. Per-track metadata is the memory of what
    each track used.
    """
    return (os.environ.get("TF_VAR_prefix") or "").strip() or None


def resolve_prefix(
    root: Path, track: meta.Track, owner_email: str, explicit: str | None, fixed_default: str | None = None
) -> tuple[str, str]:
    """(prefix, source) for this track, or exit. Never renames a deployed track.

    `fixed_default` replaces the identity-derived prefix when nothing is deployed,
    saved, or exported (self-service pins one, since it is one person per checkout).
    """
    prefix, source, error = meta.resolve_prefix(root, track, owner_email=owner_email, explicit=explicit)
    if error:
        print(f"\nError: {error}")
        sys.exit(1)
    if source == "derived" and fixed_default:
        prefix = fixed_default
    # Live state wins over saved metadata, but say so: disagreement means an
    # interrupted run or a hand-edited file.
    saved = meta.load_meta(root, track).get(meta.KEY_RESOLVED_PREFIX)
    if source == "state" and saved and saved != prefix:
        print(f"\nNote: runs/{track.name}/deployment.env records {saved!r}, but the deployed")
        print(f"      environment is {prefix!r}. Using the deployed name and updating the record.")
    return prefix, source


def prefix_sources(track: meta.Track, derived: str) -> dict[str, str]:
    """How resolve_prefix arrived at a value, phrased for the person reading the run."""
    return {
        "state": "already deployed — cannot be changed without tearing down first",
        "saved": f"reused from runs/{track.name}/deployment.env",
        "explicit": "from the exported TF_VAR_prefix",
        "derived": derived,
    }


def validated_prefix_or_exit(prefix: str) -> str:
    """Reject an unusable prefix *before* any cloud call."""
    problem = meta.validate_prefix(prefix)
    if not problem:
        return prefix
    print(f"\nError: {problem}")
    if not prefix:
        print("$USER looks like a shared account and no owner email was available, so")
        print("there was nothing to derive one from. Export TF_VAR_prefix=<name> and re-run,")
        print("or run `uv run deploy` without --automated and answer the prompt.")
    sys.exit(1)


def seconds_per_lap_default(root: Path, track: meta.Track, creds: dict) -> str:
    """Exported TF_VAR_seconds_per_lap, then this track's metadata, then credentials.env, then 20."""
    return (
        os.environ.get("TF_VAR_seconds_per_lap")
        or meta.load_meta(root, track).get(meta.KEY_SECONDS_PER_LAP)
        or creds.get("TF_VAR_seconds_per_lap")
        or DEFAULT_SECONDS_PER_LAP
    )


def validated_pacing_or_exit(raw: str, track: meta.Track) -> str:
    """Reject bad pacing *before* Terraform, Docker, AWS, or Confluent work."""
    value, problem = meta.validate_seconds_per_lap(raw)
    if problem:
        print(f"\nError: {problem}")
        print(f"Fix TF_VAR_seconds_per_lap (exported, credentials.env, or runs/{track.name}/deployment.env).")
        sys.exit(1)
    return str(value)


def saved_llm_provider(creds: dict) -> str:
    """Exported TF_VAR_llm_provider, then credentials.env, then the default."""
    value = (os.environ.get(LLM_PROVIDER_KEY) or creds.get(LLM_PROVIDER_KEY) or DEFAULT_LLM_PROVIDER).strip()
    if value not in LLM_PROVIDERS:
        print(f"Error: {LLM_PROVIDER_KEY}={value!r} — expected one of: {', '.join(LLM_PROVIDERS)}")
        sys.exit(1)
    return value


def deployed_llm_provider(root: Path, track: meta.Track) -> str | None:
    """What the live `llm_textgen_model` is backed by, read from Terraform state.

    Both tiers create `bedrock_textgen_connection` only for Bedrock. None when the
    track isn't deployed or the state can't be read.
    """
    try:
        resources = json.loads(meta.tf_state_path(root, track).read_text()).get("resources", [])
    except (OSError, ValueError):
        return None
    live = {r.get("name") for r in resources if r.get("instances")}
    if "llm_textgen_model" not in live:
        return None
    return "bedrock" if "bedrock_textgen_connection" in live else "confluent"


def llm_provider_for(root: Path, track: meta.Track, creds: dict) -> str:
    """The provider to deploy: exported, else what this track runs now, else credentials.env.

    credentials.env holds ONE `TF_VAR_llm_provider` for both tracks, so on its own
    it would let a choice made for self-service silently swap a live standalone
    model (or the reverse) on the next `--automated` run. The deployed state wins
    over it; an explicit export still wins over both.
    """
    if (os.environ.get(LLM_PROVIDER_KEY) or "").strip():
        return saved_llm_provider(creds)
    deployed = deployed_llm_provider(root, track)
    saved = saved_llm_provider(creds)
    if deployed and deployed != saved:
        print(f"  LLM provider: keeping {deployed!r}, which this {track.name} deployment runs now")
        print(f"    (credentials.env says {saved!r}; export {LLM_PROVIDER_KEY}={saved} to switch it).")
    return deployed or saved


def offer_key_generation(creds: dict, creds_file: Path) -> None:
    """Save/verify the Confluent Cloud login, then offer to mint an OrganizationAdmin key.

    The login prompt is skippable: Terraform authenticates with the API key, so a
    user who already has one and presses Enter still deploys.
    """
    logged_in = ensure_saved_confluent_login(creds, creds_file)
    if input("\nGenerate new Confluent Cloud API keys? (y/n) [n]: ").strip().lower() != "y":
        return
    # The only step that needs a Confluent CLI session.
    if not (logged_in or ensure_confluent_login(creds, creds_file=creds_file, interactive=True)):
        sys.exit(1)
    api_key, api_secret = generate_confluent_api_keys()
    if api_key and api_secret:
        set_key(str(creds_file), "TF_VAR_confluent_cloud_api_key", api_key)
        set_key(str(creds_file), "TF_VAR_confluent_cloud_api_secret", api_secret)
        creds["TF_VAR_confluent_cloud_api_key"] = api_key
        creds["TF_VAR_confluent_cloud_api_secret"] = api_secret


def bedrock_check(access_key: str, secret_key: str, session_token: str = "") -> tuple[bool, str]:
    """(ok, detail): can these keys invoke the agent's Claude model on Bedrock?"""
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        return False, "boto3 is not installed"
    client = boto3.client(
        "bedrock-runtime",
        region_name=REGION,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        aws_session_token=session_token or None,
    )
    body = {"anthropic_version": "bedrock-2023-05-31", "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}]}
    try:
        client.invoke_model(modelId=BEDROCK_MODEL_ID, body=json.dumps(body))
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("UnrecognizedClientException", "InvalidSignatureException", "ExpiredTokenException"):
            return False, f"AWS rejected the keys ({code})"
        if code == "AccessDeniedException":
            return False, f"the keys can't invoke {BEDROCK_MODEL_ID} in {REGION} — enable model access or fix IAM"
        if code == "ThrottlingException":
            return True, "throttled, but the keys are valid"
        return False, f"{code}: {e.response.get('Error', {}).get('Message', '')}"
    except BotoCoreError as e:
        return False, str(e)
    return True, ""


def check_bedrock_or_exit(cfg: dict, automated: bool) -> None:
    """Test the Bedrock keys before any apply. Skipped for CSP-hosted inference.

    Terraform accepts any key string, so bad keys used to surface only in LAB 4,
    as an agent that returns nothing. Interactive runs may continue anyway;
    automated runs stop, since nobody is there to read a warning.
    """
    if cfg.get("llm_provider") != "bedrock":
        return
    print("\nChecking the AWS keys can invoke Claude on Bedrock...")
    ok, detail = bedrock_check(cfg["aws_bedrock_key"], cfg["aws_bedrock_secret"], cfg.get("aws_session_token", ""))
    if ok:
        print(f"  Bedrock credentials work{f' ({detail})' if detail else ''}")
        return
    print(f"  Bedrock check failed: {detail}")
    print("  Without working keys the pit-strategy agent in LAB 4 returns nothing.")
    print("  Mint new ones with `uv run api-keys create`, or drop --bedrock to use CSP Hosted inference.")
    if automated or input("Deploy anyway? (y/n) [n]: ").strip().lower() != "y":
        sys.exit(1)


def print_summary(title: str, rows: dict[str, str], creds_file: Path) -> None:
    """Deployment facts, then every saved credential with secrets masked."""
    print(f"\n--- {title} ---")
    for label, value in rows.items():
        print(f"  {label + ':':<10}{value}")
    print(f"\n  Saved in {creds_file.name} (secrets masked):")
    for key, value in sorted(dotenv_values(creds_file).items()):
        if value:
            print(f"    {key}: {display_value(key, value)}")


def write_card(root: Path, track: meta.Track, cfg: dict, out: dict, rtce_keys: bool, region: str = REGION) -> Path:
    """Write the credential card and point credentials.env's F1_CARD at it.

    Same generator the workshop uses, so the card format is identical everywhere.
    `region` places the RTCE endpoint; the pre-provisioned track passes its
    cluster's, since the platform team picks it.
    """
    creds_dir = root / "runs" / track.name / "credentials"
    creds_dir.mkdir(parents=True, exist_ok=True)
    fields = creds_mod._card_fields(
        cfg["prefix"], cfg["owner_email"], out, social_feed_url="", region=region, rtce_keys=rtce_keys
    )
    creds_mod._write_env(creds_dir, fields)
    creds_mod._write_md(creds_dir, fields)
    card = creds_dir / f"{cfg['prefix']}.env"
    set_active_card(root, card)
    meta.save_meta(root, track, **{meta.KEY_CARD: str(card.relative_to(root))})
    return card


def flink_workspace_url(out: dict) -> str:
    env_id = out.get("environment_id")
    return f"https://confluent.cloud/environments/{env_id}/flink" if env_id else "https://confluent.cloud/go/flink"


def start_background(root: Path, command: list[str], log_name: str) -> int:
    """Run a `uv run` tool detached, logging to logs/<log_name>; returns the pid."""
    log = root / "logs" / log_name
    log.parent.mkdir(exist_ok=True)
    with open(log, "a", encoding="utf-8") as fh:
        proc = subprocess.Popen(
            command,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    print(f"  Started `{' '.join(command)}` (pid {proc.pid}; log: {log.relative_to(root)}; stop: kill -- -{proc.pid})")
    return proc.pid


def offer_start(root: Path, race_command: list[str] | None) -> None:
    """Offer to start the Pit Wall dashboard, and the race when it is safe to.

    `race_command` is None when the race must wait: without prebuilt labs,
    `car_state` has to be RUNNING before the first `race_standings` row, or LAB 3's
    temporal join silently drops those laps.
    """
    what = "the race feed and the Pit Wall dashboard" if race_command else "the Pit Wall dashboard"
    if input(f"\nStart {what} in the background now? (y/n) [y]: ").strip().lower() not in ("", "y", "yes"):
        return
    if race_command:
        start_background(root, race_command, "f1-race.log")
    start_background(root, ["uv", "run", "f1-pitwall"], "f1-pitwall.log")
    print("  Dashboard: http://localhost:8000")
