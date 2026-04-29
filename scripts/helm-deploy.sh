#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HELM_CHART_DIR="$SCRIPT_DIR/helm/insure-iq"
NAMESPACE="insure-iq"
RELEASE_NAME="insure-iq"

# Functions
print_header() {
    echo -e "\n${BLUE}==== $1 ====${NC}\n"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

check_prerequisites() {
    print_header "Checking Prerequisites"

    # Check helm
    if ! command -v helm &> /dev/null; then
        print_error "Helm is not installed"
        echo "Install from: https://helm.sh/docs/intro/install/"
        exit 1
    fi
    print_success "Helm found: $(helm version --short)"

    # Check kubectl
    if ! command -v kubectl &> /dev/null; then
        print_error "kubectl is not installed"
        exit 1
    fi
    print_success "kubectl found: $(kubectl version --client --short)"

    # Check cluster access
    if ! kubectl cluster-info &> /dev/null; then
        print_error "Cannot connect to Kubernetes cluster"
        echo "Make sure you have a cluster running and kubeconfig configured"
        exit 1
    fi
    print_success "Connected to cluster"
}

setup_secrets() {
    print_header "Setting Up Secrets"

    SECRETS_FILE="$HELM_CHART_DIR/values.secrets.yaml"
    SECRETS_EXAMPLE="$HELM_CHART_DIR/values.secrets.yaml.example"

    if [ ! -f "$SECRETS_FILE" ]; then
        print_warning "values.secrets.yaml not found"
        echo "Creating from template..."
        cp "$SECRETS_EXAMPLE" "$SECRETS_FILE"
        print_success "Created $SECRETS_FILE"

        echo -e "\n${YELLOW}IMPORTANT: Edit $SECRETS_FILE with your credentials:${NC}"
        echo "  - secretKey: Django secret key"
        echo "  - webhookSecretKey: Auth webhook secret"
        echo "  - dbPassword: Database password"
        echo "  - betterAuthSecret: Auth service secret"
        echo "  - AWS credentials and OAuth keys"
        echo ""
        read -p "Press Enter once you've updated $SECRETS_FILE..."
    else
        print_success "Using existing $SECRETS_FILE"
    fi
}

choose_environment() {
    print_header "Choose Deployment Environment"

    echo "1) Development (in-cluster postgres/redis/minio)"
    echo "2) Production (external DB, higher replicas)"
    echo ""
    read -p "Select option (1 or 2): " ENV_CHOICE

    case $ENV_CHOICE in
        1)
            ENV="dev"
            VALUES_FILE="$HELM_CHART_DIR/values.dev.yaml"
            print_success "Selected: Development"
            ;;
        2)
            ENV="prod"
            VALUES_FILE="$HELM_CHART_DIR/values.prod.yaml"
            print_success "Selected: Production"
            ;;
        *)
            print_error "Invalid option"
            exit 1
            ;;
    esac
}

create_namespace() {
    print_header "Creating Namespace"

    if kubectl get namespace "$NAMESPACE" &> /dev/null; then
        print_success "Namespace '$NAMESPACE' already exists"
    else
        kubectl create namespace "$NAMESPACE"
        print_success "Created namespace '$NAMESPACE'"
    fi
}

deploy_chart() {
    print_header "Deploying Helm Chart"

    # Check if release exists
    if helm list -n "$NAMESPACE" | grep -q "$RELEASE_NAME"; then
        print_warning "Release '$RELEASE_NAME' already exists, upgrading..."
        helm upgrade "$RELEASE_NAME" "$HELM_CHART_DIR" \
            -f "$VALUES_FILE" \
            -f "$HELM_CHART_DIR/values.secrets.yaml" \
            -n "$NAMESPACE"
        print_success "Upgrade complete"
    else
        print_warning "Installing release '$RELEASE_NAME'..."
        helm install "$RELEASE_NAME" "$HELM_CHART_DIR" \
            -f "$VALUES_FILE" \
            -f "$HELM_CHART_DIR/values.secrets.yaml" \
            -n "$NAMESPACE"
        print_success "Installation complete"
    fi
}

show_post_deployment() {
    print_header "Post-Deployment Steps"

    echo "Check deployment status:"
    echo "  ${BLUE}kubectl get pods -n $NAMESPACE -w${NC}"
    echo ""
    echo "View logs:"
    echo "  ${BLUE}kubectl logs -n $NAMESPACE deployment/backend${NC}"
    echo "  ${BLUE}kubectl logs -n $NAMESPACE deployment/web${NC}"
    echo "  ${BLUE}kubectl logs -n $NAMESPACE deployment/auth${NC}"
    echo ""
    echo "Port-forward for testing:"
    echo "  ${BLUE}kubectl port-forward -n $NAMESPACE svc/backend 8000:80${NC}"
    echo "  ${BLUE}kubectl port-forward -n $NAMESPACE svc/web 3000:80${NC}"
    echo ""
    echo "Check ingress:"
    echo "  ${BLUE}kubectl get ingress -n $NAMESPACE${NC}"
    echo ""
    echo "Helm status:"
    echo "  ${BLUE}helm status $RELEASE_NAME -n $NAMESPACE${NC}"
    echo ""

    if [ "$ENV" = "dev" ]; then
        echo "For development with in-cluster services:"
        echo "  ${BLUE}kubectl port-forward -n $NAMESPACE svc/postgres 5432:5432${NC}"
        echo "  ${BLUE}kubectl port-forward -n $NAMESPACE svc/redis 6379:6379${NC}"
        echo "  ${BLUE}kubectl port-forward -n $NAMESPACE svc/minio 9000:9000${NC}"
        echo ""
    fi
}

cleanup() {
    read -p "Do you want to uninstall the release? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        helm uninstall "$RELEASE_NAME" -n "$NAMESPACE"
        print_success "Release uninstalled"
    fi
}

# Main execution
main() {
    echo -e "${BLUE}"
    cat << "EOF"
╔════════════════════════════════════════╗
║      insure-iq Helm Deployment         ║
╚════════════════════════════════════════╝
EOF
    echo -e "${NC}"

    check_prerequisites
    setup_secrets
    choose_environment
    create_namespace
    deploy_chart
    show_post_deployment

    echo ""
    print_success "Deployment workflow complete!"

    # Optional: offer cleanup
    if [ "$1" = "--cleanup" ]; then
        cleanup
    fi
}

# Show help
if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    cat << EOF
Usage: $0 [OPTIONS]

Deploy insure-iq to Kubernetes using Helm

OPTIONS:
    --help          Show this help message
    --cleanup       Offer to uninstall the release after deployment

EXAMPLES:
    # Interactive deployment
    $0

    # Deploy and allow cleanup
    $0 --cleanup

REQUIREMENTS:
    - Helm 3.x installed
    - kubectl configured to your cluster
    - Docker images available: insure-iq-backend:latest, insure-iq-web:latest

For more info, see: helm/insure-iq/README.md
EOF
    exit 0
fi

# Run main function
main "$@"
