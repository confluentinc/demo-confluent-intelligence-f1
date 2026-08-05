"""Shared helpers for instructor fan-out over attendee race simulators.

Each attendee's per-attendee Terraform (`terraform/aws`) creates an ECS cluster
named `<prefix>-<suffix>-simulator` (e.g. `river-racing-f1wp001-3a2b1c4d-simulator`)
running a single looping race-simulator service. These helpers enumerate those
clusters across the shared workshop AWS account and scale their services, so an
instructor can start, stop, or synchronously restart every attendee's live feed
at once.

The entry points are `uv run workshop start-races` / `stop-races` in the
organizer namespace.
"""

from __future__ import annotations

import argparse

import boto3

# Substring every attendee simulator cluster name contains (lower-cased
# `RIVER-RACING-<prefix>` naming from the ecs module).
DEFAULT_CLUSTER_FILTER = "river-racing"
DEFAULT_REGION = "us-east-1"


def add_fleet_arguments(p: argparse.ArgumentParser) -> None:
    """The flags every fan-out command shares: which region, which clusters."""
    p.add_argument("--region", default=DEFAULT_REGION, help=f"AWS region (default: {DEFAULT_REGION})")
    p.add_argument(
        "--filter",
        default=DEFAULT_CLUSTER_FILTER,
        help=f"Cluster name substring to match (default: {DEFAULT_CLUSTER_FILTER})",
    )


def find_simulator_clusters(ecs, name_filter: str) -> list[str]:
    """Return ARNs of ECS clusters whose name matches the workshop filter."""
    clusters: list[str] = []
    paginator = ecs.get_paginator("list_clusters")
    for page in paginator.paginate():
        for arn in page["clusterArns"]:
            name = arn.split("/")[-1]
            if name_filter in name and name.endswith("-simulator"):
                clusters.append(arn)
    return clusters


def scale_all_services(region: str, name_filter: str, desired_count: int) -> int:
    """Set desired_count on every service in every attendee simulator cluster.

    Returns the number of services updated.
    """
    ecs = boto3.client("ecs", region_name=region)
    clusters = find_simulator_clusters(ecs, name_filter)

    if not clusters:
        print(f"No simulator clusters found (filter: '{name_filter}', region: {region}).")
        return 0

    print(f"Found {len(clusters)} attendee simulator cluster(s) in {region}.")
    updated = 0
    for cluster_arn in clusters:
        cluster_name = cluster_arn.split("/")[-1]
        service_arns = ecs.list_services(cluster=cluster_arn).get("serviceArns", [])
        if not service_arns:
            print(f"  {cluster_name}: no services found — skipping")
            continue
        for service_arn in service_arns:
            service_name = service_arn.split("/")[-1]
            ecs.update_service(
                cluster=cluster_arn,
                service=service_name,
                desiredCount=desired_count,
            )
            print(f"  {cluster_name}/{service_name}: desiredCount -> {desired_count}")
            updated += 1
    return updated
