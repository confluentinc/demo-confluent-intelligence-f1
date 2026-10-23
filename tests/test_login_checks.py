import subprocess
import unittest
from unittest.mock import patch

from scripts.common import login_checks
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


def _docker_info(returncode, stderr=""):
    return subprocess.CompletedProcess(["docker", "info"], returncode, "", stderr)


DENIED = "permission denied while trying to connect to the docker API at unix:///var/run/docker.sock"


@patch.object(login_checks.sys, "platform", "linux")
@patch.object(login_checks.shutil, "which", return_value="/usr/bin/x")
@patch.object(login_checks.os, "geteuid", return_value=1001)
@patch.object(login_checks.getpass, "getuser", return_value="itzuser")
@patch.dict("os.environ", {}, clear=True)
class StartDockerLinuxTests(unittest.TestCase):
    """The RHEL VM states: service stopped, or running but the user lacks the socket."""

    def run_with(self, responses):
        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            if cmd == ["docker", "info"]:
                return responses.pop(0)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch.object(login_checks.subprocess, "run", side_effect=fake_run):
            started = login_checks.start_docker()
        return started, [c for c in calls if c != ["docker", "info"]]

    def test_running_daemon_with_denied_socket_gets_group_and_acl(self, *_):
        # Daemon up since boot; itzuser is in /etc/group but not this session's groups.
        started, cmds = self.run_with([_docker_info(1, DENIED)] * 3 + [_docker_info(0)])

        self.assertTrue(started)
        self.assertEqual(
            cmds,
            [
                ["sudo", "usermod", "-aG", "docker", "itzuser"],
                ["sudo", "setfacl", "-m", "u:itzuser:rw", "/var/run/docker.sock"],
            ],
        )

    def test_stopped_service_is_enabled_and_started(self, *_):
        unreachable = _docker_info(1, "Cannot connect to the Docker daemon")
        started, cmds = self.run_with([unreachable, unreachable, _docker_info(0), _docker_info(0)])

        self.assertTrue(started)
        self.assertEqual(
            cmds,
            [
                ["systemctl", "cat", "docker.service"],
                ["sudo", "systemctl", "enable", "--now", "docker"],
            ],
        )


class DockerStartHintTests(unittest.TestCase):
    @patch.object(login_checks.sys, "platform", "linux")
    def test_missing_docker_on_rhel_gets_one_install_and_enable_command(self):
        with patch.object(login_checks.shutil, "which", side_effect=lambda b: "/usr/bin/dnf" if b == "dnf" else None):
            hint = login_checks.docker_start_hint()

        self.assertIn("download.docker.com/linux/rhel/docker-ce.repo", hint)
        self.assertIn("sudo systemctl enable --now docker", hint)

    @patch.object(login_checks.sys, "platform", "linux")
    @patch.object(login_checks.shutil, "which", return_value="/usr/bin/docker")
    def test_installed_docker_on_linux_gets_only_the_enable_command(self, _which):
        hint = login_checks.docker_start_hint()

        self.assertNotIn("dnf", hint)
        self.assertIn("usermod -aG docker", hint)
        self.assertNotIn("colima", hint)

    @patch.object(login_checks.sys, "platform", "linux")
    @patch.object(login_checks.shutil, "which", return_value=None)
    def test_start_docker_returns_false_without_the_cli(self, _which):
        with patch.object(login_checks.subprocess, "run") as run:
            self.assertFalse(login_checks.start_docker())
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
