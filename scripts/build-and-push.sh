#!/bin/bash

# ============================================================================
# Docker Build and Push Script for ECR (amd64 default, opt-in multi-arch)
# ============================================================================
# Purpose: Build and push Docker images to Amazon ECR. Builds linux/amd64 by
#          default; set MULTIARCH=1 to also build linux/arm64.
# Usage:
#   ./scripts/build-and-push.sh                      # Build all services (amd64)
#   ./scripts/build-and-push.sh backend              # Build specific service
#   IMAGE_TAG=v1.0.0 ./scripts/build-and-push.sh     # Custom tag
#   MULTIARCH=1 ./scripts/build-and-push.sh          # Build amd64 + arm64
#   DOCKER_PLATFORMS=linux/arm64 ./scripts/build-and-push.sh  # Explicit platforms (overrides MULTIARCH)
#   DRY_RUN=1 ./scripts/build-and-push.sh            # Preview without building
#   CACHE_TYPE=gha ./scripts/build-and-push.sh       # Use GitHub Actions cache (CI only)
# ============================================================================

set -e

# ============================================================================
# Configuration
# ============================================================================

# Load environment variables if .env.ecr exists
if [ -f .env.ecr ]; then
  source .env.ecr
fi

AWS_REGION="${AWS_REGION:-us-east-1}"

# Multi-arch opt-in: MULTIARCH=1 → amd64+arm64. Default: amd64 only.
# An explicit DOCKER_PLATFORMS still wins over both.
MULTIARCH="${MULTIARCH:-0}"
if [[ "$MULTIARCH" == "1" ]]; then
  DEFAULT_PLATFORMS="linux/amd64,linux/arm64"
else
  DEFAULT_PLATFORMS="linux/amd64"
fi
DOCKER_PLATFORMS="${DOCKER_PLATFORMS:-$DEFAULT_PLATFORMS}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
CACHE_TYPE="${CACHE_TYPE:-registry}"
DRY_RUN="${DRY_RUN:-0}"

# Build service to build (default: all)
BUILD_SERVICE="${1:-all}"

# Service definitions (bash 3.2 compatible)
# Format: service_name|dockerfile_path|build_context|ecr_repo_name
SERVICES=(
  "backend|backend/Dockerfile|.|insur-iq/backend"
  "web|web/Dockerfile|.|insur-iq/web"
)

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ============================================================================
# Helper Functions
# ============================================================================

log_info() {
  echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
  echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_error() {
  echo -e "${RED}[ERROR]${NC} $1"
}

log_warning() {
  echo -e "${YELLOW}[WARNING]${NC} $1"
}

# ============================================================================
# Validation Functions
# ============================================================================

validate_prerequisites() {
  log_info "Validating prerequisites..."

  # Check Docker
  if ! command -v docker &> /dev/null; then
    log_error "Docker is not installed"
    exit 1
  fi

  # Check Docker Buildx
  if ! docker buildx version &> /dev/null; then
    log_error "Docker Buildx is not installed"
    echo ""
    echo "Install with: docker buildx install"
    exit 1
  fi

  # Check AWS CLI
  if ! command -v aws &> /dev/null; then
    log_error "AWS CLI is not installed"
    exit 1
  fi

  # Check Git
  if ! command -v git &> /dev/null; then
    log_error "Git is not installed"
    exit 1
  fi

  log_success "All prerequisites installed"
}

validate_aws_config() {
  log_info "Validating AWS configuration..."

  # Check AWS credentials
  if ! aws sts get-caller-identity &> /dev/null; then
    log_error "AWS credentials not configured"
    echo ""
    echo "Configure with: aws configure"
    exit 1
  fi

  # Get account ID
  AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
  ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

  log_success "AWS credentials valid"
  log_info "  Account ID: $AWS_ACCOUNT_ID"
  log_info "  Region: $AWS_REGION"
  log_info "  ECR Registry: $ECR_REGISTRY"
}

validate_service() {
  local service=$1

  if [[ "$service" == "all" ]]; then
    return 0
  fi

  # Check if service exists in array
  local found=0
  for svc_def in "${SERVICES[@]}"; do
    local svc_name=$(echo "$svc_def" | cut -d'|' -f1)
    if [[ "$svc_name" == "$service" ]]; then
      found=1
      break
    fi
  done

  if [[ $found -eq 0 ]]; then
    log_error "Invalid service: $service"
    echo ""
    echo "Available services:"
    for svc_def in "${SERVICES[@]}"; do
      local svc_name=$(echo "$svc_def" | cut -d'|' -f1)
      echo "  - $svc_name"
    done
    echo "  - all (build all services)"
    exit 1
  fi
}

# ============================================================================
# ECR Functions
# ============================================================================

ecr_login() {
  log_info "Logging in to ECR..."

  if [[ "$DRY_RUN" == "1" ]]; then
    log_info "[DRY RUN] Would login to: $ECR_REGISTRY"
    return 0
  fi

  aws ecr get-login-password --region "$AWS_REGION" | \
    docker login --username AWS --password-stdin "$ECR_REGISTRY" > /dev/null 2>&1

  log_success "Logged in to ECR"
}

# ============================================================================
# Buildx Functions
# ============================================================================

create_buildx_builder() {
  log_info "Setting up Docker Buildx..."

  local builder_name="insur-iq-multiplatform-builder"

  # Check if builder exists
  if docker buildx inspect "$builder_name" &> /dev/null; then
    log_info "Using existing buildx builder: $builder_name"
    docker buildx use "$builder_name"
  else
    log_info "Creating new buildx builder: $builder_name"
    docker buildx create --name "$builder_name" --use --driver docker-container
  fi

  # Bootstrap the builder
  docker buildx inspect --bootstrap > /dev/null 2>&1

  log_success "Buildx ready"
}

# ============================================================================
# Git Functions
# ============================================================================

get_git_sha() {
  git rev-parse --short HEAD 2>/dev/null || echo "unknown"
}

get_git_tag() {
  git describe --exact-match --tags 2>/dev/null || echo ""
}

get_git_branch() {
  git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown"
}

# ============================================================================
# Tagging Functions
# ============================================================================

get_repo_name() {
  local service=$1
  for svc_def in "${SERVICES[@]}"; do
    local svc_name=$(echo "$svc_def" | cut -d'|' -f1)
    if [[ "$svc_name" == "$service" ]]; then
      echo "$svc_def" | cut -d'|' -f4
      return 0
    fi
  done
  return 1
}

get_image_tags() {
  local service=$1
  local repo
  repo=$(get_repo_name "$service")
  local tags=()

  # Git SHA tag (always)
  local git_sha=$(get_git_sha)
  tags+=("--tag" "${ECR_REGISTRY}/${repo}:git-${git_sha}")

  # Include latest tag unless IMAGE_TAG is "dev"
  if [[ "$IMAGE_TAG" != "dev" ]]; then
    tags+=("--tag" "${ECR_REGISTRY}/${repo}:latest")
  fi

  # Custom tag (if IMAGE_TAG is set and not "latest")
  if [[ "$IMAGE_TAG" != "latest" ]]; then
    tags+=("--tag" "${ECR_REGISTRY}/${repo}:${IMAGE_TAG}")
  fi

  # Semver tags (if git tag exists and matches semver pattern)
  local git_tag=$(get_git_tag)
  if [[ -n "$git_tag" ]] && [[ "$git_tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    # Remove 'v' prefix
    local version="${git_tag#v}"

    # Full version (e.g., 1.2.3)
    tags+=("--tag" "${ECR_REGISTRY}/${repo}:${version}")

    # Major.minor (e.g., 1.2)
    local major_minor=$(echo "$version" | cut -d'.' -f1,2)
    tags+=("--tag" "${ECR_REGISTRY}/${repo}:${major_minor}")

    # Major only (e.g., 1)
    local major=$(echo "$version" | cut -d'.' -f1)
    tags+=("--tag" "${ECR_REGISTRY}/${repo}:${major}")

    # Keep v prefix tag as well
    tags+=("--tag" "${ECR_REGISTRY}/${repo}:${git_tag}")
  fi

  echo "${tags[@]}"
}

# ============================================================================
# Build Functions
# ============================================================================

build_service() {
  local service=$1

  # Find service definition in array
  local dockerfile=""
  local build_context=""
  for svc_def in "${SERVICES[@]}"; do
    local svc_name=$(echo "$svc_def" | cut -d'|' -f1)
    if [[ "$svc_name" == "$service" ]]; then
      dockerfile=$(echo "$svc_def" | cut -d'|' -f2)
      build_context=$(echo "$svc_def" | cut -d'|' -f3)
      break
    fi
  done

  echo ""
  echo "============================================================================"
  echo "  Building: $service"
  echo "============================================================================"
  echo ""

  log_info "Service: $service"
  log_info "  Dockerfile: $dockerfile"
  log_info "  Context: $build_context"
  log_info "  Platforms: $DOCKER_PLATFORMS"

  # Get image tags
  local tags=$(get_image_tags "$service")
  log_info "  Tags:"
  for tag in $tags; do
    if [[ "$tag" != "--tag" ]]; then
      log_info "    - $tag"
    fi
  done

  # Build command base
  local build_cmd="docker buildx build"
  build_cmd="$build_cmd --platform $DOCKER_PLATFORMS"
  build_cmd="$build_cmd --file $dockerfile"
  build_cmd="$build_cmd $tags"

  # Add build cache. cache-to always carries ignore-error=true: a cache backend
  # outage (the Actions cache returned a 400 HTML error page on 2026-08-17) must
  # not fail a build whose image was already built and pushed.
  if [[ "$CACHE_TYPE" == "gha" ]]; then
    local cache_from="type=gha,scope=${service}"
    local cache_to="type=gha,scope=${service},mode=max,ignore-error=true"
  elif [[ "$CACHE_TYPE" == "local" ]]; then
    local cache_dir="/tmp/buildkit-cache/${service}"
    mkdir -p "$cache_dir"
    local cache_from="type=local,src=${cache_dir}"
    local cache_to="type=local,dest=${cache_dir},mode=max,ignore-error=true"
  else
    local repo
    repo=$(get_repo_name "$service")
    local cache_from="type=registry,ref=${ECR_REGISTRY}/${repo}:buildcache"
    local cache_to="type=registry,ref=${ECR_REGISTRY}/${repo}:buildcache,mode=max,ignore-error=true"
  fi
  build_cmd="$build_cmd --cache-from $cache_from"
  build_cmd="$build_cmd --cache-to $cache_to"

  # Push to registry
  build_cmd="$build_cmd --push"

  # Add context
  build_cmd="$build_cmd $build_context"

  # Execute or dry run
  if [[ "$DRY_RUN" == "1" ]]; then
    echo ""
    log_warning "[DRY RUN] Would execute:"
    echo "$build_cmd"
    echo ""
  else
    echo ""
    log_info "Building..."
    echo ""

    eval "$build_cmd"

    echo ""
    log_success "Build complete: $service"
  fi
}

# ============================================================================
# Main Execution
# ============================================================================

print_summary() {
  echo ""
  echo "============================================================================"
  echo "  Build Summary"
  echo "============================================================================"
  echo ""
  echo "Configuration:"
  echo "  AWS Region: $AWS_REGION"
  echo "  ECR Registry: $ECR_REGISTRY"
  echo "  Platforms: $DOCKER_PLATFORMS"
  echo "  Service: $BUILD_SERVICE"
  echo "  Git SHA: $(get_git_sha)"
  echo "  Git Branch: $(get_git_branch)"
  echo "  Cache Type: $CACHE_TYPE"

  local git_tag=$(get_git_tag)
  if [[ -n "$git_tag" ]]; then
    echo "  Git Tag: $git_tag"
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    echo ""
    log_warning "DRY RUN MODE - No images will be built"
  fi

  echo ""
}

main() {
  print_summary

  # Validate
  validate_prerequisites
  validate_service "$BUILD_SERVICE"
  validate_aws_config

  echo ""

  # Setup
  if [[ "$DRY_RUN" != "1" ]]; then
    ecr_login
    if [[ -z "$ACTIONS_CACHE_URL" ]]; then
      create_buildx_builder
    else
      log_info "GitHub Actions environment detected — using pre-configured buildx builder"
    fi
  fi

  # Build
  if [[ "$BUILD_SERVICE" == "all" ]]; then
    for svc_def in "${SERVICES[@]}"; do
      local svc_name=$(echo "$svc_def" | cut -d'|' -f1)
      build_service "$svc_name"
    done
  else
    build_service "$BUILD_SERVICE"
  fi

  # Final summary
  echo ""
  echo "============================================================================"
  echo "  All Builds Complete!"
  echo "============================================================================"
  echo ""

  if [[ "$DRY_RUN" != "1" ]]; then
    log_success "Images pushed to ECR: $ECR_REGISTRY"
    echo ""
    echo "Pushed repositories:"
    for svc_def in "${SERVICES[@]}"; do
      local svc_repo=$(echo "$svc_def" | cut -d'|' -f4)
      echo "  - ${ECR_REGISTRY}/${svc_repo}"
    done
    echo ""
    echo "To deploy via Helm, override images.* in your values file, e.g.:"
    echo "  images:"
    echo "    backend: ${ECR_REGISTRY}/$(get_repo_name backend):git-$(get_git_sha)"
    echo "    web:     ${ECR_REGISTRY}/$(get_repo_name web):git-$(get_git_sha)"
    echo ""
  fi
}

# Run main function
main "$@"
