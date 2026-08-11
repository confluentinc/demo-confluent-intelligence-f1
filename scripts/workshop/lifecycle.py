"""Manifest-backed lifecycle controls for an instructor-managed workshop run.

The manifest is the scope boundary.  Cloud resources are never discovered by a
name substring: every cluster/service pair comes from the Terraform state WSA
actually applied, and every credential-card path is recorded at build time.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import boto3
import yaml
from dotenv import dotenv_values

from scripts.common.terraform import get_project_root
from scripts.reset import (
    LAB_DROPS,
    LAB_TOPICS,
    SOURCE_TOPICS,
    delete_flink_statements,
    delete_topic_and_subjects,
    drop_flink_objects,
    existing_topics,
    kafka_admin,
    truncate_topics,
)

MANIFEST_NAME = "manifest.json"
MAX_SUBSET_ACCOUNTS = 3
RESET_WORKERS = 8
ECS_START_TIMEOUT = 180
FRESH_TELEMETRY_TIMEOUT = 60
STOP_TIMEOUT = 120
FRESH_SECONDS = 90

MIGRATION_DESTROY_TARGETS = {
    "module.topics.confluent_flink_statement.create_car_telemetry_table",
    "module.topics.confluent_flink_statement.create_race_standings_table",
    "module.topics.confluent_rtce_topic.car_telemetry[0]",
    # Both resources depend on module.topics, so Terraform includes them when
    # the RTCE topic is targeted for destroy. Keep the exact dependency cascade
    # explicit so the saved-plan allowlist still rejects anything unrelated.
    "aws_ecs_service.simulator",
    "confluent_tag.raw_data",
    "confluent_tag_binding.car_telemetry_raw_data",
}
MIGRATION_APPLY_TARGETS = {
    *MIGRATION_DESTROY_TARGETS,
    "aws_ecs_task_definition.simulator",
}
SOURCE_DROPS = [
    ("drop-race-standings", "DROP TABLE IF EXISTS `race_standings`"),
    ("drop-car-telemetry", "DROP TABLE IF EXISTS `car_telemetry`"),
]


@dataclass(frozen=True)
class Account:
    number: int
    prefix: str
    credential_card: Path
    ecs_cluster: str
    ecs_service: str
    region: str
    prepared: bool = False


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    path: Path
    accounts: tuple[Account, ...]
    preparation_status: str


def _output(state: dict, name: str) -> str:
    return str((state.get("outputs", {}).get(name) or {}).get("value") or "")


def write_manifest(root: Path, run, card_run_name: str, region: str) -> Path:
    """Write a non-secret manifest from WSA's actual per-account TF states."""
    report = json.loads((run.path / "build-report.json").read_text())
    numbers = [int(n) for n in report.get("accounts", [])]
    entries: list[dict] = []
    for number in numbers:
        state_path = (
            run.path
            / "terraform/aws/terraform.tfstate.d"
            / f"account-{number:03d}"
            / "terraform.tfstate"
        )
        if not state_path.is_file():
            continue
        state = json.loads(state_path.read_text())
        prefix = _output(state, "prefix")
        cluster = _output(state, "ecs_cluster_name")
        service = _output(state, "ecs_service_name")
        if not (prefix and cluster and service):
            continue
        card = root / "runs" / card_run_name / "credentials" / f"{prefix}.env"
        entries.append(
            {
                "account": number,
                "prefix": prefix,
                "credential_card": str(card.relative_to(root)),
                "ecs_cluster": cluster,
                "ecs_service": service,
                "region": region,
                "prepared": False,
            }
        )

    if not entries:
        raise SystemExit(f"Cannot create a lifecycle manifest: no usable ECS outputs in {run.path}.")
    missing = sorted(set(numbers) - {e["account"] for e in entries})
    if missing:
        raise SystemExit(
            "Cannot create a complete lifecycle manifest; missing applied ECS outputs for account(s): "
            + ", ".join(map(str, missing))
        )

    path = root / "runs" / run.run_id / MANIFEST_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "version": 1,
        "run_id": run.run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "preparation_status": "not_prepared",
        "accounts": entries,
    }
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return path


def _load_manifest(path: Path, root: Path) -> RunManifest:
    try:
        raw = json.loads(path.read_text())
        accounts = tuple(
            Account(
                number=int(item["account"]),
                prefix=str(item["prefix"]),
                credential_card=root / item["credential_card"],
                ecs_cluster=str(item["ecs_cluster"]),
                ecs_service=str(item["ecs_service"]),
                region=str(item.get("region") or "us-east-1"),
                prepared=bool(item.get("prepared", False)),
            )
            for item in raw["accounts"]
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"Invalid workshop run manifest {path}: {exc}") from exc
    if not accounts or len({a.number for a in accounts}) != len(accounts):
        raise SystemExit(f"Invalid workshop run manifest {path}: accounts must be non-empty and unique")
    return RunManifest(
        run_id=str(raw.get("run_id") or path.parent.name),
        path=path,
        accounts=accounts,
        preparation_status=str(raw.get("preparation_status") or "unknown"),
    )


def resolve_manifest(root: Path, run_id: str = "") -> RunManifest:
    manifests = sorted(
        path
        for path in (root / "runs").glob(f"*/{MANIFEST_NAME}")
        if not (root / "wsa-output" / f"{path.parent.name}-cleaned" / "clean-report.json").is_file()
    )
    if run_id:
        exact = root / "runs" / run_id / MANIFEST_NAME
        if not exact.is_file():
            raise SystemExit(f"No lifecycle manifest for run {run_id!r}: {exact}")
        if (root / "wsa-output" / f"{run_id}-cleaned" / "clean-report.json").is_file():
            raise SystemExit(f"Workshop run {run_id!r} has already been cleaned")
        return _load_manifest(exact, root)
    if len(manifests) != 1:
        names = ", ".join(p.parent.name for p in manifests) or "none"
        raise SystemExit(
            "Omitting --run-id is allowed only when exactly one active run manifest exists; "
            f"found {len(manifests)} ({names})."
        )
    return _load_manifest(manifests[0], root)


def parse_account_selector(selector: str, available: Iterable[int]) -> list[int]:
    allowed = set(available)
    if not selector.strip():
        return sorted(allowed)
    selected: list[int] = []
    for token in selector.split(","):
        token = token.strip()
        if not token:
            raise SystemExit("--accounts contains an empty selector")
        parts = token.split("-", 1)
        if not all(part.isdigit() for part in parts):
            raise SystemExit("--accounts must contain numbers and ascending ranges, e.g. 48-50 or 2,7")
        start, end = int(parts[0]), int(parts[-1])
        if start < 1 or end < start:
            raise SystemExit("--accounts ranges must be positive and ascending")
        selected.extend(range(start, end + 1))
    selected = list(dict.fromkeys(selected))
    if len(selected) > MAX_SUBSET_ACCOUNTS:
        raise SystemExit(f"Explicit --accounts selections are capped at {MAX_SUBSET_ACCOUNTS} accounts")
    unknown = sorted(set(selected) - allowed)
    if unknown:
        raise SystemExit("Account(s) not present in the run manifest: " + ", ".join(map(str, unknown)))
    return selected


def _selection(args: argparse.Namespace) -> tuple[RunManifest, list[Account], bool]:
    root = get_project_root()
    manifest = resolve_manifest(root, getattr(args, "run_id", ""))
    selector = getattr(args, "accounts", "") or ""
    numbers = parse_account_selector(selector, (a.number for a in manifest.accounts))
    selected = [a for a in manifest.accounts if a.number in numbers]
    return manifest, selected, not selector.strip()


def _client(account: Account):
    return boto3.client("ecs", region_name=account.region)


def describe_exact(account: Account) -> dict:
    """Describe exactly one manifest service, failing on zero/multiple results."""
    response = _client(account).describe_services(
        cluster=account.ecs_cluster, services=[account.ecs_service]
    )
    services = response.get("services", [])
    failures = response.get("failures", [])
    exact = [s for s in services if s.get("serviceName") == account.ecs_service]
    if failures or len(exact) != 1:
        detail = failures[0].get("reason", "not found") if failures else f"{len(exact)} exact matches"
        raise RuntimeError(
            f"account {account.number}: expected exactly one ECS service "
            f"{account.ecs_cluster}/{account.ecs_service}; {detail}"
        )
    return exact[0]


def _scale(account: Account, count: int) -> None:
    _client(account).update_service(
        cluster=account.ecs_cluster, service=account.ecs_service, desiredCount=count
    )


def _wait_count(account: Account, desired: int, timeout: int) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        service = describe_exact(account)
        if service.get("desiredCount", 0) == desired and service.get("runningCount", 0) == desired:
            return True
        time.sleep(2)
    return False


def _card(account: Account) -> dict[str, str]:
    if not account.credential_card.is_file():
        raise RuntimeError(f"account {account.number}: missing credential card {account.credential_card}")
    card = {k: str(v) for k, v in dotenv_values(account.credential_card).items() if v is not None}
    required = {
        "F1_KAFKA_BOOTSTRAP",
        "F1_KAFKA_API_KEY",
        "F1_KAFKA_API_SECRET",
        "F1_SCHEMA_REGISTRY_URL",
        "F1_SR_API_KEY",
        "F1_SR_API_SECRET",
        "F1_FLINK_REST_ENDPOINT",
        "F1_FLINK_API_KEY",
        "F1_FLINK_API_SECRET",
        "F1_COMPUTE_POOL_ID",
        "F1_CATALOG",
        "F1_DATABASE",
        "F1_ORGANIZATION_ID",
        "F1_ENVIRONMENT_ID",
        "F1_CLUSTER_ID",
    }
    missing = sorted(required - set(card))
    if missing:
        raise RuntimeError(f"account {account.number}: credential card missing {', '.join(missing)}")
    return card


def _latest_telemetry(
    account: Account,
    wait_seconds: float = 4.0,
    accept: Callable[[dict], bool] | None = None,
) -> dict | None:
    """Read recent tail records and return the newest matching telemetry value.

    ``accept`` lets lifecycle waits keep one Kafka consumer open until the
    expected record arrives. Recreating a consumer on every short poll causes
    a connection storm when the complete workshop cohort starts together.
    """
    from confluent_kafka import Consumer, TopicPartition
    from confluent_kafka.serialization import MessageField, SerializationContext

    from scripts.pitwall.consumer import _bootstrap, _build_deserializer

    creds = _card(account)
    consumer = Consumer(
        {
            "bootstrap.servers": _bootstrap(creds["F1_KAFKA_BOOTSTRAP"]),
            "security.protocol": "SASL_SSL",
            "sasl.mechanisms": "PLAIN",
            "sasl.username": creds["F1_KAFKA_API_KEY"],
            "sasl.password": creds["F1_KAFKA_API_SECRET"],
            "group.id": f"workshop-status-{account.number}-{time.time_ns()}",
            "enable.auto.commit": False,
        }
    )
    deserialize = _build_deserializer(creds)
    newest: dict | None = None
    try:
        meta = consumer.list_topics("car_telemetry", timeout=10)
        topic = meta.topics.get("car_telemetry")
        if topic is None or topic.error is not None:
            return None
        tails = []
        for partition in topic.partitions:
            low, high = consumer.get_watermark_offsets(
                TopicPartition("car_telemetry", partition), timeout=10
            )
            tails.append(TopicPartition("car_telemetry", partition, max(low, high - 5)))
        consumer.assign(tails)
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            msg = consumer.poll(0.25)
            if msg is None or msg.error() or msg.value() is None:
                continue
            value = deserialize(
                msg.value(), SerializationContext("car_telemetry", MessageField.VALUE)
            )
            if isinstance(value, dict):
                newest = value
                if accept is not None and accept(value):
                    return value
    finally:
        consumer.close()
    return newest


def _event_age(value: dict | None) -> float | None:
    if not value:
        return None
    raw = value.get("event_time")
    if raw is None:
        return None
    try:
        if isinstance(raw, (int, float)):
            stamp = float(raw) / (1000 if float(raw) > 10_000_000_000 else 1)
        else:
            stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
        return max(0.0, time.time() - stamp)
    except (TypeError, ValueError):
        return None


def _fresh_race(
    account: Account,
    previous_race_id: str | None,
    started_at: float,
    timeout: int = FRESH_TELEMETRY_TIMEOUT,
) -> dict | None:
    def is_fresh(event: dict) -> bool:
        age = _event_age(event)
        race_id = str(event.get("race_id") or "")
        event_time = None if age is None else time.time() - age
        return bool(
            race_id
            and race_id != (previous_race_id or "")
            and event_time is not None
            and event_time >= started_at - 5
        )

    return _latest_telemetry(account, wait_seconds=timeout, accept=is_fresh)


def _parallel(accounts: list[Account], action: Callable[[Account], object], workers: int) -> dict[int, object]:
    results: dict[int, object] = {}
    with ThreadPoolExecutor(max_workers=min(workers, len(accounts) or 1)) as pool:
        futures = {pool.submit(action, account): account for account in accounts}
        for future in as_completed(futures):
            account = futures[future]
            try:
                results[account.number] = future.result()
            except Exception as exc:
                results[account.number] = exc
    return results


def _failures(results: dict[int, object]) -> list[str]:
    return [f"account {number}: {value}" for number, value in sorted(results.items()) if isinstance(value, Exception)]


def _stop_accounts(accounts: list[Account], announce: bool = True) -> list[str]:
    def stop(account: Account) -> None:
        describe_exact(account)
        _scale(account, 0)
        if not _wait_count(account, 0, STOP_TIMEOUT):
            raise RuntimeError("did not drain before timeout")

    results = _parallel(accounts, stop, len(accounts))
    errors = _failures(results)
    if announce:
        for account in accounts:
            result = results.get(account.number)
            status = f"FAILED — {result}" if isinstance(result, Exception) else "stopped"
            print(f"  {account.number:03d} {account.prefix}: {status}")
    return errors


def start_races(args: argparse.Namespace) -> None:
    manifest, accounts, full_cohort = _selection(args)
    states: dict[int, dict] = {}
    preflight_errors: list[str] = []
    dirty: list[Account] = []
    for account in accounts:
        try:
            _card(account)
            states[account.number] = describe_exact(account)
            if not account.prepared:
                dirty.append(account)
        except Exception as exc:
            preflight_errors.append(str(exc))
    if preflight_errors:
        raise SystemExit("Start preflight failed; nothing changed:\n  " + "\n  ".join(preflight_errors))
    if full_cohort and dirty and not getattr(args, "allow_unprepared", False):
        selector = ",".join(str(account.number) for account in dirty)
        raise SystemExit(
            "Full-cohort start refused because test account(s) are not clean/prepared. Reset them first:\n"
            f"  uv run workshop reset-races --run-id {manifest.run_id} --accounts {selector}"
        )

    running = [a for a in accounts if states[a.number].get("runningCount", 0) > 0]
    if full_cohort and running and len(running) != len(accounts):
        selector = ",".join(str(a.number) for a in running)
        raise SystemExit(
            "Full-cohort start refused because only part of the cohort is running. Reset it first:\n"
            f"  uv run workshop reset-races --run-id {manifest.run_id} --accounts {selector}"
        )
    newly_started = [a for a in accounts if a not in running]
    previous_results = _parallel(
        newly_started,
        lambda account: str((_latest_telemetry(account, wait_seconds=1) or {}).get("race_id") or ""),
        len(newly_started),
    )
    previous_errors = _failures(previous_results)
    if previous_errors:
        raise SystemExit("Start preflight failed; nothing changed:\n  " + "\n  ".join(previous_errors))
    previous = {number: str(value) for number, value in previous_results.items()}
    started_at = time.time()

    def start(account: Account) -> dict:
        _scale(account, 1)
        if not _wait_count(account, 1, ECS_START_TIMEOUT):
            raise RuntimeError(
                f"ECS task did not become running within {ECS_START_TIMEOUT} seconds"
            )
        event = _fresh_race(account, previous[account.number], started_at)
        if event is None:
            raise RuntimeError(
                "no fresh telemetry carrying a new race_id within "
                f"{FRESH_TELEMETRY_TIMEOUT} seconds"
            )
        return event

    results = _parallel(newly_started, start, len(newly_started))
    # An idempotent start validates recency without requiring a new loop.
    for account in running:
        event = _latest_telemetry(account, wait_seconds=2)
        age = _event_age(event)
        if not event or not event.get("race_id") or age is None or age > FRESH_SECONDS:
            results[account.number] = RuntimeError("running ECS task has no fresh race telemetry")
    errors = _failures(results)
    if errors:
        rollback = _stop_accounts(newly_started, announce=False)
        detail = "\n  ".join(errors + (["rollback: " + "; ".join(rollback)] if rollback else []))
        raise SystemExit("Race start failed; all newly started targets were stopped:\n  " + detail)
    # A full-cohort start is the prepared workshop itself, so an operational
    # stop can resume it without a reset. A subset start is test activity and
    # marks only those targets dirty until their explicit subset reset.
    if newly_started and not full_cohort:
        _set_preparation(
            manifest,
            {a.number for a in newly_started},
            False,
            "running",
        )
    print(f"Run {manifest.run_id}: {len(accounts)} account(s) running and producing fresh race telemetry.")


def stop_races(args: argparse.Namespace) -> None:
    manifest, accounts, _ = _selection(args)
    errors = _stop_accounts(accounts)
    if errors:
        raise SystemExit("Stop incomplete:\n  " + "\n  ".join(errors))
    print(f"Run {manifest.run_id}: {len(accounts)} account(s) stopped and drained.")


def _card_to_tf(card: dict[str, str]) -> dict[str, str]:
    def get(name: str) -> str:
        return card.get(f"F1_{name.upper()}", "")
    return {
        "flink_rest_endpoint": get("flink_rest_endpoint"),
        "flink_api_key": get("flink_api_key"),
        "flink_api_secret": get("flink_api_secret"),
        "organization_id": get("organization_id"),
        "environment_id": get("environment_id"),
        "compute_pool_id": get("compute_pool_id"),
        "environment_name": get("catalog"),
        "cluster_name": get("database"),
        "cluster_bootstrap": get("kafka_bootstrap"),
        "kafka_api_key": get("kafka_api_key"),
        "kafka_api_secret": get("kafka_api_secret"),
        "cluster_id": get("cluster_id"),
    }


def _reset_account(account: Account) -> None:
    tf = _card_to_tf(_card(account))
    problems = delete_flink_statements(tf)
    admin = kafka_admin(tf)
    present = existing_topics(admin)
    # car_telemetry is append-only. race_standings is compacted and remains
    # logically isolated by race_id, so delete-records is intentionally avoided.
    safe_sources = [topic for topic in SOURCE_TOPICS if topic == "car_telemetry"]
    problems += truncate_topics(admin, safe_sources, present)
    if problems:
        raise RuntimeError("; ".join(problems))


def _set_preparation(manifest: RunManifest, selected: set[int], prepared: bool, status: str) -> None:
    raw = json.loads(manifest.path.read_text())
    for item in raw["accounts"]:
        if int(item["account"]) in selected:
            item["prepared"] = prepared
    flags = [bool(item.get("prepared", False)) for item in raw["accounts"]]
    if all(flags):
        overall = "ready"
    elif status in {"reset_failed", "prepare_failed", "running"}:
        overall = status
    else:
        overall = "partially_ready" if any(flags) else "not_prepared"
    raw["preparation_status"] = overall
    raw["prepared_at"] = datetime.now(timezone.utc).isoformat() if all(flags) else None
    manifest.path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n")


def reset_races(args: argparse.Namespace) -> None:
    manifest, accounts, _ = _selection(args)
    errors = _stop_accounts(accounts)
    if not errors:
        errors += _failures(_parallel(accounts, _reset_account, RESET_WORKERS))
    # A reset is a stop boundary even after partial failure.
    errors += _stop_accounts(accounts, announce=False)
    _set_preparation(manifest, {a.number for a in accounts}, not errors, "ready" if not errors else "reset_failed")
    if errors:
        raise SystemExit("Reset incomplete; selected accounts remain stopped:\n  " + "\n  ".join(errors))
    print(f"Run {manifest.run_id}: {len(accounts)} account(s) reset, ready, and stopped.")


def prepare_races(args: argparse.Namespace) -> None:
    if getattr(args, "accounts", ""):
        raise SystemExit("prepare-races always validates the complete cohort; --accounts is not supported")
    manifest, accounts, _ = _selection(args)
    try:
        start_races(argparse.Namespace(run_id=manifest.run_id, accounts="", allow_unprepared=True))
    except BaseException:
        _stop_accounts(accounts, announce=False)
        _set_preparation(manifest, {a.number for a in accounts}, False, "prepare_failed")
        raise
    reset_races(argparse.Namespace(run_id=manifest.run_id, accounts=""))
    print(f"Run {manifest.run_id}: preparation passed; every account is stopped and ready.")


def _state_output(state: dict, name: str) -> object:
    return (state.get("outputs", {}).get(name) or {}).get("value")


def _show_create(tf: dict[str, str], table: str) -> str:
    """Return SHOW CREATE output and always delete the probe statement."""
    from scripts.common.simulator_control import flink_session

    session = flink_session(tf)
    name = session.submit(f"SHOW CREATE TABLE `{table}`")
    try:
        status = session.wait(name, timeout=120)
        phase = (status.get("status") or {}).get("phase")
        if phase == "FAILED":
            detail = (status.get("status") or {}).get("detail", "")
            raise RuntimeError(f"SHOW CREATE TABLE {table} failed: {detail}")
        rows = list(session.results(name, max_rows=2, timeout=60))
        if len(rows) != 1 or not rows[0]:
            raise RuntimeError(f"SHOW CREATE TABLE {table} returned {len(rows)} rows")
        return str(rows[0][0])
    finally:
        session.stop(name)


def _show_create_before_migration(tf: dict[str, str], table: str) -> str:
    """Allow an explicitly targeted migration to recover a partially rebuilt table."""
    try:
        return _show_create(tf, table)
    except RuntimeError as exc:
        if "Cannot find table" in str(exc):
            return ""
        raise


def _validate_race_contract(creates: dict[str, str]) -> list[str]:
    def has_column(sql: str, name: str) -> bool:
        return bool(re.search(rf"`{name}`\s+(?:STRING|VARCHAR\s*\(\s*\d+\s*\))", sql, re.IGNORECASE))

    def has_retention(sql: str) -> bool:
        return bool(
            re.search(
                r"'kafka\.retention\.time'\s*=\s*'(?:24\s*h|1\s*d|86400000\s*ms)'",
                sql,
                re.IGNORECASE,
            )
        )

    problems: list[str] = []
    telemetry = creates.get("car_telemetry", "")
    standings = creates.get("race_standings", "")
    if not has_column(telemetry, "race_id"):
        problems.append("car_telemetry is missing race_id")
    if not re.search(r"`event_time`\s+TIMESTAMP\s*\(\s*3\s*\)", telemetry, re.IGNORECASE):
        problems.append("car_telemetry is missing event_time TIMESTAMP(3)")
    if not has_retention(telemetry):
        problems.append("car_telemetry is missing 24-hour retention")
    if not re.search(r"'kafka\.cleanup-policy'\s*=\s*'delete'", telemetry, re.IGNORECASE):
        problems.append("car_telemetry is not append/delete")
    if not re.search(r"'scan\.startup\.mode'\s*=\s*'earliest-offset'", telemetry, re.IGNORECASE):
        problems.append("car_telemetry is not configured for earliest-offset")
    if not has_column(standings, "race_id"):
        problems.append("race_standings is missing race_id")
    if not re.search(r"`event_time`\s+TIMESTAMP\s*\(\s*3\s*\)", standings, re.IGNORECASE):
        problems.append("race_standings is missing event_time TIMESTAMP(3)")
    if not re.search(
        r"PRIMARY\s+KEY\s*\(\s*`race_id`\s*,\s*`car_number`\s*\)\s+NOT\s+ENFORCED",
        standings,
        re.IGNORECASE,
    ):
        problems.append("race_standings is missing the composite race key")
    if not re.search(
        r"DISTRIBUTED\s+BY(?:\s+HASH)?\s*\(\s*`race_id`\s*,\s*`car_number`\s*\)",
        standings,
        re.IGNORECASE,
    ):
        problems.append("race_standings is not distributed by the composite race key")
    if not has_retention(standings):
        problems.append("race_standings is missing 24-hour retention")
    if not re.search(
        r"'kafka\.cleanup-policy'\s*=\s*'delete-compact'",
        standings,
        re.IGNORECASE,
    ):
        problems.append("race_standings is not compact/delete")
    if not re.search(r"'key\.format'\s*=\s*'avro-registry'", standings, re.IGNORECASE):
        problems.append("race_standings is missing its Avro key format")
    if not re.search(r"'scan\.startup\.mode'\s*=\s*'earliest-offset'", standings, re.IGNORECASE):
        problems.append("race_standings is not configured for earliest-offset")
    return problems


def _migration_tf_env(root: Path, run_dir: Path, account: Account, card: dict[str, str]) -> dict[str, str]:
    """Reconstruct WSA's exact per-account TF_VAR environment without logging it."""
    spec_path = run_dir / "wsa-spec.yaml"
    shared_state_path = run_dir / "terraform/aws-shared/terraform.tfstate"
    if not spec_path.is_file() or not shared_state_path.is_file():
        raise RuntimeError("the WSA spec or shared Terraform state is missing")
    spec = yaml.safe_load(spec_path.read_text()) or {}
    raw_vars = spec.get("terraform_vars") or {}
    if not isinstance(raw_vars, dict):
        raise RuntimeError("wsa-spec.yaml terraform_vars is invalid")

    email = card.get("F1_EMAIL") or card.get("F1_CONSOLE_USERNAME", "")
    if not email:
        raise RuntimeError(f"account {account.number}: card has no attendee owner email")
    replacements = {
        "{N}": str(account.number),
        "{NNN}": f"{account.number:03d}",
        "{email}": email,
    }
    env = os.environ.copy()
    deploy = {k: str(v) for k, v in dotenv_values(root / "credentials.env").items() if v is not None}
    for name in spec.get("env_vars") or []:
        value = deploy.get(str(name)) or env.get(str(name), "")
        if not value:
            raise RuntimeError(f"missing required deploy variable {name}")
        env[str(name)] = value
    for name, raw in raw_vars.items():
        value = str(raw)
        for token, replacement in replacements.items():
            value = value.replace(token, replacement)
        env[f"TF_VAR_{name}"] = value

    shared = json.loads(shared_state_path.read_text())
    mapping = {
        "shared_vpc_id": "vpc_id",
        "shared_subnet_ids": "subnet_ids",
        "shared_postgres_host": "postgres_host",
        "shared_postgres_port": "postgres_port",
        "shared_postgres_dbname": "postgres_dbname",
        "shared_postgres_user": "postgres_user",
        "shared_postgres_password": "postgres_password",
        "shared_ecr_image_uri": "ecr_image_uri",
    }
    for variable, output in mapping.items():
        value = _state_output(shared, output)
        if value in (None, "", []):
            raise RuntimeError(f"shared Terraform output {output} is missing")
        env[f"TF_VAR_{variable}"] = json.dumps(value) if isinstance(value, list) else str(value)
    return env


def _migration_preflight(
    root: Path, manifest: RunManifest, account: Account
) -> tuple[Path, dict[str, str], dict[str, str], dict[str, str]]:
    run_dir = root / "wsa-output" / manifest.run_id
    tf_dir = run_dir / "terraform/aws"
    state_path = tf_dir / "terraform.tfstate.d" / f"account-{account.number:03d}" / "terraform.tfstate"
    staged_topics = run_dir / "terraform/modules/topics/main.tf"
    staged_datagen = tf_dir / "datagen.tf"
    if not tf_dir.is_dir() or not state_path.is_file():
        raise RuntimeError(f"account {account.number}: exact WSA Terraform workspace/state is missing")
    current_topics = root / "terraform/modules/topics/main.tf"
    if not staged_topics.is_file() or staged_topics.read_bytes() != current_topics.read_bytes():
        raise RuntimeError(f"account {account.number}: staged topics module differs from this checkout")
    current_datagen = root / "terraform/aws/datagen.tf"
    if not staged_datagen.is_file() or staged_datagen.read_bytes() != current_datagen.read_bytes():
        raise RuntimeError(f"account {account.number}: staged simulator definition differs from this checkout")

    card = _card(account)
    state = json.loads(state_path.read_text())
    state_email = str(_state_output(state, "console_username") or "")
    card_email = card.get("F1_EMAIL") or card.get("F1_CONSOLE_USERNAME", "")
    if not card_email or not state_email or card_email != state_email:
        raise RuntimeError(f"account {account.number}: owner email does not match card/state")
    card["F1_EMAIL"] = card_email
    checks = {
        "prefix": (account.prefix, card.get("F1_PREFIX"), _state_output(state, "prefix")),
        "environment": (card.get("F1_ENVIRONMENT_ID"), _state_output(state, "environment_id")),
        "cluster": (card.get("F1_CLUSTER_ID"), _state_output(state, "cluster_id")),
    }
    for label, values in checks.items():
        present = [str(value or "") for value in values]
        if not all(present) or len(set(present)) != 1:
            raise RuntimeError(f"account {account.number}: {label} does not match manifest/card/state")
    env = _migration_tf_env(root, run_dir, account, card)
    return tf_dir, card, _card_to_tf(card), env


def _migration_service(tf_dir: Path, account: Account) -> dict | None:
    """Return the live service, tolerating a prior partial Terraform delete."""
    state_path = (
        tf_dir
        / "terraform.tfstate.d"
        / f"account-{account.number:03d}"
        / "terraform.tfstate"
    )
    state = json.loads(state_path.read_text())
    managed = any(
        resource.get("type") == "aws_ecs_service" and resource.get("name") == "simulator"
        for resource in state.get("resources", [])
    )
    try:
        service = describe_exact(account)
    except RuntimeError:
        if not managed:
            return None
        raise
    if service.get("status") == "INACTIVE":
        if managed:
            raise RuntimeError(
                f"account {account.number}: ECS service is INACTIVE but remains in Terraform state"
            )
        return None
    return service


def _stop_for_migration(tf_dir: Path, account: Account) -> list[str]:
    if _migration_service(tf_dir, account) is None:
        return []
    return _stop_accounts([account], announce=False)


def _run_tf(tf_dir: Path, env: dict[str, str], args: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["terraform", *args], cwd=tf_dir, env=env, capture_output=True, text=True, timeout=1800
    )
    if result.returncode != 0:
        raw_detail = (result.stderr or result.stdout).strip()
        clean_detail = re.sub(r"\x1b\[[0-9;]*m", "", raw_detail)
        detail = [line for line in clean_detail.splitlines() if line.strip()]
        tail = "\n".join(detail[-20:]) if detail else "unknown error"
        raise RuntimeError(f"terraform {' '.join(args[:2])} failed:\n{tail}")
    return result


def _validate_saved_plan(
    tf_dir: Path, env: dict[str, str], plan: Path, allowed: set[str]
) -> set[str]:
    shown = _run_tf(tf_dir, env, ["show", "-json", str(plan)])
    document = json.loads(shown.stdout)
    changed = {
        item["address"]
        for item in document.get("resource_changes", [])
        if item.get("change", {}).get("actions") not in (["no-op"], ["read"])
    }
    unexpected = changed - allowed
    if unexpected:
        raise RuntimeError("targeted plan changes unexpected resources: " + ", ".join(sorted(unexpected)))
    return changed


def _saved_target_plan(
    tf_dir: Path,
    env: dict[str, str],
    account: Account,
    targets: set[str],
    *,
    destroy: bool,
) -> Path:
    workspace = f"account-{account.number:03d}"
    _run_tf(tf_dir, env, ["workspace", "select", workspace])
    selected = _run_tf(tf_dir, env, ["workspace", "show"]).stdout.strip()
    if selected != workspace:
        raise RuntimeError(f"account {account.number}: selected Terraform workspace is {selected!r}")
    suffix = "detach" if destroy else "rebuild"
    plan = tf_dir / f"{workspace}-race-contract-{suffix}.tfplan"
    args = ["plan"]
    if destroy:
        args.append("-destroy")
    for target in sorted(targets):
        args.append(f"-target={target}")
    args.append(f"-out={plan}")
    _run_tf(tf_dir, env, args)
    _validate_saved_plan(tf_dir, env, plan, targets)
    # A provider failure can remove only part of a saved destroy plan. A retry
    # is safe when the remaining changes are a subset of the same exact
    # allowlist; _validate_saved_plan still rejects every unrelated address.
    return plan


def _apply_saved_plan(tf_dir: Path, env: dict[str, str], plan: Path) -> None:
    _run_tf(tf_dir, env, ["apply", "-auto-approve", str(plan)])


def _delete_lab_rtce(card: dict[str, str]) -> list[str]:
    if not card.get("F1_RTCE_API_KEY") or not card.get("F1_RTCE_API_SECRET"):
        return []
    from scripts.participant.rtce import _run_cli, _topic_name, list_registrations

    registered = {_topic_name(row) for row in list_registrations(card)}
    targets = [topic for topic in LAB_TOPICS if topic in registered]
    if not targets:
        return []
    result = _run_cli(
        card,
        [
            "confluent", "rtce", "rtce-topic", "delete", *targets,
            "--environment", card["F1_ENVIRONMENT_ID"],
            "--cluster", card["F1_CLUSTER_ID"],
            "--force",
        ],
    )
    if result.returncode == 0:
        return []
    detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "unknown CLI error"
    return [f"could not remove Lab RTCE topics ({detail})"]


def migrate_race_contract(args: argparse.Namespace) -> None:
    """Destructively rebuild the race source contract for one to three exact accounts."""
    if not str(getattr(args, "accounts", "") or "").strip():
        raise SystemExit("migrate-race-contract requires an explicit --accounts selection (maximum three)")
    seconds_per_lap = getattr(args, "seconds_per_lap", None)
    if seconds_per_lap is not None:
        from scripts.common.deployment_meta import validate_seconds_per_lap

        seconds_per_lap, problem = validate_seconds_per_lap(seconds_per_lap)
        if problem:
            raise SystemExit(problem)
    root = get_project_root()
    manifest, accounts, _ = _selection(args)
    from scripts.common.login_checks import ensure_confluent_login

    deploy_creds = {
        key: str(value)
        for key, value in dotenv_values(root / "credentials.env").items()
        if value is not None
    }
    if not ensure_confluent_login(deploy_creds, interactive=False):
        raise SystemExit("Migration preflight failed; run `confluent login --save` and retry")
    prepared = []
    try:
        for account in accounts:
            tf_dir, card, tf, env = _migration_preflight(root, manifest, account)
            if seconds_per_lap is not None:
                env["TF_VAR_seconds_per_lap"] = str(seconds_per_lap)
            _migration_service(tf_dir, account)
            before = {table: _show_create_before_migration(tf, table) for table in SOURCE_TOPICS}
            if _validate_race_contract(before):
                detach = _saved_target_plan(
                    tf_dir, env, account, MIGRATION_DESTROY_TARGETS, destroy=True
                )
            else:
                detach = None
            prepared.append((account, tf_dir, card, tf, env, detach))
    except Exception as exc:
        raise SystemExit(f"Migration preflight failed; nothing changed:\n  {exc}") from exc

    for account, tf_dir, card, tf, env, detach in prepared:
        errors = _stop_for_migration(tf_dir, account)
        _set_preparation(manifest, {account.number}, False, "migration_failed")
        if errors:
            raise SystemExit("Migration stopped before table changes:\n  " + "\n  ".join(errors))
        try:
            if detach is not None:
                _apply_saved_plan(tf_dir, env, detach)
                problems = delete_flink_statements(tf)
                problems += _delete_lab_rtce(card)
                if not problems:
                    problems += drop_flink_objects(tf, [*LAB_DROPS, *SOURCE_DROPS])
                admin = kafka_admin(tf)
                present = existing_topics(admin)
                if present is None:
                    problems.append("could not confirm exact Kafka topics before deletion")
                else:
                    for topic in [*LAB_TOPICS, *SOURCE_TOPICS]:
                        problems += delete_topic_and_subjects(
                            topic, tf["environment_id"], tf["cluster_id"], topic in present
                        )
                if problems:
                    raise RuntimeError("; ".join(problems))

            rebuild = _saved_target_plan(tf_dir, env, account, MIGRATION_APPLY_TARGETS, destroy=False)
            _apply_saved_plan(tf_dir, env, rebuild)
            after = {table: _show_create(tf, table) for table in SOURCE_TOPICS}
            problems = _validate_race_contract(after)
            if problems:
                raise RuntimeError("post-migration contract check failed: " + "; ".join(problems))
        except Exception as exc:
            _stop_for_migration(tf_dir, account)
            raise SystemExit(
                f"Account {account.number} migration failed; it remains stopped and unprepared:\n  {exc}"
            ) from exc
        final_errors = _stop_for_migration(tf_dir, account)
        if final_errors:
            raise SystemExit("Migration applied but final stop failed:\n  " + "\n  ".join(final_errors))
        print(f"  {account.number:03d} {account.prefix}: race contract migrated; stopped and unprepared")
    print(f"Run {manifest.run_id}: migrated {len(accounts)} account(s). Reset/rehearse them before cohort start.")


def prepare_social_feed(args: argparse.Namespace) -> None:
    """Build Lab 3/4 only for the manifest's organizer-controlled account 50."""
    from scripts.common.simulator_control import create_lab_objects

    run_id = str(getattr(args, "run_id", "") or "").strip()
    number = int(getattr(args, "account", 0) or 0)
    if not run_id:
        raise SystemExit("prepare-social-feed requires an explicit --run-id")
    if number != 50:
        raise SystemExit("prepare-social-feed is restricted to organizer-controlled account 50")

    root = get_project_root()
    manifest = resolve_manifest(root, run_id)
    matches = [account for account in manifest.accounts if account.number == number]
    if len(matches) != 1:
        raise SystemExit(f"Run {run_id} must contain exactly one manifest entry for account 50")
    account = matches[0]
    if account.prefix != "f1wp050":
        raise SystemExit(
            f"Run {run_id} account 50 has unexpected prefix {account.prefix!r}; expected 'f1wp050'"
        )
    if not all((account.ecs_cluster, account.ecs_service, str(account.credential_card))):
        raise SystemExit(f"Run {run_id} account 50 manifest entry is incomplete")

    # Complete every read-only preflight before stopping or changing anything.
    card = _card(account)
    describe_exact(account)
    stop_errors = _stop_accounts([account], announce=False)
    if stop_errors:
        raise SystemExit("Account 50 could not be stopped; no Flink changes were made:\n  " + "\n  ".join(stop_errors))

    tf = _card_to_tf(card)
    failure: str | None = None
    built = False
    try:
        problems = delete_flink_statements(tf)
        if not problems:
            problems += drop_flink_objects(
                tf,
                [("drop-pit-agent", "DROP AGENT IF EXISTS `pit_strategy_agent`")],
            )
        if problems:
            failure = "; ".join(problems)
        elif not create_lab_objects(tf, root):
            failure = "Lab 3/4 statements did not reach their expected states"
        else:
            built = True
    except Exception as exc:
        failure = str(exc)
    finally:
        # A failed partial build must not leave a restartable INSERT running.
        if not built:
            cleanup = delete_flink_statements(tf)
            if cleanup:
                failure = "; ".join(filter(None, [failure, *cleanup]))
        final_stop = _stop_accounts([account], announce=False)
        if final_stop:
            failure = "; ".join(filter(None, [failure, *final_stop]))
            if built:
                cleanup = delete_flink_statements(tf)
                if cleanup:
                    failure = "; ".join(filter(None, [failure, *cleanup]))
            built = False
        _set_preparation(
            manifest,
            {account.number},
            False,
            "social_feed_prepared" if built else "prepare_failed",
        )

    if not built or failure:
        raise SystemExit(f"Account 50 social-feed preparation failed; its ECS service is stopped:\n  {failure}")
    print(
        f"Run {manifest.run_id}: account 50 Lab 3/4 statements are ready; "
        "the race remains stopped."
    )


def _flink_health(card: dict[str, str]) -> str:
    from scripts.reset import _get_json, flink_api

    url, headers = flink_api(_card_to_tf(card))
    data = _get_json(f"{url}?page_size=100", headers)
    bad = [s for s in data.get("data", []) if s.get("status", {}).get("phase") == "FAILED"]
    return "failed" if bad else "ok"


def _rtce_status(card: dict[str, str]) -> str:
    if not card.get("F1_RTCE_API_KEY") or not card.get("F1_RTCE_API_SECRET"):
        return "unknown"
    try:
        from scripts.participant.rtce import _topic_name, _topic_status, list_registrations

        rows = list_registrations(card)
    except (SystemExit, Exception):
        return "unknown"
    registered = {
        _topic_name(row): _topic_status(row)
        for row in rows
        if _topic_name(row) in {"car_state", "pit_decisions"}
    }
    if not registered:
        return "not-registered"
    return ",".join(f"{name}:{registered[name]}" for name in sorted(registered))


def race_status(args: argparse.Namespace) -> None:
    _, accounts, _ = _selection(args)
    print("account prefix ecs current_race lap last_event_age flink rtce")
    failed = False
    for account in accounts:
        try:
            service = describe_exact(account)
            event = _latest_telemetry(account)
            age = _event_age(event)
            card = _card(account)
            ecs = f"{service.get('runningCount', 0)}/{service.get('desiredCount', 0)}"
            race_id = str((event or {}).get("race_id") or "-")
            lap = str((event or {}).get("lap") or "-")
            age_text = "-" if age is None else f"{age:.0f}s"
            print(
                f"{account.number:03d} {account.prefix} {ecs} {race_id} {lap} {age_text} "
                f"{_flink_health(card)} {_rtce_status(card)}"
            )
        except Exception as exc:
            failed = True
            print(f"{account.number:03d} {account.prefix} ERROR {exc}")
    if failed:
        raise SystemExit(1)


def add_lifecycle_arguments(parser: argparse.ArgumentParser, *, allow_accounts: bool = True) -> None:
    parser.add_argument("--run-id", default="", help="Run manifest ID (required when multiple runs exist)")
    if allow_accounts:
        parser.add_argument(
            "--accounts",
            default="",
            help="At most three account numbers/ranges; omitted selects the complete cohort",
        )


def add_prepare_social_feed_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-id", required=True, help="Exact workshop run manifest ID")
    parser.add_argument("--account", required=True, type=int, help="Organizer feed account (must be 50)")


def add_migration_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-id", default="", help="Run manifest ID (required when multiple runs exist)")
    parser.add_argument(
        "--accounts",
        required=True,
        help="One to three exact account numbers/ranges, for example 50 or 48-50",
    )
    parser.add_argument(
        "--seconds-per-lap",
        type=int,
        default=None,
        help="Optional simulator pacing override for only the selected accounts (minimum 10)",
    )
