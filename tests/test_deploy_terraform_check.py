"""`uv run deploy` checks for Terraform only on the tracks that run it.

The check used to come first, before the track was even picked, so a developer on
the pre-provisioned track — which mints keys and writes a card, and runs no
Terraform — was stopped for not having it installed. Picking the track only looks
for files, so the check now follows it and is skipped for that track.
"""

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import deploy
from scripts.common import deployment_meta as meta


class TerraformCheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        env = patch.dict("os.environ", {"USER": "kevin", "LOGNAME": "kevin"}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        (self.root / "credentials.env").write_text("")

    def run_deploy(self, argv: list[str], terraform: bool, answers: list[str] | None = None):
        """deploy.main() with every track's deploy stubbed; returns (exit code, check mock, track mocks, stdout)."""
        tracks = {}
        code = 0
        with (
            patch.object(deploy, "get_project_root", return_value=self.root),
            patch.object(deploy, "check_terraform_installed", return_value=terraform) as check,
            patch.object(deploy.preprovisioned, "deploy") as tracks["pre-provisioned"],
            patch.object(deploy.selfservice, "deploy") as tracks["self-service"],
            patch.object(deploy, "deploy_standalone") as tracks["standalone"],
            patch("builtins.input", side_effect=answers if answers is not None else AssertionError("prompted")),
            redirect_stdout(io.StringIO()) as out,
        ):
            try:
                deploy.main(argv)
            except SystemExit as e:
                code = e.code or 0
        return code, check, tracks, out.getvalue()

    def test_pre_provisioned_never_needs_terraform(self):
        code, check, tracks, _out = self.run_deploy(["--track", "pre-provisioned", "--automated"], terraform=False)

        self.assertEqual(code, 0)
        check.assert_not_called()
        tracks["pre-provisioned"].assert_called_once()

    def test_pre_provisioned_picked_at_the_prompt_never_needs_terraform(self):
        code, check, tracks, _out = self.run_deploy([], terraform=False, answers=["3"])

        self.assertEqual(code, 0)
        check.assert_not_called()
        tracks["pre-provisioned"].assert_called_once()

    def test_an_inferred_pre_provisioned_redeploy_never_needs_terraform(self):
        # --automated without --track infers the only deployed track; this one
        # is deployed by the keys it recorded, not by state.
        meta.save_meta(self.root, meta.PREPROVISIONED, **{meta.KEY_GLOBAL_KEY_ID: "GLOBAL1"})

        code, check, tracks, _out = self.run_deploy(["--automated"], terraform=False)

        self.assertEqual(code, 0)
        check.assert_not_called()
        tracks["pre-provisioned"].assert_called_once()

    def test_terraform_tracks_still_stop_without_it(self):
        for track in ("self-service", "standalone"):
            with self.subTest(track=track):
                code, check, tracks, out = self.run_deploy(["--track", track, "--automated"], terraform=False)

                self.assertEqual(code, 1)
                check.assert_called_once()
                self.assertIn("Terraform not found", out)
                for mock in tracks.values():
                    mock.assert_not_called()

    def test_terraform_tracks_run_with_it(self):
        for track in ("self-service", "standalone"):
            with self.subTest(track=track):
                code, check, tracks, _out = self.run_deploy(["--track", track, "--automated"], terraform=True)

                self.assertEqual(code, 0)
                check.assert_called_once()
                tracks[track].assert_called_once()


if __name__ == "__main__":
    unittest.main()
