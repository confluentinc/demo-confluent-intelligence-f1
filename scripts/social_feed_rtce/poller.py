"""Poll RTCE and feed the shared FeedState — the RTCE analogue of the Kafka consumer.

The Kafka-backed ``f1-social-feed`` tails topics continuously; here we *pull* from
RTCE on an interval and route the same rows into the same ``FeedState`` methods, so
``state.py`` and ``server.py`` (and therefore the OpenAPI tool Orchestrate sees)
are reused unchanged. Re-feeding the standings each tick lets ``FeedState`` derive
the ``headline_events`` (position changes, anomaly onset, new pit calls) exactly as
it does live.

**RTCE cannot query upsert topics, which inverts what you would expect here.**
``race_standings`` is a keyed upsert table, and every ``queryData`` against it
fails with ``MT_UPSERT_NOT_SUPPORTED: Upsert (compacted) topics are not supported
in RTCE`` — verified live 2026-08-03. It is not a permissions or schema problem
and no query shape avoids it: ``listTopics`` still reports the topic ``online``
and ``getMetadata`` still returns its columns, so the failure only appears at
query time. ``race_standings`` therefore cannot be a source here at all, and it
is not polled — a call that can only ever fail is not worth a round trip per
attendee per tick.

That leaves the *append* topics, which are exactly the ones the old comment here
guessed might not work: ``car_telemetry``, and the two an attendee creates,
``car_state`` (LAB 3) and ``pit_decisions`` (LAB 4). ``car_state`` is the useful
one — it is the temporal join's output, so it already carries ``position``,
``gap_to_leader_sec``, ``gap_to_ahead_sec``, ``pit_stops``, ``tire_compound`` and
``tire_age_laps`` for our car alongside the telemetry and the anomaly flag. It is
fed to ``update_standing`` as well as ``update_car_state`` so ``FeedState`` can
still derive position-change headlines.

Consequences to know before demoing: the digest covers **car #88 only** (no rival
gaps, no driver/team names — those live only in ``race_standings``), and it is
**empty until the attendee has run LAB 3**, since ``car_state`` does not exist
before then. Both queries stay best-effort for that reason.
"""

from __future__ import annotations

import asyncio
import logging

from scripts.social_feed.state import OUR_CAR_NUMBER, FeedState
from scripts.social_feed_rtce.rtce_client import RTCEClient

logger = logging.getLogger("f1-social-feed-rtce.poller")

CAR_STATE_TOPIC = "car_state"
PIT_DECISIONS_TOPIC = "pit_decisions"


QUERY_LIMIT = 10


def _newest(rows: list[dict]) -> dict | None:
    """RTCE returns these rows in event-time order; never compare laps across races."""
    return rows[0] if rows else None


async def poll_once(client: RTCEClient, feed: FeedState, seen: dict) -> None:
    """One RTCE refresh of a single attendee's feed."""
    # Append topics — best-effort latest row for our car.
    try:
        latest = _newest(
            await client.query(
                CAR_STATE_TOPIC,
                f'"CAR_NUMBER" = {OUR_CAR_NUMBER}',
                max_rows=QUERY_LIMIT,
                order_by='"EVENT_TIME" DESC',
                limit=QUERY_LIMIT,
            )
        )
        if latest is not None:
            feed.update_car_state(latest)
    except Exception as e:
        logger.debug("[%s] %s query failed (append topic, may be unsupported): %s", feed.prefix, CAR_STATE_TOPIC, e)

    try:
        latest = _newest(
            await client.query(
                PIT_DECISIONS_TOPIC,
                f'"CAR_NUMBER" = {OUR_CAR_NUMBER}',
                max_rows=QUERY_LIMIT,
                order_by='"EVENT_TIME" DESC',
                limit=QUERY_LIMIT,
            )
        )
        # Re-polling the same event must not fill the decisions buffer with
        # duplicates. race_id prevents lap 1 of the next race matching this one.
        decision_id = (
            latest.get("race_id"),
            latest.get("event_time") or latest.get("lap"),
        ) if latest is not None else None
        if latest is not None and decision_id != seen.get("decision_id"):
            feed.add_decision(latest)
            seen["decision_id"] = decision_id
    except Exception as e:
        logger.debug("[%s] %s query failed (append topic, may be unsupported): %s", feed.prefix, PIT_DECISIONS_TOPIC, e)


async def poll_loop(client: RTCEClient, feed: FeedState, interval: float, stop) -> None:
    logger.info("[%s] RTCE poll loop @ %ss → %s", feed.prefix, interval, client.endpoint)
    seen: dict = {"decision_id": None}
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
