"""F1 Pit Wall — live race dashboard for workshop attendees.

  uv run f1-pitwall                       # live, card resolved from credentials.env
  uv run f1-pitwall --creds <prefix>.env  # live, from a specific card
  uv run f1-pitwall --mock                # offline demo / dev (no Kafka)

Starts a local web server, opens a browser, and streams the race — leaderboard,
car #88 telemetry gauges, and (once you build them in LAB 3 / LAB 4) the anomaly
and AI pit-decision panels. Reads topics directly with the Kafka + Schema
Registry keys on your credential card; it only consumes, so it never touches your
Flink compute pool. Stop with Ctrl-C.

If the board stays empty, the reason is printed here and served at
``/healthz``. ``--verbose`` additionally shows the errors the consumer suppresses
on purpose (a missing ``car_state`` before LAB 3 is normal, not a fault).
"""

from __future__ import annotations

import argparse
import logging
import threading
import webbrowser

import uvicorn

from scripts.common.credentials import load_card
from scripts.pitwall.server import create_app
from scripts.pitwall.state import RaceState

logger = logging.getLogger("f1-pitwall")


def main() -> None:
    parser = argparse.ArgumentParser(description="F1 Pit Wall live dashboard (API-key access, no login)")
    parser.add_argument(
        "--creds",
        help="Path to your <prefix>.env credential card (default: read from credentials.env)",
    )
    parser.add_argument("--port", type=int, default=8000, help="Local port (default 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open a browser")
    parser.add_argument("--mock", action="store_true", help="Offline demo feed — no Kafka / no Confluent env needed")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Also log the errors the consumer suppresses (e.g. car_state missing before LAB 3)",
    )
    args = parser.parse_args()

    # Configure logging from the parsed args, not at import time: the consumer's
    # deliberate suppressions log at DEBUG, so INFO makes them unreachable — which
    # is exactly how auth failures used to go unnoticed.
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    state = RaceState()
    stop = threading.Event()

    if args.mock:
        from scripts.pitwall.mock import run_mock

        feed = threading.Thread(target=run_mock, args=(state, stop), daemon=True)
    else:
        path, creds = load_card(args.creds)
        logger.info("Using credential card: %s", path)

        from scripts.pitwall.consumer import run_consumer

        feed = threading.Thread(target=run_consumer, args=(creds, state, stop), daemon=True)

    feed.start()

    url = f"http://localhost:{args.port}"
    logger.info("Pit Wall live at %s  (Ctrl-C to stop)%s", url, "  [MOCK FEED]" if args.mock else "")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    app = create_app(state)
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()


if __name__ == "__main__":
    main()
