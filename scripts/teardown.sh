#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TERRAFORM_DIR="$PROJECT_DIR/terraform"

echo "=== F1 Pit Wall AI Demo — Teardown ==="
echo ""

cd "$TERRAFORM_DIR"

echo "--- Destroying all resources ---"
terraform destroy -auto-approve

echo ""
echo "=== Teardown Complete ==="
