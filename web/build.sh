#!/bin/bash

# ------------------------
# Configurable Defaults
# ------------------------

DOCKER_IMAGE=${DOCKER_IMAGE:-insure-iq-web:latest}
DOCKERFILE=${DOCKERFILE:-web/Dockerfile.dev}

NUXT_PUBLIC_AUTH_BASE_URL=${NUXT_PUBLIC_AUTH_BASE_URL:-http://localhost:10000}
NUXT_PUBLIC_APP_BASE_URL=${NUXT_PUBLIC_APP_BASE_URL:-http://localhost:3000}
NUXT_PUBLIC_API_SCHEME=${NUXT_PUBLIC_API_SCHEME:-http}
NUXT_PUBLIC_API_BASE_URL=${NUXT_PUBLIC_API_BASE_URL:-localhost:8000}

# ------------------------
# Docker Build Command
# ------------------------

echo "🛠  Building Docker image: $DOCKER_IMAGE"
docker build \
  -f "$DOCKERFILE" \
  --build-arg NUXT_PUBLIC_AUTH_BASE_URL="$NUXT_PUBLIC_AUTH_BASE_URL" \
  --build-arg NUXT_PUBLIC_APP_BASE_URL="$NUXT_PUBLIC_APP_BASE_URL" \
  --build-arg NUXT_PUBLIC_API_SCHEME="$NUXT_PUBLIC_API_SCHEME" \
  --build-arg NUXT_PUBLIC_API_BASE_URL="$NUXT_PUBLIC_API_BASE_URL" \
  -t "$DOCKER_IMAGE" .

echo "✅ Build complete"
