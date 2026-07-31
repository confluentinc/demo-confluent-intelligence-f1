"""``uv run race`` — start, stop, restart, or inspect **this deployment's** race feed.

    uv run race status
    uv run race stop
    uv run race start
    uv run race restart

The standalone demo's race feed is an ECS Fargate service (one looping simulator
task) created by ``terraform/aws``. Before this command existed, the only way to
stop it was ``uv run stop-all-races`` — the instructor fan-out, which enumerates
every ``river-racing*`` cluster in the AWS account and scales all of them. On an
organizer's laptop, mid-workshop, that stops twenty attendees' feeds to reset one
demo environment. This command reads the ECS cluster and service names out of
*this* checkout's Terraform state and touches exactly that one service.

Deliberately narrow:

- No Confluent login and no credential card. Everything it needs is in the local
  Terraform outputs plus AWS credentials, so it stays fast enough to run between
  demo takes.
- Nothing from ``scripts/instructor/`` is imported, not even a constant — the
  fan-out must not be one autocomplete away from this file.
- Self-service has no ECS service at all (its race is the local ``uv run f1-race``
  process), so this command refuses that tier by name rather than failing obscurely.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import dotenv_values

from scripts.common.simulator_control import (
    describe_simulator,
    has_ecs,
    scale_simulator,
    wait_for_drain,
    wait_for_running,
)
from scripts.common.terraform import get_project_root, run_terraform_output

# Every tier of this demo is deployed into us-east-1 (the Bedrock inference
# profile in terraform/modules/llm pins the region), and `credentials.env`
# overrides it when a deployment says otherwise. Defined here rather than
# imported from scripts/instructor/_common.py so that nothing in this command's
# import graph can reach the account-wide fan-out.
DEFAULT_REGION = "us-east-1"


def load_deployment(root: Path) -> tuple[dict, str]:
    """This deployment's Terraform outputs plus the region to reach ECS in.

    Exits with the same shape of message ``uv run reset`` uses: the per-deployment
    state only exists on the machine that ran ``uv run deploy`` (it is gitignored),
    so an attendee or a self-service user who runs this needs the real answer, not
    a stack trace.
    """
    state = root / "terraform" / "aws" / "terraform.tfstate"
    if not state.exists():
        print(f"No Terraform state at {state}")
        print("\n`uv run race` controls the ECS race feed created by `uv run deploy`.")
        if (root / "terraform" / "self-service" / "terraform.tfstate").exists():
            print("This checkout has a self-service deployment instead, which has no ECS")
            print("service — its race feed is the local simulator: `uv run f1-race`.")
        else:
            print("If you're an attendee, your instructor controls the race feed.")
        sys.exit(1)

    creds_file = root / "credentials.env"
    creds = dotenv_values(creds_file) if creds_file.exists() else {}
    region = creds.get("TF_VAR_region") or DEFAULT_REGION

    try:
        tf = run_terraform_output(state)
    except Exception as e:
        print(f"Error reading terraform state: {e}")
        sys.exit(1)

    if not has_ecs(tf):
        print("This deployment's Terraform outputs carry no ECS cluster/service name.")
        print("Re-run `uv run deploy` to refresh the outputs, then try again.")
        sys.exit(1)

    return tf, region


def show_status(tf: dict, region: str) -> int:
    """Print desired vs running counts. Nonzero when ECS can't be reached."""
    info = describe_simulator(tf, region)
    if info is None:
        return 1

    print(f"  Cluster:  {info['cluster']}")
    print(f"  Service:  {info['service']}  ({info['status']})")
    print(f"  Desired:  {info['desired']}")
    print(f"  Running:  {info['running']}" + (f"  (pending {info['pending']})" if info["pending"] else ""))

    if info["desired"] == 0:
        print("\nRace feed is stopped. Start it with `uv run race start`.")
    elif info["running"] == 0:
        print("\nRace feed is starting — no task is producing yet.")
        print(f"Task logs: aws logs tail /ecs/{info['service']} --follow --region {region}")
    else:
        print("\nRace feed is running. Watch it: `uv run f1-pitwall`")
    return 0


def do_stop(tf: dict, region: str) -> int:
    """Scale to zero and wait for the task to actually die.

    The wait is the point: ECS returns from update_service as soon as the desired
    count is recorded, while the task keeps producing to `car_telemetry` and
    `race_standings` for a few more seconds. Anything that clears those topics
    next (`uv run reset`) needs the producer genuinely gone first.
    """
    print("Stopping the race feed...")
    if not scale_simulator(tf, region, 0):
        return 1
    return 0 if wait_for_drain(tf, region) else 1


def do_start(tf: dict, region: str) -> int:
    print("Starting the race feed...")
    if not scale_simulator(tf, region, 1):
        return 1
    if not wait_for_running(tf, region):
        return 1
    print("\nA fresh race is under way from lap 0. Watch it: `uv run f1-pitwall`")
    return 0


def do_restart(tf: dict, region: str) -> int:
    """Stop, confirm stopped, then start.

    Sequential on purpose. `race_standings` has no `scan.startup.mode` override
    (terraform/modules/topics/main.tf), so it is read from `latest`: a LAB 3
    statement must already be RUNNING when new standings rows arrive, and rows
    produced by an overlapping old task have no version for the temporal join.
    Draining first keeps "the race restarted at lap 0" literally true.
    """
    rc = do_stop(tf, region)
    if rc:
        return rc
    print()
    return do_start(tf, region)


ACTIONS = {"status": show_status, "start": do_start, "stop": do_stop, "restart": do_restart}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Start, stop, restart, or inspect this deployment's race feed (one ECS service)",
    )
    parser.add_argument(
        "action",
        choices=sorted(ACTIONS),
        help="status: desired vs running counts | start/stop/restart: scale and wait for the transition",
    )
    args = parser.parse_args()

    root = get_project_root()
    tf, region = load_deployment(root)

    print(f"=== F1 Race Feed — {args.action} ===\n")
    sys.exit(ACTIONS[args.action](tf, region))


if __name__ == "__main__":
    main()
