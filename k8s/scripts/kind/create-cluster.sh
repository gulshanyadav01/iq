#!/bin/bash
set -e

# Check if kind is installed
if ! command -v kind &> /dev/null; then
    echo "Error: kind is not installed"
    echo "Install it with: brew install kind"
    exit 1
fi

# Check if kubectl is installed
if ! command -v kubectl &> /dev/null; then
    echo "Error: kubectl is not installed"
    echo "Install it with: brew install kubectl"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "Creating kind cluster 'insure-iq'..."

# Create the cluster
kind create cluster --name insure-iq --config "$SCRIPT_DIR/kind-config.yaml"

echo "Waiting for cluster to be ready..."
kubectl wait --for=condition=Ready nodes --all --timeout=300s

echo "Cluster created successfully!"
echo ""
echo "Next steps:"
echo "  1. Install nginx-ingress: $SCRIPT_DIR/install-ingress.sh"
echo "  2. Deploy the app: kubectl apply -k $PROJECT_ROOT/k8s/overlays/dev"
echo ""
echo "Useful commands:"
echo "  kubectl get pods -n insure-iq"
echo "  kubectl logs -n insure-iq -l app=backend"
echo "  kind delete cluster --name insure-iq"