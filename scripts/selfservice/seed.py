"""Seed ``driver_race_history`` with a bounded Flink INSERT (no Postgres/CDC).

The multi-attendee path (``terraform/aws``) feeds ``driver_race_history`` through
a Postgres CDC connector. Self-service has no Postgres, so we render the same 198
historical rows — from the single source of truth in
``data/generate_driver_race_history.py`` (``build_all_rows``) — as one bounded
Flink ``INSERT`` and run it through the environment's own Flink SQL session
(reusing ``FlinkSession`` from the ``f1-sql`` shell).
"""

from __future__ import annotations

import importlib.util

from scripts.common.terraform import get_project_root
from scripts.workshop.sql_shell import FlinkSession


def _load_rows() -> list[dict]:
    """Import ``build_all_rows`` from data/ (not a package) by file path."""
    path = get_project_root() / "data" / "generate_driver_race_history.py"
    spec = importlib.util.spec_from_file_location("generate_driver_race_history", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build_all_rows()


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
    return "INSERT INTO `driver_race_history` VALUES\n" + values


def seed_driver_race_history(card: dict[str, str], timeout: int = 180) -> bool:
    """Run the bounded INSERT via the credential card's Flink session.

    Returns True unless the statement is reported FAILED. A bounded ``INSERT
    ... VALUES`` terminates on its own, so RUNNING/COMPLETED both mean the rows
    were accepted.
    """
    session = FlinkSession(card)
    name = session.submit(build_insert())
    status = session.wait(name, timeout=timeout)
    phase = status["status"]["phase"]
    if phase == "FAILED":
        print(f"  driver_race_history seed FAILED: {status['status'].get('detail', '')[:500]}")
        return False
    return True
