"""Resolve who the developer is, which environment is theirs, and what is in it.

The pre-provisioned track starts from nothing but the Global key the deploy mints
for the developer. Everything else is derived from it:

- the **user ID** (``u-…``) that owns the Kafka/SR keys the deploy mints — read
  from the key's own metadata — and the developer's **email**, from their user;
- the **environment** the platform team created for them. The key sees only
  environments its owner is *directly* bound to (an SSO group mapping doesn't
  carry over to user API keys), so usually exactly one is visible. Instructors and
  developers with several bindings are narrowed by name: the platform module names
  each environment ``<sanitized local part>-<4 hex>_env``, so the same sanitizer
  applied to the developer's email picks theirs out;
- the **resources** the platform team built inside it: the cluster and Flink pool
  by their fixed names, the environment's Schema Registry, and the Flink endpoint
  of the cluster's region. Nothing here creates anything.

``choose_environment`` is pure so the decision is testable without HTTP; the
fetchers are thin wrappers over the Confluent Cloud REST API.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple

import requests

API = "https://api.confluent.cloud"
TIMEOUT = 30

# Confluent environment names allow only [A-Za-z0-9_-] and must start and end
# alphanumeric; the platform module leaves room for the "-<4 hex>_env" suffix.
MAX_LOCAL_PART = 40

# What platform-team/modules/developer names the two resources it builds per
# developer. The Schema Registry needs no name: an environment has one.
CLUSTER_NAME = "RIVER-RACING-DEMO-CLUSTER"
POOL_NAME = "RIVER-RACING-DEMO-pool"

# Every resource's metadata.resource_name is a CRN that starts with its org:
# crn://confluent.cloud/organization=<id>/environment=env-…/…
_ORG_IN_CRN = re.compile(r"/organization=([^/]+)")


class Environment(NamedTuple):
    """One environment the key can see: an ``(id, name)`` tuple with named fields."""

    id: str
    name: str


@dataclass(frozen=True)
class Choice:
    """What ``choose_environment`` decided. Exactly one field is set.

    - ``env``: the environment to use, decided without asking (it was the only one
      visible, or the only one named after the developer). Show its name and ID
      for confirmation.
    - ``candidates``: every visible environment, sorted by name, when the
      developer has to pick one.
    """

    env: Environment | None = None
    candidates: tuple[Environment, ...] = ()


class NoEnvironmentError(SystemExit):
    """The key sees no environment at all; the message names the fix.

    A ``SystemExit`` so that, left uncaught, it exits with that message like the
    repo's other fatal preflight checks; catch it by type to handle it yourself.
    """


NO_ENVIRONMENT_MESSAGE = (
    "Your Global key sees no environment you're directly bound to.\n"
    "Ask your platform team for a direct EnvironmentAdmin binding on your environment\n"
    "(User:<your ID>, not via an SSO group mapping — user API keys don't carry\n"
    "group-mapped permissions)."
)


class NotProvisionedError(SystemExit):
    """The environment lacks something the platform team builds; the message names it."""


@dataclass(frozen=True)
class Resources:
    """What the platform team built in the developer's environment, as the card needs it."""

    organization_id: str
    cloud: str
    region: str
    cluster_id: str
    cluster_name: str
    cluster_bootstrap: str  # SASL_SSL://pkc-…:9092
    cluster_rest_endpoint: str  # https://pkc-…:443, where a Kafka key lists topics
    compute_pool_id: str
    schema_registry_id: str
    schema_registry_url: str
    flink_rest_endpoint: str


def sanitize_local_part(email: str) -> str:
    """Turn an email's local part into the platform module's environment-name stem.

    ``John.Doe@acme.com`` → ``John-Doe``. Raises ``ValueError`` when nothing usable
    is left (``+++@x``).

    This rule has an HCL twin in ``platform-team/main.tf``, which names the
    environments. A drift between them is caught at runtime only by the pick-list
    fallback, so the two must apply the same steps in the same order, and both
    example lists (``tests/test_preprovisioned_lookup.py`` and ``platform-team/``'s
    tests) must stay identical.
    """
    local = email.split("@", 1)[0]
    name = re.sub(r"[^A-Za-z0-9_-]", "-", local)
    name = re.sub(r"-+", "-", name)
    # Cut first, strip second: a cut that lands on '-' must not leave it trailing.
    name = name[:MAX_LOCAL_PART].strip("-_")
    if not name:
        raise ValueError(f"{email!r} has no letters or digits before '@' to name an environment after")
    return name


def choose_environment(envs: list[Environment], email: str) -> Choice:
    """Pick the developer's environment from the ones their key can see. No I/O.

    One visible → that one. Several → the single one named
    ``<sanitized local part>-<4 hex>_env`` (case-insensitive), else a pick-list
    of all of them. None → ``NoEnvironmentError``.
    """
    if not envs:
        raise NoEnvironmentError(NO_ENVIRONMENT_MESSAGE)
    if len(envs) == 1:
        return Choice(env=envs[0])
    try:
        stem = sanitize_local_part(email)
    except ValueError:
        # The platform module can't have named an environment after this email.
        stem = None
    if stem is not None:
        pattern = re.compile(rf"^{re.escape(stem)}-[0-9a-f]{{4}}_env$", re.IGNORECASE)
        matches = [e for e in envs if pattern.match(e.name)]
        if len(matches) == 1:
            return Choice(env=matches[0])
    return Choice(candidates=tuple(sorted(envs, key=lambda e: e.name.lower())))


def resolve_user_id(key: str, secret: str) -> str:
    """Return the ``u-…`` ID that owns ``key``, asking the API about the key with itself.

    Raises ``SystemExit`` when the key is rejected or isn't owned by a user, and
    ``requests`` exceptions on other HTTP or network failures.
    """
    resp = requests.get(f"{API}/iam/v2/api-keys/{key}", auth=(key, secret), timeout=TIMEOUT)
    _refuse_rejected_key(resp)
    resp.raise_for_status()
    owner = ((resp.json().get("spec") or {}).get("owner") or {}).get("id") or ""
    if not owner.startswith("u-"):
        # A service-account key would make the deploy mint "User"-owned keys for a
        # non-user and fail halfway; say so now.
        raise SystemExit(
            f"  API key {key} is owned by {owner or 'an unknown principal'}, not a user account.\n"
            "  Log the Confluent CLI in as yourself (`confluent login`) and re-run."
        )
    return owner


def user_email(key: str, secret: str, user_id: str) -> str:
    """The email of ``user_id`` (``GET /iam/v2/users/{id}``), or "" if the API won't say.

    Never raises: the caller falls back to asking, so a refusal or a network
    failure here only costs a prompt.
    """
    try:
        resp = requests.get(f"{API}/iam/v2/users/{user_id}", auth=(key, secret), timeout=TIMEOUT)
        if resp.status_code != 200:
            return ""
        return str(resp.json().get("email") or "").strip()
    except (requests.RequestException, ValueError):
        return ""


def visible_environments(key: str, secret: str) -> list[Environment]:
    """Return every environment ``key`` can see, following ``metadata.next`` pages.

    Raises ``SystemExit`` when the key is rejected, and ``requests`` exceptions on
    other HTTP or network failures.
    """
    envs: list[Environment] = []
    url = f"{API}/org/v2/environments?page_size=100"
    while url:
        resp = requests.get(url, auth=(key, secret), timeout=TIMEOUT)
        _refuse_rejected_key(resp)
        resp.raise_for_status()
        body = resp.json()
        for env in body.get("data") or []:
            env_id, name = env.get("id"), env.get("display_name")
            if env_id and name:
                envs.append(Environment(env_id, name))
        url = (body.get("metadata") or {}).get("next") or ""
    return envs


def find_resources(key: str, secret: str, env: Environment) -> Resources:
    """Find what the platform team built in ``env``: cluster, pool, Schema Registry, Flink endpoint.

    The cluster and pool are matched by the platform module's fixed names; the
    Schema Registry is the environment's only one; the Flink endpoint is the
    cluster's region's. Raises ``NotProvisionedError`` naming the environment and
    everything missing, ``SystemExit`` when the key is rejected, and ``requests``
    exceptions on other HTTP or network failures.
    """
    query = {"environment": env.id, "page_size": 100}
    clusters = _get_list(key, secret, "/cmk/v2/clusters", query)
    pools = _get_list(key, secret, "/fcpm/v2/compute-pools", query)
    registries = _get_list(key, secret, "/srcm/v3/clusters", {"environment": env.id})

    cluster = next((c for c in clusters if _spec(c).get("display_name") == CLUSTER_NAME), None)
    pool = next((p for p in pools if _spec(p).get("display_name") == POOL_NAME), None)
    registry = registries[0] if registries else None
    missing = [
        what
        for what, found in (
            (f"Kafka cluster {CLUSTER_NAME}", cluster),
            (f"Flink compute pool {POOL_NAME}", pool),
            ("Schema Registry", registry),
        )
        if found is None
    ]
    if missing:
        raise NotProvisionedError(
            f"Your platform team hasn't provisioned {env.name} ({env.id}) yet: it has no "
            f"{', no '.join(missing)}.\n"
            f"Send them this environment ID, {env.id}, and re-run once they have."
        )

    spec = _spec(cluster)
    cloud, region = str(spec.get("cloud") or ""), str(spec.get("region") or "")
    org = next(
        (m.group(1) for item in (cluster, pool, registry) if (m := _ORG_IN_CRN.search(_crn(item)))),
        "",
    )
    if not org:
        raise SystemExit(f"  Could not tell which organization {env.name} ({env.id}) belongs to.")
    return Resources(
        organization_id=org,
        cloud=cloud,
        region=region,
        cluster_id=cluster["id"],
        cluster_name=CLUSTER_NAME,
        cluster_bootstrap=str(spec.get("kafka_bootstrap_endpoint") or ""),
        cluster_rest_endpoint=str(spec.get("http_endpoint") or ""),
        compute_pool_id=pool["id"],
        schema_registry_id=registry["id"],
        schema_registry_url=str(_spec(registry).get("http_endpoint") or ""),
        flink_rest_endpoint=flink_rest_endpoint(key, secret, cloud, region),
    )


def flink_rest_endpoint(key: str, secret: str, cloud: str, region: str) -> str:
    """The public Flink REST endpoint of ``cloud``/``region`` (``fcpm/v2/regions``)."""
    regions = _get_list(key, secret, "/fcpm/v2/regions", {"cloud": cloud, "region_name": region})
    endpoint = next((str(r["http_endpoint"]) for r in regions if r.get("http_endpoint")), "")
    if not endpoint:
        raise SystemExit(f"  Confluent Cloud lists no Flink endpoint for {cloud} {region}.")
    return endpoint


def _get_list(key: str, secret: str, path: str, params: dict) -> list[dict]:
    """The ``data`` of one list call (the platform team builds one of each, so one page)."""
    resp = requests.get(f"{API}{path}", auth=(key, secret), params=params, timeout=TIMEOUT)
    _refuse_rejected_key(resp)
    resp.raise_for_status()
    return list(resp.json().get("data") or [])


def _spec(item: dict) -> dict:
    return item.get("spec") or {}


def _crn(item: dict) -> str:
    return str((item.get("metadata") or {}).get("resource_name") or "")


def _refuse_rejected_key(resp: requests.Response) -> None:
    if resp.status_code in (401, 403):
        raise SystemExit(
            f"  Confluent Cloud rejected the API key (HTTP {resp.status_code}).\n"
            "  The deploy mints your Global key with the Confluent CLI; re-run it, and if this\n"
            "  persists, check the key still exists under Console → API keys → My account."
        )
