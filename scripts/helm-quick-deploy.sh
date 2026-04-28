#!/bin/bash
# Quick deployment script for insure-iq Helm chart
# Usage: ./helm-quick-deploy.sh dev|prod

CHART_DIR="./helm/insure-iq"
NAMESPACE="insure-iq"
RELEASE_NAME="insure-iq"
ENV="${1:-dev}"

if [ "$ENV" != "dev" ] && [ "$ENV" != "prod" ]; then
    echo "Usage: $0 [dev|prod]"
    echo ""
    echo "Examples:"
    echo "  $0 dev      # Deploy to dev (in-cluster infra)"
    echo "  $0 prod     # Deploy to prod (external DB)"
    exit 1
fi

echo "🚀 Deploying insure-iq ($ENV environment)..."
echo ""

# Check prerequisites
if ! command -v helm &> /dev/null; then
    echo "❌ Helm not installed"
    echo "   Install from: https://helm.sh/docs/intro/install/"
    exit 1
fi

if ! command -v kubectl &> /dev/null; then
    echo "❌ kubectl not installed"
    echo "   Install from: https://kubernetes.io/docs/tasks/tools/"
    exit 1
fi

echo "✓ Helm and kubectl installed"
echo ""

# Check for running cluster
if ! kubectl cluster-info &> /dev/null; then
    echo "❌ No Kubernetes cluster running"
    echo ""
    echo "Set up a local cluster with kind or minikube:"
    echo ""
    echo "  Using kind (recommended):"
    echo "    brew install kind                    # macOS"
    echo "    kind create cluster --name insure-iq"
    echo ""
    echo "  Using minikube:"
    echo "    brew install minikube               # macOS"
    echo "    minikube start"
    echo ""
    exit 1
fi

echo "✓ Connected to Kubernetes cluster"
echo ""

# Check secrets file
if [ ! -f "$CHART_DIR/values.secrets.yaml" ]; then
    echo "❌ values.secrets.yaml not found"
    echo "   Create it from template:"
    echo "   cp $CHART_DIR/values.secrets.yaml.example $CHART_DIR/values.secrets.yaml"
    exit 1
fi

echo "✓ Secrets file found"
echo ""

# Delete namespace if it exists (let Helm manage it with proper metadata)
if kubectl get namespace $NAMESPACE &> /dev/null; then
    echo "Deleting existing namespace..."
    kubectl delete namespace $NAMESPACE --wait=true
    sleep 2
fi

# Deploy (Helm will create the namespace with proper metadata)
if helm list -n $NAMESPACE 2>/dev/null | grep -q $RELEASE_NAME; then
    echo "📦 Upgrading release..."
    helm upgrade $RELEASE_NAME $CHART_DIR \
        -f $CHART_DIR/values.$ENV.yaml \
        -f $CHART_DIR/values.secrets.yaml \
        -n $NAMESPACE
else
    echo "📦 Installing release..."
    helm install $RELEASE_NAME $CHART_DIR \
        -f $CHART_DIR/values.$ENV.yaml \
        -f $CHART_DIR/values.secrets.yaml \
        -n $NAMESPACE --create-namespace
fi

echo ""
echo "✅ Deployment complete!"
echo ""
echo "Check status:"
echo "  kubectl get pods -n $NAMESPACE -w"
echo ""
echo "View logs:"
echo "  kubectl logs -n $NAMESPACE deployment/backend"
echo ""
echo "Helm status:"
echo "  helm status $RELEASE_NAME -n $NAMESPACE"
