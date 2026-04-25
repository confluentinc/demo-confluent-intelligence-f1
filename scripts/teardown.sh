#!/bin/bash
# NOTE: Prefer 'uv run destroy' instead — it handles teardown order and cleanup automatically.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DEMO_DIR="$PROJECT_DIR/terraform/demo"
CORE_DIR="$PROJECT_DIR/terraform/core"

echo "=== F1 Pit Wall AI Demo — Teardown ==="
echo ""

# Destroy demo first (depends on core)
if [ -f "$DEMO_DIR/terraform.tfstate" ]; then
    echo "--- Destroying Demo Resources ---"
    cd "$DEMO_DIR"
    terraform init
    terraform destroy -auto-approve
fi

# Then destroy core
if [ -f "$CORE_DIR/terraform.tfstate" ]; then
    echo ""
    echo "--- Destroying Core Infrastructure ---"
    cd "$CORE_DIR"
    terraform init
    terraform destroy -auto-approve
fi

echo ""
echo "=== Teardown Complete ==="
