"""F1 Pit Wall — live race dashboard for workshop attendees.

  uv run f1-pitwall                       # live, card resolved from credentials.env
  uv run f1-pitwall --creds <prefix>.env  # live, from a specific card
  uv run f1-pitwall --mock                # offline demo / dev (no Kafka)

Starts a local web server, opens a browser, and streams the race — leaderboard,
car #88 telemetry gauges, and (once you build them — Step 1 / Step 3, or Lab 3 /
Lab 4 in the hosted workshop) the anomaly and AI pit-decision panels. Reads topics directly with the Kafka + Schema
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
from pathlib import Path

import uvicorn

from scripts.common import deployment_meta as meta
from scripts.common.credentials import PROJECT_ROOT, load_card
from scripts.pitwall.server import create_app
from scripts.pitwall.state import RaceState

logger = logging.getLogger("f1-pitwall")


def _labels_for_card(path: Path) -> str:
    """'steps' for a self-service / standalone card, 'labs' for a hosted one.

    Only `deploy --track self-service` and `deploy` write cards under runs/<track>/credentials/;
    a hosted attendee's card is the pasted Env File block or an instructor-handed
    file, and the hosted walkthrough numbers its sections as labs.
    """
    resolved = path.resolve()
    for track in (meta.SELFSERVICE, meta.STANDALONE):
        if resolved.is_relative_to((PROJECT_ROOT / "runs" / track.name).resolve()):
            return "steps"
    return "labs"


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
    parser.add_argument(
        "--labels",
        choices=("steps", "labs"),
        help="How the locked panels name their section: 'steps' (self-service/standalone) "
        "or 'labs' (hosted workshop). Default: inferred from the credential card.",
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
        labels = args.labels or "steps"
    else:
        path, creds = load_card(args.creds)
        logger.info("Using credential card: %s", path)
        labels = args.labels or _labels_for_card(path)

        from scripts.pitwall.consumer import run_consumer

        feed = threading.Thread(target=run_consumer, args=(creds, state, stop), daemon=True)

    feed.start()

    url = f"http://localhost:{args.port}"
    logger.info("Pit Wall live at %s  (Ctrl-C to stop)%s", url, "  [MOCK FEED]" if args.mock else "")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    app = create_app(state, labels)
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()


if __name__ == "__main__":
    main()
