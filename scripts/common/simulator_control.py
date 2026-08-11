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

``F1_ANOMALY_FN=ai`` switches LAB 3 from the default GA ARIMA implementation to the
foundation-model Granite/``AI_DETECT_ANOMALIES`` one — see ``anomaly_sql_filename``.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

# LAB 3 has two implementations emitting the identical `car_state` schema, so
# everything downstream is indifferent to which one ran. The default uses GA
# `ML_DETECT_ANOMALIES` (ARIMA); the foundation-model
# `AI_DETECT_ANOMALIES` implementation remains available as an opt-in.
ANOMALY_SQL = "enrichment_anomaly.sql"
ANOMALY_SQL_AI = "enrichment_anomaly_ai.sql"

# The canonical lab SQL, rebuilt by --with-labs. Dependency-ordered:
# pit_decisions validates against both `car_state` and `pit_strategy_agent`, so a
# failure part-way through stops the rest (there is nothing useful to submit
# after a broken link).
LAB_BUILDS = [
    (ANOMALY_SQL, "car_state"),
    ("streaming_agent_create_agent.sql", "pit_strategy_agent"),
    ("streaming_agent_pit_decisions.sql", "pit_decisions"),
]


def anomaly_sql_filename() -> str:
    """Which LAB 3 implementation `--with-labs` should submit.

    ``F1_ANOMALY_FN=ai`` selects the foundation-model Granite variant; unset (or
    ``ml``) keeps the ARIMA default. Deliberately an explicit switch rather than
    anything automatic: the ``ai`` path currently produces a `car_state` that never
    flags an anomaly, and it must be opted into knowingly rather than fallen into.
    """
    return ANOMALY_SQL_AI if os.environ.get("F1_ANOMALY_FN") == "ai" else ANOMALY_SQL


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


def _hint_anomaly_fallback(filename: str) -> None:
    """Point back at the default when the opted-in Granite statement is what failed.

    Only reachable when someone set ``F1_ANOMALY_FN=ai``. `AI_DETECT_ANOMALIES`
    needs an Early Access Program that a fresh org does not have, and the API's own
    message ("Function ... does not exist or you do not have permission to access
    it") does not say what to do about it.
    """
    if filename != ANOMALY_SQL_AI:
        return
    print("    F1_ANOMALY_FN=ai selected AI_DETECT_ANOMALIES (Granite), which needs the")
    print("    AI-functions Early Access Program. Unset it to use the GA ARIMA default:")
    print("      unset F1_ANOMALY_FN && <the command you just ran>")


def create_lab_objects(tf: dict, root: Path) -> bool:
    """Submit durable lab DDL first, then start the restartable INSERT jobs.

    Waiting matters: every CREATE must reach COMPLETED before any INSERT starts,
    otherwise a restart can race a downstream statement against a table or agent
    that does not exist yet.  INSERT statements are successful only at RUNNING;
    accepting COMPLETED would hide a job that exited instead of tailing the feed.
    """
    from scripts.workshop.sql_shell import split_statements

    session = flink_session(tf)
    builds: list[tuple[str, str, str, list[str]]] = []

    for filename, creates in LAB_BUILDS:
        if filename == ANOMALY_SQL:
            filename = anomaly_sql_filename()

        path = root / "demo-reference" / filename
        if not path.exists():
            print(f"  {creates}: missing {path}")
            return False

        statements = split_statements(path.read_text())
        if not statements:
            print(f"  {creates}: no SQL statements in {path}")
            return False
        builds.append((filename, creates, statements[0], statements[1:]))

    def submit(sql: str, filename: str, creates: str, label: str, expected: str) -> bool:
        try:
            name = session.submit(sql)
        except Exception as e:
            print(f"  {creates}: {label} submit failed — {e}")
            _hint_anomaly_fallback(filename)
            return False

        status = session.wait(name, timeout=180)
        phase = status["status"]["phase"]
        if phase != expected:
            detail = (status.get("status") or {}).get("detail", "").strip()
            print(f"  {creates}: {label} {phase} — {detail or f'expected {expected}'}")
            _hint_anomaly_fallback(filename)
            return False
        print(f"  {creates}: {label} {phase}  ({filename})")
        return True

    for filename, creates, ddl, _ in builds:
        if not submit(ddl, filename, creates, "DDL", "COMPLETED"):
            return False

    for filename, creates, _, inserts in builds:
        for index, sql in enumerate(inserts, start=1):
            if not submit(sql, filename, creates, f"INSERT {index}", "RUNNING"):
                return False

    return True
