"""FastAPI app for the Pit Wall dashboard.

Serves the static frontend and pushes ``RaceState.snapshot()`` over a websocket
at ~4 Hz. Standings only change once per lap (60s live), so the browser animates
car motion between snapshots — pacing one track orbit per lap from the observed
lap cadence — while the server just streams state.

``GET /healthz`` answers the one question an empty dashboard raises: is the feed
flowing, and if not, why. The same ``connection_error`` rides along in every
websocket snapshot, so it is available to the page as well.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from scripts.pitwall.state import RaceState

STATIC_DIR = Path(__file__).parent / "static"
PUSH_INTERVAL_SEC = 0.25
NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}


class NoCacheStaticFiles(StaticFiles):
    """Serve assets with no-store so edits to the JS/CSS always take effect.

    The dashboard is a local, single-user tool; we never want a browser holding
    a stale ``pitwall.js`` across workshop runs.
    """

    def is_not_modified(self, response_headers, request_headers) -> bool:
        return False  # never answer 304; always re-send the current file

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.update(NO_CACHE)
        return response


def create_app(state: RaceState) -> FastAPI:
    app = FastAPI(title="F1 Pit Wall")
    app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE)

    @app.get("/healthz")
    async def healthz() -> dict:
        snap = state.snapshot()
        return {
            "status": "ok",
            "live": snap["live"],
            "race_id": snap["race_id"],
            "lap": snap["lap"],
            "connection_error": snap["connection_error"],
            "reveal": snap["reveal"],
        }

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        try:
            while True:
                # default=str so Avro-decoded datetimes (and other non-JSON
                # types) serialize as ISO strings instead of crashing the socket.
                payload = json.dumps(state.snapshot(), separators=(",", ":"), default=str)
                await socket.send_text(payload)
                await asyncio.sleep(PUSH_INTERVAL_SEC)
        except WebSocketDisconnect:
            pass
        except (RuntimeError, ConnectionError):
            with contextlib.suppress(Exception):
                await socket.close()

    return app
