"""Guard the RTCE `description` length cap.

The Confluent API rejects a `confluent_rtce_topic` description over 256
characters with `400 Bad Request: description must be at most 256 characters`.
Nothing catches that at plan time: it surfaces mid-apply, after the
environment, cluster, Flink pool and topics already exist, and wsa spends every
retry re-hitting it. A build that dies there leaves half-built attendee
environments behind that then have to be cleaned before anything can be rebuilt.

That happened on the first two smoke builds, which is why this test exists at
all. It is deliberately a *static* check on the Terraform source — it needs no
credentials and no cloud, so it runs in the same `uv run pytest` that gates
every other change and costs nothing.

The assertion is against 230, not the real 256: the descriptions are prompt
text an agent reads to pick a topic, so they get edited for wording far more
often than for length, and a limit with no headroom is one adjective away from
another failed build.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# The real API limit. Exceeding it is a 400 at apply time.
API_LIMIT = 256

# What we hold ourselves to, leaving room to reword without re-counting.
SOFT_LIMIT = 230

TOPICS_TF = Path(__file__).resolve().parents[1] / "terraform" / "modules" / "topics" / "main.tf"

_RESOURCE_RE = re.compile(
    r'resource\s+"confluent_rtce_topic"\s+"(?P<name>\w+)"\s*\{(?P<body>.*?)\n\}',
    re.S,
)
_DESCRIPTION_RE = re.compile(r'description\s*=\s*"(?P<value>(?:[^"\\]|\\.)*)"')


def _descriptions() -> dict[str, str]:
    """Every `confluent_rtce_topic` description in the topics module, unescaped.

    Parsed rather than imported because this is HCL, and the value that matters
    is the *rendered* string the provider sends — so `\\"` counts as one
    character, not two.
    """
    source = TOPICS_TF.read_text()
    found: dict[str, str] = {}
    for match in _RESOURCE_RE.finditer(source):
        body = match.group("body")
        description = _DESCRIPTION_RE.search(body)
        assert description is not None, (
            f"confluent_rtce_topic.{match.group('name')} has no literal description. "
            "It is a required field; if it became an expression, this guard can no "
            "longer measure it and needs updating."
        )
        found[match.group("name")] = description.group("value").replace('\\"', '"')
    return found


def test_finds_the_rtce_topics() -> None:
    """Fail loudly if the resources move or get renamed.

    Without this, a refactor that renamed the resources would make every check
    below vacuously pass over an empty dict.
    """
    names = set(_descriptions())
    assert names == {"car_telemetry"}, (
        f"expected the queryable source-topic RTCE resource, found {sorted(names)}. "
        "If a topic was added or renamed, update this test — do not delete it."
    )


@pytest.mark.parametrize("name", ["car_telemetry"])
def test_description_within_api_limit(name: str) -> None:
    value = _descriptions()[name]
    assert len(value) <= API_LIMIT, (
        f"{name}: {len(value)} chars exceeds the hard API limit of {API_LIMIT}. "
        "Terraform will accept this and the apply will fail with "
        "'400 Bad Request: description must be at most 256 characters' "
        "*after* creating the environment and cluster."
    )


@pytest.mark.parametrize("name", ["car_telemetry"])
def test_description_within_soft_limit(name: str) -> None:
    value = _descriptions()[name]
    assert len(value) <= SOFT_LIMIT, (
        f"{name}: {len(value)} chars is under the {API_LIMIT}-char API limit but over "
        f"the {SOFT_LIMIT}-char margin this repo keeps. Tighten the wording rather "
        "than raising the limit — the next edit should not have to count characters."
    )


@pytest.mark.parametrize("name", ["car_telemetry"])
def test_description_is_useful_prompt_text(name: str) -> None:
    """The description is what an agent reads to choose a topic, so it can't be empty
    or a placeholder. This is a floor, not a quality bar."""
    value = _descriptions()[name]
    assert len(value) >= 40, f"{name}: {len(value)} chars is too thin to guide topic selection"
