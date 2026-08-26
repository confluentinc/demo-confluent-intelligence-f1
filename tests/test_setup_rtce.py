"""`uv run setup-rtce` — Codex config.toml merge and Claude Code registration.

Nothing here touches the developer's real ``~/.codex/config.toml``: every test
points ``write_codex_config`` at a path inside a temp directory.
"""

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.setup_rtce import basic_token, write_codex_config

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
            self.assertIn('[mcp_servers.real-time-context-engine]', text)
            self.assertIn(ENDPOINT, text)
            self.assertIn(f"Basic {TOKEN}", text)

    def test_preserves_unrelated_existing_content(self):
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.toml"
            config_path.write_text(
                "# a comment worth keeping\n"
                "[mcp_servers.other]\n"
                'command = "some-other-server"\n'
            )

            with redirect_stdout(io.StringIO()):
                write_codex_config(ENDPOINT, TOKEN, config_path)

            text = config_path.read_text()
            self.assertIn("# a comment worth keeping", text)
            self.assertIn("[mcp_servers.other]", text)
            self.assertIn("some-other-server", text)
            self.assertIn('[mcp_servers.real-time-context-engine]', text)

    def test_rerun_replaces_existing_rtce_entry(self):
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.toml"

            with redirect_stdout(io.StringIO()):
                write_codex_config("https://old-endpoint", basic_token("OLD", "SECRET"), config_path)
                write_codex_config(ENDPOINT, TOKEN, config_path)

            text = config_path.read_text()
            self.assertNotIn("old-endpoint", text)
            self.assertIn(ENDPOINT, text)
            self.assertEqual(text.count('[mcp_servers.real-time-context-engine]'), 1)

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


if __name__ == "__main__":
    unittest.main()
