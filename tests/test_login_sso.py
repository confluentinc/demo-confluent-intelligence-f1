from unittest.mock import patch

from scripts.common import login_checks as lc


def test_saved_login_skips_prompt_when_session_active():
    with patch.object(lc, "check_confluent_login", return_value=True), patch.object(
        lc, "_prompt_and_save_login"
    ) as prompt:
        assert lc.ensure_saved_confluent_login({}, "credentials.env")
    prompt.assert_not_called()


def test_blank_email_runs_sso_login_when_logged_out(tmp_path):
    answers = iter(["", ""])
    with patch.object(lc, "check_confluent_login", return_value=False), patch.object(
        lc, "_sso_login", return_value=True
    ) as sso, patch("builtins.input", lambda _p: next(answers)):
        assert lc._prompt_and_save_login(tmp_path / "credentials.env", {})
    sso.assert_called_once()


def test_blank_email_declining_sso_returns_false(tmp_path):
    answers = iter(["", "n"])
    with patch.object(lc, "check_confluent_login", return_value=False), patch.object(
        lc, "_sso_login"
    ) as sso, patch("builtins.input", lambda _p: next(answers)):
        assert not lc._prompt_and_save_login(tmp_path / "credentials.env", {})
    sso.assert_not_called()
