# DBT-Confluent Adapter Setup Guide

## Prerequisites

- Python 3.9+
- Confluent Cloud environment with Flink compute pool
- Flink API key and secret

## Install

```bash
pip install dbt-confluent
```

## Configure profiles.yml

```yaml
f1_demo:
  target: dev
  outputs:
    dev:
      type: confluent
      catalog: prod-f1-env           # Confluent environment name
      schema: prod-f1-cluster        # Kafka cluster name
      threads: 1
      execution_mode: streaming_query
      flink_api_key: "{{ env_var('FLINK_API_KEY') }}"
      flink_api_secret: "{{ env_var('FLINK_API_SECRET') }}"
      flink_rest_endpoint: "{{ env_var('FLINK_REST_ENDPOINT') }}"
      flink_compute_pool_id: "{{ env_var('FLINK_COMPUTE_POOL_ID') }}"
      flink_organization_id: "{{ env_var('FLINK_ORGANIZATION_ID') }}"
      flink_environment_id: "{{ env_var('FLINK_ENVIRONMENT_ID') }}"
```

## Key Concepts

| DBT Concept | Flink Equivalent |
|---|---|
| database | Flink catalog (= environment name) |
| schema | Flink database (= cluster name) |
| materialization | `streaming_table` for continuous jobs |

## Usage

```bash
# Set environment variables
export FLINK_API_KEY="..."
export FLINK_API_SECRET="..."
export FLINK_REST_ENDPOINT="..."
export FLINK_COMPUTE_POOL_ID="..."
export FLINK_ORGANIZATION_ID="..."
export FLINK_ENVIRONMENT_ID="..."

# Run the enrichment model
dbt run --select enrichment_anomaly
```

## Notes

- Cannot create schemas — Kafka cluster must exist before `dbt run`
- Use `streaming_table` materialization for continuous Flink jobs
- The enrichment SQL with AI_DETECT_ANOMALIES is in `demo-reference/enrichment_anomaly.sql`
