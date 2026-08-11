"""`selfservice down`: retire the track on success, keep every trace on failure.

Two symmetric bugs live here.

**Success used to leave litter.** Teardown cleared the `F1_CARD` pointer but left
the card *files* on disk, so on a machine that had used both solo tracks,
destroying one left two cards and no pointer — and `resolve_card()` then
hard-exits *every* attendee tool with "Multiple credential cards found" while
exactly one live environment exists. The `.seeded` marker survived too, so the
next `selfservice up` printed "already seeded" over a brand-new empty table.

**Failure must not litter-collect.** The dangerous inverse: deleting the card,
marker, and metadata after a *failed* destroy hides an environment whose
resources are still live and still billing, and throws away the prefix the retry
needs.
"""

import argparse
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.common import deployment_meta as meta
from scripts.selfservice import cli as ss_cli

CREDS = {
    "TF_VAR_confluent_cloud_api_key": "CCKEY",
    "TF_VAR_confluent_cloud_api_secret": "CCSECRET",
    "TF_VAR_owner_email": "kevin@example.com",
    "TF_VAR_aws_bedrock_access_key": "AKIAEXAMPLE",
    "TF_VAR_aws_bedrock_secret_key": "BEDROCKSECRET",
}


class DownTests(unittest.TestCase):
    """A fully-provisioned self-service track, then `down` with each outcome."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        self.ss_path = self.root / "terraform" / "self-service"
        self.ss_path.mkdir(parents=True)
        self.state = self.ss_path / "terraform.tfstate"
        self.state.write_text("{}")

        self.creds_file = self.root / "credentials.env"
        self.card = self.root / "runs" / meta.SELFSERVICE.name / "credentials" / "kevins.env"
        self.card.parent.mkdir(parents=True)
        self.card.write_text("F1_ENVIRONMENT_ID=env-abc\n")
        self.card.with_suffix(".md").write_text("# card\n")
        self.creds_file.write_text(f"F1_CARD={self.card.relative_to(self.root)}\n")

        self.marker = meta.seed_marker_path(self.root, meta.SELFSERVICE)
        self.marker.write_text("env-abc\n")
        meta.save_meta(self.root, meta.SELFSERVICE, **{meta.KEY_RESOLVED_PREFIX: "kevins"})

        # The other track's card must survive either outcome.
        self.other_card = self.root / "runs" / meta.STANDALONE.name / "credentials" / "kevin.env"
        self.other_card.parent.mkdir(parents=True)
        self.other_card.write_text("F1_ENVIRONMENT_ID=env-standalone\n")
        meta.save_meta(self.root, meta.STANDALONE, **{meta.KEY_RESOLVED_PREFIX: "kevin"})

    def run_down(self, destroy_ok: bool):
        with (
            patch.object(ss_cli, "get_project_root", return_value=self.root),
            patch.object(ss_cli, "load_or_create_credentials_file", return_value=(self.creds_file, dict(CREDS))),
            # Exporting TF vars reads Terraform state through the terraform binary.
            patch.object(ss_cli, "export_selfservice_tf_env") as export,
            patch.object(ss_cli, "cleanup_terraform_artifacts") as cleanup,
            patch.object(ss_cli, "run_terraform_destroy", return_value=destroy_ok) as destroy,
        ):
            out = io.StringIO()
            raised = None
            with redirect_stdout(out):
                try:
                    ss_cli.down(argparse.Namespace(yes=True))
                except SystemExit as e:
                    raised = e
        return out.getvalue(), raised, export, cleanup, destroy

    def test_successful_destroy_retires_the_whole_track(self):
        output, raised, export, cleanup, destroy = self.run_down(destroy_ok=True)

        self.assertIsNone(raised)
        destroy.assert_called_once()
        export.assert_called_once()
        cleanup.assert_called_once_with(self.ss_path)

        self.assertFalse(self.card.exists())
        self.assertFalse(self.card.with_suffix(".md").exists())
        self.assertFalse(self.marker.exists())
        self.assertEqual(meta.load_meta(self.root, meta.SELFSERVICE), {})
        # The pointer goes with them, or every attendee tool exits on a dead path.
        self.assertNotIn("F1_CARD=", self.creds_file.read_text())
        self.assertIn("Teardown complete", output)

        # Scoped: the standalone track is untouched.
        self.assertTrue(self.other_card.exists())
        self.assertEqual(meta.load_meta(self.root, meta.STANDALONE)[meta.KEY_RESOLVED_PREFIX], "kevin")

    def test_failed_destroy_removes_nothing(self):
        """Live resources must stay discoverable, and the retry must stay possible."""
        output, raised, _export, cleanup, destroy = self.run_down(destroy_ok=False)

        self.assertIsNotNone(raised)
        self.assertEqual(raised.code, 1)
        destroy.assert_called_once()

        self.assertTrue(self.card.exists())
        self.assertTrue(self.marker.exists())
        self.assertEqual(meta.load_meta(self.root, meta.SELFSERVICE)[meta.KEY_RESOLVED_PREFIX], "kevins")
        self.assertIn("F1_CARD=", self.creds_file.read_text())
        # State is what makes the retry work, so it is deliberately kept.
        self.assertTrue(self.state.exists())
        cleanup.assert_not_called()
        self.assertIn("Teardown failed", output)

    def test_no_state_is_a_no_op_not_a_cleanup(self):
        """Nothing was deployed from here — don't delete another run's artifacts."""
        self.state.unlink()
        output, raised, _export, cleanup, destroy = self.run_down(destroy_ok=True)

        self.assertIsNone(raised)
        destroy.assert_not_called()
        cleanup.assert_not_called()
        self.assertTrue(self.card.exists())
        self.assertTrue(self.marker.exists())
        self.assertIn("nothing to destroy", output)


class TeardownEnvTests(unittest.TestCase):
    """`export_selfservice_tf_env` is also `uv run destroy`'s entry point."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        patcher = patch.dict("os.environ", {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_required_tf_vars_are_never_exported_empty(self):
        """TF_VAR_prefix is required, so an empty export would make Terraform prompt.

        Reached only with no state, no metadata, and no derivable identity — but a
        destroy is frequently unattended, and a hung prompt looks like a hang.
        """
        import os

        with (
            patch.object(ss_cli, "get_project_root", return_value=self.root),
            patch.dict("os.environ", {"USER": "root", "LOGNAME": "root"}),
        ):
            ss_cli.export_selfservice_tf_env({k: v for k, v in CREDS.items() if k != "TF_VAR_owner_email"})
            # Asserted inside the patch: patch.dict restores the environment on exit.
            self.assertTrue(os.environ["TF_VAR_prefix"])
            self.assertIsNone(meta.validate_prefix(os.environ["TF_VAR_prefix"]))


if __name__ == "__main__":
    unittest.main()
