# Runbook: Backup & Restore

Covers the two stateful stores that hold client assessment data:

1. **PostgreSQL** — all application data (clients, users, services, assessments, audit log).
2. **Object storage (S3 / MinIO)** — the artifacts bucket (uploaded documents, generated PDF/XLSX deliverables).

Keycloak is stateless-by-export (its realm is checked into `infra/keycloak/`); it is not part of this data-recovery path.

The scripts are **cloud-agnostic**: they talk to Postgres over a libpq connection string and to any S3-compatible store via `mc` (the MinIO client), with no dependency on a specific cloud. They live in [`infra/backup/`](../../infra/backup/).

---

## Components

| Script                          | Purpose                                                                  |
| ------------------------------- | ------------------------------------------------------------------------ |
| `infra/backup/backup.sh`        | `pg_dump` + `mc mirror` of the artifacts bucket into one timestamped dir |
| `infra/backup/restore.sh`       | Restore the DB from a dump and re-sync the artifacts bucket              |
| `infra/backup/restore-drill.sh` | Automated round-trip proof against the compose stack (used by CI)        |

Every backup lands in `${BACKUP_ROOT}/<UTC-timestamp>/`:

```
20260709T204312Z/
  db/<database>.sql.gz     gzipped plain-format pg_dump
  db/DATABASE_NAME         the dumped database name
  artifacts/               mirror of the artifacts bucket
  manifest.txt             created_utc, endpoints, skip flags (password masked)
```

---

## Configuration (environment)

| Variable                                | Default                                            | Notes                                            |
| --------------------------------------- | -------------------------------------------------- | ------------------------------------------------ |
| `BACKUP_ROOT`                           | `./backups`                                        | Destination root. **Must be encrypted at rest.** |
| `DATABASE_URL`                          | `postgresql://shield:shield@localhost:5432/shield` | `+driver` suffix (e.g. `+psycopg`) is stripped   |
| `S3_ENDPOINT_URL`                       | `http://localhost:9000`                            | Object-store endpoint                            |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY`       | `shield-minio` / `shield-minio-secret`             | Object-store credentials                         |
| `S3_BUCKET`                             | `shield-artifacts`                                 | Artifacts bucket                                 |
| `BACKUP_DIR` (restore only)             | newest child of `BACKUP_ROOT`                      | Which backup to restore                          |
| `SKIP_DATABASE` / `SKIP_OBJECT_STORAGE` | `0`                                                | Run one half only                                |

### Encryption at rest

The scripts write **plaintext** dumps to `BACKUP_ROOT`. In production `BACKUP_ROOT` MUST be an encrypted target:

- **AWS:** an S3 bucket with **SSE-KMS** (customer-managed key), or a volume on a KMS-encrypted EBS device.
- **Azure:** a Blob container with a **customer-managed key**, or an encrypted managed disk.
- **Self-hosted:** a LUKS/dm-crypt volume.

Wire the key at the sync step (`mc` supports `--encrypt-key`, or rely on the bucket's default SSE). No KMS is bundled in this repo — it is a deployment-time decision.

---

## Schedule & retention (recommended)

| Store            | Frequency                     | Retention                                         |
| ---------------- | ----------------------------- | ------------------------------------------------- |
| PostgreSQL       | Nightly full + PITR (managed) | 35 days of daily; 12 monthly; PITR window 7 days  |
| Artifacts bucket | Nightly mirror + versioning   | Object versioning retains overwrites/deletes 90 d |
| Keycloak realm   | Weekly export                 | Kept in git (`infra/keycloak/`)                   |

Run nightly from a scheduler (cron / systemd timer / cloud scheduled task) on a host that has `pg_dump` and `mc` on `PATH`:

```bash
BACKUP_ROOT=/secure/backups \
DATABASE_URL="postgresql://shield:***@db.internal:5432/shield" \
S3_ENDPOINT_URL="https://s3.us-gov-west-1.amazonaws.com" \
S3_BUCKET=shield-artifacts \
  bash infra/backup/backup.sh
```

Prune backups older than the retention window with a follow-on `find "${BACKUP_ROOT}" -maxdepth 1 -type d -mtime +35 -exec rm -rf {} +` (or the object store's lifecycle policy).

---

## Restore procedure (step by step)

> Restoring is destructive to the target database. Do it into a fresh/empty
> database or a maintenance-mode environment. The plain-format dump recreates
> its own tables, so an empty target is the clean case.

1. **Stop writers.** Scale the `api` (and `web`) down or put the environment in maintenance mode so nothing writes during restore.

2. **Pick the backup.** Default is the newest under `BACKUP_ROOT`; override with `BACKUP_DIR`:

   ```bash
   ls -1d /secure/backups/*/ | tail -5     # inspect available backups
   ```

3. **Create an empty target database** (if it does not already exist):

   ```bash
   psql "postgresql://shield:***@db.internal:5432/postgres" \
     -c 'CREATE DATABASE shield;'
   ```

4. **Run the restore:**

   ```bash
   BACKUP_ROOT=/secure/backups \
   BACKUP_DIR=/secure/backups/20260709T204312Z \
   DATABASE_URL="postgresql://shield:***@db.internal:5432/shield" \
   S3_ENDPOINT_URL="https://s3.us-gov-west-1.amazonaws.com" \
   S3_BUCKET=shield-artifacts \
     bash infra/backup/restore.sh
   ```

   This pipes `db/<database>.sql.gz` into `psql` (with `ON_ERROR_STOP`), then `mc mirror`s `artifacts/` back into the bucket.

5. **Sanity-check.** Confirm row counts and that the audit log is intact:

   ```bash
   psql "$DATABASE_URL" -c "SELECT count(*) FROM audit_entries;"
   psql "$DATABASE_URL" -c "SELECT count(*) FROM users;"
   ```

6. **Bring writers back up** and verify a login + an artifact download end-to-end.

---

## Restore drill (verification)

Backups are worthless until a restore has been proven. `infra/backup/restore-drill.sh` proves the full round trip automatically:

- Seeds a sentinel **record** in a **scratch database** (`shield_drill`) and a sentinel **artifact** in a **scratch bucket** (`shield-drill-artifacts`).
- Runs the real `backup.sh`, simulates total data loss (drops the DB, wipes the bucket), runs the real `restore.sh`.
- Asserts both the record and the artifact came back, then drops the scratch resources.

**It never touches the real `shield` database or `shield-artifacts` bucket** — only the disposable scratch resources.

```bash
docker compose up -d --wait db minio
bash infra/backup/restore-drill.sh
# ... => "RESTORE DRILL PASSED"
```

It also runs in CI (the **Restore drill** job in `.github/workflows/ci.yml`), non-blocking (`continue-on-error: true`) for its first sprint — flip to blocking once it has run green for a full sprint, exactly as the e2e job did.

---

## What is NOT covered

- No automated off-site replication is configured by these scripts; that is a deployment concern (S3 cross-region replication / a second `BACKUP_ROOT`).
- Point-in-time recovery relies on the managed Postgres offering (RDS / Azure Database), not on these logical dumps.
