# Tableflow Setup Guide — F1 Demo

## What Tableflow Does

Continuously materializes Kafka topics as Delta Lake tables in S3. No ETL needed.

## Topics with Tableflow Enabled

| Topic | Why |
|---|---|
| `pit-decisions` | Agent's AI output — the data product for Genie analytics |
| `driver_race_history` | Historical season-to-date race data (198 rows) — fact table for tire-strategy correlation queries |

## Prerequisites (Done by Terraform)

- S3 bucket: `f1-demo-tableflow-<hex>`
- IAM role with cross-account trust policy
- Confluent provider integration

## Enable Tableflow (During Demo)

1. Go to **Confluent Cloud UI** → Environment → Cluster → Topics
2. Select `pit-decisions` topic
3. Click **Tableflow** tab → **Enable**
4. Choose **Delta Lake** format
5. Select **BYOS** (Bring Your Own Storage)
6. Pick the `f1-demo-aws-integration` provider integration
7. Confirm

Repeat for `driver_race_history` topic.

## Databricks Configuration

### 1. Create External Location

In Databricks Unity Catalog:

```sql
CREATE EXTERNAL LOCATION f1_demo_tableflow
  URL 's3://f1-demo-tableflow-<hex>/'
  WITH (STORAGE CREDENTIAL f1_demo_credential);
```

### 2. Create External Table

```sql
CREATE TABLE f1_demo.pit_decisions
  USING DELTA
  LOCATION 's3://f1-demo-tableflow-<hex>/topics/pit-decisions/';

CREATE TABLE f1_demo.driver_race_history
  USING DELTA
  LOCATION 's3://f1-demo-tableflow-<hex>/topics/driver_race_history/';
```

### 3. Verify

```sql
SELECT * FROM f1_demo.pit_decisions LIMIT 10;
SELECT * FROM f1_demo.driver_race_history LIMIT 10;
```
