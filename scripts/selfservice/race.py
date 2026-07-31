"""``f1-race`` — run the race simulator locally from a credential card.

The self-service stand-in for the ECS Fargate simulator service in the AWS path.
Maps the ``F1_*`` credential-card variables to the simulator's expected env
(``datagen/config.py``) and runs it against the user's own Confluent cluster.

  uv run f1-race                     # pacing from this deployment's config
  uv run f1-race --20                # shorthand for --seconds-per-lap 20
  uv run f1-race --creds <card>.env --seconds-per-lap 60 --once
"""

from __future__ import annotations

import argparse
import os
import sys

from scripts.common import deployment_meta as meta
from scripts.common.credentials import load_card
from scripts.common.terraform import get_project_root

# Fallback only — the persisted per-track pacing wins when there is one.
DEFAULT_SECONDS_PER_LAP = 20


def _strip_scheme(bootstrap: str) -> str:
    """confluent-kafka wants host:port; the card bootstrap may carry a scheme."""
    return bootstrap.split("://", 1)[-1] if "://" in bootstrap else bootstrap


def _expand_numeric_flags(argv: list[str]) -> list[str]:
    """Rewrite the ``--<N>`` shorthand to ``--seconds-per-lap <N>``.

    Restores the ergonomics of the old ``scripts/start_race.py``: ``--20`` reads
    naturally at a demo and is far less to type. argparse cannot express it (a
    numeric option name is not a valid dest), so it is preprocessed here.
    """
    out: list[str] = []
    for arg in argv:
        if arg.startswith("--") and arg[2:].isdigit():
            out += ["--seconds-per-lap", arg[2:]]
        else:
            out.append(arg)
    return out


def _track_for_card(root, card_path) -> meta.Track | None:
    """Which deployment track a resolved card belongs to, by its runs/ directory.

    Cards live at ``runs/<track>/credentials/<prefix>.env``, so the path names the
    track whose ``deployment.env`` holds the matching pacing. Following the card
    rather than guessing keeps `f1-race` consistent with whichever environment
    ``F1_CARD`` currently points at.
    """
    try:
        parts = card_path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        return None
    return meta.TRACKS.get(parts[1]) if len(parts) > 2 and parts[0] == "runs" else None


def _resolve_seconds_per_lap(explicit: int | None, root, card_path) -> int:
    """Explicit flag > this deployment's persisted pacing > the built-in default.

    ``argparse`` cannot own the default here: a default value is indistinguishable
    from a user-supplied one, so it would always beat the persisted pacing.
    """
    if explicit is not None:
        raw: str | int = explicit
        source = "--seconds-per-lap"
    else:
        track = _track_for_card(root, card_path)
        saved = meta.load_meta(root, track).get(meta.KEY_SECONDS_PER_LAP) if track else None
        raw = saved or DEFAULT_SECONDS_PER_LAP
        source = f"runs/{track.name}/deployment.env" if saved else "default"

    value, problem = meta.validate_seconds_per_lap(raw)
    if problem:
        # A pace below the minimum is worse than a crash: readings_per_lap is
        # SECONDS_PER_LAP // 2 (datagen/simulator.py), so `--seconds-per-lap 1`
        # produced zero telemetry records per lap while the log cheerfully
        # reported lap progress.
        sys.exit(f"Error: {problem}")
    print(f"Pacing: {value}s/lap (~{60 * value // 60}-minute race, from {source})")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the F1 race simulator locally from a credential card",
        epilog="Shorthand: --20 is the same as --seconds-per-lap 20.",
    )
    parser.add_argument(
        "--creds",
        help="Path to your <prefix>.env credential card (default: read from credentials.env)",
    )
    parser.add_argument(
        "--seconds-per-lap",
        type=int,
        default=None,
        metavar="N",
        help=(
            f"Wall-clock seconds per simulated lap (minimum {meta.MIN_SECONDS_PER_LAP}). "
            "Defaults to the pacing recorded for this deployment, else 20."
        ),
    )
    parser.add_argument("--once", action="store_true", help="Run a single race instead of looping continuously")
    args = parser.parse_args(_expand_numeric_flags(sys.argv[1:]))

    root = get_project_root()
    path, card = load_card(args.creds)
    print(f"Using credential card: {path}")
    seconds_per_lap = _resolve_seconds_per_lap(args.seconds_per_lap, root, path)

    def need(key: str) -> str:
        v = card.get(key)
        if not v:
            sys.exit(f"Credential card is missing {key}. Regenerate it with `uv run selfservice up`.")
        return v

    # Map card F1_* vars to the simulator's expected env (datagen/config.py).
    os.environ["KAFKA_BOOTSTRAP"] = _strip_scheme(need("F1_KAFKA_BOOTSTRAP"))
    os.environ["KAFKA_API_KEY"] = need("F1_KAFKA_API_KEY")
    os.environ["KAFKA_API_SECRET"] = need("F1_KAFKA_API_SECRET")
    os.environ["SR_URL"] = need("F1_SCHEMA_REGISTRY_URL")
    os.environ["SR_API_KEY"] = need("F1_SR_API_KEY")
    os.environ["SR_API_SECRET"] = need("F1_SR_API_SECRET")
    os.environ["SECONDS_PER_LAP"] = str(seconds_per_lap)
    os.environ["RACE_LOOP"] = "false" if args.once else "true"

    # Skip the pre-race warmup laps (~140s at 20s/lap). They produce four
    # telemetry windows at lap=0 and **no** race_standings, and LAB 3's first CTE
    # is an *inner* temporal join against race_standings — so on a cold start
    # those rows have no version to join against and never reach
    # ML_DETECT_ANOMALIES at all. Even if they did, 4 windows against
    # minTrainingSize=20 changes nothing: real race data supplies 20 windows
    # (2/lap) by lap 10, long before the lap-32 anomaly.
    #
    # setdefault, so `PRE_RACE_WARMUP_LAPS=4 uv run f1-race` still works. The
    # standalone ECS path keeps the warmup — its value comes from the task
    # definition in terraform/, not from here.
    os.environ.setdefault("PRE_RACE_WARMUP_LAPS", "0")

    # Import after setting env — datagen.config reads os.environ at import time.
    from datagen import simulator

    simulator.main()


if __name__ == "__main__":
    main()
