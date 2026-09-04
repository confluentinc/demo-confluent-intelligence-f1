"""Exercise generated requests and importing the existing workshop MCP command."""

import base64
import json
import os
import subprocess
from unittest.mock import patch

import pytest

from scripts import setup_rtce
from scripts.workshop.onboard import _parse_pasted_email, _parse_rtce_command

ENDPOINT = (
    'https://mcp.eu-west-1.aws.confluent.cloud/mcp/v1/context-engine/'
    'organizations/org-test/environments/env-test/kafka-clusters/lkc-test'
)
CARD = {
    'F1_RTCE_MCP_ENDPOINT': ENDPOINT,
    'F1_ENVIRONMENT_ID': 'env-test',
    'F1_CLUSTER_ID': 'lkc-test',
    'F1_RTCE_API_KEY': 'test-key',
    'F1_RTCE_API_SECRET': 'test-secret',
}


def test_generated_curl_executes_with_correct_request():
    # Shell quoting must survive apostrophes and command substitutions as data.
    card = dict(CARD, F1_ENVIRONMENT_ID="env-'$(exit 97)")
    command = setup_rtce.lightning_command(card)
    output = subprocess.check_output([
        'bash', '-c', 'curl() { printf "%s\\0" "$@"; };\n' + command,
    ])
    args = output.decode().rstrip('\0').split('\0')
    assert 'https://sql.eu-west-1.aws.confluent.cloud/query/v1alpha1' in args
    payload = json.loads(args[args.index('-d') + 1])
    assert payload['catalog_name'] == card['F1_ENVIRONMENT_ID']
    assert payload['database_name'] == 'lkc-test'
    assert payload['query'].endswith('ORDER BY lap DESC LIMIT 10')
    token = setup_rtce.basic_token('test-key', 'test-secret')
    assert f'Authorization: Basic {token}' in args


def test_lightning_cli_prints_only_curl_without_registering(capsys):
    with (
        patch('sys.argv', ['setup-rtce', '--lightning']),
        patch.dict(os.environ, {}, clear=True),
        patch.object(setup_rtce, 'load_card', return_value=('test.env', CARD)),
        patch.object(setup_rtce, '_prompt_for_clients', side_effect=AssertionError),
        patch.object(setup_rtce, 'register_claude', side_effect=AssertionError),
        patch.object(setup_rtce, 'write_codex_config', side_effect=AssertionError),
    ):
        setup_rtce.main()
    assert capsys.readouterr().out == setup_rtce.lightning_command(CARD) + '\n'


def test_standalone_environment_credentials(capsys):
    card = {k: v for k, v in CARD.items() if k not in ('F1_RTCE_API_KEY', 'F1_RTCE_API_SECRET')}
    with (
        patch('sys.argv', ['setup-rtce', '--lightning']),
        patch.dict(os.environ, {'RTCE_API_KEY': 'global-key', 'RTCE_API_SECRET': 'global-secret'}, clear=True),
        patch.object(setup_rtce, 'load_card', return_value=('test.env', card)),
    ):
        setup_rtce.main()
    assert setup_rtce.basic_token('global-key', 'global-secret') in capsys.readouterr().out


@pytest.mark.parametrize('field', list(CARD))
def test_missing_fields_fail_without_printing_secrets(field):
    card = dict(CARD)
    del card[field]
    with pytest.raises(ValueError) as error:
        setup_rtce.lightning_command(card)
    assert 'test-secret' not in str(error.value)


def test_rejects_non_confluent_endpoint():
    with pytest.raises(ValueError):
        setup_rtce.lightning_command(dict(CARD, F1_RTCE_MCP_ENDPOINT='https://example.com'))


def test_claim_email_reuses_existing_mcp_credentials():
    token = base64.b64encode(b'test-key:test-secret').decode()
    email = (
        f'Real-Time Context Engine / MCP Setup Command: claude mcp add --transport http '
        f'real-time-context-engine {ENDPOINT} --header "Authorization: Basic {token}"\n'
        'Confluent Cloud / Environment ID: env-test\n'
    )
    values = _parse_pasted_email(email)
    assert values['environment_id'] == 'env-test'
    assert values['rtce_api_key'] == 'test-key'
    assert values['rtce_api_secret'] == 'test-secret'
    assert values['rtce_mcp_endpoint'] == ENDPOINT


@pytest.mark.parametrize('token', ['not-base64', '%%%%', 'bm9jb2xvbg==', 'a2V5OnNlY3JldAo='])
def test_invalid_claim_tokens_are_ignored(token):
    assert _parse_rtce_command(f'{ENDPOINT} --header "Authorization: Basic {token}"') == {}


def test_onboarding_writes_imported_rtce_credentials(tmp_path):
    from dotenv import dotenv_values

    from scripts.workshop import onboard

    values = {key: 'test-value' for key, _ in onboard.FIELDS}
    values.update(
        email='test@example.com', rtce_mcp_endpoint=ENDPOINT,
        rtce_api_key='test-key', rtce_api_secret='test-secret',
    )
    destination = tmp_path / 'credentials.env'
    with (
        patch('sys.argv', ['f1-onboard', '--out', str(destination)]),
        patch.object(onboard, '_prompt_fields', return_value=values),
        patch.object(onboard.creds_mod, '_mint_rtce_key', side_effect=AssertionError),
    ):
        onboard.main()
    written = dotenv_values(destination)
    assert written['F1_RTCE_MCP_ENDPOINT'] == ENDPOINT
    assert written['F1_RTCE_API_KEY'] == 'test-key'
    assert written['F1_RTCE_API_SECRET'] == 'test-secret'


def test_terraform_outputs_only_match_selected_deployment(tmp_path):
    state = tmp_path / 'terraform/aws/terraform.tfstate'
    state.parent.mkdir(parents=True)
    state.touch()
    outputs = {key: {'value': value} for key, value in {
        'environment_id': 'env-test', 'cluster_id': 'lkc-test',
        'rtce_api_key': 'terraform-key', 'rtce_api_secret': 'terraform-secret',
    }.items()}
    result = subprocess.CompletedProcess([], 0, json.dumps(outputs))
    with patch.object(setup_rtce, 'get_project_root', return_value=tmp_path), patch.object(
        setup_rtce.subprocess, 'run', return_value=result,
    ):
        assert setup_rtce.terraform_rtce_outputs(CARD)['rtce_api_key'] == 'terraform-key'
        assert setup_rtce.terraform_rtce_outputs(dict(CARD, F1_CLUSTER_ID='other')) == {}


def test_terraform_pair_precedes_saved_pair():
    with patch.dict(os.environ, {}, clear=True), patch.object(
        setup_rtce, 'terraform_rtce_outputs', return_value={'rtce_api_key': 'tf-key', 'rtce_api_secret': 'tf-secret'},
    ):
        result = setup_rtce.resolve_rtce_credentials(CARD)
    assert result['F1_RTCE_API_KEY'] == 'tf-key'


def test_explicit_environment_pair_precedes_terraform():
    with patch.dict(os.environ, {'RTCE_API_KEY': 'env-key', 'RTCE_API_SECRET': 'env-secret'}, clear=True), patch.object(
        setup_rtce, 'terraform_rtce_outputs', side_effect=AssertionError,
    ):
        assert setup_rtce.resolve_rtce_credentials(CARD)['F1_RTCE_API_KEY'] == 'env-key'


def test_cli_fallback_saves_pair_without_manual_prompt(tmp_path):
    from dotenv import dotenv_values

    path = tmp_path / 'card.env'
    path.write_text('F1_CLUSTER_ID=lkc-test\n')
    card = dict(CARD, F1_RTCE_API_KEY='', F1_RTCE_API_SECRET='')
    with (
        patch.dict(os.environ, {}, clear=True),
        patch.object(setup_rtce, 'terraform_rtce_outputs', return_value={'service_account_id': 'sa-test'}),
        patch('sys.stdin.isatty', return_value=True),
        patch.object(setup_rtce, 'offer_cli_key', return_value=('new-key', 'new-secret')) as offer,
        patch.object(setup_rtce.getpass, 'getpass', side_effect=AssertionError),
    ):
        setup_rtce.resolve_rtce_credentials(card, path)
    offer.assert_called_once_with('sa-test')
    assert dotenv_values(path)['F1_RTCE_API_SECRET'] == 'new-secret'
    assert dotenv_values(path)['F1_CLUSTER_ID'] == 'lkc-test'


def test_manual_fallback_after_cli_declined(capsys):
    with (
        patch.dict(os.environ, {}, clear=True),
        patch.object(setup_rtce, 'terraform_rtce_outputs', return_value={}),
        patch('sys.stdin.isatty', return_value=True),
        patch.object(setup_rtce, 'offer_cli_key', return_value=('', '')),
        patch.object(setup_rtce.getpass, 'getpass', side_effect=['manual-key', 'manual-secret']),
    ):
        result = setup_rtce.resolve_rtce_credentials(dict(CARD, F1_RTCE_API_KEY=''))
    assert result['F1_RTCE_API_SECRET'] == 'manual-secret'
    assert setup_rtce._KEY_HELP in capsys.readouterr().err


def test_cli_creation_is_explicit_and_never_deletes_keys():
    with patch('builtins.input', return_value='n'), patch.object(setup_rtce.subprocess, 'run') as run:
        assert setup_rtce.offer_cli_key('sa-test') == ('', '')
        run.assert_not_called()
    with patch('builtins.input', return_value='y'), patch.object(
        setup_rtce.subprocess, 'run', return_value=subprocess.CompletedProcess(
            [], 0, '{"api_key":"new-key","api_secret":"new-secret"}',
        ),
    ) as run:
        assert setup_rtce.offer_cli_key('sa-test') == ('new-key', 'new-secret')
        assert run.call_count == 1
        assert run.call_args.args[0][:5] == ['confluent', 'api-key', 'create', '--resource', 'global']
        assert run.call_args.args[0][-2:] == ['--service-account', 'sa-test']


def test_noninteractive_missing_keys_fail_without_prompt():
    with (
        patch.dict(os.environ, {}, clear=True),
        patch.object(setup_rtce, 'terraform_rtce_outputs', return_value={}),
        patch('sys.stdin.isatty', return_value=False),
        patch.object(setup_rtce, 'offer_cli_key', side_effect=AssertionError),
        pytest.raises(SystemExit, match='No interactive terminal'),
    ):
        setup_rtce.resolve_rtce_credentials(dict(CARD, F1_RTCE_API_KEY=''))
