River Racing is an ambitious F1 team that wants to use AI to make smarter pit wall decisions in real time. During the Silverstone Grand Prix, three live data feeds stream into Confluent Cloud — car sensor telemetry, FIA race standings, and driver profiles — and are transformed into an always-up-to-date view of their car's condition and competitive context. An AI Pit Strategy Agent monitors every sensor for anomalies, evaluates race position, and recommends whether to pit now, pit soon, or stay out, and explains its reasoning in natural language.

Data sources:
car-telemetry (Race simulator → Direct to Kafka — internal team sensors)
race-standings (FIA timing feed → IBM MQ → Confluent Cloud cluster)
drivers (Postgres → CDC Debezium → Confluent Cloud cluster)

Step 1: Car State Enrichment + Anomaly Detection
Car sensor data (tire temperatures, pressures, engine temp, brake temp, battery, fuel) is aggregated in 10-second tumbling windows and passed through AI_DETECT_ANOMALIES to monitor every metric for unusual behavior. The result is enriched with live race standings (position, gaps, pit status, tire compound) via a temporal join to produce a comprehensive car-state stream.
Features used: Flink (tumbling windows, temporal joins), AI_DETECT_ANOMALIES, DBT-Adapter (manages Flink SQL / streaming models)

Step 2: AI Pit Strategy Agent
The enriched car-state stream feeds a Streaming Agent that evaluates every lap. It assesses anomaly flags, tire condition, and race position, and uses Real-Time Context Engine to look up competitor standings. It produces a pit suggestion (Pit Now, Pit Soon, Stay Out) with recommended tire compound, expected position outcomes, and reasoning, written to a pit-decisions topic.
Features used: Streaming Agents (Flink SQL), Real-Time Context Engine, DBT-Adapter (pipeline definition)

Tableflow:
Enabled on pit-decisions and drivers to store AI strategy output and reference data as Delta Lake tables. Teams can analyze how pit decisions impacted race results.
Features used: Tableflow (Delta Lake), Databricks Genie (Unity Catalog)

MCP + Claude:
Claude Desktop uses mcp-confluent to discover data sources, deploy connectors, enable Tableflow, and manage the demo environment by natural language.
