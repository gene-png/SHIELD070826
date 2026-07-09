# SHIELD by Kentro v2.0

Enterprise cybersecurity assessment platform. Multi-tenant (many clients per deployment, isolated by `client_id`; see `DECISIONS.md` D-015), FedRAMP Moderate/High target, four-service engagement workflow:

1. **Technical Debt Review** — capability inventory, overlap analysis, consolidation plan.
2. **Zero Trust Assessment** — CISA ZTMM 2.0 and DoD ZTRA, scored per pillar with current/target maturity.
3. **NIST CSF 2.0 Assessment** — full 10-step Playbook with HIGH/MOD/LOW tiered profiles, 5-dimension scoring, weighted-floor roll-up, gap analysis, action plan.
4. **MITRE ATT&CK Coverage Mapping** — full Enterprise matrix (~600 techniques) scored against the approved capability list.

> **Authoritative spec:** [`reference-docs/SHIELDv2_Master_Spec.txt`](reference-docs/SHIELDv2_Master_Spec.txt).
> **AI build prompt:** [`reference-docs/AI_Prompt`](reference-docs/AI_Prompt).
> **Design language contract (governs UI on conflict):** [`reference-docs/Shield_UX_Round6_Design_Contract.txt`](reference-docs/Shield_UX_Round6_Design_Contract.txt).
> **Decision log:** [`DECISIONS.md`](DECISIONS.md).
> **Build status:** [`BUILD_REPORT.md`](BUILD_REPORT.md).

## Repository layout

```
apps/
  web/              Next.js 14 (App Router, TS strict, Tailwind, shadcn/ui)
  api/              FastAPI (Python 3.12) - REST API + OpenAPI
  worker/           Empty placeholder (.gitkeep only). There is NO worker: AI
                    runs synchronously inside the API. See DECISIONS.md D-015.
packages/
  design-system/    Tailwind tokens + shadcn components + label maps + copy
  shared-types/     TS types generated from apps/api OpenAPI
  csf-data/         CSF 2.0 subcategory CSV + IG metric crosswalk
  attack-data/      Vendored MITRE ATT&CK Enterprise JSON (full matrix)
  zt-data/          CISA + DoD questionnaire seed JSON
infra/
  docker/           Runtime Dockerfiles (least-privilege, no sudo)
  terraform/        IaC for AWS GovCloud / Azure Government
  keycloak/         Realm export imported on container start
docs/               Architecture, security, data model, runbooks, guides
scripts/            Seed loaders + dev helpers
reference-docs/     Locked SHIELD v2 reference documents (spec, mockup, questionnaires)
e2e/                Playwright end-to-end tests
.devcontainer/      VS Code Dev Container config (appuser + passwordless sudo)
```

## Prerequisites

- Docker Desktop (or Docker Engine) with Docker Compose v2
- VS Code with the Dev Containers extension (recommended)
- An `ANTHROPIC_API_KEY` if running real LLM calls (defaults to `fixture` mode otherwise)

All development happens inside the dev container. Nothing installs to the host.

## Quick start

### Option A - VS Code Dev Containers (recommended)

1. Open the repo in VS Code with the **Dev Containers** extension installed.
2. When prompted, **Reopen in Container**. VS Code builds the dev image and brings up the compose services (db, redis, minio, createbuckets, keycloak, mailhog, api, web). There is no worker service — AI runs synchronously in the API.
3. Once VS Code attaches, run:
   ```bash
   cp .env.example .env
   # edit .env: paste your ANTHROPIC_API_KEY and run `openssl rand -hex 32` for NEXTAUTH_SECRET
   bash scripts/dev-web.sh
   ```
4. In a second terminal:
   ```bash
   docker compose logs -f api
   ```
5. Open http://localhost:3000 once you see Next.js boot output.

### Option B - plain Docker Compose

```bash
cp .env.example .env
docker compose up -d db redis minio keycloak mailhog
docker compose up -d --build api
docker compose run --service-ports --rm web bash scripts/dev-web.sh
```

### URLs once everything is up

| Service        | URL                        |
| -------------- | -------------------------- |
| Web (Next.js)  | http://localhost:3000      |
| API (FastAPI)  | http://localhost:8000/docs |
| Keycloak admin | http://localhost:8080      |
| MinIO console  | http://localhost:9001      |
| MailHog UI     | http://localhost:8025      |
| Postgres       | postgres://localhost:5432  |

## Environment variables

Every variable in [`.env.example`](.env.example) is required. Summary:

| Group          | Vars                                                                                                      | Notes                                                                                                                                              |
| -------------- | --------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| Runtime        | `ENVIRONMENT`, `LOG_LEVEL`                                                                                |                                                                                                                                                    |
| Database       | `DATABASE_URL`                                                                                            | Postgres 16, locked in Master Spec §2                                                                                                              |
| Redis          | `REDIS_URL`                                                                                               | Ephemeral cache + rate-limit buckets. No Celery/queue consumer today (AI is synchronous)                                                           |
| Object storage | `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_KMS_KEY_ID`                         | MinIO in dev, S3+KMS in prod                                                                                                                       |
| OIDC           | `KEYCLOAK_ISSUER`, `KEYCLOAK_AUDIENCE`, `KEYCLOAK_CLIENT_ID`, `KEYCLOAK_ADMIN`, `KEYCLOAK_ADMIN_PASSWORD` |                                                                                                                                                    |
| NextAuth       | `NEXTAUTH_URL`, `NEXTAUTH_SECRET`                                                                         | Generate secret with `openssl rand -hex 32`                                                                                                        |
| LLM            | `SHIELD_LLM_PROVIDER`, `SHIELD_LLM_MODEL`, `SHIELD_LLM_MODE`, `ANTHROPIC_API_KEY`                         | `MODE=fixture` for offline tests                                                                                                                   |
| Feature flags  | `SHIELD_AUTH_REQUIRE_MFA`, `SHIELD_AUTH_REQUIRE_EMAIL_VERIFY`, `SHIELD_EMAIL_DELIVERY_ENABLED`            | All `false` for v1                                                                                                                                 |
| Redaction      | `SHIELD_REDACTION_MODE`                                                                                   | `strict` in prod; `off` forbidden outside dev                                                                                                      |
| Sessions       | `JWT_ACCESS_TTL_SECONDS`, `JWT_REFRESH_TTL_SECONDS`, `SHIELD_ACCOUNT_LOCKOUT_*`                           | Enforced: short JWT TTLs + account lockout. Idle timeout, forced re-auth, and refresh-token rotation are PLANNED, NOT PRESENT (DECISIONS.md D-017) |
| Mail           | `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`                                                                     | MailHog locally                                                                                                                                    |

## Running tests

```bash
# API unit tests
docker compose exec api pytest -m unit

# API integration tests
docker compose exec api pytest -m integration

# Web tests
docker compose exec web pnpm test

# End-to-end (Playwright)
docker compose run --rm e2e pnpm e2e

# Accessibility (axe-core / Pa11y)
docker compose run --rm e2e pnpm a11y
```

## Documentation

- [`docs/architecture.md`](docs/architecture.md) - system architecture
- [`docs/security.md`](docs/security.md) - OWASP review, redaction, audit
- [`docs/development.md`](docs/development.md) - developer onboarding
- [`docs/operations.md`](docs/operations.md) - deployment, monitoring, backup, key rotation
- [`docs/admin-guide.md`](docs/admin-guide.md) - Kentro consultant guide (filled across phases)
- [`docs/client-guide.md`](docs/client-guide.md) - client-facing guide (filled across phases)
- [`docs/runbooks/backup-restore.md`](docs/runbooks/backup-restore.md) - backup schedule, retention, and restore procedure (the other runbooks — incident, key rotation, DR — are planned, not yet written)

## Risk acceptance log

Per Master Spec §2, two risks are explicitly accepted for v1:

1. **Commercial LLM provider may not be FedRAMP-authorized.** Egress may leave the FedRAMP boundary. Mandatory PII redaction (`apps/api/app/ai/redact.py`) is the primary control. See [`docs/security.md`](docs/security.md).
2. **MFA and email verification deferred for v1.** The controls that are actually **enforced** today are the 15-minute JWT access-token lifetime and account lockout (10 failed attempts in 15 minutes). Idle timeout, daily forced re-auth, and refresh-token rotation/revocation are **PLANNED, NOT PRESENT** — `/auth/refresh` currently re-issues a token pair with no rotation and no revocation, and logout is audit-only. These, plus MFA and email verification, are scheduled into the MFA work package; refresh-token rotation + revocation is its first item (see `DECISIONS.md` D-017). Feature flags (`SHIELD_AUTH_REQUIRE_MFA`, `SHIELD_AUTH_REQUIRE_EMAIL_VERIFY`) are present but gate no enforced behavior yet.

## License

Proprietary. See [`LICENSE`](LICENSE). Operated by Kentro on behalf of customer engagements.
