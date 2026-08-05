"""
Stop every attendee's race simulator.

Scales each attendee simulator ECS service to 0 tasks, halting all attendee
feeds at once (e.g. during a break, or before a synchronized restart). The ECS
services and task definitions remain, so the start command brings them back
without re-provisioning.

Usage:
  uv run workshop stop-races
  uv run workshop stop-races --region us-west-2 --filter river-racing

"""

from __future__ import annotations

import argparse

from scripts.instructor._common import add_fleet_arguments, scale_all_services

DESCRIPTION = "Stop every attendee race simulator (organizer fan-out)."


def add_arguments(p: argparse.ArgumentParser) -> None:
    add_fleet_arguments(p)


def stop_races(args: argparse.Namespace) -> None:
    print("=== Stop all attendee races ===\n")
    updated = scale_all_services(args.region, args.filter, 0)
    print(f"\nDone — {updated} service(s) scaled to 0.")
