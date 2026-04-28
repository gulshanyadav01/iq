#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="./docker-compose.yml"
SERVICE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service|-s)
      SERVICE="$2"
      shift 2
      ;;
    *)
      echo "Usage: $0 [--service web|backend]" >&2
      exit 1
      ;;
  esac
done

case "$SERVICE" in
  web)
    TARGETS="web"
    ;;
  backend)
    TARGETS="backend celery celery_policy_extract celery_commission_intake beat"
    ;;
  "")
    TARGETS=""
    ;;
  *)
    echo "Unknown service '$SERVICE'. Use 'web' or 'backend'." >&2
    exit 1
    ;;
esac

# shellcheck disable=SC2086
docker compose -f "$COMPOSE_FILE" build $TARGETS
