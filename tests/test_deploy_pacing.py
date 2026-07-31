"""`--automated` pacing: validated with a message, before anything cloud-shaped runs.

`deploy.py` used to call `int(seconds_per_lap)` unguarded in the automated path, so
a stale `TF_VAR_seconds_per_lap=fast` produced a raw `ValueError` traceback — and
only *after* the Docker and AWS probes had already run. It also persisted pacing
only in the interactive branch, so
`export TF_VAR_seconds_per_lap=15; uv run deploy --automated` applied 15 and then
forgot it.
"""

import io
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import deploy
from scripts.common import deployment_meta as meta
from scripts.selfservice import race

CREDS = {
    "TF_VAR_confluent_cloud_api_key": "CCKEY",
    "TF_VAR_confluent_cloud_api_secret": "CCSECRET",
    "TF_VAR_owner_email": "kevin@example.com",
    "TF_VAR_aws_bedrock_access_key": "AKIAEXAMPLE",
    "TF_VAR_aws_bedrock_secret_key": "BEDROCKSECRET",
}


class PacingTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        patcher = patch.dict("os.environ", {"USER": "kevin", "LOGNAME": "kevin"}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.creds_file = self.root / "credentials.env"
        self.creds_file.write_text("")


class ValidationTests(PacingTestCase):
    def test_non_numeric_pacing_is_rejected_with_a_message(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as raised:
            deploy._validated_pacing_or_exit("fast")

        self.assertEqual(raised.exception.code, 1)
        message = out.getvalue()
        self.assertIn("'fast' is not a whole number", message)
        self.assertIn("TF_VAR_seconds_per_lap", message)

    def test_pacing_below_the_minimum_is_rejected(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            deploy._validated_pacing_or_exit("3")
        self.assertIn(f"below the {meta.MIN_SECONDS_PER_LAP}s minimum", out.getvalue())

    def test_good_pacing_is_normalised(self):
        self.assertEqual(deploy._validated_pacing_or_exit(" 30 "), "30")


class AutomatedPathTests(PacingTestCase):
    def test_automated_pacing_is_persisted_so_it_sticks(self):
        with patch.dict("os.environ", {"TF_VAR_seconds_per_lap": "15"}):
            cfg = deploy._collect_config(self.root, self.creds_file, dict(CREDS), automated=True)

        self.assertEqual(cfg["seconds_per_lap"], "15")
        self.assertEqual(meta.load_meta(self.root, meta.STANDALONE)[meta.KEY_SECONDS_PER_LAP], "15")
        # A later run with nothing exported reuses it.
        self.assertEqual(deploy._seconds_per_lap_default(self.root, {}), "15")

    def test_saved_pacing_beats_credentials_env_but_not_the_environment(self):
        meta.save_meta(self.root, meta.STANDALONE, **{meta.KEY_SECONDS_PER_LAP: "25"})
        self.assertEqual(deploy._seconds_per_lap_default(self.root, {"TF_VAR_seconds_per_lap": "40"}), "25")
        with patch.dict("os.environ", {"TF_VAR_seconds_per_lap": "50"}):
            self.assertEqual(deploy._seconds_per_lap_default(self.root, {}), "50")


class NoCloudWorkBeforeValidationTests(PacingTestCase):
    def test_bad_pacing_exits_before_docker_aws_or_terraform(self):
        """The whole point: fail on the input, not four probes later."""
        self.creds_file.write_text("\n".join(f"{k}={v}" for k, v in CREDS.items()) + "\n")

        with (
            patch.object(deploy, "get_project_root", return_value=self.root),
            patch.object(deploy, "check_terraform_installed", return_value=True),
            patch.object(deploy, "check_docker_running") as docker,
            patch.object(deploy, "check_aws_configured") as aws,
            patch.object(deploy, "ensure_confluent_login") as login,
            patch.object(deploy, "run_terraform") as terraform,
            patch("sys.argv", ["deploy", "--automated"]),
            patch.dict("os.environ", {"TF_VAR_seconds_per_lap": "fast"}),
        ):
            out = io.StringIO()
            with redirect_stdout(out), self.assertRaises(SystemExit) as raised:
                deploy.main()

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("not a whole number", out.getvalue())
        docker.assert_not_called()
        aws.assert_not_called()
        login.assert_not_called()
        terraform.assert_not_called()

    def test_automated_never_touches_the_confluent_cli(self):
        """Terraform's provider authenticates with the API key; the CLI is only
        needed to *mint* one, which --automated never does."""
        self.creds_file.write_text("\n".join(f"{k}={v}" for k, v in CREDS.items()) + "\n")

        with (
            patch.object(deploy, "get_project_root", return_value=self.root),
            patch.object(deploy, "check_terraform_installed", return_value=True),
            patch.object(deploy, "check_docker_running", return_value=False),
            patch.object(deploy, "ensure_confluent_login") as login,
            patch("sys.argv", ["deploy", "--automated"]),
        ):
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                deploy.main()

        login.assert_not_called()


class RaceCommandTests(PacingTestCase):
    """`f1-race`: the `--N` shorthand, the minimum guard, and the dead warmup."""

    def make_card(self, track: meta.Track, prefix: str) -> Path:
        card = self.root / "runs" / track.name / "credentials" / f"{prefix}.env"
        card.parent.mkdir(parents=True, exist_ok=True)
        card.write_text(
            "\n".join(
                [
                    "F1_KAFKA_BOOTSTRAP=SASL_SSL://pkc-x.us-east-1.aws.confluent.cloud:9092",
                    "F1_KAFKA_API_KEY=KK",
                    "F1_KAFKA_API_SECRET=KS",
                    "F1_SCHEMA_REGISTRY_URL=https://psrc-x.us-east-1.aws.confluent.cloud",
                    "F1_SR_API_KEY=SK",
                    "F1_SR_API_SECRET=SS",
                ]
            )
            + "\n"
        )
        return card

    def test_numeric_shorthand_expands(self):
        self.assertEqual(race._expand_numeric_flags(["--20"]), ["--seconds-per-lap", "20"])
        self.assertEqual(
            race._expand_numeric_flags(["--once", "--45", "--creds", "x.env"]),
            ["--once", "--seconds-per-lap", "45", "--creds", "x.env"],
        )
        # Real flags are left alone, including ones with digits in the value.
        self.assertEqual(race._expand_numeric_flags(["--seconds-per-lap", "30"]), ["--seconds-per-lap", "30"])

    def test_below_minimum_pacing_is_refused(self):
        """`--seconds-per-lap 1` made readings_per_lap 1 // 2 == 0: no telemetry
        at all, while the log still reported lap progress."""
        card = self.make_card(meta.SELFSERVICE, "kevins")
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as raised:
            race._resolve_seconds_per_lap(1, self.root, card)
        self.assertIn(f"below the {meta.MIN_SECONDS_PER_LAP}s minimum", str(raised.exception))

    def test_pacing_comes_from_the_track_the_card_belongs_to(self):
        card = self.make_card(meta.SELFSERVICE, "kevins")
        meta.save_meta(self.root, meta.SELFSERVICE, **{meta.KEY_SECONDS_PER_LAP: "35"})
        meta.save_meta(self.root, meta.STANDALONE, **{meta.KEY_SECONDS_PER_LAP: "99"})

        with redirect_stdout(io.StringIO()):
            self.assertEqual(race._resolve_seconds_per_lap(None, self.root, card), 35)
            # An explicit flag still wins.
            self.assertEqual(race._resolve_seconds_per_lap(60, self.root, card), 60)

    def test_pacing_falls_back_to_the_default_with_no_metadata(self):
        card = self.make_card(meta.SELFSERVICE, "kevins")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(race._resolve_seconds_per_lap(None, self.root, card), race.DEFAULT_SECONDS_PER_LAP)

    def test_run_skips_the_warmup_and_applies_the_saved_pacing(self):
        """PRE_RACE_WARMUP_LAPS=0 is set Python-side, so no Terraform edit is
        needed — the standalone ECS path keeps its warmup."""
        import datagen.simulator as simulator

        card = self.make_card(meta.SELFSERVICE, "kevins")
        meta.save_meta(self.root, meta.SELFSERVICE, **{meta.KEY_SECONDS_PER_LAP: "35"})

        with (
            patch.object(race, "get_project_root", return_value=self.root),
            patch.object(simulator, "main") as sim_main,
            patch("sys.argv", ["f1-race", "--creds", str(card)]),
        ):
            with redirect_stdout(io.StringIO()):
                race.main()

        sim_main.assert_called_once()
        self.assertEqual(os.environ["PRE_RACE_WARMUP_LAPS"], "0")
        self.assertEqual(os.environ["SECONDS_PER_LAP"], "35")
        self.assertEqual(os.environ["RACE_LOOP"], "true")
        # The scheme has to be stripped for confluent-kafka.
        self.assertEqual(os.environ["KAFKA_BOOTSTRAP"], "pkc-x.us-east-1.aws.confluent.cloud:9092")

    def test_an_explicit_warmup_is_still_honoured(self):
        import datagen.simulator as simulator

        card = self.make_card(meta.SELFSERVICE, "kevins")
        with (
            patch.object(race, "get_project_root", return_value=self.root),
            patch.object(simulator, "main"),
            patch("sys.argv", ["f1-race", "--creds", str(card), "--20"]),
            patch.dict("os.environ", {"PRE_RACE_WARMUP_LAPS": "4"}),
        ):
            with redirect_stdout(io.StringIO()):
                race.main()
            self.assertEqual(os.environ["PRE_RACE_WARMUP_LAPS"], "4")
            self.assertEqual(os.environ["SECONDS_PER_LAP"], "20")


if __name__ == "__main__":
    unittest.main()
