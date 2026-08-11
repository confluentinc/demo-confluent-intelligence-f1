"""Credential-card resolution — the logic that lets attendees skip --creds.

Every case runs against a temp project root, so nothing here touches the real
credentials.env or runs/ directory.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.common.credentials import (
    clear_active_card,
    load_card,
    resolve_card,
    set_active_card,
)

CARD_BODY = "F1_KAFKA_BOOTSTRAP=pkc-x.us-east-1.aws.confluent.cloud:9092\nF1_KAFKA_API_KEY=ABC\n"


class CardResolutionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        # A bare environ keeps a developer's real $F1_CREDS out of the results.
        patcher = patch.dict("os.environ", {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_card(self, run: str, prefix: str) -> Path:
        card = self.root / "runs" / run / "credentials" / f"{prefix}.env"
        card.parent.mkdir(parents=True, exist_ok=True)
        card.write_text(CARD_BODY)
        return card

    def write_creds_env(self, body: str) -> Path:
        path = self.root / "credentials.env"
        path.write_text(body)
        return path

    # --- resolution order ---

    def test_explicit_creds_wins(self):
        self.make_card("standalone", "PROD")
        self.assertEqual(resolve_card("some/other.env", root=self.root), Path("some/other.env"))

    @patch.dict("os.environ", {"F1_CREDS": "from/env.env"})
    def test_env_var_beats_credentials_env(self):
        card = self.make_card("standalone", "PROD")
        set_active_card(self.root, card)

        self.assertEqual(resolve_card(root=self.root), Path("from/env.env"))

    def test_pointer_beats_glob(self):
        self.make_card("selfservice", "solo")
        wanted = self.make_card("standalone", "PROD")
        set_active_card(self.root, wanted)

        self.assertEqual(resolve_card(root=self.root), wanted)

    def test_single_card_is_used_without_a_pointer(self):
        card = self.make_card("standalone", "PROD")

        self.assertEqual(resolve_card(root=self.root), card)

    def test_stale_pointer_falls_through_to_the_glob(self):
        card = self.make_card("standalone", "PROD")
        # What `uv run destroy` leaves behind if the pointer is not cleared.
        self.write_creds_env("F1_CARD=runs/selfservice/credentials/deleted.env\n")

        self.assertEqual(resolve_card(root=self.root), card)

    # --- the two families of credentials.env ---

    def test_credentials_env_holding_f1_keys_is_itself_the_card(self):
        """What f1-onboard writes for workshop attendees."""
        creds = self.write_creds_env(CARD_BODY)

        self.assertEqual(resolve_card(root=self.root), creds)

    def test_tf_var_credentials_env_is_not_mistaken_for_a_card(self):
        self.write_creds_env("TF_VAR_prefix=bren\nTF_VAR_owner_email=a@b.com\n")
        card = self.make_card("standalone", "PROD")

        self.assertEqual(resolve_card(root=self.root), card)

    def test_legacy_workshop_setting_is_not_mistaken_for_a_card(self):
        self.write_creds_env(
            "TF_VAR_prefix=bren\nF1_WORKSHOP_EMAIL_PATTERN=organizer+f1wp{N}@example.com\n"
        )
        card = self.make_card("workshop", "f1wp001")

        self.assertEqual(resolve_card(root=self.root), card)

    def test_loose_root_card_is_found(self):
        """An instructor-handed f1wp001.env dropped in the repo root."""
        card = self.root / "f1wp001.env"
        card.write_text(CARD_BODY)

        self.assertEqual(resolve_card(root=self.root), card)

    def test_unrelated_root_env_files_are_not_candidates(self):
        (self.root / "confluent-mcp.env").write_text("MCP_URL=https://example.com\n")
        self.write_creds_env("TF_VAR_prefix=bren\n")
        card = self.make_card("standalone", "PROD")

        self.assertEqual(resolve_card(root=self.root), card)

    def test_loose_card_competing_with_a_run_card_is_ambiguous(self):
        (self.root / "f1wp001.env").write_text(CARD_BODY)
        self.make_card("standalone", "PROD")

        with self.assertRaises(SystemExit) as ctx:
            resolve_card(root=self.root)

        self.assertIn("f1wp001.env", str(ctx.exception))

    def test_pointer_alone_does_not_make_credentials_env_a_card(self):
        """F1_CARD is a pointer, not card content — a dead one must not resolve to self."""
        self.write_creds_env("TF_VAR_prefix=bren\nF1_CARD=runs/standalone/credentials/gone.env\n")

        with self.assertRaises(SystemExit):
            resolve_card(root=self.root)

    # --- failure modes ---

    def test_no_cards_exits_with_guidance(self):
        with self.assertRaises(SystemExit) as ctx:
            resolve_card(root=self.root)

        self.assertIn("uv run deploy", str(ctx.exception))

    def test_ambiguous_cards_exit_listing_every_candidate(self):
        self.make_card("standalone", "PROD")
        self.make_card("selfservice", "solo")

        with self.assertRaises(SystemExit) as ctx:
            resolve_card(root=self.root)

        message = str(ctx.exception)
        self.assertIn("standalone/credentials/PROD.env", message)
        self.assertIn("selfservice/credentials/solo.env", message)

    def test_load_card_exits_when_the_explicit_path_is_missing(self):
        with self.assertRaises(SystemExit) as ctx:
            load_card("nope.env", root=self.root)

        self.assertIn("not found", str(ctx.exception))

    def test_load_card_parses_the_resolved_file(self):
        card = self.make_card("standalone", "PROD")

        path, values = load_card(root=self.root)

        self.assertEqual(path, card)
        self.assertEqual(values["F1_KAFKA_API_KEY"], "ABC")


class ActiveCardWriteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.card = self.root / "runs" / "standalone" / "credentials" / "PROD.env"
        self.card.parent.mkdir(parents=True)
        self.card.write_text(CARD_BODY)
        self.creds = self.root / "credentials.env"

    def test_appends_a_relative_pointer_and_keeps_existing_lines(self):
        self.creds.write_text("# deploy secrets\nTF_VAR_prefix=bren\n")

        set_active_card(self.root, self.card)

        self.assertEqual(
            self.creds.read_text(),
            "# deploy secrets\nTF_VAR_prefix=bren\nF1_CARD=runs/standalone/credentials/PROD.env\n",
        )

    def test_replaces_rather_than_duplicates_an_existing_pointer(self):
        self.creds.write_text("F1_CARD=runs/selfservice/credentials/solo.env\nTF_VAR_prefix=bren\n")

        set_active_card(self.root, self.card)

        self.assertEqual(
            self.creds.read_text(),
            "F1_CARD=runs/standalone/credentials/PROD.env\nTF_VAR_prefix=bren\n",
        )

    def test_handles_a_file_with_no_trailing_newline(self):
        self.creds.write_text("TF_VAR_prefix=bren")

        set_active_card(self.root, self.card)

        self.assertEqual(
            self.creds.read_text(),
            "TF_VAR_prefix=bren\nF1_CARD=runs/standalone/credentials/PROD.env\n",
        )

    def test_creates_credentials_env_when_absent(self):
        set_active_card(self.root, self.card)

        self.assertEqual(self.creds.read_text(), "F1_CARD=runs/standalone/credentials/PROD.env\n")

    def test_clear_removes_only_the_pointer(self):
        set_active_card(self.root, self.card)

        clear_active_card(self.root)

        self.assertEqual(self.creds.read_text(), "")

    def test_scoped_clear_leaves_another_deployments_pointer_alone(self):
        """`selfservice down` must not unset a pointer aimed at the standalone card."""
        set_active_card(self.root, self.card)

        clear_active_card(self.root, only_if_under=self.root / "runs" / "selfservice")

        self.assertIn("F1_CARD=", self.creds.read_text())

    def test_scoped_clear_removes_a_matching_pointer(self):
        set_active_card(self.root, self.card)

        clear_active_card(self.root, only_if_under=self.root / "runs" / "standalone")

        self.assertNotIn("F1_CARD=", self.creds.read_text())

    def test_clear_is_a_no_op_without_a_credentials_file(self):
        clear_active_card(self.root)

        self.assertFalse(self.creds.exists())


if __name__ == "__main__":
    unittest.main()
