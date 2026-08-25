import json
from pathlib import Path
from urllib.parse import urlparse

SPEC_PATH = Path(__file__).parents[1] / "docs" / "assets" / "orchestrate" / "f1-race-feed-openapi.json"


def load_spec() -> dict:
    return json.loads(SPEC_PATH.read_text())


def test_watsonx_spec_has_supported_version_and_one_public_server():
    spec = load_spec()

    assert spec["openapi"] == "3.0.3"
    assert len(spec["servers"]) == 1

    server = urlparse(spec["servers"][0]["url"])
    assert server.scheme == "https"
    assert server.hostname not in {None, "localhost", "127.0.0.1"}


def test_watsonx_spec_exposes_only_the_race_feed_operation():
    spec = load_spec()

    assert set(spec["paths"]) == {"/race-feed/{prefix}"}
    operation = spec["paths"]["/race-feed/{prefix}"]["get"]
    assert operation["operationId"] == "get_race_feed"
    assert operation["description"]
    assert any(
        parameter["name"] == "prefix"
        and parameter["in"] == "path"
        and parameter["required"] is True
        for parameter in operation["parameters"]
    )


def test_watsonx_spec_contains_no_authentication_material():
    spec_text = SPEC_PATH.read_text().lower()

    forbidden = (
        "api_key",
        "api-secret",
        "api_secret",
        "password",
        "authorization",
        "bearer",
        "sasl",
    )
    assert all(term not in spec_text for term in forbidden)
