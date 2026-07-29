"""Attendee Flink SQL shell — the no-login access surface.

  uv run f1-sql --creds <prefix>.env

Authenticates to the attendee's Flink compute pool with their API keys (from the
credential card) and runs SQL against the Statements REST API. This replaces the
Confluent Cloud Console SQL Workspace for the workshop: attendees never log in.

Submit a statement by ending it with ';'. SELECT/SHOW results stream into a
table (Ctrl-C stops a long-running query); CREATE/DROP/INSERT statements are
submitted and left running. Meta-commands: \\help, \\q.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests
from dotenv import dotenv_values
from rich.console import Console
from rich.table import Table

console = Console()

TERMINAL_PHASES = {"COMPLETED", "FAILED", "STOPPED"}
# Statements we leave running (they create durable objects); everything else
# (SELECT/SHOW/DESCRIBE) is a probe we delete after reading results.
KEEP_PREFIXES = ("CREATE", "INSERT", "DROP", "ALTER")
MAX_STREAM_ROWS = 50


def is_durable(sql: str) -> bool:
    """True if the statement creates something and should be left running.

    Classifies on the first *keyword*, not the first character: every file in
    ``demo-reference/`` opens with a ``--`` header, and pasting one whole (or
    piping it through ``--exec``) must not make a ``CREATE TABLE`` look like a
    throwaway SELECT — that would delete the job moments after submitting it.
    Leading line and block comments are skipped for this check only; the SQL
    itself is still sent to Flink verbatim.
    """
    rest = sql.lstrip()
    while rest:
        if rest.startswith("--"):
            _, _, rest = rest.partition("\n")
            rest = rest.lstrip()
        elif rest.startswith("/*"):
            _, _, rest = rest.partition("*/")
            rest = rest.lstrip()
        else:
            break
    return rest.upper().startswith(KEEP_PREFIXES)


class FlinkSession:
    def __init__(self, creds: dict[str, str]):
        try:
            self.rest = creds["F1_FLINK_REST_ENDPOINT"].rstrip("/")
            self.org = creds["F1_ORGANIZATION_ID"]
            self.env = creds["F1_ENVIRONMENT_ID"]
            self.pool = creds["F1_COMPUTE_POOL_ID"]
            self.catalog = creds["F1_CATALOG"]
            self.database = creds["F1_DATABASE"]
            self.auth = (creds["F1_FLINK_API_KEY"], creds["F1_FLINK_API_SECRET"])
        except KeyError as e:
            raise SystemExit(f"Credential file is missing {e}. Regenerate it with `uv run workshop creds`.") from e
        self.base = f"{self.rest}/sql/v1/organizations/{self.org}/environments/{self.env}/statements"
        self._seq = 0

    def _name(self) -> str:
        self._seq += 1
        return f"f1sql-{int(time.time() * 1000) % 10**9}-{self._seq}"

    def submit(self, sql: str) -> str:
        name = self._name()
        body = {
            "name": name,
            "spec": {
                "statement": sql,
                "compute_pool": {"id": self.pool},
                "properties": {
                    "sql.current-catalog": self.catalog,
                    "sql.current-database": self.database,
                },
            },
        }
        r = requests.post(self.base, json=body, auth=self.auth, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"submit failed ({r.status_code}): {r.text[:600]}")
        return name

    def wait(self, name: str, timeout: int = 120) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = requests.get(f"{self.base}/{name}", auth=self.auth, timeout=30).json()
            if st["status"]["phase"] in TERMINAL_PHASES | {"RUNNING"}:
                return st
            time.sleep(2)
        return st

    def results(self, name: str, max_rows: int, timeout: int = 60, idle_pages: int = 6):
        """Yield result rows.

        A streaming SELECT keeps a ``next`` pagination link alive indefinitely
        and returns empty pages once the stream goes quiet (or a bounded query
        has drained), so following ``next`` blindly would never terminate. Stop
        on: max_rows reached, ``next`` absent, ``idle_pages`` consecutive empty
        pages after at least one row, or the wall-clock ``timeout`` (which also
        bounds the warmup wait before the first row arrives).
        """
        url = f"{self.base}/{name}/results"
        seen = 0
        empty_streak = 0
        deadline = time.time() + timeout
        while seen < max_rows and time.time() < deadline:
            page = requests.get(url, auth=self.auth, timeout=30).json()
            data = (page.get("results") or {}).get("data") or []
            if data:
                empty_streak = 0
                for item in data:
                    yield item.get("row")
                    seen += 1
                    if seen >= max_rows:
                        return
            else:
                empty_streak += 1
                if seen > 0 and empty_streak >= idle_pages:
                    return  # bounded query drained / stream gone quiet
            nxt = (page.get("metadata") or {}).get("next")
            if not nxt:
                return
            url = nxt
            time.sleep(0.5 if data else 1.0)

    def stop(self, name: str) -> None:
        try:
            requests.delete(f"{self.base}/{name}", auth=self.auth, timeout=30)
        except requests.RequestException:
            pass


def _columns(status: dict) -> list[str]:
    cols = status.get("status", {}).get("traits", {}).get("schema", {}).get("columns", [])
    return [c["name"] for c in cols]


def run_statement(session: FlinkSession, sql: str) -> None:
    keep = is_durable(sql)
    try:
        name = session.submit(sql)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        return

    status = session.wait(name)
    phase = status["status"]["phase"]

    if phase == "FAILED":
        console.print(f"[red]FAILED:[/red] {status['status'].get('detail', '')[:1000]}")
        return

    cols = _columns(status)
    if not cols:
        # DDL/DML with no result set.
        console.print(f"[green]{phase}[/green]" + ("  (statement left running)" if keep else ""))
        if keep:
            console.print(f"[dim]statement: {name}[/dim]")
        return

    table = Table(*cols, show_lines=False)
    rows = 0
    try:
        for row in session.results(name, MAX_STREAM_ROWS):
            table.add_row(*[str(v) for v in (row or [])])
            rows += 1
    except KeyboardInterrupt:
        console.print("[yellow](stopped)[/yellow]")
    finally:
        if not keep:
            session.stop(name)

    if rows:
        console.print(table)
        console.print(f"[dim]{rows} row(s){' (truncated)' if rows >= MAX_STREAM_ROWS else ''}[/dim]")
    else:
        console.print("[yellow]no rows (a streaming query may still be warming up — try again)[/yellow]")


HELP = """[bold]F1 Flink SQL shell[/bold]
  End a statement with ';' to run it (multi-line is fine).
  \\help   show this help
  \\q      quit
Examples:
  SHOW TABLES;
  SELECT * FROM race_standings;
  SELECT car_number, lap, tire_temp_fl_c FROM car_telemetry LIMIT 5;"""


def repl(session: FlinkSession) -> None:
    console.print(HELP)
    buffer: list[str] = []
    while True:
        try:
            prompt = "f1-sql> " if not buffer else "    ...> "
            line = input(prompt)
        except (EOFError, KeyboardInterrupt):
            console.print("\nbye")
            return
        stripped = line.strip()
        if not buffer and stripped in ("\\q", "\\quit", "exit", "quit"):
            console.print("bye")
            return
        if not buffer and stripped in ("\\help", "\\h", "?"):
            console.print(HELP)
            continue
        buffer.append(line)
        # A ';' inside a comment doesn't end a statement — demo-reference SQL has
        # commented-out blocks whose lines end in ');', and treating those as
        # terminators would submit the header alone and mangle the real statement.
        if stripped.endswith(";") and not stripped.startswith("--"):
            sql = "\n".join(buffer).strip().rstrip(";").strip()
            buffer = []
            if sql:
                run_statement(session, sql)


def main() -> None:
    parser = argparse.ArgumentParser(description="F1 workshop Flink SQL shell (API-key access, no login)")
    parser.add_argument("--creds", required=True, help="Path to your <prefix>.env credential card")
    parser.add_argument("--exec", help="Run a single statement and exit (non-interactive)")
    args = parser.parse_args()

    path = Path(args.creds)
    if not path.exists():
        sys.exit(f"Credential file not found: {path}")
    creds = dotenv_values(path)
    session = FlinkSession(creds)

    if args.exec:
        # rstrip whitespace first: piping a .sql file in leaves a trailing
        # newline after the ';', which a bare rstrip(";") would not remove.
        run_statement(session, args.exec.strip().rstrip(";").strip())
    else:
        console.print(f"[green]Connected[/green] to {session.catalog} / {session.database}")
        repl(session)


if __name__ == "__main__":
    main()
