"""RTCE Global API keys on credential cards.

The Real-Time Context Engine authenticates with a **Global** Confluent Cloud API
key, which the Terraform provider cannot create — so `workshop creds --rtce-keys`
mints one per attendee via the CLI, against that attendee's own service account.

The behaviour worth pinning down is the replace-not-accumulate rule: Global keys
are capped at 2 per principal and a secret can never be re-read after creation,
so regenerating a card must delete the SA's existing Global keys first. Without
that, the third card regeneration fails and there is no way to recover a working
key.
"""

from __future__ import annotations

import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.workshop import creds as creds_mod


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


CARD_OUT = {
    "organization_id": "org-abc",
    "environment_id": "env-111",
    "environment_name": "RIVER-RACING-f1wp001-ENV",
    "cluster_name": "f1-cluster",
    "cluster_id": "lkc-999",
    "service_account_id": "sa-abc123",
    "attendee_credentials": {"cluster_id": "lkc-999"},
}


class MintTests(unittest.TestCase):
    def test_deletes_existing_global_keys_before_creating(self):
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1:3] == ["api-key", "list"]:
                return _completed(json.dumps([{"key": "OLDKEY1"}, {"key": "OLDKEY2"}]))
            if cmd[1:3] == ["api-key", "create"]:
                return _completed(json.dumps({"api_key": "NEWKEY", "api_secret": "NEWSECRET"}))
            return _completed()

        with patch.object(subprocess, "run", side_effect=fake_run):
            key, secret = creds_mod._mint_rtce_key("sa-abc123", "f1wp001")

        self.assertEqual((key, secret), ("NEWKEY", "NEWSECRET"))
        deletes = [c for c in calls if c[1:3] == ["api-key", "delete"]]
        self.assertEqual([c[3] for c in deletes], ["OLDKEY1", "OLDKEY2"])
        # Both stale keys must be gone before the create, or the 2-per-principal
        # cap rejects it.
        self.assertLess(calls.index(deletes[-1]), [c[1:3] for c in calls].index(["api-key", "create"]))

    def test_scopes_the_create_to_global_and_the_attendee_sa(self):
        with patch.object(
            subprocess,
            "run",
            side_effect=lambda cmd, **kw: _completed(
                json.dumps({"api_key": "K", "api_secret": "S"}) if cmd[1:3] == ["api-key", "create"] else "[]"
            ),
        ) as run:
            creds_mod._mint_rtce_key("sa-abc123", "f1wp001")
        create = next(c.args[0] for c in run.call_args_list if c.args[0][1:3] == ["api-key", "create"])
        self.assertIn("--resource", create)
        self.assertEqual(create[create.index("--resource") + 1], "global")
        self.assertEqual(create[create.index("--service-account") + 1], "sa-abc123")

    def test_a_failed_create_degrades_instead_of_raising(self):
        # A card without RTCE keys still works for every other lab, so a CLI that
        # isn't logged in must not abort a 20-attendee card run.
        with patch.object(subprocess, "run", side_effect=lambda cmd, **kw: _completed("", returncode=1)):
            self.assertEqual(creds_mod._mint_rtce_key("sa-abc123", "f1wp001"), ("", ""))

    def test_a_missing_cli_degrades_instead_of_raising(self):
        with patch.object(subprocess, "run", side_effect=OSError("no confluent binary")):
            self.assertEqual(creds_mod._mint_rtce_key("sa-abc123", "f1wp001"), ("", ""))


class CardFieldTests(unittest.TestCase):
    def test_no_minting_unless_asked(self):
        with patch.object(creds_mod, "_mint_rtce_key") as mint:
            fields = creds_mod._card_fields("f1wp001", "a@b.c", CARD_OUT, region="us-east-1")
        mint.assert_not_called()
        self.assertEqual(fields["rtce_api_key"], "")

    def test_no_minting_without_a_service_account_id(self):
        # The standalone/self-service cards and any pre-existing wsa CSV without
        # the Service Account ID column land here.
        out = {k: v for k, v in CARD_OUT.items() if k != "service_account_id"}
        with patch.object(creds_mod, "_mint_rtce_key") as mint:
            creds_mod._card_fields("f1wp001", "a@b.c", out, region="us-east-1", rtce_keys=True)
        mint.assert_not_called()

    def test_keys_land_on_the_card(self):
        with patch.object(creds_mod, "_mint_rtce_key", return_value=("K1", "S1")):
            fields = creds_mod._card_fields(
                "f1wp001", "a@b.c", dict(CARD_OUT, rtce_api_key="K1", rtce_api_secret="S1"),
                region="us-east-1", rtce_keys=True,
            )
        self.assertEqual(fields["rtce_api_key"], "K1")
        self.assertEqual(fields["rtce_api_secret"], "S1")


class SectionTests(unittest.TestCase):
    def base(self, **overrides) -> dict[str, str]:
        fields = {
            "rtce_api_key": "K1",
            "rtce_api_secret": "S1",
            "rtce_mcp_endpoint": "https://mcp.us-east-1.aws.confluent.cloud/mcp/v1/context-engine/x",
        }
        fields.update(overrides)
        return fields

    def test_token_is_base64_of_key_colon_secret(self):
        import base64

        section = creds_mod._rtce_section(self.base())
        expected = base64.b64encode(b"K1:S1").decode()
        self.assertIn(f'Authorization: Basic {expected}"', section)
        # The raw secret must never appear on its own — the attendee copies the
        # encoded header, not the pair.
        self.assertNotIn("K1:S1", section)

    def test_omitted_without_a_key(self):
        self.assertEqual(creds_mod._rtce_section(self.base(rtce_api_key="")), "")

    def test_omitted_without_an_endpoint(self):
        self.assertEqual(creds_mod._rtce_section(self.base(rtce_mcp_endpoint="")), "")


class DispenserCsvTests(unittest.TestCase):
    """The dispenser shows attendees only four fields — Console URL/Username/
    Password and one paste-ready Env File block. `creds` curates wsa's
    build-output.csv down to them right before `wsa dispenser-upload` reads it,
    which constrains the column set and header text in ways that are invisible
    until an attendee gets a broken claim page — hence the assertions below.
    """

    URL_COL = creds_mod.COLUMNS["console_url"]
    USER_COL = creds_mod.COLUMNS["console_username"]
    PW_COL = creds_mod.COLUMNS["console_password"]
    ENV_COL = creds_mod.ENV_FILE_COLUMN
    PREFIX_COL = creds_mod.COLUMNS["prefix"]
    SECRET_COL = creds_mod.COLUMNS["kafka_api_secret"]

    def _full_row(self, **overrides) -> dict[str, str]:
        """One freshly-built row: wsa's built-ins, every Terraform column, and the
        Env File block `creds` stamps on before curation."""
        row = {
            "Account": "1",
            "Email": "a@b.c",
            self.URL_COL: "https://confluent.cloud/environments/env-111",
            self.USER_COL: "a@b.c",
            self.PW_COL: "(from 1Password)",
            self.PREFIX_COL: "f1wp001",
            self.SECRET_COL: "SUPER-SECRET",
            creds_mod.COLUMNS["environment_id"]: "env-111",
            self.ENV_COL: "F1_PREFIX=f1wp001\nF1_KAFKA_API_SECRET=SUPER-SECRET\n",
        }
        row.update(overrides)
        return row

    def _roundtrip(self, rows: list[dict[str, str]]):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "build-output.csv"
            with path.open("w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
            filled = creds_mod._write_dispenser_csv(path, rows)
            with path.open(newline="") as fh:
                reader = csv.DictReader(fh)
                return filled, reader.fieldnames, list(reader)

    def test_keeps_only_the_four_fields_plus_structural(self):
        filled, headers, out = self._roundtrip([self._full_row()])
        self.assertEqual(filled, 1)
        self.assertEqual(headers, ["Account", "Email", self.URL_COL, self.USER_COL, self.PW_COL, self.ENV_COL])
        # The individual Terraform secrets are dropped from the sheet...
        self.assertNotIn(self.SECRET_COL, headers)
        self.assertNotIn(self.PREFIX_COL, headers)
        # ...but survive inside the paste-ready Env File block.
        self.assertIn("F1_KAFKA_API_SECRET=SUPER-SECRET", out[0][self.ENV_COL])

    def test_preserves_the_password_placeholder(self):
        # wsa resolves "(from 1Password)" itself at upload time; overwriting it with
        # the value we resolved into the cards would leak it into the sheet.
        _, _, out = self._roundtrip([self._full_row()])
        self.assertEqual(out[0][self.PW_COL], "(from 1Password)")

    def test_curation_is_idempotent(self):
        # A curated CSV fed back in yields the same columns and Env File, so a
        # `workshop creds` re-run after an upload still regenerates the cards.
        _, _, once = self._roundtrip([self._full_row()])
        filled, headers, twice = self._roundtrip(once)
        self.assertEqual(headers, ["Account", "Email", self.URL_COL, self.USER_COL, self.PW_COL, self.ENV_COL])
        self.assertEqual(twice[0][self.ENV_COL], once[0][self.ENV_COL])
        self.assertEqual(filled, 1)

    def test_unstamped_row_counts_as_unfilled(self):
        filled, _, out = self._roundtrip([self._full_row(**{self.ENV_COL: ""})])
        self.assertEqual(filled, 0)
        self.assertEqual(out[0][self.ENV_COL], "")

    def test_env_file_header_shape_is_what_the_apps_script_requires(self):
        header = creds_mod.ENV_FILE_COLUMN
        # Code.gs skips any column whose header doesn't split on " / ".
        self.assertIn(" / ", header)
        # wsa's ensureDispenserColumns substring-matches these to decide whether to
        # append its own tracking columns — colliding would suppress them and
        # Code.gs hard-throws without a Claimed By column.
        self.assertNotIn("claimed by", header.lower())
        self.assertNotIn("timestamp", header.lower())


if __name__ == "__main__":
    unittest.main()
