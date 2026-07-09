#!/usr/bin/env bash
# SHIELD backup - cloud-agnostic. Dumps Postgres and syncs the artifacts bucket
# into a single timestamped backup directory.
#
# This script is deliberately infrastructure-agnostic: it talks to Postgres over
# a libpq connection string and to any S3-compatible object store via `mc`
# (MinIO client). It has no dependency on AWS, Azure, or docker. On an ops host
# or a backup container both `pg_dump` and `mc` are on PATH; in the compose
# restore drill each half runs in the image that carries the relevant tool
# (see infra/backup/restore-drill.sh), toggled by SKIP_DATABASE / SKIP_OBJECT_STORAGE.
#
# ENCRYPTION AT REST: this script writes plaintext dumps to $BACKUP_ROOT. In a
# real deployment $BACKUP_ROOT MUST be an encrypted target -- e.g. an S3 bucket
# with SSE-KMS (AWS) or an Azure Blob container with a customer-managed key, or
# a local volume on a LUKS/dm-crypt device. Wire the KMS key id in at the sync
# step (mc supports `--encrypt-key`, or use the bucket's default SSE). No KMS is
# invented here.
#
# Env:
#   BACKUP_ROOT           destination root (default: ./backups)
#   DATABASE_URL          libpq/SQLAlchemy URL; +driver suffix is stripped
#                         (default: postgresql://shield:shield@localhost:5432/shield)
#   S3_ENDPOINT_URL       object-store endpoint (default: http://localhost:9000)
#   S3_ACCESS_KEY / S3_SECRET_KEY / S3_BUCKET
#   SKIP_DATABASE=1       skip the pg_dump step
#   SKIP_OBJECT_STORAGE=1 skip the bucket sync step
#
# Prints the created backup directory as its final line.
set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-./backups}"
DATABASE_URL="${DATABASE_URL:-postgresql://shield:shield@localhost:5432/shield}"
S3_ENDPOINT_URL="${S3_ENDPOINT_URL:-http://localhost:9000}"
S3_ACCESS_KEY="${S3_ACCESS_KEY:-shield-minio}"
S3_SECRET_KEY="${S3_SECRET_KEY:-shield-minio-secret}"
S3_BUCKET="${S3_BUCKET:-shield-artifacts}"

log() { printf '[backup] %s\n' "$*" >&2; }

# Pure-bash helpers (no sed/awk): the minio/mc image has neither.
# SQLAlchemy URLs look like postgresql+psycopg://...; libpq wants postgresql://...
pg_url() {
  local u="$1"
  case "$u" in
    postgresql+*://*) u="postgresql://${u#postgresql+*://}" ;;
  esac
  printf '%s' "$u"
}
# Extract the database name (last path segment, minus any ?query).
db_name() { local u="${1%%\?*}"; printf '%s' "${u##*/}"; }
# Mask the password in a libpq URL for the manifest.
mask_url() {
  local u="$1"
  if [ "${u#*@}" != "$u" ]; then printf '%s://***@%s' "${u%%://*}" "${u#*@}"; else printf '%s' "$u"; fi
}

ts="$(date -u +%Y%m%dT%H%M%SZ)"
dest="${BACKUP_ROOT%/}/${ts}"
mkdir -p "${dest}"
log "backup directory: ${dest}"

# --- Postgres --------------------------------------------------------------
if [ "${SKIP_DATABASE:-0}" = "1" ] || ! command -v pg_dump >/dev/null 2>&1; then
  log "skipping database dump (SKIP_DATABASE or pg_dump not on PATH)"
else
  url="$(pg_url "${DATABASE_URL}")"
  name="$(db_name "${url}")"
  mkdir -p "${dest}/db"
  log "pg_dump ${name} -> db/${name}.sql.gz"
  # Plain-format dump (schema + data); pipe straight to gzip so the plaintext
  # never lands on disk unrotated.
  pg_dump --no-owner --no-privileges -d "${url}" | gzip -9 > "${dest}/db/${name}.sql.gz"
  echo "${name}" > "${dest}/db/DATABASE_NAME"
fi

# --- Object storage (artifacts bucket) -------------------------------------
if [ "${SKIP_OBJECT_STORAGE:-0}" = "1" ] || ! command -v mc >/dev/null 2>&1; then
  log "skipping object-storage sync (SKIP_OBJECT_STORAGE or mc not on PATH)"
else
  mkdir -p "${dest}/artifacts"
  export MC_HOST_shieldbak="http://${S3_ACCESS_KEY}:${S3_SECRET_KEY}@${S3_ENDPOINT_URL#http://}"
  log "mc mirror bucket '${S3_BUCKET}' -> artifacts/"
  # `mc mirror` copies every object; add SSE flags here for encryption at rest.
  mc mirror --overwrite --remove "shieldbak/${S3_BUCKET}" "${dest}/artifacts" >&2
fi

# --- Manifest --------------------------------------------------------------
{
  echo "created_utc=${ts}"
  echo "database_url=$(mask_url "$(pg_url "${DATABASE_URL}")")"
  echo "s3_endpoint=${S3_ENDPOINT_URL}"
  echo "s3_bucket=${S3_BUCKET}"
  echo "skip_database=${SKIP_DATABASE:-0}"
  echo "skip_object_storage=${SKIP_OBJECT_STORAGE:-0}"
} > "${dest}/manifest.txt"

log "backup complete"
printf '%s\n' "${dest}"
