#!/usr/bin/env bash
# SHIELD restore drill - proves backup.sh + restore.sh actually round-trip.
#
# Exercises the REAL infra/backup/backup.sh and infra/backup/restore.sh against
# the compose stack, then asserts a seeded record and a seeded artifact survive
# a simulated total data loss.
#
# SAFETY: this never touches the developer's real data. It uses a SCRATCH
# database (`shield_drill`) inside the running postgres instance and a SCRATCH
# bucket (`shield-drill-artifacts`) - the real `shield` DB and `shield-artifacts`
# bucket are left untouched. Both scratch resources are dropped on exit.
#
# Prereq: the db + minio services must be up. The drill will bring them up if
# they are not:  docker compose up -d --wait db minio createbuckets
#
# Usage (from repo root):  bash infra/backup/restore-drill.sh
set -euo pipefail

cd "$(dirname "$0")/../.."

DC="docker compose"
SCRATCH_DB="shield_drill"
SCRATCH_BUCKET="shield-drill-artifacts"
TOKEN="drill-$(date -u +%s)-$$"
MC_HELPER="shield-drill-mc"
FAILED=0

log() { printf '\n=== %s ===\n' "$*"; }

cleanup() {
  log "cleanup"
  $DC exec -T db psql -U shield -d postgres -c "DROP DATABASE IF EXISTS ${SCRATCH_DB};" >/dev/null 2>&1 || true
  docker exec "${MC_HELPER}" sh -c "mc rm --recursive --force shieldbak/${SCRATCH_BUCKET} >/dev/null 2>&1; mc rb --force shieldbak/${SCRATCH_BUCKET} >/dev/null 2>&1" >/dev/null 2>&1 || true
  docker rm -f "${MC_HELPER}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

log "bring up db + minio"
$DC up -d --wait db minio

# ---------------------------------------------------------------------------
# Database round-trip (runs the REAL scripts inside the postgres image, which
# carries pg_dump/psql/bash; object storage is skipped in this half).
# ---------------------------------------------------------------------------
log "seed scratch database ${SCRATCH_DB} (token=${TOKEN})"
$DC exec -T db psql -U shield -d postgres -c "DROP DATABASE IF EXISTS ${SCRATCH_DB};" >/dev/null
$DC exec -T db psql -U shield -d postgres -c "CREATE DATABASE ${SCRATCH_DB};" >/dev/null
$DC exec -T db psql -U shield -d "${SCRATCH_DB}" -c \
  "CREATE TABLE drill_sentinel(id int primary key, token text NOT NULL); INSERT INTO drill_sentinel VALUES (1, '${TOKEN}');" >/dev/null

log "backup.sh (database only)"
$DC exec -T \
  -e DATABASE_URL="postgresql://shield:shield@localhost:5432/${SCRATCH_DB}" \
  -e BACKUP_ROOT=/tmp/drill-backups \
  -e SKIP_OBJECT_STORAGE=1 \
  db bash -s < infra/backup/backup.sh

log "simulate data loss: drop and recreate an EMPTY ${SCRATCH_DB}"
$DC exec -T db psql -U shield -d postgres -c "DROP DATABASE ${SCRATCH_DB};" >/dev/null
$DC exec -T db psql -U shield -d postgres -c "CREATE DATABASE ${SCRATCH_DB};" >/dev/null

log "restore.sh (database only)"
$DC exec -T \
  -e DATABASE_URL="postgresql://shield:shield@localhost:5432/${SCRATCH_DB}" \
  -e BACKUP_ROOT=/tmp/drill-backups \
  -e SKIP_OBJECT_STORAGE=1 \
  db bash -s < infra/backup/restore.sh

log "assert the seeded record survived"
GOT_DB="$($DC exec -T db psql -U shield -d "${SCRATCH_DB}" -tAc "SELECT token FROM drill_sentinel WHERE id=1" | tr -d '[:space:]')"
if [ "${GOT_DB}" = "${TOKEN}" ]; then
  echo "PASS: database record round-tripped (token=${GOT_DB})"
else
  echo "FAIL: expected token='${TOKEN}', got '${GOT_DB}'"
  FAILED=1
fi

# ---------------------------------------------------------------------------
# Object-storage round-trip (runs the REAL scripts inside the minio/mc image,
# which carries mc/bash; the database half is skipped here). A single detached
# helper keeps the backup staging dir alive across the backup + restore calls.
# ---------------------------------------------------------------------------
log "start mc helper on the compose network"
docker rm -f "${MC_HELPER}" >/dev/null 2>&1 || true
$DC run -d --name "${MC_HELPER}" --entrypoint sleep createbuckets 600 >/dev/null

docker cp infra/backup/backup.sh "${MC_HELPER}:/tmp/backup.sh"
docker cp infra/backup/restore.sh "${MC_HELPER}:/tmp/restore.sh"

log "seed scratch bucket ${SCRATCH_BUCKET} with a sentinel object"
docker exec "${MC_HELPER}" sh -c "
  set -e
  mc alias set shieldbak http://minio:9000 \"\$S3_ACCESS_KEY\" \"\$S3_SECRET_KEY\" >/dev/null
  mc mb --ignore-existing shieldbak/${SCRATCH_BUCKET} >/dev/null
  printf '%s' '${TOKEN}' | mc pipe shieldbak/${SCRATCH_BUCKET}/sentinel.txt >/dev/null
"

log "backup.sh (object storage only)"
docker exec \
  -e S3_ENDPOINT_URL=http://minio:9000 \
  -e S3_BUCKET="${SCRATCH_BUCKET}" \
  -e BACKUP_ROOT=/tmp/drill-obj \
  -e SKIP_DATABASE=1 \
  "${MC_HELPER}" bash /tmp/backup.sh

log "simulate data loss: wipe the scratch bucket"
docker exec "${MC_HELPER}" sh -c "mc rm --recursive --force shieldbak/${SCRATCH_BUCKET} >/dev/null 2>&1 || true"

log "restore.sh (object storage only)"
docker exec \
  -e S3_ENDPOINT_URL=http://minio:9000 \
  -e S3_BUCKET="${SCRATCH_BUCKET}" \
  -e BACKUP_ROOT=/tmp/drill-obj \
  -e SKIP_DATABASE=1 \
  "${MC_HELPER}" bash /tmp/restore.sh

log "assert the seeded artifact survived"
GOT_OBJ="$(docker exec "${MC_HELPER}" sh -c "mc cat shieldbak/${SCRATCH_BUCKET}/sentinel.txt" | tr -d '[:space:]')"
if [ "${GOT_OBJ}" = "${TOKEN}" ]; then
  echo "PASS: artifact object round-tripped (token=${GOT_OBJ})"
else
  echo "FAIL: expected artifact token='${TOKEN}', got '${GOT_OBJ}'"
  FAILED=1
fi

log "restore drill result"
if [ "${FAILED}" = "0" ]; then
  echo "RESTORE DRILL PASSED"
else
  echo "RESTORE DRILL FAILED"
fi
exit "${FAILED}"
