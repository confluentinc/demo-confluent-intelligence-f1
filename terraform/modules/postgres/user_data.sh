#!/bin/bash
set -e

yum update -y
yum install -y docker
systemctl start docker
systemctl enable docker

# Init SQL — content injected by terraform templatefile() from data/driver_race_history_seed.sql
# Gzip+base64 encoded to fit within EC2 user_data's 16KB limit.
mkdir -p /opt/postgres-init

echo "${driver_race_history_seed_b64}" | base64 -d | gunzip > /opt/postgres-init/01_driver_race_history.sql

# Start Postgres with CDC-ready config.
#
# `--restart unless-stopped` is load-bearing, not hygiene. cloud-init runs
# user_data once per instance *id*, so it is skipped entirely on a stop/start.
# The normal build path keeps this template stable: replication capacity is a
# fixed 105 slots, and password rotations explicitly replace the instance.
# Any operator changing a boot-time setting must also explicitly replace the
# instance (see POSTGRES-PASSWORD-MIGRATION.md). Without a restart policy Docker
# comes back on boot and this container does not, leaving every attendee's CDC
# connector failing with "Connection to <host>:5432 refused" and no way in (no
# SSH key, no SSM role) to `docker start` it by hand.
#
# The database lives in the container's writable layer (only /opt/postgres-init
# is bind-mounted), so this preserves the container across reboots but a
# container *replacement* still reseeds from scratch. That is fine here: the
# 198-row driver_race_history seed is the only data, and it is reproducible.
docker run -d \
  --name postgres \
  --restart unless-stopped \
  -p 5432:5432 \
  -e POSTGRES_DB=f1demo \
  -e POSTGRES_USER=f1user \
  -e POSTGRES_PASSWORD=${postgres_password} \
  -v /opt/postgres-init:/docker-entrypoint-initdb.d \
  postgres:15 \
  -c wal_level=logical \
  -c max_replication_slots=${max_replication_slots} \
  -c max_wal_senders=${max_replication_slots} \
  -c max_connections=500
