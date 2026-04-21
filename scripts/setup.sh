#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TERRAFORM_DIR="$PROJECT_DIR/terraform"

echo "=== F1 Pit Wall AI Demo — Setup ==="
echo ""

# Check prerequisites
if ! command -v terraform &> /dev/null; then
    echo "ERROR: terraform is not installed"
    exit 1
fi

if [ ! -f "$TERRAFORM_DIR/terraform.tfvars" ]; then
    echo "ERROR: terraform/terraform.tfvars not found"
    echo "Copy terraform/terraform.tfvars.example and fill in your credentials"
    exit 1
fi

# Initialize and apply
cd "$TERRAFORM_DIR"

echo "--- Initializing Terraform ---"
terraform init

echo ""
echo "--- Applying Terraform ---"
terraform apply -auto-approve

echo ""
echo "--- Outputs ---"
terraform output

echo ""
echo "=== Setup Complete ==="
echo ""
echo "MQ Console:  https://$(terraform output -raw mq_public_ip):9443/ibmmq/console"
echo "Postgres:    $(terraform output -raw postgres_connection_string)"
echo ""
echo "The race simulator is running on the MQ EC2 instance."
echo "Car telemetry is flowing to the 'car-telemetry' topic."
echo "Race standings are flowing to IBM MQ."
