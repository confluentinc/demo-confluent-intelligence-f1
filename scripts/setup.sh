#!/bin/bash
# NOTE: Prefer 'uv run deploy' instead — it handles credentials and tfvars automatically.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CORE_DIR="$PROJECT_DIR/terraform/core"
DEMO_DIR="$PROJECT_DIR/terraform/demo"

echo "=== F1 Pit Wall AI Demo — Setup ==="
echo ""

# Check prerequisites
if ! command -v terraform &> /dev/null; then
    echo "ERROR: terraform is not installed"
    exit 1
fi

if [ ! -f "$CORE_DIR/terraform.tfvars" ]; then
    echo "ERROR: terraform/core/terraform.tfvars not found"
    echo "Run 'uv run deploy' instead, or create terraform.tfvars in terraform/core/ and terraform/demo/"
    exit 1
fi

# Deploy core
echo "--- Deploying Core Infrastructure ---"
cd "$CORE_DIR"
terraform init
terraform apply -auto-approve

# Deploy demo
echo ""
echo "--- Deploying Demo Resources ---"
cd "$DEMO_DIR"
terraform init
terraform apply -auto-approve

echo ""
echo "--- Outputs ---"
terraform output

echo ""
echo "=== Setup Complete ==="
echo ""
echo "MQ Console:  https://$(terraform output -raw mq_public_ip):9443/ibmmq/console"
echo "Postgres:    $(terraform output -raw postgres_connection_string)"
