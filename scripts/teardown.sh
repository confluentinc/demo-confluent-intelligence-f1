#!/bin/bash
# NOTE: Prefer 'uv run destroy' instead — it handles teardown order and injects
# the shared variables the aws layer needs at destroy time.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
AWS_DIR="$PROJECT_DIR/terraform/aws"
SHARED_DIR="$PROJECT_DIR/terraform/aws-shared"

echo "=== F1 Workshop — Teardown ==="

# Destroy attendee resources first (they reference the shared layer).
if [ -f "$AWS_DIR/terraform.tfstate" ]; then
    echo "--- Destroying attendee environment ---"
    cd "$AWS_DIR"
    terraform init
    terraform destroy -auto-approve
fi

# Then destroy shared infrastructure.
if [ -f "$SHARED_DIR/terraform.tfstate" ]; then
    echo ""
    echo "--- Destroying shared infrastructure ---"
    cd "$SHARED_DIR"
    terraform init
    terraform destroy -auto-approve
fi

echo ""
echo "=== Teardown Complete ==="
