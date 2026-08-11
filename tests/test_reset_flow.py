"""`uv run reset` — what it does, in what order, and when it must fail.

Three regressions are pinned here:

- reset promised it cleared the source topics but truncated them while the ECS
  feed was still producing, so the records came straight back;
- `drop_flink_objects` submitted DROPs and returned, so a `--with-labs` rebuild
  raced its own cleanup and failed with "table already exists";
- several failures printed a warning and reset still exited 0.

Every test drives `main()` against a temp project root with the cloud calls
mocked; the ordered `calls` log is what proves stop-then-clear rather than
clear-then-hope.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import reset

STANDALONE_TF = {
    "environment_id": "env-1",
    "environment_name": "RIVER-RACING-demo-ENV",
    "cluster_id": "lkc-1",
    "cluster_name": "RIVER-RACING-demo-CLUSTER",
    "cluster_bootstrap": "SASL_SSL://pkc-x.us-east-1.aws.confluent.cloud:9092",
    "kafka_api_key": "K",
    "kafka_api_secret": "S",
    "organization_id": "org-1",
    "compute_pool_id": "lfcp-1",
    "flink_rest_endpoint": "https://flink.example",
    "flink_api_key": "FK",
    "flink_api_secret": "FS",
    "ecs_cluster_name": "river-racing-demo-abc-simulator",
    "ecs_service_name": "river-racing-demo-abc-simulator",
}

# Self-service publishes the Kafka keys only inside attendee_credentials, and has
# no ECS outputs at all.
SELFSERVICE_TF = {
    k: v for k, v in STANDALONE_TF.items() if not k.startswith("ecs_") and not k.startswith("kafka_")
}
SELFSERVICE_TF["attendee_credentials"] = {"kafka_api_key": "K", "kafka_api_secret": "S"}


class ResetFlowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.calls: list[str] = []

        patches = {
            "get_project_root": {"return_value": self.root},
            "ensure_confluent_login": {"return_value": True},
            "kafka_admin": {"return_value": object()},
            "existing_topics": {"return_value": {"car_telemetry", "race_standings"}},
            "local_race_processes": {"return_value": []},
        }
        for name, kwargs in patches.items():
            p = patch.object(reset, name, **kwargs)
            setattr(self, name, p.start())
            self.addCleanup(p.stop)

        # Recorded, order-sensitive steps. Defaults are the all-clear.
        self.stub("scale_simulator", lambda tf, region, count: True, label=lambda a: f"scale={a[2]}")
        self.stub("wait_for_drain", lambda *a, **k: True)
        self.stub("wait_for_running", lambda *a, **k: True)
        self.stub("delete_flink_statements", lambda *a, **k: [])
        self.stub("drop_flink_objects", lambda *a, **k: [])
        self.stub("delete_topic_and_subjects", lambda *a, **k: [], label=lambda a: f"delete_topic={a[0]}")
        self.stub("truncate_topics", lambda *a, **k: [])
        self.stub("create_lab_objects", lambda *a, **k: True)

    def stub(self, name, impl, label=None):
        def recorder(*args, **kwargs):
            self.calls.append(label(args) if label else name)
            return impl(*args, **kwargs)

        p = patch.object(reset, name, side_effect=recorder)
        setattr(self, name, p.start())
        self.addCleanup(p.stop)

    def make_state(self, tier: str) -> None:
        state = self.root / "terraform" / tier / "terraform.tfstate"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("{}")

    def run_reset(self, argv: list[str], outputs: dict) -> int:
        with (
            patch.object(reset, "run_terraform_output", return_value=outputs),
            patch("sys.argv", ["reset", *argv]),
        ):
            try:
                reset.main()
            except SystemExit as exc:
                return exc.code or 0
        return 0

    # --- item 5: the stop is unconditional ---

    def test_plain_reset_stops_the_feed_and_then_clears_the_source_topics(self):
        self.make_state("aws")

        rc = self.run_reset([], STANDALONE_TF)

        self.assertEqual(rc, 0)
        self.assertLess(self.calls.index("wait_for_drain"), self.calls.index("truncate_topics"))
        self.assertEqual(self.calls[0], "scale=0")
        # The DROPs are waited on before their backing topics/subjects are deleted.
        self.assertLess(self.calls.index("drop_flink_objects"), self.calls.index("delete_topic=car_state"))
        self.assertLess(self.calls.index("delete_flink_statements"), self.calls.index("drop_flink_objects"))
        # Deliberately left stopped: LAB 3 has to be RUNNING before new standings.
        self.assertNotIn("scale=1", self.calls)

    def test_plain_reset_prints_race_start_rather_than_the_workshop_fan_out(self):
        self.make_state("aws")
        with patch("builtins.print") as printed:
            self.run_reset([], STANDALONE_TF)
        output = "\n".join(str(c.args[0]) for c in printed.call_args_list if c.args)
        self.assertIn("uv run race start", output)

    def test_keep_source_skips_both_the_stop_and_the_truncation(self):
        self.make_state("aws")

        rc = self.run_reset(["--keep-source"], STANDALONE_TF)

        self.assertEqual(rc, 0)
        self.assertNotIn("scale=0", self.calls)
        self.assertNotIn("wait_for_drain", self.calls)
        self.assertNotIn("truncate_topics", self.calls)

    def test_with_labs_stops_and_restarts_even_with_keep_source(self):
        # --keep-source must not leave the feed producing while LAB 3 is submitted.
        self.make_state("aws")

        rc = self.run_reset(["--with-labs", "--keep-source"], STANDALONE_TF)

        self.assertEqual(rc, 0)
        self.assertNotIn("truncate_topics", self.calls)
        self.assertLess(self.calls.index("create_lab_objects"), self.calls.index("scale=1"))
        self.assertLess(self.calls.index("scale=0"), self.calls.index("create_lab_objects"))

    def test_with_labs_rebuilds_before_restarting(self):
        self.make_state("aws")

        rc = self.run_reset(["--with-labs"], STANDALONE_TF)

        self.assertEqual(rc, 0)
        self.assertLess(self.calls.index("truncate_topics"), self.calls.index("create_lab_objects"))
        self.assertLess(self.calls.index("create_lab_objects"), self.calls.index("scale=1"))

    def test_the_account_wide_simulator_scan_is_gone(self):
        self.assertFalse(hasattr(reset, "running_simulator_count"))

    # --- item 6: fail honestly ---

    def test_failed_drop_exits_nonzero(self):
        self.make_state("aws")
        self.drop_flink_objects.side_effect = lambda *a, **k: ["DROP TABLE `car_state` did not complete (FAILED)"]

        self.assertEqual(self.run_reset([], STANDALONE_TF), 1)

    def test_failed_drop_blocks_the_lab_rebuild(self):
        # Rebuilding over a table that was never dropped fails with a message
        # ("table already exists") that hides the real cause.
        self.make_state("aws")
        self.drop_flink_objects.side_effect = lambda *a, **k: ["DROP TABLE `car_state` did not complete (FAILED)"]

        self.assertEqual(self.run_reset(["--with-labs"], STANDALONE_TF), 1)
        self.assertNotIn("create_lab_objects", self.calls)
        self.assertNotIn("scale=1", self.calls)

    def test_drain_timeout_exits_nonzero(self):
        self.make_state("aws")
        self.wait_for_drain.side_effect = lambda *a, **k: False

        self.assertEqual(self.run_reset([], STANDALONE_TF), 1)

    def test_topic_and_truncate_failures_exit_nonzero(self):
        self.make_state("aws")
        self.truncate_topics.side_effect = lambda *a, **k: ["could not clear 1 partition(s) of car_telemetry"]

        self.assertEqual(self.run_reset([], STANDALONE_TF), 1)

    def test_lost_kafka_admin_access_is_a_failure_not_a_silent_skip(self):
        self.make_state("aws")
        self.kafka_admin.side_effect = RuntimeError("SASL_AUTHENTICATION_FAILED")

        self.assertEqual(self.run_reset([], STANDALONE_TF), 1)
        self.assertNotIn("truncate_topics", self.calls)

    def test_missing_ecs_outputs_do_not_silently_skip_the_stop(self):
        # The standalone track always has an ECS service; outputs without one mean
        # stale state, so the producer can't be shown to have stopped.
        self.make_state("aws")
        stale = {k: v for k, v in STANDALONE_TF.items() if not k.startswith("ecs_")}

        rc = self.run_reset([], stale)

        self.assertEqual(rc, 1)
        self.assertNotIn("scale=0", self.calls)

    # --- item 7: self-service ---

    def test_selfservice_reset_skips_every_ecs_step(self):
        self.make_state("self-service")

        rc = self.run_reset([], SELFSERVICE_TF)

        self.assertEqual(rc, 0)
        self.assertNotIn("scale=0", self.calls)
        self.assertNotIn("scale=1", self.calls)
        self.assertNotIn("wait_for_drain", self.calls)
        self.assertIn("truncate_topics", self.calls)

    def test_selfservice_reset_refuses_while_a_local_race_runs(self):
        self.make_state("self-service")
        self.local_race_processes.return_value = ["4242  uv run f1-race"]

        rc = self.run_reset([], SELFSERVICE_TF)

        self.assertEqual(rc, 1)
        self.assertNotIn("truncate_topics", self.calls)
        self.assertNotIn("drop_flink_objects", self.calls)

    def test_force_overrides_the_local_race_refusal(self):
        self.make_state("self-service")
        self.local_race_processes.return_value = ["4242  uv run f1-race"]

        self.assertEqual(self.run_reset(["--force"], SELFSERVICE_TF), 0)
        self.assertIn("truncate_topics", self.calls)

    def test_keep_source_on_selfservice_does_not_care_about_a_local_race(self):
        self.make_state("self-service")
        self.local_race_processes.return_value = ["4242  uv run f1-race"]

        self.assertEqual(self.run_reset(["--keep-source"], SELFSERVICE_TF), 0)

    def test_selfservice_with_labs_prints_the_f1_race_command(self):
        self.make_state("self-service")
        with patch("builtins.print") as printed:
            rc = self.run_reset(["--with-labs"], SELFSERVICE_TF)
        output = "\n".join(str(c.args[0]) for c in printed.call_args_list if c.args)

        self.assertEqual(rc, 0)
        self.assertIn("uv run f1-race", output)
        self.assertNotIn("uv run race start", output)

    def test_selfservice_kafka_keys_come_from_attendee_credentials(self):
        merged = reset.flatten_outputs(SELFSERVICE_TF)
        self.assertEqual(merged["kafka_api_key"], "K")
        self.assertEqual(merged["kafka_api_secret"], "S")

    def test_flat_outputs_win_over_the_nested_map(self):
        merged = reset.flatten_outputs({"kafka_api_key": "flat", "attendee_credentials": {"kafka_api_key": "nested"}})
        self.assertEqual(merged["kafka_api_key"], "flat")

    # --- track selection ---

    def test_both_tracks_deployed_requires_an_explicit_track(self):
        self.make_state("aws")
        self.make_state("self-service")

        self.assertEqual(self.run_reset([], STANDALONE_TF), 1)
        self.assertEqual(self.calls, [])

    def test_explicit_track_disambiguates(self):
        self.make_state("aws")
        self.make_state("self-service")

        self.assertEqual(self.run_reset(["--track", "selfservice"], SELFSERVICE_TF), 0)
        self.assertNotIn("scale=0", self.calls)

    def test_explicit_track_without_state_is_an_error(self):
        self.make_state("aws")

        self.assertEqual(self.run_reset(["--track", "selfservice"], SELFSERVICE_TF), 1)
        self.assertEqual(self.calls, [])

    def test_no_state_at_all_is_an_error(self):
        self.assertEqual(self.run_reset([], STANDALONE_TF), 1)


class LocalRaceDetectionTests(unittest.TestCase):
    """Which process command lines count as "a local race is producing"."""

    def test_matches_the_console_script_and_the_path_uv_execs(self):
        self.assertTrue(reset.is_local_race("uv run f1-race"))
        self.assertTrue(reset.is_local_race("/repo/.venv/bin/python /repo/.venv/bin/f1-race"))
        self.assertTrue(reset.is_local_race("uv run f1-race --seconds-per-lap 20"))
        self.assertTrue(reset.is_local_race("python -m scripts.selfservice.race"))

    def test_merely_mentioning_it_is_not_a_running_race(self):
        # Substring matching flagged all of these, and reset then refused to run.
        self.assertFalse(reset.is_local_race("grep -rn f1-race-notes scripts/"))
        self.assertFalse(reset.is_local_race('/bin/zsh -c eval \'exec -a "uv run f1-race" sleep 8\''))
        self.assertFalse(reset.is_local_race("vim scripts/selfservice/race.py"))
        self.assertFalse(reset.is_local_race("uv run reset --track selfservice"))
        self.assertFalse(reset.is_local_race("tail -f f1-race.log"))


class RaceCommandHintTests(unittest.TestCase):
    """The printed `uv run f1-race` hint must never be the thing that fails a reset.

    It is computed after every destructive step has already succeeded, so a raise
    here would report failure for work that actually completed.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.cards = self.root / "runs" / "selfservice" / "credentials"
        self.cards.mkdir(parents=True)

    def test_sole_card_needs_no_creds_flag(self):
        (self.cards / "f1ss001.env").write_text("F1_ENV_ID=env-1\n")

        self.assertEqual(reset.f1_race_command(self.root, {}), "uv run f1-race")

    def test_ambiguous_cards_spell_out_creds(self):
        (self.cards / "f1ss001.env").write_text("F1_ENV_ID=env-1\n")
        other = self.root / "runs" / "standalone" / "credentials"
        other.mkdir(parents=True)
        (other / "f1wp001.env").write_text("F1_ENV_ID=env-2\n")

        self.assertEqual(
            reset.f1_race_command(self.root, {}),
            "uv run f1-race --creds runs/selfservice/credentials/f1ss001.env",
        )

    def test_pointer_already_aimed_at_the_card_needs_no_flag(self):
        (self.cards / "f1ss001.env").write_text("F1_ENV_ID=env-1\n")
        other = self.root / "runs" / "standalone" / "credentials"
        other.mkdir(parents=True)
        (other / "f1wp001.env").write_text("F1_ENV_ID=env-2\n")

        creds = {"F1_CARD": "runs/selfservice/credentials/f1ss001.env"}
        self.assertEqual(reset.f1_race_command(self.root, creds), "uv run f1-race")

    def test_malformed_pointers_fall_back_instead_of_raising(self):
        (self.cards / "f1ss001.env").write_text("F1_ENV_ID=env-1\n")
        for pointer in ("", "~/nope.env", "\0bad", "/absolute/elsewhere.env", "a" * 5000):
            with self.subTest(pointer=pointer):
                self.assertEqual(
                    reset.f1_race_command(self.root, {"F1_CARD": pointer}),
                    "uv run f1-race",
                )

    def test_missing_run_directory_falls_back(self):
        self.assertEqual(reset.f1_race_command(Path("/nonexistent-root"), {}), "uv run f1-race")


class DropWaitTests(unittest.TestCase):
    """DROP statements are waited on, and a DROP that never completes fails the run.

    Submission used to be fire-and-forget, so a `--with-labs` rebuild could submit
    `CREATE TABLE car_state` while the DROP was still PENDING — reported as "table
    already exists", which says nothing about the real cause.
    """

    def setUp(self):
        self.urlopen = self.start(patch("urllib.request.urlopen"))
        self.start(patch("time.sleep"))  # the poll interval, not the behavior

    def start(self, patcher):
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def phases(self, *sequence):
        """Make _get_json walk `sequence` per statement, repeating the last entry."""
        calls = {"n": 0}

        def fake(url, headers):
            index = min(calls["n"] % len(sequence), len(sequence) - 1)
            calls["n"] += 1
            return {"status": sequence[index]}

        self.get_json = self.start(patch.object(reset, "_get_json", side_effect=fake))
        return self.get_json

    def methods(self):
        return [call.args[0].get_method() for call in self.urlopen.call_args_list]

    def test_pending_then_completed_is_success_and_the_statement_is_tidied_up(self):
        self.phases({"phase": "PENDING"}, {"phase": "COMPLETED"})

        problems = reset.drop_flink_objects(STANDALONE_TF, reset.LAB_DROPS)

        self.assertEqual(problems, [])
        # Polled past PENDING for each of the three drops.
        self.assertEqual(self.get_json.call_count, 2 * len(reset.LAB_DROPS))
        self.assertEqual(self.methods().count("POST"), len(reset.LAB_DROPS))
        self.assertEqual(self.methods().count("DELETE"), len(reset.LAB_DROPS))

    def test_failed_drop_is_reported_with_its_detail(self):
        self.phases({"phase": "FAILED", "detail": "cannot drop: in use"})

        problems = reset.drop_flink_objects(STANDALONE_TF, reset.LAB_DROPS[:1])

        self.assertEqual(len(problems), 1)
        self.assertIn("FAILED", problems[0])
        self.assertIn("cannot drop: in use", problems[0])
        # A failed DROP is not deleted — the statement is the evidence.
        self.assertNotIn("DELETE", self.methods())

    def test_timeout_names_the_last_observed_phase(self):
        self.phases({"phase": "PENDING"})

        problems = reset.drop_flink_objects(STANDALONE_TF, reset.LAB_DROPS[:1], timeout=1)

        self.assertEqual(len(problems), 1)
        self.assertIn("TIMED OUT", problems[0])
        self.assertIn("PENDING", problems[0])

    def test_running_is_not_treated_as_success_for_ddl(self):
        # create_lab_objects accepts RUNNING because a streaming INSERT never
        # completes. DDL is the opposite: RUNNING means it hasn't landed yet.
        self.phases({"phase": "RUNNING"})

        problems = reset.drop_flink_objects(STANDALONE_TF, reset.LAB_DROPS[:1], timeout=1)

        self.assertEqual(len(problems), 1)
        self.assertIn("RUNNING", problems[0])


class TopicClassificationTests(unittest.TestCase):
    """A lab topic that is already gone is the normal case, not a failure.

    `DROP TABLE` deletes the backing topic, so by the time the CLI delete runs the
    topic is usually absent — and an attendee who never ran LAB 3 never had one.
    Failing on that would make every plain reset exit nonzero.
    """

    def test_absent_lab_topic_skips_the_cli_delete_entirely(self):
        with patch.object(reset, "run_cli", return_value=(0, "", "")) as cli:
            problems = reset.delete_topic_and_subjects("car_state", "env-1", "lkc-1", exists=False)

        self.assertEqual(problems, [])
        commands = [call.args[0] for call in cli.call_args_list]
        self.assertFalse(any("topic" in cmd for cmd in commands))
        self.assertTrue(any("schema-registry" in cmd for cmd in commands))

    def test_missing_subject_error_is_benign(self):
        with patch.object(reset, "run_cli", return_value=(1, "", "Error: Subject 'car_state-key' not found.")):
            problems = reset.delete_topic_and_subjects("car_state", "env-1", "lkc-1", exists=False)
        self.assertEqual(problems, [])

    def test_permission_error_is_reported(self):
        denied = (1, "", "Error: 403 Forbidden: user has no authorization")
        with patch.object(reset, "run_cli", return_value=denied):
            problems = reset.delete_topic_and_subjects("car_state", "env-1", "lkc-1", exists=True)

        self.assertTrue(problems)
        self.assertTrue(any("could not delete topic car_state" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
