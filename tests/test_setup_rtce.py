"""`uv run setup-rtce` — Codex config.toml merge and Claude Code registration.

Nothing here touches the developer's real ``~/.codex/config.toml``: every test
points ``write_codex_config`` at a path inside a temp directory.
"""

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import setup_rtce
from scripts.setup_rtce import basic_token, warn_on_empty_card_fields, write_codex_config

ENDPOINT = "https://mcp.us-east-1.aws.confluent.cloud/mcp/v1/context-engine/organizations/org/environments/env/kafka-clusters/lkc"
TOKEN = basic_token("KEY", "SECRET")


class WriteCodexConfigTests(unittest.TestCase):
    def test_creates_file_when_missing(self):
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / ".codex" / "config.toml"
            self.assertFalse(config_path.exists())

            with redirect_stdout(io.StringIO()):
                ok = write_codex_config(ENDPOINT, TOKEN, config_path)

            self.assertTrue(ok)
            text = config_path.read_text()
            self.assertIn("[mcp_servers.real-time-context-engine]", text)
            self.assertIn(ENDPOINT, text)
            self.assertIn(f"Basic {TOKEN}", text)

    def test_preserves_unrelated_existing_content(self):
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.toml"
            config_path.write_text('# a comment worth keeping\n[mcp_servers.other]\ncommand = "some-other-server"\n')

            with redirect_stdout(io.StringIO()):
                write_codex_config(ENDPOINT, TOKEN, config_path)

            text = config_path.read_text()
            self.assertIn("# a comment worth keeping", text)
            self.assertIn("[mcp_servers.other]", text)
            self.assertIn("some-other-server", text)
            self.assertIn("[mcp_servers.real-time-context-engine]", text)

    def test_rerun_replaces_existing_rtce_entry(self):
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.toml"

            with redirect_stdout(io.StringIO()):
                write_codex_config("https://old-endpoint", basic_token("OLD", "SECRET"), config_path)
                write_codex_config(ENDPOINT, TOKEN, config_path)

            text = config_path.read_text()
            self.assertNotIn("old-endpoint", text)
            self.assertIn(ENDPOINT, text)
            self.assertEqual(text.count("[mcp_servers.real-time-context-engine]"), 1)

    def test_dry_run_does_not_write(self):
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / ".codex" / "config.toml"

            with redirect_stdout(io.StringIO()) as out:
                ok = write_codex_config(ENDPOINT, TOKEN, config_path, dry_run=True)

            self.assertTrue(ok)
            self.assertFalse(config_path.exists())
            self.assertIn("dry-run", out.getvalue())

    def test_falls_back_to_snippet_on_unparseable_existing_file(self):
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.toml"
            config_path.write_text("this is not [ valid toml at all\n===\n")

            with redirect_stdout(io.StringIO()) as out:
                ok = write_codex_config(ENDPOINT, TOKEN, config_path)

            self.assertTrue(ok)
            self.assertIn("couldn't auto-edit", out.getvalue())
            self.assertIn(ENDPOINT, out.getvalue())
            # the broken file is left untouched, not overwritten
            self.assertIn("not [ valid toml", config_path.read_text())


class EmptyCardHintTests(unittest.TestCase):
    def test_regenerate_hint_names_every_card_writing_deploy_track(self):
        with redirect_stdout(io.StringIO()) as out:
            missing = warn_on_empty_card_fields({})
        self.assertIn("F1_RTCE_API_KEY", missing)
        self.assertIn("uv run deploy --track self-service", out.getvalue())
        self.assertIn("uv run deploy --track pre-provisioned", out.getvalue())


PREPROVISIONED_CARD = {
    "F1_ENVIRONMENT_ID": "env-mine",
    "F1_CLUSTER_ID": "lkc-mine",
    "F1_RTCE_API_KEY": "GLOBALKEY",
    "F1_RTCE_API_SECRET": "GLOBALSECRET",
}


class PreprovisionedCardTests(unittest.TestCase):
    """The pre-provisioned track runs no Terraform; its card carries the developer's Global key."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        # Left behind by the old developer Terraform root: never read again.
        state = root / "terraform" / "pre-provisioned" / "terraform.tfstate"
        state.parent.mkdir(parents=True)
        state.write_text("{}")
        for p in (
            patch.object(setup_rtce, "get_project_root", return_value=root),
            patch.object(setup_rtce.subprocess, "run", side_effect=AssertionError("terraform called")),
            patch("builtins.input", side_effect=AssertionError("prompted")),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_no_terraform_state_is_scanned_for_it(self):
        self.assertEqual(setup_rtce.terraform_rtce_outputs(PREPROVISIONED_CARD), {})

    def test_the_cards_global_key_is_used_as_is(self):
        resolved = setup_rtce.resolve_rtce_credentials(PREPROVISIONED_CARD)
        self.assertEqual((resolved["F1_RTCE_API_KEY"], resolved["F1_RTCE_API_SECRET"]), ("GLOBALKEY", "GLOBALSECRET"))


if __name__ == "__main__":
    unittest.main()
