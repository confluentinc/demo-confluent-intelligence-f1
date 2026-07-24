"""F1 Pit Wall — live race dashboard for workshop attendees.

  uv run f1-pitwall --creds <prefix>.env          # live, from your own topics
  uv run f1-pitwall --creds <prefix>.env --mock    # offline demo / dev (no Kafka)

Starts a local web server, opens a browser, and streams the race — leaderboard,
car #88 telemetry gauges, and (once you build them in LAB 3 / LAB 4) the anomaly
and AI pit-decision panels. Reads topics directly with the Kafka + Schema
Registry keys on your credential card; it only consumes, so it never touches your
Flink compute pool. Stop with Ctrl-C.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn
from dotenv import dotenv_values

from scripts.pitwall.server import create_app
from scripts.pitwall.state import RaceState

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("f1-pitwall")


def main() -> None:
    parser = argparse.ArgumentParser(description="F1 Pit Wall live dashboard (API-key access, no login)")
    parser.add_argument("--creds", help="Path to your <prefix>.env credential card (required unless --mock)")
    parser.add_argument("--port", type=int, default=8000, help="Local port (default 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open a browser")
    parser.add_argument("--mock", action="store_true", help="Offline demo feed — no Kafka / no Confluent env needed")
    args = parser.parse_args()

    state = RaceState()
    stop = threading.Event()

    if args.mock:
        from scripts.pitwall.mock import run_mock

        feed = threading.Thread(target=run_mock, args=(state, stop), daemon=True)
    else:
        if not args.creds:
            sys.exit(
                "--creds is required (or use --mock). "
                "Example: uv run f1-pitwall --creds runs/<name>/credentials/f1wp001.env"
            )
        path = Path(args.creds)
        if not path.exists():
            sys.exit(f"Credential file not found: {path}")
        creds = dotenv_values(path)

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
