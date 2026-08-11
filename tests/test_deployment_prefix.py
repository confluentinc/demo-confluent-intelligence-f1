"""Prefix derivation, reuse, and cross-track isolation for the two solo tracks.

The bug these cover: the prefix used to be prompted with "e.g. demo or your
initials" and defaulted to the root ``credentials.env``, which both tracks wrote.
Two people got the same name (and, worse, the same account-global ECR repository
name), and after either track ran the other inherited its prefix.

Everything here runs against a temp project root and a scrubbed environment, so
no test reads the developer's real credentials.env, runs/, or $USER.
"""

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import deploy
from scripts.common import deployment_meta as meta
from scripts.selfservice import cli as ss_cli

EMAIL = "kevin@example.com"

# Enough of credentials.env to get `--automated` past its required-values check.
AUTOMATED_CREDS = {
    "TF_VAR_confluent_cloud_api_key": "CCKEY",
    "TF_VAR_confluent_cloud_api_secret": "CCSECRET",
    "TF_VAR_owner_email": EMAIL,
    "TF_VAR_aws_bedrock_access_key": "AKIAEXAMPLE",
    "TF_VAR_aws_bedrock_secret_key": "BEDROCKSECRET",
}


class PrefixTestCase(unittest.TestCase):
    """Temp root + empty environment, so identity is whatever the test says."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        patcher = patch.dict("os.environ", {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def as_user(self, login: str):
        """Both are read by derive_base_prefix, so both have to be set."""
        return patch.dict("os.environ", {"USER": login, "LOGNAME": login})


class DerivationTests(PrefixTestCase):
    def test_two_identities_get_different_prefixes(self):
        with self.as_user("alice"):
            alice, _ = deploy._resolve_prefix(self.root, EMAIL, None)
        with self.as_user("bob"):
            bob, _ = deploy._resolve_prefix(self.root, EMAIL, None)

        self.assertEqual(alice, "alice")
        self.assertEqual(bob, "bob")
        self.assertNotEqual(alice, bob)
        # And each is usable as-is: no prompt, no cloud call, no collision.
        self.assertIsNone(meta.validate_prefix(alice))
        self.assertIsNone(meta.validate_prefix(bob))

    def test_shared_login_falls_back_to_the_owner_email(self):
        """On a shared box $USER names the machine, so it cannot be the identity."""
        with self.as_user("ec2-user"):
            one, source = deploy._resolve_prefix(self.root, "one@example.com", None)
            two, _ = deploy._resolve_prefix(self.root, "two@example.com", None)

        self.assertEqual(source, "derived")
        self.assertNotEqual(one, two)
        self.assertIsNone(meta.validate_prefix(one))
        self.assertIsNone(meta.validate_prefix(two))

    def test_no_identity_at_all_is_reported_not_invented(self):
        """An empty derivation must fail loudly rather than collide silently."""
        with self.as_user("root"):
            prefix, _ = deploy._resolve_prefix(self.root, "", None)
        self.assertEqual(prefix, "")
        with self.assertRaises(SystemExit):
            deploy._validated_prefix_or_exit(prefix)

    def test_rerun_reuses_the_saved_prefix(self):
        with self.as_user("alice"):
            first, first_source = deploy._resolve_prefix(self.root, EMAIL, None)
            meta.save_meta(self.root, meta.STANDALONE, **{meta.KEY_RESOLVED_PREFIX: first})
            # A different login on the same checkout must not rename a deployment.
            with self.as_user("someoneelse"):
                second, second_source = deploy._resolve_prefix(self.root, EMAIL, None)

        self.assertEqual(first_source, "derived")
        self.assertEqual(second_source, "saved")
        self.assertEqual(first, second)

    def test_live_state_wins_and_refuses_to_be_switched(self):
        with patch.object(meta, "prefix_from_state", return_value="deployed"):
            reused, source = deploy._resolve_prefix(self.root, EMAIL, None)
            self.assertEqual((reused, source), ("deployed", "state"))

            with self.assertRaises(SystemExit):
                deploy._resolve_prefix(self.root, EMAIL, "somethingelse")


class CrossTrackTests(PrefixTestCase):
    def test_tracks_in_one_checkout_get_distinct_prefixes(self):
        with self.as_user("alice"):
            standalone, _ = deploy._resolve_prefix(self.root, EMAIL, None)
            selfservice, _ = ss_cli._resolve_prefix(self.root, EMAIL, None)

        self.assertEqual(standalone, "alice")
        self.assertEqual(selfservice, "alices")
        self.assertNotEqual(standalone, selfservice)

    def test_metadata_is_per_track(self):
        meta.save_meta(self.root, meta.STANDALONE, **{meta.KEY_RESOLVED_PREFIX: "alice"})
        meta.save_meta(self.root, meta.SELFSERVICE, **{meta.KEY_RESOLVED_PREFIX: "alices"})

        self.assertEqual(meta.load_meta(self.root, meta.STANDALONE)[meta.KEY_RESOLVED_PREFIX], "alice")
        self.assertEqual(meta.load_meta(self.root, meta.SELFSERVICE)[meta.KEY_RESOLVED_PREFIX], "alices")

        # Retiring one track must not touch the other's metadata.
        meta.retire_track(self.root, meta.SELFSERVICE)
        self.assertEqual(meta.load_meta(self.root, meta.STANDALONE)[meta.KEY_RESOLVED_PREFIX], "alice")
        self.assertEqual(meta.load_meta(self.root, meta.SELFSERVICE), {})

    def test_credentials_env_prefix_is_not_treated_as_explicit(self):
        """The shared root file must not steer either track's prefix.

        `uv run deploy` writes TF_VAR_prefix there for the destroy path, so
        honoring it would hand standalone's name to self-service (and give two
        people whose file says `solo` the same ECR repository).
        """
        self.assertIsNone(deploy._explicit_prefix())
        self.assertIsNone(ss_cli._explicit_prefix())
        with patch.dict("os.environ", {"TF_VAR_prefix": "exported"}):
            self.assertEqual(deploy._explicit_prefix(), "exported")

    def test_automated_deploy_ignores_a_stale_credentials_env_prefix(self):
        creds = dict(AUTOMATED_CREDS, TF_VAR_prefix="solo")
        creds_file = self.root / "credentials.env"
        creds_file.write_text("")

        with self.as_user("alice"):
            cfg = deploy._collect_config(self.root, creds_file, creds, automated=True)

        self.assertEqual(cfg["prefix"], "alice")
        self.assertEqual(meta.load_meta(self.root, meta.STANDALONE)[meta.KEY_RESOLVED_PREFIX], "alice")

    def test_selfservice_teardown_env_uses_its_own_prefix(self):
        """`export_selfservice_tf_env` keeps its signature but not its source.

        Reading `creds["TF_VAR_prefix"]` pointed a self-service destroy at
        standalone's resource names on any machine that used both tracks.
        """
        meta.save_meta(self.root, meta.SELFSERVICE, **{meta.KEY_RESOLVED_PREFIX: "alices"})
        creds = dict(AUTOMATED_CREDS, TF_VAR_prefix="standalonename")

        with patch.object(ss_cli, "get_project_root", return_value=self.root):
            ss_cli.export_selfservice_tf_env(creds)

        import os

        self.assertEqual(os.environ["TF_VAR_prefix"], "alices")


class SharedTierPrefixTests(PrefixTestCase):
    """Item 10: the account-global ECR repository name."""

    def test_shared_prefix_follows_the_resolved_prefix(self):
        self.assertEqual(deploy._shared_prefix("alice"), "f1-alice")
        self.assertEqual(deploy._shared_prefix("bob"), "f1-bob")
        # Two people, two repositories — the hard RepositoryAlreadyExistsException
        # only happened because this used to be a constant.
        self.assertNotEqual(deploy._shared_prefix("alice"), deploy._shared_prefix("bob"))

    def test_shared_prefix_env_override_pins_a_name(self):
        with patch.dict("os.environ", {deploy.SHARED_PREFIX_ENV: deploy.LEGACY_SHARED_PREFIX}):
            self.assertEqual(deploy._shared_prefix("alice"), "f1-workshop")

    def test_existing_shared_name_is_recovered_from_the_image_uri(self):
        state = self.root / "terraform.tfstate"
        state.write_text("{}")
        uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/f1-workshop-simulator:abc123def456"
        with patch.object(deploy, "run_terraform_output", return_value={"ecr_image_uri": uri}):
            self.assertEqual(deploy._deployed_shared_prefix(state), "f1-workshop")

    def test_no_shared_state_means_nothing_to_migrate(self):
        self.assertIsNone(deploy._deployed_shared_prefix(self.root / "absent.tfstate"))

    def test_automated_refuses_to_migrate_an_existing_shared_name(self):
        """Renaming deletes+recreates the ECR repo, rebuilds the image, and revises
        the task definition — which restarts a running race. Not unattended."""
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as raised:
            deploy._confirm_shared_rename("f1-workshop", "f1-alice", automated=True)

        self.assertEqual(raised.exception.code, 1)
        message = out.getvalue()
        self.assertIn(f"export {deploy.SHARED_PREFIX_ENV}=f1-workshop", message)
        self.assertIn("Refusing to migrate unattended", message)

    def test_interactive_rename_needs_a_yes(self):
        with patch("builtins.input", return_value="n"):
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as raised:
                deploy._confirm_shared_rename("f1-workshop", "f1-alice", automated=False)
        self.assertEqual(raised.exception.code, 0)

        with patch("builtins.input", return_value="y"), redirect_stdout(io.StringIO()):
            self.assertIsNone(deploy._confirm_shared_rename("f1-workshop", "f1-alice", automated=False))

    def test_state_disagreeing_with_saved_metadata_is_reported(self):
        """Item 8 wants the mismatch surfaced, not silently corrected."""
        meta.save_meta(self.root, meta.STANDALONE, **{meta.KEY_RESOLVED_PREFIX: "stale"})
        out = io.StringIO()
        with patch.object(meta, "prefix_from_state", return_value="deployed"), redirect_stdout(out):
            prefix, source = deploy._resolve_prefix(self.root, EMAIL, None)

        self.assertEqual((prefix, source), ("deployed", "state"))
        self.assertIn("'stale'", out.getvalue())
        self.assertIn("'deployed'", out.getvalue())

    def test_unreadable_shared_state_is_not_reported_as_a_match(self):
        state = self.root / "terraform.tfstate"
        state.write_text("{}")
        with patch.object(deploy, "run_terraform_output", side_effect=RuntimeError("no terraform")):
            self.assertIsNone(deploy._deployed_shared_prefix(state))


if __name__ == "__main__":
    unittest.main()
