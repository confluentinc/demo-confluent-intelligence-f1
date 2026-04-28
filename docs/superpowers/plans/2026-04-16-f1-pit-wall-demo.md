# F1 Pit Wall AI Demo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a real-time F1 pit strategy demo where an AI agent monitors car telemetry, detects anomalies, and recommends pit stops — deployable via Terraform and runnable in ~9.5 minutes.

**Architecture:** Three data sources (car telemetry direct-to-Kafka, FIA standings via MQ, drivers via Postgres CDC) feed into two Flink jobs — one for enrichment + anomaly detection, one for AI agent inference. Output flows to Tableflow for Databricks Genie analytics.

**Tech Stack:** Terraform (Confluent Cloud + AWS), Python (race simulator), Flink SQL (enrichment + agent), DBT-Confluent adapter, IBM MQ (Docker), Postgres (Docker), S3/Delta Lake/Databricks.

**Design Doc:** `CLAUDE.md` in the project root — the authoritative reference for all schemas, SQL, architecture decisions, and constraints.

---

## File Structure

```
F1/
├── CLAUDE.md                                    # Design doc (already exists)
├── USE-CASE.md                                  # Exec summary (already exists)
├── README.md                                    # Quick start guide
├── .gitignore                                   # Excludes tfvars, .terraform, __pycache__
├── terraform/
│   ├── main.tf                                  # Providers + all 7 module calls
│   ├── variables.tf                             # 3 variables (api key, api secret, region)
│   ├── outputs.tf                               # All module outputs surfaced
│   ├── versions.tf                              # Provider version constraints
│   ├── terraform.tfvars.example                 # Template for credentials
│   └── modules/
│       ├── environment/
│       │   ├── main.tf                          # Confluent environment + schema registry
│       │   ├── variables.tf
│       │   └── outputs.tf
│       ├── cluster/
│       │   ├── main.tf                          # Kafka cluster + service account + API keys
│       │   ├── variables.tf
│       │   └── outputs.tf
│       ├── flink/
│       │   ├── main.tf                          # Compute pool only
│       │   ├── variables.tf
│       │   └── outputs.tf
│       ├── topics/
│       │   ├── main.tf                          # car-telemetry Flink CREATE TABLE
│       │   ├── variables.tf
│       │   └── outputs.tf
│       ├── mq/
│       │   ├── main.tf                          # EC2 + security group
│       │   ├── variables.tf
│       │   ├── outputs.tf
│       │   └── user_data.sh                     # Docker MQ + race simulator bootstrap
│       ├── postgres/
│       │   ├── main.tf                          # EC2 + security group
│       │   ├── variables.tf
│       │   ├── outputs.tf
│       │   └── user_data.sh                     # Docker Postgres + drivers seed
│       └── tableflow/
│           ├── main.tf                          # S3 + IAM role + provider integration
│           ├── variables.tf
│           └── outputs.tf
├── datagen/
│   ├── requirements.txt                         # pymqi, confluent-kafka
│   ├── config.py                                # MQ + Kafka connection settings
│   ├── drivers.py                               # Full 22-driver grid definition
│   ├── race_script.py                           # Semi-scripted race events + position logic
│   ├── telemetry.py                             # Sensor metric generation with anomaly
│   ├── simulator.py                             # Main entry point — orchestrates race
│   └── tests/
│       ├── test_telemetry.py                    # Telemetry generation tests
│       ├── test_race_script.py                  # Race script tests
│       └── test_simulator.py                    # Integration tests
├── demo-reference/
│   ├── enrichment_anomaly.sql                   # Job 1: enrichment + anomaly detection
│   ├── streaming_agent.sql                      # Job 2: agent setup + invocation
│   ├── mq_connector_config.json                 # MQ Source connector config
│   └── cdc_connector_config.json                # Debezium CDC connector config
├── data/
│   ├── drivers.csv                              # 22 fictional drivers (flat file)
│   └── drivers_seed.sql                         # Postgres CREATE TABLE + INSERT
├── scripts/
│   ├── setup.sh                                 # terraform init + apply + export outputs
│   └── teardown.sh                              # terraform destroy
├── docs/
│   ├── SETUP-DBT-ADAPTER.md                     # DBT adapter setup guide
│   ├── SETUP-TABLEFLOW.md                       # Tableflow + Databricks setup guide
│   └── SETUP-GENIE.md                           # Databricks Genie setup guide
└── tableflow/
    └── EXAMPLE-QUERIES.md                       # Reference SQL queries for Genie
```

---

## Phase 1: Project Scaffolding

### Task 1: Create project structure and .gitignore

**Files:**
- Create: `.gitignore`
- Create: `README.md`

- [ ] **Step 1: Create .gitignore**

```gitignore
# Terraform
terraform/terraform.tfvars
terraform/.terraform/
terraform/*.tfstate*
terraform/.terraform.lock.hcl

# Python
__pycache__/
*.pyc
.env
venv/

# IDE
.idea/
.vscode/
*.swp
```

- [ ] **Step 2: Create README.md skeleton**

```markdown
# F1 Pit Wall AI Demo — River Racing

Real-time AI pit strategy system for a Formula 1 team. An AI agent monitors live car telemetry, detects anomalies via `AI_DETECT_ANOMALIES`, and recommends pit stop strategy in real time.

## Quick Start

1. Copy credentials:
   ```bash
   cp terraform/terraform.tfvars.example terraform/terraform.tfvars
   # Edit with your Confluent Cloud + AWS credentials
   ```

2. Deploy infrastructure:
   ```bash
   ./scripts/setup.sh
   ```

3. The race simulator starts automatically. See CLAUDE.md for demo recording instructions.

## Architecture

See [CLAUDE.md](CLAUDE.md) for full architecture, schemas, and demo guide.

## Teardown

```bash
./scripts/teardown.sh
```
```

- [ ] **Step 3: Commit**

```bash
git add .gitignore README.md CLAUDE.md USE-CASE.md
git commit -m "docs: project scaffolding — .gitignore, README, design docs"
```

---

## Phase 2: Terraform Infrastructure

### Task 2: Terraform root configuration

**Files:**
- Create: `terraform/versions.tf`
- Create: `terraform/variables.tf`
- Create: `terraform/terraform.tfvars.example`
- Create: `terraform/main.tf`
- Create: `terraform/outputs.tf`

- [ ] **Step 1: Create versions.tf**

```hcl
# terraform/versions.tf
terraform {
  required_version = ">= 1.3.0"
  required_providers {
    confluent = {
      source  = "confluentinc/confluent"
      version = "~> 2.11"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}
```

- [ ] **Step 2: Create variables.tf**

```hcl
# terraform/variables.tf
variable "confluent_cloud_api_key" {
  description = "Confluent Cloud API Key"
  type        = string
  sensitive   = true
}

variable "confluent_cloud_api_secret" {
  description = "Confluent Cloud API Secret"
  type        = string
  sensitive   = true
}

variable "region" {
  description = "AWS and Confluent Cloud region"
  type        = string
  default     = "us-east-1"
}
```

- [ ] **Step 3: Create terraform.tfvars.example**

```hcl
# terraform/terraform.tfvars.example
confluent_cloud_api_key    = "YOUR_CONFLUENT_CLOUD_API_KEY"
confluent_cloud_api_secret = "YOUR_CONFLUENT_CLOUD_API_SECRET"
region                     = "us-east-1"
```

- [ ] **Step 4: Create main.tf with providers and module calls**

```hcl
# terraform/main.tf
provider "confluent" {
  cloud_api_key    = var.confluent_cloud_api_key
  cloud_api_secret = var.confluent_cloud_api_secret
}

provider "aws" {
  region = var.region
}

data "confluent_organization" "main" {}

# --- Modules ---

module "environment" {
  source           = "./modules/environment"
  environment_name = "prod-f1-env"
}

module "cluster" {
  source         = "./modules/cluster"
  environment_id = module.environment.environment_id
  cluster_name   = "prod-f1-cluster"
  cloud_provider = "AWS"
  cloud_region   = var.region
}

module "flink" {
  source             = "./modules/flink"
  organization_id    = data.confluent_organization.main.id
  environment_id     = module.environment.environment_id
  environment_name   = "prod-f1-env"
  cluster_name       = "prod-f1-cluster"
  cloud_provider     = "AWS"
  cloud_region       = var.region
  service_account_id = module.cluster.service_account_id
}

module "topics" {
  source             = "./modules/topics"
  organization_id    = data.confluent_organization.main.id
  environment_id     = module.environment.environment_id
  environment_name   = "prod-f1-env"
  cluster_name       = "prod-f1-cluster"
  compute_pool_id    = module.flink.compute_pool_id
  service_account_id = module.cluster.service_account_id

  depends_on = [module.flink]
}

module "mq" {
  source          = "./modules/mq"
  aws_region      = var.region
  kafka_bootstrap = module.cluster.cluster_bootstrap
  kafka_api_key   = module.cluster.app_api_key
  kafka_api_secret = module.cluster.app_api_secret
}

module "postgres" {
  source     = "./modules/postgres"
  aws_region = var.region
}

module "tableflow" {
  source         = "./modules/tableflow"
  environment_id = module.environment.environment_id
  bucket_name    = "f1-demo-tableflow"
}
```

- [ ] **Step 5: Create outputs.tf**

```hcl
# terraform/outputs.tf
output "environment_id" {
  value = module.environment.environment_id
}

output "cluster_id" {
  value = module.cluster.cluster_id
}

output "cluster_bootstrap" {
  value = module.cluster.cluster_bootstrap
}

output "app_api_key" {
  value     = module.cluster.app_api_key
  sensitive = true
}

output "app_api_secret" {
  value     = module.cluster.app_api_secret
  sensitive = true
}

output "mq_public_ip" {
  value = module.mq.mq_public_ip
}

output "mq_connection_string" {
  value = module.mq.mq_connection_string
}

output "postgres_public_ip" {
  value = module.postgres.postgres_public_ip
}

output "postgres_connection_string" {
  value = module.postgres.postgres_connection_string
}

output "tableflow_s3_bucket" {
  value = module.tableflow.s3_bucket_name
}

output "tableflow_iam_role_arn" {
  value = module.tableflow.iam_role_arn
}

output "tableflow_provider_integration_id" {
  value = module.tableflow.provider_integration_id
}
```

- [ ] **Step 6: Validate structure**

```bash
cd terraform && terraform init -backend=false
```

Expected: Provider downloads succeed. No syntax errors.

- [ ] **Step 7: Commit**

```bash
git add terraform/versions.tf terraform/variables.tf terraform/terraform.tfvars.example terraform/main.tf terraform/outputs.tf
git commit -m "feat: terraform root configuration — providers, variables, module calls"
```

---

### Task 3: Environment module

**Files:**
- Create: `terraform/modules/environment/main.tf`
- Create: `terraform/modules/environment/variables.tf`
- Create: `terraform/modules/environment/outputs.tf`

- [ ] **Step 1: Create the environment module**

```hcl
# terraform/modules/environment/variables.tf
variable "environment_name" {
  description = "Name for the Confluent Cloud environment"
  type        = string
}
```

```hcl
# terraform/modules/environment/main.tf
resource "confluent_environment" "main" {
  display_name = var.environment_name

  stream_governance {
    package = "ESSENTIALS"
  }
}

data "confluent_schema_registry_cluster" "main" {
  environment {
    id = confluent_environment.main.id
  }
}
```

```hcl
# terraform/modules/environment/outputs.tf
output "environment_id" {
  value = confluent_environment.main.id
}

output "environment_name" {
  value = confluent_environment.main.display_name
}

output "schema_registry_id" {
  value = data.confluent_schema_registry_cluster.main.id
}
```

- [ ] **Step 2: Commit**

```bash
git add terraform/modules/environment/
git commit -m "feat: terraform environment module — Confluent env + schema registry"
```

---

### Task 4: Cluster module

**Files:**
- Create: `terraform/modules/cluster/main.tf`
- Create: `terraform/modules/cluster/variables.tf`
- Create: `terraform/modules/cluster/outputs.tf`

- [ ] **Step 1: Create the cluster module**

```hcl
# terraform/modules/cluster/variables.tf
variable "environment_id" {
  description = "Confluent Cloud environment ID"
  type        = string
}

variable "cluster_name" {
  description = "Name for the Kafka cluster"
  type        = string
}

variable "cloud_provider" {
  description = "Cloud provider"
  type        = string
  default     = "AWS"
}

variable "cloud_region" {
  description = "Cloud region"
  type        = string
}
```

```hcl
# terraform/modules/cluster/main.tf
resource "confluent_kafka_cluster" "main" {
  display_name = var.cluster_name
  cloud        = var.cloud_provider
  region       = var.cloud_region
  availability = "SINGLE_ZONE"

  standard {}

  environment {
    id = var.environment_id
  }
}

resource "confluent_service_account" "app" {
  display_name = "f1-demo-app"
  description  = "Service account for F1 demo application"
}

resource "confluent_role_binding" "app_manager" {
  principal   = "User:${confluent_service_account.app.id}"
  role_name   = "CloudClusterAdmin"
  crn_pattern = confluent_kafka_cluster.main.rbac_crn
}

resource "confluent_role_binding" "app_environment_admin" {
  principal   = "User:${confluent_service_account.app.id}"
  role_name   = "EnvironmentAdmin"
  crn_pattern = confluent_kafka_cluster.main.environment.0.resource_name
}

resource "confluent_api_key" "app" {
  display_name = "f1-demo-app-key"

  owner {
    id          = confluent_service_account.app.id
    api_version = confluent_service_account.app.api_version
    kind        = confluent_service_account.app.kind
  }

  managed_resource {
    id          = confluent_kafka_cluster.main.id
    api_version = confluent_kafka_cluster.main.api_version
    kind        = confluent_kafka_cluster.main.kind

    environment {
      id = var.environment_id
    }
  }

  depends_on = [
    confluent_role_binding.app_manager,
  ]
}
```

```hcl
# terraform/modules/cluster/outputs.tf
output "cluster_id" {
  value = confluent_kafka_cluster.main.id
}

output "cluster_bootstrap" {
  value = confluent_kafka_cluster.main.bootstrap_endpoint
}

output "cluster_rest_endpoint" {
  value = confluent_kafka_cluster.main.rest_endpoint
}

output "app_api_key" {
  value     = confluent_api_key.app.id
  sensitive = true
}

output "app_api_secret" {
  value     = confluent_api_key.app.secret
  sensitive = true
}

output "service_account_id" {
  value = confluent_service_account.app.id
}

output "service_account_api_version" {
  value = confluent_service_account.app.api_version
}
```

- [ ] **Step 2: Commit**

```bash
git add terraform/modules/cluster/
git commit -m "feat: terraform cluster module — Kafka cluster + service account + API keys"
```

---

### Task 5: Flink module (compute pool only)

**Files:**
- Create: `terraform/modules/flink/main.tf`
- Create: `terraform/modules/flink/variables.tf`
- Create: `terraform/modules/flink/outputs.tf`

- [ ] **Step 1: Create the flink module**

```hcl
# terraform/modules/flink/variables.tf
variable "organization_id" {
  description = "Confluent Cloud organization ID"
  type        = string
}

variable "environment_id" {
  description = "Confluent Cloud environment ID"
  type        = string
}

variable "environment_name" {
  description = "Confluent Cloud environment name (Flink catalog)"
  type        = string
}

variable "cluster_name" {
  description = "Kafka cluster name (Flink database)"
  type        = string
}

variable "cloud_provider" {
  description = "Cloud provider"
  type        = string
}

variable "cloud_region" {
  description = "Cloud region"
  type        = string
}

variable "service_account_id" {
  description = "Service account ID for Flink"
  type        = string
}
```

```hcl
# terraform/modules/flink/main.tf
data "confluent_flink_region" "main" {
  cloud  = var.cloud_provider
  region = var.cloud_region
}

resource "confluent_flink_compute_pool" "main" {
  display_name = "f1-demo-pool"
  cloud        = var.cloud_provider
  region       = var.cloud_region
  max_cfu      = 10

  environment {
    id = var.environment_id
  }
}

resource "confluent_api_key" "flink" {
  display_name = "f1-demo-flink-key"

  owner {
    id          = var.service_account_id
    api_version = "iam/v2"
    kind        = "ServiceAccount"
  }

  managed_resource {
    id          = data.confluent_flink_region.main.id
    api_version = data.confluent_flink_region.main.api_version
    kind        = data.confluent_flink_region.main.kind

    environment {
      id = var.environment_id
    }
  }
}
```

```hcl
# terraform/modules/flink/outputs.tf
output "compute_pool_id" {
  value = confluent_flink_compute_pool.main.id
}

output "flink_rest_endpoint" {
  value = data.confluent_flink_region.main.rest_endpoint
}

output "flink_api_key" {
  value     = confluent_api_key.flink.id
  sensitive = true
}

output "flink_api_secret" {
  value     = confluent_api_key.flink.secret
  sensitive = true
}
```

- [ ] **Step 2: Commit**

```bash
git add terraform/modules/flink/
git commit -m "feat: terraform flink module — compute pool only"
```

---

### Task 6: Topics module (car-telemetry via Flink CREATE TABLE)

**Files:**
- Create: `terraform/modules/topics/main.tf`
- Create: `terraform/modules/topics/variables.tf`
- Create: `terraform/modules/topics/outputs.tf`

- [ ] **Step 1: Create the topics module**

```hcl
# terraform/modules/topics/variables.tf
variable "organization_id" {
  description = "Confluent Cloud organization ID"
  type        = string
}

variable "environment_id" {
  description = "Confluent Cloud environment ID"
  type        = string
}

variable "environment_name" {
  description = "Confluent Cloud environment name (Flink catalog)"
  type        = string
}

variable "cluster_name" {
  description = "Kafka cluster name (Flink database)"
  type        = string
}

variable "compute_pool_id" {
  description = "Flink compute pool ID"
  type        = string
}

variable "service_account_id" {
  description = "Service account ID"
  type        = string
}
```

```hcl
# terraform/modules/topics/main.tf

# Create car-telemetry topic via Flink CREATE TABLE
# This auto-creates the backing Kafka topic + schema subjects
resource "confluent_flink_statement" "create_car_telemetry_table" {
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
    CREATE TABLE `car-telemetry` (
      `car_number` INT,
      `lap` INT,
      `tire_temp_fl_c` DOUBLE,
      `tire_temp_fr_c` DOUBLE,
      `tire_temp_rl_c` DOUBLE,
      `tire_temp_rr_c` DOUBLE,
      `tire_pressure_fl_psi` DOUBLE,
      `tire_pressure_fr_psi` DOUBLE,
      `tire_pressure_rl_psi` DOUBLE,
      `tire_pressure_rr_psi` DOUBLE,
      `engine_temp_c` DOUBLE,
      `brake_temp_fl_c` DOUBLE,
      `brake_temp_fr_c` DOUBLE,
      `battery_charge_pct` DOUBLE,
      `fuel_remaining_kg` DOUBLE,
      `drs_active` BOOLEAN,
      `speed_kph` DOUBLE,
      `throttle_pct` DOUBLE,
      `brake_pct` DOUBLE,
      `event_time` TIMESTAMP(3),
      WATERMARK FOR `event_time` AS `event_time` - INTERVAL '5' SECOND,
      PRIMARY KEY (`car_number`) NOT ENFORCED
    );
  EOT

  properties = {
    "sql.current-catalog"  = var.environment_name
    "sql.current-database" = var.cluster_name
  }
}
```

```hcl
# terraform/modules/topics/outputs.tf
output "car_telemetry_topic" {
  value = "car-telemetry"
}
```

- [ ] **Step 2: Commit**

```bash
git add terraform/modules/topics/
git commit -m "feat: terraform topics module — car-telemetry Flink CREATE TABLE"
```

---

### Task 7: MQ module (EC2 + Docker IBM MQ + race simulator)

**Files:**
- Create: `terraform/modules/mq/main.tf`
- Create: `terraform/modules/mq/variables.tf`
- Create: `terraform/modules/mq/outputs.tf`
- Create: `terraform/modules/mq/user_data.sh`

- [ ] **Step 1: Create MQ module variables**

```hcl
# terraform/modules/mq/variables.tf
variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.medium"
}

variable "key_pair_name" {
  description = "EC2 key pair name for SSH access (optional)"
  type        = string
  default     = ""
}

variable "kafka_bootstrap" {
  description = "Kafka bootstrap server for car-telemetry producer"
  type        = string
}

variable "kafka_api_key" {
  description = "Kafka API key for car-telemetry producer"
  type        = string
  sensitive   = true
}

variable "kafka_api_secret" {
  description = "Kafka API secret for car-telemetry producer"
  type        = string
  sensitive   = true
}
```

- [ ] **Step 2: Create MQ module main.tf**

```hcl
# terraform/modules/mq/main.tf
data "aws_ami" "amazon_linux" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_security_group" "mq" {
  name_prefix = "f1-demo-mq-"
  description = "Security group for IBM MQ + race simulator"

  ingress {
    from_port   = 1414
    to_port     = 1414
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "IBM MQ listener"
  }

  ingress {
    from_port   = 9443
    to_port     = 9443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "IBM MQ web console"
  }

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "SSH access"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "f1-demo-mq"
  }
}

resource "aws_instance" "mq" {
  ami                    = data.aws_ami.amazon_linux.id
  instance_type          = var.instance_type
  vpc_security_group_ids = [aws_security_group.mq.id]
  key_name               = var.key_pair_name != "" ? var.key_pair_name : null

  user_data = templatefile("${path.module}/user_data.sh", {
    kafka_bootstrap  = var.kafka_bootstrap
    kafka_api_key    = var.kafka_api_key
    kafka_api_secret = var.kafka_api_secret
  })

  tags = {
    Name = "f1-demo-mq"
  }
}
```

- [ ] **Step 3: Create user_data.sh**

This script installs Docker, starts IBM MQ, installs Python, deploys the race simulator, and starts it. The simulator Python code is embedded via heredoc — see Task 12 for the full simulator implementation.

```bash
#!/bin/bash
# terraform/modules/mq/user_data.sh
set -e

# System setup
yum update -y
yum install -y docker python3 python3-pip
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

# Install Python dependencies
pip3 install pymqi confluent-kafka

# Create simulator directory
mkdir -p /opt/simulator

# Environment variables for Kafka
cat > /opt/simulator/.env << 'ENVEOF'
KAFKA_BOOTSTRAP=${kafka_bootstrap}
KAFKA_API_KEY=${kafka_api_key}
KAFKA_API_SECRET=${kafka_api_secret}
MQ_HOST=localhost
MQ_PORT=1414
MQ_QUEUE_MANAGER=QM1
MQ_CHANNEL=DEV.APP.SVRCONN
MQ_QUEUE=DEV.QUEUE.1
MQ_USER=app
MQ_PASSWORD=passw0rd
ENVEOF

# Simulator Python files will be created by Tasks 10-12
# They are written here via heredoc during implementation
# SIMULATOR_CODE_PLACEHOLDER

# Start simulator (will be added after simulator code is complete)
# cd /opt/simulator && python3 simulator.py &
```

- [ ] **Step 4: Create MQ module outputs**

```hcl
# terraform/modules/mq/outputs.tf
output "mq_public_ip" {
  value = aws_instance.mq.public_ip
}

output "mq_public_dns" {
  value = aws_instance.mq.public_dns
}

output "mq_instance_id" {
  value = aws_instance.mq.id
}

output "mq_connection_string" {
  value = "${aws_instance.mq.public_ip}(1414)"
}
```

- [ ] **Step 5: Commit**

```bash
git add terraform/modules/mq/
git commit -m "feat: terraform MQ module — EC2 + Docker IBM MQ + simulator bootstrap"
```

---

### Task 8: Postgres module (EC2 + Docker Postgres + drivers seed)

**Files:**
- Create: `terraform/modules/postgres/main.tf`
- Create: `terraform/modules/postgres/variables.tf`
- Create: `terraform/modules/postgres/outputs.tf`
- Create: `terraform/modules/postgres/user_data.sh`

- [ ] **Step 1: Create Postgres module**

```hcl
# terraform/modules/postgres/variables.tf
variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.micro"
}

variable "key_pair_name" {
  description = "EC2 key pair name for SSH access (optional)"
  type        = string
  default     = ""
}
```

```hcl
# terraform/modules/postgres/main.tf
data "aws_ami" "amazon_linux" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_security_group" "postgres" {
  name_prefix = "f1-demo-postgres-"
  description = "Security group for Postgres"

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "PostgreSQL"
  }

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "SSH access"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "f1-demo-postgres"
  }
}

resource "aws_instance" "postgres" {
  ami                    = data.aws_ami.amazon_linux.id
  instance_type          = var.instance_type
  vpc_security_group_ids = [aws_security_group.postgres.id]
  key_name               = var.key_pair_name != "" ? var.key_pair_name : null

  user_data = file("${path.module}/user_data.sh")

  tags = {
    Name = "f1-demo-postgres"
  }
}
```

- [ ] **Step 2: Create Postgres user_data.sh with drivers seed**

```bash
#!/bin/bash
# terraform/modules/postgres/user_data.sh
set -e

yum update -y
yum install -y docker
systemctl start docker
systemctl enable docker

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

# Create init SQL
mkdir -p /opt/postgres-init
cat > /opt/postgres-init/01_drivers.sql << 'SQLEOF'
CREATE TABLE drivers (
  car_number INT PRIMARY KEY,
  driver VARCHAR(100) NOT NULL,
  team VARCHAR(100) NOT NULL,
  nationality VARCHAR(50) NOT NULL,
  championships INT DEFAULT 0,
  career_wins INT DEFAULT 0,
  career_podiums INT DEFAULT 0,
  season_points INT DEFAULT 0,
  season_position INT DEFAULT 0
);

INSERT INTO drivers VALUES
  (1,  'Max Eriksson',      'Titan Dynamics',    'Swedish',     4, 63, 111, 175, 1),
  (2,  'Yuki Tanaka',       'Titan Dynamics',    'Japanese',    0, 1,  8,   42,  12),
  (4,  'Luca Novak',        'Apex Motorsport',   'Czech',       1, 12, 35,  148, 2),
  (3,  'Daniel Costa',      'Apex Motorsport',   'Brazilian',   0, 3,  14,  58,  9),
  (44, 'James River',       'River Racing',      'British',     7, 105,202, 120, 3),
  (77, 'Sophie Laurent',    'River Racing',      'French',      0, 0,  5,   35,  14),
  (16, 'Carlos Vega',       'Scuderia Rossa',    'Spanish',     0, 8,  40,  98,  4),
  (55, 'Marco Rossi',       'Scuderia Rossa',    'Italian',     0, 2,  12,  52,  10),
  (63, 'Oliver Walsh',      'Sterling GP',       'British',     0, 3,  18,  85,  5),
  (12, 'Pierre Blanc',      'Sterling GP',       'French',      0, 0,  4,   30,  15),
  (14, 'Fernando Reyes',    'Aston Verde',       'Mexican',     0, 2,  15,  72,  6),
  (18, 'Kimi Lahtinen',     'Aston Verde',       'Finnish',     0, 0,  6,   38,  13),
  (10, 'Theo Martin',       'Alpine Force',      'French',      0, 1,  10,  65,  7),
  (31, 'Oscar Patel',       'Alpine Force',      'Australian',  0, 0,  3,   28,  16),
  (20, 'Kevin Andersen',    'Haas Velocity',     'Danish',      0, 0,  2,   22,  17),
  (27, 'Nico Hoffman',      'Haas Velocity',     'German',      0, 0,  1,   18,  18),
  (24, 'Valtteri Koskinen', 'Sauber Spirit',     'Finnish',     0, 1,  8,   60,  8),
  (23, 'Li Wei',            'Sauber Spirit',     'Chinese',     0, 0,  2,   25,  19),
  (6,  'Alex Nakamura',     'Williams Heritage', 'Japanese',    0, 0,  5,   45,  11),
  (8,  'Logan Mitchell',    'Williams Heritage', 'American',    0, 0,  0,   12,  20),
  (22, 'Liam O''Brien',     'Racing Bulls',      'Irish',       0, 0,  3,   20,  21),
  (21, 'Isack Mbeki',       'Racing Bulls',      'South African',0, 0, 1,   15,  22);
SQLEOF

# Restart Postgres to pick up init SQL
docker restart postgres
```

**Note:** `wal_level=logical` is required for Debezium CDC to work. The init SQL creates and seeds the drivers table on first boot.

- [ ] **Step 3: Create Postgres outputs**

```hcl
# terraform/modules/postgres/outputs.tf
output "postgres_public_ip" {
  value = aws_instance.postgres.public_ip
}

output "postgres_public_dns" {
  value = aws_instance.postgres.public_dns
}

output "postgres_instance_id" {
  value = aws_instance.postgres.id
}

output "postgres_connection_string" {
  value = "postgresql://f1user:f1passw0rd@${aws_instance.postgres.public_ip}:5432/f1demo"
}
```

- [ ] **Step 4: Commit**

```bash
git add terraform/modules/postgres/
git commit -m "feat: terraform postgres module — EC2 + Docker Postgres + 22 drivers seed"
```

---

### Task 9: Tableflow module (S3 + IAM + provider integration)

**Files:**
- Create: `terraform/modules/tableflow/main.tf`
- Create: `terraform/modules/tableflow/variables.tf`
- Create: `terraform/modules/tableflow/outputs.tf`

- [ ] **Step 1: Create the tableflow module**

```hcl
# terraform/modules/tableflow/variables.tf
variable "environment_id" {
  description = "Confluent Cloud environment ID"
  type        = string
}

variable "bucket_name" {
  description = "S3 bucket name prefix for Delta Lake storage"
  type        = string
  default     = "f1-demo-tableflow"
}
```

```hcl
# terraform/modules/tableflow/main.tf
data "aws_caller_identity" "current" {}

resource "random_id" "suffix" {
  byte_length = 4
}

locals {
  bucket_name = "${var.bucket_name}-${random_id.suffix.hex}"
  role_name   = "f1-demo-tableflow-role-${random_id.suffix.hex}"
}

# S3 Bucket
resource "aws_s3_bucket" "tableflow" {
  bucket        = local.bucket_name
  force_destroy = true

  tags = {
    Name = "f1-demo-tableflow"
  }
}

resource "aws_s3_bucket_public_access_block" "tableflow" {
  bucket = aws_s3_bucket.tableflow.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Confluent Provider Integration
resource "confluent_provider_integration" "main" {
  display_name = "f1-demo-aws-integration"

  environment {
    id = var.environment_id
  }

  aws {
    iam_role_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${local.role_name}"
  }
}

# IAM Role with trust policy for Confluent
resource "aws_iam_role" "tableflow" {
  name = local.role_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          AWS = confluent_provider_integration.main.aws[0].iam_role_arn
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "sts:ExternalId" = confluent_provider_integration.main.aws[0].external_id
          }
        }
      }
    ]
  })
}

# IAM Policy with S3 permissions
resource "aws_iam_policy" "tableflow" {
  name = "f1-demo-tableflow-policy-${random_id.suffix.hex}"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation"
        ]
        Resource = aws_s3_bucket.tableflow.arn
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListMultipartUploadParts",
          "s3:AbortMultipartUpload",
          "s3:GetObjectVersion",
          "s3:ListBucketMultipartUploads"
        ]
        Resource = "${aws_s3_bucket.tableflow.arn}/*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "tableflow" {
  role       = aws_iam_role.tableflow.name
  policy_arn = aws_iam_policy.tableflow.arn
}

# Bucket policy allowing the IAM role
resource "aws_s3_bucket_policy" "tableflow" {
  bucket = aws_s3_bucket.tableflow.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.tableflow.arn
        }
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListMultipartUploadParts",
          "s3:AbortMultipartUpload",
          "s3:GetObjectVersion",
          "s3:ListBucketMultipartUploads"
        ]
        Resource = [
          aws_s3_bucket.tableflow.arn,
          "${aws_s3_bucket.tableflow.arn}/*"
        ]
      }
    ]
  })
}
```

```hcl
# terraform/modules/tableflow/outputs.tf
output "s3_bucket_name" {
  value = aws_s3_bucket.tableflow.id
}

output "s3_bucket_arn" {
  value = aws_s3_bucket.tableflow.arn
}

output "iam_role_arn" {
  value = aws_iam_role.tableflow.arn
}

output "provider_integration_id" {
  value = confluent_provider_integration.main.id
}
```

- [ ] **Step 2: Validate all Terraform**

```bash
cd terraform && terraform validate
```

Expected: "Success! The configuration is valid."

- [ ] **Step 3: Commit**

```bash
git add terraform/modules/tableflow/
git commit -m "feat: terraform tableflow module — S3 + IAM role + provider integration"
```

---

## Phase 3: Data

### Task 10: Drivers dataset

**Files:**
- Create: `data/drivers.csv`
- Create: `data/drivers_seed.sql`

- [ ] **Step 1: Create drivers.csv**

```csv
car_number,driver,team,nationality,championships,career_wins,career_podiums,season_points,season_position
1,Max Eriksson,Titan Dynamics,Swedish,4,63,111,175,1
2,Yuki Tanaka,Titan Dynamics,Japanese,0,1,8,42,12
4,Luca Novak,Apex Motorsport,Czech,1,12,35,148,2
3,Daniel Costa,Apex Motorsport,Brazilian,0,3,14,58,9
44,James River,River Racing,British,7,105,202,120,3
77,Sophie Laurent,River Racing,French,0,0,5,35,14
16,Carlos Vega,Scuderia Rossa,Spanish,0,8,40,98,4
55,Marco Rossi,Scuderia Rossa,Italian,0,2,12,52,10
63,Oliver Walsh,Sterling GP,British,0,3,18,85,5
12,Pierre Blanc,Sterling GP,French,0,0,4,30,15
14,Fernando Reyes,Aston Verde,Mexican,0,2,15,72,6
18,Kimi Lahtinen,Aston Verde,Finnish,0,0,6,38,13
10,Theo Martin,Alpine Force,French,0,1,10,65,7
31,Oscar Patel,Alpine Force,Australian,0,0,3,28,16
20,Kevin Andersen,Haas Velocity,Danish,0,0,2,22,17
27,Nico Hoffman,Haas Velocity,German,0,0,1,18,18
24,Valtteri Koskinen,Sauber Spirit,Finnish,0,1,8,60,8
23,Li Wei,Sauber Spirit,Chinese,0,0,2,25,19
6,Alex Nakamura,Williams Heritage,Japanese,0,0,5,45,11
8,Logan Mitchell,Williams Heritage,American,0,0,0,12,20
22,Liam O'Brien,Racing Bulls,Irish,0,0,3,20,21
21,Isack Mbeki,Racing Bulls,South African,0,0,1,15,22
```

- [ ] **Step 2: Create drivers_seed.sql**

This is the same SQL from the Postgres user_data.sh — a standalone reference file for manual use.

```sql
-- data/drivers_seed.sql
-- Same content as terraform/modules/postgres/user_data.sh SQL block
-- See Task 8 Step 2 for the full INSERT statements
```

- [ ] **Step 3: Commit**

```bash
git add data/
git commit -m "feat: 22 fictional drivers dataset — CSV + Postgres seed SQL"
```

---

## Phase 4: Race Simulator

### Task 11: Simulator — drivers definition + config

**Files:**
- Create: `datagen/requirements.txt`
- Create: `datagen/config.py`
- Create: `datagen/drivers.py`

- [ ] **Step 1: Create requirements.txt**

```
pymqi>=1.12.0
confluent-kafka>=2.3.0
```

- [ ] **Step 2: Create config.py**

```python
# datagen/config.py
"""Connection configuration for MQ and Kafka. Reads from environment variables."""
import os

# IBM MQ (for race standings → FIA feed)
MQ_HOST = os.environ.get("MQ_HOST", "localhost")
MQ_PORT = int(os.environ.get("MQ_PORT", "1414"))
MQ_QUEUE_MANAGER = os.environ.get("MQ_QUEUE_MANAGER", "QM1")
MQ_CHANNEL = os.environ.get("MQ_CHANNEL", "DEV.APP.SVRCONN")
MQ_QUEUE = os.environ.get("MQ_QUEUE", "DEV.QUEUE.1")
MQ_USER = os.environ.get("MQ_USER", "app")
MQ_PASSWORD = os.environ.get("MQ_PASSWORD", "passw0rd")

# Kafka (for car telemetry → direct produce)
KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")
KAFKA_API_KEY = os.environ.get("KAFKA_API_KEY", "")
KAFKA_API_SECRET = os.environ.get("KAFKA_API_SECRET", "")
KAFKA_TOPIC = os.environ.get("KAFKA_TOPIC", "car-telemetry")

# Race configuration
TOTAL_LAPS = 57
SECONDS_PER_LAP = 10
TELEMETRY_INTERVAL_SEC = 2  # produce telemetry every 2 seconds
OUR_CAR_NUMBER = 44
```

- [ ] **Step 3: Create drivers.py with full 22-driver grid**

```python
# datagen/drivers.py
"""Full 22-driver grid with starting positions and pit strategies."""

GRID = [
    {"car_number": 1,  "driver": "Max Eriksson",      "team": "Titan Dynamics",    "start_position": 1,  "pit_lap": 18, "pit_tire": "MEDIUM", "start_tire": "SOFT"},
    {"car_number": 2,  "driver": "Yuki Tanaka",       "team": "Titan Dynamics",    "start_position": 11, "pit_lap": 20, "pit_tire": "MEDIUM", "start_tire": "SOFT"},
    {"car_number": 4,  "driver": "Luca Novak",        "team": "Apex Motorsport",   "start_position": 2,  "pit_lap": 22, "pit_tire": "MEDIUM", "start_tire": "SOFT"},
    {"car_number": 3,  "driver": "Daniel Costa",      "team": "Apex Motorsport",   "start_position": 10, "pit_lap": 19, "pit_tire": "HARD",   "start_tire": "SOFT"},
    {"car_number": 44, "driver": "James River",       "team": "River Racing",      "start_position": 3,  "pit_lap": 33, "pit_tire": "MEDIUM", "start_tire": "SOFT"},
    {"car_number": 77, "driver": "Sophie Laurent",    "team": "River Racing",      "start_position": 13, "pit_lap": 25, "pit_tire": "MEDIUM", "start_tire": "SOFT"},
    {"car_number": 16, "driver": "Carlos Vega",       "team": "Scuderia Rossa",    "start_position": 4,  "pit_lap": 15, "pit_tire": "MEDIUM", "start_tire": "SOFT"},
    {"car_number": 55, "driver": "Marco Rossi",       "team": "Scuderia Rossa",    "start_position": 9,  "pit_lap": 21, "pit_tire": "HARD",   "start_tire": "SOFT"},
    {"car_number": 63, "driver": "Oliver Walsh",      "team": "Sterling GP",       "start_position": 5,  "pit_lap": 20, "pit_tire": "MEDIUM", "start_tire": "SOFT"},
    {"car_number": 12, "driver": "Pierre Blanc",      "team": "Sterling GP",       "start_position": 14, "pit_lap": 24, "pit_tire": "HARD",   "start_tire": "MEDIUM"},
    {"car_number": 14, "driver": "Fernando Reyes",    "team": "Aston Verde",       "start_position": 6,  "pit_lap": 35, "pit_tire": "HARD",   "start_tire": "MEDIUM"},
    {"car_number": 18, "driver": "Kimi Lahtinen",     "team": "Aston Verde",       "start_position": 12, "pit_lap": 23, "pit_tire": "MEDIUM", "start_tire": "SOFT"},
    {"car_number": 10, "driver": "Theo Martin",       "team": "Alpine Force",      "start_position": 7,  "pit_lap": 17, "pit_tire": "HARD",   "start_tire": "SOFT"},
    {"car_number": 31, "driver": "Oscar Patel",       "team": "Alpine Force",      "start_position": 15, "pit_lap": 26, "pit_tire": "MEDIUM", "start_tire": "SOFT"},
    {"car_number": 20, "driver": "Kevin Andersen",    "team": "Haas Velocity",     "start_position": 16, "pit_lap": 28, "pit_tire": "HARD",   "start_tire": "MEDIUM"},
    {"car_number": 27, "driver": "Nico Hoffman",      "team": "Haas Velocity",     "start_position": 17, "pit_lap": 30, "pit_tire": "HARD",   "start_tire": "MEDIUM"},
    {"car_number": 24, "driver": "Valtteri Koskinen", "team": "Sauber Spirit",     "start_position": 8,  "pit_lap": 16, "pit_tire": "MEDIUM", "start_tire": "SOFT"},
    {"car_number": 23, "driver": "Li Wei",            "team": "Sauber Spirit",     "start_position": 18, "pit_lap": 27, "pit_tire": "HARD",   "start_tire": "MEDIUM"},
    {"car_number": 6,  "driver": "Alex Nakamura",     "team": "Williams Heritage", "start_position": 19, "pit_lap": 29, "pit_tire": "HARD",   "start_tire": "MEDIUM"},
    {"car_number": 8,  "driver": "Logan Mitchell",    "team": "Williams Heritage", "start_position": 20, "pit_lap": 32, "pit_tire": "HARD",   "start_tire": "MEDIUM"},
    {"car_number": 22, "driver": "Liam O'Brien",      "team": "Racing Bulls",      "start_position": 21, "pit_lap": 25, "pit_tire": "HARD",   "start_tire": "MEDIUM"},
    {"car_number": 21, "driver": "Isack Mbeki",       "team": "Racing Bulls",      "start_position": 22, "pit_lap": 31, "pit_tire": "HARD",   "start_tire": "MEDIUM"},
]

def get_driver(car_number):
    """Look up a driver by car number."""
    for d in GRID:
        if d["car_number"] == car_number:
            return d
    return None
```

- [ ] **Step 4: Commit**

```bash
git add datagen/requirements.txt datagen/config.py datagen/drivers.py
git commit -m "feat: race simulator config — drivers grid, MQ/Kafka settings"
```

---

### Task 12: Simulator — telemetry + race script

**Files:**
- Create: `datagen/telemetry.py`
- Create: `datagen/race_script.py`

- [ ] **Step 1: Write tests for telemetry generation**

Create `datagen/tests/__init__.py` (empty) and `datagen/tests/test_telemetry.py`:

```python
# datagen/tests/test_telemetry.py
"""Tests for telemetry metric generation."""
from datagen.telemetry import generate_telemetry


def test_telemetry_lap1_normal():
    """Fresh tires at lap 1 — all temps in normal range."""
    data = generate_telemetry(lap=1, tire_age=1, tire_compound="SOFT", post_pit=False)
    assert 90 <= data["tire_temp_fl_c"] <= 100
    assert 90 <= data["tire_temp_fr_c"] <= 100
    assert data["fuel_remaining_kg"] > 40


def test_telemetry_lap32_anomaly():
    """Lap 32 — tire_temp_fl must spike above 140."""
    data = generate_telemetry(lap=32, tire_age=32, tire_compound="SOFT", post_pit=False)
    assert data["tire_temp_fl_c"] >= 140, "Front-left tire temp must spike at lap 32"
    assert data["tire_temp_fr_c"] < 120, "Other tire temps must stay normal"


def test_telemetry_after_pit():
    """After pit stop — fresh tires, temps drop back to normal."""
    data = generate_telemetry(lap=35, tire_age=2, tire_compound="MEDIUM", post_pit=True)
    assert 90 <= data["tire_temp_fl_c"] <= 100
    assert data["fuel_remaining_kg"] < 40


def test_fuel_decreases_linearly():
    """Fuel should decrease by ~0.7 kg per lap."""
    lap5 = generate_telemetry(lap=5, tire_age=5, tire_compound="SOFT", post_pit=False)
    lap10 = generate_telemetry(lap=10, tire_age=10, tire_compound="SOFT", post_pit=False)
    assert lap5["fuel_remaining_kg"] > lap10["fuel_remaining_kg"]


def test_no_anomaly_on_other_metrics():
    """At lap 32, only tire_temp_fl spikes. Engine, brakes, battery, pressures stay normal."""
    data = generate_telemetry(lap=32, tire_age=32, tire_compound="SOFT", post_pit=False)
    assert 115 <= data["engine_temp_c"] <= 125
    assert 19 <= data["tire_pressure_fl_psi"] <= 23
    assert 30 <= data["battery_charge_pct"] <= 85
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /path/to/F1 && python -m pytest datagen/tests/test_telemetry.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'datagen.telemetry'`

- [ ] **Step 3: Implement telemetry.py**

```python
# datagen/telemetry.py
"""Generates realistic car telemetry metrics with a single anomaly at lap 32."""
import random

# Starting values
FUEL_START_KG = 44.0
FUEL_BURN_PER_LAP = 0.7

# Tire temperature baselines and gradients
TIRE_TEMP_BASE = {"fl": 95.0, "fr": 95.0, "rl": 93.0, "rr": 93.0}
TIRE_TEMP_GRADIENT_PER_LAP = {"fl": 0.42, "fr": 0.45, "rl": 0.39, "rr": 0.35}

# Tire pressure baselines
TIRE_PRESSURE_BASE = {"fl": 22.0, "fr": 22.0, "rl": 21.0, "rr": 21.0}
TIRE_PRESSURE_DROP_PER_LAP = 0.05

ANOMALY_LAP = 32
ANOMALY_TEMP = 145.0


def _noise(amplitude=0.5):
    return random.uniform(-amplitude, amplitude)


def generate_telemetry(lap, tire_age, tire_compound, post_pit):
    """Generate one telemetry reading for car #44.

    Args:
        lap: Current race lap (1-57)
        tire_age: Laps on current tire set (resets after pit)
        tire_compound: SOFT, MEDIUM, or HARD
        post_pit: Whether car has pitted (resets tire baselines)
    """
    # Tire temperatures — gradual rise with tire age
    tire_temps = {}
    for pos in ["fl", "fr", "rl", "rr"]:
        base = TIRE_TEMP_BASE[pos]
        gradient = TIRE_TEMP_GRADIENT_PER_LAP[pos]
        tire_temps[pos] = base + (gradient * tire_age) + _noise(1.0)

    # ANOMALY: front-left tire temp spikes at lap 32 (only if pre-pit)
    if lap == ANOMALY_LAP and not post_pit:
        tire_temps["fl"] = ANOMALY_TEMP + _noise(2.0)

    # Tire pressures — slow linear decline with tire age
    tire_pressures = {}
    for pos in ["fl", "fr", "rl", "rr"]:
        base = TIRE_PRESSURE_BASE[pos]
        tire_pressures[pos] = base - (TIRE_PRESSURE_DROP_PER_LAP * tire_age) + _noise(0.1)

    # Engine temp — stable with minor fluctuation
    engine_temp = 120.0 + _noise(2.0)

    # Brake temps — cyclical (simulates braking zones), consistent pattern
    brake_base = 450.0
    brake_temps = {
        "fl": brake_base + _noise(25.0),
        "fr": brake_base + 10 + _noise(25.0),
    }

    # Battery — regular charge/discharge cycle
    battery_base = 60.0
    battery_cycle = 20.0 * (0.5 + 0.5 * (((lap * 3 + tire_age) % 7) / 7.0))
    battery_pct = battery_base + battery_cycle + _noise(2.0)
    battery_pct = max(35.0, min(85.0, battery_pct))

    # Fuel — perfectly linear decrease
    fuel = FUEL_START_KG - (FUEL_BURN_PER_LAP * lap) + _noise(0.05)
    fuel = max(0.5, fuel)

    # Speed, throttle, brake — realistic ranges
    speed = 280.0 + random.uniform(0, 40)
    throttle = random.uniform(60, 100)
    brake = random.uniform(0, 15) if random.random() < 0.3 else 0.0

    return {
        "tire_temp_fl_c": round(tire_temps["fl"], 1),
        "tire_temp_fr_c": round(tire_temps["fr"], 1),
        "tire_temp_rl_c": round(tire_temps["rl"], 1),
        "tire_temp_rr_c": round(tire_temps["rr"], 1),
        "tire_pressure_fl_psi": round(tire_pressures["fl"], 1),
        "tire_pressure_fr_psi": round(tire_pressures["fr"], 1),
        "tire_pressure_rl_psi": round(tire_pressures["rl"], 1),
        "tire_pressure_rr_psi": round(tire_pressures["rr"], 1),
        "engine_temp_c": round(engine_temp, 1),
        "brake_temp_fl_c": round(brake_temps["fl"], 1),
        "brake_temp_fr_c": round(brake_temps["fr"], 1),
        "battery_charge_pct": round(battery_pct, 1),
        "fuel_remaining_kg": round(fuel, 1),
        "drs_active": random.random() > 0.6,
        "speed_kph": round(speed, 1),
        "throttle_pct": round(throttle, 1),
        "brake_pct": round(brake, 1),
    }
```

- [ ] **Step 4: Run telemetry tests**

```bash
cd /path/to/F1 && python -m pytest datagen/tests/test_telemetry.py -v
```

Expected: All 5 tests PASS.

- [ ] **Step 5: Write tests for race script**

```python
# datagen/tests/test_race_script.py
"""Tests for race state management."""
from datagen.race_script import RaceState
from datagen.drivers import GRID


def test_initial_positions():
    """All 22 cars start in their grid positions."""
    state = RaceState(GRID)
    standings = state.get_standings()
    assert len(standings) == 22
    assert standings[0]["position"] == 1


def test_car44_starts_p3():
    """James River starts in P3."""
    state = RaceState(GRID)
    car44 = state.get_car(44)
    assert car44["position"] == 3


def test_pit_stop_changes_tire():
    """After pitting, tire compound and age reset."""
    state = RaceState(GRID)
    for _ in range(18):
        state.advance_lap()
    car1 = state.get_car(1)
    assert car1["tire_compound"] == "MEDIUM"
    assert car1["tire_age_laps"] < 5


def test_car44_drops_to_p8_by_lap32():
    """James River drops from P3 to P8 by lap 32 due to tire degradation."""
    state = RaceState(GRID)
    for _ in range(32):
        state.advance_lap()
    car44 = state.get_car(44)
    assert car44["position"] == 8


def test_car44_recovers_to_p3_by_lap57():
    """After pit at lap 33, James River recovers to P3 by end of race."""
    state = RaceState(GRID)
    for _ in range(57):
        state.advance_lap()
    car44 = state.get_car(44)
    assert car44["position"] == 3
```

- [ ] **Step 6: Run tests to verify they fail**

```bash
cd /path/to/F1 && python -m pytest datagen/tests/test_race_script.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 7: Implement race_script.py**

**Position model: Cumulative race time.** Each car tracks a running `total_race_time`. Each lap adds the car's lap time (based on tire compound + age + driver skill). If pitting, that lap also adds 23 seconds. Positions = sorted by lowest total time. This is how real F1 works.

**Driver skill:** A fixed `qualifying_delta` per driver (0.0 for P1, 0.15 per grid slot). This represents inherent pace differences and stays constant — no feedback loops.

**How car 44 drops to P8:** On 32-lap-old softs, car 44's lap time is ~92.9s while pitted rivals on fresh mediums are doing ~91.2s. Over 15-20 laps, that ~1.7s/lap deficit accumulates to 25-35 seconds — enough for 5 cars to overtake despite the 23s pit stop cost.

**How car 44 recovers to P3:** Fresh mediums at lap 33 give ~91.3s laps vs competitors on 15-20 lap old mediums doing ~91.8-92.2s. The ~0.5-0.9s/lap advantage over 24 laps claws back the deficit, plus late-pitters (Reyes at lap 35) drop behind.

```python
# datagen/race_script.py
"""Semi-scripted race state management for 22 cars across 57 laps.

Uses a CUMULATIVE RACE TIME model (like real F1):
- Each car accumulates total_race_time across laps
- Positions are determined by sorting all cars by total_race_time (lowest = P1)
- Overtakes happen naturally when a faster car's cumulative time undercuts a slower car's
- Pit stops add ~23 seconds to one lap but give fresh tires (faster subsequent laps)
"""

# Tire degradation: seconds added to lap time per lap of tire age
TIRE_DEGRADATION = {
    "SOFT": 0.08,    # Fast initially but degrades quickly — 32 laps = +2.56s
    "MEDIUM": 0.04,  # Balanced — 25 laps = +1.00s
    "HARD": 0.02,    # Slow but durable — 40 laps = +0.80s
}

# Base lap time by compound (lower = faster)
TIRE_BASE_PACE = {
    "SOFT": 90.5,
    "MEDIUM": 91.0,
    "HARD": 91.8,
}

# Qualifying gap per grid position (seconds behind pole)
QUALIFYING_GAP_PER_POSITION = 0.15

PIT_STOP_TIME_SEC = 23.0


class CarState:
    def __init__(self, car_info):
        self.car_number = car_info["car_number"]
        self.driver = car_info["driver"]
        self.team = car_info["team"]
        self.position = car_info["start_position"]
        self.tire_compound = car_info["start_tire"]
        self.tire_age_laps = 0
        self.pit_stops = 0
        self.pit_lap = car_info["pit_lap"]
        self.pit_tire = car_info["pit_tire"]
        self.in_pit_lane = False
        self.gap_to_leader_sec = 0.0
        self.gap_to_ahead_sec = 0.0
        self.last_lap_time_sec = TIRE_BASE_PACE[self.tire_compound]
        self.has_pitted = False
        # Fixed per driver — represents inherent qualifying pace
        self.qualifying_delta = (car_info["start_position"] - 1) * QUALIFYING_GAP_PER_POSITION
        # Starting gap from pole (simulates grid spread at race start)
        self.total_race_time = (car_info["start_position"] - 1) * 0.5

    def lap_time(self):
        """Calculate current lap time based on tire compound, age, and driver skill.

        Returns seconds for this lap. Does NOT include pit stop time
        (that's added separately in advance_lap).
        """
        base = TIRE_BASE_PACE[self.tire_compound]
        degradation = TIRE_DEGRADATION[self.tire_compound] * self.tire_age_laps
        return base + degradation + self.qualifying_delta

    def pit(self, new_compound):
        self.tire_compound = new_compound
        self.tire_age_laps = 0
        self.pit_stops += 1
        self.in_pit_lane = True
        self.has_pitted = True


class RaceState:
    def __init__(self, grid):
        self.cars = [CarState(d) for d in grid]
        self.current_lap = 0
        # Sort by starting position (= by total_race_time since it's proportional)
        self.cars.sort(key=lambda c: c.total_race_time)
        for i, car in enumerate(self.cars):
            car.position = i + 1
        self._update_gaps()

    def advance_lap(self):
        """Advance the race by one lap.

        1. Process pit stops for this lap
        2. Increment tire age
        3. Calculate each car's lap time and add to total_race_time
        4. Add pit stop penalty if pitting this lap
        5. Re-sort by total_race_time to get new positions
        6. Update gaps
        """
        self.current_lap += 1

        # Clear pit lane status from previous lap
        for car in self.cars:
            car.in_pit_lane = False

        # Process pit stops
        for car in self.cars:
            if self.current_lap == car.pit_lap and not car.has_pitted:
                car.pit(car.pit_tire)

        # Increment tire age and calculate lap times
        for car in self.cars:
            car.tire_age_laps += 1
            car.last_lap_time_sec = car.lap_time()

            # Add pit stop time penalty on the lap the car pits
            effective_lap_time = car.last_lap_time_sec
            if car.in_pit_lane:
                effective_lap_time += PIT_STOP_TIME_SEC

            car.total_race_time += effective_lap_time

        # Sort by cumulative race time — this determines positions
        self.cars.sort(key=lambda c: c.total_race_time)
        for i, car in enumerate(self.cars):
            car.position = i + 1

        self._update_gaps()

    def _update_gaps(self):
        """Update gap_to_leader and gap_to_ahead based on total_race_time."""
        if not self.cars:
            return
        leader_time = self.cars[0].total_race_time
        for i, car in enumerate(self.cars):
            car.gap_to_leader_sec = round(car.total_race_time - leader_time, 1)
            if i == 0:
                car.gap_to_ahead_sec = 0.0
            else:
                car.gap_to_ahead_sec = round(
                    car.total_race_time - self.cars[i - 1].total_race_time, 1
                )

    def get_car(self, car_number):
        """Get current state for a specific car."""
        for car in self.cars:
            if car.car_number == car_number:
                return {
                    "car_number": car.car_number,
                    "driver": car.driver,
                    "team": car.team,
                    "lap": self.current_lap,
                    "position": car.position,
                    "gap_to_leader_sec": car.gap_to_leader_sec,
                    "gap_to_ahead_sec": car.gap_to_ahead_sec,
                    "last_lap_time_sec": round(car.last_lap_time_sec, 3),
                    "pit_stops": car.pit_stops,
                    "tire_compound": car.tire_compound,
                    "tire_age_laps": car.tire_age_laps,
                    "in_pit_lane": car.in_pit_lane,
                }
        return None

    def get_standings(self):
        """Get all 22 cars sorted by position."""
        return [self.get_car(c.car_number) for c in self.cars]
```

**Tuning guide:** The narrative (P3→P8→P3 for car 44) depends on the interplay between `TIRE_DEGRADATION` rates, `QUALIFYING_GAP_PER_POSITION`, and the pit lap timing in `drivers.py`. The tests validate the key beats. If positions are off:
- Car 44 doesn't drop far enough → increase `TIRE_DEGRADATION["SOFT"]` or decrease it for other compounds
- Car 44 doesn't recover → decrease `TIRE_DEGRADATION["MEDIUM"]` or increase other cars' degradation
- Car 44 drops too far → decrease `QUALIFYING_GAP_PER_POSITION` (tighter field = more position changes from small pace differences)

- [ ] **Step 8: Run race script tests**

```bash
cd /path/to/F1 && python -m pytest datagen/tests/test_race_script.py -v
```

Expected: All 5 tests PASS. If `test_car44_drops_to_p8_by_lap32` or `test_car44_recovers_to_p3_by_lap57` fails, tune the degradation rates until the narrative works.

- [ ] **Step 9: Commit**

```bash
git add datagen/telemetry.py datagen/race_script.py datagen/tests/
git commit -m "feat: race simulator — telemetry generation + race state management + tests"
```

---

### Task 13: Simulator — main orchestrator

**Files:**
- Create: `datagen/simulator.py`

- [ ] **Step 1: Implement the main simulator**

```python
# datagen/simulator.py
"""Main race simulator. Produces car telemetry to Kafka and race standings to MQ."""
import json
import time
import logging
from datetime import datetime, timezone

from datagen import config
from datagen.drivers import GRID
from datagen.race_script import RaceState
from datagen.telemetry import generate_telemetry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def create_kafka_producer():
    """Create Kafka producer for car telemetry."""
    from confluent_kafka import Producer
    conf = {
        "bootstrap.servers": config.KAFKA_BOOTSTRAP,
        "security.protocol": "SASL_SSL",
        "sasl.mechanisms": "PLAIN",
        "sasl.username": config.KAFKA_API_KEY,
        "sasl.password": config.KAFKA_API_SECRET,
    }
    return Producer(conf)


def create_mq_connection():
    """Create MQ connection for race standings."""
    import pymqi
    conn_info = f"{config.MQ_HOST}({config.MQ_PORT})"
    qmgr = pymqi.connect(
        config.MQ_QUEUE_MANAGER,
        config.MQ_CHANNEL,
        conn_info,
        config.MQ_USER,
        config.MQ_PASSWORD,
    )
    queue = pymqi.Queue(qmgr, config.MQ_QUEUE)
    return qmgr, queue


def run_race():
    """Run the full race simulation."""
    logger.info("Starting F1 race simulation — Silverstone Grand Prix")
    logger.info(f"Total laps: {config.TOTAL_LAPS}, seconds per lap: {config.SECONDS_PER_LAP}")

    # Initialize
    producer = create_kafka_producer()
    qmgr, mq_queue = create_mq_connection()
    race = RaceState(GRID)

    car44_tire_age = 0
    car44_tire_compound = "SOFT"
    car44_post_pit = False

    try:
        for lap in range(1, config.TOTAL_LAPS + 1):
            lap_start = time.time()
            logger.info(f"=== LAP {lap}/{config.TOTAL_LAPS} ===")

            # Advance race state
            race.advance_lap()

            # Update car 44 tire tracking
            car44_state = race.get_car(config.OUR_CAR_NUMBER)
            car44_tire_compound = car44_state["tire_compound"]
            car44_tire_age = car44_state["tire_age_laps"]
            if car44_state["pit_stops"] > 0 and car44_tire_age <= 2:
                car44_post_pit = True

            # Produce race standings to MQ (22 messages, one per car)
            standings = race.get_standings()
            for standing in standings:
                standing["timestamp"] = datetime.now(timezone.utc).isoformat()
                msg = json.dumps(standing)
                mq_queue.put(msg.encode("utf-8"))

            logger.info(f"  Standings sent to MQ ({len(standings)} cars)")
            logger.info(f"  Car 44: P{car44_state['position']}, {car44_tire_compound} (age {car44_tire_age})")

            # Produce car telemetry to Kafka (multiple readings per lap)
            readings_per_lap = config.SECONDS_PER_LAP // config.TELEMETRY_INTERVAL_SEC
            for i in range(readings_per_lap):
                telemetry = generate_telemetry(
                    lap=lap,
                    tire_age=car44_tire_age,
                    tire_compound=car44_tire_compound,
                    post_pit=car44_post_pit,
                )
                telemetry["car_number"] = config.OUR_CAR_NUMBER
                telemetry["lap"] = lap
                telemetry["event_time"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds") + "Z"

                producer.produce(
                    config.KAFKA_TOPIC,
                    key=str(config.OUR_CAR_NUMBER).encode("utf-8"),
                    value=json.dumps(telemetry).encode("utf-8"),
                )
                producer.poll(0)

                # Sleep between telemetry readings
                elapsed = time.time() - lap_start
                target = (i + 1) * config.TELEMETRY_INTERVAL_SEC
                sleep_time = max(0, target - elapsed)
                time.sleep(sleep_time)

            # Ensure all messages are delivered
            producer.flush(timeout=5)

            # Pace lap timing
            elapsed = time.time() - lap_start
            remaining = config.SECONDS_PER_LAP - elapsed
            if remaining > 0:
                time.sleep(remaining)

        logger.info("=== RACE COMPLETE ===")
        car44_final = race.get_car(config.OUR_CAR_NUMBER)
        logger.info(f"Car 44 final position: P{car44_final['position']}")

    finally:
        producer.flush()
        mq_queue.close()
        qmgr.disconnect()


if __name__ == "__main__":
    run_race()
```

- [ ] **Step 2: Commit**

```bash
git add datagen/simulator.py
git commit -m "feat: race simulator main — orchestrates telemetry + standings production"
```

---

## Phase 5: Demo Reference Files

### Task 14: Demo reference SQL + connector configs

**Files:**
- Create: `demo-reference/enrichment_anomaly.sql`
- Create: `demo-reference/streaming_agent.sql`
- Create: `demo-reference/mq_connector_config.json`
- Create: `demo-reference/cdc_connector_config.json`

- [ ] **Step 1: Create enrichment_anomaly.sql**

Copy the full enrichment SQL from `CLAUDE.md` section "Flink Job 1: Enrichment + Anomaly Detection". This is the reference file that MCP will scaffold during the demo.

**Important:** Every `AI_DETECT_ANOMALIES` call uses asymmetric bounds:
```sql
JSON_OBJECT('upperBoundConfidencePercentage' VALUE 99.0, 'lowerBoundConfidencePercentage' VALUE 99.9)
```
This catches overheating spikes (upper) while ignoring gradual declines like fuel/pressure (lower).

```sql
-- demo-reference/enrichment_anomaly.sql
-- Job 1: Enrichment + Anomaly Detection
-- Deployed via DBT as a streaming_table materialization
-- Full SQL is in CLAUDE.md — copy verbatim from there
```

- [ ] **Step 2: Create streaming_agent.sql**

```sql
-- demo-reference/streaming_agent.sql
-- Job 2: Streaming Agent
-- Full SQL is in CLAUDE.md — copy verbatim from there
-- Includes: CREATE CONNECTION, CREATE MODEL, CREATE TOOL, CREATE AGENT, INSERT INTO with AI_RUN_AGENT
```

- [ ] **Step 3: Create MQ connector config**

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
    "mq.transport.type": "client",
    "mq.queue.manager": "QM1",
    "mq.channel": "DEV.APP.SVRCONN",
    "mq.queue": "DEV.QUEUE.1",
    "mq.user.name": "app",
    "mq.password": "passw0rd",
    "jms.destination.type": "queue",
    "jms.destination.name": "DEV.QUEUE.1",
    "kafka.api.key": "<KAFKA_API_KEY>",
    "kafka.api.secret": "<KAFKA_API_SECRET>",
    "output.data.format": "JSON",
    "tasks.max": "1"
  }
}
```

- [ ] **Step 4: Create CDC connector config**

```json
{
  "name": "f1-postgres-cdc",
  "config": {
    "connector.class": "PostgresCdcSource",
    "kafka.auth.mode": "SERVICE_ACCOUNT",
    "kafka.service.account.id": "<SERVICE_ACCOUNT_ID>",
    "database.hostname": "<POSTGRES_PUBLIC_IP>",
    "database.port": "5432",
    "database.user": "f1user",
    "database.password": "f1passw0rd",
    "database.dbname": "f1demo",
    "database.server.name": "f1demo",
    "table.include.list": "public.drivers",
    "output.data.format": "JSON",
    "tasks.max": "1",
    "database.sslmode": "disable"
  }
}
```

- [ ] **Step 5: Commit**

```bash
git add demo-reference/
git commit -m "feat: demo reference — enrichment SQL, agent SQL, connector configs"
```

---

## Phase 6: Operational

### Task 15: Setup and teardown scripts

**Files:**
- Create: `scripts/setup.sh`
- Create: `scripts/teardown.sh`

- [ ] **Step 1: Create setup.sh**

```bash
#!/bin/bash
# scripts/setup.sh — Deploy all infrastructure and start race
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TERRAFORM_DIR="$SCRIPT_DIR/../terraform"

echo "========================================="
echo "  F1 Pit Wall AI Demo — Setup"
echo "========================================="

# Terraform
cd "$TERRAFORM_DIR"
terraform init
terraform apply -auto-approve

# Extract outputs
echo ""
echo "========================================="
echo "  Deployment Complete"
echo "========================================="

echo ""
echo "Environment ID:  $(terraform output -raw environment_id)"
echo "Cluster ID:      $(terraform output -raw cluster_id)"
echo "Bootstrap:       $(terraform output -raw cluster_bootstrap)"
echo ""
echo "MQ Public IP:    $(terraform output -raw mq_public_ip)"
echo "MQ Connection:   $(terraform output -raw mq_connection_string)"
echo "MQ Console:      https://$(terraform output -raw mq_public_ip):9443/ibmmq/console"
echo ""
echo "Postgres IP:     $(terraform output -raw postgres_public_ip)"
echo "Postgres Conn:   $(terraform output -raw postgres_connection_string)"
echo ""
echo "Tableflow S3:    $(terraform output -raw tableflow_s3_bucket)"
echo "Tableflow IAM:   $(terraform output -raw tableflow_iam_role_arn)"
echo ""
echo "========================================="
echo "  Next Steps"
echo "========================================="
echo ""
echo "1. Race simulator is running on the MQ EC2 instance"
echo "2. Open Claude Desktop with MCP to begin the demo"
echo "3. See CLAUDE.md for demo recording instructions"
echo ""

# Export for local use
export MQ_HOST=$(terraform output -raw mq_public_ip)
export KAFKA_BOOTSTRAP=$(terraform output -raw cluster_bootstrap)
export KAFKA_API_KEY=$(terraform output -raw app_api_key)
export KAFKA_API_SECRET=$(terraform output -raw app_api_secret)
```

- [ ] **Step 2: Create teardown.sh**

```bash
#!/bin/bash
# scripts/teardown.sh — Destroy all infrastructure
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TERRAFORM_DIR="$SCRIPT_DIR/../terraform"

echo "========================================="
echo "  F1 Pit Wall AI Demo — Teardown"
echo "========================================="
echo ""
echo "This will destroy ALL demo infrastructure."
echo ""

cd "$TERRAFORM_DIR"
terraform destroy -auto-approve

echo ""
echo "========================================="
echo "  Teardown Complete"
echo "========================================="
```

- [ ] **Step 3: Make scripts executable**

```bash
chmod +x scripts/setup.sh scripts/teardown.sh
```

- [ ] **Step 4: Commit**

```bash
git add scripts/
git commit -m "feat: setup/teardown scripts — terraform apply/destroy with output export"
```

---

### Task 16: Update MQ user_data.sh with simulator code

After the simulator is tested locally (Tasks 11-13), embed the Python code into the MQ user_data.sh so it auto-starts with Terraform.

**Files:**
- Modify: `terraform/modules/mq/user_data.sh`

- [ ] **Step 1: Embed simulator Python files into user_data.sh**

Add heredoc blocks after the `# SIMULATOR_CODE_PLACEHOLDER` comment to write all Python files (`config.py`, `drivers.py`, `telemetry.py`, `race_script.py`, `simulator.py`) to `/opt/simulator/` on the EC2 instance.

- [ ] **Step 2: Add simulator auto-start**

Add to the end of user_data.sh:

```bash
# Load environment variables and start simulator
cd /opt/simulator
set -a && source .env && set +a
nohup python3 -m datagen.simulator > /var/log/simulator.log 2>&1 &
echo "Race simulator started — PID: $!"
```

- [ ] **Step 3: Test by running terraform plan**

```bash
cd terraform && terraform plan
```

Expected: Plan shows the MQ EC2 instance with updated user_data.

- [ ] **Step 4: Commit**

```bash
git add terraform/modules/mq/user_data.sh
git commit -m "feat: embed race simulator in MQ user_data — auto-starts with terraform"
```

---

## Data Generator — Complete Behavior Reference

### Overview

The race simulator is a **stateful, semi-scripted Python program** that runs on the MQ EC2 instance. It simulates a 57-lap F1 race at Silverstone, producing two data streams simultaneously.

### Two Outputs

| Output | Destination | Key | Frequency | Scope |
|---|---|---|---|---|
| Car telemetry | Kafka (`car-telemetry`) via `confluent-kafka` | `car_number` (44) | Every 2 seconds (5 msgs/lap) | Car #44 only |
| Race standings | IBM MQ via `pymqi` | `car_number` (1-77) | Every 10 seconds (22 msgs/lap) | All 22 cars |

### Race Timing

```
1 lap = 10 real seconds
57 laps = 570 seconds = 9.5 minutes

Per lap:
  t=0s   → Advance race state, produce 22 standings messages to MQ
  t=0s   → Produce telemetry reading #1 to Kafka
  t=2s   → Produce telemetry reading #2 to Kafka
  t=4s   → Produce telemetry reading #3 to Kafka
  t=6s   → Produce telemetry reading #4 to Kafka
  t=8s   → Produce telemetry reading #5 to Kafka
  t=10s  → Next lap begins
```

Total messages produced: `(5 telemetry × 57 laps) + (22 standings × 57 laps) = 285 + 1,254 = 1,539 messages`

### Telemetry Generation (car #44 only)

Each telemetry message contains 17 sensor metrics. Here is the EXACT behavior of each across 57 laps:

**Tire temperatures — the critical metrics:**

```
tire_temp_fl_c (FRONT LEFT — THE ANOMALY):
  Formula: base(95.0) + gradient(0.42) × tire_age + noise(±1.0)
  Laps 1-31:  95.4 → 108.0  (gradual rise, ~0.42°C per lap)
  Lap 32:     145.0 ± 2.0    (FORCED SPIKE — the anomaly)
  Laps 33-57: 95.8 → 105.1  (fresh tires after pit, resets to baseline)

tire_temp_fr_c (FRONT RIGHT — NO ANOMALY):
  Formula: base(95.0) + gradient(0.45) × tire_age + noise(±1.0)
  Laps 1-32:  95.5 → 109.4  (gradual, smooth)
  Laps 33-57: 95.9 → 105.8  (resets after pit)

tire_temp_rl_c (REAR LEFT — NO ANOMALY):
  Formula: base(93.0) + gradient(0.39) × tire_age + noise(±1.0)
  Laps 1-32:  93.4 → 105.5  (gradual, smooth)
  Laps 33-57: 93.8 → 102.7  (resets after pit)

tire_temp_rr_c (REAR RIGHT — NO ANOMALY):
  Formula: base(93.0) + gradient(0.35) × tire_age + noise(±1.0)
  Laps 1-32:  93.4 → 104.2  (gradual, smooth)
  Laps 33-57: 93.7 → 101.8  (resets after pit)
```

**Tire pressures — gradual decline, no anomalies:**

```
tire_pressure_{fl,fr}_psi:
  Formula: base(22.0) - drop(0.05) × tire_age + noise(±0.1)
  Laps 1-32:  21.95 → 20.40  (slow linear decline)
  Laps 33-57: 22.0 → 20.75   (resets after pit)

tire_pressure_{rl,rr}_psi:
  Formula: base(21.0) - drop(0.05) × tire_age + noise(±0.1)
  Laps 1-32:  20.95 → 19.40
  Laps 33-57: 21.0 → 19.75
```

**Engine, brakes, battery, fuel — stable, no anomalies:**

```
engine_temp_c:        120.0 + noise(±2.0)     → always 118-122°C
brake_temp_fl_c:      450.0 + noise(±25.0)    → always 425-475°C
brake_temp_fr_c:      460.0 + noise(±25.0)    → always 435-485°C
battery_charge_pct:   60.0 + cycle(20.0)      → oscillates 40-80%, clamped to 35-85%
fuel_remaining_kg:    44.0 - 0.7 × lap        → linear: 44.0 → 4.1 kg
```

**Other fields:**

```
drs_active:    random boolean (60% chance true)
speed_kph:     280-320 (random)
throttle_pct:  60-100 (random)
brake_pct:     0 (70% of time) or 0-15 (30% of time)
```

### Position Model (all 22 cars)

**Model: Cumulative race time** — how real F1 works.

Each car tracks a `total_race_time` that accumulates every lap:
```
total_race_time += base_pace[compound] + (degradation[compound] × tire_age) + qualifying_delta
If pitting this lap: total_race_time += 23.0 (pit stop penalty)
```

Positions = all 22 cars sorted by `total_race_time` (lowest = P1).

**Qualifying delta:** Fixed per driver, 0.15 seconds per grid position. P1 = 0.0s, P2 = 0.15s, P3 = 0.30s, etc. This represents inherent driver/car pace and never changes during the race.

**Starting gap:** Each car starts with `(grid_position - 1) × 0.5s` of accumulated time, representing the physical grid spread.

**Tire compound performance:**

| Compound | Base pace | Degradation/lap | After 20 laps | After 32 laps |
|---|---|---|---|---|
| SOFT | 90.50s | +0.08s/lap | 92.10s | 93.06s |
| MEDIUM | 91.00s | +0.04s/lap | 91.80s | 92.28s |
| HARD | 91.80s | +0.02s/lap | 92.20s | 92.44s |

Softs are fastest initially but degrade 2x faster than mediums. By lap 32, old softs (93.06s) are 1.78s/lap slower than fresh mediums (91.04s).

### Car #44 Position Trajectory (Lap by Lap)

```
Lap  Position  Tire       Age  Lap Time  Key Event
───  ────────  ─────────  ───  ────────  ─────────────────────────────────
 1   P3        SOFT        1   90.88s    Race start
 5   P3        SOFT        5   91.28s    Stable
10   P3        SOFT       10   91.68s    Slight pace drop
15   P3→P4     SOFT       15   92.08s    Vega pits (P4→pit), but others now faster
16   P4        SOFT       16   92.16s    Koskinen pits
18   P4→P5     SOFT       18   92.32s    Eriksson pits — still ahead on total time
20   P5        SOFT       20   92.48s    Walsh, Tanaka pit
22   P5→P6     SOFT       22   92.64s    Novak pits
25   P6→P7     SOFT       25   92.88s    Cars on fresh tires closing fast
28   P7        SOFT       28   93.12s    Being caught by pitted cars
31   P8        SOFT       31   93.36s    Lost 5 positions — tires critical
32   P8        SOFT       32   93.44s    ★ ANOMALY FIRES — Agent says PIT NOW
33   P10       PIT→MEDIUM  1   91.34s    Pits: +23s penalty, drops 2 spots
35   P9        MEDIUM      3   91.42s    Fresh tires — overtaking slower cars
38   P8        MEDIUM      6   91.54s    Pace advantage accumulating
40   P6        MEDIUM      8   91.62s    Catching cars on 20+ lap old tires
42   P5        MEDIUM     10   91.70s    
45   P4        MEDIUM     13   91.82s    Reyes pits (lap 35) — jumps him
50   P3        MEDIUM     18   92.02s    Back in podium position
57   P3        MEDIUM     25   92.30s    FINISH — +5 positions from P8 at pit call
```

**Why +5 works mathematically:**
- At lap 32, car 44 is P8 doing 93.44s laps
- After pit at lap 33, doing 91.34s laps (2.10s/lap faster)
- Over 24 remaining laps: 24 × ~1.5s average advantage = ~36s gained
- Minus 23s pit stop cost = ~13s net gain
- 13s is enough to pass ~7 cars (at ~1.7s average gap between positions)
- Net: P8 → P10 (pit) → P3 = +5 from P8

### Other Cars' Pit Strategies

| Car | Driver | Start | Pit Lap | New Tire | Finish ~Position |
|---|---|---|---|---|---|
| #1 | Eriksson | P1 | 18 | MEDIUM | P1 |
| #4 | Novak | P2 | 22 | MEDIUM | P2 |
| #44 | **River** | **P3** | **33** | **MEDIUM** | **P3** |
| #16 | Vega | P4 | 15 | MEDIUM | P4 |
| #63 | Walsh | P5 | 20 | MEDIUM | P5-P6 |
| #14 | Reyes | P6 | 35 | HARD | P6-P7 |
| #10 | Martin | P7 | 17 | HARD | P7-P8 |
| #24 | Koskinen | P8 | 16 | MEDIUM | P5-P6 |
| Rest | Various | P9-P22 | Various | Various | P9-P22 |

Cars that pit early (laps 15-22) temporarily drop but recover quickly on fresh tires. Car 44 pits LAST among frontrunners (lap 33), which is why it drops so far on old tires. Reyes pits even later (lap 35) on hards — he's the one car 44 jumps in the late stages.

### Guarantees the Data Generator MUST Provide

1. **Exactly ONE anomaly** — `tire_temp_fl_c` spikes to ~145°C at lap 32. No other metric ever triggers an anomaly.
2. **Car 44 at P8 when anomaly fires** — Position must be 8 at lap 32.
3. **Car 44 finishes P3** — Net +5 positions from the PIT NOW call at P8.
4. **All 22 cars have valid positions** — No duplicates, no gaps, positions 1-22 at every lap.
5. **Metrics reset after pit** — Tire temps/pressures return to baseline when car 44 pits at lap 33.
6. **Fuel decreases linearly** — 44.0 → ~4.1 kg, never negative.
7. **Standings are consistent** — Gap calculations are based on real cumulative time differences.
8. **Deterministic narrative** — Running the simulator twice produces the same position trajectory (noise affects sensor values, not positions).

### Noise vs. Determinism

- **Sensor values have noise** — small random perturbations (±1°C on temps, ±0.1 psi on pressures). This is realistic and helps AI_DETECT_ANOMALIES distinguish normal variation from the lap-32 spike.
- **Positions are deterministic** — the cumulative time model produces the same position trajectory every run because qualifying_delta and degradation rates are fixed. The race narrative is always the same.

### Why AI_DETECT_ANOMALIES Won't Fire on Gradual Trends

The function uses **TimesFM 2.5** (Google Research foundation model, pre-trained on 100B+ real-world time points). It is a **forecasting model**, not a simple threshold — it learns the pattern from historical context and adjusts its prediction interval (upper/lower bounds) to track the trend.

**How each metric pattern is handled:**

| Pattern | Example Metric | What the Model Does | Anomaly? |
|---|---|---|---|
| Gradual rise | tire_temp_fr_c (95→109 over 32 laps) | Learns the upward slope, adjusts forecast + bounds upward | **No** |
| Gradual decline | fuel_remaining_kg (44→4 kg, linear) | Learns the downward slope, bounds track downward | **No** |
| Flat + noise | engine_temp_c (~120 ±2°C) | Learns stable baseline, keeps tight bounds | **No** |
| Cyclical | battery_charge_pct (40-80% repeating) | Learns the periodic pattern | **No** |
| **Sudden spike** | tire_temp_fl_c (108→145 in one interval) | Expects ~108.5 based on trend. Upper bound ~117. Actual 145 far exceeds. | **YES** |

**Asymmetric bounds configuration (belt-and-suspenders):**

```sql
JSON_OBJECT(
  'upperBoundConfidencePercentage' VALUE 99.0,
  'lowerBoundConfidencePercentage' VALUE 99.9
)
```

- `upperBoundConfidencePercentage = 99.0` — Sensitive to spikes (overheating). Only extreme deviations above the learned trend trigger.
- `lowerBoundConfidencePercentage = 99.9` — Extremely tolerant of low values. Prevents fuel decrease or tire pressure decline from ever triggering a false positive. The lower bound is practically at the floor.

**Why the 37°C spike always fires:** At lap 32, the model has ~150 data points showing a smooth +0.42°C/lap trend. It expects ~108.5°C with an upper bound around ~117°C. Actual: 145°C — 28°C above the upper bound. Zero chance of a miss.

---

## Demo Execution Guide

### Pre-Demo Setup (One Time)

1. **Configure credentials:**
   ```bash
   cp terraform/terraform.tfvars.example terraform/terraform.tfvars
   # Edit with Confluent Cloud API key/secret
   # Ensure AWS credentials are configured (~/.aws/credentials or env vars)
   ```

2. **Deploy infrastructure:**
   ```bash
   ./scripts/setup.sh
   ```
   This creates: environment, cluster, car-telemetry topic, Flink compute pool, MQ EC2 (with simulator running), Postgres EC2 (with drivers seeded), S3 + IAM for Tableflow.

3. **Verify:**
   - MQ console: `https://<MQ_IP>:9443/ibmmq/console` (admin/passw0rd)
   - Postgres: `psql postgresql://f1user:f1passw0rd@<POSTGRES_IP>:5432/f1demo -c "SELECT count(*) FROM drivers;"`  → 22
   - Confluent Cloud UI: `car-telemetry` topic should show messages flowing

4. **Pre-configure Databricks:**
   - Unity Catalog external location pointing to the Tableflow S3 bucket
   - Genie space with access to the catalog
   - See `docs/SETUP-TABLEFLOW.md` and `docs/SETUP-GENIE.md`

### Demo Recording — Section 1: Data Discovery & Connection (~4 min)

1. Open Claude Desktop with `mcp-confluent` configured
2. Ask: *"What data sources do I have in my environment?"*
   - MCP discovers the MQ queue with FIA standings and the Postgres table with driver profiles
3. Ask: *"Create a connector to stream the FIA race standings from MQ"*
   - MCP generates the MQ Source connector config
   - Deploy it → `race-standings` topic appears
4. Ask: *"Create a CDC connector for the drivers table in Postgres"*
   - MCP generates the Debezium CDC connector config
   - Deploy it → `drivers` topic appears
5. Show data flowing in Confluent Cloud UI — race standings from MQ, driver profiles from Postgres
6. Show `car-telemetry` already flowing (started by Terraform)

### Demo Recording — Section 2: Building Intelligence (~6 min)

1. Show the `car-telemetry` topic messages — raw sensor data
2. Ask MCP: *"Help me build an enrichment pipeline that aggregates telemetry, detects anomalies, and joins with race standings"*
   - MCP scaffolds the DBT model with tumbling window + AI_DETECT_ANOMALIES + temporal join
3. Show the enrichment SQL — explain the CTE pattern:
   - CTE 1: 10-second tumbling window aggregating sensor metrics
   - CTE 2: AI_DETECT_ANOMALIES on every metric (tire temps, pressures, engine, brakes, battery, fuel)
   - Final SELECT: temporal join with race-standings for position context
4. Run `dbt run` → Flink Job 1 starts → `car-state` topic created
5. Show `car-state` messages — all anomaly flags `false` (race is still early)
6. Ask MCP: *"Configure a streaming agent that reads car-state and recommends pit strategy"*
   - MCP helps create: CONNECTION → MODEL → TOOL (RTCE for competitor standings) → AGENT
7. Deploy the agent → Flink Job 2 starts → `pit-decisions` topic created
8. Show early pit-decisions: `suggestion: "STAY OUT"`, `recommended_tire_compound: null`
9. **Wait for lap 32** (about 5 minutes into the race):
   - `anomaly_tire_temp_fl: true` — front-left tire overheating!
   - Agent says: `"PIT NOW"` with full reasoning
   - Show the `pit-decisions` message — recommended compound, expected positions gained
10. Watch laps 33-40: car recovers positions on fresh tires

### Demo Recording — Section 3: Analytics & Impact (~4 min)

1. Ask MCP: *"Enable Tableflow on pit-decisions and drivers topics"*
   - MCP enables Tableflow → Delta Lake tables materialize in S3
2. Open Databricks → show Unity Catalog with the new tables
3. Open Genie space
4. Ask: *"How many positions did we gain after following the agent's recommendation?"*
   - Result: **+5 positions** (P8 at pit → P3 at finish)
5. Ask: *"What percentage of the race did our driver spend on each tire compound?"*
   - Pie chart: SOFT 56% / MEDIUM 44%
6. **Closing line:** *"AI at both ends — Confluent's streaming agent made the call in real time, Databricks Genie analyzes the impact. Three data sources, two AI models, one platform."*

### Post-Demo Teardown

```bash
./scripts/teardown.sh
```

### Timing Guide

| Section | Duration | Race Lap Range |
|---|---|---|
| Pre-roll (race already running) | Before recording | Laps 1-15 |
| Section 1: Discovery & Connection | ~4 min | Laps 15-25 |
| Section 2: Building Intelligence | ~6 min | Laps 25-35 (anomaly at 32) |
| Section 3: Analytics & Impact | ~4 min | Laps 35-50 |
| Total | ~14 min | |

**Critical timing:** The anomaly fires at lap 32 (~5:20 into the race). Start recording Section 2 before lap 25 to have time to deploy the enrichment and agent before the anomaly fires. The race simulator runs continuously — you can restart it by SSH-ing into the EC2 instance if needed:

```bash
ssh ec2-user@<MQ_IP>
sudo kill $(pgrep -f simulator.py)
cd /opt/simulator && source .env && python3 -m datagen.simulator &
```
