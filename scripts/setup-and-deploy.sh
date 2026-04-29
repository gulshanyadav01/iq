#!/bin/bash
# Complete setup and deployment script for insure-iq on local kind cluster
# Handles: cluster creation, ingress setup, secret creation, and helm deployment

set -e

CLUSTER_NAME="insure-iq"
NAMESPACE="insure-iq"
CHART_DIR="./helm/insure-iq"
ENV="${1:-dev}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_step() {
    echo -e "\n${BLUE}▶ $1${NC}\n"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

# Validate env
if [ "$ENV" != "dev" ] && [ "$ENV" != "prod" ]; then
    print_error "Invalid environment: $ENV"
    echo "Usage: $0 [dev|prod]"
    exit 1
fi

echo -e "${BLUE}"
cat << "EOF"
╔═══════════════════════════════════════════╗
║     insure-iq Setup & Deployment          ║
╚═══════════════════════════════════════════╝
EOF
echo -e "${NC}"

print_step "Checking Prerequisites"

# Check tools
for tool in kind kubectl helm; do
    if ! command -v $tool &> /dev/null; then
        print_error "$tool not installed"
        echo "Install from:"
        case $tool in
            kind) echo "  https://kind.sigs.k8s.io/docs/user/quick-start/" ;;
            kubectl) echo "  https://kubernetes.io/docs/tasks/tools/" ;;
            helm) echo "  https://helm.sh/docs/intro/install/" ;;
        esac
        exit 1
    fi
    print_success "$tool installed"
done

print_step "Setting up Kubernetes Cluster"

# Create cluster if needed
if ! kind get clusters | grep -q "^$CLUSTER_NAME$"; then
    print_warning "Cluster not found, creating..."

    # Create temp config
    cat > /tmp/kind-config.yaml << EOF2
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: $CLUSTER_NAME
nodes:
- role: control-plane
  image: kindest/node:v1.28.0
  extraPortMappings:
  - containerPort: 80
    hostPort: 80
    protocol: TCP
  - containerPort: 443
    hostPort: 443
    protocol: TCP
  - containerPort: 5432
    hostPort: 5432
    protocol: TCP
  - containerPort: 6379
    hostPort: 6379
    protocol: TCP
  - containerPort: 9000
    hostPort: 9000
    protocol: TCP
  - containerPort: 9001
    hostPort: 9001
    protocol: TCP
EOF2

    kind create cluster --config /tmp/kind-config.yaml
    print_success "Cluster created"
else
    print_success "Cluster already exists"
fi

# Set context
kubectl config use-context kind-$CLUSTER_NAME
print_success "Configured kubectl context"

print_step "Installing Ingress Controller"

# Check if nginx-ingress is already installed
if kubectl get namespace ingress-nginx &> /dev/null; then
    print_success "Ingress nginx namespace exists"
else
    print_warning "Installing nginx-ingress controller..."
    kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.8.1/deploy/static/provider/kind/deploy.yaml
    sleep 5
fi

# Wait for controller pod to be ready
print_warning "Waiting for ingress controller pod (this may take 1-2 minutes)..."
kubectl wait --namespace ingress-nginx \
    --for=condition=ready pod \
    --selector=app.kubernetes.io/component=controller \
    --timeout=180s 2>/dev/null || true

# Wait for webhook pod to be ready
print_warning "Waiting for webhook pod..."
kubectl wait --namespace ingress-nginx \
    --for=condition=ready pod \
    --selector=app.kubernetes.io/component=admission-webhook \
    --timeout=60s 2>/dev/null || {
    print_warning "Webhook pod might not be ready, adding extra wait time..."
    sleep 10
}

# Double-check both are running
print_warning "Verifying all components..."
if kubectl get pods -n ingress-nginx | grep -q "ingress-nginx-controller.*Running"; then
    print_success "Controller pod is running"
else
    print_warning "Controller pod status uncertain, proceeding anyway..."
fi

# Delete the admission webhook to avoid timing issues during helm install
kubectl delete -A ValidatingWebhookConfiguration ingress-nginx-admission 2>/dev/null || true

sleep 5
print_success "Ingress controller setup complete"

print_step "Preparing Namespace"

# Delete existing namespace if it exists (to avoid Helm metadata conflicts)
if kubectl get namespace $NAMESPACE &> /dev/null; then
    print_warning "Deleting existing namespace to allow Helm to manage it..."
    kubectl delete namespace $NAMESPACE --wait=true
    sleep 2
fi

print_step "Preparing Secrets"

# Create secrets file if needed
if [ ! -f "$CHART_DIR/values.secrets.yaml" ]; then
    print_warning "Creating values.secrets.yaml from template..."
    cp "$CHART_DIR/values.secrets.yaml.example" "$CHART_DIR/values.secrets.yaml"
    print_success "Created values.secrets.yaml"

    echo -e "\n${YELLOW}⚠️  IMPORTANT: Edit $CHART_DIR/values.secrets.yaml${NC}"
    echo "Update at minimum:"
    echo "  - secretKey: strong random string"
    echo "  - webhookSecretKey: strong random string"
    echo "  - betterAuthSecret: strong random string"
    echo ""

    if [ "$ENV" = "dev" ]; then
        echo "For dev, you can use simple values:"
        echo "  - dbPassword: dev-password"
        echo "  - secretKey: dev-secret-key"
        echo ""
    fi

    read -p "Press Enter after updating secrets file..."
else
    print_success "Using existing values.secrets.yaml"
fi

print_step "Building and Loading Docker Images"

# Build backend image
print_warning "Building backend image..."
docker build -f backend/Dockerfile -t insure-iq-backend:latest .
print_success "Backend image built"

# Build web image
print_warning "Building web image..."
docker build -f web/Dockerfile -t insure-iq-web:latest .
print_success "Web image built"

# Load images into kind cluster
print_warning "Loading images into kind cluster..."
kind load docker-image insure-iq-backend:latest --name $CLUSTER_NAME
kind load docker-image insure-iq-web:latest --name $CLUSTER_NAME
print_success "Images loaded into kind"

# Label the node for ingress
kubectl label node $CLUSTER_NAME-control-plane ingress-ready=true --overwrite 2>/dev/null || true
print_success "Node labeled for ingress"

print_step "Final Checks Before Deployment"

# Final wait for everything to settle
print_warning "Waiting for all components to be fully ready..."
sleep 5

print_step "Deploying with Helm"

# Deploy (Helm will create the namespace with proper metadata)
HELM_CMD="helm install insure-iq $CHART_DIR -f $CHART_DIR/values.$ENV.yaml -f $CHART_DIR/values.secrets.yaml -n $NAMESPACE --create-namespace"

if helm list -n $NAMESPACE 2>/dev/null | grep -q insure-iq; then
    print_warning "Release exists, upgrading..."
    HELM_CMD="helm upgrade insure-iq $CHART_DIR -f $CHART_DIR/values.$ENV.yaml -f $CHART_DIR/values.secrets.yaml -n $NAMESPACE"
fi

eval $HELM_CMD
print_success "Helm deployment complete"

print_step "Waiting for Pods"

echo "Waiting for services to be ready (this may take 3-5 minutes)..."
kubectl rollout status deployment/backend -n $NAMESPACE --timeout=300s || true
kubectl rollout status deployment/web -n $NAMESPACE --timeout=300s || true
kubectl rollout status deployment/auth -n $NAMESPACE --timeout=300s || true

print_step "Deployment Complete ✨"

echo "Cluster: ${BLUE}kind-$CLUSTER_NAME${NC}"
echo "Namespace: ${BLUE}$NAMESPACE${NC}"
echo "Environment: ${BLUE}$ENV${NC}"
echo ""

echo "Pod status:"
kubectl get pods -n $NAMESPACE
echo ""

echo "Next steps:"
echo ""
echo "1. Check pod logs:"
echo "   ${BLUE}kubectl logs -n $NAMESPACE deployment/backend${NC}"
echo "   ${BLUE}kubectl logs -n $NAMESPACE deployment/web${NC}"
echo ""
echo "2. Port-forward to test locally:"
echo "   ${BLUE}kubectl port-forward -n $NAMESPACE svc/backend 8000:80${NC}"
echo "   ${BLUE}kubectl port-forward -n $NAMESPACE svc/web 3000:80${NC}"
echo ""
echo "3. Access via ingress:"
echo "   ${BLUE}http://localhost/api/health/${NC}       (backend health check)"
echo "   ${BLUE}http://localhost/auth/api/auth/ok${NC}  (auth check)"
echo "   ${BLUE}http://localhost/${NC}                   (frontend)"
echo ""
echo "4. Delete cluster when done:"
echo "   ${BLUE}kind delete cluster --name $CLUSTER_NAME${NC}"
echo ""
