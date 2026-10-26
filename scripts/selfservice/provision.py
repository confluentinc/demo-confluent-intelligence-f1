"""Self-service track of `uv run deploy` — the whole workshop for one person, Confluent-only.

  uv run deploy --track self-service              # apply terraform/self-service,
                                                  # write a credential card, seed
                                                  # driver_race_history
  uv run deploy --track self-service --with-labs  # ...and prebuild LAB 3 + LAB 4
  uv run destroy                                  # tear it down

Unlike the standalone track (which also stands up EC2 Postgres + an ECS
simulator + CDC), self-service provisions Confluent Cloud only. The user runs the
simulator locally with ``uv run f1-race`` and seeds ``driver_race_history`` with
a bounded Flink INSERT — no Docker, no AWS infrastructure. The LAB 4 LLM model is
backed by AWS Bedrock keys or, with no keys at all, CSP Hosted Flink Model
Inference.
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import dotenv_values, set_key

from scripts.common import deploy_common as common
from scripts.common import deployment_meta as meta
from scripts.common.login_checks import USERNAME_KEY
from scripts.common.simulator_control import create_lab_objects
from scripts.common.terraform import get_project_root, run_terraform_output
from scripts.common.terraform_runner import run_terraform
from scripts.common.ui import prompt_with_default
from scripts.selfservice.seed import ensure_driver_race_history

TRACK = meta.SELFSERVICE
RUN_NAME = TRACK.name
REGION = common.REGION

# credentials.env keys (shared with the standalone track so the file is reusable).
REQUIRED = {
    "TF_VAR_confluent_cloud_api_key": "Confluent Cloud API Key",
    "TF_VAR_confluent_cloud_api_secret": "Confluent Cloud API Secret",
    "TF_VAR_owner_email": "Owner email (tags the Confluent environment)",
}
BEDROCK_REQUIRED = {
    "TF_VAR_aws_bedrock_access_key": "AWS Bedrock Access Key",
    "TF_VAR_aws_bedrock_secret_key": "AWS Bedrock Secret Key",
}

# Self-service is one person / one checkout / one deployment at a time, and the
# prefix is only a Confluent Cloud display label + local file-naming
# convenience (see deployment_meta.py's resolve_prefix precedence for the real
# isolation guarantees), so there's no need to derive or prompt for one — fix
# it and skip the question.
DEFAULT_PREFIX = "DEMO"

_PREFIX_SOURCES = common.prefix_sources(TRACK, f"the default self-service prefix ({DEFAULT_PREFIX!r})")


def _tf_env(cfg: dict[str, str]) -> dict[str, str]:
    """TF_VAR_* environment for the self-service tier."""
    env = {
        "TF_VAR_prefix": cfg["prefix"],
        "TF_VAR_owner_email": cfg["owner_email"],
        "TF_VAR_region": REGION,
        "TF_VAR_confluent_cloud_api_key": cfg["api_key"],
        "TF_VAR_confluent_cloud_api_secret": cfg["api_secret"],
        "TF_VAR_aws_bedrock_access_key": cfg["aws_bedrock_key"],
        "TF_VAR_aws_bedrock_secret_key": cfg["aws_bedrock_secret"],
        "TF_VAR_llm_provider": cfg.get("llm_provider") or common.DEFAULT_LLM_PROVIDER,
    }
    if cfg.get("aws_session_token"):
        env["TF_VAR_aws_session_token"] = cfg["aws_session_token"]
    return env


def export_selfservice_tf_env(creds: dict) -> None:
    """Export the TF_VAR_* set the self-service tier needs, from credentials.env.

    Terraform evaluates the config even on destroy, so `uv run destroy` needs the
    same variables as apply.

    The secrets come from credentials.env, but the **prefix** does not.
    `creds["TF_VAR_prefix"]` belongs to the standalone track, so reading it here
    pointed a self-service teardown at standalone's resource names whenever both
    tracks were used on one machine. `resolve_prefix` reads this track's own
    state first, then its own metadata, so teardown targets what it deployed.
    """
    root = get_project_root()
    prefix, _source, _error = meta.resolve_prefix(root, TRACK, owner_email=creds.get("TF_VAR_owner_email", ""))
    cfg = {
        # TF_VAR_prefix is a required variable, so it must never be exported empty
        # (Terraform would prompt, and a destroy is often unattended). Every real
        # teardown resolves from state; this only covers the degenerate case of no
        # state, no metadata, and no derivable identity.
        "prefix": prefix or TRACK.name,
        "owner_email": creds.get("TF_VAR_owner_email", ""),
        "api_key": creds.get("TF_VAR_confluent_cloud_api_key", ""),
        "api_secret": creds.get("TF_VAR_confluent_cloud_api_secret", ""),
        "aws_bedrock_key": creds.get("TF_VAR_aws_bedrock_access_key", ""),
        "aws_bedrock_secret": creds.get("TF_VAR_aws_bedrock_secret_key", ""),
        "aws_session_token": creds.get("TF_VAR_aws_session_token", ""),
        "llm_provider": common.saved_llm_provider(creds),
    }
    for k, v in _tf_env(cfg).items():
        os.environ[k] = v


def _collect_config(root, creds_file, creds: dict, automated: bool) -> dict[str, str]:
    """Gather secrets/config, prompting interactively unless --automated.

    Everything the apply needs is resolved *and validated* here.
    """
    if automated:
        cfg = {
            "api_key": creds.get("TF_VAR_confluent_cloud_api_key", ""),
            "api_secret": creds.get("TF_VAR_confluent_cloud_api_secret", ""),
            "owner_email": creds.get("TF_VAR_owner_email", ""),
            "aws_bedrock_key": creds.get("TF_VAR_aws_bedrock_access_key", ""),
            "aws_bedrock_secret": creds.get("TF_VAR_aws_bedrock_secret_key", ""),
            "aws_session_token": creds.get("TF_VAR_aws_session_token", ""),
            "llm_provider": common.llm_provider_for(root, TRACK, creds),
        }
        required = {**REQUIRED, **(BEDROCK_REQUIRED if cfg["llm_provider"] == "bedrock" else {})}
        missing = [label for key, label in required.items() if not creds.get(key)]
        if missing:
            print(f"Error: credentials.env is missing required values: {', '.join(missing)}")
            sys.exit(1)
        prefix, source = common.resolve_prefix(
            root, TRACK, cfg["owner_email"], common.explicit_prefix(), fixed_default=DEFAULT_PREFIX
        )
        cfg["prefix"] = common.validated_prefix_or_exit(prefix)
        print(f"  Prefix: {prefix}  ({_PREFIX_SOURCES[source]})")
        cfg["seconds_per_lap"] = common.validated_pacing_or_exit(
            common.seconds_per_lap_default(root, TRACK, creds), TRACK
        )
        _persist(root, creds_file, cfg, interactive=False)
        return cfg

    common.offer_key_generation(creds, creds_file)

    llm_provider = common.llm_provider_for(root, TRACK, creds)
    use_bedrock = llm_provider == "bedrock"

    print("\n--- Configuration ---\n")
    cfg = {
        "api_key": prompt_with_default("Confluent Cloud API Key", creds.get("TF_VAR_confluent_cloud_api_key", "")),
        "api_secret": prompt_with_default(
            "Confluent Cloud API Secret", creds.get("TF_VAR_confluent_cloud_api_secret", ""), secret=True
        ),
        "owner_email": prompt_with_default(
            "Owner email", creds.get("TF_VAR_owner_email") or creds.get(USERNAME_KEY) or ""
        ),
        "aws_bedrock_key": (
            prompt_with_default("AWS Bedrock Access Key", creds.get("TF_VAR_aws_bedrock_access_key", ""))
            if use_bedrock
            else creds.get("TF_VAR_aws_bedrock_access_key", "")
        ),
        "aws_bedrock_secret": (
            prompt_with_default("AWS Bedrock Secret Key", creds.get("TF_VAR_aws_bedrock_secret_key", ""), secret=True)
            if use_bedrock
            else creds.get("TF_VAR_aws_bedrock_secret_key", "")
        ),
        "aws_session_token": "",
        "llm_provider": llm_provider,
    }

    # Not prompted: self-service pins a fixed prefix (see DEFAULT_PREFIX) unless
    # a deployment already exists, one was saved from a prior run, or the
    # operator exported TF_VAR_prefix — `resolve_prefix` still honors all of
    # those ahead of the default so a rerun never silently renames a live
    # deployment.
    resolved, source = common.resolve_prefix(
        root, TRACK, cfg["owner_email"], common.explicit_prefix(), fixed_default=DEFAULT_PREFIX
    )
    cfg["prefix"] = common.validated_prefix_or_exit(resolved)
    print(f"  Prefix: {cfg['prefix']}  ({_PREFIX_SOURCES[source]})")
    cfg["seconds_per_lap"] = common.validated_pacing_or_exit(common.seconds_per_lap_default(root, TRACK, creds), TRACK)

    if use_bedrock and cfg["aws_bedrock_key"].startswith("ASIA"):
        cfg["aws_session_token"] = prompt_with_default(
            "AWS Session Token (required for temporary credentials)",
            creds.get("TF_VAR_aws_session_token", ""),
            secret=True,
        )

    _persist(root, creds_file, cfg, interactive=True)
    return cfg


def _persist(root, creds_file, cfg: dict[str, str], interactive: bool) -> None:
    """Record the resolved inputs before anything is applied.

    `TF_VAR_prefix` is deliberately **not** written to credentials.env: that one
    root file feeding both tracks' Terraform inputs is what made a self-service
    teardown target standalone's names. This track's prefix lives only in its own
    metadata, which `export_selfservice_tf_env` reads.
    """
    meta.save_meta(
        root,
        TRACK,
        **{
            meta.KEY_BASE_PREFIX: meta.derive_base_prefix(cfg["owner_email"]),
            meta.KEY_RESOLVED_PREFIX: cfg["prefix"],
            meta.KEY_SECONDS_PER_LAP: cfg["seconds_per_lap"],
            meta.KEY_REGION: REGION,
        },
    )
    # credentials.env is the source of truth for the user's choices, so the
    # provider is written even on --automated: an exported TF_VAR_llm_provider
    # must survive into later runs and teardown, or a re-run without the export
    # would swap a CSP-hosted model back to Bedrock.
    set_key(str(creds_file), common.LLM_PROVIDER_KEY, cfg["llm_provider"])
    if not interactive:
        return
    for key, value in {
        "TF_VAR_confluent_cloud_api_key": cfg["api_key"],
        "TF_VAR_confluent_cloud_api_secret": cfg["api_secret"],
        "TF_VAR_owner_email": cfg["owner_email"],
        "TF_VAR_aws_bedrock_access_key": cfg["aws_bedrock_key"],
        "TF_VAR_aws_bedrock_secret_key": cfg["aws_bedrock_secret"],
    }.items():
        set_key(str(creds_file), key, value)
    if cfg["aws_session_token"]:
        set_key(str(creds_file), "TF_VAR_aws_session_token", cfg["aws_session_token"])


def deploy(args: argparse.Namespace, root, creds_file, creds: dict) -> None:
    print("\n=== F1 Workshop — Self-Service (Confluent-only, run one person) ===\n")
    cfg = _collect_config(root, creds_file, creds, args.automated)
    common.check_bedrock_or_exit(cfg, args.automated)

    common.print_summary(
        "Summary",
        {
            "Region": REGION,
            "Prefix": f"{cfg['prefix']}   (Confluent env: RIVER-RACING-{cfg['prefix']}-ENV)",
            "Owner": cfg["owner_email"],
            "Pacing": f"{cfg['seconds_per_lap']}s/lap for `uv run f1-race`",
            "LLM": common.LLM_PROVIDERS[cfg["llm_provider"]],
            "Labs": "prebuilt (LAB 3 + LAB 4)" if args.with_labs else "not built (you write them)",
            "Creates": "Confluent env + cluster + Flink pool + topics + LLM models (no AWS infra)",
        },
        creds_file,
    )
    if not args.automated and input("\nReady to provision? (y/n): ").strip().lower() != "y":
        print("Cancelled.")
        sys.exit(0)

    os.environ.setdefault("AWS_RETRY_MODE", "adaptive")
    os.environ.setdefault("AWS_MAX_ATTEMPTS", "10")
    for k, v in _tf_env(cfg).items():
        os.environ[k] = v

    ss_path = root / "terraform" / "self-service"
    print("\n=== Provisioning Confluent environment (terraform/self-service) ===")
    if not run_terraform(ss_path):
        print("\nProvisioning failed. `uv run destroy` to clean up, then retry.")
        sys.exit(1)

    out = run_terraform_output(ss_path / "terraform.tfstate")
    card_path = common.write_card(root, TRACK, cfg, out, rtce_keys=True)
    print(f"\nCredential card: {card_path}  (recorded as F1_CARD in credentials.env)")

    # Seed driver_race_history. Idempotent: it counts before inserting, verifies
    # afterwards, and only then records the environment it verified.
    print("\n=== driver_race_history ===")
    seeded = ensure_driver_race_history(dotenv_values(card_path), root, TRACK)

    labs_ok = True
    if args.with_labs:
        # No ECS here — the race is the local `uv run f1-race`, which the user
        # starts *after* this returns. So the labs are necessarily RUNNING before
        # any `race_standings` row is produced, which is the ordering LAB 3's
        # temporal join needs (see scripts/common/simulator_control.py).
        print("\n=== Prebuilding the labs (--with-labs) ===")
        labs_ok = create_lab_objects(out, root)
        if not labs_ok:
            print("\nLab build FAILED — see the error above. The environment is up; fix the")
            print("SQL problem and re-run `uv run deploy --track self-service --with-labs`.")

    labs_live = args.with_labs and labs_ok
    print("\n=== Ready ===\n")
    print(f"Flink SQL Workspace:  {common.flink_workspace_url(out)}\n")
    if labs_live:
        print("LAB 3 and LAB 4 are already running — they start filling as soon as the race does.")
        print("  `car_state` stays empty for ~4 min while anomaly detection fills its")
        print("  first 12 windows of context. The anomaly fires around lap 24.\n")
    print("1. Start the live race feed (leave running in its own terminal):")
    print(f"     uv run f1-race          # {cfg['seconds_per_lap']}s/lap, from this deployment's config")
    if not labs_live:
        print("   Create `car_state` first (section 1) — laps raced before it is Running are lost.")
    print("2. Open the live dashboard:")
    print("     uv run f1-pitwall")
    if out.get("rtce_api_key"):
        print("3. (optional) Connect an AI agent to the live feed via RTCE:")
        print("     uv run setup-rtce")
    if not labs_live:
        print("\nContinue in docs/tracks/SELF-SERVICE.md at section 1.")
    print("\nTear down when finished:  uv run destroy")

    if not args.automated and seeded:
        common.offer_start(root, ["uv", "run", "f1-race"] if labs_live else None)

    if not (seeded and labs_ok):
        # Exit nonzero so a scripted run can tell a half-built environment from a
        # complete one — an empty driver_race_history used to be entirely silent.
        sys.exit(1)
