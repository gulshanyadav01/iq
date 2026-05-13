#!/usr/bin/env bash

# Generate a workspace-specific .env from .env.example by picking random free
# host ports. Run this in a fresh Conductor workspace so its stack doesn't
# collide with siblings sharing the same repo.

set -euo pipefail

cd "$(dirname "$0")/.."

ENV_FILE=".env"
EXAMPLE_FILE=".env.example"
WORKSPACE_NAME="$(basename "$(pwd)")"

if [ ! -f "$EXAMPLE_FILE" ]; then
  echo "❌ $EXAMPLE_FILE not found in $(pwd)" >&2
  exit 1
fi

FORCE=0
for arg in "${@:-}"; do
  case "$arg" in
    -f|--force) FORCE=1 ;;
    -h|--help)
      cat <<EOF
Usage: $(basename "$0") [--force]

Generates .env from .env.example with random free host ports and a
workspace-specific DOCKER_NETWORK_NAME.
EOF
      exit 0
      ;;
  esac
done

if [ -f "$ENV_FILE" ] && [ "$FORCE" -ne 1 ]; then
  echo "❌ $ENV_FILE already exists. Re-run with --force to overwrite." >&2
  exit 1
fi

PORT_LOW=20000
PORT_HIGH=50000
USED_PORTS=()

is_port_free() {
  ! lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

pick_free_port() {
  local p i
  for i in $(seq 1 500); do
    p=$(( RANDOM % (PORT_HIGH - PORT_LOW + 1) + PORT_LOW ))
    local clash=0
    for used in "${USED_PORTS[@]:-}"; do
      if [ "$used" = "$p" ]; then clash=1; break; fi
    done
    if [ "$clash" -eq 0 ] && is_port_free "$p"; then
      USED_PORTS+=("$p")
      echo "$p"
      return
    fi
  done
  echo "❌ Failed to find a free port in ${PORT_LOW}-${PORT_HIGH}" >&2
  exit 1
}

API_PORT=$(pick_free_port)
APP_PORT=$(pick_free_port)
AUTH_PORT=$(pick_free_port)
PROXY_PORT=$(pick_free_port)
MINIO_PORT=$(pick_free_port)
MINIO_CONSOLE_PORT=$(pick_free_port)
REDIS_HOST_PORT=$(pick_free_port)
POSTGRES_HOST_PORT=$(pick_free_port)

NETWORK_NAME="app_template_network_${WORKSPACE_NAME}"

cp "$EXAMPLE_FILE" "$ENV_FILE"

sed_inplace() {
  if [ "$(uname)" = "Darwin" ]; then
    sed -i '' "$@"
  else
    sed -i "$@"
  fi
}

set_kv() {
  local key="$1" val="$2"
  sed_inplace -E "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
}

set_kv API_PORT "$API_PORT"
set_kv APP_PORT "$APP_PORT"
set_kv AUTH_PORT "$AUTH_PORT"
set_kv PROXY_PORT "$PROXY_PORT"
set_kv MINIO_PORT "$MINIO_PORT"
set_kv MINIO_CONSOLE_PORT "$MINIO_CONSOLE_PORT"
set_kv REDIS_HOST_PORT "$REDIS_HOST_PORT"
set_kv POSTGRES_HOST_PORT "$POSTGRES_HOST_PORT"
set_kv DOCKER_NETWORK_NAME "$NETWORK_NAME"

# Browser-facing URLs that route through the proxy.
set_kv CSRF_TRUSTED_ORIGINS "http://localhost:${PROXY_PORT},"
set_kv HOST_NAME "http://localhost:${PROXY_PORT}"
set_kv NUXT_PUBLIC_AUTH_BASE_URL "http://localhost:${PROXY_PORT}/auth/api/auth"
set_kv NUXT_PUBLIC_APP_BASE_URL "http://localhost:${PROXY_PORT}"
set_kv NUXT_PUBLIC_API_BASE_URL "localhost:${PROXY_PORT}/service-api"
set_kv BETTER_AUTH_URL "http://localhost:${PROXY_PORT}/auth"
set_kv TRUSTED_ORIGINS "http://localhost:${PROXY_PORT}"

# MinIO direct (S3 public endpoint reached from host/browser).
set_kv AWS_S3_PUBLIC_ENDPOINT_URL "http://localhost:${MINIO_PORT}"

echo "✅ Generated $ENV_FILE for workspace '${WORKSPACE_NAME}'"
echo ""
printf "  %-22s %s\n" "API_PORT"            "$API_PORT"
printf "  %-22s %s\n" "APP_PORT"            "$APP_PORT"
printf "  %-22s %s\n" "AUTH_PORT"           "$AUTH_PORT"
printf "  %-22s %s\n" "PROXY_PORT"          "$PROXY_PORT"
printf "  %-22s %s\n" "MINIO_PORT"          "$MINIO_PORT"
printf "  %-22s %s\n" "MINIO_CONSOLE_PORT"  "$MINIO_CONSOLE_PORT"
printf "  %-22s %s\n" "REDIS_HOST_PORT"     "$REDIS_HOST_PORT"
printf "  %-22s %s\n" "POSTGRES_HOST_PORT"  "$POSTGRES_HOST_PORT"
printf "  %-22s %s\n" "DOCKER_NETWORK_NAME" "$NETWORK_NAME"
echo ""
echo "App will be available at http://localhost:${PROXY_PORT}"
