from fastapi.testclient import TestClient

from scripts.social_feed.server import WATSONX_SPEC_PATH, create_app
from scripts.social_feed.state import FeedStore

PUBLIC_URL = "https://small-underpass-refinery.ngrok-free.dev"
PREFIX = "f1wp050"


def _client() -> TestClient:
    store = FeedStore()
    feed = store.get_or_create(PREFIX)
    feed.mark_consumer_ready()
    return TestClient(create_app(store, public_base_url=PUBLIC_URL, fixed_prefix=PREFIX))


def test_watsonx_download_is_one_fixed_no_input_operation() -> None:
    with _client() as client:
        response = client.get(WATSONX_SPEC_PATH)

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="f1-watsonx-race-feed.json"'
    spec = response.json()
    assert spec["openapi"] == "3.0.3"
    assert spec["servers"] == [{"url": PUBLIC_URL}]
    assert list(spec["paths"]) == [f"/race-feed/{PREFIX}"]
    operation = spec["paths"][f"/race-feed/{PREFIX}"]["get"]
    assert operation["operationId"] == "get_race_feed"
    assert "parameters" not in operation
    assert "/healthz" not in spec["paths"]
    text = response.text.lower()
    assert "shared race feed" in text
    assert "live is false" in text
    assert '"type":"null"' not in response.text


def test_dynamic_internal_route_remains_available() -> None:
    with _client() as client:
        response = client.get(f"/race-feed/{PREFIX}")
    assert response.status_code == 200
    assert response.json()["prefix"] == PREFIX


def test_health_reports_consumer_errors_instead_of_false_ok() -> None:
    store = FeedStore()
    feed = store.get_or_create(PREFIX)
    feed.record_error("AUTH", "Kafka rejected the card")
    with TestClient(create_app(store)) as client:
        response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
