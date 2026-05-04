#!/bin/bash

# ============================================================================
# Download commission_intake dump from S3 and restore into target database
# ============================================================================
# Usage:
#   ./scripts/restore-commission-intake.sh <dump-file-or-s3-uri>
#
# Examples:
#   ./scripts/restore-commission-intake.sh commission_intake_20260504T120000Z.dump
#   ./scripts/restore-commission-intake.sh s3://rhobots-temp/commission-intake-dumps/commission_intake_20260504T120000Z.dump
#   ./scripts/restore-commission-intake.sh latest
#
# Env overrides:
#   DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
#   POSTGRES_CONTAINER  (if set, restore runs inside this docker container)
#   S3_BUCKET           (default: rhobots-temp)
#   S3_PREFIX           (default: commission-intake-dumps)
#   AWS_REGION          (default: us-east-1)
#   DATA_ONLY=1         restore data only (schema must already exist)
#   SCHEMA_ONLY=1       restore schema only
#   TRUNCATE=1          TRUNCATE target tables before restore (data-only safety)
#   DROP_FIRST=1        DROP target tables before restore (full restore)
# ============================================================================

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <dump-file-or-s3-uri|latest>" >&2
  exit 1
fi

INPUT="$1"

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
DB_PASSWORD="${DB_PASSWORD:-postgres}"
DB_NAME="${DB_NAME:-insure_iq}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-}"
S3_BUCKET="${S3_BUCKET:-rhobots-temp}"
S3_PREFIX="${S3_PREFIX:-commission-intake-dumps}"
AWS_REGION="${AWS_REGION:-us-east-1}"

TABLES=(
  "public.commission_intake_cicsresult"
  "public.commission_intake_commissiondocument"
  "public.commission_intake_extractionpass"
  "public.commission_intake_indexedcontent"
  "public.commission_intake_validationresult"
)

# ----------------------------------------------------------------------------
# Resolve dump source -> local file path
# ----------------------------------------------------------------------------
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

resolve_dump() {
  local input="$1"

  if [ "$input" = "latest" ]; then
    echo "==> Looking up latest dump in s3://${S3_BUCKET}/${S3_PREFIX}/" >&2
    local key
    key=$(aws s3api list-objects-v2 \
      --bucket "$S3_BUCKET" \
      --prefix "${S3_PREFIX}/commission_intake_" \
      --region "$AWS_REGION" \
      --query 'sort_by(Contents, &LastModified)[-1].Key' \
      --output text)
    if [ -z "$key" ] || [ "$key" = "None" ]; then
      echo "ERROR: no dumps found in s3://${S3_BUCKET}/${S3_PREFIX}/" >&2
      exit 1
    fi
    input="s3://${S3_BUCKET}/${key}"
  fi

  if [[ "$input" == s3://* ]]; then
    local fname
    fname=$(basename "$input")
    local dest="${TMP_DIR}/${fname}"
    echo "==> Downloading $input" >&2
    aws s3 cp "$input" "$dest" --region "$AWS_REGION" >&2
    echo "$dest"
  elif [ -f "$input" ]; then
    echo "$input"
  else
    echo "ERROR: dump not found: $input" >&2
    exit 1
  fi
}

DUMP_PATH=$(resolve_dump "$INPUT")
echo "==> Using dump: $DUMP_PATH"

# ----------------------------------------------------------------------------
# Build pg_restore / psql commands (host vs container)
# ----------------------------------------------------------------------------
PG_RESTORE_FLAGS=(--no-owner --no-privileges --verbose -d "$DB_NAME")

if [ "${SCHEMA_ONLY:-0}" = "1" ]; then
  PG_RESTORE_FLAGS+=(--schema-only)
elif [ "${DATA_ONLY:-0}" = "1" ]; then
  PG_RESTORE_FLAGS+=(--data-only --disable-triggers --single-transaction)
fi

run_psql() {
  local sql="$1"
  if [ -n "$POSTGRES_CONTAINER" ]; then
    docker exec -e PGPASSWORD="$DB_PASSWORD" "$POSTGRES_CONTAINER" \
      psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -c "$sql"
  else
    PGPASSWORD="$DB_PASSWORD" psql \
      -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
      -v ON_ERROR_STOP=1 -c "$sql"
  fi
}

run_pg_restore() {
  local dump="$1"
  if [ -n "$POSTGRES_CONTAINER" ]; then
    local container_path="/tmp/$(basename "$dump")"
    docker cp "$dump" "${POSTGRES_CONTAINER}:${container_path}"
    docker exec -e PGPASSWORD="$DB_PASSWORD" "$POSTGRES_CONTAINER" \
      pg_restore -U "$DB_USER" "${PG_RESTORE_FLAGS[@]}" "$container_path" || true
    docker exec "$POSTGRES_CONTAINER" rm -f "$container_path"
  else
    PGPASSWORD="$DB_PASSWORD" pg_restore \
      -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" \
      "${PG_RESTORE_FLAGS[@]}" "$dump" || true
  fi
}

# ----------------------------------------------------------------------------
# Pre-flight: connection check
# ----------------------------------------------------------------------------
echo "==> Checking DB connectivity"
if [ -n "$POSTGRES_CONTAINER" ]; then
  if ! docker ps --format '{{.Names}}' | grep -qx "$POSTGRES_CONTAINER"; then
    echo "ERROR: container '$POSTGRES_CONTAINER' is not running" >&2
    exit 1
  fi
  echo "    via docker container: $POSTGRES_CONTAINER -> $DB_NAME"
else
  echo "    via host: ${DB_HOST}:${DB_PORT} -> $DB_NAME"
fi
run_psql "SELECT 1;" >/dev/null

# ----------------------------------------------------------------------------
# Optional: drop or truncate existing tables
# ----------------------------------------------------------------------------
if [ "${DROP_FIRST:-0}" = "1" ]; then
  echo "==> Dropping existing target tables (CASCADE)"
  for t in "${TABLES[@]}"; do
    run_psql "DROP TABLE IF EXISTS ${t} CASCADE;"
  done
elif [ "${TRUNCATE:-0}" = "1" ]; then
  echo "==> Truncating target tables"
  TRUNCATE_LIST=$(IFS=, ; echo "${TABLES[*]}")
  run_psql "TRUNCATE ${TRUNCATE_LIST} RESTART IDENTITY CASCADE;"
fi

# ----------------------------------------------------------------------------
# Restore
# ----------------------------------------------------------------------------
echo "==> Restoring dump into $DB_NAME"
run_pg_restore "$DUMP_PATH"

echo ""
echo "Done."
echo "Restored tables:"
printf '  - %s\n' "${TABLES[@]}"
