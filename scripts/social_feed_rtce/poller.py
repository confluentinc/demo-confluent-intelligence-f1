"""Poll RTCE and feed the shared FeedState — the RTCE analogue of the Kafka consumer.

The Kafka-backed ``f1-social-feed`` tails topics continuously; here we *pull* from
RTCE on an interval and route the same rows into the same ``FeedState`` methods, so
``state.py`` and ``server.py`` (and therefore the OpenAPI tool Orchestrate sees)
are reused unchanged. Re-feeding the standings each tick lets ``FeedState`` derive
the ``headline_events`` (position changes, anomaly onset, new pit calls) exactly as
it does live.

``race_standings`` is a keyed upsert table — RTCE's native case, so ``SELECT *``
returns the current row per car. ``car_state`` and ``pit_decisions`` are *append*
topics with no primary key; RTCE may not materialize them the same way, so those
two queries are **best-effort** — if they error or return nothing, the digest still
stands on ``race_standings`` (which itself carries position, gaps, compound, tire
age, and pit count).
"""

from __future__ import annotations

import asyncio
import logging

from scripts.social_feed.state import OUR_CAR_NUMBER, FeedState
from scripts.social_feed_rtce.rtce_client import RTCEClient

logger = logging.getLogger("f1-social-feed-rtce.poller")

STANDINGS_TOPIC = "race_standings"
CAR_STATE_TOPIC = "car_state"
PIT_DECISIONS_TOPIC = "pit_decisions"


def _latest_by_lap(rows: list[dict]) -> dict | None:
    return max(rows, key=lambda r: r.get("lap") or 0) if rows else None


async def poll_once(client: RTCEClient, feed: FeedState, seen: dict) -> None:
    """One RTCE refresh of a single attendee's feed."""
    try:
        for row in await client.query(STANDINGS_TOPIC, "SELECT *"):
            feed.update_standing(row)
    except Exception as e:
        logger.debug("[%s] %s query failed: %s", feed.prefix, STANDINGS_TOPIC, e)

    # Append topics — best-effort latest row for our car.
    try:
        latest = _latest_by_lap(await client.query(CAR_STATE_TOPIC, f"SELECT * WHERE car_number = {OUR_CAR_NUMBER}"))
        if latest is not None:
            feed.update_car_state(latest)
    except Exception as e:
        logger.debug("[%s] %s query failed (append topic, may be unsupported): %s", feed.prefix, CAR_STATE_TOPIC, e)

    try:
        latest = _latest_by_lap(
            await client.query(PIT_DECISIONS_TOPIC, f"SELECT * WHERE car_number = {OUR_CAR_NUMBER}")
        )
        # Only add a decision when it's for a newer lap, so re-polling the same
        # latest row doesn't fill the decisions buffer with duplicates.
        if latest is not None and (latest.get("lap") or 0) != seen.get("decision_lap"):
            feed.add_decision(latest)
            seen["decision_lap"] = latest.get("lap") or 0
    except Exception as e:
        logger.debug("[%s] %s query failed (append topic, may be unsupported): %s", feed.prefix, PIT_DECISIONS_TOPIC, e)


async def poll_loop(client: RTCEClient, feed: FeedState, interval: float, stop) -> None:
    logger.info("[%s] RTCE poll loop @ %ss → %s", feed.prefix, interval, client.endpoint)
    seen: dict = {"decision_lap": None}
    while not stop.is_set():
        await poll_once(client, feed, seen)
        # Interruptible sleep so Ctrl-C stops promptly.
        slept = 0.0
        while slept < interval and not stop.is_set():
            await asyncio.sleep(min(0.2, interval - slept))
            slept += 0.2


async def poll_all(jobs: list[tuple[RTCEClient, FeedState]], interval: float, stop) -> None:
    """Run one poll loop per attendee concurrently until ``stop`` is set."""
    await asyncio.gather(*(poll_loop(client, feed, interval, stop) for client, feed in jobs))
