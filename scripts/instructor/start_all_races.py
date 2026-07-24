"""
Start (or synchronously restart) every attendee's race simulator.

Scales each attendee simulator ECS service to the desired count (default 1),
so all attendee clusters begin feeding car_telemetry + race_standings at once.
The simulator runs with RACE_LOOP=true, so once started it replays races
back-to-back for the whole workshop.

For a hard synchronized restart, run stop-all-races first, then this.

Usage:
  uv run start-all-races
  uv run start-all-races --region us-west-2 --filter river-racing --count 1
"""

import argparse

from scripts.instructor._common import DEFAULT_CLUSTER_FILTER, DEFAULT_REGION, scale_all_services


def main() -> None:
    parser = argparse.ArgumentParser(description="Start all attendee race simulators.")
    parser.add_argument("--region", default=DEFAULT_REGION, help=f"AWS region (default: {DEFAULT_REGION})")
    parser.add_argument(
        "--filter",
        default=DEFAULT_CLUSTER_FILTER,
        help=f"Cluster name substring to match (default: {DEFAULT_CLUSTER_FILTER})",
    )
    parser.add_argument("--count", type=int, default=1, help="Desired task count per service (default: 1)")
    args = parser.parse_args()

    print("=== Start all attendee races ===\n")
    updated = scale_all_services(args.region, args.filter, args.count)
    print(f"\nDone — {updated} service(s) scaled to {args.count}.")


if __name__ == "__main__":
    main()
