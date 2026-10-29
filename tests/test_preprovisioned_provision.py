"""`uv run deploy --track pre-provisioned` — the self-service workshop in a customer's org.

The platform team builds everything inside the developer's environment; the
deploy runs no Terraform. What is pinned here:

- the track is first-class wherever tracks are enumerated (picker, `--track`,
  per-track metadata, `retire_track`, reset's track selection);
- the deploy needs a Confluent CLI session, mints a Global key with the CLI and a
  Kafka and an SR key over REST, and invokes no `terraform` at all;
- the environment is resolved from the key, shown, and confirmed, and a re-run
  never silently retargets a deployment whose keys live elsewhere;
- the card carries the Global key as its Flink and RTCE key;
- every key ID reaches deployment.env the moment the key exists, a run that stops
  before the card deletes the keys it minted, and a re-run reuses a card whose
  keys all exist or replaces the keys and deletes the old ones;
- no tool mints keys for a "service account" in this track, whose principal is
  the developer's user ID.

Every test runs against a temp project root with HTTP and every subprocess
mocked (`FakeConfluent`); nothing here can reach Confluent Cloud.
"""

import io
import json
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import requests
from dotenv import dotenv_values

import deploy
from scripts import reset, setup_rtce
from scripts.common import deploy_common as common
from scripts.common import deployment_meta as meta
from scripts.preprovisioned import keys, lookup
from scripts.preprovisioned import provision as pp
from scripts.workshop import creds as creds_mod

API = "https://api.confluent.cloud"
USER = "u-johndoe"
ORG = "9759ca38-0000-4000-8000-000000000001"
EMAIL = "John.Doe@acme.com"
MINE = lookup.Environment("env-mine", "John-Doe-c21e_env")
OTHER = lookup.Environment("env-other", "Jane-Roe-1a2b_env")
DECOY = lookup.Environment("env-decoy", "shared_env")

GLOBAL_CREATE = ["confluent", "api-key", "create", "--resource", "global"]


class Resp:
    def __init__(self, status_code: int = 200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeConfluent:
    """Confluent Cloud's REST API and CLI, as far as this deploy uses them.

    `live` is every key that exists (ID → secret); a request authenticates when
    its key is live. The CLI mints Global keys GLOBAL1, GLOBAL2, ...; REST mints
    KAFKA<n> / SR<n>.
    """

    def __init__(self, envs=(MINE,), region="us-east-1", email=EMAIL):
        self.envs = list(envs)
        self.region = region
        self.email = email
        env = self.envs[0].id if self.envs else "env-none"
        crn = f"crn://confluent.cloud/organization={ORG}/environment={env}"
        self.clusters = [
            {
                "id": "lkc-1",
                "metadata": {"resource_name": f"{crn}/cloud-cluster=lkc-1"},
                "spec": {
                    "display_name": "RIVER-RACING-DEMO-CLUSTER",
                    "cloud": "AWS",
                    "region": region,
                    "kafka_bootstrap_endpoint": f"SASL_SSL://pkc-1.{region}.aws.confluent.cloud:9092",
                    "http_endpoint": f"https://pkc-1.{region}.aws.confluent.cloud:443",
                },
            }
        ]
        self.pools = [
            {
                "id": "lfcp-1",
                "metadata": {"resource_name": f"{crn}/flink-region=aws.{region}/compute-pool=lfcp-1"},
                "spec": {"display_name": "RIVER-RACING-DEMO-pool", "cloud": "AWS", "region": region},
            }
        ]
        self.registries = [
            {
                "id": "lsrc-1",
                "metadata": {"resource_name": f"{crn}/schema-registry=lsrc-1"},
                "spec": {"http_endpoint": f"https://psrc-1.{region}.aws.confluent.cloud"},
            }
        ]
        self.live: dict[str, str] = {}
        self.calls: list[tuple] = []
        self.cli_calls: list[list[str]] = []
        self.global_cap_reached = False
        self.orgs: list[dict] | None = None
        self.kafka_probe_failures = 0
        self.global_warmup = 0  # requests a new Global key is refused for, as it propagates
        self.on_post = None
        self._serial = 0

    def _new_key(self, kind: str) -> tuple[str, str]:
        self._serial += 1
        key = f"{kind}{self._serial}"
        self.live[key] = f"{key}-secret"
        return key, self.live[key]

    def _authed(self, auth) -> bool:
        if auth and auth[0].startswith("GLOBAL") and self.global_warmup:
            self.global_warmup -= 1
            return False
        return bool(auth) and self.live.get(auth[0]) == auth[1]

    def get(self, url, auth=None, params=None, timeout=None):
        self.calls.append(("GET", url, params))
        if not self._authed(auth):
            return Resp(401)
        path = url.removeprefix(API)
        if path.startswith("/iam/v2/api-keys/"):
            key_id = path.rsplit("/", 1)[1]
            return Resp(200, {"id": key_id, "spec": {"owner": {"id": USER}}}) if key_id in self.live else Resp(404)
        if path.startswith("/org/v2/environments"):
            return Resp(200, {"data": [{"id": e.id, "display_name": e.name} for e in self.envs], "metadata": {}})
        if path == f"/iam/v2/users/{USER}":
            return Resp(200, {"id": USER, "email": self.email}) if self.email else Resp(403)
        if path == "/cmk/v2/clusters":
            return Resp(200, {"data": self.clusters})
        if path == "/fcpm/v2/compute-pools":
            return Resp(200, {"data": self.pools})
        if path == "/srcm/v3/clusters":
            return Resp(200, {"data": self.registries})
        if path == "/fcpm/v2/regions":
            region = params["region_name"]
            return Resp(
                200, {"data": [{"id": f"aws.{region}", "http_endpoint": f"https://flink.{region}.aws.confluent.cloud"}]}
            )
        if url.endswith("/kafka/v3/clusters/lkc-1/topics"):
            if self.kafka_probe_failures:
                self.kafka_probe_failures -= 1
                return Resp(401)
            return Resp(200, {"data": []})
        if url.endswith("/subjects"):
            return Resp(200, [])
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, auth=None, json=None, timeout=None):
        self.calls.append(("POST", url, json))
        if not self._authed(auth):
            return Resp(401)
        assert url == f"{API}/iam/v2/api-keys", url
        if self.on_post:
            self.on_post(json)
        resource = json["spec"]["resource"]["id"]
        key, secret = self._new_key("KAFKA" if resource.startswith("lkc-") else "SR")
        return Resp(202, {"id": key, "spec": {**json["spec"], "secret": secret}})

    def delete(self, url, auth=None, timeout=None):
        self.calls.append(("DELETE", url, None))
        if not self._authed(auth):
            return Resp(401)
        key_id = url.rsplit("/", 1)[1]
        if self.live.pop(key_id, None) is None:
            return Resp(404)
        return Resp(204)

    def run(self, argv, **_kw):
        self.cli_calls.append(list(argv))
        if argv[:5] == GLOBAL_CREATE:
            if self.global_cap_reached:
                return SimpleNamespace(returncode=1, stdout="", stderr="Error: global API key quota exceeded")
            key, secret = self._new_key("GLOBAL")
            return SimpleNamespace(returncode=0, stdout=json.dumps({"api_key": key, "api_secret": secret}), stderr="")
        if argv[:3] == ["confluent", "organization", "list"]:
            orgs = self.orgs if self.orgs is not None else [{"id": ORG, "name": "Acme", "is_current": True}]
            return SimpleNamespace(returncode=0, stdout=json.dumps(orgs), stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    @property
    def deleted(self) -> list[str]:
        return [url.rsplit("/", 1)[1] for method, url, _ in self.calls if method == "DELETE"]

    @property
    def posts(self) -> list[dict]:
        return [body for method, _url, body in self.calls if method == "POST"]


class TempRoot(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        env = patch.dict("os.environ", {"USER": "kevin", "LOGNAME": "kevin"}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.creds_file = self.root / "credentials.env"
        self.creds_file.write_text("")
        # Key generation for a service account must never run in this track.
        for name in ("offer_key_generation", "ensure_saved_confluent_login"):
            p = patch.object(common, name, side_effect=AssertionError(f"{name} called"))
            p.start()
            self.addCleanup(p.stop)
        self.login = patch.object(pp, "ensure_confluent_login", return_value=True)
        self.logged_in = self.login.start()
        self.addCleanup(self.login.stop)

    @contextmanager
    def cloud(self, fake: FakeConfluent):
        with (
            patch("requests.get", side_effect=fake.get),
            patch("requests.post", side_effect=fake.post),
            patch("requests.delete", side_effect=fake.delete),
            patch("subprocess.run", side_effect=fake.run),
            patch("subprocess.Popen", side_effect=AssertionError("Popen called")),
            patch.object(keys.time, "sleep"),
        ):
            yield

    def run_deploy(self, fake, automated=True, with_labs=False, answers=None, prompts=None):
        """Run the deploy; returns (exception or None, stdout, seed mock, labs mock)."""
        args = SimpleNamespace(automated=automated, with_labs=with_labs, bedrock=False)
        inputs = list(answers) if answers is not None else AssertionError("prompted")
        exc = None
        with (
            self.cloud(fake),
            patch.object(pp, "ensure_driver_race_history", return_value=True) as seed,
            patch.object(pp, "create_lab_objects", return_value=True) as labs,
            patch.object(common, "offer_start"),
            patch("builtins.input", side_effect=inputs),
            patch.object(pp, "prompt_with_default", side_effect=prompts or AssertionError("prompted")),
            redirect_stdout(io.StringIO()) as out,
        ):
            try:
                pp.deploy(args, self.root, self.creds_file, {})
            except SystemExit as e:
                exc = e
        return exc, out.getvalue(), seed, labs

    def saved(self) -> dict[str, str]:
        return meta.load_meta(self.root, meta.PREPROVISIONED)

    def card(self) -> dict[str, str]:
        return dotenv_values(self.root / "runs" / "pre-provisioned" / "credentials" / "DEMO.env")


# --- wiring -------------------------------------------------------------------------


class TrackWiringTests(TempRoot):
    def test_track_is_registered_everywhere_tracks_are_listed(self):
        self.assertIs(meta.TRACKS["pre-provisioned"], meta.PREPROVISIONED)
        self.assertEqual(
            meta.meta_path(self.root, meta.PREPROVISIONED), self.root / "runs" / "pre-provisioned" / "deployment.env"
        )
        self.assertIs(common.TRACKS["pre-provisioned"][0], meta.PREPROVISIONED)

    def test_pick_track(self):
        self.assertIs(common.pick_track(self.root, "pre-provisioned", automated=True), meta.PREPROVISIONED)
        # A fresh checkout still defaults to self-service; the new track is listed third.
        with patch("builtins.input", return_value="3"), redirect_stdout(io.StringIO()) as out:
            self.assertIs(common.pick_track(self.root, None, automated=False), meta.PREPROVISIONED)
        self.assertIn("Pre-provisioned", out.getvalue())
        # No Terraform state here: recorded keys are what make the track deployed.
        meta.save_meta(self.root, meta.PREPROVISIONED, **{meta.KEY_GLOBAL_KEY_ID: "GLOBAL1"})
        self.assertTrue(meta.is_deployed(self.root, meta.PREPROVISIONED))
        self.assertIs(common.pick_track(self.root, None, automated=True), meta.PREPROVISIONED)

    def test_deploy_dispatches_to_the_track(self):
        with (
            patch.object(deploy, "get_project_root", return_value=self.root),
            patch.object(deploy, "check_terraform_installed", return_value=True),
            patch.object(deploy.preprovisioned, "deploy") as track_deploy,
            patch.object(deploy, "deploy_standalone", side_effect=AssertionError),
            patch.object(deploy.selfservice, "deploy", side_effect=AssertionError),
            redirect_stdout(io.StringIO()),
        ):
            deploy.main(["--track", "pre-provisioned", "--automated", "--with-labs"])
        args = track_deploy.call_args.args[0]
        self.assertTrue(args.automated and args.with_labs)

    def test_retire_track_removes_only_this_tracks_files(self):
        mine = self.root / "runs" / "pre-provisioned"
        theirs = self.root / "runs" / "selfservice"
        for run in (mine, theirs):
            (run / "credentials").mkdir(parents=True)
            (run / "credentials" / "DEMO.env").write_text("F1_KAFKA_API_KEY=K\n")
            (run / ".seeded").write_text("env-1\n")
        meta.save_meta(self.root, meta.PREPROVISIONED, **{meta.KEY_ENVIRONMENT_ID: MINE.id})

        removed = meta.retire_track(self.root, meta.PREPROVISIONED)

        self.assertIn("runs/pre-provisioned/deployment.env", removed)
        self.assertFalse((mine / "credentials" / "DEMO.env").exists())
        self.assertFalse((mine / ".seeded").exists())
        self.assertTrue((theirs / "credentials" / "DEMO.env").exists())

    def test_reset_selects_the_track(self):
        # No Terraform state on this track: the keys deployment.env records are
        # what make it deployed.
        meta.save_meta(self.root, meta.PREPROVISIONED, **{meta.KEY_GLOBAL_KEY_ID: "GLOBAL1"})
        with redirect_stdout(io.StringIO()):
            self.assertIs(reset.select_track(self.root, None), meta.PREPROVISIONED)
            self.assertIs(reset.select_track(self.root, "pre-provisioned"), meta.PREPROVISIONED)

    def test_reset_race_hint_follows_the_track(self):
        for track in ("selfservice", "pre-provisioned"):
            (self.root / "runs" / track / "credentials").mkdir(parents=True)
            (self.root / "runs" / track / "credentials" / "DEMO.env").write_text("F1_ENVIRONMENT_ID=e\n")
        self.assertEqual(
            reset.f1_race_command(self.root, {}, meta.PREPROVISIONED),
            "uv run f1-race --creds runs/pre-provisioned/credentials/DEMO.env",
        )


class MetadataTests(TempRoot):
    def test_drop_meta_forgets_only_the_named_keys(self):
        meta.save_meta(self.root, meta.PREPROVISIONED, **{meta.KEY_ENVIRONMENT_ID: MINE.id, meta.KEY_SR_KEY_ID: "SR1"})
        meta.drop_meta(self.root, meta.PREPROVISIONED, meta.KEY_SR_KEY_ID, "F1_NOT_THERE")
        self.assertEqual(self.saved().get(meta.KEY_ENVIRONMENT_ID), MINE.id)
        self.assertNotIn(meta.KEY_SR_KEY_ID, self.saved())

    def test_recorded_key_ids_are_in_deletion_order_global_last(self):
        meta.save_meta(
            self.root,
            meta.PREPROVISIONED,
            **{meta.KEY_GLOBAL_KEY_ID: "G", meta.KEY_SR_KEY_ID: "S", meta.KEY_KAFKA_KEY_ID: "K"},
        )
        ids = meta.recorded_key_ids(self.root, meta.PREPROVISIONED)
        self.assertEqual(list(ids.values()), ["K", "S", "G"])
        self.assertEqual(
            (meta.KEY_GLOBAL_KEY_ID, meta.KEY_KAFKA_KEY_ID, meta.KEY_SR_KEY_ID),
            ("F1_GLOBAL_KEY_ID", "F1_KAFKA_KEY_ID", "F1_SR_KEY_ID"),
        )


# --- deploy, end to end -------------------------------------------------------------


class DeployTests(TempRoot):
    def test_happy_path_mints_three_keys_writes_the_card_and_runs_no_terraform(self):
        fake = FakeConfluent()
        exc, out, seed, labs = self.run_deploy(fake)

        self.assertIsNone(exc)
        # The only subprocess is the CLI minting the Global key; no terraform, ever.
        self.assertEqual([argv[:5] for argv in fake.cli_calls], [GLOBAL_CREATE])
        self.assertIn("-o", fake.cli_calls[0])
        self.assertFalse(any("terraform" in " ".join(argv) for argv in fake.cli_calls))
        self.assertFalse(hasattr(pp, "run_terraform"))
        self.logged_in.assert_called_once()
        self.assertFalse(self.logged_in.call_args.kwargs.get("interactive"))

        # Kafka and SR keys owned by the user, for the platform team's resources.
        self.assertEqual(
            [(p["spec"]["owner"]["id"], p["spec"]["resource"]) for p in fake.posts],
            [(USER, {"id": "lkc-1", "environment": MINE.id}), (USER, {"id": "lsrc-1", "environment": MINE.id})],
        )
        self.assertIn(
            "This creates your Global, Kafka and Schema Registry keys. It creates nothing in\nyour environment "
            "except the driver_race_history rows if they're missing;\nyour platform team provisioned the rest.",
            out,
        )
        self.assertIn(f"Catalog: {MINE.name}", out)

        # Nothing but the card pointer goes to credentials.env: no TF_VAR_*.
        self.assertEqual(dotenv_values(self.creds_file), {"F1_CARD": "runs/pre-provisioned/credentials/DEMO.env"})
        saved = self.saved()
        self.assertEqual(saved[meta.KEY_ENVIRONMENT_ID], MINE.id)
        self.assertEqual(saved[meta.KEY_ENVIRONMENT_NAME], MINE.name)
        self.assertEqual(saved[meta.KEY_USER_ID], USER)
        self.assertEqual(saved[meta.KEY_REGION], "us-east-1")
        self.assertEqual(saved[meta.KEY_RESOLVED_PREFIX], "DEMO")
        self.assertEqual(saved[meta.KEY_SECONDS_PER_LAP], "20")
        self.assertEqual(
            (saved[meta.KEY_GLOBAL_KEY_ID], saved[meta.KEY_KAFKA_KEY_ID], saved[meta.KEY_SR_KEY_ID]),
            ("GLOBAL1", "KAFKA2", "SR3"),
        )
        self.assertEqual(seed.call_args.args[0]["F1_ENVIRONMENT_ID"], MINE.id)
        self.assertIs(seed.call_args.args[2], meta.PREPROVISIONED)
        labs.assert_not_called()

    def test_card_flink_and_rtce_fields_are_the_global_key(self):
        fake = FakeConfluent(region="eu-central-1")
        self.run_deploy(fake)
        card = self.card()

        self.assertEqual((card["F1_FLINK_API_KEY"], card["F1_FLINK_API_SECRET"]), ("GLOBAL1", "GLOBAL1-secret"))
        self.assertEqual((card["F1_RTCE_API_KEY"], card["F1_RTCE_API_SECRET"]), ("GLOBAL1", "GLOBAL1-secret"))
        self.assertEqual((card["F1_KAFKA_API_KEY"], card["F1_KAFKA_API_SECRET"]), ("KAFKA2", "KAFKA2-secret"))
        self.assertEqual((card["F1_SR_API_KEY"], card["F1_SR_API_SECRET"]), ("SR3", "SR3-secret"))
        # Everything else comes from what the platform team built, in its region.
        self.assertEqual(card["F1_ORGANIZATION_ID"], ORG)
        self.assertEqual(card["F1_ENVIRONMENT_ID"], MINE.id)
        self.assertEqual(card["F1_CATALOG"], MINE.name)
        self.assertEqual(card["F1_DATABASE"], "RIVER-RACING-DEMO-CLUSTER")
        self.assertEqual(card["F1_COMPUTE_POOL_ID"], "lfcp-1")
        self.assertEqual(card["F1_KAFKA_BOOTSTRAP"], "SASL_SSL://pkc-1.eu-central-1.aws.confluent.cloud:9092")
        self.assertEqual(card["F1_SCHEMA_REGISTRY_URL"], "https://psrc-1.eu-central-1.aws.confluent.cloud")
        self.assertEqual(card["F1_FLINK_REST_ENDPOINT"], "https://flink.eu-central-1.aws.confluent.cloud")
        self.assertEqual(
            card["F1_RTCE_MCP_ENDPOINT"],
            "https://mcp.eu-central-1.aws.confluent.cloud/mcp/v1/context-engine"
            f"/organizations/{ORG}/environments/{MINE.id}/kafka-clusters/lkc-1",
        )
        self.assertEqual(card["F1_EMAIL"], EMAIL)
        # The principal ID never reaches the card under a "service account" name.
        self.assertNotIn("F1_SERVICE_ACCOUNT_ID", card)
        self.assertEqual(self.saved()[meta.KEY_REGION], "eu-central-1")

    def test_outputs_keep_the_old_tiers_shape_so_write_card_is_unchanged(self):
        found = lookup.Resources(
            organization_id=ORG,
            cloud="AWS",
            region="us-east-1",
            cluster_id="lkc-1",
            cluster_name="RIVER-RACING-DEMO-CLUSTER",
            cluster_bootstrap="SASL_SSL://pkc-1:9092",
            cluster_rest_endpoint="https://pkc-1:443",
            compute_pool_id="lfcp-1",
            schema_registry_id="lsrc-1",
            schema_registry_url="https://psrc-1",
            flink_rest_endpoint="https://flink",
        )
        out = pp._outputs(MINE, found, USER, ("G", "GS"), ("K", "KS"), ("S", "SS"))
        # The output names the old pre-provisioned Terraform root had.
        old_tier = (
            "organization_id environment_id environment_name cluster_id cluster_name user_id service_account_id "
            "cluster_bootstrap compute_pool_id flink_rest_endpoint flink_api_key flink_api_secret "
            "attendee_credentials rtce_api_key rtce_api_secret"
        )
        self.assertEqual(set(out), set(old_tier.split()))
        self.assertEqual((out["flink_api_key"], out["rtce_api_key"], out["service_account_id"]), ("G", "G", USER))

    def test_each_key_id_is_recorded_as_soon_as_the_key_exists(self):
        fake = FakeConfluent()
        seen = []
        fake.on_post = lambda _body: seen.append(meta.recorded_key_ids(self.root, meta.PREPROVISIONED))
        self.run_deploy(fake)
        self.assertEqual(
            seen,
            [
                {meta.KEY_GLOBAL_KEY_ID: "GLOBAL1"},  # before the Kafka key is minted
                {meta.KEY_KAFKA_KEY_ID: "KAFKA2", meta.KEY_GLOBAL_KEY_ID: "GLOBAL1"},  # before the SR key
            ],
        )

    def test_with_labs_builds_them_from_the_card(self):
        _exc, _out, _seed, labs = self.run_deploy(FakeConfluent(), with_labs=True)
        tf, root = labs.call_args.args
        self.assertEqual(root, self.root)
        self.assertEqual(tf["flink_api_key"], "GLOBAL1")
        self.assertEqual(tf["environment_name"], MINE.name)
        self.assertEqual(tf["cluster_name"], "RIVER-RACING-DEMO-CLUSTER")
        self.assertEqual(tf["flink_rest_endpoint"], "https://flink.us-east-1.aws.confluent.cloud")

    def test_new_keys_are_retried_until_they_propagate(self):
        fake = FakeConfluent()
        fake.kafka_probe_failures = 2
        exc, _out, _seed, _labs = self.run_deploy(fake)
        self.assertIsNone(exc)
        probes = [url for method, url, _ in fake.calls if method == "GET" and url.endswith("/topics")]
        self.assertEqual(len(probes), 3)

    def test_a_new_global_key_is_waited_for_before_its_first_use(self):
        fake = FakeConfluent()
        fake.global_warmup = 2
        exc, _out, _seed, _labs = self.run_deploy(fake)
        self.assertIsNone(exc)
        self.assertEqual(self.card()["F1_FLINK_API_KEY"], "GLOBAL1")

    def test_keys_that_never_propagate_are_deleted(self):
        fake = FakeConfluent()
        fake.kafka_probe_failures = 10**6
        with patch.object(keys, "PROPAGATION_TIMEOUT", 0):
            exc, out, seed, _labs = self.run_deploy(fake)
        self.assertEqual(exc.code, 1)
        self.assertIn("still can't list topics", out)
        self.assertEqual(fake.deleted, ["KAFKA2", "SR3", "GLOBAL1"])  # the Global key last
        self.assertEqual(fake.live, {})
        self.assertEqual(meta.recorded_key_ids(self.root, meta.PREPROVISIONED), {})
        seed.assert_not_called()

    def test_interactive_run_asks_once_before_minting_and_confirms_the_environment(self):
        fake = FakeConfluent()
        prompts = []

        def answer(prompt):
            prompts.append((prompt, len(fake.cli_calls)))
            return "y"

        with (
            self.cloud(fake),
            patch("builtins.input", side_effect=answer),
            patch.object(pp, "ensure_driver_race_history", return_value=True),
            patch.object(common, "offer_start"),
            redirect_stdout(io.StringIO()),
        ):
            pp.deploy(SimpleNamespace(automated=False, with_labs=False), self.root, self.creds_file, {})
        # "Continue?" before any key exists, then the environment once the key sees it.
        self.assertEqual(
            prompts, [("Continue? (y/n) [y]: ", 0), (f"Deploy into {MINE.name} ({MINE.id})? (y/n) [y]: ", 1)]
        )

    def test_declining_the_summary_creates_nothing(self):
        fake = FakeConfluent()
        exc, out, _seed, _labs = self.run_deploy(fake, automated=False, answers=["n"])
        self.assertIn("Cancelled.", out)
        self.assertEqual(exc.code, 0)
        self.assertEqual(fake.cli_calls, [])
        self.assertFalse(meta.meta_path(self.root, meta.PREPROVISIONED).exists())


class StopTests(TempRoot):
    def test_cli_not_logged_in_stops_before_anything(self):
        self.logged_in.return_value = False
        fake = FakeConfluent()
        exc, out, _seed, _labs = self.run_deploy(fake)
        self.assertEqual(exc.code, 1)
        self.assertIn("`confluent login`", out)
        self.assertEqual((fake.cli_calls, fake.calls), ([], []))
        self.assertFalse(meta.meta_path(self.root, meta.PREPROVISIONED).exists())

    def test_global_key_cap_says_how_to_free_one(self):
        fake = FakeConfluent()
        fake.global_cap_reached = True
        exc, _out, _seed, _labs = self.run_deploy(fake)
        self.assertIsInstance(exc, keys.GlobalKeyCapError)
        self.assertIn("2 Global keys", exc.code)
        self.assertIn("Console → API keys → My account", exc.code)
        self.assertEqual(fake.calls, [])
        self.assertFalse(meta.meta_path(self.root, meta.PREPROVISIONED).exists())

    def test_email_falls_back_to_the_prompt(self):
        fake = FakeConfluent(email="")
        asked = []

        def prompt(label, default="", secret=False):
            asked.append(label)
            return "typed@acme.com"

        exc, _out, _seed, _labs = self.run_deploy(fake, automated=False, answers=["y", "y"], prompts=prompt)
        self.assertIsNone(exc)
        self.assertEqual(asked, ["Your email (the one you sign in to Confluent Cloud with)"])
        self.assertEqual(self.card()["F1_EMAIL"], "typed@acme.com")

    def test_email_fallback_fails_under_automated_and_deletes_the_global_key(self):
        fake = FakeConfluent(email="")
        exc, out, _seed, _labs = self.run_deploy(fake)
        self.assertEqual(exc.code, 1)
        self.assertIn("--automated can't ask", out)
        self.assertEqual(fake.deleted, ["GLOBAL1"])
        self.assertEqual(meta.recorded_key_ids(self.root, meta.PREPROVISIONED), {})

    def test_missing_platform_resources_stop_naming_the_environment(self):
        for missing in ("clusters", "pools", "registries"):
            with self.subTest(missing=missing):
                meta.clear_meta(self.root, meta.PREPROVISIONED)
                fake = FakeConfluent()
                setattr(fake, missing, [])
                exc, _out, _seed, _labs = self.run_deploy(fake)

                self.assertIsInstance(exc, lookup.NotProvisionedError)
                self.assertIn(f"hasn't provisioned {MINE.name} ({MINE.id}) yet", exc.code)
                self.assertIn(f"Send them this environment ID, {MINE.id}", exc.code)
                self.assertEqual(fake.posts, [])
                # The Global key is deleted again, so the re-run has both slots...
                self.assertEqual(fake.deleted, ["GLOBAL1"])
                self.assertEqual(meta.recorded_key_ids(self.root, meta.PREPROVISIONED), {})
                # ...and the environment is remembered for it.
                self.assertEqual(self.saved()[meta.KEY_ENVIRONMENT_ID], MINE.id)

    def test_no_environment_names_the_fix_and_the_organization_switch(self):
        fake = FakeConfluent(envs=())
        fake.orgs = [{"id": ORG, "name": "Acme", "is_current": True}, {"id": "org-2", "name": "Sandbox"}]
        exc, _out, _seed, _labs = self.run_deploy(fake)
        self.assertIsInstance(exc, lookup.NoEnvironmentError)
        self.assertIn("direct EnvironmentAdmin binding", exc.code)
        self.assertIn("confluent login --organization <id>", exc.code)
        self.assertIn("org-2  Sandbox", exc.code)
        self.assertEqual(fake.deleted, ["GLOBAL1"])

    def test_no_environment_in_a_single_organization_skips_the_switch(self):
        exc, _out, _seed, _labs = self.run_deploy(FakeConfluent(envs=()))
        self.assertNotIn("--organization", exc.code)

    def test_a_different_users_key_is_refused_on_a_recorded_deployment(self):
        old = {meta.KEY_GLOBAL_KEY_ID: "GOLD", meta.KEY_KAFKA_KEY_ID: "KOLD", meta.KEY_SR_KEY_ID: "SOLD"}
        meta.save_meta(self.root, meta.PREPROVISIONED, **{meta.KEY_USER_ID: "u-someoneelse", **old})
        fake = FakeConfluent()
        exc, out, _seed, _labs = self.run_deploy(fake)
        self.assertEqual(exc.code, 1)
        self.assertIn("u-someoneelse", out)
        self.assertEqual(fake.deleted, ["GLOBAL1"])
        # The previous record is put back as it was.
        self.assertEqual(meta.recorded_key_ids(self.root, meta.PREPROVISIONED), {k: old[k] for k in meta.KEY_ID_FIELDS})


class RerunTests(TempRoot):
    def test_rerun_reuses_a_card_whose_keys_all_exist(self):
        fake = FakeConfluent()
        self.assertIsNone(self.run_deploy(fake)[0])
        first = self.card()
        fake.cli_calls.clear()
        fake.calls.clear()
        self.logged_in.reset_mock()

        exc, out, seed, _labs = self.run_deploy(fake)

        self.assertIsNone(exc)
        self.assertIn("its keys all still exist, so this run creates none", out)
        self.assertEqual(fake.cli_calls, [])  # no Global key minted, no CLI needed
        self.logged_in.assert_not_called()
        self.assertEqual({method for method, _url, _ in fake.calls}, {"GET"})
        self.assertEqual(sorted(url.rsplit("/", 1)[1] for _m, url, _ in fake.calls), ["GLOBAL1", "KAFKA2", "SR3"])
        self.assertEqual(self.card(), first)
        seed.assert_called_once()

    def test_rerun_with_a_revoked_key_mints_new_keys_and_deletes_the_old_ones(self):
        fake = FakeConfluent()
        self.run_deploy(fake)
        fake.live.pop("KAFKA2")  # revoked in the Console
        fake.calls.clear()

        exc, out, _seed, _labs = self.run_deploy(fake)

        self.assertIsNone(exc)
        self.assertIn("At least one of them no longer exists — minting new keys.", out)
        self.assertIn("the old ones still left are deleted afterwards", out)
        card = self.card()
        self.assertEqual(
            (card["F1_FLINK_API_KEY"], card["F1_KAFKA_API_KEY"], card["F1_SR_API_KEY"]), ("GLOBAL4", "KAFKA5", "SR6")
        )
        self.assertEqual(
            list(meta.recorded_key_ids(self.root, meta.PREPROVISIONED).values()), ["KAFKA5", "SR6", "GLOBAL4"]
        )
        # The old keys that still existed are gone, the old Global key last.
        self.assertEqual(fake.deleted, ["KAFKA2", "SR3", "GLOBAL1"])
        self.assertEqual(set(fake.live), {"GLOBAL4", "KAFKA5", "SR6"})

    def test_rerun_with_a_revoked_global_key_replaces_the_card(self):
        fake = FakeConfluent()
        self.run_deploy(fake)
        fake.live.pop("GLOBAL1")
        exc, _out, _seed, _labs = self.run_deploy(fake)
        self.assertIsNone(exc)
        self.assertEqual(self.card()["F1_FLINK_API_KEY"], "GLOBAL4")
        self.assertEqual(set(fake.live), {"GLOBAL4", "KAFKA5", "SR6"})

    def test_rerun_never_moves_the_deployment_to_another_environment(self):
        fake = FakeConfluent(envs=(MINE,))
        self.run_deploy(fake)
        fake.live.pop("SR3")
        # The key now sees one other environment, which a fresh run would just use.
        fake.envs = [OTHER]
        exc, out, _seed, _labs = self.run_deploy(fake)

        self.assertEqual(exc.code, 1)
        self.assertIn(f"your keys from the last deploy are for {MINE.id}", out)
        self.assertEqual(fake.deleted, ["GLOBAL4"])  # only this run's key
        self.assertEqual(self.saved()[meta.KEY_ENVIRONMENT_ID], MINE.id)
        self.assertEqual(
            list(meta.recorded_key_ids(self.root, meta.PREPROVISIONED).values()), ["KAFKA2", "SR3", "GLOBAL1"]
        )

    def test_a_network_failure_during_the_check_never_counts_as_revoked(self):
        fake = FakeConfluent()
        self.run_deploy(fake)
        with (
            patch("requests.get", side_effect=requests.ConnectionError("down")),
            patch("subprocess.run", side_effect=AssertionError("minted")),
            redirect_stdout(io.StringIO()) as out,
            self.assertRaises(SystemExit),
        ):
            pp.deploy(SimpleNamespace(automated=True, with_labs=False), self.root, self.creds_file, {})
        self.assertIn("could not reach the Confluent Cloud API", out.getvalue())


# --- environment resolution ----------------------------------------------------------


class ResolveEnvironmentTests(unittest.TestCase):
    def resolve(self, envs, saved="", locked=False, automated=True, answers=None, orgs=None):
        inputs = list(answers) if answers is not None else AssertionError("prompted")
        fake = FakeConfluent()
        fake.orgs = orgs
        with (
            patch("builtins.input", side_effect=inputs),
            patch("subprocess.run", side_effect=fake.run),
            redirect_stdout(io.StringIO()) as self.out,
        ):
            return pp._resolve_environment(saved, locked, list(envs), EMAIL, automated)

    def test_one_visible_environment_is_used(self):
        self.assertEqual(self.resolve([MINE]), MINE)

    def test_several_environments_narrowed_by_name(self):
        self.assertEqual(self.resolve([OTHER, MINE, DECOY]), MINE)

    def test_pick_list_fails_under_automated_with_the_list(self):
        with self.assertRaises(SystemExit) as raised:
            self.resolve([OTHER, DECOY])
        self.assertEqual(raised.exception.code, 1)
        for env in (OTHER, DECOY):
            self.assertIn(env.id, self.out.getvalue())
        self.assertIn("without\n--automated", self.out.getvalue())

    def test_a_saved_environment_is_preferred(self):
        # The saved choice beats a name match, so a pick made once sticks.
        self.assertEqual(self.resolve([MINE, OTHER], saved=OTHER.id), OTHER)

    def test_a_saved_environment_the_key_no_longer_sees_is_looked_up_again(self):
        self.assertEqual(self.resolve([MINE], saved="env-gone"), MINE)

    def test_a_locked_environment_is_never_retargeted_or_confirmed(self):
        self.assertEqual(self.resolve([MINE, OTHER], saved=OTHER.id, locked=True, automated=False, answers=[]), OTHER)

    def test_a_locked_environment_the_key_no_longer_sees_stops(self):
        with self.assertRaises(SystemExit) as raised:
            self.resolve([MINE], saved=OTHER.id, locked=True)
        self.assertEqual(raised.exception.code, 1)
        self.assertIn(OTHER.id, self.out.getvalue())

    def test_interactive_confirmation(self):
        with patch("builtins.input", side_effect=["y"]) as confirm, redirect_stdout(io.StringIO()):
            pp._resolve_environment("", False, [MINE], EMAIL, automated=False)
        self.assertEqual(confirm.call_args.args[0], f"Deploy into {MINE.name} ({MINE.id})? (y/n) [y]: ")

    def test_pick_list_is_prompted(self):
        # Sorted by name: Jane-Roe-1a2b_env, shared_env.
        self.assertEqual(self.resolve([OTHER, DECOY], automated=False, answers=["x", "2"]), DECOY)

    def test_declining_the_match_offers_the_pick_list(self):
        self.assertEqual(self.resolve([MINE, OTHER], automated=False, answers=["n", "1"]), OTHER)

    def test_declining_the_only_environment_cancels(self):
        with self.assertRaises(SystemExit) as raised:
            self.resolve([MINE], automated=False, answers=["n"])
        self.assertEqual(raised.exception.code, 0)

    def test_an_unknown_organization_count_still_mentions_the_switch(self):
        with (
            patch("subprocess.run", return_value=SimpleNamespace(returncode=1, stdout="", stderr="boom")),
            self.assertRaises(lookup.NoEnvironmentError) as raised,
        ):
            pp._resolve_environment("", False, [], EMAIL, True)
        self.assertIn("confluent login --organization <id>", str(raised.exception))


# --- keys: the CLI and REST wrappers ---------------------------------------------------


class MintGlobalKeyTests(unittest.TestCase):
    def test_parses_the_cli_json(self):
        done = SimpleNamespace(returncode=0, stdout='{"api_key": "G", "api_secret": "S"}', stderr="")
        with patch.object(keys.subprocess, "run", return_value=done) as run:
            self.assertEqual(keys.mint_global_key(), ("G", "S"))
        argv = run.call_args.args[0]
        self.assertEqual(argv[:5], GLOBAL_CREATE)
        self.assertEqual(argv[-2:], ["-o", "json"])

    def test_cap_names_the_previous_key(self):
        done = SimpleNamespace(returncode=1, stdout="", stderr="Error: maximum number of API keys reached")
        with (
            patch.object(keys.subprocess, "run", return_value=done),
            self.assertRaises(keys.GlobalKeyCapError) as raised,
        ):
            keys.mint_global_key(previous="GOLD")
        self.assertIn("delete that one", raised.exception.code)
        self.assertIn("GOLD", raised.exception.code)

    def test_other_failures_show_the_cli_error(self):
        done = SimpleNamespace(returncode=1, stdout="", stderr="Error: not logged in")
        with patch.object(keys.subprocess, "run", return_value=done), self.assertRaises(SystemExit) as raised:
            keys.mint_global_key()
        self.assertNotIsInstance(raised.exception, keys.GlobalKeyCapError)
        self.assertIn("not logged in", raised.exception.code)

    def test_missing_cli(self):
        with (
            patch.object(keys.subprocess, "run", side_effect=FileNotFoundError),
            self.assertRaises(SystemExit) as raised,
        ):
            keys.mint_global_key()
        self.assertIn("not installed", raised.exception.code)


class RestKeyTests(unittest.TestCase):
    def test_create_accepts_202_and_returns_the_one_time_secret(self):
        body = {"id": "K1", "spec": {"secret": "S1"}}
        with patch.object(keys.requests, "post", return_value=Resp(202, body)) as post:
            self.assertEqual(keys.create_api_key(("G", "GS"), USER, "lkc-1", MINE.id, "name"), ("K1", "S1"))
        (url,), kwargs = post.call_args
        self.assertEqual(url, f"{API}/iam/v2/api-keys")
        self.assertEqual(kwargs["auth"], ("G", "GS"))
        self.assertEqual(kwargs["json"]["spec"]["owner"], {"id": USER})
        self.assertEqual(kwargs["json"]["spec"]["resource"], {"id": "lkc-1", "environment": MINE.id})

    def test_create_refusal_shows_the_api_message(self):
        with (
            patch.object(keys.requests, "post", return_value=Resp(400, {"errors": [{"detail": "quota"}]})),
            self.assertRaises(SystemExit) as raised,
        ):
            keys.create_api_key(("G", "GS"), USER, "lsrc-1", MINE.id, "name")
        self.assertIn("HTTP 400", raised.exception.code)
        self.assertIn("quota", raised.exception.code)

    def test_key_exists(self):
        for status, expected in ((200, True), (404, False), (401, False), (403, False)):
            with self.subTest(status=status), patch.object(keys.requests, "get", return_value=Resp(status)):
                self.assertIs(keys.key_exists(("G", "GS"), "K1"), expected)
        with patch.object(keys.requests, "get", return_value=Resp(500)), self.assertRaises(requests.HTTPError):
            keys.key_exists(("G", "GS"), "K1")

    def test_delete_reports_what_happened(self):
        for response, expected in ((Resp(204), "deleted"), (Resp(404), "gone"), (Resp(401), "failed")):
            with self.subTest(expected=expected), patch.object(keys.requests, "delete", return_value=response):
                self.assertEqual(keys.delete_api_key(("G", "GS"), "K1"), expected)
        with patch.object(keys.requests, "delete", side_effect=requests.ConnectionError):
            self.assertEqual(keys.delete_api_key(("G", "GS"), "K1"), "failed")

    def test_wait_until_retries_and_gives_up(self):
        answers = iter([False, requests.ConnectionError(), True])

        def check():
            value = next(answers)
            if isinstance(value, Exception):
                raise value
            return value

        with patch.object(keys.time, "sleep") as sleep:
            self.assertTrue(keys.wait_until(check))
            self.assertEqual(sleep.call_count, 2)
            self.assertFalse(keys.wait_until(lambda: False, timeout=0))


# --- no "service account" minting in this track ---------------------------------------


class NoServiceAccountMintingTests(unittest.TestCase):
    def test_setup_rtce_never_offers_to_mint_for_a_user_principal(self):
        with (
            patch("builtins.input", side_effect=AssertionError("prompted")),
            patch.object(setup_rtce.subprocess, "run", side_effect=AssertionError("CLI called")),
        ):
            self.assertEqual(setup_rtce.offer_cli_key(USER), ("", ""))

    def test_legacy_card_minting_refuses_a_user_principal(self):
        # It deletes the principal's existing Global keys first — for a user, that
        # would be the developer's own key.
        with patch.object(creds_mod.subprocess, "run", side_effect=AssertionError("CLI called")):
            self.assertEqual(creds_mod._mint_rtce_key(USER, "DEMO"), ("", ""))


if __name__ == "__main__":
    unittest.main()
