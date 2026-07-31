"""Statement classification, splitting and file execution in the SQL shell.

The classification behaviors guard one failure: a `CREATE` statement that the
shell mistakes for a throwaway query gets deleted moments after submission, so
the lab object never exists and nothing says why. Every file in `demo-reference/`
opens with a `--` header, and one of them has commented-out blocks whose lines
end in `');'`.

`--file` reuses that same rule via `split_statements`, so the tests import the
real splitter rather than mirroring it — a mirror can agree with itself while
the shipped rule drifts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.workshop.sql_shell import (
    is_durable,
    is_terminator,
    run_file,
    split_statements,
    strip_leading_comments,
)

REFERENCE_SQL = sorted((Path(__file__).resolve().parents[1] / "demo-reference").glob("*.sql"))


@pytest.mark.parametrize("path", REFERENCE_SQL, ids=lambda p: p.name)
def test_reference_sql_is_left_running(path: Path) -> None:
    """Every canonical lab file must survive being pasted or --exec'd whole."""
    assert is_durable(path.read_text())


@pytest.mark.parametrize("path", REFERENCE_SQL, ids=lambda p: p.name)
def test_reference_sql_is_one_statement(path: Path) -> None:
    """A ';' inside a comment must not split the file into fragments."""
    stmts = split_statements(path.read_text())
    assert len(stmts) == 1
    assert is_durable(stmts[0])


@pytest.mark.parametrize(
    ("sql", "durable"),
    [
        ("SELECT * FROM t", False),
        ("SHOW TABLES", False),
        ("DESCRIBE car_state", False),
        ("CREATE TABLE x AS SELECT 1", True),
        ("DROP AGENT x", True),
        ("  \n\tINSERT INTO x VALUES (1)", True),
        ("-- a comment\nSELECT 1", False),
        ("-- a comment\nCREATE TABLE x", True),
        ("/* block */ CREATE TABLE x", True),
        ("-- a\n-- b\n/* c */\n-- d\nALTER TABLE x", True),
        ("", False),
        ("-- only a comment", False),
        ("/* unterminated", False),
    ],
)
def test_is_durable(sql: str, durable: bool) -> None:
    assert is_durable(sql) is durable


@pytest.mark.parametrize(
    ("line", "terminates"),
    [
        ("SELECT 1;", True),
        ("WITH ('max_iterations' = '10');", True),
        ("-- );", False),
        ("--   'transport-type' = 'STREAMABLE_HTTP'", False),
        ("SELECT 1", False),
        ("", False),
    ],
)
def test_is_terminator(line: str, terminates: bool) -> None:
    assert is_terminator(line) is terminates


# --- splitting a multi-statement file -------------------------------------


def test_splits_multiple_statements() -> None:
    text = "-- header\nCREATE TABLE a (x INT);\n\nINSERT INTO a VALUES (1);\nSELECT * FROM a;\n"
    stmts = split_statements(text)

    assert len(stmts) == 3
    assert stmts[0].endswith("CREATE TABLE a (x INT)")
    assert stmts[1] == "INSERT INTO a VALUES (1)"
    assert stmts[2] == "SELECT * FROM a"
    assert [is_durable(s) for s in stmts] == [True, True, False]


def test_trailing_semicolon_is_stripped_from_every_statement() -> None:
    """The ';' is a shell convention for "run it", not part of the statement."""
    assert all(not s.endswith(";") for s in split_statements("SELECT 1;\nSELECT 2;\n"))


def test_final_statement_without_a_terminator_still_runs() -> None:
    """Flushed at EOF — otherwise a file's last statement silently vanishes."""
    assert split_statements("CREATE TABLE a (x INT)\n") == ["CREATE TABLE a (x INT)"]


def test_trailing_comment_block_is_not_submitted() -> None:
    """The EOF flush must not send a file's closing notes to Flink as SQL."""
    stmts = split_statements("SELECT 1;\n\n-- notes for later\n-- more notes\n")
    assert stmts == ["SELECT 1"]


def test_comment_only_file_yields_nothing() -> None:
    assert split_statements("-- nothing to run\n/* not this either */\n") == []


# --- run_file: order, stop-on-failure, exit codes -------------------------


def _returns_rows(sql: str) -> bool:
    return strip_leading_comments(sql).upper().startswith(("SELECT", "SHOW", "DESCRIBE"))


class _FakeSession:
    """Records what was submitted; fails whichever statement index is chosen.

    ``wait`` mimics the Statements API closely enough to reach the *probe* path:
    a query comes back carrying schema columns, which is what makes
    ``run_statement`` read results and then delete the statement. A fake without
    that always takes the no-columns DDL branch, so ``results``/``stop`` are never
    called and any assertion about cleanup passes vacuously.
    """

    def __init__(self, fail_on: int | None = None) -> None:
        self.submitted: list[str] = []
        self.fail_on = fail_on
        self.stopped: list[str] = []
        self.read: list[str] = []
        self._sql_by_name: dict[str, str] = {}

    def submit(self, sql: str) -> str:
        name = f"stmt-{len(self.submitted) + 1}"
        self.submitted.append(sql)
        self._sql_by_name[name] = sql
        return name

    def wait(self, name: str, timeout: int = 120) -> dict:
        status = {"phase": "COMPLETED", "detail": "boom"}
        if self.fail_on == len(self.submitted):
            status["phase"] = "FAILED"
        elif _returns_rows(self._sql_by_name[name]):
            status["traits"] = {"schema": {"columns": [{"name": "car_number"}, {"name": "lap"}]}}
        return {"status": status}

    def results(self, name: str, max_rows: int):
        self.read.append(name)
        yield [88, 12]

    def stop(self, name: str) -> None:
        self.stopped.append(name)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "lab.sql"
    path.write_text(body)
    return path


MULTI = "-- header\nCREATE TABLE a (x INT);\nINSERT INTO a VALUES (1);\nDROP TABLE a;\n"
# Ends in a query, so one statement takes the probe path and two do not.
MIXED = "-- header\nCREATE TABLE a (x INT);\nINSERT INTO a VALUES (1);\nSELECT * FROM a;\n"


def test_run_file_executes_every_statement_in_order(tmp_path: Path) -> None:
    session = _FakeSession()

    assert run_file(session, _write(tmp_path, MULTI)) == 0
    assert len(session.submitted) == 3
    assert session.submitted[1] == "INSERT INTO a VALUES (1)"
    assert session.submitted[2] == "DROP TABLE a"


def test_run_file_leaves_durable_statements_running(tmp_path: Path) -> None:
    """Same classification as --exec: a CREATE must not be deleted after submit."""
    session = _FakeSession()
    run_file(session, _write(tmp_path, MULTI))

    assert session.stopped == []


def test_run_file_deletes_probe_statements_but_not_durable_ones(tmp_path: Path) -> None:
    """The other half of the classification, and what stops the all-durable test
    above from passing vacuously: a query inside a file has its rows read and is
    then deleted, while the CREATE and INSERT before it stay running."""
    session = _FakeSession()

    assert run_file(session, _write(tmp_path, MIXED)) == 0
    assert session.read == ["stmt-3"]
    assert session.stopped == ["stmt-3"]


def test_run_file_stops_at_the_first_failure(tmp_path: Path) -> None:
    """Statement 3 would fail confusingly on an object statement 2 never made."""
    session = _FakeSession(fail_on=2)

    assert run_file(session, _write(tmp_path, MULTI)) == 1
    assert len(session.submitted) == 2


def test_run_file_reports_a_missing_file(tmp_path: Path) -> None:
    session = _FakeSession()

    assert run_file(session, tmp_path / "nope.sql") == 1
    assert session.submitted == []


def test_run_file_reports_a_file_with_no_sql(tmp_path: Path) -> None:
    session = _FakeSession()

    assert run_file(session, _write(tmp_path, "-- just a note\n")) == 1
    assert session.submitted == []


@pytest.mark.parametrize("path", REFERENCE_SQL, ids=lambda p: p.name)
def test_run_file_submits_reference_sql_verbatim(path: Path) -> None:
    """What `--file demo-reference/x.sql` sends must match what the lab guides
    show, minus the trailing ';' — the same normalization create_lab_objects does."""
    session = _FakeSession()

    assert run_file(session, path) == 0
    assert session.submitted == [path.read_text().strip().rstrip(";").strip()]
