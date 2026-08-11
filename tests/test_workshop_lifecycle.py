from __future__ import annotations

import argparse
import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.workshop import lifecycle


def account(number: int) -> lifecycle.Account:
    return lifecycle.Account(
        number=number,
        prefix=f"f1wp{number:03d}",
        credential_card=Path(f"/cards/{number}.env"),
        ecs_cluster=f"cluster-{number}",
        ecs_service=f"service-{number}",
        region="us-east-1",
        prepared=True,
    )


class SelectorTests(unittest.TestCase):
    def test_ranges_and_commas_are_deduplicated(self):
        self.assertEqual(lifecycle.parse_account_selector("48-50,49", range(1, 51)), [48, 49, 50])

    def test_explicit_subset_is_capped_at_three(self):
        with self.assertRaisesRegex(SystemExit, "capped at 3"):
            lifecycle.parse_account_selector("1-4", range(1, 10))

    def test_omitted_selector_means_complete_cohort(self):
        self.assertEqual(lifecycle.parse_account_selector("", [3, 1, 2]), [1, 2, 3])

    def test_unknown_account_fails(self):
        with self.assertRaisesRegex(SystemExit, "not present"):
            lifecycle.parse_account_selector("9", [1, 2])


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _manifest(self, run_id: str) -> Path:
        path = self.root / "runs" / run_id / "manifest.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "preparation_status": "ready",
                    "accounts": [
                        {
                            "account": 50,
                            "prefix": "f1wp050",
                            "credential_card": "runs/cards/f1wp050.env",
                            "ecs_cluster": "exact-cluster",
                            "ecs_service": "exact-service",
                            "region": "ap-southeast-2",
                        }
                    ],
                }
            )
        )
        return path

    def test_omitted_run_id_requires_exactly_one_manifest(self):
        self._manifest("one")
        self._manifest("two")
        with self.assertRaisesRegex(SystemExit, "exactly one"):
            lifecycle.resolve_manifest(self.root)

    def test_omitted_run_id_ignores_cleaned_runs(self):
        self._manifest("active")
        self._manifest("old")
        cleaned = self.root / "wsa-output/old-cleaned/clean-report.json"
        cleaned.parent.mkdir(parents=True)
        cleaned.write_text("{}")
        self.assertEqual(lifecycle.resolve_manifest(self.root).run_id, "active")
        with self.assertRaisesRegex(SystemExit, "already been cleaned"):
            lifecycle.resolve_manifest(self.root, "old")

    def test_manifest_contains_no_credential_values(self):
        path = self._manifest("one")
        loaded = lifecycle.resolve_manifest(self.root, "one")
        self.assertEqual(loaded.accounts[0].ecs_service, "exact-service")
        text = path.read_text()
        self.assertNotIn("API_SECRET", text)
        self.assertNotIn("password", text.lower())

    def test_write_manifest_uses_actual_terraform_outputs(self):
        run_path = self.root / "wsa-output" / "abc12"
        state_path = run_path / "terraform/aws/terraform.tfstate.d/account-050/terraform.tfstate"
        state_path.parent.mkdir(parents=True)
        (run_path / "build-report.json").write_text(json.dumps({"accounts": [50]}))
        state_path.write_text(
            json.dumps(
                {
                    "outputs": {
                        "prefix": {"value": "f1wp050"},
                        "ecs_cluster_name": {"value": "generated-cluster-a8f"},
                        "ecs_service_name": {"value": "generated-service-a8f"},
                    }
                }
            )
        )
        run = argparse.Namespace(path=run_path, run_id="abc12")
        path = lifecycle.write_manifest(self.root, run, "cards", "us-west-2")
        raw = json.loads(path.read_text())
        self.assertEqual(raw["accounts"][0]["ecs_cluster"], "generated-cluster-a8f")
        self.assertEqual(raw["accounts"][0]["credential_card"], "runs/cards/credentials/f1wp050.env")


class ExactResolutionTests(unittest.TestCase):
    @patch.object(lifecycle, "_client")
    def test_describe_uses_only_exact_manifest_names(self, client):
        client.return_value.describe_services.return_value = {
            "services": [{"serviceName": "service-50", "runningCount": 0}],
            "failures": [],
        }
        result = lifecycle.describe_exact(account(50))
        self.assertEqual(result["serviceName"], "service-50")
        client.return_value.describe_services.assert_called_once_with(
            cluster="cluster-50", services=["service-50"]
        )

    @patch.object(lifecycle, "_client")
    def test_zero_matches_fails(self, client):
        client.return_value.describe_services.return_value = {"services": [], "failures": []}
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            lifecycle.describe_exact(account(50))


class LifecycleBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.manifest = lifecycle.RunManifest(
            "run1", Path("/manifest.json"), tuple(account(n) for n in (48, 49, 50)), "ready"
        )
        self.selection = patch.object(
            lifecycle, "_selection", return_value=(self.manifest, list(self.manifest.accounts), False)
        )
        self.selection.start()
        self.addCleanup(self.selection.stop)

    @patch.object(lifecycle, "_stop_accounts", return_value=[])
    @patch.object(lifecycle, "_parallel")
    @patch.object(lifecycle, "_latest_telemetry", return_value={"race_id": "old"})
    @patch.object(lifecycle, "_card", return_value={})
    @patch.object(lifecycle, "describe_exact")
    def test_partial_start_rolls_back_every_new_target(
        self, describe, card_mock, latest, parallel, stop
    ):
        describe.return_value = {"runningCount": 0}
        parallel.side_effect = [
            {48: "old", 49: "old", 50: "old"},
            {48: {"race_id": "r"}, 49: RuntimeError("boom"), 50: {"race_id": "r"}},
        ]
        with self.assertRaisesRegex(SystemExit, "all newly started targets were stopped"):
            lifecycle.start_races(argparse.Namespace(run_id="run1", accounts="48-50"))
        self.assertEqual([a.number for a in stop.call_args.args[0]], [48, 49, 50])
        self.assertEqual(parallel.call_args_list[0].args[2], 3)

    @patch.object(lifecycle, "_latest_telemetry")
    @patch.object(lifecycle, "_event_age", return_value=1.0)
    def test_fresh_race_rejects_the_pre_start_race_id(self, age, latest):
        latest.side_effect = [
            {"race_id": "old", "event_time": 1},
            {"race_id": "new", "event_time": 1},
        ]
        with patch.object(lifecycle.time, "time", return_value=1_005.0):
            event = lifecycle._fresh_race(account(50), "old", 1_000.0, timeout=1)
        self.assertEqual(event["race_id"], "new")
        self.assertEqual(latest.call_count, 2)

    @patch.object(lifecycle, "_card", return_value={})
    @patch.object(lifecycle, "describe_exact", return_value={"runningCount": 0})
    def test_full_start_refuses_dirty_test_account_with_exact_reset(self, describe, card_mock):
        dirty = account(50)
        dirty = lifecycle.Account(**{**dirty.__dict__, "prepared": False})
        with patch.object(
            lifecycle,
            "_selection",
            return_value=(self.manifest, [dirty], True),
        ):
            with self.assertRaisesRegex(
                SystemExit,
                r"reset-races --run-id run1 --accounts 50",
            ):
                lifecycle.start_races(argparse.Namespace(run_id="run1", accounts=""))

    @patch.object(lifecycle, "_set_preparation")
    @patch.object(lifecycle, "_parallel", return_value={50: {"race_id": "new"}})
    @patch.object(lifecycle, "_latest_telemetry", return_value={"race_id": "old", "event_time": 1})
    @patch.object(lifecycle, "_card", return_value={})
    @patch.object(lifecycle, "describe_exact", return_value={"runningCount": 0})
    def test_explicit_dirty_subset_can_resume_after_operational_stop(
        self, describe, card_mock, latest, parallel, set_preparation
    ):
        dirty = account(50)
        dirty = lifecycle.Account(**{**dirty.__dict__, "prepared": False})
        with patch.object(
            lifecycle,
            "_selection",
            return_value=(self.manifest, [dirty], False),
        ):
            lifecycle.start_races(argparse.Namespace(run_id="run1", accounts="50"))
        set_preparation.assert_called_once()

    @patch.object(lifecycle, "_set_preparation")
    @patch.object(
        lifecycle,
        "_parallel",
        return_value={48: {"race_id": "new"}, 49: {"race_id": "new"}, 50: {"race_id": "new"}},
    )
    @patch.object(lifecycle, "_latest_telemetry", return_value={"race_id": "old", "event_time": 1})
    @patch.object(lifecycle, "_card", return_value={})
    @patch.object(lifecycle, "describe_exact", return_value={"runningCount": 0})
    def test_full_cohort_start_keeps_prepared_flags_for_pause_resume(
        self, describe, card_mock, latest, parallel, set_preparation
    ):
        with patch.object(
            lifecycle,
            "_selection",
            return_value=(self.manifest, list(self.manifest.accounts), True),
        ):
            lifecycle.start_races(argparse.Namespace(run_id="run1", accounts=""))
        set_preparation.assert_not_called()

    @patch.object(lifecycle, "_set_preparation")
    @patch.object(lifecycle, "_reset_account")
    @patch.object(lifecycle, "_stop_accounts", return_value=[])
    def test_reset_is_idempotent_and_stops_before_and_after(self, stop, reset_one, set_prepared):
        lifecycle.reset_races(argparse.Namespace(run_id="run1", accounts="48-50"))
        self.assertEqual(stop.call_count, 2)
        self.assertEqual(reset_one.call_count, 3)
        set_prepared.assert_called_once()

    @patch.object(lifecycle, "_set_preparation")
    @patch.object(lifecycle, "_reset_account")
    @patch.object(lifecycle, "_stop_accounts", return_value=[])
    def test_reset_parallelism_is_bounded_at_eight(self, stop, reset_one, set_prepared):
        many = [account(n) for n in range(1, 21)]
        with patch.object(lifecycle, "_selection", return_value=(self.manifest, many, True)):
            real_executor = lifecycle.ThreadPoolExecutor
            seen = []

            class RecordingExecutor(real_executor):
                def __init__(self, max_workers, *args, **kwargs):
                    seen.append(max_workers)
                    super().__init__(max_workers, *args, **kwargs)

            with patch.object(lifecycle, "ThreadPoolExecutor", RecordingExecutor):
                lifecycle.reset_races(argparse.Namespace(run_id="run1", accounts=""))
        self.assertIn(8, seen)

class PreparationStateTests(unittest.TestCase):
    def test_subset_reset_does_not_mark_whole_run_ready(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(
                json.dumps(
                    {
                        "accounts": [
                            {"account": 1, "prepared": False},
                            {"account": 2, "prepared": False},
                        ]
                    }
                )
            )
            manifest = lifecycle.RunManifest("run1", path, (), "not_prepared")
            lifecycle._set_preparation(manifest, {1}, True, "ready")
            raw = json.loads(path.read_text())
            self.assertEqual(raw["preparation_status"], "partially_ready")
            self.assertEqual([a["prepared"] for a in raw["accounts"]], [True, False])


class SocialFeedPreparationTests(unittest.TestCase):
    def test_refuses_any_account_except_50_before_manifest_resolution(self):
        with patch.object(lifecycle, "resolve_manifest") as resolve:
            with self.assertRaisesRegex(SystemExit, "account 50"):
                lifecycle.prepare_social_feed(argparse.Namespace(run_id="run1", account=49))
        resolve.assert_not_called()

    def test_requires_exact_account_50_prefix_before_mutation(self):
        bad = lifecycle.Account(**{**account(50).__dict__, "prefix": "f1wp049"})
        manifest = lifecycle.RunManifest("run1", Path("/manifest.json"), (bad,), "ready")
        with (
            patch.object(lifecycle, "get_project_root", return_value=Path("/repo")),
            patch.object(lifecycle, "resolve_manifest", return_value=manifest),
            patch.object(lifecycle, "_stop_accounts") as stop,
        ):
            with self.assertRaisesRegex(SystemExit, "unexpected prefix"):
                lifecycle.prepare_social_feed(argparse.Namespace(run_id="run1", account=50))
        stop.assert_not_called()

    def test_prepares_only_exact_manifest_account_and_leaves_it_stopped(self):
        target = account(50)
        manifest = lifecycle.RunManifest("run1", Path("/manifest.json"), (account(49), target), "ready")
        card = {
            "F1_FLINK_REST_ENDPOINT": "https://flink.example",
            "F1_FLINK_API_KEY": "sentinel-key",
            "F1_FLINK_API_SECRET": "sentinel-secret",
            "F1_ORGANIZATION_ID": "org",
            "F1_ENVIRONMENT_ID": "env",
            "F1_COMPUTE_POOL_ID": "pool",
            "F1_CATALOG": "catalog",
            "F1_DATABASE": "database",
            "F1_KAFKA_BOOTSTRAP": "kafka",
            "F1_KAFKA_API_KEY": "k-key",
            "F1_KAFKA_API_SECRET": "k-secret",
            "F1_SCHEMA_REGISTRY_URL": "https://sr.example",
            "F1_SR_API_KEY": "sr-key",
            "F1_SR_API_SECRET": "sr-secret",
            "F1_CLUSTER_ID": "cluster",
        }
        order = []
        with (
            patch.object(lifecycle, "get_project_root", return_value=Path("/repo")),
            patch.object(lifecycle, "resolve_manifest", return_value=manifest),
            patch.object(lifecycle, "_card", return_value=card),
            patch.object(lifecycle, "describe_exact", return_value={"runningCount": 0}),
            patch.object(
                lifecycle,
                "_stop_accounts",
                side_effect=lambda accounts, announce=False: order.append(
                    ("stop", [a.number for a in accounts])
                )
                or [],
            ),
            patch.object(
                lifecycle,
                "delete_flink_statements",
                side_effect=lambda _tf: order.append(("delete", 50)) or [],
            ),
            patch.object(
                lifecycle,
                "drop_flink_objects",
                side_effect=lambda _tf, _drops: order.append(("drop-agent", 50)) or [],
            ),
            patch(
                "scripts.common.simulator_control.create_lab_objects",
                side_effect=lambda _tf, _root: order.append(("build", 50)) or True,
            ),
            patch.object(lifecycle, "_set_preparation") as set_preparation,
            contextlib.redirect_stdout(io.StringIO()) as stdout,
        ):
            lifecycle.prepare_social_feed(argparse.Namespace(run_id="run1", account=50))

        self.assertEqual(
            order,
            [("stop", [50]), ("delete", 50), ("drop-agent", 50), ("build", 50), ("stop", [50])],
        )
        set_preparation.assert_called_once()
        self.assertNotIn("sentinel-key", stdout.getvalue())
        self.assertNotIn("sentinel-secret", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
