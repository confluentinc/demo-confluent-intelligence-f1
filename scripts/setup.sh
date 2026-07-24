#!/bin/bash
# NOTE: Prefer 'uv run deploy' — it prompts for credentials and wires the shared
# outputs into the attendee layer automatically. This script is a thin manual
# fallback that assumes you have already provided variables (terraform.tfvars or
# TF_VAR_* env vars) for each layer.
#
# For a real multi-attendee workshop, use `wsa` with wsa-spec-aws.yaml instead.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SHARED_DIR="$PROJECT_DIR/terraform/aws-shared"
AWS_DIR="$PROJECT_DIR/terraform/aws"

echo "=== F1 Workshop — Setup (aws-shared -> aws) ==="

if ! command -v terraform &> /dev/null; then
    echo "ERROR: terraform is not installed"
    exit 1
fi

echo "--- Deploying shared infrastructure ---"
cd "$SHARED_DIR"
terraform init
terraform apply -auto-approve

echo ""
echo "--- Deploying attendee environment ---"
echo "NOTE: pass the shared outputs (vpc_id, subnet_ids, postgres_host,"
echo "      postgres_password, ecr_image_uri, ...) as TF_VAR_shared_vpc_id,"
echo "      TF_VAR_shared_subnet_ids, TF_VAR_shared_postgres_host, etc. first"
echo "      (every terraform/aws shared_* variable = TF_VAR_shared_<aws-shared output name>),"
echo "      or use 'uv run deploy' which does this for you."
cd "$AWS_DIR"
terraform init
terraform apply -auto-approve

echo ""
echo "=== Setup Complete ==="
echo "Attendee credentials:  cd terraform/aws && terraform output -json attendee_credentials"
