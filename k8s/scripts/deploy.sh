#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$(dirname "$SCRIPT_DIR")"

ENV="${1:-dev}"
OVERLAY_DIR="$K8S_DIR/overlays/$ENV"

if [ ! -d "$OVERLAY_DIR" ]; then
    echo "Error: Environment '$ENV' not found"
    echo "Available: dev, prod"
    exit 1
fi

echo "Deploying to $ENV environment..."

kubectl apply -k "$OVERLAY_DIR"

echo ""
echo "Deployment complete!"
echo ""
echo "Check status:"
echo "  kubectl get pods -n insure-iq"