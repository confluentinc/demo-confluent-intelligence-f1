"""`create-workshop`: --attendees is the authoritative attendee count.

It used to be capped by the spec's ``account_count``, so growing a workshop meant
editing and committing ``wsa-spec-aws.yaml``. Now the flag drives the accounts wsa
builds, the derived spec's ``account_count``, and the shared Postgres replication-slot
capacity, and the only thing that can stop an over-large count is the 1Password
Console-password check.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.workshop import create as create_mod
from scripts.workshop import creds as creds_mod
from scripts.workshop import wsa as wsa_mod

# wsa >= 0.3.0 rejects a spec that declares email_pattern; the attendee pattern
# now travels via WSA_EMAIL_PATTERN / WORKSHOP_EMAIL_PATTERN, not the spec.
SPEC_WITH_CONSOLE = """\
name: test
account_count: 5
terraform_vars:
  prefix: f1wp{NNN}
  grant_console_access: "true"
"""


class ExportAttendeeCountTests(unittest.TestCase):
    """TF_VAR_attendee_count must track --attendees, not a Terraform default."""

    def test_sets_the_variable(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TF_VAR_attendee_count", None)
            with contextlib.redirect_stdout(io.StringIO()):
                create_mod._export_attendee_count(40)
            self.assertEqual(os.environ["TF_VAR_attendee_count"], "40")

    def test_a_conflicting_export_is_overridden_and_reported(self):
        # Assignment, not setdefault: a stale export must not silently cap the
        # slot count. It is reported so the override isn't invisible.
        with patch.dict(os.environ, {"TF_VAR_attendee_count": "5"}, clear=False):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                create_mod._export_attendee_count(40)
            self.assertEqual(os.environ["TF_VAR_attendee_count"], "40")
            self.assertIn("overriding exported TF_VAR_attendee_count=5", out.getvalue())

    def test_a_matching_export_is_not_reported(self):
        with patch.dict(os.environ, {"TF_VAR_attendee_count": "40"}, clear=False):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                create_mod._export_attendee_count(40)
            self.assertNotIn("overriding", out.getvalue())


class ConsoleAccountCheckTests(unittest.TestCase):
    """The password check is now the only guard on an over-large --attendees."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (self.root / wsa_mod.SPEC_FILE).write_text(SPEC_WITH_CONSOLE)

    def test_bails_after_three_misses(self):
        # Each lookup is an `op read` with a 30s timeout, so --attendees 400 must
        # not sit through 400 sequential probes before reporting anything.
        with patch.object(creds_mod, "_resolve_op_password", return_value="") as probe:
            with self.assertRaises(SystemExit) as caught:
                wsa_mod._check_console_accounts(self.root, list(range(1, 401)))
        self.assertEqual(probe.call_count, 3)
        message = str(caught.exception)
        self.assertIn("1, 2, 3", message)
        self.assertIn("there may be more", message)

    def test_passes_when_every_password_resolves(self):
        with patch.object(creds_mod, "_resolve_op_password", return_value="pw"):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                wsa_mod._check_console_accounts(self.root, list(range(1, 41)))
        self.assertIn("console pw:  ok (40 accounts)", out.getvalue())

    def test_skipped_without_console_access(self):
        (self.root / wsa_mod.SPEC_FILE).write_text("name: test\naccount_count: 5\n")
        with patch.object(creds_mod, "_resolve_op_password") as probe:
            wsa_mod._check_console_accounts(self.root, list(range(1, 41)))
        probe.assert_not_called()


class AttendeeCountAuthorityTests(unittest.TestCase):
    """--attendees above the spec's account_count builds instead of exiting."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (self.root / wsa_mod.SPEC_FILE).write_text(SPEC_WITH_CONSOLE)

        for target, kwargs in (
            ("get_project_root", {"return_value": self.root}),
            ("check_terraform_installed", {"return_value": True}),
            ("check_docker_running", {"return_value": True}),
            ("check_aws_configured", {"return_value": True}),
            ("ensure_secrets", {"return_value": None}),
            ("_prompt_prefix", {"return_value": "f1wp{NNN}"}),
            ("_check_env_name_collisions", {"return_value": None}),
            ("_print_next_steps", {"return_value": None}),
        ):
            patcher = patch.object(create_mod, target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

        for target in ("find_wsa", "spec_validate", "newest_run", "resolve_run", "_check_console_accounts"):
            patcher = patch.object(wsa_mod, target, return_value=None)
            patcher.start()
            self.addCleanup(patcher.stop)

    def args(self, **overrides) -> argparse.Namespace:
        defaults = dict(
            attendees=40,
            concurrency=4,
            name="",
            region="us-east-1",
            prefix="",
            social_feed_url="",
            yes=True,
            force=False,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_forty_attendees_against_a_spec_of_five(self):
        env = {wsa_mod.EMAIL_PATTERN_ENV: "org+f1wp{N}@example.com"}
        with patch.object(wsa_mod, "build") as build:
            with patch.dict(os.environ, env, clear=False):
                with contextlib.redirect_stdout(io.StringIO()):
                    create_mod.create(self.args(attendees=40))
                self.assertEqual(os.environ["TF_VAR_attendee_count"], "40")
                # The resolved pattern is exported for every downstream wsa call.
                self.assertEqual(
                    os.environ[wsa_mod.WSA_EMAIL_PATTERN_ENV], "org+f1wp{N}@example.com"
                )
        namespace = build.call_args.args[0]
        self.assertEqual(namespace.accounts, "1-40")
        self.assertEqual(namespace.account_count, 40)
        self.assertEqual(namespace.email_pattern, "org+f1wp{N}@example.com")

    def test_zero_is_still_rejected(self):
        with patch.object(wsa_mod, "build") as build:
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):
                    create_mod.create(self.args(attendees=0))
        build.assert_not_called()

    def test_yes_rejects_an_unset_email_pattern_before_preflight(self):
        # --yes is non-interactive: with no pattern resolvable from the
        # environment or credentials.env, wsa >= 0.3.0 has nothing to build
        # accounts from, so create must bail before touching the wsa binary.
        (self.root / wsa_mod.SPEC_FILE).write_text("name: test\naccount_count: 5\n")
        with (
            patch.dict(
                os.environ,
                {wsa_mod.EMAIL_PATTERN_ENV: "", wsa_mod.WSA_EMAIL_PATTERN_ENV: ""},
                clear=False,
            ),
            patch.object(wsa_mod, "find_wsa") as find_wsa,
            self.assertRaisesRegex(SystemExit, "email pattern is not set"),
        ):
            create_mod.create(self.args(yes=True, email_pattern=""))
        find_wsa.assert_not_called()


if __name__ == "__main__":
    unittest.main()
