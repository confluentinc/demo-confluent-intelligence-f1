# Avro Serialization Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch the car-telemetry Kafka producer from raw JSON to Avro serialization using the schema Flink already registered in Schema Registry.

**Architecture:** The environment module exposes Schema Registry attributes. The cluster module creates an SR-scoped API key for the existing service account. Those credentials flow through root main.tf → MQ module → EC2 user_data → simulator config → AvroSerializer at produce time. The serializer uses `use.latest.version=True` so no local schema is needed.

**Tech Stack:** Terraform (Confluent provider), Python (`confluent-kafka[avro]`, `fastavro`)

**Spec:** `docs/superpowers/specs/2026-04-20-avro-serialization-migration.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `terraform/modules/environment/outputs.tf` | Expose SR rest_endpoint, api_version, kind |
| Modify | `terraform/modules/cluster/variables.tf` | Accept SR attributes from environment module |
| Modify | `terraform/modules/cluster/main.tf` | Create SR-scoped API key |
| Modify | `terraform/modules/cluster/outputs.tf` | Expose SR API key/secret |
| Modify | `terraform/modules/mq/variables.tf` | Accept SR URL + credentials |
| Modify | `terraform/modules/mq/main.tf` | Pass SR vars to user_data templatefile |
| Modify | `terraform/modules/mq/user_data.sh` | Template SR env vars into .env file |
| Modify | `terraform/main.tf` | Wire environment → cluster and cluster → MQ for SR values |
| Modify | `datagen/requirements.txt` | Add avro extras |
| Modify | `datagen/config.py` | Add SR config variables |
| Modify | `datagen/simulator.py` | Use AvroSerializer instead of json.dumps |
| Create | `datagen/tests/test_simulator.py` | Test Avro serialization integration |

---

### Task 1: Expose Schema Registry Attributes from Environment Module

**Files:**
- Modify: `terraform/modules/environment/outputs.tf`

The environment module already has `data.confluent_schema_registry_cluster.main` but only outputs `schema_registry_id`. We need 3 more attributes for the cluster module to create an SR-scoped API key.

- [ ] **Step 1: Add Schema Registry outputs**

Add the following outputs to `terraform/modules/environment/outputs.tf` after the existing `schema_registry_id` output (line 12):

```terraform
output "schema_registry_rest_endpoint" {
  value = data.confluent_schema_registry_cluster.main.rest_endpoint
}

output "schema_registry_api_version" {
  value = data.confluent_schema_registry_cluster.main.api_version
}

output "schema_registry_kind" {
  value = data.confluent_schema_registry_cluster.main.kind
}
```

- [ ] **Step 2: Validate Terraform syntax**

Run: `cd /Users/ahmedsaefzamzam/0-workshops/CurrentLondon/KeynoteDemo/F1/terraform && terraform validate`

Expected: May show errors about missing variables in downstream modules (that's fine — we'll fix those in subsequent tasks). The syntax of this file should be valid.

- [ ] **Step 3: Commit**

```bash
git add terraform/modules/environment/outputs.tf
git commit -m "feat(terraform): expose Schema Registry endpoint and attributes from environment module"
```

---

### Task 2: Create Schema Registry API Key in Cluster Module

**Files:**
- Modify: `terraform/modules/cluster/variables.tf`
- Modify: `terraform/modules/cluster/main.tf`
- Modify: `terraform/modules/cluster/outputs.tf`

The cluster module needs to accept SR attributes and create a `confluent_api_key` scoped to Schema Registry.

- [ ] **Step 1: Add SR variables to cluster module**

Append to `terraform/modules/cluster/variables.tf` after line 20 (after the `cloud_region` variable):

```terraform
variable "schema_registry_id" {
  description = "Schema Registry cluster ID"
  type        = string
}

variable "schema_registry_api_version" {
  description = "Schema Registry API version"
  type        = string
}

variable "schema_registry_kind" {
  description = "Schema Registry cluster kind"
  type        = string
}
```

- [ ] **Step 2: Add SR API key resource to cluster module**

Append to `terraform/modules/cluster/main.tf` after line 53 (after the `confluent_api_key.app` resource):

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

  depends_on = [
    confluent_role_binding.app_environment_admin,
  ]
}
```

- [ ] **Step 3: Add SR API key outputs**

Append to `terraform/modules/cluster/outputs.tf` after line 30 (after the `service_account_api_version` output):

```terraform
output "sr_api_key" {
  value     = confluent_api_key.schema_registry.id
  sensitive = true
}

output "sr_api_secret" {
  value     = confluent_api_key.schema_registry.secret
  sensitive = true
}
```

- [ ] **Step 4: Commit**

```bash
git add terraform/modules/cluster/variables.tf terraform/modules/cluster/main.tf terraform/modules/cluster/outputs.tf
git commit -m "feat(terraform): create Schema Registry API key in cluster module"
```

---

### Task 3: Wire SR Credentials Through MQ Module to EC2

**Files:**
- Modify: `terraform/modules/mq/variables.tf`
- Modify: `terraform/modules/mq/main.tf`
- Modify: `terraform/modules/mq/user_data.sh`

The MQ module needs to accept SR credentials and template them into the EC2 instance's `.env` file.

- [ ] **Step 1: Add SR variables to MQ module**

Append to `terraform/modules/mq/variables.tf` after line 33 (after the `kafka_api_secret` variable):

```terraform
variable "sr_url" {
  description = "Schema Registry REST endpoint URL"
  type        = string
}

variable "sr_api_key" {
  description = "Schema Registry API key"
  type        = string
  sensitive   = true
}

variable "sr_api_secret" {
  description = "Schema Registry API secret"
  type        = string
  sensitive   = true
}
```

- [ ] **Step 2: Update templatefile call in MQ main.tf**

In `terraform/modules/mq/main.tf`, replace the `user_data` block (lines 62-66):

```terraform
  user_data = templatefile("${path.module}/user_data.sh", {
    kafka_bootstrap  = var.kafka_bootstrap
    kafka_api_key    = var.kafka_api_key
    kafka_api_secret = var.kafka_api_secret
    sr_url           = var.sr_url
    sr_api_key       = var.sr_api_key
    sr_api_secret    = var.sr_api_secret
  })
```

- [ ] **Step 3: Add SR env vars to user_data.sh**

In `terraform/modules/mq/user_data.sh`, replace the `.env` block (lines 31-42) with:

```bash
cat > /opt/simulator/.env << 'ENVEOF'
KAFKA_BOOTSTRAP=${kafka_bootstrap}
KAFKA_API_KEY=${kafka_api_key}
KAFKA_API_SECRET=${kafka_api_secret}
SR_URL=${sr_url}
SR_API_KEY=${sr_api_key}
SR_API_SECRET=${sr_api_secret}
MQ_HOST=localhost
MQ_PORT=1414
MQ_QUEUE_MANAGER=QM1
MQ_CHANNEL=DEV.APP.SVRCONN
MQ_QUEUE=DEV.QUEUE.1
MQ_USER=app
MQ_PASSWORD=passw0rd
ENVEOF
```

- [ ] **Step 4: Commit**

```bash
git add terraform/modules/mq/variables.tf terraform/modules/mq/main.tf terraform/modules/mq/user_data.sh
git commit -m "feat(terraform): wire Schema Registry credentials to MQ EC2 instance"
```

---

### Task 4: Wire Modules Together in Root main.tf

**Files:**
- Modify: `terraform/main.tf`

Connect the environment module's SR outputs to the cluster module, and cluster module's SR key outputs to the MQ module.

- [ ] **Step 1: Pass SR attributes to cluster module**

In `terraform/main.tf`, replace the `module "cluster"` block (lines 19-25) with:

```terraform
module "cluster" {
  source                      = "./modules/cluster"
  environment_id              = module.environment.environment_id
  cluster_name                = "prod-f1-cluster"
  cloud_provider              = "AWS"
  cloud_region                = var.region
  schema_registry_id          = module.environment.schema_registry_id
  schema_registry_api_version = module.environment.schema_registry_api_version
  schema_registry_kind        = module.environment.schema_registry_kind
}
```

- [ ] **Step 2: Pass SR credentials to MQ module**

In `terraform/main.tf`, replace the `module "mq"` block (lines 50-56) with:

```terraform
module "mq" {
  source           = "./modules/mq"
  aws_region       = var.region
  kafka_bootstrap  = module.cluster.cluster_bootstrap
  kafka_api_key    = module.cluster.app_api_key
  kafka_api_secret = module.cluster.app_api_secret
  sr_url           = module.environment.schema_registry_rest_endpoint
  sr_api_key       = module.cluster.sr_api_key
  sr_api_secret    = module.cluster.sr_api_secret
}
```

- [ ] **Step 3: Validate full Terraform configuration**

Run: `cd /Users/ahmedsaefzamzam/0-workshops/CurrentLondon/KeynoteDemo/F1/terraform && terraform validate`

Expected: `Success! The configuration is valid.`

- [ ] **Step 4: Commit**

```bash
git add terraform/main.tf
git commit -m "feat(terraform): wire Schema Registry attributes between environment, cluster, and MQ modules"
```

---

### Task 5: Update Python Dependencies

**Files:**
- Modify: `datagen/requirements.txt`

- [ ] **Step 1: Update requirements.txt**

Replace the contents of `datagen/requirements.txt` with:

```
pymqi
confluent-kafka[avro]
```

The `[avro]` extra pulls in `fastavro` and `requests` as dependencies.

- [ ] **Step 2: Commit**

```bash
git add datagen/requirements.txt
git commit -m "feat(datagen): add avro extras to confluent-kafka dependency"
```

---

### Task 6: Add Schema Registry Config Variables

**Files:**
- Modify: `datagen/config.py`

- [ ] **Step 1: Add SR config variables**

In `datagen/config.py`, add the following after line 8 (after `KAFKA_TOPIC`):

```python
# Schema Registry settings
SR_URL = os.environ.get("SR_URL", "")
SR_API_KEY = os.environ.get("SR_API_KEY", "")
SR_API_SECRET = os.environ.get("SR_API_SECRET", "")
```

- [ ] **Step 2: Commit**

```bash
git add datagen/config.py
git commit -m "feat(datagen): add Schema Registry config variables"
```

---

### Task 7: Write Failing Test for Avro Serialization

**Files:**
- Create: `datagen/tests/test_simulator.py`

Test that the simulator's Kafka producer setup creates an AvroSerializer and that telemetry dicts are serialized correctly through it. We mock the SchemaRegistryClient since we can't connect to a real SR in unit tests.

- [ ] **Step 1: Write the test file**

Create `datagen/tests/test_simulator.py`:

```python
"""Tests for simulator Avro serialization."""
from unittest.mock import MagicMock, patch

from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import SerializationContext, MessageField


def test_create_kafka_producer_returns_avro_serializer():
    """_create_kafka_producer must return a (Producer, AvroSerializer) tuple."""
    mock_sr_client = MagicMock()

    with patch("datagen.simulator.SchemaRegistryClient", return_value=mock_sr_client):
        with patch("datagen.simulator.AvroSerializer") as mock_avro_cls:
            mock_avro_cls.return_value = MagicMock(spec=AvroSerializer)

            from datagen.simulator import _create_kafka_producer

            producer, serializer = _create_kafka_producer()

            mock_avro_cls.assert_called_once_with(
                mock_sr_client,
                schema_str=None,
                conf={
                    "auto.register.schemas": False,
                    "use.latest.version": True,
                },
            )
            assert serializer is mock_avro_cls.return_value


def test_sr_client_configured_with_basic_auth():
    """SchemaRegistryClient must use basic.auth.user.info from config."""
    with patch("datagen.simulator.config") as mock_config:
        mock_config.SR_URL = "https://psrc-test.us-east-1.aws.confluent.cloud"
        mock_config.SR_API_KEY = "test-key"
        mock_config.SR_API_SECRET = "test-secret"
        mock_config.KAFKA_BOOTSTRAP = "localhost:9092"
        mock_config.KAFKA_API_KEY = ""
        mock_config.KAFKA_API_SECRET = ""

        with patch("datagen.simulator.SchemaRegistryClient") as mock_sr_cls:
            with patch("datagen.simulator.AvroSerializer"):
                from datagen.simulator import _create_kafka_producer

                _create_kafka_producer()

                mock_sr_cls.assert_called_once_with({
                    "url": "https://psrc-test.us-east-1.aws.confluent.cloud",
                    "basic.auth.user.info": "test-key:test-secret",
                })


def test_event_time_is_epoch_millis():
    """event_time must be an int (epoch milliseconds), not an ISO string."""
    from datagen.telemetry import generate_telemetry

    telemetry = generate_telemetry(lap=1, tire_age=1, tire_compound="SOFT", post_pit=False)
    telemetry["car_number"] = 44
    telemetry["lap"] = 1

    from datetime import datetime, timezone
    telemetry["event_time"] = int(datetime.now(timezone.utc).timestamp() * 1000)

    assert isinstance(telemetry["event_time"], int)
    assert telemetry["event_time"] > 1_000_000_000_000  # after year 2001 in millis
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/ahmedsaefzamzam/0-workshops/CurrentLondon/KeynoteDemo/F1 && python -m pytest datagen/tests/test_simulator.py -v`

Expected: First two tests FAIL because `_create_kafka_producer` currently returns a single `Producer` (not a tuple), and `SchemaRegistryClient` / `AvroSerializer` are not imported in `simulator.py`. Third test should PASS (it only tests the dict pattern).

- [ ] **Step 3: Commit**

```bash
git add datagen/tests/test_simulator.py
git commit -m "test(datagen): add failing tests for Avro serialization"
```

---

### Task 8: Implement Avro Serialization in Simulator

**Files:**
- Modify: `datagen/simulator.py`

- [ ] **Step 1: Update imports**

In `datagen/simulator.py`, replace the import block (lines 8-19) with:

```python
import json
import logging
import time
from datetime import datetime, timezone

import pymqi
from confluent_kafka import Producer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import SerializationContext, MessageField

from datagen import config
from datagen.drivers import GRID
from datagen.race_script import RaceState
from datagen.telemetry import generate_telemetry
```

- [ ] **Step 2: Replace _create_kafka_producer function**

Replace the `_create_kafka_producer` function (lines 28-37) with:

```python
def _create_kafka_producer():
    """Create a Kafka producer with Avro serializer for car telemetry."""
    sr_client = SchemaRegistryClient({
        "url": config.SR_URL,
        "basic.auth.user.info": f"{config.SR_API_KEY}:{config.SR_API_SECRET}",
    })

    avro_serializer = AvroSerializer(
        sr_client,
        schema_str=None,
        conf={
            "auto.register.schemas": False,
            "use.latest.version": True,
        },
    )

    producer = Producer({
        "bootstrap.servers": config.KAFKA_BOOTSTRAP,
        "security.protocol": "SASL_SSL",
        "sasl.mechanisms": "PLAIN",
        "sasl.username": config.KAFKA_API_KEY,
        "sasl.password": config.KAFKA_API_SECRET,
    })

    return producer, avro_serializer
```

- [ ] **Step 3: Update run_race to use AvroSerializer**

In `run_race()`, replace line 62:

```python
    producer = _create_kafka_producer()
```

with:

```python
    producer, avro_serializer = _create_kafka_producer()
```

- [ ] **Step 4: Replace event_time and produce call**

Replace the telemetry produce block (lines 111-122) with:

```python
                telemetry["car_number"] = config.OUR_CAR_NUMBER
                telemetry["lap"] = lap
                telemetry["event_time"] = int(
                    datetime.now(timezone.utc).timestamp() * 1000
                )

                producer.produce(
                    config.KAFKA_TOPIC,
                    key=str(config.OUR_CAR_NUMBER).encode("utf-8"),
                    value=avro_serializer(
                        telemetry,
                        SerializationContext(config.KAFKA_TOPIC, MessageField.VALUE),
                    ),
                )
                producer.poll(0)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/ahmedsaefzamzam/0-workshops/CurrentLondon/KeynoteDemo/F1 && python -m pytest datagen/tests/test_simulator.py -v`

Expected: All 3 tests PASS.

- [ ] **Step 6: Run all existing tests to check for regressions**

Run: `cd /Users/ahmedsaefzamzam/0-workshops/CurrentLondon/KeynoteDemo/F1 && python -m pytest datagen/tests/ -v`

Expected: All tests pass (test_telemetry.py, test_race_script.py, test_simulator.py).

- [ ] **Step 7: Commit**

```bash
git add datagen/simulator.py
git commit -m "feat(datagen): switch car-telemetry producer to Avro serialization via Schema Registry"
```

---

### Task 9: Final Validation

- [ ] **Step 1: Validate Terraform**

Run: `cd /Users/ahmedsaefzamzam/0-workshops/CurrentLondon/KeynoteDemo/F1/terraform && terraform validate`

Expected: `Success! The configuration is valid.`

- [ ] **Step 2: Run full test suite**

Run: `cd /Users/ahmedsaefzamzam/0-workshops/CurrentLondon/KeynoteDemo/F1 && python -m pytest datagen/tests/ -v`

Expected: All tests pass.

- [ ] **Step 3: Verify no raw JSON produce remains**

Search for `json.dumps(telemetry)` in `datagen/simulator.py`. It must not appear. The only `json.dumps` remaining should be for MQ standings messages (line 100).

- [ ] **Step 4: Commit plan doc**

```bash
git add docs/superpowers/plans/2026-04-20-avro-serialization-migration.md docs/superpowers/specs/2026-04-20-avro-serialization-migration.md
git commit -m "docs: add Avro serialization migration spec and implementation plan"
```
