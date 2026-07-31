"""Control **this deployment's** race simulator, and rebuild its lab objects.

Extracted from scripts/reset.py so three callers can share one implementation:
``uv run race`` (scripts/race_control.py), ``uv run reset``, and the
``--with-labs`` paths of ``uv run deploy`` / ``uv run selfservice up``.

Everything here is deliberately **single-deployment**. It reads the ECS cluster
and service names out of *this* checkout's ``terraform/aws`` state and updates
exactly that service. It is emphatically not
``scripts/instructor/_common.scale_all_services()``, which fans out over every
``river-racing*`` cluster in the AWS account — using the fan-out here would stop
and restart all twenty attendees' feeds when an instructor resets one
environment mid-workshop.
"""

from __future__ import annotations

import time
from pathlib import Path

# The canonical lab SQL, rebuilt by --with-labs. Dependency-ordered:
# pit_decisions validates against both `car_state` and `pit_strategy_agent`, so a
# failure part-way through stops the rest (there is nothing useful to submit
# after a broken link).
LAB_BUILDS = [
    ("enrichment_anomaly.sql", "car_state"),
    ("streaming_agent_create_agent.sql", "pit_strategy_agent"),
    ("streaming_agent_pit_decisions.sql", "pit_decisions"),
]


def _ecs_service(tf: dict, region: str):
    """This deployment's own ECS client + cluster/service names.

    Raises KeyError when the Terraform outputs predate the ECS service (or the
    state belongs to the Confluent-only self-service tier, which has no ECS at
    all). Callers decide whether that is fatal or a skip.
    """
    import boto3

    return boto3.client("ecs", region_name=region), tf["ecs_cluster_name"], tf["ecs_service_name"]


def has_ecs(tf: dict) -> bool:
    """Whether this deployment has an ECS simulator service at all.

    False for the self-service tier (Confluent-only — the race is the local
    ``uv run f1-race`` process), so callers can skip every ECS step instead of
    reporting a spurious failure.
    """
    return bool(tf.get("ecs_cluster_name")) and bool(tf.get("ecs_service_name"))


def scale_simulator(tf: dict, region: str, count: int) -> bool:
    """Scale this deployment's simulator service. Returns False if it couldn't."""
    try:
        ecs, cluster, service = _ecs_service(tf, region)
        ecs.update_service(cluster=cluster, service=service, desiredCount=count)
        print(f"  {cluster}/{service}: desiredCount -> {count}")
        return True
    except KeyError as e:
        print(f"  Skipped: no {e} in Terraform state (re-run `uv run deploy` to refresh outputs).")
        return False
    except Exception as e:
        print(f"  Could not scale the simulator: {e}")
        return False


def describe_simulator(tf: dict, region: str) -> dict | None:
    """Desired/running/pending counts for this deployment's simulator.

    Returns None when the service can't be reached, so callers can exit nonzero
    rather than printing a confident zero.
    """
    try:
        ecs, cluster, service = _ecs_service(tf, region)
        services = ecs.describe_services(cluster=cluster, services=[service]).get("services", [])
    except Exception as e:
        print(f"  Could not describe the simulator: {e}")
        return None

    if not services:
        print(f"  Service not found: {tf.get('ecs_service_name')}")
        return None

    svc = services[0]
    return {
        "cluster": cluster,
        "service": service,
        "desired": svc.get("desiredCount", 0),
        "running": svc.get("runningCount", 0),
        "pending": svc.get("pendingCount", 0),
        "status": svc.get("status", "UNKNOWN"),
    }


def wait_for_drain(tf: dict, region: str, timeout: int = 180) -> bool:
    """Block until the simulator has no running task. True once it's stopped.

    Truncating while it produces is pointless — records land again the moment
    delete_records returns — so the stop has to actually finish before the
    caller clears the source topics.
    """
    try:
        ecs, cluster, service = _ecs_service(tf, region)
    except Exception as e:
        print(f"  Could not reach the simulator to confirm it stopped: {e}")
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            services = ecs.describe_services(cluster=cluster, services=[service]).get("services", [])
        except Exception as e:
            print(f"  Could not confirm the simulator stopped: {e}")
            return False
        if not services or services[0].get("runningCount", 0) == 0:
            print("  Simulator stopped.")
            return True
        time.sleep(5)

    print("  Warning: simulator task still running after waiting — the source topics may")
    print("  not stay empty. Check `uv run race status` and re-run.")
    return False


def wait_for_running(tf: dict, region: str, timeout: int = 300) -> bool:
    """Block until the simulator has a running task. True once it's up."""
    try:
        ecs, cluster, service = _ecs_service(tf, region)
    except Exception as e:
        print(f"  Could not reach the simulator to confirm it started: {e}")
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            services = ecs.describe_services(cluster=cluster, services=[service]).get("services", [])
        except Exception as e:
            print(f"  Could not confirm the simulator started: {e}")
            return False
        if services and services[0].get("runningCount", 0) > 0:
            print("  Simulator running.")
            return True
        time.sleep(5)

    print("  Warning: simulator has no running task yet. Check `uv run race status`,")
    print(f"  or the task logs: aws logs tail /ecs/{tf.get('ecs_service_name', '<service>')} --follow")
    return False


def flink_session(tf: dict):
    """A FlinkSession built from the per-deployment Terraform outputs.

    Not ``load_card()``: the credential card is resolved from credentials.env and
    could point at a different environment than the state file the caller
    operates on. Both halves must act on exactly one environment.
    """
    from scripts.workshop.sql_shell import FlinkSession

    return FlinkSession(
        {
            "F1_FLINK_REST_ENDPOINT": tf["flink_rest_endpoint"],
            "F1_ORGANIZATION_ID": tf["organization_id"],
            "F1_ENVIRONMENT_ID": tf["environment_id"],
            "F1_COMPUTE_POOL_ID": tf["compute_pool_id"],
            "F1_CATALOG": tf["environment_name"],
            "F1_DATABASE": tf["cluster_name"],
            "F1_FLINK_API_KEY": tf["flink_api_key"],
            "F1_FLINK_API_SECRET": tf["flink_api_secret"],
        }
    )


def create_lab_objects(tf: dict, root: Path) -> bool:
    """Submit the canonical lab SQL and wait for each statement to come up.

    Waiting matters: ``CREATE AGENT`` must reach COMPLETED and ``car_state`` must
    exist before the pit_decisions statement will pass validation.
    Fire-and-forget submission would race and fail with "table does not exist".
    """
    session = flink_session(tf)

    for filename, creates in LAB_BUILDS:
        path = root / "demo-reference" / filename
        if not path.exists():
            print(f"  {creates}: missing {path}")
            return False

        # Same normalization as `f1-sql --exec`: the trailing ';' in the .sql file
        # is a shell-shell convention, not part of the statement.
        sql = path.read_text().strip().rstrip(";").strip()

        try:
            name = session.submit(sql)
        except Exception as e:
            print(f"  {creates}: submit failed — {e}")
            return False

        status = session.wait(name, timeout=180)
        phase = status["status"]["phase"]
        if phase in ("RUNNING", "COMPLETED"):
            print(f"  {creates}: {phase}  ({filename})")
            continue

        detail = (status.get("status") or {}).get("detail", "").strip()
        print(f"  {creates}: {phase} — {detail or 'no detail returned'}")
        return False

    return True
