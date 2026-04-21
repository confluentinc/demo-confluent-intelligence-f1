# Avro Serialization Migration for Car Telemetry Producer

**Date:** 2026-04-20
**Status:** Approved
**Scope:** Switch the race simulator's Kafka producer from raw JSON to Avro serialization using Schema Registry

---

## Problem

The `car-telemetry` topic is created via Flink `CREATE TABLE`, which auto-registers an Avro schema in Schema Registry. The race simulator currently produces raw JSON bytes (`json.dumps().encode()`), which will fail deserialization when Flink reads the topic expecting Avro-encoded messages.

## Solution

Use `confluent-kafka`'s `AvroSerializer` with `use.latest.version=True` to serialize messages using the schema Flink already registered. No local schema definition needed — the serializer fetches it from Schema Registry at runtime.

---

## Changes

### 1. Terraform: Schema Registry API Key

**File:** `modules/cluster/main.tf`

Add a `confluent_api_key` resource scoped to Schema Registry for the existing `f1-demo-app` service account. No new role bindings needed — `EnvironmentAdmin` already covers Schema Registry access.

```terraform
resource "confluent_api_key" "schema_registry" {
  display_name = "f1-demo-sr-key"

  owner {
    id          = confluent_service_account.app.id
    api_version = confluent_service_account.app.api_version
    kind        = confluent_service_account.app.kind
  }

  managed_resource {
    id          = var.schema_registry_id
    api_version = var.schema_registry_api_version
    kind        = var.schema_registry_kind

    environment {
      id = var.environment_id
    }
  }
}
```

Requires 3 new variables passed from the environment module: `schema_registry_id`, `schema_registry_api_version`, `schema_registry_kind`.

**File:** `modules/cluster/outputs.tf`

Add outputs:
- `sr_api_key` (sensitive)
- `sr_api_secret` (sensitive)

**File:** `modules/cluster/variables.tf`

Add variables: `schema_registry_id`, `schema_registry_api_version`, `schema_registry_kind`.

### 2. Terraform: Wire Schema Registry URL + Credentials to EC2

**File:** `modules/environment/outputs.tf`

Add output: `schema_registry_rest_endpoint` from `data.confluent_schema_registry_cluster.main.rest_endpoint`.

**File:** Root `main.tf`

Pass to cluster module:
- `schema_registry_id = module.environment.schema_registry_id`
- `schema_registry_api_version` and `schema_registry_kind` from new environment outputs

Pass to MQ module:
- `sr_url = module.environment.schema_registry_rest_endpoint`
- `sr_api_key = module.cluster.sr_api_key`
- `sr_api_secret = module.cluster.sr_api_secret`

**File:** `modules/mq/variables.tf`

Add 3 new variables: `sr_url`, `sr_api_key`, `sr_api_secret`.

**File:** `modules/mq/user_data.sh`

Add to the `.env` block:
```
SR_URL=${sr_url}
SR_API_KEY=${sr_api_key}
SR_API_SECRET=${sr_api_secret}
```

Update the `templatefile()` call in `modules/mq/main.tf` to pass these 3 new variables.

### 3. Simulator: Config

**File:** `datagen/config.py`

Add:
```python
SR_URL = os.environ.get("SR_URL", "")
SR_API_KEY = os.environ.get("SR_API_KEY", "")
SR_API_SECRET = os.environ.get("SR_API_SECRET", "")
```

### 4. Simulator: Avro Producer

**File:** `datagen/simulator.py`

Replace the `_create_kafka_producer()` function to also return an `AvroSerializer`:

```python
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import SerializationContext, MessageField

def _create_kafka_producer():
    sr_client = SchemaRegistryClient({
        'url': config.SR_URL,
        'basic.auth.user.info': f'{config.SR_API_KEY}:{config.SR_API_SECRET}',
    })

    avro_serializer = AvroSerializer(
        sr_client,
        schema_str=None,
        conf={
            'auto.register.schemas': False,
            'use.latest.version': True,
        },
    )

    producer = Producer({
        'bootstrap.servers': config.KAFKA_BOOTSTRAP,
        'security.protocol': 'SASL_SSL',
        'sasl.mechanisms': 'PLAIN',
        'sasl.username': config.KAFKA_API_KEY,
        'sasl.password': config.KAFKA_API_SECRET,
    })

    return producer, avro_serializer
```

Update the produce call in `run_race()`:

```python
# Before (raw JSON):
producer.produce(topic, key=..., value=json.dumps(telemetry).encode("utf-8"))

# After (Avro):
producer.produce(
    topic,
    key=str(config.OUR_CAR_NUMBER).encode("utf-8"),
    value=avro_serializer(
        telemetry,
        SerializationContext(config.KAFKA_TOPIC, MessageField.VALUE),
    ),
)
```

**`event_time` handling:** Convert from ISO string to epoch milliseconds (int) before serialization, since Avro's `timestamp-millis` logical type expects a `long`:

```python
telemetry["event_time"] = int(datetime.now(timezone.utc).timestamp() * 1000)
```

### 5. Dependencies

**File:** `datagen/requirements.txt`

Change `confluent-kafka` to `confluent-kafka[avro]` (pulls in `fastavro` and `requests`).

---

## What Does NOT Change

- No new RBAC role bindings — `EnvironmentAdmin` covers Schema Registry access
- No ACLs needed — RBAC alone is sufficient on Standard clusters
- No local Avro schema definition — `use.latest.version=True` fetches from Schema Registry
- No changes to `telemetry.py`, `race_script.py`, `drivers.py`
- MQ producer (race standings) stays as raw JSON — MQ doesn't use Schema Registry

---

## Risk: Flink Schema Nullability

Flink's CREATE TABLE may register fields as nullable unions (`["null", "type"]`). All fields in the `car-telemetry` CREATE TABLE are defined as non-null, so this should not be an issue. If it is, the serializer will fail with a clear error at produce time, and we fix the dict values to match.
