#!/bin/bash
set -e

# Check if kubectl is installed
if ! command -v kubectl &> /dev/null; then
    echo "Error: kubectl is not installed"
    exit 1
fi

echo "Installing nginx-ingress controller..."

# Apply nginx-ingress controller for kind
echo "Applying nginx-ingress manifest..."
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml

echo "Waiting for ingress controller to be ready..."
kubectl wait --namespace ingress-nginx \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller \
  --timeout=120s

echo "nginx-ingress controller installed successfully!"
echo ""
echo "Verify with: kubectl get pods -n ingress-nginx"