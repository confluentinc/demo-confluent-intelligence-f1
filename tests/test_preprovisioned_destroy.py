"""`uv run destroy` for the pre-provisioned track — keys only, and never the environment.

The track runs no Terraform: the platform team built everything in the
developer's environment, and the deploy only minted three keys owned by the
developer (Kafka, Schema Registry, Global) and wrote a card. Their IDs in
runs/pre-provisioned/deployment.env are the whole deployment. These tests pin
that the teardown deletes exactly those IDs over REST, authenticated with the
card's Global key and deleting it last; that a partial failure keeps what is
left (and the Global key) for a retry; that nothing recorded is a no-op; and that
nothing reaches the environment, Terraform or a subprocess.

Everything runs against a temp project root with HTTP faked.
"""

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests

from scripts.common import deployment_meta as meta
from scripts.common import destroy
from scripts.preprovisioned import lookup

REPO = Path(__file__).resolve().parents[1]
API_KEYS = "https://api.confluent.cloud/iam/v2/api-keys/"
ENV_ID, ENV_NAME, USER = "env-mine", "John-Doe-c21e_env", "u-johndoe"
KAFKA, SR, GLOBAL, GLOBAL_SECRET = "KAFKA1", "SR1", "GLOBAL1", "GLOBALSECRET"
CARD = "runs/pre-provisioned/credentials/DEMO.env"
KEPT = f"Your environment {ENV_NAME} ({ENV_ID}) and everything in it stay — they belong to your platform team."


class Resp:
    def __init__(self, status: int):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeIam:
    """The only calls the teardown may make: GET (does the Global key work?) and DELETE of a key."""

    def __init__(self, global_ok: bool = True, fail: dict[str, int] | None = None, unreachable: bool = False):
        self.global_ok, self.fail, self.unreachable = global_ok, fail or {}, unreachable
        self.calls: list[tuple[str, str, tuple]] = []

    def get(self, url, auth=None, **_kw):
        self.calls.append(("GET", url, auth))
        if self.unreachable:
            raise requests.ConnectionError("unreachable")
        return Resp(200 if self.global_ok else 401)

    def delete(self, url, auth=None, **_kw):
        self.calls.append(("DELETE", url, auth))
        return Resp(self.fail.get(url.removeprefix(API_KEYS), 204))

    def deleted(self) -> list[str]:
        return [url.removeprefix(API_KEYS) for method, url, _auth in self.calls if method == "DELETE"]


class PreprovisionedDestroyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        env = patch.dict("os.environ", {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

        self.creds_file = self.root / "credentials.env"
        self.creds_file.write_text(f"F1_CARD={CARD}\n")
        self.card = self.root / CARD
        self.card.parent.mkdir(parents=True)
        self.card.write_text(
            f"F1_ENVIRONMENT_ID={ENV_ID}\nF1_CATALOG={ENV_NAME}\n"
            f"F1_KAFKA_API_KEY={KAFKA}\nF1_KAFKA_API_SECRET=KS\nF1_SR_API_KEY={SR}\nF1_SR_API_SECRET=SS\n"
            f"F1_FLINK_API_KEY={GLOBAL}\nF1_FLINK_API_SECRET={GLOBAL_SECRET}\n"
            f"F1_RTCE_API_KEY={GLOBAL}\nF1_RTCE_API_SECRET={GLOBAL_SECRET}\n"
        )
        self.card.with_suffix(".md").write_text("# DEMO\n")
        meta.save_meta(
            self.root,
            meta.PREPROVISIONED,
            **{
                meta.KEY_ENVIRONMENT_ID: ENV_ID,
                meta.KEY_ENVIRONMENT_NAME: ENV_NAME,
                meta.KEY_USER_ID: USER,
                meta.KEY_CARD: CARD,
                meta.KEY_KAFKA_KEY_ID: KAFKA,
                meta.KEY_SR_KEY_ID: SR,
                meta.KEY_GLOBAL_KEY_ID: GLOBAL,
            },
        )

    def run_destroy(self, argv: list[str], fake: FakeIam, answers: list[str] | None = None) -> tuple[int, str]:
        prompts = list(answers) if answers is not None else AssertionError("prompted")
        with (
            patch.object(destroy, "get_project_root", return_value=self.root),
            patch("scripts.selfservice.provision.export_selfservice_tf_env"),
            patch.object(destroy, "run_terraform_destroy", side_effect=self.fake_terraform) as self.terraform,
            patch.object(destroy, "cleanup_terraform_artifacts"),
            patch.object(destroy, "_cleanup_mcp") as self.mcp_cleanup,
            patch("requests.get", side_effect=fake.get),
            patch("requests.delete", side_effect=fake.delete),
            patch("requests.post", side_effect=AssertionError("POST")),
            patch("subprocess.run", side_effect=AssertionError("subprocess called")),
            # The teardown needs no lookup: the IDs were recorded at deploy.
            patch.object(lookup, "resolve_user_id", side_effect=AssertionError("API lookup")),
            patch.object(lookup, "visible_environments", side_effect=AssertionError("API lookup")),
            patch("builtins.input", side_effect=prompts),
            redirect_stdout(io.StringIO()) as out,
        ):
            try:
                destroy.main(argv)
            except SystemExit as e:
                return e.code or 0, out.getvalue()
        return 0, out.getvalue()

    def fake_terraform(self, env_path, *_a, **_kw):
        (env_path / "terraform.tfstate").unlink()
        return True

    def make_state(self, tier: str) -> None:
        state = self.root / "terraform" / tier / "terraform.tfstate"
        state.parent.mkdir(parents=True)
        state.write_text("{}")

    def recorded(self) -> dict[str, str]:
        return meta.recorded_key_ids(self.root, meta.PREPROVISIONED)

    # --- the happy path ---

    def test_deletes_exactly_the_recorded_keys_global_last_with_the_global_key(self):
        fake = FakeIam()
        code, _out = self.run_destroy(["--only", "pre-provisioned", "--force"], fake)

        self.assertEqual(code, 0)
        self.assertEqual(fake.deleted(), [KAFKA, SR, GLOBAL])
        # Nothing but the developer's own keys is touched — never the environment.
        for _method, url, auth in fake.calls:
            self.assertTrue(url.startswith(API_KEYS), url)
            self.assertEqual(auth, (GLOBAL, GLOBAL_SECRET))
        self.terraform.assert_not_called()

    def test_success_retires_the_card_and_the_record(self):
        code, _out = self.run_destroy(["--force"], FakeIam())

        self.assertEqual(code, 0)
        self.assertFalse(meta.meta_path(self.root, meta.PREPROVISIONED).exists())
        self.assertFalse(self.card.exists())
        self.assertFalse(self.card.with_suffix(".md").exists())
        self.assertNotIn("F1_CARD", self.creds_file.read_text())
        # Nothing is left deployed, so the MCP registrations go too.
        self.mcp_cleanup.assert_called_once()

    def test_says_the_environment_stays_and_belongs_to_the_platform_team(self):
        _code, out = self.run_destroy(["--force"], FakeIam())

        self.assertIn(f"Will delete your pre-provisioned keys and credential card: Kafka key {KAFKA}", out)
        self.assertIn(KEPT, out)
        self.assertIn(f"Kept your environment {ENV_NAME} ({ENV_ID}) and everything in it.", out)
        self.assertNotIn("WARNING", out)

    def test_already_gone_keys_count_as_deleted(self):
        code, out = self.run_destroy(["--force"], FakeIam(fail={KAFKA: 404}))

        self.assertEqual(code, 0)
        self.assertIn(f"Kafka key {KAFKA}: already gone.", out)
        self.assertFalse(meta.meta_path(self.root, meta.PREPROVISIONED).exists())

    # --- partial failures: keep what's left, and the Global key, for a retry ---

    def test_a_failure_keeps_the_rest_and_the_global_key_and_a_retry_finishes(self):
        fake = FakeIam(fail={SR: 500})
        code, out = self.run_destroy(["--force"], fake)

        self.assertEqual(code, 1)
        self.assertEqual(fake.deleted(), [KAFKA, SR])
        self.assertEqual(self.recorded(), {meta.KEY_SR_KEY_ID: SR, meta.KEY_GLOBAL_KEY_ID: GLOBAL})
        self.assertTrue(self.card.exists())
        self.assertIn(f"Not deleting Global key {GLOBAL} yet", out)
        self.mcp_cleanup.assert_not_called()

        retry = FakeIam()
        code, _out = self.run_destroy(["--force"], retry)
        self.assertEqual(code, 0)
        self.assertEqual(retry.deleted(), [SR, GLOBAL])
        self.assertFalse(meta.meta_path(self.root, meta.PREPROVISIONED).exists())

    def test_a_failed_global_delete_keeps_only_the_global_key(self):
        code, _out = self.run_destroy(["--force"], FakeIam(fail={GLOBAL: 500}))

        self.assertEqual(code, 1)
        self.assertEqual(self.recorded(), {meta.KEY_GLOBAL_KEY_ID: GLOBAL})
        self.assertEqual(meta.load_meta(self.root, meta.PREPROVISIONED)[meta.KEY_ENVIRONMENT_ID], ENV_ID)

    def test_an_unreachable_api_deletes_nothing(self):
        fake = FakeIam(unreachable=True)
        code, _out = self.run_destroy(["--force"], fake)

        self.assertEqual(code, 1)
        self.assertEqual(fake.deleted(), [])
        self.assertEqual(list(self.recorded().values()), [KAFKA, SR, GLOBAL])

    # --- nothing to authenticate with ---

    def test_a_missing_card_lists_the_keys_to_delete_in_the_console(self):
        self.card.unlink()
        fake = FakeIam()
        code, out = self.run_destroy(["--force"], fake)

        self.assertEqual(code, 1)
        self.assertEqual(fake.calls, [])
        self.assertIn("Console → API keys → My account", out)
        for key in (f"Kafka key {KAFKA}", f"Schema Registry key {SR}", f"Global key {GLOBAL}"):
            self.assertIn(key, out)
        self.assertEqual(list(self.recorded().values()), [KAFKA, SR, GLOBAL])

    def test_keys_deleted_by_hand_can_be_forgotten(self):
        self.card.unlink()

        # Proceed, then "not yet": the record stays.
        code, _out = self.run_destroy([], FakeIam(), answers=["y", "n"])
        self.assertEqual(code, 1)
        self.assertTrue(self.recorded())

        # Proceed, "they're gone", keep credentials.env.
        code, _out = self.run_destroy([], FakeIam(), answers=["y", "y", "n"])
        self.assertEqual(code, 0)
        self.assertFalse(meta.meta_path(self.root, meta.PREPROVISIONED).exists())

    def test_a_revoked_global_key_deletes_nothing(self):
        fake = FakeIam(global_ok=False)
        code, out = self.run_destroy(["--force"], fake)

        self.assertEqual(code, 1)
        self.assertEqual(fake.deleted(), [])
        self.assertIn(f"Global key {GLOBAL} is no longer accepted", out)
        self.assertEqual(list(self.recorded().values()), [KAFKA, SR, GLOBAL])

    # --- nothing recorded, and the other groups ---

    def test_nothing_recorded_is_a_no_op(self):
        meta.drop_meta(self.root, meta.PREPROVISIONED, *meta.KEY_ID_FIELDS)
        fake = FakeIam()
        code, out = self.run_destroy(["--only", "pre-provisioned"], fake)

        self.assertEqual(code, 0)
        self.assertIn("Nothing to destroy", out)
        self.assertIn("no keys recorded in runs/pre-provisioned/deployment.env", out)
        self.assertEqual(fake.calls, [])
        self.assertTrue(self.card.exists())

    def test_offered_beside_a_terraform_group_and_deleted_alone(self):
        self.make_state("self-service")
        fake = FakeIam()

        # Two groups -> pick 2 (pre-provisioned), proceed, keep credentials.env.
        code, out = self.run_destroy([], fake, answers=["2", "y", ""])

        self.assertEqual(code, 0)
        self.assertIn(f"Kafka key {KAFKA} + Schema Registry key {SR} + Global key {GLOBAL}", out)
        self.assertEqual(fake.deleted(), [KAFKA, SR, GLOBAL])
        self.terraform.assert_not_called()
        # Self-service is still deployed: its MCP registrations stay.
        self.mcp_cleanup.assert_not_called()

    def test_another_group_alone_leaves_the_keys(self):
        self.make_state("self-service")
        fake = FakeIam()

        code, _out = self.run_destroy(["--only", "self-service", "--force"], fake)

        self.assertEqual(code, 0)
        self.assertEqual(fake.calls, [])
        self.assertEqual(list(self.recorded().values()), [KAFKA, SR, GLOBAL])
        # The recorded keys are a live deployment.
        self.mcp_cleanup.assert_not_called()

    def test_the_track_is_no_terraform_tier(self):
        # Globbed rather than "doesn't exist": an old checkout may keep untracked state there.
        self.assertEqual(list((REPO / "terraform" / "pre-provisioned").glob("**/*.tf")), [])
        self.assertNotIn("pre-provisioned", [tier for _name, tiers, _desc in destroy.GROUPS for tier in tiers])


if __name__ == "__main__":
    unittest.main()
