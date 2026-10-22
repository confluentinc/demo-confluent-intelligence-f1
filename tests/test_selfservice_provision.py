"""The merged `uv run deploy` / `uv run destroy` around the self-service track.

- `pick_track`: one entry point, two tracks. `--automated` must never guess
  between them, or an unattended run could deploy the wrong architecture.
- Teardown cleanup: once `uv run destroy` leaves nothing deployed, the MCP
  registrations and (by default) credentials.env go with it — but never while
  another deployment still needs them.
"""

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dotenv import dotenv_values

from scripts.common import deploy_common as common
from scripts.common import deployment_meta as meta
from scripts.common import destroy
from scripts.selfservice import provision as ss_cli

CREDS = {
    "TF_VAR_confluent_cloud_api_key": "CCKEY",
    "TF_VAR_confluent_cloud_api_secret": "CCSECRET",
    "TF_VAR_owner_email": "kevin@example.com",
    "TF_VAR_aws_bedrock_access_key": "AKIAEXAMPLE",
    "TF_VAR_aws_bedrock_secret_key": "BEDROCKSECRET",
}


class TempRoot:
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def make_state(self, tier: str) -> None:
        state = self.root / "terraform" / tier / "terraform.tfstate"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("{}")


class PickTrackTests(TempRoot, unittest.TestCase):
    def test_explicit_track_wins(self):
        self.assertIs(common.pick_track(self.root, "standalone", automated=True), meta.STANDALONE)
        self.assertIs(common.pick_track(self.root, "self-service", automated=False), meta.SELFSERVICE)

    def test_automated_infers_the_only_deployed_track(self):
        self.make_state("aws")
        self.assertIs(common.pick_track(self.root, None, automated=True), meta.STANDALONE)

    def test_automated_refuses_to_guess(self):
        for tiers in ([], ["aws", "self-service"]):
            for tier in tiers:
                self.make_state(tier)
            with self.subTest(tiers=tiers), redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                common.pick_track(self.root, None, automated=True)

    def test_prompt_defaults_to_the_deployed_track(self):
        self.make_state("aws")
        with patch("builtins.input", return_value=""), redirect_stdout(io.StringIO()):
            self.assertIs(common.pick_track(self.root, None, automated=False), meta.STANDALONE)
        with patch("builtins.input", return_value="2"), redirect_stdout(io.StringIO()):
            self.assertIs(common.pick_track(self.root, None, automated=False), meta.SELFSERVICE)

    def test_fresh_checkout_defaults_to_self_service(self):
        with patch("builtins.input", return_value=""), redirect_stdout(io.StringIO()):
            self.assertIs(common.pick_track(self.root, None, automated=False), meta.SELFSERVICE)


class LlmProviderTests(TempRoot, unittest.TestCase):
    """credentials.env holds one provider for both tracks; live state must win over it."""

    def write_state(self, tier: str, *names: str) -> None:
        import json

        state = self.root / "terraform" / tier / "terraform.tfstate"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"resources": [{"name": n, "instances": [{}]} for n in names]}))

    def setUp(self):
        super().setUp()
        patcher = patch.dict("os.environ", {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_deployed_provider_read_from_state(self):
        self.write_state("aws", "llm_textgen_model", "bedrock_textgen_connection")
        self.write_state("self-service", "llm_textgen_model")
        self.assertEqual(common.deployed_llm_provider(self.root, meta.STANDALONE), "bedrock")
        self.assertEqual(common.deployed_llm_provider(self.root, meta.SELFSERVICE), "confluent")

    def test_other_tracks_choice_never_swaps_a_live_model(self):
        self.write_state("aws", "llm_textgen_model", "bedrock_textgen_connection")
        with redirect_stdout(io.StringIO()):
            got = common.llm_provider_for(self.root, meta.STANDALONE, {"TF_VAR_llm_provider": "confluent"})
        self.assertEqual(got, "bedrock")

    def test_export_still_switches_deliberately(self):
        self.write_state("aws", "llm_textgen_model", "bedrock_textgen_connection")
        with patch.dict("os.environ", {"TF_VAR_llm_provider": "confluent"}):
            got = common.llm_provider_for(self.root, meta.STANDALONE, {})
        self.assertEqual(got, "confluent")

    def test_nothing_deployed_uses_credentials_env(self):
        self.assertEqual(
            common.llm_provider_for(self.root, meta.SELFSERVICE, {"TF_VAR_llm_provider": "confluent"}), "confluent"
        )
        self.assertEqual(common.llm_provider_for(self.root, meta.SELFSERVICE, {}), "confluent")


class TeardownCleanupTests(TempRoot, unittest.TestCase):
    """`uv run destroy` on a self-service-only checkout, end to end with Terraform stubbed."""

    def setUp(self):
        super().setUp()
        self.make_state("self-service")
        self.creds_file = self.root / "credentials.env"
        self.creds_file.write_text("TF_VAR_owner_email=kevin@example.com\n")
        (self.root / destroy.MCP_ENV_FILE).write_text("SECRET=1\n")

    def run_destroy(self, argv: list[str], answers: list[str], destroy_ok: bool = True):
        def fake_destroy(env_path, *_a, **_kw):
            if destroy_ok:
                (env_path / "terraform.tfstate").unlink()
            return destroy_ok

        calls = []
        with (
            patch.object(destroy, "get_project_root", return_value=self.root),
            patch.object(destroy, "run_terraform_destroy", side_effect=fake_destroy),
            patch.object(destroy, "cleanup_terraform_artifacts"),
            patch("scripts.selfservice.provision.get_project_root", return_value=self.root),
            patch.object(destroy.shutil, "which", return_value="/usr/bin/claude"),
            patch.object(destroy.subprocess, "run", side_effect=lambda argv, **kw: _record(calls, argv)),
            patch("builtins.input", side_effect=answers),
            redirect_stdout(io.StringIO()) as out,
        ):
            code = 0
            try:
                destroy.main(argv)
            except SystemExit as e:
                code = e.code
        return code, calls, out.getvalue()

    def test_enter_deletes_credentials_and_mcp_registrations(self):
        code, calls, _out = self.run_destroy([], answers=["y", ""])
        self.assertEqual(code, 0)
        self.assertFalse(self.creds_file.exists())
        self.assertFalse((self.root / destroy.MCP_ENV_FILE).exists())
        self.assertEqual(calls, [["claude", "mcp", "remove", name, "-s", "local"] for name in destroy.MCP_SERVERS])

    def test_no_keeps_credentials(self):
        code, _calls, out = self.run_destroy([], answers=["y", "n"])
        self.assertEqual(code, 0)
        self.assertTrue(self.creds_file.exists())
        self.assertIn("Kept credentials.env", out)

    def test_force_never_prompts_and_keeps_credentials(self):
        code, _calls, _out = self.run_destroy(["--force"], answers=[])
        self.assertEqual(code, 0)
        self.assertTrue(self.creds_file.exists())

    def test_force_with_delete_credentials(self):
        self.run_destroy(["--force", "--delete-credentials"], answers=[])
        self.assertFalse(self.creds_file.exists())

    def test_failed_destroy_touches_nothing(self):
        code, calls, _out = self.run_destroy(["--force", "--delete-credentials"], answers=[], destroy_ok=False)
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])
        self.assertTrue(self.creds_file.exists())
        self.assertTrue((self.root / destroy.MCP_ENV_FILE).exists())

    def test_another_live_deployment_keeps_everything(self):
        self.make_state("aws")
        self.make_state("aws-shared")
        # Two groups -> selection prompt; pick only self-service (group 2). The
        # credentials prompt still comes, but Enter now means keep.
        code, calls, out = self.run_destroy([], answers=["2", "y", ""])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertTrue(self.creds_file.exists())
        self.assertIn("also backs a deployment that is still running", out)

    def test_only_self_service_never_offers_the_aws_group(self):
        self.make_state("aws")
        self.make_state("aws-shared")
        # No selection prompt: --only leaves one group. Then confirm + keep creds.
        code, _calls, out = self.run_destroy(["--only", "self-service"], answers=["y", ""])
        self.assertEqual(code, 0)
        self.assertNotIn("aws-shared", out.split("Will destroy:")[1].splitlines()[0])
        self.assertTrue((self.root / "terraform" / "aws" / "terraform.tfstate").exists())


class SelfServiceShortcutTests(unittest.TestCase):
    """`uv run selfservice up|down` must forward, not reimplement."""

    def test_up_forwards_to_deploy(self):
        from scripts.selfservice import cli

        with patch.object(cli.deploy, "main") as main:
            cli.main(["up", "--automated", "--with-labs"])
        main.assert_called_once_with(["--track", "self-service", "--automated", "--with-labs"])

    def test_down_forwards_to_destroy(self):
        from scripts.selfservice import cli

        with patch.object(cli.destroy, "main") as main:
            cli.main(["down", "--yes"])
        main.assert_called_once_with(["--only", "self-service", "--yes"])


class _Done:
    def __init__(self, returncode: int):
        self.returncode, self.stdout, self.stderr = returncode, "", ""


def _record(calls: list, argv: list[str]) -> _Done:
    """Record `claude mcp` calls; fail anything else (terraform output on a fake state)."""
    if argv[0] == "claude":
        calls.append(argv)
        return _Done(0)
    return _Done(1)


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

    def test_automated_persist_writes_llm_provider_to_credentials_env(self):
        """credentials.env is the source of truth for user choices. `--automated` skips
        the secret writes, but must still record the provider, or a later run without
        the export would swap a CSP-hosted model back to Bedrock."""
        import os

        creds_file = self.root / "credentials.env"
        creds_file.write_text("")
        cfg = {"owner_email": "a@example.com", "prefix": "CSP", "seconds_per_lap": "20", "llm_provider": "confluent"}
        ss_cli._persist(self.root, creds_file, cfg, interactive=False)
        saved = {k: v for k, v in dotenv_values(creds_file).items() if v is not None}
        self.assertEqual(saved, {"TF_VAR_llm_provider": "confluent"})
        with patch.object(ss_cli, "get_project_root", return_value=self.root):
            ss_cli.export_selfservice_tf_env({**CREDS, **saved})
            self.assertEqual(os.environ["TF_VAR_llm_provider"], "confluent")


if __name__ == "__main__":
    unittest.main()
