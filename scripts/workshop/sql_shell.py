"""Flink SQL shell driven by a credential card — no Console login needed.

  uv run f1-sql                      # card resolved from credentials.env
  uv run f1-sql --creds <prefix>.env
  uv run f1-sql --exec 'SHOW TABLES'         # one statement, then exit
  uv run f1-sql --file demo-reference/x.sql  # every statement in a file, in order

Authenticates to a Flink compute pool with the API keys on a credential card and
runs SQL against the Statements REST API. The instructor-led workshop teaches the
Console SQL workspace instead (LAB 1-6); this shell is what the standalone and
self-service tracks use, and stays available as a fallback in the room.

Submit a statement by ending it with ';'. SELECT/SHOW results stream into a
table (Ctrl-C stops a long-running query); CREATE/DROP/INSERT statements are
submitted and left running. Meta-commands: \\help, \\q.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import requests
from rich.console import Console
from rich.table import Table

from scripts.common.credentials import load_card

try:
    # Imported purely for the side effect: readline hooks input() so the REPL gets
    # arrow-key editing, ^A/^E and in-session history. Attendees live in this shell
    # for four labs, retyping multi-line CREATE statements without it. Absent on
    # some builds (Windows without pyreadline), where input() still works fine.
    import readline  # noqa: F401
except ImportError:  # pragma: no cover - platform dependent
    pass

console = Console()

TERMINAL_PHASES = {"COMPLETED", "FAILED", "STOPPED"}
# Statements we leave running (they create durable objects); everything else
# (SELECT/SHOW/DESCRIBE) is a probe we delete after reading results.
KEEP_PREFIXES = ("CREATE", "INSERT", "DROP", "ALTER")
MAX_STREAM_ROWS = 50

# Remediation for a card (or Terraform state) missing a field. Phrased
# conditionally because FlinkSession is built both from a credential card and
# straight from Terraform outputs (scripts/common/simulator_control.py), where no
# card exists — naming only `workshop creds` was wrong for every path but the
# organizer's. scripts/pitwall/consumer.py carries the same text; the shared home
# would be scripts/common/credentials.py, which neither module can import from
# here without a cycle through the card loader.
CARD_REMEDIATION = (
    "If this came from a credential card, recreate it the way you made it:\n"
    "    uv run deploy          standalone deploy (AWS + Confluent)\n"
    "    uv run selfservice up  solo, Confluent-only\n"
    "    uv run f1-onboard      workshop attendee, from your claim email\n"
    "    uv run workshop creds  organizer, from wsa's build-output.csv"
)


def strip_leading_comments(sql: str) -> str:
    """Return ``sql`` with any leading line/block comments removed.

    Used to classify a statement by its first *keyword* and to tell a
    comment-only fragment from real SQL. The statement itself is always sent to
    Flink verbatim — this only ever informs a decision.
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
    return rest


def is_durable(sql: str) -> bool:
    """True if the statement creates something and should be left running.

    Classifies on the first *keyword*, not the first character: every file in
    ``demo-reference/`` opens with a ``--`` header, and pasting one whole (or
    piping it through ``--exec`` / ``--file``) must not make a ``CREATE TABLE``
    look like a throwaway SELECT — that would delete the job moments after
    submitting it.
    """
    return strip_leading_comments(sql).upper().startswith(KEEP_PREFIXES)


def is_terminator(line: str) -> bool:
    """True if this (already stripped) line ends the statement being buffered.

    A ';' inside a comment doesn't end a statement — demo-reference SQL has
    commented-out blocks whose lines end in ');', and treating those as
    terminators would submit the header alone and mangle the real statement.
    Shared by the REPL (which buffers incrementally, line by line) and
    ``split_statements`` so both obey exactly one rule.
    """
    return line.endswith(";") and not line.startswith("--")


def normalize(lines: list[str]) -> str:
    """Join buffered lines into a submittable statement.

    Strips the trailing ';' — that's a shell convention for "run it", not part of
    the statement (``scripts/common/simulator_control.py`` does the same to the
    ``demo-reference/*.sql`` files it submits).
    """
    return "\n".join(lines).strip().rstrip(";").strip()


def split_statements(text: str) -> list[str]:
    """Split a .sql file into statements using the REPL's buffering rule.

    Also flushes whatever is left at EOF, so a final statement whose ';' is
    followed by a trailing comment still runs instead of silently vanishing. The
    remainder is only flushed if it contains real SQL — a file ending in a block
    of ``--`` notes must not submit its notes. (A ';' inside a string literal is
    not tracked; that limitation is inherited from the REPL on purpose.)
    """
    statements: list[str] = []
    buffer: list[str] = []
    for line in text.splitlines():
        buffer.append(line)
        if is_terminator(line.strip()):
            sql = normalize(buffer)
            buffer = []
            if sql:
                statements.append(sql)
    tail = normalize(buffer)
    if tail and strip_leading_comments(tail):
        statements.append(tail)
    return statements


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
            raise SystemExit(f"Credential file is missing {e}.\n  {CARD_REMEDIATION}") from e
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


def run_statement(session: FlinkSession, sql: str) -> bool:
    """Submit one statement, print its result, and report whether it succeeded.

    The bool exists for ``--file``, which must stop at the first failure instead
    of plowing on into statements whose objects the failed one was meant to
    create. The interactive REPL ignores it.
    """
    keep = is_durable(sql)
    try:
        name = session.submit(sql)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        return False

    status = session.wait(name)
    phase = status["status"]["phase"]

    if phase == "FAILED":
        console.print(f"[red]FAILED:[/red] {status['status'].get('detail', '')[:1000]}")
        return False

    cols = _columns(status)
    if not cols:
        # DDL/DML with no result set.
        console.print(f"[green]{phase}[/green]" + ("  (statement left running)" if keep else ""))
        if keep:
            console.print(f"[dim]statement: {name}[/dim]")
        return True

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
    return True


def run_file(session: FlinkSession, path: str | Path) -> int:
    """Run every statement in a .sql file in order. Returns a process exit code.

    Same classification, comment handling, wait and error reporting as ``--exec``
    — this is why the splitting rule lives in ``split_statements`` rather than
    being reimplemented per caller (``scripts/common/simulator_control.py`` had to
    do exactly that to submit ``demo-reference/*.sql``).
    """
    file = Path(path)
    if not file.exists():
        console.print(f"[red]File not found:[/red] {file}")
        return 1

    statements = split_statements(file.read_text())
    if not statements:
        console.print(f"[yellow]No SQL statements in {file}[/yellow]")
        return 1

    total = len(statements)
    for i, sql in enumerate(statements, 1):
        if total > 1:
            console.print(f"[dim]-- {file.name}: statement {i}/{total}[/dim]")
        if not run_statement(session, sql):
            console.print(f"[red]Stopped at statement {i} of {total} — see the error above.[/red]")
            return 1
    return 0


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
        if is_terminator(stripped):
            sql = normalize(buffer)
            buffer = []
            if sql:
                run_statement(session, sql)


def main() -> None:
    parser = argparse.ArgumentParser(description="F1 workshop Flink SQL shell (API-key access, no login)")
    parser.add_argument(
        "--creds",
        help="Path to your <prefix>.env credential card (default: read from credentials.env)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--exec", help="Run a single statement and exit (non-interactive)")
    mode.add_argument(
        "--file",
        help="Run every statement in a .sql file, in order, and exit (stops at the first failure)",
    )
    args = parser.parse_args()

    path, creds = load_card(args.creds)
    # Printed in every mode, before FlinkSession, so a card missing a field names
    # itself. That matters most non-interactively: the card was auto-resolved from
    # one of four sources, and "recreate it" is useless if the attendee can't tell
    # which file is broken.
    console.print(f"[dim]card: {path}[/dim]")
    session = FlinkSession(creds)

    if args.exec:
        # normalize() over one "line": piping a .sql file in leaves a trailing
        # newline after the ';', which a bare rstrip(";") would not remove.
        run_statement(session, normalize([args.exec]))
    elif args.file:
        raise SystemExit(run_file(session, args.file))
    else:
        console.print(f"[green]Connected[/green] to {session.catalog} / {session.database}")
        repl(session)


if __name__ == "__main__":
    main()
