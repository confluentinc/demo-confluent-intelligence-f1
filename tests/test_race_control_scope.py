"""`uv run race` must touch exactly one ECS service, and say so when it can't.

The command exists because the only previous way to stop a standalone demo's feed
was `uv run workshop stop-races`, the instructor fan-out over every `river-racing*`
cluster in the AWS account. On an organizer's laptop that stops twenty attendees'
feeds. These tests pin the two properties that keep them apart: the service names
come from this checkout's Terraform state, and nothing in the import graph reaches
`scripts/instructor/`.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import race_control

TF = {
    "ecs_cluster_name": "river-racing-demo-abc-simulator",
    "ecs_service_name": "river-racing-demo-abc-simulator",
}


class RaceControlScopeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        p = patch.object(race_control, "get_project_root", return_value=self.root)
        p.start()
        self.addCleanup(p.stop)

    def make_state(self, tier: str = "aws") -> None:
        state = self.root / "terraform" / tier / "terraform.tfstate"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("{}")

    def run_race(self, action: str, outputs: dict = TF, **stubs) -> int:
        """Drive race_control.main() with every ECS call stubbed. Returns the exit code."""
        returns = {
            "run_terraform_output": outputs,
            "scale_simulator": True,
            "wait_for_drain": True,
            "wait_for_running": True,
            "describe_simulator": {
                "cluster": TF["ecs_cluster_name"],
                "service": TF["ecs_service_name"],
                "desired": 1,
                "running": 1,
                "pending": 0,
                "status": "ACTIVE",
            },
        }
        returns.update(stubs)

        self.mocks = {}
        for name, value in returns.items():
            patcher = patch.object(race_control, name, return_value=value)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

        with patch("sys.argv", ["race", action]):
            try:
                race_control.main()
            except SystemExit as exc:
                return exc.code or 0
        return 0

    # --- scope ---

    def test_stop_updates_only_the_service_named_in_local_state(self):
        self.make_state()

        rc = self.run_race("stop")

        self.assertEqual(rc, 0)
        self.mocks["scale_simulator"].assert_called_once_with(TF, "us-east-1", 0)

    def test_start_scales_to_one_and_waits(self):
        self.make_state()

        rc = self.run_race("start")

        self.assertEqual(rc, 0)
        self.mocks["scale_simulator"].assert_called_once_with(TF, "us-east-1", 1)
        self.mocks["wait_for_running"].assert_called_once()

    def test_restart_drains_before_starting(self):
        self.make_state()

        self.assertEqual(self.run_race("restart"), 0)

        self.assertEqual([c.args[2] for c in self.mocks["scale_simulator"].call_args_list], [0, 1])
        self.mocks["wait_for_drain"].assert_called_once()

    def test_region_override_from_credentials_env(self):
        self.make_state()
        (self.root / "credentials.env").write_text("TF_VAR_region=eu-west-1\n")

        self.run_race("stop")

        self.mocks["scale_simulator"].assert_called_once_with(TF, "eu-west-1", 0)

    def test_no_instructor_fan_out_in_the_import_graph(self):
        source = Path(race_control.__file__).read_text()
        self.assertNotIn("scripts.instructor", source)
        self.assertNotIn("scale_all_services", source)

    # --- failure modes ---

    def test_missing_state_exits_nonzero(self):
        self.assertEqual(self.run_race("status"), 1)

    def test_self_service_state_is_named_rather_than_guessed_at(self):
        self.make_state("self-service")
        with patch("builtins.print") as printed:
            rc = self.run_race("status")
        output = "\n".join(str(c.args[0]) for c in printed.call_args_list if c.args)

        self.assertEqual(rc, 1)
        self.assertIn("uv run f1-race", output)

    def test_stale_outputs_without_ecs_names_exit_nonzero(self):
        self.make_state()
        self.assertEqual(self.run_race("status", outputs={"environment_id": "env-1"}), 1)

    def test_unreachable_service_exits_nonzero(self):
        self.make_state()
        self.assertEqual(self.run_race("status", describe_simulator=None), 1)

    def test_failed_scale_exits_nonzero(self):
        self.make_state()
        self.assertEqual(self.run_race("stop", scale_simulator=False), 1)

    def test_drain_timeout_exits_nonzero(self):
        self.make_state()
        self.assertEqual(self.run_race("stop", wait_for_drain=False), 1)

    def test_start_timeout_exits_nonzero(self):
        self.make_state()
        self.assertEqual(self.run_race("start", wait_for_running=False), 1)

    def test_restart_does_not_start_after_a_failed_stop(self):
        self.make_state()
        self.assertEqual(self.run_race("restart", wait_for_drain=False), 1)
        self.assertEqual([c.args[2] for c in self.mocks["scale_simulator"].call_args_list], [0])


if __name__ == "__main__":
    unittest.main()
