"""`workshop build` / `clean` — binary discovery, run-dir discovery, card handoff.

Nothing here touches a real `wsa-output/` tree, a real `wsa` binary, or the
network: every case builds a throwaway project root in a temp directory. The
point is the glue that removes hand-copied paths from the organizer flow, so
that is what gets asserted — which run directory wins, which binary path wins,
and that the namespace handed to `workshop creds` is complete.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import os
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml

from scripts.workshop import creds as creds_mod
from scripts.workshop import wsa as wsa_mod


def write_report(run_dir: Path, run_id: str, completed_at: str | None, *, with_csv: bool = True) -> Path:
    """Fabricate one `wsa-output/<run-id>/` the way a finished build leaves it."""
    run_dir.mkdir(parents=True, exist_ok=True)
    report = {"run_id": run_id, "operation": "build", "work_dir": str(run_dir / "terraform")}
    if completed_at is not None:
        report["completed_at"] = completed_at
    (run_dir / wsa_mod.BUILD_REPORT).write_text(json.dumps(report))
    if with_csv:
        (run_dir / wsa_mod.BUILD_CSV).write_text("Account,Email\n")
    return run_dir


class TimestampTests(unittest.TestCase):
    """wsa reports are written by Go, so its RFC 3339 dialect has to parse."""

    def test_z_suffix_and_nanoseconds(self):
        parsed = wsa_mod._parse_timestamp("2026-07-30T12:23:45.123456789Z")
        self.assertEqual(parsed, datetime(2026, 7, 30, 12, 23, 45, 123456, tzinfo=timezone.utc))

    def test_offset_is_preserved(self):
        parsed = wsa_mod._parse_timestamp("2026-07-30T05:23:45-07:00")
        self.assertEqual(parsed, datetime(2026, 7, 30, 12, 23, 45, tzinfo=timezone.utc))

    def test_go_zero_time_reads_as_absent(self):
        # Go marshals an unset time.Time as year 1 rather than omitting it.
        self.assertIsNone(wsa_mod._parse_timestamp("0001-01-01T00:00:00Z"))

    def test_junk_reads_as_absent(self):
        self.assertIsNone(wsa_mod._parse_timestamp("last tuesday"))
        self.assertIsNone(wsa_mod._parse_timestamp(""))
        self.assertIsNone(wsa_mod._parse_timestamp(None))


class RunDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.output = Path(self._tmp.name) / wsa_mod.OUTPUT_DIR
        self.output.mkdir(parents=True)
        self.addCleanup(self._tmp.cleanup)

    def test_picks_newest_by_completed_at(self):
        # run-ids are random 3-5 char strings, so alphabetical order is noise:
        # "zzz9" sorts last but finished first, and must lose.
        write_report(self.output / "zzz9", "zzz9", "2026-07-30T09:00:00Z")
        write_report(self.output / "ab12", "ab12", "2026-07-30T17:30:00Z")
        write_report(self.output / "mid7", "mid7", "2026-07-30T13:00:00Z")

        newest = wsa_mod.newest_run(self.output)
        self.assertIsNotNone(newest)
        self.assertEqual(newest.run_id, "ab12")
        self.assertEqual(newest.csv, self.output / "ab12" / wsa_mod.BUILD_CSV)
        self.assertEqual([r.run_id for r in wsa_mod.discover_runs(self.output)], ["ab12", "mid7", "zzz9"])

    def test_skips_cleaned_missing_and_unparseable(self):
        # A torn-down run: wsa renames the directory with -cleaned.
        write_report(self.output / f"new1{wsa_mod.CLEANED_SUFFIX}", "new1", "2026-07-30T23:00:00Z")
        # A build that died before writing its report.
        (self.output / "dead2").mkdir()
        (self.output / "dead2" / wsa_mod.BUILD_CSV).write_text("Account,Email\n")
        # A truncated report.
        (self.output / "trnc3").mkdir()
        (self.output / "trnc3" / wsa_mod.BUILD_REPORT).write_text('{"run_id": "trnc3"')
        # Finder droppings — iterdir sees files too.
        (self.output / ".DS_Store").write_bytes(b"\x00\x01")
        # The only usable run.
        write_report(self.output / "good4", "good4", "2026-07-30T08:00:00Z")

        self.assertEqual([r.run_id for r in wsa_mod.discover_runs(self.output)], ["good4"])

    def test_falls_back_to_started_at_then_mtime(self):
        started_only = write_report(self.output / "aaa1", "aaa1", None)
        (started_only / wsa_mod.BUILD_REPORT).write_text(
            json.dumps({"run_id": "aaa1", "started_at": "2026-07-30T20:00:00Z"})
        )
        # No usable timestamp at all: ordering falls back to the report's mtime,
        # which we pin here so the assertion is not a race.
        no_time = write_report(self.output / "bbb2", "bbb2", "0001-01-01T00:00:00Z")
        os.utime(no_time / wsa_mod.BUILD_REPORT, (0, 0))

        self.assertEqual([r.run_id for r in wsa_mod.discover_runs(self.output)], ["aaa1", "bbb2"])

    def test_run_id_falls_back_to_directory_name(self):
        (self.output / "orph5").mkdir()
        (self.output / "orph5" / wsa_mod.BUILD_REPORT).write_text(json.dumps({"completed_at": "2026-07-30T10:00:00Z"}))
        self.assertEqual(wsa_mod.newest_run(self.output).run_id, "orph5")

    def test_empty_and_absent_output_dir(self):
        self.assertEqual(wsa_mod.discover_runs(self.output), [])
        self.assertIsNone(wsa_mod.newest_run(self.output / "nope"))

    def test_explicit_run_id_beats_newest(self):
        write_report(self.output / "old11", "old11", "2026-07-01T09:00:00Z")
        write_report(self.output / "new22", "new22", "2026-07-30T17:00:00Z")

        self.assertEqual(wsa_mod.resolve_run(self.output).run_id, "new22")
        self.assertEqual(wsa_mod.resolve_run(self.output, "old11").run_id, "old11")
        # A run-id that never produced a report must not silently fall back to
        # a different workshop's run.
        self.assertIsNone(wsa_mod.resolve_run(self.output, "gone9"))

    def test_matches_the_reports_run_id_not_the_directory_name(self):
        """A renamed/copied run dir must not yield a run whose id isn't the one asked for.

        The resolved id names the credential cards and is what gets handed to
        `wsa clean`, so returning a run with a different id than requested
        would quietly point both at the wrong workshop.
        """
        write_report(self.output / "copied", "real7", "2026-07-30T10:00:00Z")

        self.assertIsNone(wsa_mod.resolve_run(self.output, "copied"))
        self.assertEqual(wsa_mod.resolve_run(self.output, "real7").run_id, "real7")


class BinaryDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        patcher = patch.dict("os.environ", {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_binary(self, checkout: Path) -> Path:
        binary = checkout / wsa_mod.BINARY_SUBPATH
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
        return binary

    def test_wsa_home_wins(self):
        root = self.tmp / "repo"
        root.mkdir()
        sibling = self.make_binary(self.tmp / wsa_mod.SIBLING_DIR)
        override = self.make_binary(self.tmp / "elsewhere")
        os.environ["WSA_HOME"] = str(self.tmp / "elsewhere")

        with patch.object(wsa_mod, "_main_checkout", return_value=None):
            self.assertEqual(wsa_mod.find_wsa(root), override)
        self.assertTrue(sibling.exists())  # present, just outranked

    def test_sibling_of_repo_root(self):
        root = self.tmp / "repo"
        root.mkdir()
        binary = self.make_binary(self.tmp / wsa_mod.SIBLING_DIR)
        with patch.object(wsa_mod, "_main_checkout", return_value=None):
            self.assertEqual(wsa_mod.find_wsa(root), binary)

    def test_sibling_of_main_checkout_when_run_from_a_worktree(self):
        # A git worktree lives at <main>/.claude/worktrees/<name>, so its own
        # parent is `worktrees/` — the sibling checkout is next to the MAIN
        # checkout. Without this candidate, every wsa command fails in a
        # worktree even though the binary is right there.
        main = self.tmp / "main-checkout"
        worktree = main / ".claude" / "worktrees" / "plan-exec"
        worktree.mkdir(parents=True)
        binary = self.make_binary(self.tmp / wsa_mod.SIBLING_DIR)
        self.assertFalse((worktree.parent / wsa_mod.SIBLING_DIR).exists())

        with patch.object(wsa_mod, "_main_checkout", return_value=main):
            self.assertEqual(wsa_mod.find_wsa(worktree), binary)

    def test_path_is_the_last_resort(self):
        root = self.tmp / "repo"
        root.mkdir()
        binary = self.make_binary(self.tmp / "somewhere")
        with (
            patch.object(wsa_mod, "_main_checkout", return_value=None),
            patch.object(wsa_mod.shutil, "which", return_value=str(binary)),
        ):
            self.assertEqual(wsa_mod.find_wsa(root), binary)

    def test_missing_binary_error_names_every_path_and_the_fix(self):
        root = self.tmp / "repo"
        root.mkdir()
        os.environ["WSA_HOME"] = str(self.tmp / "custom")
        with (
            patch.object(wsa_mod, "_main_checkout", return_value=self.tmp / "main-checkout"),
            patch.object(wsa_mod.shutil, "which", return_value=None),
        ):
            with self.assertRaises(SystemExit) as caught:
                wsa_mod.find_wsa(root)

        message = str(caught.exception)
        self.assertIn(str(self.tmp / "custom" / wsa_mod.BINARY_SUBPATH), message)
        self.assertIn(str(self.tmp / wsa_mod.SIBLING_DIR / wsa_mod.BINARY_SUBPATH), message)
        self.assertIn("WSA_HOME", message)
        self.assertIn("workshop-setup-accelerator", message)

    def test_non_executable_file_is_not_accepted(self):
        root = self.tmp / "repo"
        root.mkdir()
        stub = self.tmp / wsa_mod.SIBLING_DIR / wsa_mod.BINARY_SUBPATH
        stub.parent.mkdir(parents=True)
        stub.write_text("not built yet")
        stub.chmod(0o644)
        with (
            patch.object(wsa_mod, "_main_checkout", return_value=None),
            patch.object(wsa_mod.shutil, "which", return_value=None),
        ):
            with self.assertRaises(SystemExit):
                wsa_mod.find_wsa(root)

    def test_main_checkout_reads_gits_common_dir(self):
        repo = self.tmp / "plain-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
        # In a non-worktree checkout the common dir is the repo's own .git, so
        # this candidate collapses onto the plain sibling lookup (and dedupes).
        self.assertEqual(wsa_mod._main_checkout(repo), repo.resolve())

    def test_main_checkout_outside_a_repo_is_none(self):
        loose = self.tmp / "not-a-repo"
        loose.mkdir()
        with patch.object(
            wsa_mod.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(128, "git"),
        ):
            self.assertIsNone(wsa_mod._main_checkout(loose))


def sample_csv(path: Path, prefixes: list[str]) -> None:
    """A build-output.csv shaped by creds.py's own COLUMNS map.

    Deriving the headers from `creds_mod.COLUMNS` keeps this fixture honest if
    the credential group is renamed — but it does NOT prove wsa emits these
    headers for this spec. Only a real build does that.
    """
    headers = ["Account", "Email", *creds_mod.COLUMNS.values()]
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        for i, prefix in enumerate(prefixes, 1):
            row = {header: f"{key}-{prefix}" for key, header in creds_mod.COLUMNS.items()}
            row[creds_mod.COLUMNS["prefix"]] = prefix
            row["Account"] = str(i)
            row["Email"] = f"dmarsh+{prefix}@confluent.io"
            writer.writerow(row)


class BuildHandoffTests(unittest.TestCase):
    """`workshop build` must leave nothing for the organizer to copy."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (self.root / wsa_mod.SPEC_FILE).write_text("name: test\n")
        self.run_dir = self.root / wsa_mod.OUTPUT_DIR / "ab12"
        for module in (wsa_mod, creds_mod):
            patcher = patch.object(module, "get_project_root", return_value=self.root)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(wsa_mod, "find_wsa", return_value=Path("/fake/bin/wsa"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def build_args(self, **overrides) -> argparse.Namespace:
        defaults = dict(
            accounts="1-2",
            concurrency=4,
            retries=None,
            run_id="",
            force=False,
            no_dispenser_check=False,
            stream_terraform_logs=False,
            name="",
            social_feed_url="",
            region="us-east-1",
            no_cards=False,
            prefix="",
            account_count=None,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def fake_build(
        self,
        code: int = 0,
        prefixes: tuple[str, ...] = ("f1wp001", "f1wp002"),
        run_id: str = "ab12",
    ):
        """Stand in for `wsa build`: leave the artifacts, return an exit code."""
        run_dir = self.root / wsa_mod.OUTPUT_DIR / run_id

        def _run(binary, root, subcommand, extra, spec_path=None):
            write_report(run_dir, run_id, "2026-07-30T17:00:00Z", with_csv=False)
            sample_csv(run_dir / wsa_mod.BUILD_CSV, list(prefixes))
            self.recorded_extra = extra
            self.recorded_spec_path = spec_path
            return code

        return patch.object(wsa_mod, "_stream_wsa", side_effect=_run)

    def test_flags_reach_wsa(self):
        with self.fake_build(run_id="xy99"), patch.object(creds_mod, "creds") as fake_creds:
            with contextlib.redirect_stdout(io.StringIO()):
                wsa_mod.build(self.build_args(force=True, run_id="xy99"))
        self.assertEqual(
            self.recorded_extra,
            ["--accounts", "1-2", "--concurrency", "4", "--run-id", "xy99", "--force"],
        )
        # An explicit run-id also names the cards, so build and clean agree.
        self.assertEqual(fake_creds.call_args.args[0].name, "xy99")

    def test_no_prefix_override_uses_the_committed_spec(self):
        # No --prefix: _stream_wsa gets spec_path=None (the committed spec) and no
        # generated file is written.
        with self.fake_build(), patch.object(creds_mod, "creds"):
            with contextlib.redirect_stdout(io.StringIO()):
                wsa_mod.build(self.build_args())
        self.assertIsNone(self.recorded_spec_path)
        self.assertFalse((self.root / wsa_mod.GENERATED_SPEC).exists())

    def test_prefix_override_writes_a_derived_spec(self):
        # --prefix that differs from the spec derives a spec with only that field
        # changed and points the build at it.
        (self.root / wsa_mod.SPEC_FILE).write_text(
            "name: test\nterraform_vars:\n  prefix: f1wp{NNN}\n  region: us-east-1\n"
        )
        with self.fake_build(), patch.object(creds_mod, "creds"):
            with contextlib.redirect_stdout(io.StringIO()):
                wsa_mod.build(self.build_args(prefix="f1ws{NNN}"))
        self.assertEqual(self.recorded_spec_path, self.root / wsa_mod.GENERATED_SPEC)
        derived = yaml.safe_load((self.root / wsa_mod.GENERATED_SPEC).read_text())
        self.assertEqual(derived["terraform_vars"]["prefix"], "f1ws{NNN}")
        self.assertEqual(derived["terraform_vars"]["region"], "us-east-1")  # untouched

    def test_bare_base_prefix_gets_the_account_placeholder(self):
        # `--prefix f1ws` (no placeholder) must not name every account the same —
        # build appends {NNN} before deriving the spec.
        (self.root / wsa_mod.SPEC_FILE).write_text(
            "name: test\nterraform_vars:\n  prefix: f1wp{NNN}\n"
        )
        with self.fake_build(), patch.object(creds_mod, "creds"):
            with contextlib.redirect_stdout(io.StringIO()):
                wsa_mod.build(self.build_args(prefix="f1ws"))
        derived = yaml.safe_load((self.root / wsa_mod.GENERATED_SPEC).read_text())
        self.assertEqual(derived["terraform_vars"]["prefix"], "f1ws{NNN}")

    def test_account_count_override_writes_a_derived_spec(self):
        # --attendees is authoritative: N lands in the derived spec so wsa's
        # "(N accounts)" banner can't contradict what --accounts builds.
        (self.root / wsa_mod.SPEC_FILE).write_text("name: test\naccount_count: 5\n")
        with self.fake_build(), patch.object(creds_mod, "creds"):
            with contextlib.redirect_stdout(io.StringIO()):
                wsa_mod.build(self.build_args(accounts="1-40", account_count=40))
        self.assertEqual(self.recorded_spec_path, self.root / wsa_mod.GENERATED_SPEC)
        derived = yaml.safe_load((self.root / wsa_mod.GENERATED_SPEC).read_text())
        self.assertEqual(derived["account_count"], 40)

    def test_account_count_matching_the_spec_is_not_an_override(self):
        (self.root / wsa_mod.SPEC_FILE).write_text("name: test\naccount_count: 5\n")
        with self.fake_build(), patch.object(creds_mod, "creds"):
            with contextlib.redirect_stdout(io.StringIO()):
                wsa_mod.build(self.build_args(account_count=5))
        self.assertIsNone(self.recorded_spec_path)
        self.assertFalse((self.root / wsa_mod.GENERATED_SPEC).exists())

    def test_prefix_and_account_count_land_in_one_derived_spec(self):
        # Both overrides go through a single read-modify-write. Two separate
        # deriving functions would each clobber the other's field — this is the
        # test that catches that, since neither single-override test can.
        (self.root / wsa_mod.SPEC_FILE).write_text(
            "name: test\naccount_count: 5\nterraform_vars:\n  prefix: f1wp{NNN}\n"
        )
        with self.fake_build(), patch.object(creds_mod, "creds"):
            with contextlib.redirect_stdout(io.StringIO()):
                wsa_mod.build(self.build_args(prefix="f1ws", account_count=40))
        derived = yaml.safe_load((self.root / wsa_mod.GENERATED_SPEC).read_text())
        self.assertEqual(derived["terraform_vars"]["prefix"], "f1ws{NNN}")
        self.assertEqual(derived["account_count"], 40)

    def test_prefix_matching_the_spec_is_not_an_override(self):
        # --prefix equal to the committed value uses the committed spec, no file.
        (self.root / wsa_mod.SPEC_FILE).write_text(
            "name: test\nterraform_vars:\n  prefix: f1wp{NNN}\n"
        )
        with self.fake_build(), patch.object(creds_mod, "creds"):
            with contextlib.redirect_stdout(io.StringIO()):
                wsa_mod.build(self.build_args(prefix="f1wp{NNN}"))
        self.assertIsNone(self.recorded_spec_path)
        self.assertFalse((self.root / wsa_mod.GENERATED_SPEC).exists())

    def test_creds_namespace_is_complete(self):
        # creds() reads four attributes but only marks two required, so a
        # partial namespace fails at runtime rather than at parse time.
        with self.fake_build(), patch.object(creds_mod, "creds") as fake_creds:
            with contextlib.redirect_stdout(io.StringIO()):
                wsa_mod.build(self.build_args(social_feed_url="http://feed:8080", region="us-west-2"))

        namespace = fake_creds.call_args.args[0]
        self.assertEqual(namespace.csv, str(self.run_dir / wsa_mod.BUILD_CSV))
        self.assertEqual(namespace.name, "ab12")  # run_id, not a prompt
        self.assertEqual(namespace.social_feed_url, "http://feed:8080")
        self.assertEqual(namespace.region, "us-west-2")

    def test_name_overrides_the_run_id(self):
        with self.fake_build(), patch.object(creds_mod, "creds") as fake_creds:
            with contextlib.redirect_stdout(io.StringIO()):
                wsa_mod.build(self.build_args(name="london-june"))
        self.assertEqual(fake_creds.call_args.args[0].name, "london-june")

    def test_cards_land_under_the_run_id(self):
        # End-to-end through the real creds writer: build -> cards on disk.
        with self.fake_build():
            with contextlib.redirect_stdout(io.StringIO()):
                wsa_mod.build(self.build_args())

        creds_dir = self.root / "runs" / "ab12" / "credentials"
        self.assertTrue((creds_dir / "f1wp001.env").exists())
        self.assertTrue((creds_dir / "f1wp002.md").exists())
        self.assertTrue((creds_dir / "credentials.csv").exists())

    def test_partial_failure_propagates_and_prints_the_follow_up(self):
        with self.fake_build(code=1), patch.object(creds_mod, "creds") as fake_creds:
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit) as caught:
                    wsa_mod.build(self.build_args())

        self.assertEqual(caught.exception.code, 1)
        fake_creds.assert_not_called()
        self.assertIn(f"--csv {self.run_dir / wsa_mod.BUILD_CSV}", err.getvalue())
        self.assertIn("--name ab12", err.getvalue())

    def test_explicit_run_id_is_not_confused_by_an_older_run(self):
        # An earlier workshop sits in wsa-output/. This build names its own
        # run-id and dies before writing a report, so there is nothing of
        # *ours* to point at — and pointing at the older run's CSV would send
        # the organizer to the wrong workshop's credentials.
        write_report(self.root / wsa_mod.OUTPUT_DIR / "old11", "old11", "2026-07-01T09:00:00Z")

        with patch.object(wsa_mod, "_stream_wsa", return_value=1), patch.object(creds_mod, "creds") as fake_creds:
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit):
                    wsa_mod.build(self.build_args(run_id="new22"))

        fake_creds.assert_not_called()
        self.assertNotIn("old11", err.getvalue())

    def test_no_cards_prints_the_command_instead(self):
        with self.fake_build(), patch.object(creds_mod, "creds") as fake_creds:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                wsa_mod.build(self.build_args(no_cards=True))
        fake_creds.assert_not_called()
        self.assertIn("uv run workshop creds --csv", out.getvalue())


class CleanTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (self.root / wsa_mod.SPEC_FILE).write_text("name: test\n")
        patcher = patch.object(wsa_mod, "get_project_root", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(wsa_mod, "find_wsa", return_value=Path("/fake/bin/wsa"))
        patcher.start()
        self.addCleanup(patcher.stop)
        # Default to "no OAuth client on this machine" so the assertions below do
        # not depend on whether the developer running them happens to have one at
        # ~/.wsa/. Tests that care patch it themselves.
        patcher = patch.object(wsa_mod, "find_google_credentials", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def clean_args(self, **overrides) -> argparse.Namespace:
        defaults = dict(
            run_id="",
            accounts="",
            concurrency=None,
            no_password_reset=False,
            no_dispenser_clear=False,
            accounts_only=False,
            shared_only=False,
            google_credentials="",
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_run_id_is_discovered(self):
        write_report(self.root / wsa_mod.OUTPUT_DIR / "ab12", "ab12", "2026-07-30T17:00:00Z")
        recorded = {}

        def _run(binary, root, subcommand, extra):
            recorded["extra"] = extra
            return 0

        with patch.object(wsa_mod, "_stream_wsa", side_effect=_run):
            with contextlib.redirect_stdout(io.StringIO()):
                wsa_mod.clean(self.clean_args(no_password_reset=True, no_dispenser_clear=True))

        self.assertEqual(
            recorded["extra"],
            ["--run-id", "ab12", "--no-password-reset", "--no-dispenser-clear"],
        )

    def test_explicit_run_id_beats_discovery(self):
        write_report(self.root / wsa_mod.OUTPUT_DIR / "ab12", "ab12", "2026-07-30T17:00:00Z")
        recorded = {}

        def _run(binary, root, subcommand, extra):
            recorded["extra"] = extra
            return 0

        with patch.object(wsa_mod, "_stream_wsa", side_effect=_run):
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.redirect_stderr(io.StringIO()):
                    wsa_mod.clean(self.clean_args(run_id="old99"))

        # The two skips are appended by clean itself: with no OAuth client there is
        # no way to run either post-teardown step.
        self.assertEqual(
            recorded["extra"],
            ["--run-id", "old99", "--no-password-reset", "--no-dispenser-clear"],
        )

    def test_no_run_to_clean_is_an_actionable_error(self):
        with self.assertRaises(SystemExit) as caught:
            wsa_mod.clean(self.clean_args())
        self.assertIn("workshop build", str(caught.exception))

    # --- Google OAuth credentials for the two post-teardown steps ---------------
    #
    # wsa only *warns* when they are missing (main.go:1432,1507), so a teardown can
    # report success while leaving attendee passwords live and their credentials in
    # a shared sheet. These pin down that we either pass the file or say we didn't.

    def _clean_extra(self, **overrides) -> list[str]:
        write_report(self.root / wsa_mod.OUTPUT_DIR / "ab12", "ab12", "2026-07-30T17:00:00Z")
        recorded: dict[str, list[str]] = {}

        def _run(binary, root, subcommand, extra):
            recorded["extra"] = extra
            return 0

        with patch.object(wsa_mod, "_stream_wsa", side_effect=_run):
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.redirect_stderr(io.StringIO()):
                    wsa_mod.clean(self.clean_args(**overrides))
        return recorded["extra"]

    def test_both_steps_get_the_credentials_when_a_dispenser_exists(self):
        (self.root / "wsa.env").write_text("WSA_DISPENSER_SPREADSHEET_ID=1AbC_real-sheet-id\n")
        with patch.object(wsa_mod, "find_google_credentials", return_value=Path("/creds.json")):
            extra = self._clean_extra()
        self.assertIn("--gmail-credentials", extra)
        self.assertIn("--sheets-credentials", extra)
        self.assertEqual(extra[extra.index("--sheets-credentials") + 1], "/creds.json")
        self.assertNotIn("--no-password-reset", extra)
        self.assertNotIn("--no-dispenser-clear", extra)

    def test_no_dispenser_configured_skips_only_the_dispenser_clear(self):
        # The common case before a dispenser is set up: rotating passwords still
        # matters, and asking wsa to clear a sheet that doesn't exist only earns a
        # "cannot resolve spreadsheet" warning at every teardown.
        with patch.object(wsa_mod, "find_google_credentials", return_value=Path("/creds.json")):
            extra = self._clean_extra()
        self.assertIn("--gmail-credentials", extra)
        self.assertIn("--no-dispenser-clear", extra)
        self.assertNotIn("--sheets-credentials", extra)

    def test_missing_credentials_skip_both_and_name_the_consequence(self):
        (self.root / "wsa.env").write_text("WSA_DISPENSER_SPREADSHEET_ID=1AbC_real-sheet-id\n")
        write_report(self.root / wsa_mod.OUTPUT_DIR / "ab12", "ab12", "2026-07-30T17:00:00Z")
        err = io.StringIO()
        with patch.object(wsa_mod, "_stream_wsa", return_value=0):
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.redirect_stderr(err):
                    wsa_mod.clean(self.clean_args())
        warning = err.getvalue()
        # A silent skip is the failure mode this guards against: the operator has to
        # learn that old cards still work and the sheet still holds credentials.
        self.assertIn("stays valid", warning)
        self.assertIn("dispenser sheet", warning)

    def test_an_explicit_skip_never_passes_credentials(self):
        with patch.object(wsa_mod, "find_google_credentials", return_value=Path("/creds.json")):
            extra = self._clean_extra(no_password_reset=True, no_dispenser_clear=True)
        self.assertNotIn("--gmail-credentials", extra)
        self.assertNotIn("--sheets-credentials", extra)


class GoogleCredentialsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_explicit_path_wins_and_a_bad_one_is_fatal(self):
        real = self.root / "creds.json"
        real.write_text("{}")
        self.assertEqual(wsa_mod.find_google_credentials(str(real)), real)
        with self.assertRaises(SystemExit) as caught:
            wsa_mod.find_google_credentials(str(self.root / "nope.json"))
        self.assertIn("no such file", str(caught.exception))

    def test_falls_back_to_the_wsa_checkout_root(self):
        checkout = self.root / "workshop-setup-accelerator"
        (checkout / "bin").mkdir(parents=True)
        creds = checkout / wsa_mod.GOOGLE_CREDS_NAME
        creds.write_text("{}")
        with patch.dict(os.environ, {wsa_mod.GOOGLE_CREDS_ENV: ""}, clear=False):
            with patch.object(wsa_mod.Path, "home", return_value=self.root / "nohome"):
                found = wsa_mod.find_google_credentials(wsa_binary=checkout / "bin" / "wsa")
        self.assertEqual(found, creds)

    def test_placeholder_spreadsheet_id_is_not_a_dispenser(self):
        # wsa.env.example ships `<YOUR_SPREADSHEET_ID>`; a copied-but-unedited file
        # must not make teardown try to clear a sheet.
        (self.root / "wsa.env").write_text("WSA_DISPENSER_SPREADSHEET_ID=<YOUR_SPREADSHEET_ID>\n")
        with patch.dict(os.environ, {wsa_mod.DISPENSER_ID_ENV: ""}, clear=False):
            self.assertFalse(wsa_mod.dispenser_configured(self.root))

    def test_a_real_id_in_wsa_env_counts(self):
        (self.root / "wsa.env").write_text("# comment\nWSA_DISPENSER_SPREADSHEET_ID=1AbC-real\n")
        with patch.dict(os.environ, {wsa_mod.DISPENSER_ID_ENV: ""}, clear=False):
            self.assertTrue(wsa_mod.dispenser_configured(self.root))

    def test_an_export_prefix_is_stripped_like_wsa_does(self):
        # wsa's loader strips `export ` (`internal/envfile/envfile.go`), so failing
        # to strip it here would skip a clear wsa would have run.
        (self.root / "wsa.env").write_text("export WSA_DISPENSER_SPREADSHEET_ID=1AbC-real\n")
        with patch.dict(os.environ, {wsa_mod.DISPENSER_ID_ENV: ""}, clear=False):
            self.assertTrue(wsa_mod.dispenser_configured(self.root))

    def test_a_commented_out_id_is_not_a_dispenser(self):
        (self.root / "wsa.env").write_text("#WSA_DISPENSER_SPREADSHEET_ID=1AbC-real\n")
        with patch.dict(os.environ, {wsa_mod.DISPENSER_ID_ENV: ""}, clear=False):
            self.assertFalse(wsa_mod.dispenser_configured(self.root))

    def test_the_shell_environment_wins_over_wsa_env(self):
        (self.root / "wsa.env").write_text("WSA_DISPENSER_SPREADSHEET_ID=<YOUR_SPREADSHEET_ID>\n")
        with patch.dict(os.environ, {wsa_mod.DISPENSER_ID_ENV: "1AbC-real"}, clear=False):
            self.assertTrue(wsa_mod.dispenser_configured(self.root))


class SpecHeaderContractTests(unittest.TestCase):
    """The handoff only works if wsa's CSV headers are the ones creds.py reads.

    wsa builds each header as ``"<credential group name> / <field label>"``
    (`internal/spec/spec.go:172`, `internal/report/report.go:383-390`) from the
    spec in *this* repo — so the contract is checkable from the spec alone,
    without a build. What this still cannot prove: that every field resolves to
    a non-empty value. That needs a real `wsa build`.
    """

    def test_every_column_creds_reads_is_a_column_the_spec_produces(self):
        import yaml

        from scripts.common.terraform import get_project_root

        spec = yaml.safe_load((get_project_root() / wsa_mod.SPEC_FILE).read_text())
        headers = {
            f"{group['name']} / {field['label']}"
            for group in spec.get("credentials", [])
            for field in group.get("fields", [])
        }
        missing = sorted(set(creds_mod.COLUMNS.values()) - headers)
        self.assertEqual(missing, [], f"wsa-spec-aws.yaml has no field for: {missing}")


class RaceControlNamingTests(unittest.TestCase):
    """Item 20: `workshop start-races` is canonical; the old scripts alias it."""

    def test_workshop_subcommands_keep_every_flag(self):
        from scripts.workshop import cli as cli_mod

        argv = ["workshop", "start-races", "--region", "us-west-2", "--filter", "rr", "--count", "3"]
        with patch.object(cli_mod.start_mod, "start_races") as started:
            with patch("sys.argv", argv):
                cli_mod.main()
        args = started.call_args.args[0]
        self.assertEqual((args.region, args.filter, args.count), ("us-west-2", "rr", 3))

        with patch.object(cli_mod.stop_mod, "stop_races") as stopped:
            with patch("sys.argv", ["workshop", "stop-races", "--region", "eu-west-1"]):
                cli_mod.main()
        self.assertEqual(stopped.call_args.args[0].region, "eu-west-1")

    def test_deprecated_aliases_warn_and_delegate(self):
        from scripts.instructor import start_all_races, stop_all_races

        for module, body_name, replacement in (
            (start_all_races, "start_races", "workshop start-races"),
            (stop_all_races, "stop_races", "workshop stop-races"),
        ):
            with self.subTest(module=module.__name__):
                with patch.object(module, body_name) as body:
                    err = io.StringIO()
                    with patch("sys.argv", [module.__name__, "--region", "us-west-2"]):
                        with contextlib.redirect_stderr(err):
                            module.main()
                self.assertEqual(body.call_args.args[0].region, "us-west-2")
                self.assertIn("deprecated", err.getvalue())
                self.assertIn(replacement, err.getvalue())


if __name__ == "__main__":
    unittest.main()
