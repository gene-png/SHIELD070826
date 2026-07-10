# SHIELD Remediation — Handoff

**Branch:** `remediation/fable-plan` (9 commits, **not pushed**)
**Base:** `main` @ `474729d`
**Date:** 2026-07-09
**Source document:** `SHIELD_Remediation_Plan_2.docx` (Revision 3) — 45 fixes across 8 workstreams
**Working plan + evidence:** `FABLE_REMEDIATION_PLAN.md` (in this repo, authoritative over the .docx)

---

## 1. Summary

All three sprints are complete. **44 of the document's 45 fixes are addressed and complete**; one (**B-6**) was already implemented before this engagement began and was deliberately skipped. H-6 was completed after the sprint close (see §8.2).

|                     | Before             | After                                      |
| ------------------- | ------------------ | ------------------------------------------ |
| API tests           | 480 passed         | **625 passed, 8 skipped, 0 failed**        |
| Web tests           | 0                  | e2e harness (Playwright, in Docker)        |
| `prettier --check`  | **17 files dirty** | clean repo-wide                            |
| `next lint`         | **crashed**        | `✔ No ESLint warnings or errors`           |
| `next dev` homepage | **HTTP 500**       | **HTTP 200**                               |
| `tsc --noEmit`      | clean              | clean                                      |
| `bandit` HIGH       | 0                  | 0                                          |
| Alembic head        | `0028`             | `0035` (7 additive, reversible migrations) |

**156 files changed, +12,444 / −1,289.** 26 new test files. Nothing pushed to a remote.

> **The most important number is not 619.** It is that **every new regression test was proven to fail against the un-fixed code.** A test that passes whether or not the bug is present is a false guarantee, and this repository already contained one: `test_llm_client.py` committed the transaction by hand to "prove" a durability property production did not have.

---

## 2. Sprints completed

| Sprint                                  | Goal                                                              | Commits                         | Result                                                                           |
| --------------------------------------- | ----------------------------------------------------------------- | ------------------------------- | -------------------------------------------------------------------------------- |
| **0 — Validation harness**              | Make it possible to _prove_ anything                              | `2d4a7d4`                       | Playwright bootstrapped in Docker; gated live-AI smoke test armed                |
| **1 — Trustworthy core**                | No fabricated data; no deliverable that contradicts its dashboard | `5e49c5c`, `cc36aeb`            | A-2, A-3, A-4, B-1, B-2, B-3, C-1, C-2, G-2 (+A-1)                               |
| **2 — Solid operations**                | Bounded, audited, guarded runtime                                 | `32d7a05`, `358c4ed`, `dbad444` | A-5, A-6, C-3–C-8, D-1–D-3, E-1–E-5, F-1, F-2, G-3, H-2, H-5, E-4, H-6 (partial) |
| **3 — Complete deliverables and truth** | Exports contain what the dashboard promises; docs stop lying      | `908621c`, `fb0b137`            | B-4, B-5, B-7, D-4, E-6, F-3, G-1, H-1, H-3, H-4, H-7, H-8                       |

---

## 3. Subagents used

All implementation ran on **Opus** with narrow scope, disjoint file ownership, explicit acceptance criteria, and a hard requirement to prove each test fails against un-fixed code. Planning, audit and validation used **Fable**.

| Sprint           | Subagents                                                                           |
| ---------------- | ----------------------------------------------------------------------------------- |
| Audit (pre-plan) | 5 × Fable, one per workstream group, read-only                                      |
| 1                | AI core · ZT · Risk · ATT&CK · CSF · Extraction/upload                              |
| 2                | AI runtime · Web · Security/config · Extraction/storage · Routes/concurrency        |
| 3                | Exporters · Docs/ops · Risk governance · CSF action plan · Nav/release/audit-viewer |

**Eight subagents stalled** on background waiters and were stopped by the lead after their work was complete; **two died** on transient API/SSL errors mid-run. In every case the lead verified the claims directly rather than resuming blindly. No subagent's report was accepted without independent checking.

---

## 4. Files changed (by area)

| Area                                           | What changed                                                                                                                                                                         |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `apps/api/app/ai/`                             | Boot-time SDK guard; per-job model + `max_tokens`; `schemas.py` (shared response shapes); autonomous `llm_calls` session; typed 504; `preview_job_payload()`                         |
| `apps/api/app/routes/`                         | All five run-ai call sites; chunking; export gates; target resolution; advisory locks; open-draft guards; evidence tenant checks; risk governance; `/admin/audit`; `/admin/ai-usage` |
| `apps/api/app/db/`                             | `locks.py` (NullPool advisory lock); autonomous session helper                                                                                                                       |
| `apps/api/app/models/`                         | `csf_dimension_scores.scored_at`; `llm_calls.client_id`; `risk_registers` unique constraint; `risk_entries.locked/deleted_at`; ZT/ATT&CK narrative columns; `CsfActionItem`          |
| `apps/api/app/{csf,zt,attack,risk,tech_debt}/` | Exporters (full gap lists, roadmap, 5×5 matrix, Action Plan); parsers (multi-sheet, tolerant numerics); storage timeouts                                                             |
| `apps/api/app/middleware/`                     | `ratelimit.py` (Redis fixed-window, fails open, off by default)                                                                                                                      |
| `apps/web/src/`                                | Client detail pages; admin `ClientSwitcher`; real error messages; `AbortSignal` + Cancel; Simulated badge; Active Work; audit viewer; `/dev` auth gate                               |
| `alembic/versions/`                            | `0029`–`0035`, all additive and reversible                                                                                                                                           |
| `infra/backup/`, `docs/runbooks/`              | `backup.sh`, `restore.sh`, `restore-drill.sh`, `backup-restore.md`                                                                                                                   |
| `e2e/`                                         | Playwright config + smoke spec, running in Docker                                                                                                                                    |
| `.github/workflows/ci.yml`                     | e2e job; restore-drill job                                                                                                                                                           |
| Docs                                           | `architecture.md`, `operations.md`, `README.md`, `BUILD_REPORT.md`, `CHANGELOG.md`, `DECISIONS.md` (D-016 renumber, D-017 added)                                                     |

---

## 5. Tests run

Every sprint was gated on a **full suite against a quiescent tree** — no result taken while an agent (or the lead) was mid-edit.

- `python -m pytest` → **625 passed, 8 skipped, 0 failed**
- `ruff check app tests` → clean
- `black --check app tests alembic` → clean, 225 files
- `bandit -c pyproject.toml -r app` → **High: 0** (2 pre-existing Mediums, reduced to 1)
- `alembic upgrade head → downgrade → upgrade` for each of `0029`–`0035` → reversible
- Migrations applied against **real Postgres** in the container, not only SQLite
- `prettier --check "**/*.{ts,tsx,js,jsx,json,md,yml,yaml}"` → clean
- `pnpm -F web typecheck` (`tsc --noEmit`) → zero errors
- `pnpm -F web lint` → `✔ No ESLint warnings or errors`
- `pnpm install --frozen-lockfile` → exit 0

**The 8 skips are the gated live-AI smoke tests.** They are not failures. See §8.

---

## 6. Playwright validation

Bootstrapped from zero in Sprint 0 — the repo had **no Playwright at all** (no config, no dependency, `e2e/` held only a `.gitkeep`), despite the remediation document repeatedly instructing us to "extend the s5/s7/s8 spec".

- Runs **inside the compose network** against `http://web:3000`, so CI executes the identical command and no host toolchain is required.
- Behind a `test` profile — `docker compose up` is unchanged.
- Image and `@playwright/test` pinned **together** at `1.61.1`. The image bakes `chromium-1129`; a floating caret fails with `Executable doesn't exist at /ms-playwright/…`.
- **Negative control:** pointed at a dead port it produces 3 failures, exit 1, and a trace. A suite that has never failed is not evidence of anything.
- Green against every sprint's rebuilt stack (3/3, including a console-errors check).
- Artifacts (trace, screenshot, video) land on the host and are gitignored; CI uploads them.

**It is also what caught the `react-dom` defect** (§9) — nothing else in the repo actually ran the app.

---

## 7. The remediation document was wrong in seven places

Acting on it verbatim would have produced bad edits. Each was verified against the code before deviating.

| #   | Document says                                                                                                | Reality                                                                                                                |
| --- | ------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| 1   | **B-6**: Tech Debt export lacks overlap + consolidation                                                      | Both sheets already exist. **Skipped.**                                                                                |
| 2   | **C-1**: a fixture fabricates CrowdStrike/Splunk/Okta capabilities from empty uploads (`app/ai/fixtures.py`) | **That file does not exist.** No fabrication logic anywhere. Two _other_ real defects fixed instead.                   |
| 3   | **E-3**: "copy the open-draft guard CSF already has"                                                         | CSF has no such guard. All three services needed it **built**.                                                         |
| 4   | **A-5**: `claude-opus-4-7` is not a recognizable model id                                                    | It is valid and active. Sub-fix void.                                                                                  |
| 5   | **B-5**: the ZT questionnaire is missing from exports                                                        | The XLSX "Answers" sheet already existed.                                                                              |
| 6   | **H-4**: `CHANGELOG.md` has two `[3.0.0]` headings                                                           | Zero `[3.0.0]` headings. Three `[Unreleased]` variants.                                                                |
| 7   | **H-4**: `audit_entries` is ORM-enforced, no DB trigger                                                      | The trigger is **real** (`audit_entries_block_mutation()`, migration `0001`) _and_ there is a `before_flush` listener. |

Finding 7 was caught by a subagent correcting **this plan** — I had recorded the document's claim. It refused to write a doc statement it had not verified. I then caught it citing `D-015` (multi-tenancy) for the no-Celery decision, which lives in `D-016`.

---

## 8. Issues still open

### 8.1 Live AI is unproven against the real API

**No `ANTHROPIC_API_KEY` exists on this machine, and no live Claude call has been made.** I will not claim otherwise.

What _is_ proven:

- **Structurally** — the A-6 contract tests assert that every key the route consumes is declared in the shape the prompt interpolates. Restoring the original CSF prompt shape yields `AssertionError: route reads data['scores']`; restoring the risk display labels yields `'very_low' is a valid enum token but the prompt never offers it`. Those are the two defects that shipped to production against a green suite.
- **Behaviourally** — fixture-mode Playwright drives the real HTTP path.

To arm the live path: put a key in `.env`, then

```bash
SHIELD_LIVE_SMOKE=1 SHIELD_LLM_MODE=live ANTHROPIC_API_KEY=sk-... \
  python -m pytest tests/live -v
```

The 8 skipped tests become 8 real calls. The gate was verified to arm correctly with a dummy key.

### 8.2 H-6 — CLOSED (was partial; completed after sprint close)

Preview is wired to **ZT, CSF and ATT&CK**. `csf_score` (per tier) and `mitre_map` (per tactic) are chunked, so their preview returns **every chunk**: the union is what egresses, and showing only the first would understate it — a comforting half-truth in the one tool whose purpose is showing egress.

The one-time per-client acknowledgment gate lives **inside `LLMClient.invoke`**, which the codebase itself declares "the ONLY path that calls an external AI provider". Gating there covers every job, including jobs written after the gate exists. It raises `RedactionAckRequiredError` → typed **409** with instructions, **before** the RUNNING audit row is committed and before `provider.complete`: nothing leaves, and nothing is recorded as having tried to. Fixture mode is exempt by construction (nothing egresses), which is why the 625-test suite does not 409 — asserted explicitly, not inferred.

`POST /admin/clients/{id}/redaction-preview-ack` records it. Once per client, not per run: the point is that a human reviewed redaction quality on real client data, not that an operator dismisses a modal on every job. Idempotent — re-acknowledging preserves the original timestamp, so the audit trail shows when review first happened. Migration `0035`.

### 8.3 The v2 Work Order does not exist

`find . -iname "*work*order*"` returns nothing. It is absent from `reference-docs/`. Yet **41 files under `apps/api/app` cite it in code comments** as the specification for the A–F changes. It is the de-facto spec for this codebase and it is not in the repository. It cannot be invented. **This is the single largest risk to whoever maintains this next.**

### 8.4 Smaller

- `PublicHeader` makes a server-side `/intake` fetch on **every authenticated non-admin page render**. Guarded and fails closed, but deserves caching.
- `_MISSING_KEY_CODES` in `storage/s3.py` maps `NoSuchBucket` → 410 ("your file is gone"). A missing _bucket_ is misconfiguration, not a lost object — arguably the same confusion C-7 exists to remove.
- The **sha256 upload-dedup** half of C-8 is unimplemented.
- The `e2e` and `restore-drill` CI jobs are `continue-on-error: true` for their first sprint, matching the plan. Flip to blocking once green for a full sprint.
- CI pays a ~2.8 GB Playwright image pull per run. Cache the layer before making e2e blocking.

---

## 9. Risks and assumptions

### Assumptions recorded (per rule 9)

1. **Repo identity.** The document targets "SHIELD062626"; this is `SHIELD070826`. The file trees are structurally identical and `package.json` still reads `shield062626`. Proceeded here.
2. **Model IDs verified, not assumed.** `claude-opus-4-7` is valid. `claude-haiku-4-5` caps output at **64K**; `claude-sonnet-5` at 128K. Global default moved to `claude-sonnet-5`.
3. **A-3 chunking gates the Haiku split.** The document treats them as independent. They are not — see below.
4. **B-6 skipped**, C-1 re-scoped, E-3 guards built rather than copied, per §7.
5. **Playwright runs in Docker**, per direction, against `http://web:3000`.
6. **No secrets, `.env` files, production config or deployment config were modified.** The compose changes add a test-only service and a read-only `packages/` mount.

### Risks a reviewer should weigh

**The Haiku coupling the document missed.** `mitre_map` is pinned to `claude-haiku-4-5`, whose maximum output is **64K tokens**. The full ATT&CK map is ~65K output tokens. Routing it to Haiku _before_ chunking would truncate the map mid-JSON — recreating the exact defect A-3 exists to fix. Both Haiku jobs pin `max_tokens=32000`. **`AnthropicProvider.complete` still falls back to `128000` when `max_tokens` is `None`**, so a future job pinned to Haiku without an explicit cap will take a 400. Consider a provider-level assertion.

**A green test can prove an invariant that does not hold in production.** E-1 freed the pooled DB connection; E-3 then took an advisory lock via `db.get_bind().connect()`, silently borrowing it back and holding it across the provider call. It was invisible because the suite runs on SQLite, which takes the in-process-mutex branch and never opens a lock connection. Fixed with a dedicated `NullPool` engine and `test_e3_lock_pool.py`. **This is the failure mode the whole engagement is about.** Watch for it.

**The rate limiter is off by default** (`SHIELD_RATE_LIMIT_ENABLED=false`) so the suite stays inert. Production must opt in. It **fails open** if Redis is unreachable — deliberate; a limiter outage must not take the API down.

**Three Dependabot bumps had been merged without their peer upgrades**, and only the Sprint 0 harness caught the worst one. See §10.

---

## 10. Three pre-existing defects nobody had noticed

None of these are in the remediation document. All predate this work. All were on `main`.

| Package              | Committed | Needs                | Symptom                                                       |
| -------------------- | --------- | -------------------- | ------------------------------------------------------------- |
| `eslint-config-next` | `16.2.10` | `eslint>=9`, Next 16 | `next lint` crashed — "Converting circular structure to JSON" |
| `react-dom`          | `19.2.7`  | `react@19`           | **dev server returned HTTP 500 on every request**             |
| `@types/react-dom`   | `19.2.3`  | `@types/react@19`    | type mismatch                                                 |

Plus 17 prettier-dirty files. So `main`'s Web CI job was red on two steps, and the application **did not serve**.

Each bump looks harmless in a PR diff, and the Python suite stays green because it never touches the web tier. The failures surface only when something actually _runs_ the app. That is the same structural lesson as the 45 code defects. The plan's own Deferred Backlog says the framework-majors bundle (Next 15/16, React 19, Tailwind 4) must be done "as one e2e-netted pass after the fix sprints, **never during them**." Pieces were merged early, without the net.

All three are aligned to the Next-14 / React-18 line, the lockfile is regenerated, and the homepage serves 200.

---

## 11. Recommended next sprint

In priority order.

1. **Supply an `ANTHROPIC_API_KEY` and run the live smoke test.** Everything else is inference until a real call succeeds on all five jobs. One command, already written (§8.1). _This is the highest-value hour available._
2. ~~Finish H-6~~ **DONE** (§8.2).
3. ~~Add the provider-level Haiku cap assertion~~ **DONE.** `max_output_tokens(model)` gives each model its real ceiling; an over-cap `max_tokens` now raises `LLMConfigurationError` naming the model and its limit rather than clamping (a clamp truncates mid-JSON — the A-3 defect). An unrecognised model id gets the _conservative_ 64K ceiling, so a future model fails safe. `test_every_pinned_job_fits_its_model_output_ceiling` guards the registry.
4. **Write the e2e click-path specs the plan calls for** — the playbook export gate, extraction errors, the client message thread, the admin switcher, the risk governance flow. The unit tests prove the behaviour; these prove the _user_ can reach it. Then flip `e2e` and `restore-drill` to blocking.
5. **Recover or rewrite the v2 Work Order** (§8.3). Forty-one files cite a document that does not exist.
6. **Deal with the framework-majors bundle deliberately** — Next 15/16, React 19, Tailwind 4, Node 22 — as one pass, behind the e2e net that now exists. Close the open Dependabot PRs together rather than one at a time.
7. **Auth enforcement package**, per D-017: refresh-token rotation and revocation first, then idle timeout, forced re-auth, MFA. The docs no longer claim these exist; the work is now scoped and homed.

---

## 12. How to verify this handoff

```bash
# API
cd apps/api && python -m pytest            # 625 passed, 8 skipped, 0 failed
python -m ruff check app tests             # clean
python -m black --check app tests alembic  # clean
python -m bandit -q -c pyproject.toml -r app   # High: 0

# Migrations (absolute sqlite URL)
DATABASE_URL="sqlite:///C:/tmp/x.db" python -m alembic upgrade head
DATABASE_URL="sqlite:///C:/tmp/x.db" python -m alembic downgrade -7
DATABASE_URL="sqlite:///C:/tmp/x.db" python -m alembic upgrade head

# Web (in Docker; no host pnpm needed)
docker compose up -d web
docker compose exec -T web sh -c 'cd /app && pnpm -F web typecheck && pnpm -F web lint'

# e2e
docker compose up -d db redis minio createbuckets api web
docker compose --profile test run --rm playwright   # 3 passed

# Backup/restore drill (scratch db + scratch bucket; never your real volumes)
docker compose up -d db minio && bash infra/backup/restore-drill.sh
```

Nothing has been pushed. `git log main..HEAD` shows 9 commits.
