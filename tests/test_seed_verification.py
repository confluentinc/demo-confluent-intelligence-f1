"""`driver_race_history` seeding: verified by row count, never by a bare marker.

Two compounding bugs are covered here.

(a) The seeder accepted ``RUNNING`` as success — ``FlinkSession.wait`` returns at
    ``RUNNING`` — and wrote its ``.seeded`` marker, so a bounded ``INSERT`` that
    later FAILED was never retried and the table stayed empty. LAB 2's
    ``COUNT(*)`` returns 0 and LAB 4's history join returns nothing, silently.

(b) The marker survived ``uv run destroy``, so `up` → `destroy` → `up` printed
    "already seeded" over a brand-new empty table.

And the hazard introduced by fixing them: if verification is inconclusive and no
marker is written, the next run must **not** insert a second copy of every row.
"""

import io
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.common import deployment_meta as meta
from scripts.selfservice import seed

ENV_ID = "env-new123"
CARD = {
    "F1_ENVIRONMENT_ID": ENV_ID,
    "F1_FLINK_REST_ENDPOINT": "https://flink.example.com",
    "F1_ORGANIZATION_ID": "org-1",
    "F1_COMPUTE_POOL_ID": "lfcp-1",
    "F1_CATALOG": "RIVER-RACING-alices-ENV",
    "F1_DATABASE": "RIVER-RACING-alices-CLUSTER",
    "F1_FLINK_API_KEY": "FK",
    "F1_FLINK_API_SECRET": "FS",
}

# `results()` on an empty table yields nothing and only returns when its window
# expires, which is how count_rows tells "no data" from "no answer". The fake
# reproduces that by actually spending time, so the real guard is exercised.
DRAIN = 0.3
COUNT_TIMEOUT = 0.4


class FakeSession:
    """Stands in for FlinkSession: scripted counts, recorded submissions."""

    def __init__(self, count_sequences):
        self.count_sequences = [list(s) for s in count_sequences]
        self.submitted: list[str] = []
        self.stopped: list[str] = []
        self.base = "https://flink.example.com/sql/v1/organizations/org-1/environments/env/statements"
        self.auth = ("FK", "FS")

    def submit(self, sql: str) -> str:
        self.submitted.append(sql)
        return f"stmt-{len(self.submitted)}"

    def wait(self, name: str, timeout: int = 120) -> dict:
        return {"status": {"phase": "RUNNING"}}

    def results(self, name: str, max_rows: int, timeout: int = 60, idle_pages: int = 6):
        sequence = self.count_sequences.pop(0) if self.count_sequences else []
        if not sequence:
            time.sleep(DRAIN)
            return
        for value in sequence:
            yield [str(value)]

    def stop(self, name: str) -> None:
        self.stopped.append(name)

    @property
    def inserts(self) -> list[str]:
        return [s for s in self.submitted if s.lstrip().upper().startswith("INSERT")]


def _phase_response(phase: str, detail: str = ""):
    class Response:
        @staticmethod
        def json():
            return {"status": {"phase": phase, "detail": detail}}

    return Response()


class SeedTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.marker = meta.seed_marker_path(self.root, meta.SELFSERVICE)
        self.marker.parent.mkdir(parents=True, exist_ok=True)

    def ensure(self, session, insert_phases):
        """Run the seeder against a fake session and a scripted INSERT lifecycle."""
        responses = [_phase_response(p) for p in insert_phases]
        with (
            patch.object(seed, "FlinkSession", return_value=session),
            patch.object(seed.requests, "get", side_effect=responses),
        ):
            out = io.StringIO()
            with redirect_stdout(out):
                ok = seed.ensure_driver_race_history(
                    CARD, self.root, meta.SELFSERVICE, count_timeout=COUNT_TIMEOUT
                )
        return ok, out.getvalue()


class ExpectedRowsTests(unittest.TestCase):
    def test_expected_count_comes_from_the_generator(self):
        """198 is asserted against data/, not hardcoded twice."""
        self.assertEqual(seed.expected_rows(), 198)


class TerminalPhaseTests(unittest.TestCase):
    def test_running_is_not_terminal(self):
        self.assertNotIn("RUNNING", seed.TERMINAL_PHASES)

    def test_wait_reports_timeout_rather_than_guessing(self):
        session = FakeSession([])
        with patch.object(seed.requests, "get", return_value=_phase_response("RUNNING")):
            phase, _detail = seed._wait_terminal(session, "stmt-1", timeout=0)
        self.assertEqual(phase, "TIMEOUT")

    def test_insert_that_runs_then_fails_is_a_failure(self):
        session = FakeSession([])
        responses = [_phase_response("RUNNING"), _phase_response("FAILED", "table not found")]
        with patch.object(seed.requests, "get", side_effect=responses):
            ok, problem = seed.run_insert(session, timeout=30)
        self.assertFalse(ok)
        self.assertIn("FAILED", problem)
        self.assertIn("table not found", problem)


class EnsureTests(SeedTestCase):
    def test_running_then_failed_writes_no_marker(self):
        session = FakeSession([[]])  # empty table -> the INSERT is attempted
        ok, output = self.ensure(session, ["RUNNING", "FAILED"])

        self.assertFalse(ok)
        self.assertFalse(self.marker.exists())
        self.assertIn("Seed failed", output)
        self.assertEqual(len(session.inserts), 1)

    def test_successful_seed_is_verified_then_marked_with_the_environment_id(self):
        session = FakeSession([[], [1, 50, 198]])
        ok, output = self.ensure(session, ["COMPLETED"])

        self.assertTrue(ok)
        self.assertEqual(self.marker.read_text().strip(), ENV_ID)
        self.assertIn("Seeded and verified 198 rows", output)
        self.assertEqual(len(session.inserts), 1)

    def test_insert_completed_but_short_count_is_a_failure(self):
        session = FakeSession([[], [12]])
        ok, output = self.ensure(session, ["COMPLETED"])

        self.assertFalse(ok)
        self.assertFalse(self.marker.exists())
        self.assertIn("holds 12 rows, expected 198", output)

    def test_already_populated_table_is_not_seeded_again(self):
        """Idempotency: no marker, but 198 rows already there."""
        session = FakeSession([[198]])
        ok, output = self.ensure(session, [])

        self.assertTrue(ok)
        self.assertEqual(self.marker.read_text().strip(), ENV_ID)
        self.assertEqual(session.inserts, [])
        self.assertIn("already holds 198 rows", output)

    def test_unexpected_row_count_refuses_to_insert_again(self):
        """A double-seeded table must not be tripled."""
        session = FakeSession([[396]])
        ok, output = self.ensure(session, [])

        self.assertFalse(ok)
        self.assertFalse(self.marker.exists())
        self.assertEqual(session.inserts, [])
        self.assertIn("Refusing to insert again", output)

    def test_marker_from_another_environment_is_ignored(self):
        """destroy -> up used to print "already seeded" over an empty table."""
        self.marker.write_text("env-OLD999\n")
        session = FakeSession([[], [198]])
        ok, output = self.ensure(session, ["COMPLETED"])

        self.assertTrue(ok)
        self.assertIn("re-verifying", output)
        self.assertEqual(self.marker.read_text().strip(), ENV_ID)
        self.assertEqual(len(session.inserts), 1)

    def test_matching_marker_short_circuits_without_any_statement(self):
        self.marker.write_text(f"{ENV_ID}\n")
        session = FakeSession([])
        ok, output = self.ensure(session, [])

        self.assertTrue(ok)
        self.assertEqual(session.submitted, [])
        self.assertIn("already seeded and verified", output)

    def test_unverifiable_count_is_not_reported_as_a_failed_seed(self):
        """None and a number are different answers, and must read differently."""
        session = FakeSession([[]])
        with (
            patch.object(seed, "FlinkSession", return_value=session),
            patch.object(seed, "count_rows", return_value=(None, "endpoint unreachable")),
        ):
            out = io.StringIO()
            with redirect_stdout(out):
                ok = seed.ensure_driver_race_history(CARD, self.root, meta.SELFSERVICE)

        self.assertFalse(ok)
        self.assertFalse(self.marker.exists())
        self.assertIn("Could not verify", out.getvalue())
        self.assertEqual(session.inserts, [])


class CountRowsTests(unittest.TestCase):
    def test_largest_changelog_value_wins(self):
        """A streaming COUNT(*) emits a changelog, not one final row."""
        session = FakeSession([[1, 2, 197, 198]])
        count, problem = seed.count_rows(session, expected=198, timeout=COUNT_TIMEOUT)
        self.assertEqual((count, problem), (198, ""))
        self.assertEqual(session.stopped, ["stmt-1"])

    def test_empty_table_counts_as_zero_once_the_window_expires(self):
        session = FakeSession([[]])
        count, problem = seed.count_rows(session, expected=198, timeout=COUNT_TIMEOUT)
        self.assertEqual((count, problem), (0, ""))

    def test_an_early_empty_return_is_inconclusive_not_zero(self):
        """Mistaking this for an empty table would insert every row twice."""
        session = FakeSession([[]])
        count, problem = seed.count_rows(session, expected=198, timeout=600)
        self.assertIsNone(count)
        self.assertIn("returned nothing", problem)

    def test_a_failed_count_query_is_unknown_not_zero(self):
        session = FakeSession([[]])
        with patch.object(session, "wait", return_value={"status": {"phase": "FAILED", "detail": "no such table"}}):
            count, problem = seed.count_rows(session, expected=198, timeout=COUNT_TIMEOUT)
        self.assertIsNone(count)
        self.assertIn("FAILED", problem)


if __name__ == "__main__":
    unittest.main()
