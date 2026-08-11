"""
Start a manifest-selected set of attendee race simulators.

The command resolves exact ECS cluster and service names from
``runs/<run-id>/manifest.json``. With no ``--accounts`` it starts the complete
cohort. An explicit selector accepts up to three account numbers or ranges.

Usage:
  uv run workshop start-races --run-id f7zxf
  uv run workshop start-races --run-id f7zxf --accounts 48-50

"""

from __future__ import annotations

import argparse

from scripts.workshop import lifecycle

DESCRIPTION = "Start every attendee race simulator (organizer fan-out)."


def add_arguments(p: argparse.ArgumentParser) -> None:
    lifecycle.add_lifecycle_arguments(p)


def start_races(args: argparse.Namespace) -> None:
    lifecycle.start_races(args)
