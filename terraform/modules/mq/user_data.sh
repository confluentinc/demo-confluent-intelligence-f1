#!/bin/bash
set -e

# --- System setup ---
yum update -y
yum install -y docker python3 python3-pip gcc python3-devel tar gzip nc
systemctl start docker
systemctl enable docker

# --- Install IBM MQ Redist client (needed for pymqi to compile and run) ---
curl -fsSL https://public.dhe.ibm.com/ibmdl/export/pub/software/websphere/messaging/mqdev/redist/9.4.1.0-IBM-MQC-Redist-LinuxX64.tar.gz \
  -o /tmp/mq-redist.tar.gz
mkdir -p /opt/mqm
tar -xzf /tmp/mq-redist.tar.gz -C /opt/mqm
rm -f /tmp/mq-redist.tar.gz

# Persist env vars so pymqi can find the client libs at install + run time
echo 'MQ_INSTALLATION_PATH=/opt/mqm' >> /etc/environment
echo 'LD_LIBRARY_PATH=/opt/mqm/lib64' >> /etc/environment
echo 'HOME=/root' >> /etc/environment
export MQ_INSTALLATION_PATH=/opt/mqm
export LD_LIBRARY_PATH=/opt/mqm/lib64
export HOME=/root  # IBM MQ client errors with AMQ6235E if $HOME is unset

pip3 install pymqi

# --- Start IBM MQ ---
docker run -d \
  --name ibm-mq \
  -p 1414:1414 \
  -p 9443:9443 \
  -e LICENSE=accept \
  -e MQ_QMGR_NAME=QM1 \
  -e MQ_APP_PASSWORD=passw0rd \
  -e MQ_ADMIN_PASSWORD=passw0rd \
  icr.io/ibm-messaging/mq:latest

# --- Wait for MQ to actually accept connections ---
echo "Waiting for IBM MQ to be ready..."
for i in $(seq 1 60); do
  if nc -z localhost 1414 2>/dev/null; then
    echo "MQ port 1414 open after $((i * 5))s"
    sleep 15  # extra time for queue manager to fully initialize
    break
  fi
  sleep 5
done

# --- Publish a single retained warmup message to dev/race_standings ---
# Why: the MQ Source Connector creates a durable subscription only when
# Terraform deploys it. Without a retained publication, the connector would
# not see anything on subscribe and `race_standings_raw` would not exist
# until the simulator publishes its first lap. Retained publications are
# delivered to new subscribers on subscribe, so the connector will pick up
# this warmup message immediately, materialise the topic + schema in Kafka,
# and Job 0 (also Terraform-managed) can read from offset 0.
#
# Job 0's WHERE clause filters out records with car_number = 0, so this
# warmup record never reaches the clean `race_standings` topic.
cat > /opt/mq_warmup.py <<'PYEOF'
import json
import pymqi
from datetime import datetime, timezone

WARMUP_MSG = {
    "car_number": 0,
    "driver": "WARMUP",
    "team": "WARMUP",
    "lap": 0,
    "position": 0,
    "gap_to_leader_sec": 0.0,
    "gap_to_ahead_sec": 0.0,
    "last_lap_time_sec": 0.0,
    "pit_stops": 0,
    "tire_compound": "SOFT",
    "tire_age_laps": 0,
    "in_pit_lane": False,
    "event_time": int(datetime.now(timezone.utc).timestamp() * 1000),
}

qmgr = pymqi.connect(
    "QM1",
    "DEV.ADMIN.SVRCONN",
    "localhost(1414)",
    "admin",
    "passw0rd",
)
topic = pymqi.Topic(
    qmgr,
    topic_string="dev/race_standings",
    open_opts=pymqi.CMQC.MQOO_OUTPUT | pymqi.CMQC.MQOO_FAIL_IF_QUIESCING,
)

# RFH2 envelope so the connector receives a JMS TextMessage (not BytesMessage)
rfh2 = pymqi.RFH2()
rfh2["Format"] = pymqi.CMQC.MQFMT_STRING
rfh2["CodedCharSetId"] = 1208
rfh2["NameValueCCSID"] = 1208
rfh2.add_folder(b"<mcd><Msd>jms_text</Msd></mcd>")
rfh2.add_folder(b"<jms><Dst>topic://dev/race_standings</Dst><Dlv>2</Dlv></jms>")

md = pymqi.MD()
md.Format = pymqi.CMQC.MQFMT_RF_HEADER_2
md.Encoding = 273
md.CodedCharSetId = 1208

pmo = pymqi.PMO()
pmo.Options |= pymqi.CMQC.MQPMO_RETAIN  # delivered to subscribers on subscribe

topic.pub_rfh2(json.dumps(WARMUP_MSG).encode("utf-8"), md, pmo, [rfh2])
topic.close()
qmgr.disconnect()
print("Warmup retained message published to dev/race_standings.")
PYEOF

# Retry the publish a few times — MQ may still be initialising channels even
# after the port opens.
for i in $(seq 1 10); do
  if HOME=/root LD_LIBRARY_PATH=/opt/mqm/lib64 python3 /opt/mq_warmup.py; then
    echo "Warmup publish succeeded on attempt $i"
    break
  fi
  echo "Warmup publish attempt $i failed, retrying in 10s..."
  sleep 10
done
