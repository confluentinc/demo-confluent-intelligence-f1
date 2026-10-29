"""Identity, environment and resource lookup for the pre-provisioned track.

The deploy mints the developer one Global key; everything else is derived from
it: the user ID that owns the keys the deploy mints, the developer's email, the
one environment the platform team bound them to, and what the platform team built
inside it. The environment's name comes from the platform team's HCL sanitizer,
so the Python twin must agree with it byte for byte. A drift is caught at runtime
only by the pick-list fallback, so the sanitizer examples here must stay identical
to the ones in `platform-team/`'s tests.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

from scripts.preprovisioned import lookup
from scripts.preprovisioned.lookup import Choice, Environment, NoEnvironmentError

# --- sanitize_local_part ------------------------------------------------------

# Keep identical to the examples in the HCL twin's tests (platform-team/).
SANITIZER_EXAMPLES = [
    ("jane.doe+envadmin@example.com", "jane-doe-envadmin"),
    ("John.Doe@acme.com", "John-Doe"),
    ("o'brien@x.com", "o-brien"),
    ("a..b@x", "a-b"),
    ("_x_@y", "x"),
    # 60-character local part: cut to 40, which here ends on a letter.
    ("a" * 40 + "." + "b" * 19 + "@x.com", "a" * 40),
    # 60-character local part whose 40th character is the '-' from '.': the
    # strip runs after the cut, so no trailing '-' survives (39 characters).
    ("a" * 39 + "." + "b" * 20 + "@x.com", "a" * 39),
]


@pytest.mark.parametrize(("email", "expected"), SANITIZER_EXAMPLES, ids=[e for e, _ in SANITIZER_EXAMPLES])
def test_sanitizer_examples(email: str, expected: str) -> None:
    assert lookup.sanitize_local_part(email) == expected


def test_sixty_character_local_parts_are_cut_to_forty_without_a_trailing_dash() -> None:
    for email, _ in SANITIZER_EXAMPLES[-2:]:
        assert len(email.split("@")[0]) == 60
        name = lookup.sanitize_local_part(email)
        assert len(name) <= 40
        assert not name.endswith(("-", "_"))


def test_local_part_with_nothing_usable_raises() -> None:
    with pytest.raises(ValueError, match=r"\+\+\+@x"):
        lookup.sanitize_local_part("+++@x")


# --- choose_environment -------------------------------------------------------

EMAIL = "John.Doe@acme.com"
MINE = Environment("env-mine", "John-Doe-c21e_env")
OTHER = Environment("env-other", "Jane-Roe-0a1b_env")
SHARED = Environment("env-shared", "platform-sandbox")


def test_single_visible_environment_is_used_whatever_its_name() -> None:
    assert choose(SHARED) == Choice(env=SHARED)


def test_several_visible_with_one_name_match_uses_it() -> None:
    assert choose(OTHER, MINE, SHARED) == Choice(env=MINE)


def test_name_match_ignores_case() -> None:
    lowered = Environment("env-lower", "john-doe-C21E_ENV")
    assert choose(OTHER, lowered) == Choice(env=lowered)


def test_several_visible_with_an_ambiguous_match_offers_every_environment() -> None:
    twin = Environment("env-twin", "John-Doe-9f00_env")
    choice = choose(MINE, OTHER, twin)
    assert choice.env is None
    assert set(choice.candidates) == {MINE, OTHER, twin}


def test_several_visible_with_no_match_offers_every_environment() -> None:
    choice = choose(OTHER, SHARED)
    assert choice.env is None
    assert set(choice.candidates) == {OTHER, SHARED}


def test_pick_list_is_sorted_by_name() -> None:
    choice = choose(SHARED, OTHER, Environment("env-a", "alpha"))
    assert [e.name for e in choice.candidates] == ["alpha", "Jane-Roe-0a1b_env", "platform-sandbox"]


@pytest.mark.parametrize(
    "name",
    [
        "John-Doe-c21e_env-old",  # anchored at the end
        "x-John-Doe-c21e_env",  # anchored at the start
        "John-Doe-c21_env",  # exactly four hex digits
        "John-Doe-zzzz_env",  # hex only
        "JohnXDoe-c21e_env",  # the sanitized name is matched literally
    ],
)
def test_near_misses_do_not_match(name: str) -> None:
    near_miss = Environment("env-near", name)
    assert choose(OTHER, near_miss).env is None


def test_unsanitizable_email_falls_back_to_the_pick_list() -> None:
    choice = lookup.choose_environment([MINE, OTHER], "+++@x")
    assert choice.env is None
    assert set(choice.candidates) == {MINE, OTHER}


def test_no_visible_environment_names_the_fix() -> None:
    with pytest.raises(NoEnvironmentError) as excinfo:
        lookup.choose_environment([], EMAIL)
    message = str(excinfo.value)
    assert "direct EnvironmentAdmin binding" in message
    assert "SSO group mapping" in message


def choose(*envs: Environment) -> Choice:
    return lookup.choose_environment(list(envs), EMAIL)


# --- resolve_user_id / visible_environments (mocked HTTP) ----------------------


class FakeResponse:
    def __init__(self, status_code: int = 200, body: dict | None = None):
        self.status_code = status_code
        self._body = body or {}

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _key_response(owner_id: str) -> FakeResponse:
    return FakeResponse(200, {"id": "KEY", "spec": {"owner": {"id": owner_id, "kind": "User"}}})


def test_resolve_user_id_asks_about_the_key_with_the_key_itself() -> None:
    with patch.object(lookup.requests, "get", return_value=_key_response("u-johndoe")) as get:
        assert lookup.resolve_user_id("KEY", "SECRET") == "u-johndoe"
    (url,), kwargs = get.call_args
    assert url == "https://api.confluent.cloud/iam/v2/api-keys/KEY"
    assert kwargs["auth"] == ("KEY", "SECRET")
    assert kwargs["timeout"]


def test_resolve_user_id_rejected_key_says_so() -> None:
    with patch.object(lookup.requests, "get", return_value=FakeResponse(401)):
        with pytest.raises(SystemExit, match="HTTP 401"):
            lookup.resolve_user_id("KEY", "SECRET")


def test_resolve_user_id_refuses_a_key_not_owned_by_a_user() -> None:
    with patch.object(lookup.requests, "get", return_value=_key_response("sa-abc123")):
        with pytest.raises(SystemExit, match="sa-abc123"):
            lookup.resolve_user_id("KEY", "SECRET")


def test_resolve_user_id_other_http_errors_propagate() -> None:
    with patch.object(lookup.requests, "get", return_value=FakeResponse(500)):
        with pytest.raises(requests.HTTPError):
            lookup.resolve_user_id("KEY", "SECRET")


def _env_page(envs: list[tuple[str, str]], next_url: str | None = None) -> FakeResponse:
    return FakeResponse(
        200,
        {
            "data": [{"id": i, "display_name": n} for i, n in envs],
            "metadata": {"next": next_url} if next_url else {},
        },
    )


def test_visible_environments_follows_every_page() -> None:
    page2 = "https://api.confluent.cloud/org/v2/environments?page_size=100&page_token=t2"
    pages = [
        _env_page([("env-1", "one"), ("env-2", "two")], next_url=page2),
        _env_page([("env-3", "three")]),
    ]
    with patch.object(lookup.requests, "get", side_effect=pages) as get:
        envs = lookup.visible_environments("KEY", "SECRET")
    assert envs == [Environment("env-1", "one"), Environment("env-2", "two"), Environment("env-3", "three")]
    urls = [c.args[0] for c in get.call_args_list]
    assert urls == ["https://api.confluent.cloud/org/v2/environments?page_size=100", page2]
    assert all(c.kwargs["auth"] == ("KEY", "SECRET") for c in get.call_args_list)


def test_visible_environments_returns_id_name_tuples() -> None:
    with patch.object(lookup.requests, "get", return_value=_env_page([("env-1", "one")])):
        (env,) = lookup.visible_environments("KEY", "SECRET")
    assert env == ("env-1", "one")
    assert (env.id, env.name) == ("env-1", "one")


def test_visible_environments_empty_org_is_an_empty_list() -> None:
    with patch.object(lookup.requests, "get", return_value=FakeResponse(200, {"data": [], "metadata": {}})):
        assert lookup.visible_environments("KEY", "SECRET") == []


def test_visible_environments_rejected_key_says_so() -> None:
    with patch.object(lookup.requests, "get", return_value=FakeResponse(403)):
        with pytest.raises(SystemExit, match="HTTP 403"):
            lookup.visible_environments("KEY", "SECRET")


# --- user_email (mocked HTTP) --------------------------------------------------


def test_user_email_reads_the_users_own_record() -> None:
    body = {"id": "u-johndoe", "email": "John.Doe@acme.com"}
    with patch.object(lookup.requests, "get", return_value=FakeResponse(200, body)) as get:
        assert lookup.user_email("KEY", "SECRET", "u-johndoe") == "John.Doe@acme.com"
    (url,), kwargs = get.call_args
    assert url == "https://api.confluent.cloud/iam/v2/users/u-johndoe"
    assert kwargs["auth"] == ("KEY", "SECRET")


@pytest.mark.parametrize(
    "outcome",
    [FakeResponse(403), FakeResponse(200, {"id": "u-1"}), requests.ConnectionError("down")],
    ids=["refused", "no email", "network"],
)
def test_user_email_is_empty_when_the_api_wont_say(outcome) -> None:
    kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
    with patch.object(lookup.requests, "get", **kwargs):
        assert lookup.user_email("KEY", "SECRET", "u-1") == ""


# --- find_resources (mocked HTTP) -------------------------------------------------

ORG = "9759ca38-0000-4000-8000-000000000001"
CRN = f"crn://confluent.cloud/organization={ORG}/environment=env-mine"
CLUSTER = {
    "id": "lkc-1",
    "metadata": {"resource_name": f"{CRN}/cloud-cluster=lkc-1"},
    "spec": {
        "display_name": "RIVER-RACING-DEMO-CLUSTER",
        "cloud": "AWS",
        "region": "eu-central-1",
        "kafka_bootstrap_endpoint": "SASL_SSL://pkc-1.eu-central-1.aws.confluent.cloud:9092",
        "http_endpoint": "https://pkc-1.eu-central-1.aws.confluent.cloud:443",
    },
}
DECOY_CLUSTER = {"id": "lkc-0", "spec": {"display_name": "someone-elses-cluster"}}
POOL = {"id": "lfcp-1", "spec": {"display_name": "RIVER-RACING-DEMO-pool"}}
REGISTRY = {"id": "lsrc-1", "spec": {"http_endpoint": "https://psrc-1.eu-central-1.aws.confluent.cloud"}}
FLINK_REGION = {"id": "aws.eu-central-1", "http_endpoint": "https://flink.eu-central-1.aws.confluent.cloud"}


def _routes(clusters=(DECOY_CLUSTER, CLUSTER), pools=(POOL,), registries=(REGISTRY,)):
    data = {
        "/cmk/v2/clusters": clusters,
        "/fcpm/v2/compute-pools": pools,
        "/srcm/v3/clusters": registries,
        "/fcpm/v2/regions": [FLINK_REGION],
    }
    calls = []

    def get(url, auth=None, params=None, timeout=None):
        calls.append((url.removeprefix("https://api.confluent.cloud"), params, auth))
        return FakeResponse(200, {"data": list(data[calls[-1][0]])})

    return get, calls


def test_find_resources_matches_the_platform_teams_names_in_the_environment() -> None:
    get, calls = _routes()
    with patch.object(lookup.requests, "get", side_effect=get):
        found = lookup.find_resources("KEY", "SECRET", MINE)

    assert found == lookup.Resources(
        organization_id=ORG,
        cloud="AWS",
        region="eu-central-1",
        cluster_id="lkc-1",
        cluster_name="RIVER-RACING-DEMO-CLUSTER",
        cluster_bootstrap="SASL_SSL://pkc-1.eu-central-1.aws.confluent.cloud:9092",
        cluster_rest_endpoint="https://pkc-1.eu-central-1.aws.confluent.cloud:443",
        compute_pool_id="lfcp-1",
        schema_registry_id="lsrc-1",
        schema_registry_url="https://psrc-1.eu-central-1.aws.confluent.cloud",
        flink_rest_endpoint="https://flink.eu-central-1.aws.confluent.cloud",
    )
    by_path = {path: params for path, params, _auth in calls}
    for path in ("/cmk/v2/clusters", "/fcpm/v2/compute-pools", "/srcm/v3/clusters"):
        assert by_path[path]["environment"] == MINE.id
    # The Flink endpoint is the cluster's region's.
    assert by_path["/fcpm/v2/regions"] == {"cloud": "AWS", "region_name": "eu-central-1"}
    assert all(auth == ("KEY", "SECRET") for _path, _params, auth in calls)


@pytest.mark.parametrize(
    ("missing", "named"),
    [
        ({"clusters": [DECOY_CLUSTER]}, "Kafka cluster RIVER-RACING-DEMO-CLUSTER"),
        ({"pools": []}, "Flink compute pool RIVER-RACING-DEMO-pool"),
        ({"registries": []}, "Schema Registry"),
    ],
    ids=["cluster", "pool", "schema registry"],
)
def test_find_resources_names_the_environment_and_what_is_missing(missing: dict, named: str) -> None:
    get, _calls = _routes(**missing)
    with patch.object(lookup.requests, "get", side_effect=get):
        with pytest.raises(lookup.NotProvisionedError) as excinfo:
            lookup.find_resources("KEY", "SECRET", MINE)
    message = str(excinfo.value)
    assert f"Your platform team hasn't provisioned {MINE.name} ({MINE.id}) yet" in message
    assert named in message
    assert f"Send them this environment ID, {MINE.id}" in message


def test_find_resources_rejected_key_says_so() -> None:
    with patch.object(lookup.requests, "get", return_value=FakeResponse(401)):
        with pytest.raises(SystemExit, match="HTTP 401"):
            lookup.find_resources("KEY", "SECRET", MINE)
