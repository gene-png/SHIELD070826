# Operations

> Stub - populated during Phase 6 (Polish and harden). The contents below are the planned shape, not implemented detail.

## Deployment targets

- **AWS GovCloud** — Terraform plan under `infra/terraform/aws-govcloud/`.
- **Azure Government** — Terraform plan under `infra/terraform/azure-gov/`.
- No third-party CDNs. All assets served from the deployment's own origin.

## Runtime components

| Component      | Image                                                   | Notes                                                                                                                                                     |
| -------------- | ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| api            | `apps/api/Dockerfile` (least-privilege user, no sudo)   | uvicorn + workers per `WEB_CONCURRENCY`. AI extraction, exports, and notifications run **synchronously in-process** — there is no separate worker (D-015) |
| web            | `apps/web/Dockerfile`                                   | Next.js standalone output                                                                                                                                 |
| db             | managed Postgres 16 (RDS / Azure Database for Postgres) | KMS-encrypted at rest; PITR enabled                                                                                                                       |
| redis          | managed Redis 7 (ElastiCache / Azure Cache)             | Multi-AZ                                                                                                                                                  |
| object storage | S3 + KMS or Azure Blob + KMS                            | Versioning ON; bucket-level encryption; deny anonymous; tight bucket policy                                                                               |
| OIDC           | Keycloak (self-hosted) or federated to customer IdP     | Realm export checked into `infra/keycloak/`                                                                                                               |
| secrets        | AWS Secrets Manager or Azure Key Vault                  | Bootstrapped via Terraform; rotated quarterly                                                                                                             |

## Backups

An implemented, cloud-agnostic backup/restore pair ships under `infra/backup/` and is documented step-by-step in [`docs/runbooks/backup-restore.md`](runbooks/backup-restore.md):

- `infra/backup/backup.sh` — `pg_dump` of Postgres plus an `mc mirror` of the artifacts bucket into a single timestamped directory.
- `infra/backup/restore.sh` — restores the database and re-syncs the artifacts bucket.
- `infra/backup/restore-drill.sh` — a self-contained restore drill that round-trips a seeded record + artifact against the compose stack (also wired into CI, non-blocking for its first sprint).

In managed cloud production these are complemented by platform features: Postgres point-in-time recovery + nightly snapshot to a separate region; object-storage versioning + cross-region replication on the artifacts bucket; weekly Keycloak realm export. Encryption at rest is configured on the backup target (SSE-KMS / customer-managed key), noted in `backup.sh` — no KMS is bundled here.

## Key rotation

- KMS keys rotated annually (automatic on AWS).
- Database credentials rotated quarterly (Secrets Manager rotation lambda).
- API JWT signing key rotated every 90 days; old keys kept hot for the JWT TTL window then archived.

## Monitoring + alerting

- Structured JSON logs to CloudWatch / Log Analytics.
- Metrics: Prometheus exposition from `api` and `web` (there is no worker process).
- Alerts: latency p95 > 1s; 5xx rate > 1%; queue depth > 1000; redactor failure (page immediately).

## Incident response

Runbooks live under `docs/runbooks/`. Status today:

- `backup-restore.md` — **written** (backup schedule, retention, restore procedure).
- `incident-response.md`, `key-rotation.md`, `disaster-recovery.md`, `redactor-failure.md` — **planned, not yet written.**

Each runbook lists: signal → triage steps → mitigation → post-incident actions.

## FedRAMP package

- SSP (System Security Plan) draft under `docs/fedramp/ssp.md` (Phase 6).
- SAR (Security Assessment Report) template under `docs/fedramp/sar.md` (Phase 6).
- POA&M (Plan of Action & Milestones) tracker under `docs/fedramp/poam.md` (Phase 6).
