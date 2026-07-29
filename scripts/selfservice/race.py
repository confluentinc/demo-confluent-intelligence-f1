"""``f1-race`` — run the race simulator locally from a credential card.

The self-service stand-in for the ECS Fargate simulator service in the AWS path.
Maps the ``F1_*`` credential-card variables to the simulator's expected env
(``datagen/config.py``) and runs it against the user's own Confluent cluster.

  uv run f1-race
  uv run f1-race --creds <card>.env --seconds-per-lap 60 --once
"""

from __future__ import annotations

import argparse
import os
import sys

from scripts.common.credentials import load_card


def _strip_scheme(bootstrap: str) -> str:
    """confluent-kafka wants host:port; the card bootstrap may carry a scheme."""
    return bootstrap.split("://", 1)[-1] if "://" in bootstrap else bootstrap


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the F1 race simulator locally from a credential card")
    parser.add_argument(
        "--creds",
        help="Path to your <prefix>.env credential card (default: read from credentials.env)",
    )
    parser.add_argument(
        "--seconds-per-lap",
        type=int,
        default=20,
        help="Wall-clock seconds per simulated lap (default 20 → ~20-minute race)",
    )
    parser.add_argument("--once", action="store_true", help="Run a single race instead of looping continuously")
    args = parser.parse_args()

    path, card = load_card(args.creds)
    print(f"Using credential card: {path}")

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
    os.environ["SECONDS_PER_LAP"] = str(args.seconds_per_lap)
    os.environ["RACE_LOOP"] = "false" if args.once else "true"

    # Import after setting env — datagen.config reads os.environ at import time.
    from datagen import simulator

    simulator.main()


if __name__ == "__main__":
    main()
