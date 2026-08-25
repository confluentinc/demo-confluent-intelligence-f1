"""
Reset a deployment's lab state for a fresh run of the stream-processing labs.

Gives a blank slate to start a new race from. Stops the race feed, stops running
Flink statements, drops the objects the labs create — the `car_state` and
`pit_decisions` tables, the `pit_strategy_agent`, and their backing topics +
Schema Registry subjects — and clears the race data already sitting in the source
topics.

    uv run reset                          # blank slate, feed left stopped
    uv run reset --with-labs              # ...then rebuild LAB 3/4 and restart the race
    uv run reset --keep-source            # keep the accumulated race data
    uv run reset --track selfservice      # when both tracks are deployed here

**The feed is stopped first, always.** Clearing the source topics while a producer
is still running puts the records straight back, so the truncation used to be a
no-op that reported success. On the standalone track that means scaling *this*
deployment's ECS service to zero and waiting for its task to die; on the
self-service track the producer is the user's own `uv run f1-race`, which this
command can only detect and refuse to race against (`--force` overrides).

**Plain reset does not restart the race.** `race_standings` has no
`scan.startup.mode` override (terraform/modules/topics/main.tf), so it is read
from `latest`: a LAB 3 statement submitted after the race starts never sees the
standings versions its first laps need, and `car_state` silently drops them. Reset
leaves the feed stopped and tells you to submit LAB 3 before `uv run race start`.
`--with-labs` does both, in that order, for you.

The source topics are *truncated*, not deleted: `car_telemetry` and
`race_standings` are Terraform-owned (created by a Flink CREATE TABLE, with
registered schemas), so deleting them would break Terraform state and the
schemas the simulator produces against. Deleting their records leaves the topic,
its config, and its subjects intact while removing every record — which is what
"free from previous races" actually requires. Pass --keep-source to skip it.

`race_standings` is compacted (it is the keyed upsert side of the LAB 3 temporal
join) and Kafka refuses delete-records on a compacted topic. That is reported,
not worked around: compaction already reduces it to the latest row per
car_number, lap 0 of the next race overwrites all 22 keys, and the temporal join
resolves versions by event time so a finished race's rows can never be selected
by newer telemetry.

Every step reports whether it worked, and **anything that did not work exits
nonzero**. A reset that half-succeeded and printed "Reset complete" was worse than
a failure: the next lab run inherited a `car_state` table that was never dropped
or a topic that was never cleared, with nothing in the output to explain it.

This is a Confluent-only operation — it does not run Terraform.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from base64 import b64encode
from pathlib import Path

from dotenv import dotenv_values

from scripts.common.deployment_meta import (
    SELFSERVICE,
    STANDALONE,
    TRACKS,
    Track,
    has_state,
    tf_state_path,
)
from scripts.common.login_checks import ensure_confluent_login
from scripts.common.simulator_control import (
    LAB_BUILDS,
    create_lab_objects,
    flink_session,
    has_ecs,
    scale_simulator,
    wait_for_drain,
    wait_for_running,
)
from scripts.common.terraform import get_project_root, run_terraform_output

# DEFAULT_REGION only — never the fan-out helpers beside it. `scale_all_services`
# walks every `river-racing*` cluster in the AWS account, so reaching for it here
# would stop twenty attendees' feeds to reset one environment.
from scripts.instructor._common import DEFAULT_REGION

__all__ = [
    "LAB_BUILDS",
    "LAB_DROPS",
    "LAB_TOPICS",
    "SOURCE_TOPICS",
    "create_lab_objects",
    "flink_session",
    "has_ecs",
    "scale_simulator",
    "wait_for_drain",
    "wait_for_running",
]

# Topics created by attendees while running the labs — deleted only (they are
# recreated when the attendee re-runs the Flink jobs).
LAB_TOPICS = ["car_state", "pit_decisions"]

# Terraform-owned source topics the simulator produces to. Truncated, never
# deleted — see the module docstring.
SOURCE_TOPICS = ["car_telemetry", "race_standings"]

# Flink objects created by the labs, dropped before their topics. Labels become
# part of the Flink statement name, which rejects underscores (HTTP 400).
#
# Order is deliberately NOT the dependency order that docs/tracks/STANDALONE-DEMO.md
# gives for dropping these by hand (`pit_decisions` first, because its INSERT
# reads `car_state`). It does not need to be: delete_flink_statements() has
# already stopped every running lab statement by the time these are submitted, so
# no reader is left to break, and `DROP TABLE IF EXISTS` on an idle table has no
# dependants. Both orders are correct; the doc's is the one that also works while
# the labs are running.
LAB_DROPS = [
    ("drop-car-state",     "DROP TABLE IF EXISTS `car_state`"),
    ("drop-pit-decisions", "DROP TABLE IF EXISTS `pit_decisions`"),
    ("drop-pit-agent",     "DROP AGENT IF EXISTS `pit_strategy_agent`"),
]

# A DDL statement is done when it reaches COMPLETED. RUNNING is *not* success here
# — create_lab_objects() accepts it because a streaming `INSERT INTO ... SELECT`
# never completes, which is the opposite situation.
DROP_SUCCESS_PHASE = "COMPLETED"
DROP_TERMINAL_PHASES = {"COMPLETED", "FAILED", "STOPPED"}

# CLI stderr that means "the thing I was asked to delete isn't there" — the normal
# outcome for a lab topic or subject when the attendee never ran LAB 3, and for
# every lab topic once the DROP TABLE above has taken its backing topic with it.
# Anything outside this set (auth, authorization, cluster unreachable) is a real
# failure and is reported verbatim so the next live run can widen this list.
BENIGN_MISSING = (
    "not found",
    "does not exist",
    "unknown topic",
    "no such",
    "40401",
    "40403",
)

# argv tokens that mean a local `uv run f1-race` is producing: the console script
# (`f1-race`, or the absolute path to it that uv actually execs) and the module
# form. Matched per whitespace-separated token rather than as a substring, so a
# command that merely *mentions* f1-race — a grep, an editor, a shell wrapper
# whose own command line quotes it — doesn't read as a running race. Editing
# scripts/selfservice/race.py is not a match either, which is why the module form
# is the dotted path and not the filename.
LOCAL_RACE_TOKENS = ("f1-race", "scripts.selfservice.race")

# LAB_BUILDS, scale_simulator, wait_for_drain, wait_for_running, flink_session
# and create_lab_objects live in scripts/common/simulator_control.py — `uv run
# race`, `uv run deploy --with-labs` and `uv run selfservice up --with-labs` need
# the same behavior. They are re-exported above so existing importers of
# `scripts.reset` keep working. That module also owns the `F1_ANOMALY_FN` switch
# choosing which LAB 3 implementation `--with-labs` submits (ARIMA by default,
# Granite/AI_DETECT_ANOMALIES as an opt-in).


def run_cli(cmd: list[str], confirm: bool = False) -> tuple[int, str, str]:
    result = subprocess.run(cmd, capture_output=True, text=True, input="y\n" if confirm else None)
    return result.returncode, result.stdout, result.stderr


def first_error_line(stderr: str) -> str:
    return stderr.strip().splitlines()[0] if stderr.strip() else ""


def is_benign_missing(stderr: str) -> bool:
    """True when a delete failed only because the target was already gone."""
    lowered = stderr.lower()
    return any(marker in lowered for marker in BENIGN_MISSING)


# --- deployment selection ---------------------------------------------------


def select_track(root: Path, requested: str | None) -> Track:
    """Which deployment this reset acts on. Ambiguity is an error, never a guess.

    Both solo tracks can be deployed from one checkout, and reset is destructive:
    it drops tables and deletes records. Picking the "probably right" one would
    eventually wipe the wrong environment, so with state for both tracks and no
    `--track`, this exits and lists them — the same contract `resolve_card()` uses
    for credential cards.
    """
    if requested:
        track = TRACKS[requested]
        if not has_state(root, track):
            print(f"No Terraform state for the {track.name} track at {tf_state_path(root, track)}")
            print(f"({track.label})")
            sys.exit(1)
        return track

    deployed = [t for t in (STANDALONE, SELFSERVICE) if has_state(root, t)]

    if not deployed:
        print("No Terraform state found for either solo track:")
        for track in (STANDALONE, SELFSERVICE):
            print(f"  {tf_state_path(root, track)}")
        print("\n`uv run reset` needs the state written by `uv run deploy` or")
        print("`uv run selfservice up`, which only exists on the machine that ran them")
        print("(it is gitignored). If you're a workshop attendee, ask your instructor to")
        print("reset your environment — a wsa-provisioned workshop keeps its state in")
        print("wsa's own run directory, out of this checkout's reach.")
        sys.exit(1)

    if len(deployed) > 1:
        print("Both solo tracks are deployed from this checkout:")
        for track in deployed:
            print(f"  --track {track.name:<12} {track.label}")
        print("\nReset drops tables and deletes records, so it won't guess which one you")
        print("meant. Name it with --track.")
        sys.exit(1)

    return deployed[0]


def flatten_outputs(tf: dict) -> dict:
    """Flat Terraform outputs, backfilled from the nested attendee_credentials map.

    `terraform/aws` publishes `kafka_api_key` / `kafka_api_secret` as flat root
    outputs; `terraform/self-service` publishes them only inside
    `attendee_credentials` (terraform/self-service/outputs.tf). Merging here lets
    one code path serve both tracks without either Terraform root changing —
    they are staged into `wsa` builds, so edits there carry workshop risk this
    command has no business taking on.

    Flat outputs win: they are the contract wsa-spec-aws.yaml reads.
    """
    merged = dict(tf)
    for key, value in (tf.get("attendee_credentials") or {}).items():
        if not merged.get(key):
            merged[key] = value
    return merged


# --- local producer detection (self-service) --------------------------------


def is_local_race(args: str) -> bool:
    """Whether one process command line is a running `f1-race`. See LOCAL_RACE_TOKENS."""
    for token in args.split():
        for marker in LOCAL_RACE_TOKENS:
            if token == marker or token.endswith(f"/{marker}"):
                return True
    return False


def local_race_processes() -> list[str]:
    """Command lines of any local `uv run f1-race` currently producing.

    The self-service track has no ECS service to scale down — its race feed is a
    process on the user's own machine — so reset can't stop the producer itself.
    It can at least refuse to pretend it cleared topics that are being refilled.
    Uses `ps` rather than adding a psutil dependency for one check.

    Best-effort by design: a false positive costs one `--force`, while a missed
    producer costs a reset that silently didn't reset anything.
    """
    try:
        result = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=15)
    except Exception:
        return []

    me = os.getpid()
    found = []
    for line in result.stdout.splitlines():
        pid_text, _, args = line.strip().partition(" ")
        if not pid_text.isdigit() or int(pid_text) == me:
            continue
        if is_local_race(args):
            found.append(f"{pid_text}  {args.strip()}")
    return found


# --- Flink -----------------------------------------------------------------


def flink_api(tf: dict) -> tuple[str, dict]:
    """(statements endpoint, auth headers) for this deployment's Flink REST API."""
    rest = tf["flink_rest_endpoint"].rstrip("/")
    token = b64encode(f"{tf['flink_api_key']}:{tf['flink_api_secret']}".encode()).decode()
    url = f"{rest}/sql/v1/organizations/{tf['organization_id']}/environments/{tf['environment_id']}/statements"
    return url, {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def _get_json(url: str, headers: dict) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers)) as resp:
        return json.loads(resp.read())


def delete_flink_statements(tf: dict, timeout: int = 90) -> list[str]:
    """Stop every non-terminal Flink statement, then wait until they are gone.

    The wait exists so the DROPs that follow have no readers left: a running
    `INSERT INTO pit_decisions` selects from `car_state`, and a DELETE only moves
    a statement into DELETING — it does not mean the job has released anything
    yet. Returns a list of problems (empty when clean).
    """
    url, headers = flink_api(tf)
    problems: list[str] = []

    try:
        data = _get_json(f"{url}?page_size=100", headers)
    except Exception as e:
        print(f"  Could not list Flink statements: {e}")
        return [f"could not list Flink statements ({e})"]

    running = [
        s["name"]
        for s in data.get("data", [])
        if s.get("status", {}).get("phase") not in ("COMPLETED", "FAILED", "STOPPED", "DELETING")
    ]

    if not running:
        print("  No running Flink statements found")
        return problems

    for name in running:
        try:
            urllib.request.urlopen(urllib.request.Request(f"{url}/{name}", headers=headers, method="DELETE"))
            print(f"  {name}: deleted")
        except Exception as e:
            print(f"  {name}: failed ({e})")
            problems.append(f"could not stop Flink statement {name} ({e})")

    deadline = time.time() + timeout
    pending = set(running)
    while pending and time.time() < deadline:
        try:
            data = _get_json(f"{url}?page_size=100", headers)
        except Exception as e:
            print(f"  Could not confirm the statements stopped: {e}")
            return [*problems, f"could not confirm Flink statements stopped ({e})"]
        alive = {
            s["name"]
            for s in data.get("data", [])
            if s["name"] in pending and s.get("status", {}).get("phase") != "DELETING"
        }
        if not alive:
            pending.clear()
            break
        pending = alive
        time.sleep(3)

    if pending:
        print(f"  Warning: still shutting down after {timeout}s: {', '.join(sorted(pending))}")
        problems.append(f"Flink statements did not stop within {timeout}s: {', '.join(sorted(pending))}")
    return problems


def drop_flink_objects(tf: dict, drops: list, timeout: int = 180) -> list[str]:
    """Submit each DROP and wait for it to reach COMPLETED. Returns problems.

    Waiting is the whole point. Submission used to be fire-and-forget, which broke
    both things that run next:

    - `--with-labs` submits the CREATEs moments later. A DROP still PENDING loses
      that race and the rebuild fails with "table already exists" — an error that
      says nothing about the actual cause.
    - deleting a lab topic and its Schema Registry subjects before the DROP lands
      leaves Flink still believing the table exists, over a topic that no longer
      does.

    Each statement is deleted once it completes: reset would otherwise leave three
    more COMPLETED statements in the environment on every run, and the name
    (second-resolution) could collide with a re-run inside the same second.
    """
    url, headers = flink_api(tf)
    problems: list[str] = []

    for label, sql in drops:
        name = f"reset-{label}-{int(time.time() * 1000) % 10**9}"
        body = json.dumps(
            {
                "name": name,
                "spec": {
                    "statement": sql,
                    "compute_pool_id": tf["compute_pool_id"],
                    "properties": {
                        "sql.current-catalog": tf["environment_name"],
                        "sql.current-database": tf["cluster_name"],
                    },
                },
            }
        ).encode()

        try:
            urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers, method="POST"))
        except Exception as e:
            detail = e.read().decode()[:300] if isinstance(e, urllib.error.HTTPError) else str(e)
            print(f"  {sql}: submit failed — {detail}")
            problems.append(f"{sql} was not submitted ({detail})")
            continue

        phase, detail = _wait_for_phase(f"{url}/{name}", headers, timeout)
        if phase == DROP_SUCCESS_PHASE:
            print(f"  {sql}: {phase}")
            try:  # tidy-up only; a leftover COMPLETED statement is harmless
                urllib.request.urlopen(urllib.request.Request(f"{url}/{name}", headers=headers, method="DELETE"))
            except Exception:
                pass
            continue

        print(f"  {sql}: {phase}{f' — {detail}' if detail else ''}")
        problems.append(f"{sql} did not complete (last phase {phase}{f': {detail}' if detail else ''})")

    return problems


def _wait_for_phase(statement_url: str, headers: dict, timeout: int) -> tuple[str, str]:
    """Poll one statement until it is terminal. Returns (last phase, detail).

    The last observed phase is reported on timeout too, so a run against a live
    environment diagnoses itself: if Confluent ever settles a DDL statement
    somewhere other than COMPLETED, the message names that phase instead of
    saying only "timed out".
    """
    phase, detail = "UNKNOWN", ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status = _get_json(statement_url, headers).get("status", {})
        except Exception as e:
            return "UNREACHABLE", str(e)
        phase = status.get("phase", "UNKNOWN")
        detail = (status.get("detail") or "").strip()
        if phase in DROP_TERMINAL_PHASES:
            return phase, detail
        time.sleep(2)
    return f"TIMED OUT after {timeout}s in {phase}", detail


# --- Kafka / Schema Registry ------------------------------------------------


def kafka_admin(tf: dict):
    """An AdminClient for this deployment's cluster. Raises if the keys are absent.

    Built from the Terraform outputs rather than the credential card: the card is
    resolved from credentials.env and can point at a different environment than
    the state file this reset was pointed at, and both halves have to act on
    exactly one environment (same reasoning as simulator_control.flink_session).
    """
    from confluent_kafka.admin import AdminClient

    return AdminClient(
        {
            "bootstrap.servers": tf["cluster_bootstrap"].split("://", 1)[-1],
            "security.protocol": "SASL_SSL",
            "sasl.mechanisms": "PLAIN",
            "sasl.username": tf["kafka_api_key"],
            "sasl.password": tf["kafka_api_secret"],
        }
    )


def existing_topics(admin) -> set[str] | None:
    """Every topic in the cluster, or None if the metadata request failed.

    Asking beforehand is what keeps reset's exit code meaningful: after the DROPs
    above, Flink has already deleted the lab topics, so a CLI "topic not found"
    is the *normal* outcome — not something to classify from an error string and
    certainly not something to fail on.
    """
    try:
        return set(admin.list_topics(timeout=30).topics)
    except Exception as e:
        print(f"  Could not list topics: {e}")
        return None


def delete_topic_and_subjects(topic: str, env_id: str, cluster_id: str, exists: bool | None) -> list[str]:
    """Delete a lab topic (if it is still there) and hard-delete its SR subjects.

    Dropping a Flink table deletes its backing topic but leaves `<topic>-key` and
    `<topic>-value` behind, and a soft-deleted subject still blocks re-registering
    an incompatible schema — hence the `--permanent` pass.
    """
    problems: list[str] = []

    if exists is False:
        print(f"  Topic {topic}: already gone")
    else:
        rc, _, stderr = run_cli(
            [
                "confluent", "kafka", "topic", "delete", topic,
                "--environment", env_id,
                "--cluster", cluster_id,
            ],
            confirm=True,
        )
        if rc == 0:
            print(f"  Topic {topic}: deleted")
        elif is_benign_missing(stderr):
            print(f"  Topic {topic}: already gone")
        else:
            print(f"  Topic {topic}: FAILED — {first_error_line(stderr)}")
            problems.append(f"could not delete topic {topic} ({first_error_line(stderr)})")

    for subject in [f"{topic}-key", f"{topic}-value"]:
        base_cmd = [
            "confluent", "schema-registry", "schema", "delete",
            "--subject", subject,
            "--version", "all",
            "--environment", env_id,
        ]
        soft_rc, _, soft_err = run_cli(base_cmd, confirm=True)
        hard_rc, _, hard_err = run_cli([*base_cmd, "--permanent"], confirm=True)

        if hard_rc == 0:
            print(f"  SR {subject}: cleaned")
        elif is_benign_missing(hard_err) and (soft_rc == 0 or is_benign_missing(soft_err)):
            print(f"  SR {subject}: already gone")
        else:
            failure = first_error_line(hard_err) or first_error_line(soft_err)
            print(f"  SR {subject}: FAILED — {failure}")
            problems.append(f"could not delete Schema Registry subject {subject} ({failure})")

    return problems


def truncate_topics(admin, topics: list[str], present: set[str] | None) -> list[str]:
    """Delete every record in `topics`, leaving the topics and schemas in place.

    Uses the Kafka delete-records admin API (there is no `confluent kafka topic
    delete-records` CLI equivalent) to move each partition's low watermark up to
    its high watermark.
    """
    from confluent_kafka import OFFSET_END, TopicPartition

    problems: list[str] = []

    for topic in topics:
        if present is not None and topic not in present:
            # Terraform owns these topics, so a missing one means someone dropped
            # the Flink table by hand. Reset can't deliver its blank slate and
            # LAB 3 has nothing to read — say so instead of reporting success.
            print(f"  Topic {topic}: MISSING — nothing to clear")
            problems.append(f"source topic {topic} does not exist (re-apply Terraform to recreate it)")
            continue

        try:
            meta = admin.list_topics(topic=topic, timeout=30)
        except Exception as e:
            print(f"  Topic {topic}: metadata lookup failed: {e}")
            problems.append(f"could not read metadata for {topic} ({e})")
            continue

        topic_meta = meta.topics.get(topic)
        if topic_meta is None or topic_meta.error is not None:
            print(f"  Topic {topic}: not readable ({topic_meta.error if topic_meta else 'no metadata'})")
            problems.append(f"could not read {topic} to clear it")
            continue

        # OFFSET_END deletes up to each partition's current high watermark.
        partitions = [TopicPartition(topic, p, OFFSET_END) for p in topic_meta.partitions]
        futures = admin.delete_records(partitions)

        deleted, errors = 0, []
        for tp, future in futures.items():
            try:
                future.result()
                deleted += 1
            except Exception as e:
                errors.append((tp.partition, e))

        if not errors:
            print(f"  Topic {topic}: cleared ({deleted} partition(s))")
            continue

        # Kafka rejects delete-records on a compacted topic. `race_standings` is
        # compacted because it is the keyed upsert side of the LAB 3 temporal
        # join, so this is expected and not worth alarming anyone over: a
        # compacted topic only resolves to the latest value per car_number, and
        # the temporal join picks the version as of each telemetry row's
        # event_time. Rows from a finished race are strictly older than any new
        # telemetry, so they can never be selected, and lap 0 of the next race
        # overwrites all 22 keys anyway. Expected, so NOT a failure.
        if any("POLICY_VIOLATION" in str(e) for _, e in errors):
            print(f"  Topic {topic}: kept (compacted topic — records can't be deleted)")
            print("    Harmless: the next race overwrites every key on lap 0, and the")
            print("    temporal join can't reach versions older than the new telemetry.")
            continue

        for partition, e in errors:
            print(f"    partition {partition}: {e}")
        print(f"  Topic {topic}: {deleted} partition(s) cleared, {len(errors)} failed")
        problems.append(f"could not clear {len(errors)} partition(s) of {topic}")

    return problems


# --- next-step hints -------------------------------------------------------


def f1_race_command(root: Path, creds: dict) -> str:
    """The exact `uv run f1-race` command for this checkout.

    `f1-race` resolves its own card, so the bare command is right whenever
    resolution is unambiguous. When the self-service card is one of several and
    the F1_CARD pointer isn't already aimed at it, `--creds` is spelled out —
    otherwise the printed command would hard-exit with "Multiple credential cards
    found". Globbed directly instead of calling resolve_card(), which exits the
    process on ambiguity rather than returning.

    Every failure falls back to the bare command. This runs at the very end of a
    *successful* reset, so raising here — on a malformed `F1_CARD` value, say —
    would turn a clean reset into a traceback and a nonzero exit with all the
    destructive work already done. A less specific hint is the cheaper outcome.
    """
    try:
        cards = sorted((root / "runs" / SELFSERVICE.name / "credentials").glob("*.env"))
        if len(cards) != 1:
            return "uv run f1-race"

        card = cards[0]
        pointer = creds.get("F1_CARD")
        if pointer and (root / pointer).resolve() == card.resolve():
            return "uv run f1-race"
        if len(list(root.glob("runs/*/credentials/*.env"))) == 1:
            return "uv run f1-race"
        return f"uv run f1-race --creds {card.relative_to(root)}"
    except Exception:
        return "uv run f1-race"


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset a deployment's lab state for a fresh stream-processing run")
    parser.add_argument(
        "--track",
        choices=sorted(TRACKS),
        help="Which deployment to reset. Required only when both tracks have Terraform state here.",
    )
    parser.add_argument(
        "--keep-source",
        action="store_true",
        help=(
            "Leave car_telemetry / race_standings data in place (default: clear them for a blank "
            "slate). Also leaves the race feed running, unless --with-labs needs it stopped."
        ),
    )
    parser.add_argument(
        "--with-labs",
        action="store_true",
        help=(
            "Also rebuild the lab objects from docs/demo-reference/ and restart the race — a "
            "ready-to-demo environment in one command. Omit for the workshop, where attendees "
            "build the labs themselves."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Proceed even though a local `uv run f1-race` is still producing (self-service only).",
    )
    args = parser.parse_args()

    print("=== F1 Lab Reset ===\n")

    root = get_project_root()
    creds_file = root / "credentials.env"
    creds = dotenv_values(creds_file) if creds_file.exists() else {}

    # Resolved before anything interactive: the per-deployment Terraform state is
    # the source of truth for the environment this touches, and it only exists on
    # the machine that deployed it. Checked ahead of the Confluent login so
    # someone who runs this by mistake gets the real answer instead of a prompt
    # for a login they don't have.
    track = select_track(root, args.track)
    print(f"Track: {track.name} — {track.label}")

    if not ensure_confluent_login(creds, creds_file=creds_file, interactive=True):
        sys.exit(1)

    for k, v in creds.items():
        if v:
            os.environ[k] = v
    if creds.get("TF_VAR_confluent_cloud_api_key"):
        os.environ["CONFLUENT_CLOUD_API_KEY"] = creds["TF_VAR_confluent_cloud_api_key"]
    if creds.get("TF_VAR_confluent_cloud_api_secret"):
        os.environ["CONFLUENT_CLOUD_API_SECRET"] = creds["TF_VAR_confluent_cloud_api_secret"]

    try:
        tf = flatten_outputs(run_terraform_output(tf_state_path(root, track)))
    except Exception as e:
        print(f"Error reading terraform state: {e}")
        sys.exit(1)

    env_id = tf["environment_id"]
    cluster_id = tf["cluster_id"]
    region = creds.get("TF_VAR_region") or DEFAULT_REGION

    clear_source = not args.keep_source
    # --with-labs stops and restarts the feed even with --keep-source: LAB 3 has
    # to be RUNNING before any new standings row is produced (see the module
    # docstring), so submitting it against a live feed is the one thing this
    # command must never do.
    stop_feed = clear_source or args.with_labs

    # Whether this track *should* have an ECS service, decided by the track rather
    # than by has_ecs(tf). They differ in exactly the case that matters: standalone
    # state with no ecs_* outputs is stale, not Confluent-only, and treating it as
    # "nothing to stop" would clear the source topics under a live feed and report
    # success.
    ecs_track = track is STANDALONE

    problems: list[str] = []

    # Refuse before anything destructive happens. A local f1-race would refill the
    # source topics the moment delete_records returns, and there is no service to
    # scale down on this track — the user has to stop their own process.
    if stop_feed and not ecs_track:
        local = local_race_processes()
        if local and not args.force:
            print("\nA local race simulator is still producing:")
            for line in local:
                print(f"  {line}")
            print("\nStop it (Ctrl-C in that terminal) and re-run, or pass --force to reset")
            print("anyway. Clearing the source topics while it runs just moves the offsets —")
            print("the records land again immediately.")
            sys.exit(1)
        if local:
            print("\n  WARNING: --force with a local f1-race running; the source topics will refill.")

    counter = 0

    def step(msg: str) -> None:
        nonlocal counter
        counter += 1
        if counter > 1:
            print()
        print(f"{counter}. {msg}")

    if stop_feed and ecs_track:
        step("Stopping the race feed...")
        if not has_ecs(tf):
            print("  No ecs_cluster_name/ecs_service_name in the Terraform outputs — this")
            print("  state predates the simulator service. Re-run `uv run deploy` to refresh")
            print("  the outputs; until then the feed can't be stopped or confirmed stopped.")
            problems.append("could not locate the race feed (stale Terraform outputs)")
        elif not scale_simulator(tf, region, 0):
            problems.append("could not stop the race feed")
        elif not wait_for_drain(tf, region):
            problems.append("the race feed did not stop; source data may be re-produced")

    step("Stopping Flink statements...")
    problems += delete_flink_statements(tf)

    step("Dropping lab Flink objects (car_state, pit_decisions, pit_strategy_agent)...")
    problems += drop_flink_objects(tf, LAB_DROPS)

    try:
        admin = kafka_admin(tf)
    except Exception as e:
        print(f"\nCould not reach the Kafka admin API: {e}")
        problems.append(f"no Kafka admin access, so topics were not cleaned ({e})")
        admin = None

    present = existing_topics(admin) if admin is not None else None

    step("Dropping lab topics and SR subjects...")
    for topic in LAB_TOPICS:
        exists = None if present is None else (topic in present)
        problems += delete_topic_and_subjects(topic, env_id, cluster_id, exists)

    if not clear_source:
        step("Keeping source topic data (--keep-source).")
    elif admin is None:
        step("Skipping source topics — no Kafka admin access (see above).")
    else:
        step("Clearing race data from source topics...")
        problems += truncate_topics(admin, SOURCE_TOPICS, present)

    if args.with_labs:
        # Before the feed restarts, not after. `car_telemetry` sets
        # scan.startup.mode=earliest-offset at the table level (see
        # terraform/modules/topics/main.tf), so it would replay either way — but
        # `race_standings` does not, and it is the versioned side of LAB 3's
        # temporal join. Standings rows produced before this statement is RUNNING
        # are never seen, leaving those laps with no version to join against, so
        # the join drops them and car_state silently loses its first laps.
        step("Rebuilding lab objects from docs/demo-reference/...")
        if problems:
            print("  Skipped — the cleanup above did not finish cleanly, so a rebuild would")
            print("  fail confusingly (most often 'table already exists'). Fix the errors")
            print("  listed at the end and re-run.")
        elif not create_lab_objects(tf, root):
            problems.append("the lab objects were not rebuilt")

    labs_ok = args.with_labs and not problems

    if labs_ok and ecs_track:
        step("Starting the race feed...")
        if not scale_simulator(tf, region, 1):
            problems.append("could not start the race feed")
        elif not wait_for_running(tf, region):
            problems.append("the race feed has no running task yet")

    if problems:
        print("\n=== Reset INCOMPLETE ===")
        for problem in problems:
            print(f"  - {problem}")
        print("\nNothing downstream is safe to assume: re-run `uv run reset` once the above")
        print("is fixed. Leaving the environment half-reset silently breaks the next lab run.")
        sys.exit(1)

    print("\n=== Reset complete ===")

    if labs_ok and ecs_track:
        print("Environment is ready — race running from lap 0, all lab objects rebuilt.")
        print("  `car_state` stays empty for ~4 min while anomaly detection fills")
        print("  its first 12 windows of context. The anomaly fires around lap 24.")
        print("  Watch it: `uv run f1-pitwall`")
    elif labs_ok:
        print("Lab objects rebuilt. Start the race feed to fill them:")
        print(f"  {f1_race_command(root, creds)}")
        print("  `car_state` then stays empty for ~12 windows while anomaly detection")
        print("  warms up. The anomaly fires around lap 24. Watch it: `uv run f1-pitwall`")
    elif args.keep_source and not stop_feed:
        # The feed was never stopped, so it is still producing wherever it runs.
        print("Next steps:")
        print("  Re-run the stream-processing labs (LAB 3 → LAB 4).")
        print("  The accumulated race data is still in car_telemetry and race_standings,")
        print("  so LAB 3 replays the finished races along with the new one.")
    else:
        start = "uv run race start" if ecs_track else f1_race_command(root, creds)
        print("Next steps:")
        print("  1. Re-run the stream-processing labs — LAB 3, then LAB 4 (`uv run f1-sql`).")
        print(f"  2. `{start}`  — starts a fresh race from lap 0.")
        print("  In that order. `race_standings` is read from `latest`, so a LAB 3 statement")
        print("  submitted after the race is already producing never sees the versions its")
        print("  first laps need, and car_state silently drops them.")
        print("  (Or do all of it in one command next time: `uv run reset --with-labs`.)")


if __name__ == "__main__":
    main()
