"""FastAPI app for the social-media race feed.

Exposes one read-only endpoint, ``GET /race-feed/{prefix}``, returning a digest of
the live race for that attendee. The Pydantic response models give FastAPI's
auto-generated OpenAPI 3.0 spec (``GET /openapi.json``) clean field names and
descriptions — that spec is what attendees import as a tool in the watsonx
Orchestrate no-code Agent Builder.

There is no auth beyond the path ``prefix``; this is a workshop convenience
service and the data is non-sensitive race telemetry. Front it with HTTPS when
hosting it where a cloud Orchestrate agent can reach it.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from scripts.social_feed.state import FeedStore


class StandingEntry(BaseModel):
    position: int | None = Field(None, description="Current race position, 1 = leader")
    car_number: int | None = Field(None, description="Car number")
    driver: str | None = Field(None, description="Driver full name")
    team: str | None = Field(None, description="Constructor team name")
    gap_to_leader_sec: float | None = Field(None, description="Time gap to the race leader, in seconds")
    last_lap_time_sec: float | None = Field(None, description="Last completed lap time, in seconds")
    pit_stops: int | None = Field(None, description="Pit stops completed so far")
    tire_compound: str | None = Field(None, description="Current tire compound: SOFT, MEDIUM, or HARD")


class TireStatus(BaseModel):
    compound: str | None = Field(None, description="Current tire compound on our car")
    age_laps: int | None = Field(None, description="Laps run on the current set of tires")
    front_left_temp_c: float | None = Field(None, description="Front-left tire temperature, °C")
    anomaly: bool = Field(False, description="True when the front-left tire temperature is anomalous (overheating)")


class PitDecision(BaseModel):
    lap: int | None = Field(None, description="Lap the recommendation was made on")
    suggestion: str | None = Field(None, description="PIT NOW, PIT SOON, or STAY OUT")
    reasoning: str | None = Field(None, description="The AI strategist's natural-language reasoning")
    recommended_tire_compound: str | None = Field(None, description="Recommended next compound, if pitting")


class RaceFeed(BaseModel):
    prefix: str = Field(description="Attendee prefix this feed belongs to")
    lap: int = Field(description="Current lap number (of 60)")
    driver: str = Field(description="Our driver")
    team: str = Field(description="Our team")
    car_number: int = Field(description="Our car number")
    our_position: int | None = Field(None, description="Our driver's current race position")
    standings: list[StandingEntry] = Field(description="Leaders plus our car")
    tire: TireStatus | None = Field(
        None, description="Our car's tire status (from LAB 3 car_state); null until LAB 3 is built"
    )
    latest_pit_decision: PitDecision | None = Field(
        None, description="Most recent AI pit recommendation (from LAB 4 pit_decisions); null until LAB 4 is built"
    )
    headline_events: list[str] = Field(
        description="Recent notable race moments, newest last — use these to decide what to post about"
    )
    live: bool = Field(description="True if a record arrived recently (the race feed is flowing)")
    updated_at: str = Field(description="When this snapshot was taken (ISO 8601, UTC)")


def create_app(store: FeedStore) -> FastAPI:
    app = FastAPI(
        title="F1 Race Feed",
        version="1.0.0",
        description=(
            "Live race feed for River Racing's social-media agent. "
            "Call GET /race-feed/{prefix} to get the current standings, our driver's "
            "tire status, the latest AI pit recommendation, and recent headline events "
            "to write social posts about."
        ),
    )
    # Orchestrate calls this from the browser/cloud; allow cross-origin reads.
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"]
    )

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "feeds": store.prefixes()}

    @app.get(
        "/race-feed/{prefix}",
        response_model=RaceFeed,
        summary="Get the live race feed for an attendee",
        operation_id="get_race_feed",
    )
    async def race_feed(prefix: str) -> dict:
        feed = store.get(prefix)
        if feed is None:
            raise HTTPException(status_code=404, detail=f"No race feed for prefix '{prefix}'")
        return feed.snapshot()

    return app
