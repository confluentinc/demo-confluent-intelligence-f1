from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from scripts.workshop import reset


def test_reset_cards_uses_at_most_eight_workers_and_preserves_order():
    seen_workers = []

    class RecordingExecutor(ThreadPoolExecutor):
        def __init__(self, max_workers, *args, **kwargs):
            seen_workers.append(max_workers)
            super().__init__(max_workers, *args, **kwargs)

    cards = [f"/cards/f1wp{number:03d}.env" for number in range(1, 21)]

    def reset_card(path, keep_source):
        assert keep_source is False
        return path.removesuffix(".env").rsplit("/", 1)[-1], []

    with (
        patch.object(reset, "ProcessPoolExecutor", RecordingExecutor),
        patch.object(reset, "_reset_card", side_effect=reset_card),
    ):
        results = reset._reset_cards(cards, keep_source=False)

    assert seen_workers == [8]
    assert [index for index, _, _ in results] == list(range(1, 21))


def test_reset_cards_turns_worker_exception_into_account_problem():
    with (
        patch.object(reset, "ProcessPoolExecutor", ThreadPoolExecutor),
        patch.object(reset, "_reset_card", side_effect=RuntimeError("boom")),
    ):
        results = reset._reset_cards(["/cards/f1wp050.env"], keep_source=True)

    assert results == [(1, "f1wp050", ["unexpected reset failure (boom)"])]
