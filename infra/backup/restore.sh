#!/usr/bin/env bash
# SHIELD restore - cloud-agnostic counterpart to backup.sh. Restores Postgres
# from a gzipped pg_dump and pushes the artifacts back into the object store.
#
# Env:
#   BACKUP_DIR            the timestamped directory produced by backup.sh.
#                         If unset, the newest child of BACKUP_ROOT is used.
#   BACKUP_ROOT           default: ./backups
#   DATABASE_URL          TARGET db to restore into; +driver suffix stripped
#                         (default: postgresql://shield:shield@localhost:5432/shield)
#   S3_ENDPOINT_URL / S3_ACCESS_KEY / S3_SECRET_KEY / S3_BUCKET
#   SKIP_DATABASE=1       skip the psql restore
#   SKIP_OBJECT_STORAGE=1 skip the bucket restore
#
# The target database must already exist (create it empty first). The dump is
# plain-format and recreates its own tables, so restoring into an empty DB is
# a clean round trip.
set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-./backups}"
DATABASE_URL="${DATABASE_URL:-postgresql://shield:shield@localhost:5432/shield}"
S3_ENDPOINT_URL="${S3_ENDPOINT_URL:-http://localhost:9000}"
S3_ACCESS_KEY="${S3_ACCESS_KEY:-shield-minio}"
S3_SECRET_KEY="${S3_SECRET_KEY:-shield-minio-secret}"
S3_BUCKET="${S3_BUCKET:-shield-artifacts}"

log() { printf '[restore] %s\n' "$*" >&2; }
# Pure-bash (no sed): SQLAlchemy postgresql+psycopg:// -> libpq postgresql://
pg_url() {
  local u="$1"
  case "$u" in
    postgresql+*://*) u="postgresql://${u#postgresql+*://}" ;;
  esac
  printf '%s' "$u"
}

# Resolve BACKUP_DIR (newest timestamped child if not given explicitly).
if [ -z "${BACKUP_DIR:-}" ]; then
  BACKUP_DIR="$(ls -1d "${BACKUP_ROOT%/}"/*/ 2>/dev/null | sort | tail -n1 || true)"
fi
BACKUP_DIR="${BACKUP_DIR%/}"
if [ -z "${BACKUP_DIR}" ] || [ ! -d "${BACKUP_DIR}" ]; then
  log "no backup directory found (BACKUP_DIR='${BACKUP_DIR}')"
  exit 1
fi
log "restoring from: ${BACKUP_DIR}"

# --- Postgres --------------------------------------------------------------
if [ "${SKIP_DATABASE:-0}" = "1" ] || ! command -v psql >/dev/null 2>&1; then
  log "skipping database restore (SKIP_DATABASE or psql not on PATH)"
else
  url="$(pg_url "${DATABASE_URL}")"
  dump="$(ls -1 "${BACKUP_DIR}"/db/*.sql.gz 2>/dev/null | head -n1 || true)"
  if [ -z "${dump}" ]; then
    log "no db/*.sql.gz in backup; nothing to restore"
  else
    log "psql restore <- $(basename "${dump}")"
    gunzip -c "${dump}" | psql --set ON_ERROR_STOP=1 -q -d "${url}" >&2
  fi
fi

# --- Object storage --------------------------------------------------------
if [ "${SKIP_OBJECT_STORAGE:-0}" = "1" ] || ! command -v mc >/dev/null 2>&1; then
  log "skipping object-storage restore (SKIP_OBJECT_STORAGE or mc not on PATH)"
else
  export MC_HOST_shieldbak="http://${S3_ACCESS_KEY}:${S3_SECRET_KEY}@${S3_ENDPOINT_URL#http://}"
  mc mb --ignore-existing "shieldbak/${S3_BUCKET}" >&2 || true
  log "mc mirror artifacts/ -> bucket '${S3_BUCKET}'"
  mc mirror --overwrite "${BACKUP_DIR}/artifacts" "shieldbak/${S3_BUCKET}" >&2
fi

log "restore complete"
