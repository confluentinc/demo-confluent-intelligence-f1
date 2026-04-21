# Race Standings Temporal Join Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pre-create the `race-standings` Kafka topic via Flink CREATE TABLE with `event_time` watermark and primary key so the temporal join in the enrichment job works correctly.

**Architecture:** Add a Flink CREATE TABLE for `race-standings` in the Terraform topics module (same pattern as `car-telemetry`). Update the simulator to write `event_time` as epoch milliseconds instead of `timestamp` as an ISO string. Update the MQ connector config to use AVRO format with a ValueToKey SMT so the Kafka message key is `car_number`. The enrichment SQL's temporal join (`FOR SYSTEM_TIME AS OF a.window_time`) already uses the correct time attribute.

**Tech Stack:** Terraform (Confluent provider), Python, Flink SQL

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `terraform/modules/topics/main.tf` | Add `race-standings` Flink CREATE TABLE with event_time + watermark + PK |
| Modify | `terraform/modules/topics/outputs.tf` | Add race-standings topic output |
| Modify | `datagen/race_script.py` | Rename `timestamp` key to `event_time` in `get_car()` return dict |
| Modify | `datagen/simulator.py` | Write `event_time` as epoch millis (int) instead of ISO string `timestamp` |
| Modify | `datagen/tests/test_race_script.py` | Add test for `event_time` field name in standings output |
| Modify | `demo-reference/mq_connector_config.json` | Switch to AVRO format, add ValueToKey SMT |
| Modify | `CLAUDE.md` | Update topic table, schema, and architecture notes |

---

### Task 1: Add race-standings Flink CREATE TABLE to Terraform

**Files:**
- Modify: `terraform/modules/topics/main.tf`
- Modify: `terraform/modules/topics/outputs.tf`

The topics module currently creates only `car-telemetry` via a `confluent_flink_statement`. Add a second statement for `race-standings` with `event_time TIMESTAMP(3)`, WATERMARK, and PRIMARY KEY.

- [ ] **Step 1: Add the race-standings CREATE TABLE statement**

In `terraform/modules/topics/main.tf`, append after the existing `create_car_telemetry_table` resource (after line 48):

```terraform
resource "confluent_flink_statement" "create_race_standings_table" {
  organization {
    id = var.organization_id
  }
  environment {
    id = var.environment_id
  }
  compute_pool {
    id = var.compute_pool_id
  }
  principal {
    id = var.service_account_id
  }

  statement = <<-EOT
    CREATE TABLE `race-standings` (
      `car_number` INT,
      `driver` STRING,
      `team` STRING,
      `lap` INT,
      `position` INT,
      `gap_to_leader_sec` DOUBLE,
      `gap_to_ahead_sec` DOUBLE,
      `last_lap_time_sec` DOUBLE,
      `pit_stops` INT,
      `tire_compound` STRING,
      `tire_age_laps` INT,
      `in_pit_lane` BOOLEAN,
      `event_time` TIMESTAMP(3),
      WATERMARK FOR `event_time` AS `event_time` - INTERVAL '10' SECOND,
      PRIMARY KEY (`car_number`) NOT ENFORCED
    );
  EOT

  properties = {
    "sql.current-catalog"  = var.environment_name
    "sql.current-database" = var.cluster_name
  }

  depends_on = [confluent_flink_statement.create_car_telemetry_table]
}
```

Note: The WATERMARK delay is 10 seconds (matching the lap interval). The `depends_on` ensures sequential execution on the Flink compute pool.

- [ ] **Step 2: Add race-standings output**

Append to `terraform/modules/topics/outputs.tf` after line 3:

```terraform
output "race_standings_topic" {
  value = "race-standings"
}
```

- [ ] **Step 3: Commit**

```bash
git add terraform/modules/topics/main.tf terraform/modules/topics/outputs.tf
git commit -m "feat(terraform): add race-standings Flink CREATE TABLE with event_time watermark and primary key"
```

---

### Task 2: Update Simulator to Write event_time as Epoch Millis

**Files:**
- Modify: `datagen/simulator.py`

The simulator currently writes a `timestamp` field as an ISO string for each race standing sent to MQ. Change it to `event_time` as epoch milliseconds (int), matching the Avro schema's `TIMESTAMP(3)` → `timestamp-millis` logical type.

- [ ] **Step 1: Replace the timestamp field in the MQ produce block**

In `datagen/simulator.py`, replace lines 114-118:

```python
            for standing in standings:
                standing["timestamp"] = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                mq_queue.put(json.dumps(standing).encode("utf-8"))
```

With:

```python
            for standing in standings:
                standing["event_time"] = int(
                    datetime.now(timezone.utc).timestamp() * 1000
                )
                mq_queue.put(json.dumps(standing).encode("utf-8"))
```

- [ ] **Step 2: Run existing tests to check for regressions**

Run: `cd /Users/ahmedsaefzamzam/0-workshops/CurrentLondon/KeynoteDemo/F1 && python3 -m pytest datagen/tests/ -v`

Expected: All 13 tests pass. No existing test references the `timestamp` field directly — they test race state, not serialization output.

- [ ] **Step 3: Commit**

```bash
git add datagen/simulator.py
git commit -m "feat(datagen): write event_time as epoch millis for race standings MQ messages"
```

---

### Task 3: Add Test for event_time Field in Race Standings

**Files:**
- Modify: `datagen/tests/test_race_script.py`

Add a test that verifies `get_car()` does NOT return a `timestamp` field (that's added by the simulator, not RaceState) and that the standings dict has the expected keys. This ensures future refactors don't accidentally reintroduce `timestamp`.

- [ ] **Step 1: Add test for standings dict keys**

Append to `datagen/tests/test_race_script.py` after the last test:

```python
def test_standings_dict_has_expected_keys():
    """get_car() should return the expected set of keys (no 'timestamp' — that's added by simulator)."""
    state = RaceState(GRID)
    state.advance_lap()
    car44 = state.get_car(44)
    expected_keys = {
        "car_number", "driver", "team", "lap", "position",
        "gap_to_leader_sec", "gap_to_ahead_sec", "last_lap_time_sec",
        "pit_stops", "tire_compound", "tire_age_laps", "in_pit_lane",
    }
    assert set(car44.keys()) == expected_keys
    assert "timestamp" not in car44
    assert "event_time" not in car44
```

- [ ] **Step 2: Run tests**

Run: `cd /Users/ahmedsaefzamzam/0-workshops/CurrentLondon/KeynoteDemo/F1 && python3 -m pytest datagen/tests/test_race_script.py -v`

Expected: All 6 tests pass (5 existing + 1 new).

- [ ] **Step 3: Commit**

```bash
git add datagen/tests/test_race_script.py
git commit -m "test(datagen): verify standings dict keys do not include timestamp"
```

---

### Task 4: Update MQ Connector Config

**Files:**
- Modify: `demo-reference/mq_connector_config.json`

Switch from JSON to AVRO output format (so the connector writes Avro-encoded messages matching the Flink-registered schema). Add a ValueToKey SMT so `car_number` becomes the Kafka message key (required for the PRIMARY KEY / versioned table behavior).

- [ ] **Step 1: Replace connector config**

Replace the entire contents of `demo-reference/mq_connector_config.json` with:

```json
{
  "name": "f1-mq-source",
  "config": {
    "connector.class": "IbmMQSource",
    "kafka.auth.mode": "SERVICE_ACCOUNT",
    "kafka.service.account.id": "<SERVICE_ACCOUNT_ID>",
    "kafka.topic": "race-standings",
    "mq.hostname": "<MQ_PUBLIC_IP>",
    "mq.port": "1414",
    "mq.queue.manager": "QM1",
    "mq.channel": "DEV.APP.SVRCONN",
    "mq.queue": "DEV.QUEUE.1",
    "mq.username": "app",
    "mq.password": "passw0rd",
    "jms.destination.type": "queue",
    "kafka.api.key": "<KAFKA_API_KEY>",
    "kafka.api.secret": "<KAFKA_API_SECRET>",
    "output.data.format": "AVRO",
    "transforms": "extractKey",
    "transforms.extractKey.type": "org.apache.kafka.connect.transforms.ValueToKey",
    "transforms.extractKey.fields": "car_number",
    "tasks.max": "1"
  }
}
```

Changes from previous config:
- `output.data.format`: `JSON` → `AVRO`
- Added `transforms`, `transforms.extractKey.type`, `transforms.extractKey.fields`

- [ ] **Step 2: Commit**

```bash
git add demo-reference/mq_connector_config.json
git commit -m "feat(demo-reference): switch MQ connector to AVRO format with ValueToKey SMT for car_number"
```

---

### Task 5: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

Update the documentation to reflect that `race-standings` is now pre-created by Terraform (not by the MQ connector), uses `event_time` instead of `timestamp`, and has a primary key with watermark.

- [ ] **Step 1: Update the Kafka Topics table**

In `CLAUDE.md`, find the Kafka Topics table and change the `race-standings` row. Replace:

```
| `race-standings` | FIA → MQ → MQ Connector | Job 1: Enrichment (temporal join) + RTCE | **MQ Connector** (during demo) | No |
```

With:

```
| `race-standings` | FIA → MQ → MQ Connector | Job 1: Enrichment (temporal join) + RTCE | **Terraform** (Flink CREATE TABLE) | No |
```

- [ ] **Step 2: Update the Race Standings schema**

Find the `### Race Standings (Created by MQ Connector)` section header and replace it with `### Race Standings (Flink CREATE TABLE — Terraform)`. Then replace the CREATE TABLE SQL in that section:

```sql
CREATE TABLE `race-standings` (
  `car_number` INT,
  `driver` STRING,
  `team` STRING,
  `lap` INT,
  `position` INT,
  `gap_to_leader_sec` DOUBLE,
  `gap_to_ahead_sec` DOUBLE,
  `last_lap_time_sec` DOUBLE,
  `pit_stops` INT,
  `tire_compound` STRING,
  `tire_age_laps` INT,
  `in_pit_lane` BOOLEAN,
  `event_time` TIMESTAMP(3),
  WATERMARK FOR `event_time` AS `event_time` - INTERVAL '10' SECOND,
  PRIMARY KEY (`car_number`) NOT ENFORCED
);
```

- [ ] **Step 3: Update the Race Standings JSON example**

Find the race-standings JSON example and replace `"timestamp"` with `"event_time"` as epoch millis:

```json
{
  "car_number": 44,
  "driver": "James River",
  "team": "River Racing",
  "lap": 32,
  "position": 8,
  "gap_to_leader_sec": 18.4,
  "gap_to_ahead_sec": 2.1,
  "last_lap_time_sec": 92.100,
  "pit_stops": 0,
  "tire_compound": "SOFT",
  "tire_age_laps": 32,
  "in_pit_lane": false,
  "event_time": 1718373720000
}
```

- [ ] **Step 4: Update the Data Sources side-by-side table**

Find the `Topic created by` row in the data sources comparison table and change `race-standings` from `MQ Connector (during demo)` to `Terraform (Flink CREATE TABLE)`.

- [ ] **Step 5: Update Terraform Deploys table**

In the "What Terraform Deploys vs. Demo Hero Moments" section, add `race-standings` topic to the Terraform deploys table:

```
| `race-standings` topic with schema (Flink CREATE TABLE) | `modules/topics/` |
```

And in the "Left for Demo" section, update the MQ connector bullet to note it writes to the **existing** topic:

```
- Deploy MQ Source Connector (writes to existing `race-standings` topic)
```

- [ ] **Step 6: Update Topic Creation Strategy note**

Find the "Topic Creation Strategy" section. Update to reflect that both `car-telemetry` AND `race-standings` are created by Terraform:

Replace:
```
**Only `car-telemetry` is created by Terraform.** The other 4 topics are created during the demo by their respective producers.
```

With:
```
**`car-telemetry` and `race-standings` are created by Terraform.** The other 3 topics are created during the demo by their respective producers.
```

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for race-standings pre-creation, event_time, and temporal join requirements"
```

---

### Task 6: Final Validation

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/ahmedsaefzamzam/0-workshops/CurrentLondon/KeynoteDemo/F1 && python3 -m pytest datagen/tests/ -v`

Expected: All 14 tests pass (5 race_script + 3 simulator + 5 telemetry + 1 new).

- [ ] **Step 2: Verify no 'timestamp' field in simulator MQ produce**

Search for `standing["timestamp"]` in `datagen/simulator.py`. It must not appear. Only `standing["event_time"]` should be present.

- [ ] **Step 3: Verify enrichment SQL is consistent**

Check that `demo-reference/enrichment_anomaly.sql` line 121 still reads:
```sql
JOIN `race-standings` FOR SYSTEM_TIME AS OF a.window_time AS r
```
This should NOT have changed — the temporal join uses `window_time` from the tumble window on the left side. The `event_time` watermark on `race-standings` enables the right side's watermark to advance, which is required for the temporal join to produce results.

- [ ] **Step 4: Commit plan doc**

```bash
git add docs/superpowers/plans/2026-04-20-race-standings-temporal-join.md
git commit -m "docs: add race-standings temporal join implementation plan"
```
