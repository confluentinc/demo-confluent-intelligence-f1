"""
Stop every attendee's race simulator.

Scales each attendee simulator ECS service to 0 tasks, halting all attendee
feeds at once (e.g. during a break, or before a synchronized restart). The ECS
services and task definitions remain, so start-all-races brings them back
without re-provisioning.

Usage:
  uv run stop-all-races
  uv run stop-all-races --region us-west-2 --filter river-racing
"""

import argparse

from scripts.instructor._common import DEFAULT_CLUSTER_FILTER, DEFAULT_REGION, scale_all_services


def main() -> None:
    parser = argparse.ArgumentParser(description="Stop all attendee race simulators.")
    parser.add_argument("--region", default=DEFAULT_REGION, help=f"AWS region (default: {DEFAULT_REGION})")
    parser.add_argument(
        "--filter",
        default=DEFAULT_CLUSTER_FILTER,
        help=f"Cluster name substring to match (default: {DEFAULT_CLUSTER_FILTER})",
    )
    args = parser.parse_args()

    print("=== Stop all attendee races ===\n")
    updated = scale_all_services(args.region, args.filter, 0)
    print(f"\nDone — {updated} service(s) scaled to 0.")


if __name__ == "__main__":
    main()
