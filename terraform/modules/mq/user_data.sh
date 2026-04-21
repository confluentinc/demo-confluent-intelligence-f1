#!/bin/bash
set -e

# System setup
yum update -y
yum install -y docker
systemctl start docker
systemctl enable docker

# Start IBM MQ
docker run -d \
  --name ibm-mq \
  -p 1414:1414 \
  -p 9443:9443 \
  -e LICENSE=accept \
  -e MQ_QMGR_NAME=QM1 \
  -e MQ_APP_PASSWORD=passw0rd \
  -e MQ_ADMIN_PASSWORD=passw0rd \
  icr.io/ibm-messaging/mq:latest

# Wait for MQ to be ready
sleep 30
