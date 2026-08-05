"""
Start (or synchronously restart) every attendee's race simulator.

Scales each attendee simulator ECS service to the desired count (default 1),
so all attendee clusters begin feeding car_telemetry + race_standings at once.
The simulator runs with RACE_LOOP=true, so once started it replays races
back-to-back for the whole workshop.

For a hard synchronized restart, run the stop command first, then this.

Usage:
  uv run workshop start-races
  uv run workshop start-races --region us-west-2 --filter river-racing --count 1

"""

from __future__ import annotations

import argparse

from scripts.instructor._common import add_fleet_arguments, scale_all_services

DESCRIPTION = "Start every attendee race simulator (organizer fan-out)."


def add_arguments(p: argparse.ArgumentParser) -> None:
    add_fleet_arguments(p)
    p.add_argument("--count", type=int, default=1, help="Desired task count per service (default: 1)")


def start_races(args: argparse.Namespace) -> None:
    print("=== Start all attendee races ===\n")
    updated = scale_all_services(args.region, args.filter, args.count)
    print(f"\nDone — {updated} service(s) scaled to {args.count}.")
