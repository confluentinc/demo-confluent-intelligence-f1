"""Teardown ordering — a failed tier must not take its own dependencies down with it.

`uv run deploy` applies `aws-shared` (Postgres, simulator image, VPC lookups) and
then `aws` (the Confluent env, the CDC connector, the ECS simulator service). The
attendee tier *consumes* the shared tier, so destroying the shared half after the
attendee half failed strands whatever survived against infrastructure that no
longer exists — and no later `uv run destroy` can reach it.

The subtlety these tests pin down: the fix must stop the rest of the *failing
group* without stopping an independently-selected `self-service` teardown, which
shares nothing with either AWS tier.

Everything runs against a temp project root. `get_project_root()` walks up from
CWD to the nearest pyproject.toml, so without that patch these tests would delete
real terraform state and real credential cards out of the working checkout.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.common import destroy


class DestroyHarness:
    """Temp-root fixture + a driver for destroy.main(). Not a TestCase itself."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        patcher = patch.object(destroy, "get_project_root", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Nothing here should reach a real cloud account or a real Terraform run.
        for name in ("_inject_shared_vars", "cleanup_terraform_artifacts"):
            p = patch.object(destroy, name)
            p.start()
            self.addCleanup(p.stop)

        self.destroyed: list[str] = []

    def make_state(self, *tiers: str) -> None:
        for tier in tiers:
            state = self.root / "terraform" / tier / "terraform.tfstate"
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text("{}")

    def run_destroy(self, answers: list[str], fail_tiers: set[str]) -> None:
        """Drive destroy.main() with canned prompt answers and a failing tier set."""

        def fake_destroy(env_path: Path, *_a, **_kw) -> bool:
            self.destroyed.append(env_path.name)
            return env_path.name not in fail_tiers

        with (
            patch.object(destroy, "run_terraform_destroy", side_effect=fake_destroy),
            patch("builtins.input", side_effect=answers),
        ):
            try:
                destroy.main()
            except SystemExit as exc:
                self.exit_code = exc.code
            else:
                self.exit_code = 0


class DestroyOrderingTests(DestroyHarness, unittest.TestCase):
    # --- the dependency chain ---

    def test_failed_aws_destroy_never_starts_the_shared_destroy(self):
        self.make_state("aws", "aws-shared")

        # One group available -> no selection prompt, just the y/n confirmation.
        self.run_destroy(answers=["y"], fail_tiers={"aws"})

        self.assertEqual(self.destroyed, ["aws"])
        self.assertNotIn("aws-shared", self.destroyed)
        self.assertEqual(self.exit_code, 1)

    def test_successful_aws_destroy_still_reaches_the_shared_tier(self):
        self.make_state("aws", "aws-shared")

        self.run_destroy(answers=["y"], fail_tiers=set())

        self.assertEqual(self.destroyed, ["aws", "aws-shared"])
        self.assertEqual(self.exit_code, 0)

    # --- group independence: the reason this isn't a one-line `break` ---

    def test_independent_self_service_teardown_survives_an_aws_failure(self):
        self.make_state("aws", "aws-shared", "self-service")

        # Both groups selected (Enter = all), then confirm.
        with patch("scripts.selfservice.cli.export_selfservice_tf_env"):
            self.run_destroy(answers=["", "y"], fail_tiers={"aws"})

        self.assertIn("self-service", self.destroyed)
        self.assertNotIn("aws-shared", self.destroyed)
        self.assertEqual(self.destroyed, ["aws", "self-service"])
        self.assertEqual(self.exit_code, 1)

    def test_self_service_failure_does_not_block_the_deploy_group(self):
        self.make_state("aws", "aws-shared", "self-service")

        with patch("scripts.selfservice.cli.export_selfservice_tf_env"):
            self.run_destroy(answers=["", "y"], fail_tiers={"self-service"})

        self.assertEqual(self.destroyed, ["aws", "aws-shared", "self-service"])
        self.assertEqual(self.exit_code, 1)

    def test_selecting_only_self_service_leaves_the_aws_tiers_alone(self):
        self.make_state("aws", "aws-shared", "self-service")

        with patch("scripts.selfservice.cli.export_selfservice_tf_env"):
            self.run_destroy(answers=["2", "y"], fail_tiers=set())

        self.assertEqual(self.destroyed, ["self-service"])
        self.assertEqual(self.exit_code, 0)


class DeadCardCleanupTests(DestroyHarness, unittest.TestCase):
    """A torn-down track must not leave a card behind for `resolve_card()` to trip on.

    Two cards and no `F1_CARD` pointer is the deadlock: resolution refuses to
    guess and hard-exits every attendee tool, even though one environment is live.
    """

    def make_track_files(self, track: str, prefix: str) -> dict[str, Path]:
        run_root = self.root / "runs" / track
        (run_root / "credentials").mkdir(parents=True, exist_ok=True)
        files = {
            "card": run_root / "credentials" / f"{prefix}.env",
            "md": run_root / "credentials" / f"{prefix}.md",
            "meta": run_root / "deployment.env",
            "seeded": run_root / ".seeded",
        }
        files["card"].write_text("F1_KAFKA_API_KEY=ABC\n")
        files["md"].write_text(f"# {prefix}\n")
        files["meta"].write_text(f"F1_RESOLVED_PREFIX={prefix}\n")
        files["seeded"].write_text("env-123\n")
        return files

    def test_successful_destroy_removes_only_that_tracks_files(self):
        self.make_state("aws", "aws-shared", "self-service")
        standalone = self.make_track_files("standalone", "demo")
        selfservice = self.make_track_files("selfservice", "demos")
        (self.root / "credentials.env").write_text("F1_CARD=runs/selfservice/credentials/demos.env\n")

        with patch("scripts.selfservice.cli.export_selfservice_tf_env"):
            self.run_destroy(answers=["2", "y"], fail_tiers=set())

        for name, path in selfservice.items():
            self.assertFalse(path.exists(), f"selfservice {name} survived its own destroy")
        for name, path in standalone.items():
            self.assertTrue(path.exists(), f"standalone {name} was removed by a selfservice destroy")
        # The pointer named the dead card, so it goes too.
        self.assertNotIn("F1_CARD", (self.root / "credentials.env").read_text())

    def test_failed_destroy_removes_nothing(self):
        self.make_state("self-service")
        selfservice = self.make_track_files("selfservice", "demos")
        (self.root / "credentials.env").write_text("F1_CARD=runs/selfservice/credentials/demos.env\n")

        with patch("scripts.selfservice.cli.export_selfservice_tf_env"):
            self.run_destroy(answers=["y"], fail_tiers={"self-service"})

        self.assertEqual(self.exit_code, 1)
        for name, path in selfservice.items():
            self.assertTrue(path.exists(), f"selfservice {name} was removed after a FAILED destroy")
        self.assertIn("F1_CARD", (self.root / "credentials.env").read_text())

    def test_shared_tier_owns_no_track_files(self):
        # aws succeeds, aws-shared succeeds: only the standalone track is retired,
        # and aws-shared must not reach for a track of its own.
        self.make_state("aws", "aws-shared")
        standalone = self.make_track_files("standalone", "demo")

        self.run_destroy(answers=["y"], fail_tiers=set())

        self.assertEqual(self.destroyed, ["aws", "aws-shared"])
        self.assertFalse(standalone["card"].exists())
        self.assertFalse(standalone["meta"].exists())


if __name__ == "__main__":
    unittest.main()
