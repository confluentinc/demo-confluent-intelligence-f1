"""`uv run setup-mcp` — card-based MCP registration for Claude Code and Codex.

Nothing here touches the real ``~/.claude.json`` / ``~/.codex/config.toml``, the
real project root, or npm: every test runs against a fabricated card in a temp
directory with ``scripts.setup_mcp._run`` replaced by a recorder. The one thing
these tests must never do is register a server into the developer's own agent
config, so the recorder is installed before ``main()`` is ever called.
"""

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import setup_mcp

# A complete card, with values distinctive enough to trace into the output.
CARD = {
    "F1_PREFIX": "f1wp007",
    "F1_KAFKA_BOOTSTRAP": "SASL_SSL://pkc-abc12.us-east-1.aws.confluent.cloud:9092",
    "F1_KAFKA_API_KEY": "KKEY7",
    "F1_KAFKA_API_SECRET": "KSECRET7",
    "F1_CLUSTER_ID": "lkc-cluster7",
    "F1_ENVIRONMENT_ID": "env-abc7",
    "F1_ORGANIZATION_ID": "org-abc7",
    "F1_FLINK_API_KEY": "FKEY7",
    "F1_FLINK_API_SECRET": "FSECRET7",
    "F1_FLINK_REST_ENDPOINT": "https://flink.us-east-1.aws.confluent.cloud",
    "F1_COMPUTE_POOL_ID": "lfcp-pool7",
    "F1_CATALOG": "RIVER-RACING-f1wp007-ENV",
    "F1_DATABASE": "river-racing-f1wp007",
    "F1_SR_API_KEY": "SRKEY7",
    "F1_SR_API_SECRET": "SRSECRET7",
    "F1_SCHEMA_REGISTRY_URL": "https://psrc-abc12.us-east-1.aws.confluent.cloud",
}

CARD_BODY = "".join(f"{k}={v}\n" for k, v in CARD.items())


class FakeRun:
    """Stands in for ``setup_mcp._run``: records argv, returns a canned result.

    ``node`` probes answer with a prebuilt-ABI version so the preflight passes;
    anything else returns success. ``npm`` must never appear — the tests
    pre-create ``dist/index.js`` so the install short-circuits — and an assertion
    here is what proves it.
    """

    def __init__(self, node_version: str = "24.4.0", node_abi: int = 137, add_returncode: int = 0):
        self.calls: list[list[str]] = []
        self.node_version = node_version
        self.node_abi = node_abi
        self.add_returncode = add_returncode

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        assert argv[0] != "npm", "npm must not be invoked by these tests"

        if argv[0].endswith("node") or argv[0] == "node":
            out = f"v{self.node_version}" if argv[1] == "--version" else str(self.node_abi)
            return _Completed(argv, 0, out)

        rc = self.add_returncode if "add" in argv else 0
        return _Completed(argv, rc, "")

    def agent_calls(self) -> list[list[str]]:
        """Only the claude/codex invocations, in order."""
        return [c for c in self.calls if c[0] in ("claude", "codex")]


class _Completed:
    def __init__(self, args, returncode, stdout):
        self.args = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


class PureMappingTests(unittest.TestCase):
    """The card -> MCP variable mapping, with no filesystem or subprocess in play."""

    def test_card_values_reach_the_right_mcp_variables(self):
        env = dict(setup_mcp.build_mcp_env(CARD))

        self.assertEqual(env["BOOTSTRAP_SERVERS"], "pkc-abc12.us-east-1.aws.confluent.cloud:9092")
        self.assertEqual(env["KAFKA_API_KEY"], "KKEY7")
        self.assertEqual(env["KAFKA_CLUSTER_ID"], "lkc-cluster7")
        self.assertEqual(env["FLINK_COMPUTE_POOL_ID"], "lfcp-pool7")
        self.assertEqual(env["FLINK_ORG_ID"], "org-abc7")
        self.assertEqual(env["FLINK_REST_ENDPOINT"], CARD["F1_FLINK_REST_ENDPOINT"])
        self.assertEqual(env["SCHEMA_REGISTRY_ENDPOINT"], CARD["F1_SCHEMA_REGISTRY_URL"])
        self.assertEqual(env["SCHEMA_REGISTRY_API_SECRET"], "SRSECRET7")

    def test_environment_id_feeds_both_kafka_and_flink(self):
        env = dict(setup_mcp.build_mcp_env(CARD))
        self.assertEqual(env["KAFKA_ENV_ID"], "env-abc7")
        self.assertEqual(env["FLINK_ENV_ID"], "env-abc7")

    def test_catalog_and_database_become_flink_names(self):
        env = dict(setup_mcp.build_mcp_env(CARD))
        self.assertEqual(env["FLINK_CATALOG_NAME"], CARD["F1_CATALOG"])
        self.assertEqual(env["FLINK_DATABASE_NAME"], CARD["F1_DATABASE"])

    def test_rest_endpoint_derived_from_bootstrap_host(self):
        # Same host, https scheme, :443 — see kafka_rest_endpoint's docstring.
        self.assertEqual(
            setup_mcp.kafka_rest_endpoint("SASL_SSL://pkc-abc12.us-east-1.aws.confluent.cloud:9092"),
            "https://pkc-abc12.us-east-1.aws.confluent.cloud:443",
        )
        self.assertEqual(
            setup_mcp.kafka_rest_endpoint("pkc-abc12.us-east-1.aws.confluent.cloud:9092"),
            "https://pkc-abc12.us-east-1.aws.confluent.cloud:443",
        )
        self.assertEqual(setup_mcp.kafka_rest_endpoint(""), "")

    def test_bootstrap_scheme_is_removed_for_mcp(self):
        self.assertEqual(
            setup_mcp.kafka_bootstrap_servers("SASL_SSL://pkc-abc12.us-east-1.aws.confluent.cloud:9092"),
            "pkc-abc12.us-east-1.aws.confluent.cloud:9092",
        )
        self.assertEqual(
            setup_mcp.kafka_bootstrap_servers("pkc-abc12.us-east-1.aws.confluent.cloud:9092"),
            "pkc-abc12.us-east-1.aws.confluent.cloud:9092",
        )

    def test_cloud_api_keys_default_to_empty(self):
        env = dict(setup_mcp.build_mcp_env(CARD))
        self.assertEqual(env["CONFLUENT_CLOUD_API_KEY"], "")
        self.assertEqual(env["CONFLUENT_CLOUD_API_SECRET"], "")

    def test_missing_card_key_yields_an_empty_value_not_a_crash(self):
        env = dict(setup_mcp.build_mcp_env({"F1_KAFKA_BOOTSTRAP": "host:9092"}))
        self.assertEqual(env["FLINK_API_KEY"], "")


class NodePreflightTests(unittest.TestCase):
    """The version verdict is pure, so it needs no mocking at all."""

    def test_too_old_is_fatal_and_names_a_fix(self):
        fatal, lines = setup_mcp._classify_node("18.20.4", 108)
        self.assertTrue(fatal)
        blob = "\n".join(lines)
        self.assertIn("too old", blob)
        self.assertIn("nvm install 24", blob)
        self.assertIn("brew install node@24", blob)

    def test_no_prebuilt_binary_warns_with_build_tool_guidance(self):
        fatal, lines = setup_mcp._classify_node("26.5.0", 147)
        self.assertFalse(fatal)  # compiling from source may still work
        blob = "\n".join(lines)
        self.assertIn("no prebuilt", blob)
        self.assertIn("@confluentinc/kafka-javascript", blob)
        self.assertIn("xcode-select --install", blob)
        self.assertIn("build-essential", blob)

    def test_preferred_abi_passes_cleanly(self):
        fatal, lines = setup_mcp._classify_node("24.4.0", 137)
        self.assertFalse(fatal)
        self.assertIn("Node 24 LTS", "\n".join(lines))
        self.assertNotIn("Warning", "\n".join(lines))

    def test_other_prebuilt_abi_passes(self):
        fatal, lines = setup_mcp._classify_node("22.11.0", 127)
        self.assertFalse(fatal)
        self.assertIn("prebuilt binary available", "\n".join(lines))

    def test_abi_ranking_prefers_node_24(self):
        self.assertEqual(setup_mcp._abi_score(137), 2)
        self.assertEqual(setup_mcp._abi_score(127), 1)
        self.assertEqual(setup_mcp._abi_score(147), 0)


class ArgvTests(unittest.TestCase):
    """The exact commands handed to each agent CLI."""

    def setUp(self):
        self.dist = Path("/proj/node_modules/@confluentinc/mcp-confluent/dist/index.js")
        self.env = Path("/proj/confluent-mcp.env")

    def test_claude_uses_local_scope_and_the_proven_flag_asymmetry(self):
        remove, add = setup_mcp.claude_argv("/usr/bin/node", self.dist, self.env)
        self.assertEqual(remove, ["claude", "mcp", "remove", "confluent-cloud-mcp-server", "-s", "local"])
        self.assertEqual(
            add,
            [
                "claude",
                "mcp",
                "add",
                "--scope",
                "local",
                "confluent-cloud-mcp-server",
                "--",
                "/usr/bin/node",
                str(self.dist),
                "-e",
                str(self.env),
            ],
        )

    def test_codex_uses_the_documented_stdio_form(self):
        # `codex mcp add [OPTIONS] <NAME> (--url <URL> | -- <COMMAND>...)`
        remove, add = setup_mcp.codex_argv("/usr/bin/node", self.dist, self.env)
        self.assertEqual(remove, ["codex", "mcp", "remove", "confluent-cloud-mcp-server"])
        self.assertEqual(
            add,
            [
                "codex",
                "mcp",
                "add",
                "confluent-cloud-mcp-server",
                "--",
                "/usr/bin/node",
                str(self.dist),
                "-e",
                str(self.env),
            ],
        )
        # Codex has no --scope; asserting its absence keeps a future edit honest.
        self.assertNotIn("--scope", add)


class EndToEndTests(unittest.TestCase):
    """main() against a fabricated card in a temp root, with every exec recorded."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        # A single card under runs/ — resolve_card's "only card lying around" path.
        self.card = self.root / "runs" / "solo" / "credentials" / "f1wp007.env"
        self.card.parent.mkdir(parents=True)
        self.card.write_text(CARD_BODY)

        # Pretend the package is already installed, so npm is never reached.
        self.dist = self.root / "node_modules" / "@confluentinc" / "mcp-confluent" / "dist" / "index.js"
        self.dist.parent.mkdir(parents=True)
        self.dist.write_text("// stub\n")

        patcher = patch.dict("os.environ", {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, argv: list[str], fake: FakeRun | None = None) -> FakeRun:
        fake = fake or FakeRun()
        with (
            patch.object(setup_mcp, "_run", fake),
            patch.object(setup_mcp, "get_project_root", return_value=self.root),
            patch.object(setup_mcp, "_find_preferred_node", return_value="node"),
            patch("sys.argv", ["setup-mcp", *argv]),
        ):
            setup_mcp.main()
        return fake

    def env_file(self) -> str:
        return (self.root / "confluent-mcp.env").read_text()

    def test_client_claude_registers_claude_only_from_the_resolved_card(self):
        fake = self.run_main(["--client", "claude"])

        agent = fake.agent_calls()
        self.assertEqual([c[0] for c in agent], ["claude", "claude"])
        self.assertEqual(agent[0][2], "remove")
        self.assertEqual(agent[1][2], "add")
        # The launch command points at the local install and our env file.
        self.assertIn(str(self.dist.resolve()), agent[1])
        self.assertIn(str((self.root / "confluent-mcp.env").resolve()), agent[1])

    def test_successful_registration_tells_the_user_to_restart_the_agent(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.run_main(["--client", "claude"])

        self.assertIn("Restart your coding agent to pick the server up.", output.getvalue())

    def test_generated_env_carries_the_cards_values(self):
        self.run_main(["--client", "claude"])
        body = self.env_file()
        self.assertIn('BOOTSTRAP_SERVERS="pkc-abc12.us-east-1.aws.confluent.cloud:9092"', body)
        self.assertIn('KAFKA_REST_ENDPOINT="https://pkc-abc12.us-east-1.aws.confluent.cloud:443"', body)
        self.assertIn('FLINK_COMPUTE_POOL_ID="lfcp-pool7"', body)
        self.assertIn('FLINK_CATALOG_NAME="RIVER-RACING-f1wp007-ENV"', body)
        self.assertIn('SCHEMA_REGISTRY_API_KEY="SRKEY7"', body)

    def test_env_file_is_owner_only(self):
        self.run_main(["--client", "claude"])
        self.assertEqual((self.root / "confluent-mcp.env").stat().st_mode & 0o777, 0o600)

    def test_cloud_api_keys_come_from_credentials_env_tf_vars(self):
        (self.root / "credentials.env").write_text(
            "TF_VAR_confluent_cloud_api_key=CLOUDKEY\n"
            "TF_VAR_confluent_cloud_api_secret=CLOUDSECRET\n"
            f"F1_CARD=runs/solo/credentials/{self.card.name}\n"
        )
        self.run_main(["--client", "claude"])
        self.assertIn('CONFLUENT_CLOUD_API_KEY="CLOUDKEY"', self.env_file())
        self.assertIn('CONFLUENT_CLOUD_API_SECRET="CLOUDSECRET"', self.env_file())

    def test_absent_credentials_env_is_not_an_error(self):
        self.run_main(["--client", "claude"])  # no credentials.env in the temp root at all
        self.assertIn('CONFLUENT_CLOUD_API_KEY=""', self.env_file())

    def test_rerun_is_idempotent(self):
        first = self.run_main(["--client", "claude"])
        body_after_first = self.env_file()

        second = self.run_main(["--client", "claude"])

        # Byte-identical env file: written whole, never appended.
        self.assertEqual(body_after_first, self.env_file())
        # Same commands, same order, and remove still precedes add.
        self.assertEqual(first.agent_calls(), second.agent_calls())
        self.assertEqual([c[2] for c in second.agent_calls()], ["remove", "add"])
        # The install short-circuited, so npm never ran (FakeRun asserts this too).
        self.assertNotIn("npm", [c[0] for c in second.calls])

    def test_explicit_creds_override_wins(self):
        other = self.root / "elsewhere" / "f1wp042.env"
        other.parent.mkdir()
        other.write_text(CARD_BODY.replace("lfcp-pool7", "lfcp-pool42"))

        self.run_main(["--creds", str(other)])
        self.assertIn('FLINK_COMPUTE_POOL_ID="lfcp-pool42"', self.env_file())

    def test_client_codex_registers_codex_only(self):
        fake = self.run_main(["--client", "codex"])
        self.assertEqual([c[0] for c in fake.agent_calls()], ["codex", "codex"])

    def test_client_both_registers_each_agent_once(self):
        fake = self.run_main(["--client", "both"])
        agent = fake.agent_calls()
        self.assertEqual([c[0] for c in agent], ["claude", "claude", "codex", "codex"])
        self.assertEqual([c[2] for c in agent], ["remove", "add", "remove", "add"])

    def test_dry_run_writes_the_env_file_but_calls_no_agent(self):
        fake = self.run_main(["--client", "both", "--dry-run"])
        self.assertEqual(fake.agent_calls(), [])
        self.assertIn('FLINK_ORG_ID="org-abc7"', self.env_file())

    def test_failed_registration_exits_nonzero(self):
        with self.assertRaises(SystemExit) as ctx:
            self.run_main(["--client", "claude"], fake=FakeRun(add_returncode=1))
        self.assertNotEqual(ctx.exception.code, 0)

    def test_missing_agent_cli_exits_nonzero_without_crashing(self):
        class NoCli(FakeRun):
            def __call__(self, argv, **kwargs):
                if argv[0] in ("claude", "codex"):
                    raise FileNotFoundError(argv[0])
                return super().__call__(argv, **kwargs)

        with self.assertRaises(SystemExit) as ctx:
            self.run_main(["--client", "claude"], fake=NoCli())
        self.assertNotEqual(ctx.exception.code, 0)

    def test_old_node_aborts_before_anything_is_written(self):
        with self.assertRaises(SystemExit):
            self.run_main(["--client", "claude"], fake=FakeRun(node_version="18.20.4", node_abi=108))
        self.assertFalse((self.root / "confluent-mcp.env").exists())

    def test_ambiguous_cards_refuse_to_guess(self):
        second = self.root / "runs" / "other" / "credentials" / "f1wp008.env"
        second.parent.mkdir(parents=True)
        second.write_text(CARD_BODY)

        with self.assertRaises(SystemExit) as ctx:
            self.run_main(["--client", "claude"])
        self.assertIn("Multiple credential cards", str(ctx.exception))

    def test_omitted_client_prompts_and_defaults_to_claude_on_enter(self):
        with patch.object(setup_mcp, "input", return_value="", create=True):
            fake = self.run_main([])
        self.assertEqual([c[0] for c in fake.agent_calls()], ["claude", "claude"])

    def test_omitted_client_prompt_choice_2_selects_codex(self):
        with patch.object(setup_mcp, "input", return_value="2", create=True):
            fake = self.run_main([])
        self.assertEqual([c[0] for c in fake.agent_calls()], ["codex", "codex"])

    def test_omitted_client_prompt_choice_both_selects_both(self):
        with patch.object(setup_mcp, "input", return_value="both", create=True):
            fake = self.run_main([])
        self.assertEqual([c[0] for c in fake.agent_calls()], ["claude", "claude", "codex", "codex"])

    def test_omitted_client_prompt_falls_back_on_eof(self):
        with patch.object(setup_mcp, "input", side_effect=EOFError, create=True):
            fake = self.run_main([])
        self.assertEqual([c[0] for c in fake.agent_calls()], ["claude", "claude"])

    def test_explicit_client_flag_skips_the_prompt(self):
        with patch.object(setup_mcp, "input", side_effect=AssertionError("should not prompt"), create=True):
            fake = self.run_main(["--client", "codex"])
        self.assertEqual([c[0] for c in fake.agent_calls()], ["codex", "codex"])

    def test_incomplete_card_warns_and_still_writes(self):
        self.card.write_text("F1_KAFKA_API_KEY=KKEY7\n")
        missing = setup_mcp.warn_on_empty_card_fields({"F1_KAFKA_API_KEY": "KKEY7"})
        self.assertIn("F1_KAFKA_BOOTSTRAP", missing)

        self.run_main(["--client", "claude"])
        self.assertIn('KAFKA_API_KEY="KKEY7"', self.env_file())


if __name__ == "__main__":
    unittest.main()
