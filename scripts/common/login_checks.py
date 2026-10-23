"""
Login verification utilities.

Provides functions for:
- Checking Confluent CLI login status
- Keeping the Confluent CLI session alive (prompt once, then auto-login)
- Checking AWS CLI configuration
- Checking Docker daemon availability
- Checking Terraform installation
"""

import getpass
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from dotenv import dotenv_values, set_key

from scripts.common.ui import prompt_secret

USERNAME_KEY = "TF_VAR_confluent_username"
PASSWORD_KEY = "TF_VAR_confluent_password"


def check_confluent_login() -> bool:
    """Return True if the Confluent CLI has an active session.

    Uses the exit code rather than parsing stdout: `confluent environment list`
    exits 1 with "Error: not logged in" when there is no session, and exits 0 for
    a valid session even in an org with zero environments (e.g. right after
    `uv run destroy`), which stdout parsing would misread as logged out.
    """
    try:
        result = subprocess.run(["confluent", "environment", "list"], capture_output=True, text=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def _try_confluent_login(username: str, password: str) -> bool:
    """Attempt a non-interactive Confluent CLI login via env vars. Returns True on success.

    Passes `--save` so the CLI stores the credentials locally and silently
    re-authenticates when the 8-hour token expires — that is what keeps the user
    logged in across later script runs instead of re-prompting.
    """
    try:
        env = {**os.environ, "CONFLUENT_CLOUD_EMAIL": username, "CONFLUENT_CLOUD_PASSWORD": password}
        result = subprocess.run(["confluent", "login", "--save"], capture_output=True, text=True, env=env)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def _prompt_and_save_login(creds_file, creds: dict) -> bool:
    """Prompt for Confluent Cloud login once and persist it for future auto-login.

    Credentials are only written after a login actually succeeds, so a typo never
    gets saved. Returns True if logged in; False if the user skipped or gave up
    (callers decide whether that is fatal).
    """
    print("\nConfluent Cloud login (saved to credentials.env so you're not asked again):")
    creds_file = Path(creds_file)
    creds_file.touch(exist_ok=True)  # set_key needs the file to exist
    for attempt in range(3):
        username = input("  Email (press Enter to skip): ").strip()
        if not username:
            print("  Skipped. Run `confluent login` manually if your session expires.")
            return False
        password = prompt_secret("  Password: ")

        if _try_confluent_login(username, password):
            set_key(str(creds_file), USERNAME_KEY, username)
            set_key(str(creds_file), PASSWORD_KEY, password)
            creds[USERNAME_KEY] = username
            creds[PASSWORD_KEY] = password
            print("  Logged in and saved.")
            return True

        remaining = 2 - attempt
        if remaining:
            print(f"  Login failed. {remaining} attempt{'s' if remaining > 1 else ''} remaining.")
        else:
            print("  Login failed. Run `confluent login` manually.")
            print("  (SSO accounts cannot auto-login — use `confluent login --no-browser`.)")
    return False


def ensure_confluent_login(creds: dict | None = None, creds_file=None, interactive: bool = False) -> bool:
    """
    Ensure the Confluent CLI has an active session.

    Order: existing session → auto-login from saved credentials → (interactive
    only) prompt once and save. Together with `--save`, the user supplies their
    Confluent Cloud login a single time and later script runs re-authenticate
    without asking again.

    Args:
        creds: credentials.env values; loaded from the project root when None.
        creds_file: path to credentials.env, required to persist a prompted login.
        interactive: prompt for credentials when none are saved.

    Returns True if logged in, False if login could not be established.
    """
    if check_confluent_login():
        return True

    if creds is None:
        creds = dict(dotenv_values(Path(__file__).resolve().parents[2] / "credentials.env"))

    # dotenv_values yields None for bare keys, so coerce before stripping.
    username = (creds.get(USERNAME_KEY) or "").strip()
    password = (creds.get(PASSWORD_KEY) or "").strip()

    if username and password:
        print("  Not logged into Confluent Cloud — attempting auto-login...")
        if _try_confluent_login(username, password):
            print("  Logged in successfully")
            return True
        print(f"  Auto-login failed. Check {USERNAME_KEY} / {PASSWORD_KEY} in credentials.env")
        if interactive and creds_file:
            return _prompt_and_save_login(creds_file, creds)
        return False

    if interactive and creds_file:
        return _prompt_and_save_login(creds_file, creds)

    print("Error: Not logged into Confluent Cloud.")
    print("Run: confluent login --save")
    print(f"(Or set {USERNAME_KEY} and {PASSWORD_KEY} in credentials.env for auto-login)")
    return False


def ensure_saved_confluent_login(creds: dict, creds_file) -> bool:
    """Make sure credentials.env holds a working Confluent Cloud login, then log in.

    Unlike `ensure_confluent_login`, a live CLI session is not enough: when no
    email/password is saved yet, prompt for them (verified by a real login before
    they are written) so later runs, and the CLI's 8-hour token refresh, never
    need the user again. Mirrors quickstart-streaming-agents' deploy step 0.
    """
    username = (creds.get(USERNAME_KEY) or "").strip()
    password = (creds.get(PASSWORD_KEY) or "").strip()
    if not (username and password):
        # Skipped or failed three times: fall back to whatever session the CLI
        # already has rather than asking a second time.
        return _prompt_and_save_login(creds_file, creds) or check_confluent_login()
    return ensure_confluent_login(creds, creds_file=creds_file, interactive=True)


def check_terraform_installed() -> bool:
    """
    Check if Terraform CLI is installed.

    Returns:
        True if installed, False otherwise
    """
    try:
        subprocess.run(["terraform", "version"], capture_output=True, text=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def check_docker_running() -> bool:
    """Return True when the Docker CLI can reach a running Docker daemon."""
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _docker_socket_permission_denied() -> bool:
    """True when a Docker daemon is up but this user can't open its socket.

    The usual state on a fresh Linux VM: docker-ce is installed and running,
    but the login user isn't in the `docker` group.
    """
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode != 0 and "permission denied" in result.stderr.lower()


def _start_docker_linux() -> bool:
    """Start the systemd docker service and grant this user its socket.

    Replaces the manual `systemctl enable --now docker` / `usermod -aG docker`
    / `newgrp docker` steps. `usermod` only takes effect at the next login, so
    an ACL on the socket gives this already-running process (and the
    Terraform/docker children it spawns) access right away.
    """
    if shutil.which("systemctl") is None:
        return False
    sudo = [] if os.geteuid() == 0 else ["sudo"]
    if not check_docker_running() and not _docker_socket_permission_denied():
        unit = subprocess.run(["systemctl", "cat", "docker.service"], capture_output=True, text=True)
        if unit.returncode != 0:
            return False
        print("  Starting the docker service (sudo may ask for your password)...")
        subprocess.run([*sudo, "systemctl", "enable", "--now", "docker"])
    if _docker_socket_permission_denied():
        user = getpass.getuser()
        socket = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock").removeprefix("unix://")
        print(f"  Giving {user} access to the docker socket...")
        subprocess.run([*sudo, "usermod", "-aG", "docker", user])
        subprocess.run([*sudo, "setfacl", "-m", f"u:{user}:rw", socket])
    return check_docker_running()


RHEL_DOCKER_INSTALL = (
    "sudo dnf -y install dnf-plugins-core"
    " && sudo dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo"
    " && sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin"
)
LINUX_DOCKER_ENABLE = "sudo systemctl enable --now docker && sudo usermod -aG docker $USER && newgrp docker"


def docker_installed() -> bool:
    return shutil.which("docker") is not None


def docker_start_hint() -> str:
    """The one-paste fix to print when Docker is missing or its daemon can't be reached."""
    if sys.platform.startswith("linux"):
        if docker_installed():
            return f"Run:\n  {LINUX_DOCKER_ENABLE}\nthen verify with: docker info"
        install = RHEL_DOCKER_INSTALL if shutil.which("dnf") else "curl -fsSL https://get.docker.com | sudo sh"
        return f"Install and start it with:\n  {install} && {LINUX_DOCKER_ENABLE}\nthen verify with: docker info"
    if not docker_installed():
        return (
            "Install Docker Desktop (https://docs.docker.com/desktop/) or run:\n"
            "  brew install colima docker && colima start\n"
            "then verify with: docker info"
        )
    return "Start Docker Desktop or run `colima start`, then verify with: docker info"


def start_docker(timeout: int = 180) -> bool:
    """Try to start a Docker daemon: Docker Desktop, the Linux systemd service, then Colima.

    Returns True once `docker info` succeeds, False if no runtime is
    installed or the daemon is not up within `timeout` seconds.
    """
    if shutil.which("docker") is None:
        return False
    deadline = time.monotonic() + timeout
    if sys.platform == "darwin" and Path("/Applications/Docker.app").exists():
        print("  Starting Docker Desktop...")
        subprocess.run(["open", "-a", "Docker"], capture_output=True)
        while time.monotonic() < deadline:
            if check_docker_running():
                return True
            time.sleep(3)
    if sys.platform.startswith("linux") and _start_docker_linux():
        return True
    if shutil.which("colima"):
        print("  Starting Colima...")
        try:
            subprocess.run(["colima", "start"], timeout=max(deadline - time.monotonic(), 60))
        except subprocess.TimeoutExpired:
            pass
        return check_docker_running()
    return False


def check_aws_configured() -> bool:
    """
    Check if AWS CLI is configured with valid credentials.

    Returns:
        True if configured, False otherwise
    """
    try:
        result = subprocess.run(["aws", "sts", "get-caller-identity"], capture_output=True, text=True, check=True)
        return "Account" in result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
