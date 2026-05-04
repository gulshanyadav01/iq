#!/bin/bash

# ============================================================================
# Dump commission_intake tables and upload to S3
# ============================================================================
# Usage:
#   ./scripts/dump-commission-intake.sh
#   S3_BUCKET=other-bucket ./scripts/dump-commission-intake.sh
#   SCHEMA_ONLY=1 ./scripts/dump-commission-intake.sh
#   DATA_ONLY=1 ./scripts/dump-commission-intake.sh
# ============================================================================

set -euo pipefail

POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-insur-iq-postgres-1}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-insure_iq}"
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

TIMESTAMP=$(date -u +"%Y%m%dT%H%M%SZ")
DUMP_FILE="commission_intake_${TIMESTAMP}.dump"
LOCAL_PATH="/tmp/${DUMP_FILE}"
CONTAINER_PATH="/tmp/${DUMP_FILE}"

PG_DUMP_FLAGS=(--format=custom --no-owner --no-privileges --verbose)

if [ "${SCHEMA_ONLY:-0}" = "1" ]; then
  PG_DUMP_FLAGS+=(--schema-only)
elif [ "${DATA_ONLY:-0}" = "1" ]; then
  PG_DUMP_FLAGS+=(--data-only --disable-triggers)
fi

for t in "${TABLES[@]}"; do
  PG_DUMP_FLAGS+=(--table="$t")
done

echo "==> Checking docker container: $POSTGRES_CONTAINER"
if ! docker ps --format '{{.Names}}' | grep -qx "$POSTGRES_CONTAINER"; then
  echo "ERROR: container '$POSTGRES_CONTAINER' is not running" >&2
  exit 1
fi

echo "==> Checking AWS credentials"
if ! aws sts get-caller-identity --region "$AWS_REGION" >/dev/null 2>&1; then
  echo "ERROR: AWS credentials not configured for region $AWS_REGION" >&2
  exit 1
fi

echo "==> Dumping tables from $DB_NAME"
printf '   - %s\n' "${TABLES[@]}"

docker exec "$POSTGRES_CONTAINER" pg_dump \
  -U "$DB_USER" \
  -d "$DB_NAME" \
  "${PG_DUMP_FLAGS[@]}" \
  -f "$CONTAINER_PATH"

echo "==> Copying dump out of container"
docker cp "${POSTGRES_CONTAINER}:${CONTAINER_PATH}" "$LOCAL_PATH"
docker exec "$POSTGRES_CONTAINER" rm -f "$CONTAINER_PATH"

DUMP_SIZE=$(du -h "$LOCAL_PATH" | cut -f1)
echo "==> Dump size: $DUMP_SIZE"

S3_URI="s3://${S3_BUCKET}/${S3_PREFIX}/${DUMP_FILE}"
echo "==> Uploading to $S3_URI"
aws s3 cp "$LOCAL_PATH" "$S3_URI" --region "$AWS_REGION"

rm -f "$LOCAL_PATH"

echo ""
echo "Done."
echo "S3:    $S3_URI"
echo ""
echo "Restore on target (after schema exists):"
echo "  aws s3 cp $S3_URI ./${DUMP_FILE}"
echo "  pg_restore -U <user> -d <db> --data-only --disable-triggers ./${DUMP_FILE}"
