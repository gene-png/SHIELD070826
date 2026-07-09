# Architecture

> Authoritative spec: [`reference-docs/SHIELDv2_Master_Spec.txt`](../reference-docs/SHIELDv2_Master_Spec.txt) §§ 4, 11, 16. This document is the narrative version.

## 10,000-foot view

SHIELD is a multi-tenant web platform: one deployment serves many client tenants, isolated by a `client_id` on every business row (platform-level admins — `User.client_id IS NULL` — select the active tenant via an `X-Client-Id` header; client-role users are pinned to their own tenant). See `DECISIONS.md` D-015, which superseded the original single-tenant design. Each deployment exposes:

- A **public-facing experience** for unauthenticated users (marketing, intake start).
- An **operational dashboard** for Admins (Kentro consultants) and Reviewers.
- An **executive experience** for Client leadership.

All three experiences are delivered by one Next.js app talking to one FastAPI service, with shared infrastructure (Postgres, Redis, S3, Keycloak). There is **no Celery worker**: AI extraction, exports, and notifications all run synchronously inside the API process (see `DECISIONS.md` D-016). Redis is present for caching and rate-limit buckets but has no queue consumer today.

## Components

```
┌────────────────────────────────────────────────────────────┐
│                  Browser (3 experiences)                   │
└──────────────────┬─────────────────────────────────────────┘
                   │ TLS 1.2+
                   ▼
┌────────────────────────────────────────────────────────────┐
│  apps/web — Next.js 14 (App Router, TS strict)             │
│  • NextAuth (Credentials → SHIELD-issued JWT for v1;       │
│    OIDC via Keycloak for v1.x onward)                      │
│  • Tailwind + shadcn (Round 6 design language)             │
│  • Server Components + Server Actions for write paths      │
└──────────────────┬─────────────────────────────────────────┘
                   │ HTTPS (server-side calls)
                   ▼
┌────────────────────────────────────────────────────────────┐
│  apps/api — FastAPI (Python 3.12)                          │
│  • Pydantic v2 schemas                                     │
│  • SQLAlchemy 2 + Alembic                                  │
│  • Global exception handler → correlation-id-only 500      │
│  • JSON structured logs to stdout                          │
│  • PII redactor as a SECURITY BOUNDARY on every LLM call   │
└──────┬──────────────┬──────────────┬──────────────┬────────┘
       │              │              │              │
       ▼              ▼              ▼              ▼
   Postgres 16    Redis 7        MinIO (S3)    Keycloak 25
   (data model)  (cache +       (object        (OIDC IdP,
                  rate limit)    storage)       realm export)

   NO worker: extraction (LLM), PDF/XLSX export, and notification
   fan-out all run SYNCHRONOUSLY inside the api process. apps/worker/
   holds only a .gitkeep. Redis has no queue consumer today.
```

## Tech stack (Master Spec §2 - locked)

| Layer          | Choice                                                                                     | Rationale                                                                                                  |
| -------------- | ------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------- |
| Frontend       | Next.js 14 App Router + React + TypeScript + Tailwind + shadcn/ui (self-hosted, copied in) | Locked by spec; matches Round 6 design language; SSR for executive PDFs                                    |
| Backend        | FastAPI on Python 3.12                                                                     | Locked by spec; native async; OpenAPI for type generation                                                  |
| Database       | PostgreSQL 16                                                                              | Locked by spec; row-level security available; Alembic migrations                                           |
| Cache          | Redis 7                                                                                    | Locked by spec; used for caching + rate-limit buckets. No Celery broker in use — AI is synchronous (D-016) |
| Object storage | S3-compatible (MinIO in dev; AWS S3 + KMS or Azure Blob in prod)                           | Locked by spec                                                                                             |
| IdP            | Keycloak 25 (OIDC)                                                                         | Federable to any external IdP for v1.x                                                                     |
| Async          | None (synchronous)                                                                         | AI/exports/notifications run inline in the API request; no Celery/worker in v1 (D-016)                     |
| Migrations     | Alembic                                                                                    | Locked by spec - no manual schema edits, ever                                                              |
| Tests          | pytest + Playwright + axe-core/Pa11y                                                       | Tests run as part of CI; accessibility enforced                                                            |

## Data isolation

The platform is **multi-tenant** (D-015, which superseded the original single-tenant design in Master Spec §2). Every business table carries a `client_id`; every data route filters by it. `User.client_id` is `NULL` for platform-level admins, who pick the active tenant via the `X-Client-Id` header; client-role users are pinned to their own `client_id` and cannot escape it. (The role model today is `UserRole = {admin, client}` — the reviewer role was collapsed into admin.) Id-based fetches verify tenant ownership and return 404 (no existence oracle) on mismatch — see `apps/api/app/tenant.py`.

## Audit log

Every state-changing route writes one row to the `audit_entries` table (model: `apps/api/app/models/audit_entry.py`). Append-only is enforced at two layers: a Postgres trigger created in migration `0001_initial_schema.py` blocks `UPDATE`/`DELETE` on Postgres, and a SQLAlchemy `before_flush` event listener raises `AuditEntryImmutableError` so the same invariant holds on the SQLite test DB. The only blessed insert path is `apps/api/app/audit/spine.py::audit()`; routes never construct `AuditEntry` directly. The audit row records: actor user id, action verb, target type + id, details (JSON), correlation id, timestamp.

## AI integration boundary

```
caller → redact_for_ai(text) → LLM provider → response (placeholders remain)
              │
              └── audit row written before send
```

- The redactor is `apps/api/app/ai/redact.py`; its public entry points are `redact_for_ai()` (free text) and `redact_payload()` (structured payloads). PII is replaced with **placeholders that are never reversed** — there is no `unredact`/restore step, by design: redacted values must not travel back out of the model boundary. It is the security boundary, not a convenience.
- Provider is selected by `SHIELD_LLM_PROVIDER`. No code references a specific endpoint.
- `SHIELD_LLM_MODE=fixture` short-circuits to canned responses for offline tests.

## Failure model

- 4xx renders a plain-English page; never a JSON body in the user surface.
- 5xx returns a page with the correlation ID only — no stack trace, no internal error message.
- Async jobs have explicit status, retry policy, and failure notifications.
