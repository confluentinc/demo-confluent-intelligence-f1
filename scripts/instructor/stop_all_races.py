"""
Stop a manifest-selected set of attendee race simulators.

With no ``--accounts`` the command stops the complete cohort. An explicit
selector accepts up to three account numbers or ranges. Kafka and lab state are
preserved, so the same selection can resume without a reset.

Usage:
  uv run workshop stop-races --run-id f7zxf
  uv run workshop stop-races --run-id f7zxf --accounts 48-50

"""

from __future__ import annotations

import argparse

from scripts.workshop import lifecycle

DESCRIPTION = "Stop every attendee race simulator (organizer fan-out)."


def add_arguments(p: argparse.ArgumentParser) -> None:
    lifecycle.add_lifecycle_arguments(p)


def stop_races(args: argparse.Namespace) -> None:
    lifecycle.stop_races(args)
