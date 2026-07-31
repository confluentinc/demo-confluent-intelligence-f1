"""Seed ``driver_race_history`` with a bounded Flink INSERT (no Postgres/CDC).

The multi-attendee path (``terraform/aws``) feeds ``driver_race_history`` through
a Postgres CDC connector. Self-service has no Postgres, so we render the same 198
historical rows — from the single source of truth in
``data/generate_driver_race_history.py`` (``build_all_rows``) — as one bounded
Flink ``INSERT`` and run it through the environment's own Flink SQL session
(reusing ``FlinkSession`` from the ``f1-sql`` shell).

**Why this file counts rows instead of trusting a marker.** It used to submit the
INSERT, accept ``RUNNING`` as success, and write a ``.seeded`` marker. Two silent
failures came out of that:

- ``FlinkSession.wait`` returns as soon as a statement reaches ``RUNNING`` (right
  for the interactive shell, wrong here), so a bounded INSERT that later *failed*
  still got a marker and was never retried.
- the marker survived ``uv run destroy``, so ``selfservice up`` → ``destroy`` →
  ``selfservice up`` printed "already seeded" over an empty table. LAB 2's
  ``COUNT(*)`` returns 0 and LAB 4's history join returns nothing, with no error
  anywhere.

So: wait for a real terminal phase, verify the row count, and only then write a
marker whose contents are the **environment ID** it was verified against — a new
environment invalidates it automatically. Counting *before* inserting is what
makes a retry safe: without it, a run whose verification was inconclusive would
insert a second time and leave 396 rows, which breaks the same two labs in a new
way.
"""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import requests

from scripts.common.deployment_meta import SELFSERVICE, Track, seed_marker_path
from scripts.common.terraform import get_project_root
from scripts.workshop.sql_shell import FlinkSession

TABLE = "driver_race_history"

# A statement is only finished at one of these. `RUNNING` is deliberately absent.
TERMINAL_PHASES = {"COMPLETED", "FAILED", "STOPPED"}

# A streaming global aggregate emits a changelog, not one final row: 198 inserts
# produce up to ~395 update_before/update_after rows. Read generously and keep the
# largest value seen rather than assuming the last page is the final one.
COUNT_MAX_ROWS = 2000


def _load_rows() -> list[dict]:
    """Import ``build_all_rows`` from data/ (not a package) by file path."""
    path = get_project_root() / "data" / "generate_driver_race_history.py"
    spec = importlib.util.spec_from_file_location("generate_driver_race_history", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build_all_rows()


def expected_rows() -> int:
    """The row count a correct seed produces — from the generator, not a literal."""
    return len(_load_rows())


def _sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _row_literal(r: dict) -> str:
    return (
        f"({_sql_str(r['race_id'])}, {_sql_str(r['gp_name'])}, DATE {_sql_str(r['race_date'])}, "
        f"{r['car_number']}, {_sql_str(r['driver'])}, {_sql_str(r['team'])}, "
        f"{r['starting_grid']}, {r['finishing_pos']}, {r['positions_gained']}, {r['pit_stops']}, "
        f"{_sql_str(r['stint_1_tire'])}, {_sql_str(r['stint_2_tire'])}, {_sql_str(r['stint_3_tire'])})"
    )


def build_insert() -> str:
    values = ",\n".join(_row_literal(r) for r in _load_rows())
    return f"INSERT INTO `{TABLE}` VALUES\n" + values


def _wait_terminal(session: FlinkSession, name: str, timeout: int) -> tuple[str, str]:
    """Poll until the statement reaches a genuinely terminal phase.

    Returns ``(phase, detail)``, with phase ``"TIMEOUT"`` if the deadline passes
    first. Not ``FlinkSession.wait``: that returns at ``RUNNING`` — correct for the
    interactive shell, and the exact reason a failing seed used to look successful.
    """
    url = f"{session.base}/{name}"
    deadline = time.time() + timeout
    phase = "UNKNOWN"
    detail = ""
    while time.time() < deadline:
        try:
            status = requests.get(url, auth=session.auth, timeout=30).json()
        except Exception as e:  # any transport problem is worth reporting, not raising
            return "UNKNOWN", f"could not read statement status: {e}"
        state = status.get("status") or {}
        phase = state.get("phase", "UNKNOWN")
        detail = (state.get("detail") or "").strip()
        if phase in TERMINAL_PHASES:
            return phase, detail
        time.sleep(2)
    return "TIMEOUT", detail


def _as_int(row) -> int | None:
    """First column of a result row as an int, or None if it isn't one."""
    value = row[0] if isinstance(row, (list, tuple)) and row else row
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def count_rows(session: FlinkSession, expected: int, timeout: int = 45) -> tuple[int | None, str]:
    """``(count, problem)`` for ``driver_race_history``. count is None if unknown.

    ``None`` and a number are different answers and callers must treat them
    differently: a number that isn't ``expected`` is a real failure, while ``None``
    means "could not obtain a count" and must not be reported as a failed seed.

    ``driver_race_history`` is created with ``'scan.startup.mode' =
    'earliest-offset'`` (terraform/self-service/main.tf), so this reads the whole
    topic rather than only rows written after the query starts. The table's
    changelog mode is ``append``, so ``COUNT(*)`` is a *streaming* aggregate: it
    emits nothing at all over an empty table, which is how zero is detected.
    """
    try:
        name = session.submit(f"SELECT COUNT(*) AS row_count FROM `{TABLE}`")
    except Exception as e:  # a submit failure is a diagnosis, not a crash
        return None, f"could not submit the row count query ({e})"

    try:
        status = session.wait(name, timeout=60)
        phase = (status.get("status") or {}).get("phase", "UNKNOWN")
        if phase not in ("RUNNING", "COMPLETED"):
            detail = ((status.get("status") or {}).get("detail") or "").strip()
            return None, f"row count query reached {phase} ({detail or 'no detail returned'})"

        started = time.time()
        best: int | None = None
        for row in session.results(name, max_rows=COUNT_MAX_ROWS, timeout=timeout):
            value = _as_int(row)
            if value is None:
                continue
            best = value if best is None else max(best, value)
            if best >= expected:
                break
        elapsed = time.time() - started
    except Exception as e:
        return None, f"row count query failed ({e})"
    finally:
        session.stop(name)

    if best is not None:
        return best, ""

    # No rows at all. For a streaming aggregate that normally means "no input",
    # i.e. an empty table — but only if we actually waited out the window. An
    # early return (a missing pagination link, say) is not evidence of emptiness,
    # and mistaking it for one would insert a second copy of every row.
    if elapsed < timeout / 2:
        return None, f"row count query returned nothing after only {elapsed:.0f}s of a {timeout}s window"
    return 0, ""


def run_insert(session: FlinkSession, timeout: int = 240) -> tuple[bool, str]:
    """Submit the bounded INSERT and wait for it to actually complete."""
    try:
        name = session.submit(build_insert())
    except Exception as e:
        return False, f"could not submit the INSERT ({e})"

    phase, detail = _wait_terminal(session, name, timeout=timeout)
    if phase == "COMPLETED":
        return True, ""
    if phase == "TIMEOUT":
        return False, f"INSERT did not finish within {timeout}s (last detail: {detail or 'none'})"
    return False, f"INSERT reached {phase}: {detail[:500] or 'no detail returned'}"


def _write_marker(root: Path, track: Track, environment_id: str) -> None:
    """Record *which environment* was verified, not merely that something was.

    A bare marker survived ``uv run destroy`` and made the next provision skip
    seeding a brand-new, empty environment. Storing the environment ID makes the
    marker self-invalidating.
    """
    marker = seed_marker_path(root, track)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{environment_id}\n")


def ensure_driver_race_history(
    card: dict[str, str],
    root: Path | None = None,
    track: Track = SELFSERVICE,
    insert_timeout: int = 240,
    count_timeout: int = 45,
) -> bool:
    """Make sure ``driver_race_history`` holds exactly the expected rows.

    Idempotent and safe to re-run: counts first, inserts only into an empty table,
    re-counts afterwards, and writes the marker only once the count matches.
    Returns False for both "the seed failed" and "the seed could not be verified" —
    in either case the marker is not written, so the next run tries again.
    """
    root = root or get_project_root()
    expected = expected_rows()
    env_id = (card.get("F1_ENVIRONMENT_ID") or "").strip()
    marker = seed_marker_path(root, track)

    if marker.exists():
        recorded = marker.read_text().strip()
        if env_id and recorded == env_id:
            print(f"  {TABLE} already seeded and verified for {env_id}.")
            return True
        print(f"  Seed marker is for {recorded or '(unknown)'}, not {env_id or '(unknown)'} — re-verifying.")

    session = FlinkSession(card)

    count, problem = count_rows(session, expected, timeout=count_timeout)
    if count is None:
        print(f"  Could not verify {TABLE}: {problem}")
        # Most often a cold compute pool scheduling the first statement slowly, so
        # the fix is simply to try again. Safe to repeat *because* it counts first:
        # a re-run inserts only into a table it has confirmed to be empty.
        print("  Re-run `uv run selfservice up` — it counts before inserting, so it cannot double-seed.")
        print(f"  Or check by hand: uv run f1-sql  ->  SELECT COUNT(*) FROM `{TABLE}`;")
        return False

    if count == expected:
        print(f"  {TABLE} already holds {count} rows — nothing to seed.")
        _write_marker(root, track, env_id)
        return True

    if count != 0:
        print(f"  {TABLE} holds {count} rows, expected {expected}.")
        print("  Refusing to insert again — that would add a second copy of every row.")
        print("  Tear down and re-provision (`uv run selfservice down && uv run selfservice up`).")
        return False

    print(f"  Seeding {TABLE} ({expected} rows) via a bounded Flink INSERT...")
    ok, problem = run_insert(session, timeout=insert_timeout)
    if not ok:
        print(f"  Seed failed: {problem}")
        return False

    count, problem = count_rows(session, expected, timeout=count_timeout)
    if count is None:
        print(f"  INSERT completed but the row count could not be confirmed: {problem}")
        print("  Re-run `uv run selfservice up` to verify (it will not insert twice).")
        return False
    if count != expected:
        print(f"  INSERT completed but {TABLE} holds {count} rows, expected {expected}.")
        return False

    print(f"  Seeded and verified {count} rows.")
    _write_marker(root, track, env_id)
    return True
