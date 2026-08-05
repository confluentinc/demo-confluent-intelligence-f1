"""F1 social-media race feed, backed by the Real-Time Context Engine (RTCE).

Same OpenAPI tool as ``f1-social-feed`` (it reuses that service's FeedState and
FastAPI app, so ``/race-feed/{prefix}`` and ``/openapi.json`` are identical and
Orchestrate imports it the same way) — but instead of consuming Kafka directly it
acts as an **MCP client to RTCE**, polling each attendee's materialized topics.

  # Validate the RTCE contract against a real endpoint first (recommended)
  RTCE_API_KEY=... RTCE_API_SECRET=... \
    uv run f1-social-feed-rtce --probe --creds runs/<name>/credentials/f1wp001.env

  # Serve every attendee from their cards (endpoints read from F1_RTCE_MCP_ENDPOINT)
  RTCE_API_KEY=... RTCE_API_SECRET=... \
    uv run f1-social-feed-rtce --creds-glob 'runs/*/credentials/*.env'

  # Single endpoint smoke test (no cards)
  uv run f1-social-feed-rtce --endpoint <RTCE_MCP_URL> --prefix f1wp001 \
    --api-key ... --api-secret ...

Auth uses a **Global** Confluent Cloud API key (shared across the org), passed
once to this process (flags or RTCE_API_KEY / RTCE_API_SECRET) — it is a secret,
so it is NOT read from credential cards. The per-attendee RTCE endpoint URL is
non-secret and comes from each card's F1_RTCE_MCP_ENDPOINT (or is constructed from
F1_REGION / F1_ORGANIZATION_ID / F1_ENVIRONMENT_ID / F1_CLUSTER_ID).

Host it where the Orchestrate agent can reach it (public HTTPS). Stop with Ctrl-C.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import logging
import os
import sys
import threading
from builtins import BaseExceptionGroup
from pathlib import Path

import uvicorn
from dotenv import dotenv_values

from scripts.social_feed.server import create_app
from scripts.social_feed.state import FeedState, FeedStore
from scripts.social_feed_rtce.poller import poll_all
from scripts.social_feed_rtce.rtce_client import RTCEClient, basic_token, build_endpoint

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("f1-social-feed-rtce")

DEFAULT_REGION = "us-east-1"


def _endpoint_from_card(creds: dict[str, str]) -> str:
    """Per-attendee RTCE endpoint: explicit card field, else construct from parts."""
    explicit = creds.get("F1_RTCE_MCP_ENDPOINT")
    if explicit:
        return explicit.rstrip("/")
    org, env, lkc = creds.get("F1_ORGANIZATION_ID"), creds.get("F1_ENVIRONMENT_ID"), creds.get("F1_CLUSTER_ID")
    if org and env and lkc:
        return build_endpoint(creds.get("F1_REGION") or DEFAULT_REGION, org, env, lkc)
    return ""


def _resolve_token(args) -> str:
    key = args.api_key or os.environ.get("RTCE_API_KEY")
    secret = args.api_secret or os.environ.get("RTCE_API_SECRET")
    if not key or not secret:
        sys.exit(
            "RTCE needs a Global API key — pass --api-key/--api-secret or set "
            "RTCE_API_KEY / RTCE_API_SECRET in the environment."
        )
    return basic_token(key, secret)


def _load_jobs(args, store: FeedStore, token: str) -> list[tuple[RTCEClient, FeedState]]:
    """Build (RTCEClient, FeedState) pairs from --endpoint or credential cards."""
    jobs = []
    if args.endpoint:
        prefix = args.prefix or "f1wp001"
        feed = store.get_or_create(prefix)
        jobs.append((RTCEClient(args.endpoint.rstrip("/"), token), feed))
        return jobs

    paths = [Path(p) for p in glob.glob(args.creds_glob)] if args.creds_glob else []
    if args.creds:
        paths.append(Path(args.creds))
    if not paths:
        sys.exit(
            "Provide --endpoint <url>, or --creds/--creds-glob pointing at cards that carry "
            "F1_RTCE_MCP_ENDPOINT. Example: uv run f1-social-feed-rtce --creds-glob 'runs/*/credentials/*.env'"
        )
    for path in sorted(set(paths)):
        if not path.exists():
            sys.exit(f"Credential file not found: {path}")
        creds = dict(dotenv_values(path))
        prefix = creds.get("F1_PREFIX") or path.stem
        endpoint = _endpoint_from_card(creds)
        if not endpoint:
            logger.warning("[%s] no RTCE endpoint on card (F1_RTCE_MCP_ENDPOINT) — skipping", prefix)
            continue
        jobs.append((RTCEClient(endpoint, token), store.get_or_create(prefix)))
    if not jobs:
        sys.exit("No RTCE endpoints found on the provided cards.")
    return jobs


def _root_causes(exc: BaseException) -> list[str]:
    """Flatten nested ExceptionGroups to their leaf messages.

    The ``mcp`` SDK runs each session inside two nested ``anyio`` task groups, so
    every real error — a wrong tool name, a rejected key — reaches us as
    ``ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)``.
    Printing ``str(exc)`` therefore prints that sentence and nothing else, which
    is how a plain ``unknown tool "queryData"`` stayed invisible through a whole
    debugging session. Always report the leaves.
    """
    if isinstance(exc, BaseExceptionGroup):
        return [msg for sub in exc.exceptions for msg in _root_causes(sub)]
    return [f"{type(exc).__name__}: {exc}"]


async def _probe(client: RTCEClient) -> None:
    """Connect to one endpoint, list tools/topics, and dump a sample query."""
    print(f"Probing RTCE endpoint: {client.endpoint}")
    try:
        topics = await client.list_topics()
        print("\nlistTopics →")
        for block in getattr(topics, "content", None) or []:
            print(" ", getattr(block, "text", block))
        rows = await client.query("race_standings")
        print(f"\nqueryData race_standings → {len(rows)} row(s)")
        print(json.dumps(rows[:3], indent=2, default=str))
    except Exception as e:
        causes = "\n".join(f"  - {c}" for c in _root_causes(e))
        sys.exit(
            f"\nProbe failed:\n{causes}\n"
            "Check the endpoint URL, the Global API key, and that RTCE is enabled on the topic.\n"
            'An `unknown tool "..."` here means the tool names in rtce_client.py drifted — '
            "list them with session.list_tools() against the live endpoint."
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="F1 social-media race feed backed by Confluent RTCE (OpenAPI tool for Orchestrate)"
    )
    parser.add_argument("--creds", help="Path to a single <prefix>.env credential card")
    parser.add_argument("--creds-glob", help="Glob matching many cards, e.g. 'runs/*/credentials/*.env'")
    parser.add_argument("--endpoint", help="A single RTCE MCP endpoint URL (smoke test; bypasses cards)")
    parser.add_argument("--prefix", help="Prefix to serve when using --endpoint (default f1wp001)")
    parser.add_argument("--api-key", help="Global Confluent Cloud API key (or env RTCE_API_KEY)")
    parser.add_argument("--api-secret", help="Global Confluent Cloud API secret (or env RTCE_API_SECRET)")
    parser.add_argument("--interval", type=float, default=10.0, help="Seconds between RTCE polls (default 10)")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Port (default 8080)")
    parser.add_argument("--probe", action="store_true", help="Validate the RTCE contract, then exit")
    args = parser.parse_args()

    token = _resolve_token(args)
    store = FeedStore()
    jobs = _load_jobs(args, store, token)

    if args.probe:
        asyncio.run(_probe(jobs[0][0]))
        return

    stop = threading.Event()

    def _run_pollers() -> None:
        try:
            asyncio.run(poll_all(jobs, args.interval, stop))
        except RuntimeError:
            pass  # loop torn down on shutdown

    threading.Thread(target=_run_pollers, daemon=True).start()
    logger.info("Serving %d RTCE-backed feed(s): %s", len(jobs), ", ".join(store.prefixes()))
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
