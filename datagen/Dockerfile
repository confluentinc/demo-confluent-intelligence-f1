FROM python:3.11-slim

# Install IBM MQ C client libraries (required by pymqi)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl gnupg && \
    curl -fsSL https://public.dhe.ibm.com/ibmdl/export/pub/software/websphere/messaging/mqdev/redist/9.4.1.0-IBM-MQC-Redist-LinuxX64.tar.gz \
      -o /tmp/mq.tar.gz && \
    mkdir -p /opt/mqm && \
    tar -xzf /tmp/mq.tar.gz -C /opt/mqm && \
    rm /tmp/mq.tar.gz && \
    apt-get purge -y curl gnupg && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

ENV LD_LIBRARY_PATH=/opt/mqm/lib64
ENV MQ_HOME=/opt/mqm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/datagen/
RUN touch /app/datagen/__init__.py

ENTRYPOINT ["python", "-m", "datagen.simulator"]
