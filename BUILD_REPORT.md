# SHIELD v2.0 — Build Report

> Live build status. Per AI Prompt §14, Eugene reads this first.
> See [`How to resume an interrupted build`](#how-to-resume-an-interrupted-build) below for resume instructions.

## Latest change — 2026-05-21

**Multi-tenant: the platform now supports many clients per deployment.** Single-tenant assumption removed end-to-end (schema, auth, every data route, frontend). See `DECISIONS.md` D-015 and `CHANGELOG.md` for the full record.

Highlights:

- Alembic migration `0013` adds `client_id` to `services`, `service_requests`, `artifacts`; backfills + enforces `NOT NULL` on every business `client_id`.
- New `current_client` FastAPI dependency + `app/tenant.py` helpers; every data route now requires it and 404s on cross-tenant access.
- New admin endpoints `GET/POST /admin/clients`, `GET /admin/clients/{id}`. Intake queue accepts an optional `client_id` query param.
- Frontend client switcher added to the top nav (admin/reviewer only). Cookie-driven `X-Client-Id` is forwarded automatically through `apps/web/src/lib/api.ts`.
- Cross-tenant isolation tests in `apps/api/tests/unit/test_multi_tenant_isolation.py` (not yet executed — local Python env unavailable in this session).

## Overall status

**Phase 2 complete (`v0.2.0`). Phase 3 (Tech Debt service) next.**

| Phase                                                     | Status                     | Last tag            |
| --------------------------------------------------------- | -------------------------- | ------------------- |
| Opening commit (scaffold)                                 | Complete                   | (untagged)          |
| **Phase 1 — Foundation**                                  | **Complete**               | `v0.1.0`            |
| Phase 2 stage 1 — Intake data model                       | Complete                   | `v0.2.1`            |
| Phase 2 stage 2 — Intake API routes                       | Complete                   | `v0.2.2`            |
| Phase 2 stage 3 — Wizard skeleton                         | Complete                   | `v0.2.3`            |
| Phase 2 stage 4 — Per-step forms + auto-save              | Complete                   | `v0.2.4`            |
| Phase 2 stage 5 — Section-tabbed questionnaire renderer   | Complete                   | `v0.2.5`            |
| Phase 2 stage 6 — Document upload + redaction disclosure  | Complete                   | `v0.2.6`            |
| Phase 2 stage 7 — Admin queue + role-based authz          | Complete                   | `v0.2.7`            |
| Phase 2 stage 8 — Notifications + Phase 2 acceptance gate | **Complete (this commit)** | `v0.2.8` / `v0.2.0` |
| **Phase 2 — Intake**                                      | **Complete**               | `v0.2.0`            |
| Phase 3 — Tech Debt service                               | **Next**                   | —                   |
| Phase 4 — CSF service                                     | Not started                | —                   |
| Phase 5 — Zero Trust + ATT&CK                             | Not started                | —                   |
| Phase 6 — Polish and harden                               | Not started                | —                   |

## Phase 2 acceptance criteria (Master Spec §15 Phase 2)

| Criterion                                                                                                     | Status   | Evidence                                                                                                                                                                                                                                                                                                |
| ------------------------------------------------------------------------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A new client can complete intake end-to-end without seeing any internal vocabulary, raw JSON, or stack trace. | **PASS** | 6-step wizard at `/intake`; copy comes from plain-English `SERVICE_LABELS` map (no enum slugs in UI); Step 6 renders structured `<dl>` summary (no JSON dumps); global exception handler returns correlation-ID-only 500s (verified by `test_unhandled_exception_returns_500_without_stack_trace`).     |
| Submitting intake reflects correctly in the admin queue with the new-lead timestamp.                          | **PASS** | `POST /intake/submit` stamps `client.intake_completed_at = utcnow()` (Phase 2 stage 2); admin queue at `/admin/queue` renders the timestamp in a `StatusPill`; notification fan-out via `notify_role(ADMIN, ...)` writes a `intake.submitted` notification linking to `/admin/queue` (Phase 2 stage 8). |
| All intake data round-trips correctly: client enters X, admin reads X (no v1 data-binding leak).              | **PASS** | Verified by `test_admin_queue_reflects_submitted_intake` — client posts `legal_name=Atlas Defense Solutions, industry=Defense, service_requests=[nist_csf, consultation]`, admin queue returns the same fields verbatim with the requester user summary joined in.                                      |

**All 3 acceptance criteria fully met.**

## OWASP Top 10 cumulative review (as of `v0.2.0`)

| ID  | Category                  | Status                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| --- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A01 | Broken Access Control     | PASS — `current_user` for authenticated routes; `require_role(*allowed)` returns 403 (not 401) for role mismatches; admin layout double-checks server-side                                                                                                                                                                                                                                                                                                                                  |
| A02 | Cryptographic Failures    | PASS — Argon2id (OWASP cheat-sheet) + HS256 JWT; placeholder secret refused in production; sha256 captured on every upload; S3 backend applies SSE=KMS in prod                                                                                                                                                                                                                                                                                                                              |
| A03 | Injection                 | PASS — SQLAlchemy parameterized queries only; storage keys app-generated; filename sanitization on uploads                                                                                                                                                                                                                                                                                                                                                                                  |
| A04 | Insecure Design           | PASS — append-only audit log at two layers; MIME allowlist + size cap on uploads; redaction disclosure shown _before_ the upload action; service-request lifecycle has explicit states                                                                                                                                                                                                                                                                                                      |
| A05 | Security Misconfiguration | PASS — `assert_safe_for_runtime` refuses unsafe production combos; HSTS + X-Frame-Options + Permissions-Policy + Referrer-Policy at the edge                                                                                                                                                                                                                                                                                                                                                |
| A06 | Vulnerable Components     | PARTIAL — versions pinned; pip-audit + pnpm audit + Dependabot land in Phase 6                                                                                                                                                                                                                                                                                                                                                                                                              |
| A07 | ID & Auth Failures        | PARTIAL — email+password + Argon2id + lockout + account-existence oracle defense done; MFA + email verification deferred per Master Spec §2. NO further compensating controls are enforced: idle timeout and forced re-auth are loaded but never applied, and `/auth/refresh` re-issues a token pair with no rotation or revocation (logout is audit-only). These are PLANNED, NOT PRESENT — deferred to the MFA work package, refresh-token rotation/revocation first (DECISIONS.md D-017) |
| A08 | Software & Data Integrity | PASS — audit rows immutable by contract; sha256 stored + audited on every upload                                                                                                                                                                                                                                                                                                                                                                                                            |
| A09 | Logging & Monitoring      | PASS — structured JSON + correlation IDs everywhere; audit log on every state change; admin notification fan-out on intake submit                                                                                                                                                                                                                                                                                                                                                           |
| A10 | SSRF                      | PASS — LLM endpoint env-configured only; no user-supplied URLs anywhere                                                                                                                                                                                                                                                                                                                                                                                                                     |

## Tests at HEAD

```
$ /tmp/shield-api-venv/bin/python -m pytest -m unit \
    apps/api/tests/unit --rootdir apps/api -q
72 passed

$ pnpm format:check && pnpm -F web lint && pnpm -F web typecheck && pnpm -F web build
prettier: All matched files use Prettier code style!
eslint:   No ESLint warnings or errors
tsc:      No errors
next:     16 routes built; 87.2 kB First Load JS shared
```

## Open items

1. **Docker not available in current container.** The 8-service stack (Postgres + Redis + MinIO + Keycloak + MailHog + api + worker + web) requires `docker compose up` from a host with Docker. The dev SQLite + LocalFilesystemStorage demo (running now at http://localhost:3000) covers the user-facing flows but doesn't exercise Postgres-specific behavior (the audit-trigger smoke + KMS encryption).
2. **No notification bell UI in the header.** API + notifications shipped (admin gets a row on every intake submit); UI surfacing lands as a small follow-up. Not a Phase 2 blocker (notifications are written; admin queue link is in the header for admins; consultants will see new leads in the queue).
3. **Phase 3 (Tech Debt service) ready to start.** Capability list ingest (Excel + AI extraction with redaction — first real LLM call), overlap analysis dashboard, consolidation plan workflow, PDF + XLSX exporters.

## Significant decisions

See [`DECISIONS.md`](DECISIONS.md) for the full log. Highlights:

- **D-007 (FLIPPED):** ATT&CK uses full Enterprise matrix (~600 techniques), not curated subset.
- **D-011:** Working directory is `/workspaces/repos/SHIELD062626`, not spec-mandated `/workspaces/SHIELD062626`.

## How to resume an interrupted build

If you stop me mid-stream, here is exactly how to restart:

> **"Resume the SHIELD v2 build at `/workspaces/repos/SHIELD062626`. Read `BUILD_REPORT.md`, `DECISIONS.md`, the last `git log --oneline -15`, and `CHANGELOG.md` to find where we left off. Continue with the next stage."**

Paste that into a new Claude Code session and I'll figure out the state from the repo.

### What I look at

1. `git describe --tags --abbrev=0` → last tag.
2. `git log --oneline -15` → recent commits.
3. This file → phase/stage table.
4. `CHANGELOG.md` → narrative.
5. `DECISIONS.md` → non-spec choices.

### What you don't need to re-provide

- Anthropic API key (in `.env`, gitignored)
- The plan, spec answers, Q1–Q7 from session memory

### What you DO need to provide

- **Docker** if you want me to integration-test the full 8-service stack.

## Recommended next steps for Eugene

1. **Play with the live demo at http://localhost:3000** (running now under SQLite). Walk the intake; verify the admin queue reflects what you entered.
2. **Tell me to start Phase 3** when ready. Phase 3 is the first phase that involves real LLM calls (capability extraction); the PII redactor module ships there.
3. **Optional:** rebuild the devcontainer with Docker-in-Docker so the full stack can integration-smoke in CI.

## Estimated effort for deferred items

| Item                                                | Estimate                    |
| --------------------------------------------------- | --------------------------- |
| MFA enrollment + verification                       | 1.5 weeks (TOTP + WebAuthn) |
| Email verification flow                             | 0.5 weeks                   |
| FedRAMP-authorized LLM connector (Azure OpenAI Gov) | 0.5 weeks                   |
| Notification bell UI in the header                  | 0.2 weeks                   |
| Postgres audit-trigger integration smoke            | 0.1 weeks (waits on Docker) |
| axe-core / Playwright a11y CI job                   | 0.3 weeks                   |
