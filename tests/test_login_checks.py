import subprocess
import unittest
from unittest.mock import patch

from scripts.common.login_checks import check_docker_running


class CheckDockerRunningTests(unittest.TestCase):
    @patch("scripts.common.login_checks.subprocess.run")
    def test_returns_true_when_daemon_is_reachable(self, run):
        run.return_value = subprocess.CompletedProcess(["docker", "info"], 0)

        self.assertTrue(check_docker_running())
        run.assert_called_once_with(["docker", "info"], capture_output=True, text=True, timeout=10)

    @patch("scripts.common.login_checks.subprocess.run")
    def test_returns_false_when_daemon_is_unreachable(self, run):
        run.return_value = subprocess.CompletedProcess(["docker", "info"], 1)

        self.assertFalse(check_docker_running())

    @patch("scripts.common.login_checks.subprocess.run", side_effect=FileNotFoundError)
    def test_returns_false_when_cli_is_missing(self, _run):
        self.assertFalse(check_docker_running())

    @patch("scripts.common.login_checks.subprocess.run")
    def test_returns_false_when_daemon_check_times_out(self, run):
        run.side_effect = subprocess.TimeoutExpired(["docker", "info"], 10)

        self.assertFalse(check_docker_running())


if __name__ == "__main__":
    unittest.main()
