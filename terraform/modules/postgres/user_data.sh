#!/bin/bash
set -e

yum update -y
yum install -y docker
systemctl start docker
systemctl enable docker

# Init SQL — content injected by terraform templatefile() from data/race_results_seed.sql
# Gzip+base64 encoded to fit within EC2 user_data's 16KB limit.
mkdir -p /opt/postgres-init

echo "${race_results_seed_b64}" | base64 -d | gunzip > /opt/postgres-init/01_race_results.sql

# Start Postgres with CDC-ready config
docker run -d \
  --name postgres \
  -p 5432:5432 \
  -e POSTGRES_DB=f1demo \
  -e POSTGRES_USER=f1user \
  -e POSTGRES_PASSWORD=f1passw0rd \
  -v /opt/postgres-init:/docker-entrypoint-initdb.d \
  postgres:15 \
  -c wal_level=logical \
  -c max_replication_slots=5 \
  -c max_wal_senders=5
