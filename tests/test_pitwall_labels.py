"""Pit Wall section labels: 'steps' for every solo-track card, 'labs' for hosted.

The locked panels name the section that unlocks them. The solo walkthroughs
(SELF-SERVICE.md, STANDALONE-DEMO.md) number theirs as steps, the hosted one as
labs. `_labels_for_card` used to list only self-service and standalone, so a
pre-provisioned card — whose attendee follows SELF-SERVICE.md — said "Lab 3".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.common import deployment_meta as meta
from scripts.pitwall import app


@pytest.fixture
def root(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(app, "PROJECT_ROOT", tmp_path)
    return tmp_path


def _card(root: Path, run: str) -> Path:
    path = root / "runs" / run / "credentials" / "abc.env"
    path.parent.mkdir(parents=True)
    path.write_text("")
    return path


@pytest.mark.parametrize("track", list(meta.TRACKS.values()), ids=list(meta.TRACKS))
def test_every_solo_track_card_gets_steps(root: Path, track: meta.Track) -> None:
    assert app._labels_for_card(_card(root, track.name)) == "steps"


def test_pre_provisioned_card_gets_steps(root: Path) -> None:
    """Named explicitly so it can't vanish if the registry is ever reshaped."""
    assert app._labels_for_card(_card(root, meta.PREPROVISIONED.name)) == "steps"


def test_hosted_workshop_card_gets_labs(root: Path) -> None:
    assert app._labels_for_card(_card(root, "acme-workshop")) == "labs"


def test_card_outside_runs_gets_labs(root: Path) -> None:
    """A pasted dispenser Env File block saved anywhere else."""
    path = root / "credentials.env"
    path.write_text("")
    assert app._labels_for_card(path) == "labs"
