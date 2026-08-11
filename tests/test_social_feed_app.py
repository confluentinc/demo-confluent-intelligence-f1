import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.social_feed import app


def _args(**overrides):
    values = {
        "public_base_url": "https://small-underpass-refinery.ngrok-free.dev",
        "fixed_prefix": "f1wp050",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_fixed_configuration_requires_one_matching_card() -> None:
    card = (Path("account.env"), {"F1_PREFIX": "f1wp050"})
    assert app._fixed_configuration(_args(), [card]) == (
        "https://small-underpass-refinery.ngrok-free.dev",
        "f1wp050",
    )

    with pytest.raises(SystemExit, match="exactly one"):
        app._fixed_configuration(_args(), [card, card])
    with pytest.raises(SystemExit, match="does not match"):
        app._fixed_configuration(_args(fixed_prefix="f1wp049"), [card])


def test_load_cards_rejects_missing_values_without_printing_secrets(tmp_path) -> None:
    card = tmp_path / "f1wp050.env"
    card.write_text("F1_KAFKA_API_SECRET=do-not-print\n")
    args = argparse.Namespace(creds=str(card), creds_glob="")

    with pytest.raises(SystemExit) as caught:
        app._load_cards(args)

    message = str(caught.value)
    assert "F1_KAFKA_BOOTSTRAP" in message
    assert "do-not-print" not in message


def test_failed_preflight_stops_before_http_server() -> None:
    card = (Path("account.env"), {"F1_PREFIX": "f1wp050"})
    argv = [
        "f1-social-feed",
        "--creds",
        "account.env",
        "--public-base-url",
        "https://small-underpass-refinery.ngrok-free.dev",
        "--fixed-prefix",
        "f1wp050",
    ]
    with (
        patch("sys.argv", argv),
        patch.object(app, "_load_cards", return_value=[card]),
        patch.object(app, "_preflight_card", side_effect=SystemExit("card rejected")),
        patch.object(app.uvicorn, "run") as run,
        pytest.raises(SystemExit, match="card rejected"),
    ):
        app.main()
    run.assert_not_called()
