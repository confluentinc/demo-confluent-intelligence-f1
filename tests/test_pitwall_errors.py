"""Pit Wall feed-error classification.

The failure these guard: the consumer used to send *every* poll error to
`logger.debug` so that a not-yet-created `car_state` topic wouldn't spam the
terminal before LAB 3. With `basicConfig(INFO)` that also silenced
`SASL_AUTHENTICATION_FAILED`, so a stale credential card produced a dashboard
that loaded, rendered empty, reported `live: false`, and never said why.

So there are three properties to hold at once: UNKNOWN_TOPIC stays quiet, a real
failure is said exactly once no matter how many times the 0.5s poll loop sees it,
and the cause reaches the page/`/healthz` where a user will actually look.

These use real `KafkaError` objects (they are constructible) but a fake consumer.
Measured separately against an unresolvable bootstrap host: librdkafka delivers
connection failures *only* to `error_cb`, never as poll() error events (0 in 15s
without a callback, 17 with one), which is why both channels feed one reporter.
An actual `SASL_AUTHENTICATION_FAILED` still needs a live cluster to observe.
"""

from __future__ import annotations

import logging
import threading

import pytest
from confluent_kafka import KafkaError

from scripts.pitwall.consumer import (
    BENIGN_ERROR_CODES,
    ERROR_HINTS,
    ConsumerErrorReporter,
    _build_consumer,
    run_consumer,
)
from scripts.pitwall.state import RaceState

CARD = {
    "F1_KAFKA_BOOTSTRAP": "SASL_SSL://example.invalid:9092",
    "F1_KAFKA_API_KEY": "key",
    "F1_KAFKA_API_SECRET": "secret",
}


@pytest.fixture
def state() -> RaceState:
    return RaceState()


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


# --- suppression: the reason the code exists -------------------------------


@pytest.mark.parametrize("code", sorted(BENIGN_ERROR_CODES))
def test_benign_codes_stay_quiet(state: RaceState, caplog, code: int) -> None:
    """car_state / pit_decisions genuinely don't exist until LAB 3 / LAB 4."""
    caplog.set_level(logging.DEBUG)
    reporter = ConsumerErrorReporter(state)

    assert reporter.kafka_error(KafkaError(code)) is True
    assert _warnings(caplog) == []
    assert state.snapshot()["connection_error"] is None


def test_unknown_topic_visible_when_asked_for(state: RaceState, caplog) -> None:
    """Suppressed is not lost — `f1-pitwall --verbose` sets DEBUG to see these."""
    caplog.set_level(logging.DEBUG)
    ConsumerErrorReporter(state).kafka_error(KafkaError(KafkaError.UNKNOWN_TOPIC_OR_PART))

    assert any("Unknown topic" in r.getMessage() for r in caplog.records)


# --- classification: one warning per distinct cause ------------------------


@pytest.mark.parametrize(
    ("code", "category"),
    [
        (KafkaError._AUTHENTICATION, "authentication"),
        (KafkaError.SASL_AUTHENTICATION_FAILED, "authentication"),
        (KafkaError._RESOLVE, "unreachable"),
        (KafkaError._TRANSPORT, "unreachable"),
        (KafkaError._ALL_BROKERS_DOWN, "unreachable"),
        (KafkaError.TOPIC_AUTHORIZATION_FAILED, "authorization"),
        (KafkaError.GROUP_AUTHORIZATION_FAILED, "authorization"),
        (KafkaError.CLUSTER_AUTHORIZATION_FAILED, "authorization"),
    ],
)
def test_each_failure_category_warns_and_hints(state: RaceState, caplog, code: int, category: str) -> None:
    caplog.set_level(logging.DEBUG)
    err = KafkaError(code)

    assert ConsumerErrorReporter(state).kafka_error(err) is False
    warned = "\n".join(_warnings(caplog))
    assert err.name() in warned, f"{category} failure must name its error code"
    assert ERROR_HINTS[code].splitlines()[0] in warned, f"{category} failure must carry a hint"


def test_auth_warns_once_across_repeated_polls(state: RaceState, caplog) -> None:
    """librdkafka re-emits auth failures on every reconnect; the 0.5s loop sees
    them all. One warning, not a wall of them."""
    caplog.set_level(logging.DEBUG)
    reporter = ConsumerErrorReporter(state)

    for _ in range(50):
        reporter.kafka_error(KafkaError(KafkaError._AUTHENTICATION, "SASL auth failed"))

    assert sum("_AUTHENTICATION" in m for m in _warnings(caplog)) == 1


def test_distinct_codes_each_get_said(state: RaceState, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    reporter = ConsumerErrorReporter(state)

    for _ in range(3):
        reporter.kafka_error(KafkaError(KafkaError._AUTHENTICATION))
        reporter.kafka_error(KafkaError(KafkaError._RESOLVE))

    warned = _warnings(caplog)
    assert sum("_AUTHENTICATION" in m for m in warned) == 1
    assert sum("_RESOLVE" in m for m in warned) == 1


def test_unrecognized_error_is_not_swallowed(state: RaceState, caplog) -> None:
    """A code with no hint still warns — being swallowed twice is the bug."""
    caplog.set_level(logging.DEBUG)

    assert ConsumerErrorReporter(state).kafka_error(KafkaError(KafkaError.UNKNOWN)) is False
    assert _warnings(caplog)


def test_deserialization_failure_warns_once_per_topic(state: RaceState, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    reporter = ConsumerErrorReporter(state)

    for _ in range(10):
        reporter.deserialize_error("car_state", ValueError("bad magic byte"))
        reporter.deserialize_error("race_standings", ValueError("bad magic byte"), field="key")

    warned = _warnings(caplog)
    assert sum("car_state value" in m for m in warned) == 1
    assert sum("race_standings key" in m for m in warned) == 1
    assert "Schema Registry" in state.snapshot()["connection_error"]["detail"]


# --- visibility: the cause reaches the page and /healthz ------------------


def test_error_is_published_to_page_state(state: RaceState) -> None:
    ConsumerErrorReporter(state).kafka_error(KafkaError(KafkaError._AUTHENTICATION, "SASL auth failed"))

    published = state.snapshot()["connection_error"]
    assert published["code"] == "_AUTHENTICATION"
    assert "uv run deploy" in published["detail"], "must name a fix, not just the error"
    assert published["ts"] > 0


def test_error_is_published_every_time_not_just_the_first(state: RaceState) -> None:
    """Warn-once is a logging concern; page state must always reflect reality."""
    reporter = ConsumerErrorReporter(state)
    reporter.kafka_error(KafkaError(KafkaError._AUTHENTICATION))
    state.clear_error()
    reporter.kafka_error(KafkaError(KafkaError._AUTHENTICATION))

    assert state.snapshot()["connection_error"]["code"] == "_AUTHENTICATION"


def test_healthz_reports_the_error(state: RaceState) -> None:
    from fastapi.testclient import TestClient

    from scripts.pitwall.server import create_app

    ConsumerErrorReporter(state).kafka_error(KafkaError(KafkaError._AUTHENTICATION, "SASL auth failed"))
    with TestClient(create_app(state)) as client:
        body = client.get("/healthz").json()

    assert body["live"] is False
    assert body["connection_error"]["code"] == "_AUTHENTICATION"


def test_records_clear_the_error(state: RaceState) -> None:
    """A transient startup blip must not sit on the dashboard all race."""
    reporter = ConsumerErrorReporter(state)
    reporter.kafka_error(KafkaError(KafkaError._TRANSPORT))
    assert state.snapshot()["connection_error"] is not None

    reporter.clear()
    assert state.snapshot()["connection_error"] is None


def test_reporter_without_state_only_logs(caplog) -> None:
    """The reporter is reusable by a consumer with no RaceState to publish to."""
    caplog.set_level(logging.DEBUG)
    ConsumerErrorReporter(label="f1wp002").kafka_error(KafkaError(KafkaError._AUTHENTICATION))

    assert any("[f1wp002]" in m for m in _warnings(caplog))


# --- wiring: error_cb, and the config the social feed shares ---------------


def _captured_conf(monkeypatch, **kwargs) -> dict:
    captured: dict = {}
    monkeypatch.setattr("scripts.pitwall.consumer.Consumer", lambda conf: captured.update(conf))
    _build_consumer(CARD, **kwargs)
    return captured


def test_build_consumer_omits_error_cb_unless_asked(monkeypatch) -> None:
    """scripts/social_feed/consumer.py calls this with creds only and must get the
    configuration it always had — this consumer is shared, so silence is the default."""
    assert "error_cb" not in _captured_conf(monkeypatch)


def test_build_consumer_installs_the_error_cb_it_is_given(monkeypatch) -> None:
    """Without it, connection failures reach no channel at all: log_level 0
    silences librdkafka's log and poll() never sees them."""
    reporter = ConsumerErrorReporter()

    conf = _captured_conf(monkeypatch, error_cb=reporter.kafka_error)
    assert conf["error_cb"] == reporter.kafka_error  # bound methods: equal, never identical


def test_missing_card_field_names_every_way_to_fix_it() -> None:
    with pytest.raises(SystemExit) as excinfo:
        _build_consumer({"F1_KAFKA_BOOTSTRAP": "x"})

    message = str(excinfo.value)
    assert "F1_KAFKA_API_KEY" in message
    for command in ("uv run deploy", "uv run selfservice up", "uv run f1-onboard", "uv run workshop creds"):
        assert command in message, f"{command} must be offered — `workshop creds` alone is organizer-only"


# --- the poll loop wires it up -------------------------------------------


class _FakeError:
    """Stand-in for a poll() error event carrying a real KafkaError."""

    def __init__(self, err: KafkaError) -> None:
        self._err = err

    def error(self) -> KafkaError:
        return self._err


class _FakeConsumer:
    """Serves a fixed list of poll() results, then None forever."""

    def __init__(self, events: list) -> None:
        self.events = list(events)
        self.closed = False
        self.subscribed: list[str] = []

    def subscribe(self, topics, on_assign=None) -> None:
        self.subscribed = list(topics)

    def poll(self, _timeout):
        return self.events.pop(0) if self.events else None

    def close(self) -> None:
        self.closed = True


def test_run_consumer_reports_auth_failure(monkeypatch, state: RaceState, caplog) -> None:
    """End-to-end through the poll loop: a repeated auth error warns once, is
    published, and does not stop the consumer."""
    caplog.set_level(logging.DEBUG)
    stop = threading.Event()
    events = [_FakeError(KafkaError(KafkaError._AUTHENTICATION, "SASL auth failed")) for _ in range(5)]
    events.append(_FakeError(KafkaError(KafkaError.UNKNOWN_TOPIC_OR_PART)))
    fake = _FakeConsumer(events)

    installed: dict = {}

    def build(creds, error_cb=None):
        installed["error_cb"] = error_cb
        return fake

    monkeypatch.setattr("scripts.pitwall.consumer._build_consumer", build)
    monkeypatch.setattr("scripts.pitwall.consumer._build_deserializer", lambda creds: None)

    def poll(timeout):  # stop once the scripted events are exhausted
        event = _FakeConsumer.poll(fake, timeout)
        if event is None:
            stop.set()
        return event

    fake.poll = poll
    run_consumer({}, state, stop)

    assert installed["error_cb"] is not None, "connection failures arrive only via error_cb"
    assert fake.closed, "the consumer must always be closed"
    assert sum("_AUTHENTICATION" in m for m in _warnings(caplog)) == 1
    assert sum("UNKNOWN_TOPIC" in m for m in _warnings(caplog)) == 0
    assert state.snapshot()["connection_error"]["code"] == "_AUTHENTICATION"
    assert state.snapshot()["live"] is False


def test_run_consumer_reports_an_incomplete_card(monkeypatch, state: RaceState, caplog) -> None:
    """`threading.excepthook` discards SystemExit from a daemon thread *silently*,
    so the feed would die on a card missing a key with nothing printed at all."""
    caplog.set_level(logging.DEBUG)

    def explode(creds, error_cb=None):
        raise SystemExit("Credential file is missing 'F1_KAFKA_API_KEY'.")

    monkeypatch.setattr("scripts.pitwall.consumer._build_consumer", explode)
    run_consumer({}, state, threading.Event())

    assert any("F1_KAFKA_API_KEY" in r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)
    assert state.snapshot()["connection_error"]["code"] == "CARD_INCOMPLETE"


def test_run_consumer_reports_a_fatal_poll_error(monkeypatch, state: RaceState, caplog) -> None:
    """A client error raised out of poll() must not vanish into the thread either."""
    caplog.set_level(logging.DEBUG)
    fake = _FakeConsumer([])
    fake.poll = lambda _timeout: (_ for _ in ()).throw(RuntimeError("fatal client error"))

    monkeypatch.setattr("scripts.pitwall.consumer._build_consumer", lambda creds, error_cb=None: fake)
    monkeypatch.setattr("scripts.pitwall.consumer._build_deserializer", lambda creds: None)
    run_consumer({}, state, threading.Event())

    assert fake.closed
    assert state.snapshot()["connection_error"]["code"] == "FEED_STOPPED"
    assert any("fatal client error" in r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)
