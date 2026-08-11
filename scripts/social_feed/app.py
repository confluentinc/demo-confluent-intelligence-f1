"""F1 social-media race feed — shared HTTP service behind the Orchestrate lab.

  # Organizer: serve the shared Watsonx feed from account 50
  uv run f1-social-feed --creds runs/<run-id>/credentials/f1wp050.env \
    --public-base-url https://small-underpass-refinery.ngrok-free.dev \
    --fixed-prefix f1wp050

  # Single attendee (smoke test)
  uv run f1-social-feed --creds runs/<name>/credentials/f1wp001.env

  # Offline demo / dev — no Kafka, synthesizes prefix f1wp001
  uv run f1-social-feed --mock

Tails race_standings, car_state and pit_decisions, then serves a digest at
``GET /race-feed/{prefix}``. For LAB 5, every attendee uploads the no-input file
at ``/watsonx/f1-race-feed-openapi.json``; it points to the organizer-controlled
account-50 feed. The dynamic route and default OpenAPI document remain available
for internal diagnostics and older multi-card uses.

Host it where the agent can reach it (public HTTPS). Stop with Ctrl-C.
"""

from __future__ import annotations

import argparse
import glob
import logging
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from dotenv import dotenv_values

from scripts.social_feed.mock import MOCK_PREFIX, run_mock
from scripts.social_feed.server import create_app
from scripts.social_feed.state import FeedStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("f1-social-feed")

REQUIRED_CARD_FIELDS = (
    "F1_KAFKA_BOOTSTRAP",
    "F1_KAFKA_API_KEY",
    "F1_KAFKA_API_SECRET",
    "F1_SCHEMA_REGISTRY_URL",
    "F1_SR_API_KEY",
    "F1_SR_API_SECRET",
)


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
        creds = dict(dotenv_values(path))
        missing = [name for name in REQUIRED_CARD_FIELDS if not str(creds.get(name) or "").strip()]
        if missing:
            sys.exit(f"Credential file {path} is missing required field(s): {', '.join(missing)}")
        cards.append((path, creds))
    return cards


def _fixed_configuration(args, cards: list[tuple[Path, dict[str, str]]]) -> tuple[str | None, str | None]:
    public_url = str(args.public_base_url or "").strip().rstrip("/")
    fixed_prefix = str(args.fixed_prefix or "").strip()
    if bool(public_url) != bool(fixed_prefix):
        sys.exit("--public-base-url and --fixed-prefix must be provided together")
    if not public_url:
        return None, None
    parsed = urlsplit(public_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        sys.exit("--public-base-url must be an absolute HTTP(S) URL without a query or fragment")
    if parsed.path not in {"", "/"}:
        sys.exit("--public-base-url must not include a path")
    if len(cards) != 1:
        sys.exit("--fixed-prefix requires exactly one credential card")
    resolved = _prefix_for(*cards[0])
    if resolved != fixed_prefix:
        sys.exit(
            f"--fixed-prefix {fixed_prefix!r} does not match credential card prefix {resolved!r}"
        )
    return public_url, fixed_prefix


def _preflight_card(path: Path, creds: dict[str, str]) -> None:
    """Authenticate to Kafka and Schema Registry before the HTTP server starts."""
    from confluent_kafka.schema_registry import SchemaRegistryClient

    from scripts.pitwall.consumer import STANDINGS_TOPIC, _build_consumer

    consumer = None
    try:
        consumer = _build_consumer(creds)
        metadata = consumer.list_topics(STANDINGS_TOPIC, timeout=10)
        topic = metadata.topics.get(STANDINGS_TOPIC)
        if topic is None or topic.error is not None:
            raise RuntimeError("race_standings metadata is unavailable")
        SchemaRegistryClient(
            {
                "url": creds["F1_SCHEMA_REGISTRY_URL"],
                "basic.auth.user.info": f"{creds['F1_SR_API_KEY']}:{creds['F1_SR_API_SECRET']}",
            }
        ).get_subjects()
    except Exception as exc:
        # Do not include the provider exception: some clients embed endpoint or
        # credential configuration in it. The field names and card path are enough.
        raise SystemExit(
            f"Credential preflight failed for {path}: Kafka or Schema Registry rejected the card"
        ) from exc
    finally:
        if consumer is not None:
            consumer.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="F1 social-media race feed (OpenAPI tool for watsonx Orchestrate)")
    parser.add_argument("--creds", help="Path to a single <prefix>.env credential card")
    parser.add_argument("--creds-glob", help="Glob matching many credential cards, e.g. 'runs/*/credentials/*.env'")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Port (default 8080)")
    parser.add_argument("--public-base-url", help="Public HTTP(S) origin written into the Watsonx OpenAPI file")
    parser.add_argument("--fixed-prefix", help="Only expose this card prefix in the no-input Watsonx OpenAPI file")
    parser.add_argument("--mock", action="store_true", help="Offline demo feed — no Kafka / no Confluent env needed")
    args = parser.parse_args()

    store = FeedStore()
    stop = threading.Event()
    threads: list[threading.Thread] = []

    if args.mock:
        if args.fixed_prefix and args.fixed_prefix != MOCK_PREFIX:
            sys.exit(f"Mock mode only serves fixed prefix {MOCK_PREFIX}")
        public_url, fixed_prefix = _fixed_configuration(
            args,
            [(Path("<mock>"), {"F1_PREFIX": MOCK_PREFIX})] if args.fixed_prefix else [],
        )
        feed = store.get_or_create(MOCK_PREFIX)
        feed.mark_consumer_ready()
        threads.append(threading.Thread(target=run_mock, args=(feed, stop), daemon=True))
        logger.info("MOCK feed serving prefix %s", MOCK_PREFIX)
    else:
        from scripts.social_feed.consumer import run_consumer

        cards = _load_cards(args)
        public_url, fixed_prefix = _fixed_configuration(args, cards)
        for path, creds in cards:
            _preflight_card(path, creds)
        for path, creds in cards:
            prefix = _prefix_for(path, creds)
            feed = store.get_or_create(prefix)
            threads.append(threading.Thread(target=run_consumer, args=(creds, feed, stop), daemon=True))
        logger.info("Serving %d feed(s): %s", len(cards), ", ".join(store.prefixes()))

    for t in threads:
        t.start()

    logger.info("Race feed live on http://%s:%d  (OpenAPI at /openapi.json, Ctrl-C to stop)", args.host, args.port)
    if public_url and fixed_prefix:
        logger.info(
            "Watsonx tool download: %s/watsonx/f1-race-feed-openapi.json",
            public_url,
        )
    app = create_app(store, public_base_url=public_url, fixed_prefix=fixed_prefix)
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()


if __name__ == "__main__":
    main()
