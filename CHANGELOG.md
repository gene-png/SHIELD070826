# Changelog

All notable changes to SHIELD by Kentro v2.0. Format roughly follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the phase template in AI Prompt §9.

## [Unreleased]

### Documentation, backup/restore, and seed-loader remediation — 2026-07-09

- **H-1 (auth truth):** Retracted the fictional "compensating controls." Idle
  timeout, forced re-auth, and refresh-token rotation are now documented as
  PLANNED, NOT PRESENT across `README.md`, `BUILD_REPORT.md` (OWASP A07),
  `docs/architecture.md`, `infra/keycloak/README.md`; dead env-var names removed
  from `.env.example` and `docker-compose.yml`. New decision `D-017` defers
  enforcement to the MFA work package (refresh-token rotation/revocation first).
- **H-4 (doc truth pass):** Corrected `docs/architecture.md` (multi-tenant, no
  Celery worker, `audit_entries` table, no `unredact`) and `docs/operations.md`
  (no worker); renumbered the duplicate `D-015` to `D-016`; amended `D-009` to
  rescind i18n/`next-intl` for v1; collapsed the three `[Unreleased]` changelog
  headings to one (this section) plus dated phase headings.
- **H-3 (backup/restore):** Added `infra/backup/{backup,restore,restore-drill}.sh`,
  `docs/runbooks/backup-restore.md`, and a non-blocking CI restore-drill job.
- **E-6 (seed loaders):** Seed loaders now run inside the api container as
  documented — `packages/` is mounted read-only, data resolves via
  `SHIELD_SEED_DATA_DIR`, and `_common.py` no longer crashes on `parents[3]`.
  Added `scripts/seed.sh`.

### Multi-tenant: allow many clients per deployment — 2026-05-21

- Added `client_id` to `services`, `service_requests`, `artifacts` (Alembic 0013); made `client_id` `NOT NULL` on `csf_assessments`, `csf_answers`, `zt_assessments`, `zt_answers`, `attack_assessments`, `attack_coverage` after backfill from the deployment's existing singleton client (or a placeholder `(legacy backfill)` client when business data exists but no `client` row does).
- `User.client_id` stays nullable (platform admin/reviewer = `NULL`; client-role users get a fresh client created and bound at registration). Indexed for filtering speed.
- New FastAPI dependency `current_client` resolves the active tenant per request: client-role users are pinned to `user.client_id`; admin/reviewer users pick a tenant via the `X-Client-Id` header.
- `app/tenant.py` introduces `require_*_in_tenant` helpers used by every data route (CSF, ZT, ATT&CK, tech-debt, artifacts, deliverables); cross-tenant id-based access returns 404 with no existence oracle.
- New admin endpoints: `GET/POST /admin/clients`, `GET /admin/clients/{id}`. `GET /admin/intake-queue` now optional-filters by `client_id` and shows cross-tenant rows by default.
- Frontend: added `ClientSwitcher` to the top nav for admin/reviewer roles; the selection is persisted in a `shield_active_client_id` cookie (`httpOnly`, `SameSite=Lax`) and `lib/api.ts` forwards it as `X-Client-Id` to the FastAPI backend on every proxied call. New route handler `POST /api/active-client` sets the cookie.
- See DECISIONS.md D-015 for the architectural rationale.

### Opening commit — 2026-05-19

- Repo scaffolded per Master Spec §16 and AI Prompt §8.
- Reference documents relocated to `reference-docs/` with normalized filenames (see DECISIONS.md D-013).
- Dev container configured with `appuser` + passwordless sudo per AI Prompt §3.10–§3.11.
- Docker Compose stack defined for 8 services (db, redis, minio, keycloak, mailhog, api, worker, web).
- Pre-commit hooks and CI workflow seeded per AI Prompt §5 / §8.6.
- Documentation skeleton seeded under `docs/`.
- Seven spec §17 open questions answered in DECISIONS.md (D-003 through D-009); Q5 flipped to full ATT&CK matrix per Eugene's direction.

### Phase 1 stage 1 — API skeleton (`v0.1.1`) — 2026-05-19

- FastAPI app factory with lifespan (`apps/api/app/main.py`).
- Structured JSON logging via `structlog` with merged correlation-IDs (`apps/api/app/logging.py`).
- `CorrelationIdMiddleware` reads/echoes `X-Request-ID` (validated; 1–128 chars, alnum + `-_`).
- Global exception handler returns correlation-ID-only 500 responses; stack traces never leak (Master Spec §6.3).
- `app.config.Settings` (pydantic-settings) loads every env var, refuses production with `SHIELD_REDACTION_MODE=off` or placeholder `JWT_SIGNING_SECRET`.
- SQLAlchemy 2 + Alembic wiring (`alembic.ini`, `alembic/env.py`, `script.py.mako`), shared metadata naming convention.
- `/health` liveness endpoint.
- Runtime Dockerfile under `apps/api/Dockerfile` with least-privilege `shield` user (uid 10001), no shell, no sudo (production posture per AI Prompt §3.10 note).
- Unit tests (9 passing): health, correlation-ID middleware, exception handler, config safety asserts.

### Phase 1 stage 2 — Data model + audit log (`v0.1.2`) — 2026-05-19

- ORM models for the three Phase 1 tables: `client` (singleton org), `users` (with `UserRole` enum: admin/reviewer/client), `audit_entries` (append-only) — `apps/api/app/models/`.
- Cross-dialect first Alembic migration (`alembic/versions/0001_initial_schema.py`): creates tables on both Postgres and SQLite; installs Postgres-only `audit_entries_block_mutation()` trigger function + `BEFORE UPDATE`/`BEFORE DELETE` triggers.
- Application-layer append-only guard: `SQLAlchemy` `before_flush` event listener raises `AuditEntryImmutableError` on any update or delete of an `AuditEntry`. Catches logic bugs even when running against SQLite or if the prod trigger is somehow missing.
- `app.audit.spine.audit()` is the only blessed write surface for audit rows; automatically merges the current correlation ID from the request context.
- `/ready` readiness probe that touches the DB (`SELECT 1`) and reports per-dependency status (returns 200 with `status=degraded` rather than 5xx, so load balancers get a clean signal but readiness sweeps stay green).
- Alembic env honors any `sqlalchemy.url` already set in the config (tests override it for SQLite).
- 16 unit tests passing: migration applies cleanly on SQLite; ORM round-trips a User + audit row; audit immutability fires on UPDATE and DELETE; client singleton inserts; `audit()` row carries correlation_id; everything from stage 1 still green.

### Phase 1 stage 3 — Auth backbone (`v0.1.3`) — 2026-05-19

- Argon2id password hashing tuned per OWASP Password Storage Cheat Sheet (`apps/api/app/security/password.py`).
- HS256 JWT issue + verify with typed claims (`apps/api/app/security/jwt.py`); separate access / refresh `typ` claim; `verify_token(expected_type=...)` prevents token-confusion attacks.
- Lockout bookkeeping columns added to `users` via migration `0002_user_lockout_columns.py`: `failed_login_count`, `last_failed_login_at`, `locked_until_at`. 10 failed attempts in 15 minutes locks the account (Master Spec §4.5).
- Auth routes (`apps/api/app/routes/auth.py`):
  - `POST /auth/register` — self-registration per D-004. First registrant becomes Primary POC with `admin` role; subsequent registrants are `client`.
  - `POST /auth/login` — email + password. Account-existence oracle defended (wrong-email runs a dummy Argon2 verify so timing matches wrong-password).
  - `POST /auth/refresh` — refresh token → new access + refresh pair. Refuses access tokens.
  - `POST /auth/logout` — audited.
  - `GET /auth/me` — current user.
- `current_user` FastAPI dependency: validates `Authorization: Bearer <access>` and loads the user (`apps/api/app/dependencies.py`).
- 14 new auth route tests + 13 primitive tests = 43 unit tests all passing.

### Phase 1 stage 4 — Keycloak realm (`v0.1.4`) — 2026-05-19

- `infra/keycloak/shield-realm.json` imported on `keycloak` service start (compose mounts the dir at `/opt/keycloak/data/import` and starts with `--import-realm`).
- Realm + 3 realm roles (admin / reviewer / client) + 2 clients (`shield-web` public OIDC w/ PKCE S256, `shield-api` bearer-only).
- Brute-force protection mirrors API lockout counters (10 failures, 60s/900s waits).
- SSO session idle 1800s, max 86400s — matches Master Spec §4.5.
- Bootstrap dev-admin user with temporary password (dev only).

### Phase 1 stage 5 — Next.js skeleton (`v0.1.5`) — 2026-05-19

- `apps/web` baseline: Next.js 14.2 App Router + React 18 + TS strict + Tailwind 3.4 + NextAuth 4.24.
- `next.config.mjs` ships `output: "standalone"` for slim prod image, security headers (`X-Frame-Options: DENY`, HSTS, Permissions-Policy, no `X-Powered-By`).
- NextAuth Credentials provider (`src/lib/auth/options.ts`) posts to `/auth/login` on the API and stores access + refresh tokens in the encrypted JWT session. 401/423 from the API map to `null` (sign-in failure); other errors propagate.
- Server-side `apiFetch<T>()` helper (`src/lib/api.ts`) attaches Bearer tokens, surfaces correlation IDs from `X-Request-ID`, raises `ApiError` with status + payload on non-2xx.
- Typed session augmentation in `src/types/next-auth.d.ts` exposes `session.role` and `session.accessToken`.
- Placeholder landing at `/` (real Round-6 landing arrives in stage 7).
- Smoke: `pnpm typecheck` clean; `pnpm build` succeeded — 4 routes built (`/`, `/_not-found`, `/api/auth/[...nextauth]`), 87.2 kB First Load JS shared.

### Phase 1 stage 6 — Design-system primitives (`v0.1.6`) — 2026-05-19

- New workspace package `@shield/design-system` (`packages/design-system/`).
- Round-6 tokens in `src/tokens.css` as CSS custom properties: surface, ink, border, brand navy, status palette (saturated colors reserved for status per Round-6), type scale, 4-px spacing scale, radii, soft shadows, motion tokens that collapse under `prefers-reduced-motion`.
- Tailwind preset (`src/tailwind-preset.ts`) wires the tokens to classnames.
- 8 primitives, all keyboard-accessible and WCAG-2.1-AA-targeted:
  - `Card` + sub-parts — modular, soft shadow.
  - `StatusPill` — saturated colors only here per Round-6.
  - `NumberCard` — KPI card for executive surfaces.
  - `DataTable` — sticky header, sortable columns with `aria-sort`, row click, empty-state slot.
  - `Toast` + `ToastProvider` + `useToast()` — `aria-live=polite` region, auto-dismiss.
  - `Modal` + `SlideOver` — native `<dialog>` (browser focus trap + ESC), backdrop click closes.
  - `EmptyState` — icon + title + description + action slot.
- Wired into `apps/web`: package dep, Tailwind preset, token CSS import, placeholder `/` now uses `Card` + `StatusPill`.
- Smoke: `pnpm typecheck` clean across workspace; `pnpm build` succeeded — `/` route now 8.57 kB First Load JS (up from 138 B placeholder); 4 routes, 87.1 kB shared.

### Phase 1 stage 7 — Landing + auth screens (`v0.1.7`) — 2026-05-19

- Marketing landing (`/`): `PublicHeader` + `Hero` + `ServiceGrid` (4 service cards using `Card` from `@shield/design-system`) + trust strip with `StatusPill`s + `PublicFooter`. Round-6 PUBLIC EXPERIENCE tier.
- `/sign-in`: NextAuth Credentials-backed form (`SignInForm`) wrapped in `<Suspense>` (uses `useSearchParams` for `callbackUrl`). Errors render inline; 401/423 from the API surface as "Invalid email or password" to avoid an account-existence oracle.
- `/sign-up`: form (`SignUpForm`) posting to `/api/proxy/auth/register`, which proxies to the FastAPI `/auth/register` via the server-side `apiFetch` helper. On success, immediately calls `signIn("credentials")` so the user lands in an authenticated session.
- `/api/proxy/auth/register`: thin server route that keeps API host names off the wire to the browser and maps `ApiError` → `NextResponse` with the upstream status preserved.
- Footer pages stubbed at `/accessibility`, `/privacy`, `/security` so the footer nav doesn't 404; each carries a real mailto contact for the relevant team.
- `AuthSessionProvider` (NextAuth `SessionProvider`) and `ToastProvider` wired into the root layout.
- `next.config.mjs` `typedRoutes` left OFF intentionally (requires `next build` to populate the route manifest before `tsc --noEmit`, which we run as a pre-build smoke).
- Smoke: `pnpm typecheck` clean across workspace; `pnpm build` succeeded — 9 routes total (`/`, `/_not-found`, `/sign-in`, `/sign-up`, `/accessibility`, `/privacy`, `/security`, `/api/auth/[...nextauth]`, `/api/proxy/auth/register`). First Load JS shared 87.2 kB; biggest route (`/sign-up`) at 105 kB.

### Phase 1 stage 8 — CI green (`v0.1.8`) — 2026-05-19

- All linters and formatters configured and clean across the whole tree:
  - **Python:** `ruff` (curated rule set, with TCH dropped because SQLAlchemy 2's `Mapped[uuid.UUID]` etc. need their referent types resolvable at runtime; per-file ignores for test fixtures and Alembic env), `black`, `bandit` (0 issues), `pytest -m unit` (43 passing). Targeted `# noqa` for FastAPI's `Depends(...)` default and the OAuth `token_type="bearer"` field (false positives, not credentials).
  - **Web:** `prettier --check`, `eslint`, `tsc --noEmit`, `next build` (9 routes). Added `.prettierignore` so the lockfile and `reference-docs/` are not reformatted.
- CI workflow rewritten (`.github/workflows/ci.yml`) to actually run those checks against the codebase. Three jobs: `python` (ruff, black, pytest, bandit), `web` (prettier, eslint, typecheck, build), `secret-scan` (gitleaks).
- `apps/api/app/models/user.py`: `UserRole(str, Enum)` → `UserRole(StrEnum)` (Python 3.11+ idiom; what ruff UP042 wanted).

## Phase 1 — Foundation — Complete (`v0.1.0`) — 2026-05-19

### Acceptance criteria

- [x] User can self-register, sign in. (MFA + email verification deferred per Master Spec §2 risk acceptance; columns + feature flags in place to enable in v1.x.)
- [x] Three roles distinguishable (admin / reviewer / client).
- [x] Audit log records every login (and registration, lockout, logout).
- [x] No stack trace surfaces to user under any forced error.

### Notable features shipped

- API skeleton with structured JSON logs and correlation IDs end-to-end.
- Data model for `client` (singleton), `users` (with role enum + lockout bookkeeping), `audit_entries` (append-only at two layers).
- Auth backbone: Argon2id hashing, JWT issue/verify, register/login/refresh/logout/me routes, account lockout, account-existence oracle defense.
- Keycloak realm exported and ready for v1.x OIDC federation with the same audience claim.
- Next.js 14 web app: marketing landing (Round-6 PUBLIC EXPERIENCE), sign-in + sign-up, NextAuth Credentials provider, security headers, footer stub pages.
- `@shield/design-system` package: Round-6 tokens, Tailwind preset, 8 keyboard-accessible primitives (Card, StatusPill, NumberCard, DataTable, Toast, Modal, SlideOver, EmptyState).
- CI green across the whole tree: ruff, black, bandit, pytest, prettier, eslint, tsc, next build.

### Security review (OWASP Top 10) — see BUILD_REPORT.md for full matrix

- A01 Access Control: PARTIAL (authn done; role-based route guards in Phase 2)
- A02 Cryptographic Failures: PASS
- A03 Injection: PASS
- A04 Insecure Design: PASS
- A05 Misconfiguration: PASS
- A06 Vulnerable Components: PARTIAL (pinned versions; audit hooks in Phase 6)
- A07 Auth Failures: PASS WITH NOTES (MFA deferred per spec)
- A08 Software Integrity: PASS
- A09 Logging and Monitoring: PASS
- A10 SSRF: PASS

### What's stubbed or deferred

- MFA enrollment + email verification — feature-flagged off; columns and feature flags ready.
- Postgres audit-trigger integration smoke — waits on Docker availability in the dev container.
- axe-core / Playwright accessibility CI job — deferred to Phase 6 hardening; WCAG 2.1 AA is implemented at the component layer.
- Redactor module (`apps/api/app/ai/redact.py`) — lands in Phase 3 with the first AI-extraction use case (Tech Debt capability list).

### Known issues

- None blocking Phase 2.

### How to try it

1. `cp .env.example .env`; paste `ANTHROPIC_API_KEY` (only needed when `SHIELD_LLM_MODE=live`); generate `NEXTAUTH_SECRET` via `openssl rand -hex 32`.
2. `docker compose up -d db redis minio keycloak mailhog && docker compose up -d --build api worker`.
3. `docker compose run --service-ports --rm web bash scripts/dev-web.sh`.
4. Open http://localhost:3000.

### Decisions logged this phase

- D-001 through D-014 (opening commit). No new decisions added during stages 1–9 beyond DECISIONS.md entries already on `main`.

## [Phase 2 — Intake]

### Phase 2 stage 1 — Intake data model (`v0.2.1`) — 2026-05-19

- New `ServiceRequest` ORM model (`apps/api/app/models/service_request.py`) matching Master Spec §11: `service_type`, `requested_by`, `requested_at`, `notes`, `deadline`, `fulfilled_service_id`, `declined_at`, `declined_reason`.
- New `ServiceType(StrEnum)`: `tech_debt`, `zero_trust_cisa`, `zero_trust_dod`, `nist_csf`, `attack_coverage`, `consultation`. The fifth-option "I'm not sure" intake path maps to `CONSULTATION`.
- `Client.intake_completed_at` column added so the admin queue can surface new leads with a real timestamp (Phase 2 acceptance).
- `Client.service_interests` switched to `ARRAY(String(32)).with_variant(JSONB, "sqlite")` for SQLite test compatibility.
- Migration `0003_intake.py`: adds the column, creates `service_requests` + indexes on `(requested_at)` and `(service_type)`.
- 5 new unit tests (48 total).

### Phase 2 stage 2 — Intake API routes (`v0.2.2`) — 2026-05-19

- Three routes on the FastAPI side back the wizard (`apps/api/app/routes/intake.py`):
  - `GET /intake` — current state; lazily creates the singleton client placeholder so the wizard always has a target.
  - `PATCH /intake` — auto-save target on every blur. Accepts a sparse body; only set-and-non-None fields are applied (avoids overwriting NOT-NULL columns like `users.timezone` with None).
  - `POST /intake/submit` — finalizes submission: validates a real legal name, writes `ServiceRequest` rows (dedupes by service_type), stamps `client.intake_completed_at = utcnow()`, and writes a `client.intake_submitted` audit row whose `details.services` are sorted for stable diffing.
- `IntakePatchRequest` / `IntakeSubmitRequest` / `IntakeStateResponse` Pydantic schemas (`apps/api/app/schemas/intake.py`). `IntakeSubmitRequest.service_requests` enforces `min_length=1`; `consultation` is a valid first pick so the "I'm not sure" path doesn't get blocked.
- 7 new route tests (55 total): GET creates the singleton, PATCH writes partial updates, submit writes service_requests + audit row, submit rejects empty service list, submit rejects the pending placeholder legal name, submit dedupes duplicates, all three routes return 401 when unauthenticated.

### Phase 2 stage 3 — Web intake wizard skeleton (`v0.2.3`) — 2026-05-19

- `/intake` route gated by `getServerSession()` in `app/intake/layout.tsx`; unauthenticated users are redirected to `/sign-in?callbackUrl=/intake`.
- `IntakeWizard` client component manages step state (`services` → `organization` → `contact` → `systems` → `notes` → `review`) and pulls the current intake from `/api/proxy/intake` on mount. If the intake is already submitted, jumps to `review` so the user can verify what's on file instead of re-starting.
- `IntakeProgress` renders a 6-step indicator with `aria-current="step"`, success-tone tick marks for completed steps, focus-tone for the current step, and ink-tertiary for upcoming steps.
- `SaveStatus` reads a discriminated `SaveState` (`idle | saving | saved | error`) and renders an `aria-live=polite` indicator with "Saved X seconds/minutes ago" that updates once a second.
- Server-side proxy routes (`/api/proxy/intake`, `/api/proxy/intake/submit`) attach the session's access token as a Bearer header and forward to FastAPI; ApiError shapes pass through with the upstream status preserved.
- Client-side wrappers (`lib/intake/client.ts`) cover `fetchIntake`, `patchIntake`, `submitIntake` with typed return values; per-step forms in stage 4 call these.
- TS types in `lib/intake/types.ts` mirror `apps/api/app/schemas/intake.py` 1:1; `SERVICE_LABELS` gives the plain-English copy the wizard renders (Master Spec §15 Phase 2: "All copy in plain English").
- Smoke: typecheck clean, eslint 0 warnings, prettier clean, `next build` clean — now 12 routes total (3 new: `/intake`, `/api/proxy/intake`, `/api/proxy/intake/submit`). `/intake` is server-rendered on demand (dynamic) because it reads the session at request time.

### Phase 2 stage 4 — Per-step form fields + real auto-save (`v0.2.4`) — 2026-05-19

- `useIntakeAutoSave` hook wraps `patchIntake` with a discriminated `SaveState` and surfaces the updated intake state back to the wizard via an `onUpdate` callback.
- Six step components (`apps/web/src/components/intake/steps/Step*.tsx`):
  - **Step 1 — Services:** card-grid of the 6 service types with USWDS-style checkboxes. Picking "I'm not sure" is exclusive (clears the four real services); picking a real service clears "I'm not sure". Each card is keyboard-focusable; descriptions are wired via `aria-describedby`.
  - **Step 2 — Organization:** legal_name (required) + dba_name + website + size_band (`<select>`) + industry, plus 6-field address block. Every input is wired to PATCH on blur.
  - **Step 3 — Contact:** display_name + title + phone + timezone for the user. Email shown read-only with hint copy (locked to the signed-in account).
  - **Step 4 — Systems:** single textarea writing to `client.prompting_context`. Real systems table comes in Phase 4 with the CSF assessment.
  - **Step 5 — Notes:** per-picked-service notes + optional target deadline. Lives in wizard local state until submit (the API only writes `service_requests` at POST `/intake/submit`).
  - **Step 6 — Review:** read-only summary (organization / services / context). Submit button disabled unless legal_name is real and at least one service is picked. Pre-existing `intake_completed_at` surfaces as "submitted on …; you can re-submit" copy.
- New `Field` component (`apps/web/src/components/intake/Field.tsx`) wires label / hint / error with `aria-describedby` and `aria-invalid` per USWDS accessibility patterns. Exports shared Tailwind class strings (`inputClasses`, `textareaClasses`, `selectClasses`) so every input renders identically.
- `IntakeWizard` rewires the placeholder step renderer to dispatch to the real step components; submit handler bundles client state + service_inputs into `POST /intake/submit` and reflects the response.
- Smoke: typecheck clean, ESLint 0 warnings, prettier clean, `next build` clean — `/intake` route now 8.0 kB (up from 3.56 kB), 112 kB First Load JS. 12 routes total; no schema changes so the 55 API unit tests still pass.

### Phase 2 stage 5 — Section-tabbed questionnaire renderer (`v0.2.5`) — 2026-05-19

- New `@/components/questionnaire` module — the shared rendering primitive that Phases 4 (CSF) and 5 (ATT&CK with the full ~600-technique matrix per D-007) will both consume. Master Spec §15 Phase 2: "Section-tabbed questionnaire renderer (shared component for CSF, ZT, future frameworks)."
- **`QuestionnaireDefinition` shape:** JSON-friendly (so it can ship as static assets in `packages/csf-data` / `packages/attack-data` / `packages/zt-data`). Sections contain questions; each question carries a stable id used as the key in a flat `Responses` map (matches the `questionnaire_responses` table shape from Master Spec §11).
- **Eight question primitives** cover the v1 surface: `short_text`, `long_text`, `number` (with optional `unit`), `score_0_2` (named-label radio group for CSF 5-dimension scoring + ATT&CK coverage), `choice` (single-select), `multi` (multi-checkbox), `yes_no`, `tristate` (yes/no/n-a). Specialized CSF grid composes these in Phase 4.
- **`SectionTabs`** has full WAI-ARIA APG tab semantics: `role="tablist"`, `role="tab"`, `aria-selected`, `aria-controls`, roving `tabIndex`, plus full keyboard nav (ArrowLeft / ArrowRight / Home / End) with manual activation. Per-section completion chips drive a small progress percentage.
- **`QuestionnaireRenderer`** renders the active section as a `role="tabpanel"` with `aria-labelledby` wired to the active tab. Computes `sectionProgress` via `useMemo` from the responses map — Phase 4 reuses this for the "needs answers" badge on the admin queue.
- **Dev preview** at `/dev/questionnaire-preview` exercises every question type end-to-end with a hand-rolled definition (3 sections, 10 questions). Unlisted route; the page header makes the dev-only nature obvious.
- Smoke: typecheck clean, ESLint 0 warnings, prettier clean, `next build` clean. 13 routes total (1 new: `/dev/questionnaire-preview`).

### Phase 2 stage 6 — Document upload + redaction disclosure (`v0.2.6`) — 2026-05-19

- Storage abstraction (`apps/api/app/storage/`): `StorageBackend` Protocol with `LocalFilesystemStorage` for tests/dev and `S3Storage` for production (KMS-encrypted, boto3 imported lazily).
- `Artifact` model + migration `0004_artifacts.py` matching Master Spec §11 (origin enum, indexes on uploaded_at / uploaded_by / sha256).
- Three API routes: `POST /artifacts` (multipart, MIME allowlist, 50 MB cap, filename sanitization, sha256 + audit row), `GET /artifacts` (current user's uploads), `GET /artifacts/{id}` (404 for unknown id or wrong owner).
- Server-side multipart proxy `/api/proxy/artifacts` forwards FormData with the session bearer; browser never sees the API host name.
- Web components: `Dropzone` (drag/drop + click + keyboard, multi-file, per-file status with `aria-label`), `RedactionDisclosure` (plain-English copy of §12 policy), `EmptyArtifactsHint`.
- Wired into intake **Step 5** above the per-service notes: redaction disclosure → dropzone → live upload list (refreshes on mount via `GET /artifacts`).
- 7 new pytest tests (62 total): upload writes row + storage object + audit; rejects unknown MIME (415); rejects empty (422); sanitizes path-traversal filenames; list returns own only; GET unknown id 404; routes 401 without auth.
- Smoke: pytest 62/62 green, ruff + black + bandit clean, prettier + ESLint + tsc clean, `next build` clean. 14 routes total.

### Phase 2 stage 7 — Admin queue (`v0.2.7`) — 2026-05-19

- `require_role(*allowed)` FastAPI dependency factory (`apps/api/app/dependencies.py`) returns 403 (not 401) when authenticated callers lack the required role — matches RFC 7231 and lets clients distinguish "sign in" from "you're signed in but not allowed".
- `GET /admin/intake-queue` (`apps/api/app/routes/admin.py`) returns the singleton client (with `intake_completed_at`), all service requests (with requester user summary joined in), all artifacts, and the total user count. Per Master Spec §15 Phase 2 acceptance: the admin queue surfaces the new-lead timestamp and reflects exactly what the client entered.
- `AdminUserSummary` schema redacts password hash + lockout state but keeps the identity bits the consultant needs (email, display name, title, role, last_login_at).
- Server-side proxy `/api/proxy/admin/intake-queue` attaches the session bearer.
- `/admin/queue` page gated by `app/admin/layout.tsx`: redirects to `/sign-in?callbackUrl=/admin/queue` if unauthenticated; renders a "Not authorized" landing if signed in as a non-admin (session intact so navigation elsewhere works).
- `IntakeQueue` component (`apps/web/src/components/admin/IntakeQueue.tsx`) renders the organization panel, service requests list (with `Open`/`Fulfilled`/`Declined` `StatusPill` per row + the requester's name/email/title), and uploaded documents. Empty states for no-intake-yet and no-service-requests.
- `PublicHeader` is now an async Server Component: shows "Intake" + "Admin queue" (admin-only) when signed in, sign-in/get-started CTAs when not. Surfaces the signed-in user's email.
- 4 new pytest tests (66 total): empty queue, reflects submitted intake with requester summary, client role gets 403, unauthenticated gets 401.
- Smoke: pytest 66/66 green, ruff + black + bandit clean, prettier + ESLint + tsc clean, `next build` clean. 16 routes total (2 new: `/admin/queue`, `/api/proxy/admin/intake-queue`).

### Phase 2 stage 8 — Notifications + Phase 2 acceptance gate (`v0.2.8` / `v0.2.0`) — 2026-05-19

- `Notification` model + migration `0005_notifications.py` matching Master Spec §11: user_id, event_type, title, body, link, created_at, read_at. Indexes on `(user_id, created_at)` and `(user_id, read_at)` so per-user list + unread count both stay index-backed.
- `notify(...)` and `notify_role(role, ...)` helpers (`apps/api/app/notifications/spine.py`) — blessed write surface, mirrors the audit-spine pattern. `notify_role` fans out one row per user with the given role.
- Three notification routes: `GET /notifications` (newest first, capped at 50, returns `unread_count`); `POST /notifications/{id}/read` (404 for unknown id or wrong owner); `POST /notifications/read-all`.
- **Intake submit now fans out a `intake.submitted` admin notification** with `link=/admin/queue` (AI Prompt §6.12: bell links must resolve to a working page). Body includes the client legal name + sorted services list.
- 6 new pytest tests (72 total): intake submit writes admin notification; submitter (client role) does NOT get a copy; `GET /notifications` reflects unread count; mark-read updates `read_at` and clears unread count; cross-user mark-read returns 404; all routes 401 without auth.

## Phase 2 — Intake — Complete (`v0.2.0`) — 2026-05-19

### Acceptance criteria

- [x] A new client can complete intake end-to-end without internal vocabulary, raw JSON, or stack traces.
- [x] Submitting intake reflects correctly in the admin queue with the new-lead timestamp.
- [x] All intake data round-trips correctly: client enters X, admin reads X.

### Notable features shipped

- Self-service 6-step intake wizard with auto-save on every blur and a live "Saved Xs ago" indicator.
- Drag-and-drop document upload with up-front redaction disclosure (the user-facing copy of the Master Spec §12 policy).
- Generic section-tabbed questionnaire renderer with full WAI-ARIA tab semantics — load-bearing for Phases 4 and 5.
- Admin queue at `/admin/queue` with role-based authz; reflects the singleton client + every service request (with requester user joined in) + every uploaded document.
- Admin notification fan-out on intake submit, with link pointing at `/admin/queue`.
- 72 unit tests across the API; web typecheck + lint + prettier + next build all green.

### Security review (OWASP Top 10) — full matrix in BUILD_REPORT.md

- A01 Access Control: PASS (role-based guards at route + layout layer)
- A02 Cryptographic Failures: PASS
- A03 Injection: PASS
- A04 Insecure Design: PASS (audit immutability, MIME allowlist, redaction disclosure)
- A05 Misconfiguration: PASS
- A06 Vulnerable Components: PARTIAL (Dependabot in Phase 6)
- A07 Auth Failures: PASS WITH NOTES (MFA still deferred per spec)
- A08 Software & Data Integrity: PASS (sha256 captured + audited on upload)
- A09 Logging & Monitoring: PASS (audit + notification fan-out)
- A10 SSRF: PASS

### What's stubbed or deferred

- Notification bell UI in the header — API + data layer shipped; visual surfacing is a small follow-up.
- Postgres audit-trigger integration smoke — waits on Docker availability.
- The redactor module — lands in Phase 3 with the first AI extraction (Tech Debt capability list).

### Known issues

- None blocking Phase 3.

### How to try it

A SQLite-only dev demo is documented in BUILD_REPORT.md ("Recommended next steps"). For the full stack: `cp .env.example .env`, paste `ANTHROPIC_API_KEY`, generate `NEXTAUTH_SECRET`, then `docker compose up`.

### Decisions logged this phase

- No new DECISIONS.md entries beyond stages tracked in this CHANGELOG; the seven §17 open questions were already settled in Phase 1's D-003 through D-009.

## [Phase 3 — Tech Debt]

### Phase 3 stage 1 — Tech Debt data model (`v0.3.1`) — 2026-05-19

- New ORM models matching Master Spec §11 verbatim:
  - **`Service`** — the workspace that opens when an admin promotes a `ServiceRequest` to live work. Carries `kind` (StrEnum: `tech_debt` / `zero_trust_cisa` / `zero_trust_dod` / `nist_csf` / `attack_coverage`), `status` (`draft`/`in_progress`/`review`/`released`/`archived`), `title`, `source_request_id` (FK back to the originating request), `opened_by`, `released_at`. Other service kinds are listed in the enum so Phase 4 + 5 don't need a schema change.
  - **`CapabilityList`** — versioned per service (unique constraint on `(service_id, version)`); `draft` / `approved` / `released` status.
  - **`CapabilityItem`** — `name` / `vendor` / `category` / `function` / `annual_cost_usd` (Numeric(14,2)) / `license_count` / `notes` / `confidence_pct` (0-100, AI-set; cleared on human edit) / `source_artifact_id` (FK to the uploaded artifact the item was extracted from).
  - **`Deliverable`** — `service_id`, `title`, `summary`, `version`, `pdf_artifact_id`, `xlsx_artifact_id`, `finalized_at`/`finalized_by`, `released_to_client_at`, `superseded_by` (self-FK for re-releases).
- Migration `0006_tech_debt.py`: creates all four tables + indexes (`services.kind`, `services.status`, `capability_items.capability_list_id`, `deliverables.service_id`, `deliverables.released_to_client_at`).
- 3 new pytest tests (75 total): migration creates Phase 3 tables; full round-trip through Service → CapabilityList → CapabilityItem → Deliverable with realistic financial data; unique-constraint on `(service_id, version)` enforced.

### Phase 3 stage 2 — PII redactor (`v0.3.2`) — 2026-05-19

- **`app.ai.redact`** module — the §12 security boundary in front of every LLM call. Intentionally pure (no I/O, no DB, no clock) so it can be reviewed line-by-line in an OWASP audit.
- Two public functions: `redact_for_ai(text, *, mode, client_org_name, name_hints)` for strings and `redact_payload(obj, ...)` that walks dicts/lists/tuples recursively. Both return `(cleaned, removed_counts)` — the counts dict (e.g. `{"email": 3, "phone": 1}`) is what `artifact_redactions.removed_items` (Master Spec §11) and the `llm_calls` audit row both record. **Counts only, never payload content.**
- Eight categories redacted in `strict` mode: emails, phones (US + international, 10–20 char digit-run-with-separators), SSNs, EINs, CAGE codes (introducer-keyword form only), govcon contract numbers (e.g. `W91QUZ-23-C-0001`), street addresses + Suite/Apt/PO Box, signature blocks (everything from `Sincerely,` / `Regards,` / `V/R` etc. onwards), name hints supplied by the caller, and the client's org name.
- `standard` mode keeps addresses + org name (when the prompt explicitly needs the org context).
- `off` mode is pass-through. The runtime config refuses it outside development via `Settings.assert_safe_for_runtime()` (Phase 1); tests use it to compare raw-vs-redacted paths.
- Order of operations matters: signature block → email → SSN → EIN → contract → phone → CAGE → names → addresses → org. SSN runs before phone so `123-45-6789` is replaced with `[SSN]` before the phone pass sees it.
- 23 new pytest tests (98 total): every PII pattern + every mode + nested-payload walk + non-string-scalar preservation.

### Phase 3 stage 3 — AI client + `llm_calls` audit (`v0.3.3`) — 2026-05-19

- `app.ai.llm` module — the only path that calls an external AI provider. `LLMClient.invoke(...)` redacts the payload, opens an `llm_calls` row with `status=running` before the provider call, calls the provider, then finalizes with `status=completed | failed` + token counts + duration + `redacted_counts`.
- Two provider implementations: `FixtureProvider` (canned responses keyed by `purpose`; raises on unregistered purpose), `AnthropicProvider` (lazy SDK import; raises if `ANTHROPIC_API_KEY` is empty).
- `LLMCall` model + migration `0007_llm_calls.py` matching Master Spec §11 verbatim. `redacted_counts` is JSONB on Postgres / JSON on SQLite — counts only, never payload content (§12.1). `correlation_id` auto-populates from the request-scoped contextvar.
- 5 new pytest tests (103 total): provider sees the **redacted** payload (raw email/SSN never reach it); completed row carries token counts; failed row carries `error_message` + `duration_ms`; dict keys (field names) preserved through redaction; unregistered fixture purpose raises a loud `KeyError`; correlation_id threaded from request context.

### Phase 3 stage 4 — Capability list ingest (`v0.3.4`) — 2026-05-19

- **First real LLM extraction.** Admin uploads a CSV/XLSX inventory; the route runs it through `LLMClient.invoke(purpose="extract.capabilities")` with strict redaction; the response becomes a versioned `CapabilityList` + `CapabilityItem` rows.
- `app.tech_debt.parsers` parses CSV (stdlib) and XLSX (openpyxl, lazy import) into row-dicts. Header row becomes the keys; up to 500 rows ship; a sentinel `__truncated__` marker rides at the end if the input was longer.
- `app.tech_debt.extract` builds the prompt (versioned `PROMPT_VERSION="v1"` so future prompt-shape changes don't silently regress past extractions; the version is recorded on the `llm_calls` row), assembles `{rows, context}` payload, and parses the JSON response. Response parser handles the common "LLM wrapped JSON in prose" case by stripping to the outermost `{...}`.
- Name hints + client org name are pulled from the deployment (every user's display name + email-local-part as name hints; the singleton client's legal name as `[CLIENT]`) so the redactor uses real deployment data, not hardcoded fixtures.
- Three routes (`/tech-debt/services`, `/tech-debt/services/{id}/capability-lists/extract`, `/tech-debt/services/{id}/capability-lists/latest`) — all admin-only via `require_role(UserRole.ADMIN)`.
- Versioning: each extract creates `version = max + 1`; the unique constraint from stage 1's migration enforces it.
- Bad-JSON path: extractor raises `ValueError`, route maps to **502 Bad Gateway** (the `llm_calls` row is already written, so the operator can debug; client error code is wrong here because it's the upstream provider that misbehaved).
- 8 new pytest tests (111 total): admin can open service; client role gets 403; full extract flow with PII in CSV → redacted payload reaches the FixtureProvider (verified end-to-end); subsequent extracts version incrementally; unsupported artifact MIME (PDF) returns 415; non-existent service returns 404; bad JSON from the LLM returns 502; latest-list is admin-only until release.
- Demo DB migrated to head; `openpyxl` added to the API dev environment.

### Phase 3 stage 5 — Editable extraction table (`v0.3.5`) — 2026-05-19

- Two new API routes:
  - `PATCH /tech-debt/capability-items/{id}` — partial-update; clears `confidence_pct` on every human edit (the row is no longer an AI guess); rejects edits on items in a released list (409); audit row `capability_item.edited` records the list of fields touched.
  - `POST /tech-debt/capability-lists/{id}/approve` — flips `status: draft → approved`, stamps `approved_at` + `approved_by`, audits.
- New web workspace at `/admin/services/{id}/tech-debt` (admin-gated by the existing `/admin` layout):
  - Inventory upload via the existing `Dropzone` + `RedactionDisclosure` (Phase 2 stage 6) — drop a CSV/XLSX, the workspace auto-runs `POST /tech-debt/services/{id}/capability-lists/extract`.
  - `EditableCapabilityTable` renders the AI-extracted rows as a **real editable table** (AI Prompt §6.2: no raw JSON in user-facing UI). Each cell auto-saves on blur via `PATCH`; per-row status pill flips to "Saving…" → "Saved" or "Save failed". Low-confidence rows (`confidence_pct < 70`) get a warning row tint.
  - Confidence pill per row: `Human-curated` (success, after edit) | `AI 85%+` (info) | `AI 70–84%` (warning) | `AI <70%` (neutral with warning row tint).
  - Header strip shows total cost, low-confidence count, and the **Approve list** button (disabled if already approved/released).
  - Released lists render read-only.
- Six new server-side proxies (`/api/proxy/tech-debt/*`) consolidated through a shared `_proxy.ts` helper that handles bearer attachment + `ApiError` mapping. Adding a new tech-debt proxy is now a 4-line file.
- 6 new pytest tests (117 total): patch clears confidence + persists edits, patch rejects empty body 422, patch 404 for unknown item, patch rejects client role 403, approve writes status + actor, approve 404 for unknown list.
