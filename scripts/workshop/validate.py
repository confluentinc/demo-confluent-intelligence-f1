"""`workshop validate` — verify attendee environments using only API keys.

  workshop validate --creds file.env
  workshop validate --creds-glob 'runs/*/credentials/*.env'

Every check runs through the attendee's own Flink + Schema Registry credentials
(no AWS CLI, no `confluent login`) — so it tests exactly what an attendee has,
and works regardless of how the environment was provisioned (wsa or otherwise).
The live race feed and CDC pipeline are proven through data (22 cars in
race_standings, 198 rows in driver_race_history) rather than by inspecting
ECS/connectors directly.
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

import requests
from dotenv import dotenv_values
from rich.console import Console
from rich.table import Table

from scripts.workshop.sql_shell import FlinkSession

console = Console()

EXPECTED_HISTORY_ROWS = 198
EXPECTED_CARS = 22


def _query(session: FlinkSession, sql: str, max_rows: int = 60, timeout: int = 90) -> list[list]:
    """Submit a probe statement, collect up to max_rows result rows, clean up."""
    name = session.submit(sql)
    try:
        status = session.wait(name, timeout=timeout)
        if status["status"]["phase"] == "FAILED":
            raise RuntimeError(status["status"].get("detail", "")[:300])
        return [row for row in session.results(name, max_rows, timeout=timeout)]
    finally:
        session.stop(name)


class Check:
    """One validation check; appends (name, ok, detail) to results."""

    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append((name, ok, detail))

    def run(self, name: str, fn) -> None:
        try:
            ok, detail = fn()
            self.record(name, ok, detail)
        except Exception as e:
            # Surface any check failure (network, SQL, parse) as a red row.
            self.record(name, False, str(e)[:120])


def validate_connection(creds: dict[str, str]) -> list[tuple[str, bool, str]]:
    """Run all API-key-only checks against one environment."""
    session = FlinkSession(creds)
    sr_url = creds.get("F1_SCHEMA_REGISTRY_URL", "").rstrip("/")
    sr_auth = (creds.get("F1_SR_API_KEY", ""), creds.get("F1_SR_API_SECRET", ""))
    c = Check()

    def tables():
        rows = {r[0] for r in _query(session, "SHOW TABLES;", timeout=60)}
        need = {"car_telemetry", "race_standings", "driver_race_history"}
        missing = need - rows
        return (not missing), ("all present" if not missing else f"missing {missing}")

    def models():
        rows = {r[0] for r in _query(session, "SHOW MODELS;", timeout=60)}
        need = {"llm_textgen_model", "llm_embedding_model"}
        missing = need - rows
        return (not missing), ("both present" if not missing else f"missing {missing}")

    def connections():
        rows = {r[0] for r in _query(session, "SHOW CONNECTIONS;", timeout=60)}
        return (len(rows) >= 2), f"{len(rows)} connection(s)"

    def history_rows():
        # COUNT(*) is a retract/changelog stream that climbs 1 -> 198 (each
        # increment is a retract+insert pair). Read well past 2*198 rows so it
        # reaches convergence before the cap; the table is static so it then
        # goes idle and we take the converged max.
        rows = _query(session, "SELECT COUNT(*) FROM driver_race_history;", max_rows=1000, timeout=120)
        got = max((int(r[0]) for r in rows), default=0)
        return (got == EXPECTED_HISTORY_ROWS), f"{got} rows (expect {EXPECTED_HISTORY_ROWS})"

    def live_feed():
        cars = {r[0] for r in _query(session, "SELECT car_number FROM race_standings;", max_rows=60, timeout=90)}
        return (len(cars) == EXPECTED_CARS), f"{len(cars)} cars (expect {EXPECTED_CARS})"

    def sr_subjects():
        if not sr_url:
            return False, "no schema registry url in card"
        subs = requests.get(f"{sr_url}/subjects", auth=sr_auth, timeout=30).json()
        if "race_standings-key" not in subs:
            return False, "race_standings-key subject missing"
        if "car_telemetry-key" in subs:
            return False, "unexpected car_telemetry-key subject (string key expected)"
        standings_key = requests.get(
            f"{sr_url}/subjects/race_standings-key/versions/latest", auth=sr_auth, timeout=30
        ).json().get("schema", "")
        telemetry_value = requests.get(
            f"{sr_url}/subjects/car_telemetry-value/versions/latest", auth=sr_auth, timeout=30
        ).json().get("schema", "")
        missing = []
        if not all(field in standings_key for field in ("race_id", "car_number")):
            missing.append("composite race_standings key")
        if "race_id" not in telemetry_value:
            missing.append("car_telemetry race_id")
        return (not missing), ("race_id schemas present" if not missing else "missing " + ", ".join(missing))

    c.run("flink tables", tables)
    c.run("SR key encoding", sr_subjects)
    c.run("history rows (CDC)", history_rows)
    c.run("live feed (22 cars)", live_feed)
    c.run("LLM models", models)
    c.run("Bedrock connections", connections)
    return c.results


def _print(label: str, results: list[tuple[str, bool, str]]) -> None:
    table = Table(title=f"validate: {label}", title_justify="left")
    table.add_column("check")
    table.add_column("result")
    table.add_column("detail")
    for name, ok, detail in results:
        table.add_row(name, "[green]PASS[/green]" if ok else "[red]FAIL[/red]", detail)
    console.print(table)


def validate(args: argparse.Namespace) -> None:
    paths: list[Path] = []
    if args.creds_glob:
        paths += [Path(p) for p in glob.glob(args.creds_glob)]
    if args.creds:
        paths.append(Path(args.creds))
    if not paths:
        sys.exit(
            "Provide --creds-glob 'runs/*/credentials/*.env' or --creds <card>.env. "
            "Example: uv run workshop validate --creds-glob 'runs/*/credentials/*.env'"
        )

    any_fail = False
    for path in sorted(set(paths)):
        if not path.exists():
            sys.exit(f"Credential file not found: {path}")
        creds = dict(dotenv_values(path))
        label = creds.get("F1_PREFIX") or path.stem
        results = validate_connection(creds)
        _print(label, results)
        any_fail = any_fail or not all(ok for _, ok, _ in results)

    raise SystemExit(1 if any_fail else 0)


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--creds", help="Validate a single environment from a credential card (.env)")
    p.add_argument("--creds-glob", help="Glob matching many credential cards, e.g. 'runs/*/credentials/*.env'")
