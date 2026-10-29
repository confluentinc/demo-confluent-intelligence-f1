"""The developer's three API keys: minted, waited for, checked and deleted.

The pre-provisioned deploy creates nothing in the developer's environment except
keys, all owned by the developer's own user account:

- a **Global** key, minted by the Confluent CLI as whoever ran `confluent login`.
  An OrganizationAdmin can't mint keys for another user, so this is the one step
  that needs the developer's own session. It is also the card's Flink and RTCE
  key, and it authenticates everything below;
- a **Kafka** and a **Schema Registry** key, minted over REST with the Global key
  (`POST /iam/v2/api-keys`, which answers 202 Accepted).

New keys are refused for a while after they're created — the CLI itself warns it
"may take a couple of minutes" — so each one's first use is retried for up to
`PROPAGATION_TIMEOUT` (ticket 08 saw both REST keys work inside 2.5 minutes).

The REST calls are thin wrappers, like ``lookup``'s; the CLI call is a subprocess.
Both are mocked in tests.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable

import requests

from scripts.preprovisioned.lookup import API, TIMEOUT

# Seconds to keep retrying a new key's first use, and the pause between tries.
PROPAGATION_TIMEOUT = 180
POLL_SECONDS = 5

CLI_INSTALL = "https://docs.confluent.io/confluent-cli/current/install.html"

# Shown in the Console's key list. (`--description` doesn't set the Global key's
# display name, which stays empty; that's harmless.)
DESCRIPTION = "F1 workshop: uv run deploy --track pre-provisioned"

# Global keys cap at 2 per user. The CLI's refusal wording isn't pinned down, so
# match the words any phrasing of a quota error uses.
_CAP_WORDS = re.compile(r"quota|limit|maximum|exceed", re.IGNORECASE)


class GlobalKeyCapError(SystemExit):
    """The developer already holds the 2 Global keys a user may own; the message says how to free one."""


def mint_global_key(previous: str = "") -> tuple[str, str]:
    """Mint a Global key for the logged-in CLI user: ``(key, secret)``.

    ``previous`` is a Global key an earlier deploy recorded, named in the cap
    message as the one to delete if it's still listed. Raises ``GlobalKeyCapError``
    when the user already holds two, and ``SystemExit`` on any other failure.
    """
    cmd = ["confluent", "api-key", "create", "--resource", "global", "--description", DESCRIPTION, "-o", "json"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise SystemExit(f"  The Confluent CLI is not installed: {CLI_INSTALL}") from None
    except subprocess.TimeoutExpired:
        raise SystemExit("  `confluent api-key create --resource global` did not answer within 2 minutes.") from None
    error = (result.stderr or result.stdout or "").strip()
    if result.returncode != 0:
        if _CAP_WORDS.search(error):
            last = f"\n  Your last deploy minted {previous}; if it's still listed, delete that one." if previous else ""
            raise GlobalKeyCapError(
                f"  Confluent Cloud refused a new Global key: {error}\n"
                "  Each account can hold 2 Global keys. Delete one under\n"
                f"  Console → API keys → My account, then re-run.{last}"
            )
        raise SystemExit(f"  `confluent api-key create --resource global` failed: {error or 'no output'}")
    try:
        data = json.loads(result.stdout)
        return data["api_key"], data["api_secret"]
    except (ValueError, KeyError, TypeError):
        raise SystemExit(f"  Unexpected output from `confluent api-key create`: {result.stdout[:200]!r}") from None


def create_api_key(
    auth: tuple[str, str], owner_id: str, resource_id: str, environment_id: str, display_name: str
) -> tuple[str, str]:
    """Mint a key owned by ``owner_id`` for one cluster or Schema Registry: ``(key, secret)``.

    Any 2xx is success (the API answers 202). The secret is in ``spec.secret`` and
    is returned only this once. Raises ``SystemExit`` with the API's own message
    on a refusal (the per-user cap of 10 cluster keys, say), and ``requests``
    exceptions on network failures.
    """
    body = {
        "spec": {
            "display_name": display_name,
            "description": DESCRIPTION,
            "owner": {"id": owner_id},
            "resource": {"id": resource_id, "environment": environment_id},
        }
    }
    resp = requests.post(f"{API}/iam/v2/api-keys", auth=auth, json=body, timeout=TIMEOUT)
    if not 200 <= resp.status_code < 300:
        raise SystemExit(f"  Could not create a key for {resource_id} (HTTP {resp.status_code}): {resp.text[:500]}")
    key = resp.json()
    return key["id"], key["spec"]["secret"]


def key_exists(auth: tuple[str, str], key_id: str) -> bool:
    """Whether ``key_id`` exists, as far as ``auth`` can tell (``GET /iam/v2/api-keys/{id}``).

    False when it's gone (404) and when ``auth`` itself is refused (401/403) — a
    revoked Global key can vouch for nothing. Raises ``requests`` exceptions on
    anything else, so a network blip is never mistaken for a revoked key.
    """
    resp = requests.get(f"{API}/iam/v2/api-keys/{key_id}", auth=auth, timeout=TIMEOUT)
    if resp.status_code in (401, 403, 404):
        return False
    resp.raise_for_status()
    return True


def delete_api_key(auth: tuple[str, str], key_id: str) -> str:
    """Delete ``key_id``: "deleted", "gone" (it didn't exist), or "failed". Never raises.

    A Global key may delete itself, so it can authenticate its own deletion last.
    """
    try:
        resp = requests.delete(f"{API}/iam/v2/api-keys/{key_id}", auth=auth, timeout=TIMEOUT)
    except requests.RequestException:
        return "failed"
    if resp.status_code == 404:
        return "gone"
    return "deleted" if 200 <= resp.status_code < 300 else "failed"


def kafka_key_works(rest_endpoint: str, cluster_id: str, key: str, secret: str) -> bool:
    """Whether the Kafka key can list the cluster's topics yet."""
    url = f"{rest_endpoint.rstrip('/')}/kafka/v3/clusters/{cluster_id}/topics"
    return _ok(url, (key, secret))


def schema_registry_key_works(registry_url: str, key: str, secret: str) -> bool:
    """Whether the Schema Registry key can list subjects yet."""
    return _ok(f"{registry_url.rstrip('/')}/subjects", (key, secret))


def wait_until(check: Callable[[], bool], timeout: float | None = None) -> bool:
    """Call ``check`` until it returns True or ``timeout`` seconds pass. A raise counts as False."""
    deadline = time.monotonic() + (PROPAGATION_TIMEOUT if timeout is None else timeout)
    while True:
        try:
            if check():
                return True
        except requests.RequestException:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_SECONDS)


def _ok(url: str, auth: tuple[str, str]) -> bool:
    try:
        return requests.get(url, auth=auth, timeout=TIMEOUT).status_code == 200
    except requests.RequestException:
        return False
