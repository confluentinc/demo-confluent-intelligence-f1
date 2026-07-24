"""F1 social-media race feed — shared HTTP service behind the Orchestrate lab.

  # Organizer: serve every attendee's feed from their credential cards
  uv run f1-social-feed --creds-glob 'runs/*/credentials/*.env'

  # Single attendee (smoke test)
  uv run f1-social-feed --creds runs/<name>/credentials/f1wp001.env

  # Offline demo / dev — no Kafka, synthesizes prefix f1wp001
  uv run f1-social-feed --mock

Tails each attendee's race topics (race_standings, car_state, pit_decisions) and
serves a per-attendee digest at ``GET /race-feed/{prefix}``. A watsonx Orchestrate
no-code agent imports the OpenAPI spec (``/openapi.json``) as a tool and calls it
to draft social posts (LAB 5). One organizer-hosted instance serves all attendees
— their isolated clusters mean the Orchestrate cloud can't reach a per-attendee
localhost, so this service loads every credential card and runs one consumer each.

Host it where the agent can reach it (public HTTPS). Stop with Ctrl-C.
"""

from __future__ import annotations

import argparse
import glob
import logging
import sys
import threading
from pathlib import Path

import uvicorn
from dotenv import dotenv_values

from scripts.social_feed.mock import MOCK_PREFIX, run_mock
from scripts.social_feed.server import create_app
from scripts.social_feed.state import FeedStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("f1-social-feed")


def _prefix_for(path: Path, creds: dict[str, str]) -> str:
    """Prefer the card's F1_PREFIX; fall back to the filename stem."""
    return creds.get("F1_PREFIX") or path.stem


def _load_cards(args) -> list[tuple[Path, dict[str, str]]]:
    paths: list[Path] = []
    if args.creds_glob:
        paths += [Path(p) for p in glob.glob(args.creds_glob)]
    if args.creds:
        paths.append(Path(args.creds))
    if not paths:
        sys.exit(
            "Provide --creds-glob 'runs/*/credentials/*.env' or --creds <card>.env (or use --mock). "
            "Example: uv run f1-social-feed --creds-glob 'runs/*/credentials/*.env'"
        )
    cards: list[tuple[Path, dict[str, str]]] = []
    for path in sorted(set(paths)):
        if not path.exists():
            sys.exit(f"Credential file not found: {path}")
        cards.append((path, dict(dotenv_values(path))))
    return cards


def main() -> None:
    parser = argparse.ArgumentParser(description="F1 social-media race feed (OpenAPI tool for watsonx Orchestrate)")
    parser.add_argument("--creds", help="Path to a single <prefix>.env credential card")
    parser.add_argument("--creds-glob", help="Glob matching many credential cards, e.g. 'runs/*/credentials/*.env'")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Port (default 8080)")
    parser.add_argument("--mock", action="store_true", help="Offline demo feed — no Kafka / no Confluent env needed")
    args = parser.parse_args()

    store = FeedStore()
    stop = threading.Event()
    threads: list[threading.Thread] = []

    if args.mock:
        feed = store.get_or_create(MOCK_PREFIX)
        threads.append(threading.Thread(target=run_mock, args=(feed, stop), daemon=True))
        logger.info("MOCK feed serving prefix %s", MOCK_PREFIX)
    else:
        from scripts.social_feed.consumer import run_consumer

        cards = _load_cards(args)
        for path, creds in cards:
            prefix = _prefix_for(path, creds)
            feed = store.get_or_create(prefix)
            threads.append(threading.Thread(target=run_consumer, args=(creds, feed, stop), daemon=True))
        logger.info("Serving %d feed(s): %s", len(cards), ", ".join(store.prefixes()))

    for t in threads:
        t.start()

    logger.info("Race feed live on http://%s:%d  (OpenAPI at /openapi.json, Ctrl-C to stop)", args.host, args.port)
    app = create_app(store)
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()


if __name__ == "__main__":
    main()
