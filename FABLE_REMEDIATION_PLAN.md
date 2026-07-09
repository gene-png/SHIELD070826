# FABLE Remediation Plan — SHIELD

**Repository:** `C:\repos\SHIELD070826` (remote: `github.com/gene-png/SHIELD070826`)
**Source document:** `SHIELD_Remediation_Plan_2.docx` (Revision 3, dated July 9, 2026) — 45 fixes across 8 workstreams
**Plan authored:** 2026-07-09
**Lead:** Claude (Opus 4.8) as engineering + QA lead
**Planning / orchestration / validation model:** Fable (`claude-fable-5`)
**Implementation model:** Opus (`claude-opus-4-8`) via focused subagents

---

## 0. Pre-flight: what I verified before planning

Per the tasking gates, nothing below is assumed. Each item was checked against the working tree.

| Gate                                             | Result                                                                    |
| ------------------------------------------------ | ------------------------------------------------------------------------- |
| Can access remediation plan                      | ✅ Yes — `.docx`, extracted to text (452 lines, 77 KB)                    |
| Read the full file                               | ✅ Yes — all 13 sections                                                  |
| Inspected architecture / stack / routing / tests | ✅ Yes — see §0.2                                                         |
| Playwright installed and usable                  | ❌ **NO — not installed anywhere** (see §0.4)                             |
| Permission + ability to run Playwright           | ⚠️ **Not yet** — must be installed first; Docker is available and running |
| Missing pieces documented                        | ✅ §0.4 and §0.5                                                          |
| Written FABLE plan before coding                 | ✅ This document. **No application code has been modified.**              |

### 0.1 Two discrepancies that change the plan

**(a) The plan targets a different repository name.** §1 of the source says _"This is the complete remediation plan for the SHIELD062626 repository."_ We are in `SHIELD070826`. I compared the two local clones: **their tracked file trees are byte-identical in structure**, differing only in `.github/workflows/ci.yml`, three `package.json` files, `pnpm-lock.yaml`, and one reference doc. `package.json` in this repo is still literally `"name": "shield062626"`. **Conclusion: same codebase lineage; the plan applies here.** I proceed against `SHIELD070826`.

**(b) The plan has drifted from the code.** It was written against an older snapshot. A five-agent Fable-driven audit re-verified all 45 fixes line by line. Results:

| Status                | Count | Meaning                                                                            |
| --------------------- | ----- | ---------------------------------------------------------------------------------- |
| `APPLIES_AS_WRITTEN`  | 29    | Defect confirmed; plan's fix is correct                                            |
| `APPLIES_BUT_DRIFTED` | 15    | Defect real, but cited file/line or root cause is wrong — fix must be re-specified |
| `ALREADY_DONE`        | 1     | B-6 — already implemented; **do not touch**                                        |

**No fix was found to be entirely inapplicable**, but several root causes in the source document are provably false. Those are corrected in §F below. Acting on the document verbatim would have produced wrong edits in at least six places.

### 0.2 Architecture as it actually exists

- **Monorepo**, pnpm workspaces. `apps/api` (Python 3.13 / FastAPI / SQLAlchemy / Alembic), `apps/web` (Next.js App Router / TypeScript / Tailwind / NextAuth), `apps/worker` (**empty — only `.gitkeep`**), `packages/` (design-system, questionnaire data).
- **Domain modules** under `apps/api/app/`: `attack/`, `csf/`, `zt/`, `risk/`, `tech_debt/` — each with `exporters.py` + pure scoring functions. `ai/` holds the single LLM egress path (`llm.py`, `engine.py`, `jobs.py`, `redact.py`). `routes/` holds the FastAPI routers.
- **AI is synchronous.** `run-ai` endpoints call `app.ai.engine.run_job` inline. There is **no Celery worker and no queue**, despite `docs/architecture.md` claiming otherwise. Redis runs in compose with **zero consumers**.
- **Compose stack:** `db` (postgres 16), `redis`, `minio` + `createbuckets`, `keycloak`, `mailhog`, `api` (uvicorn --reload, runs `alembic upgrade head` on boot), `web` (node:20, pnpm dev). Compose config validates clean.
- **Multi-tenant** via `client_id` scoping and an `X-Client-Id` header derived from the `shield_active_client_id` cookie.

### 0.3 Test baseline (measured, not assumed)

```
apps/api $ python -m pytest
480 passed in 567.51s (9m 27s)
```

**480 API tests, fully green, ~9.5 min.** This is the regression baseline. Any sprint that reduces this number without an explicit, documented reason has failed.

- **Web tests: 0.** No Jest/Vitest, no component tests.
- **CI (`ci.yml`):** three jobs — Python (ruff + black + pytest + bandit), Web (prettier + eslint + typecheck + build), Secret scan (gitleaks). **There is no e2e job.**

### 0.4 Playwright: MISSING — full statement of what is absent

This is the blocking gap the tasking asked me to document before changing anything.

| Expected                      | Actual                                                                       |
| ----------------------------- | ---------------------------------------------------------------------------- |
| `@playwright/test` dependency | ❌ Absent from every `package.json`                                          |
| `playwright.config.ts`        | ❌ Does not exist anywhere in the repo                                       |
| `e2e/` specs                  | ❌ Directory contains **only `.gitkeep`**                                    |
| Browser binaries              | ❌ Never installed                                                           |
| e2e job in CI                 | ❌ Not present                                                               |
| `pnpm` on the host            | ❌ `command not found` (Node v24.15.0 is present; corepack can provide pnpm) |
| `node_modules`                | ❌ Not installed (root or `apps/web`)                                        |

The only trace of Playwright in the entire repository is an unresolved peer-dependency reference at `pnpm-lock.yaml:1565`.

**Consequence for the source plan:** every instruction of the form _"extend the s5/s7/s8 e2e spec"_, _"the axe/nav specs already provide the pattern"_, or _"the CI workflow already seeds; extend it"_ refers to **artifacts that do not exist**. The source document's §10 claim that "the suite currently reaches workspaces by `page.goto`" describes a suite that is not in this repository. Playwright must be **bootstrapped from zero**, not extended.

Per the user's explicit direction, Playwright will be installed **into the Docker stack** (not the Windows host) so tests run against `http://web:3000` on the compose network, matching CI and avoiding host toolchain drift.

### 0.5 Second blocker: no Anthropic API key

- No `.env` file exists (only `.env.example`).
- `ANTHROPIC_API_KEY` is unset in the shell environment.
- `anthropic==0.96.0` **is** importable on the host, and `anthropic>=0.40` **is** declared in `apps/api/pyproject.toml:25`.

**Therefore live AI calls cannot be executed or validated today.** The user's instruction — _"validate that the AI calls actually work"_ — is achievable in three graded layers, only the third of which needs a key:

1. **Contract tests (no key).** Prove prompt shape == parser shape by construction. This is what would have caught A-2/A-4 — the four live-mode breaks shipped precisely because fixtures were written to match the _route_, not the _prompt_.
2. **Fixture-mode e2e via Playwright (no key).** Prove the Run AI button drives a real HTTP round trip that mutates visible data.
3. **Gated live smoke test (needs key).** `SHIELD_LIVE_SMOKE=1` + `ANTHROPIC_API_KEY` → smallest real call per job. **Skipped by default; wired now, armed the moment a key is supplied.**

**Assumption recorded (per rule 9):** I build all three layers, and run layers 1–2. Layer 3 is implemented and left skipping. I will not invent, generate, or request a key.

### 0.6 Model IDs — verified, and one correction that matters

Checked against the current Claude model catalog, not from memory:

| Model     | ID                 | Context | **Max output** |
| --------- | ------------------ | ------- | -------------- |
| Opus 4.7  | `claude-opus-4-7`  | 1M      | 128K           |
| Sonnet 5  | `claude-sonnet-5`  | 1M      | 128K           |
| Haiku 4.5 | `claude-haiku-4-5` | 200K    | **64K**        |

**Correction to source §2, FIX A-5.** The document asserts the default `claude-opus-4-7` "is not a recognizable Anthropic model identifier, so the first live call after A-1 would likely 404." **This is false.** `claude-opus-4-7` is a valid, active model. The A-5 sub-fix "set the default to a valid model id" is therefore **moot as stated**. The rest of A-5 (typed configuration errors) stands.

**A load-bearing coupling the source document missed.** Section 8's Model decision routes `mitre_map` and `csf_score` to Haiku. But:

- `AnthropicProvider.complete` currently streams with a hardcoded **`max_tokens=128000`** (`app/ai/llm.py:149`), with a comment stating the full ATT&CK map is _"~65K tokens even when terse."_
- **Haiku 4.5 caps output at 64K.**

So routing `mitre_map` to Haiku **as the code stands today would either 400 on the `max_tokens` value or truncate the map mid-JSON** — reintroducing exactly the failure A-3 exists to fix. **A-3's chunking is a hard prerequisite for the Haiku decision, not a parallel nicety.** This is sequenced accordingly in §B (Sprint 1).

---

## F — Findings

Every fix from the source document, with its **verified** status. `→` marks a correction to the source's stated root cause. Line numbers below are the **real** ones in this tree.

### Workstream A — Make live AI actually work

| ID      | Sev      | Status    | Verified finding                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ------- | -------- | --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A-1** | CRITICAL | `DRIFTED` | → **`anthropic>=0.40` is already declared** (`pyproject.toml:25`). The dependency half is done. What remains: `LLMClient.from_settings` (`llm.py:190-193`) performs **no startup import check**; the SDK import is still lazy inside `_ensure_client` (`llm.py:118`). A live-mode container that cannot import the SDK still fails at first click, not at boot.                                                                                                                                            |
| **A-2** | CRITICAL | `APPLIES` | Confirmed. Prompt (`ai/jobs.py:50-53`) demands `{"subcategories":[{"code":…}]}` — **no tier**. Route (`routes/csf.py:1083-1086`) reads `data["scores"]` keyed `f"{tier}                                                                                                                                                                                                                                                                                                                                    | {subcategory_code}"`. **A compliant live response matches zero rows.** Worse, the payload (`csf.py:1073-1076`) sends only tier strings + subcategory codes, while the prompt (`jobs.py:42-43`) claims the model receives "interview answers, evidence summaries" — **the model is asked to score from nothing.** |
| **A-3** | CRITICAL | `DRIFTED` | → **`max_tokens=4096` no longer exists.** Code now streams at `max_tokens=128000` (`llm.py:147-149`). The _symptom_ was addressed differently than the plan assumed. **But the plan's actual remedies were never built:** `AIJob` (`ai/engine.py:27-41`) has **no per-job `model` or `max_tokens`**, and **there is no chunking** — `attack.py:491-512` sends all 600+ techniques in one call (a stale comment there even describes batching that was then removed). **Blocks the Haiku decision (§0.6).** |
| **A-4** | CRITICAL | `APPLIES` | Confirmed exactly. Prompt asks for `Very Low..Very High` / `Negligible..Catastrophic` (`jobs.py:110-112`); enums are lowercase snake_case (`risk/engine.py:23-36`); `_enum_or_none` (`routes/risk.py:177-181`) returns `None` on mismatch → `likelihood`, `impact`, and code-derived `tier` all silently null.                                                                                                                                                                                             |
| **A-5** | HIGH     | `DRIFTED` | → **Model id `claude-opus-4-7` is VALID** (§0.6); that sub-fix is moot. → **`GET /admin/ai-status` already exists** (`routes/admin.py:642-679`, tested). **Remaining:** missing key raises a bare `RuntimeError` (`llm.py:107-111`), as does an unimplemented provider (`llm.py:176-179`) — not the typed `{reason, message}` pattern.                                                                                                                                                                     |
| **A-6** | HIGH     | `APPLIES` | Confirmed, and worse than described. **Zero tests import `AnthropicProvider`.** No shared schema constant exists. `tests/conftest.py:12` forces `SHIELD_LLM_MODE=fixture` suite-wide. Critically, `tests/unit/test_csf_run_ai.py:80,109,127` register fixtures returning `{"scores":[…]}` — **the route's shape, not the prompt's** — which is the precise mechanism by which A-2 shipped green.                                                                                                           |

### Workstream B — Deliverable integrity

| ID      | Sev      | Status            | Verified finding                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ------- | -------- | ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **B-1** | CRITICAL | `DRIFTED`         | Real. Finalize is at **`routes/zt.py:1073`** (not 1066): `analyze_gaps(cat_fw, stage_map, notes=notes_map)` — no target args → falls back to `DEFAULT_TARGET_STAGE=3` (`zt/scoring.py:32,235`). Dashboard passes both at `zt.py:901-908`. `zt_target_stage` exists (`models/service_request.py:83`) and helper `_client_target_stage` (`zt.py:140-150`) is used **only for serialization**, never by finalize.                                                                                                                                                             |
| **B-2** | CRITICAL | `DRIFTED`         | Real. Finalize at **`routes/csf.py:1374`** (not 1404): `analyze_gaps(tier_map, notes=notes_map)` → `DEFAULT_TARGET_TIER=3` (`csf/maturity.py:23`). `csf_target_tier` at `models/service_request.py:81`; helper `_client_target_tier` (`csf.py:131-141`) serialization-only.                                                                                                                                                                                                                                                                                                |
| **B-3** | CRITICAL | `DRIFTED`         | Real. Only gate is `if not all_rows` at **`routes/csf.py:1153-1157`**. Seeding creates rows with all five dimensions defaulting to `0` (`models/csf_profile.py:49-53`), and `csf/playbook.py:58-62` maps total `0-2 → Level 1`. **No `scored_at` field exists anywhere** (grep: zero hits). **Export is not gated on approval.** Export clears `documents_stale` at `csf.py:1262` regardless.                                                                                                                                                                              |
| **B-4** | MEDIUM   | `APPLIES`         | Confirmed exactly. `DEFAULT_TOP_N = 20` (`zt/scoring.py:33`, `csf/gap.py:41`); caps at `scoring.py:278`, `gap.py:150`. `total_gap_count` carries the truth. The ZT PDF even prints the true `total_gap_count` (`zt/exporters.py:395`) beside a 20-row table — the contradiction is already on the page.                                                                                                                                                                                                                                                                    |
| **B-5** | MEDIUM   | `APPLIES`         | Confirmed, with nuance. `build_roadmap` (`zt/scoring.py:297`) has exactly **one** call site — the dashboard endpoint (`zt.py:909`). `zt/exporters.py` never imports it. → **Nuance the source missed:** an "Answers" sheet **already exists** in the XLSX (`zt/exporters.py:123`). Only the **DOCX** questionnaire section and the roadmap are missing.                                                                                                                                                                                                                    |
| **B-6** | MEDIUM   | ✅ `ALREADY_DONE` | **The source document is wrong.** `tech_debt/exporters.py` already computes overlap (`_build_analysis` → `analyze_overlap`, :98,129) and renders XLSX sheets **"Spend by Category"** (:211), **"Overlaps"** (:233), **"Consolidation Plan"** (:263), plus DOCX **"Functional Overlaps"** (:773) and **"Consolidation Plan"** (:793). **Take no action. Removing this from scope saves an M-effort sprint item.**                                                                                                                                                           |
| **B-7** | MEDIUM   | `DRIFTED`         | All four sub-claims verified individually: **(1)** alphabetical sort cut at 50 — `attack/exporters.py:250-252` (DOCX) and `:352-354` (PDF). **(2)** No 5×5 matrix in Risk DOCX (`risk/exporters.py:294-330`); the **module docstring** (:4-5) promises it and the PDF renders it (:242-256). **(3)** Blank name on KeyError, `attack/exporters.py:262`; PDF falls back to the code (:369). **(4)** `deliverable_filename` (`tech_debt/filename.py:38`) is **never imported** by `routes/risk.py`; playbook export uses raw f-strings at `csf.py:1181,1193,1205,1217,1229`. |

### Workstream C — Extraction that works and never lies

> **Correction that invalidates the source's headline claim.** The document repeatedly cites `app/ai/fixtures.py`. **That file does not exist.** Fixture logic lives in `FixtureProvider` (`ai/llm.py:65-94`), which ships with **zero** canned responses and raises `KeyError` when a purpose is unregistered (`llm.py:88-92`). A repo-wide grep for `CrowdStrike|Splunk|Okta|120000|Capability 501` in `apps/api/app` returns **zero hits**.

| ID      | Sev      | Status    | Verified finding                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ------- | -------- | --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **C-1** | CRITICAL | `DRIFTED` | → **The "fabricated CrowdStrike/Splunk/Okta capabilities" defect DOES NOT EXIST in this repo.** No hardcoded demo fixture exists in production code (those names appear only in per-test registrations, e.g. `tests/unit/test_tech_debt_routes.py:164`). **Two real defects remain:** the truncation sentinel `{"__truncated__": True, …}` **is** appended as a row (`tech_debt/parsers.py:62-65`) and is **never skipped** — it ships to the LLM inside `rows` (`extract.py:172-180`); and **there is no zero-row guard** — an empty file still triggers an LLM call and a `201` (`routes/tech_debt.py:154-235`). |
| **C-2** | CRITICAL | `APPLIES` | Confirmed. `"application/vnd.ms-excel"` allowlisted (`routes/artifacts.py:43`) → mapped to the xlsx path (`parsers.py:29`) → `openpyxl` raises `zipfile.BadZipFile`, which the route does **not** catch (it catches only `UnsupportedInventoryFormat` at `tech_debt.py:182` and `ValueError` at `:187`) → **unhandled 500**. Web accept lists: `TechDebtWorkspace.tsx:236`, `Dropzone.tsx:23`, `IntakeDocumentsPanel.tsx:20,26`.                                                                                                                                                                                   |
| **C-3** | HIGH     | `APPLIES` | Confirmed. `wb.active` only (`parsers.py:83`); header-keyed dicts collapse duplicate columns, last wins (`parsers.py:91,95-99`). **`parsers.py` has zero direct tests** — no `test_parsers*` file exists.                                                                                                                                                                                                                                                                                                                                                                                                          |
| **C-4** | HIGH     | `APPLIES` | Confirmed. `_opt_int` / `_opt_float` (`tech_debt/extract.py:128-144`) swallow `TypeError`/`ValueError` into silent `None`, so `"$120,000"` → `None`. `confidence_pct` coerced at `:154` with **no 0–100 clamp**; a value of 250 reaches the DB (`routes/tech_debt.py:214`).                                                                                                                                                                                                                                                                                                                                        |
| **C-5** | HIGH     | `APPLIES` | Confirmed verbatim: `raw_items = decoded.get("items", []) if isinstance(decoded, dict) else []` (`extract.py:116`). A list-shaped or wrong-key response yields `[]`, and the route still mints a version and returns **201** (`tech_debt.py:196-235`). Test `test_tech_debt_routes.py:251` **cements** the empty-201 behavior.                                                                                                                                                                                                                                                                                     |
| **C-6** | MEDIUM   | `APPLIES` | Confirmed. Client-asserted `file.content_type` trusted (`artifacts.py:79-84`); no magic-byte sniffing anywhere. `data = await file.read()` at `:86` **precedes** the size check at `:92`. Next proxy re-buffers via `await request.formData()` then re-serializes (`api/proxy/artifacts/route.ts:55,63-67`).                                                                                                                                                                                                                                                                                                       |
| **C-7** | MEDIUM   | `APPLIES` | Confirmed. `_load_artifact_bytes` sniffs the **private attribute** `_path_for` (`extract.py:92-93`) and otherwise calls `urllib.request.urlopen(url)` at `:100` with **no timeout**. `S3Storage.get()` wraps **every** exception into `FileNotFoundError` (`storage/s3.py:43-48`), which routes translate to **410 Gone** — telling the user their file is permanently lost when MinIO is merely down.                                                                                                                                                                                                             |
| **C-8** | MEDIUM   | `DRIFTED` | → **`require_artifact_in_tenant` already exists** (`tenant.py:60-71`) and is used by the extract route (`tech_debt.py:164`). It is simply **not called** at the three evidence sites: `attack.py:361-362`, **`csf.py:471-472`** (not 500), **`zt.py:612-613`**. Upload dedupe absent: `artifacts.py:98-115` stores under a fresh `uuid4()`, records `sha256` (:108), never queries it.                                                                                                                                                                                                                             |

### Workstream D — Navigation and everyday usability

| ID      | Sev    | Status    | Verified finding                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ------- | ------ | --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **D-1** | HIGH   | `APPLIES` | Confirmed. `SELF_ASSESSMENT_TYPES` lists only `nist_csf`, `zero_trust_cisa`, `zero_trust_dod` (`AssessmentsView.tsx:29-33`); `canContinue = isSelfAssessment && status === "draft"` (:310-311) gates the only link (:331-338). Self-assessment `COPY` map omits `tech_debt`/`attack_coverage` (`self-assessment/[serviceId]/page.tsx:16-32`) → dead-end card. `/messages` only links back to `/assessments` (`messages/page.tsx:37-42`).                                                                                                                                                                                                                                                                                                                                                    |
| **D-2** | HIGH   | `DRIFTED` | Real; details differ. Header block is **`AdminShell.tsx:98-115`** and renders Home + "View public site" + email + `SignOutButton` — **not** merely "email and sign-out." `ClientSwitcher` (`site/ClientSwitcher.tsx:28`) is rendered **only** in `PublicHeader.tsx:60`. Raw 400 chain confirmed: `dependencies.py:119-123` → `messages/client.ts:86-95` surfaces the backend `detail` verbatim.                                                                                                                                                                                                                                                                                                                                                                                             |
| **D-3** | MEDIUM | `APPLIES` | Confirmed. `ProxyError` does `super(\`Intake proxy ${status}\`)`and stores`payload` **unused** (`lib/intake/client.ts:18-25`); `AssessmentsView.tsx:116-121`(and`:82-86`) render `err.message`. → **Useful nuance:** `lib/messages/client.ts:86-95` already implements the correct pattern — copy it, don't invent it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| **D-4** | MEDIUM | mixed     | **(a)** `APPLIES` — no session check in `app/assessments/page.tsx:12-23`, no `middleware.ts` in `apps/web`. **(b)** `APPLIES` — `SignUpForm.tsx:59` emits `?registered=1`; `SignInForm.tsx:8-9` reads only `callbackUrl`. **(c)** `DRIFTED` — `Hero.tsx:26` is the only nav `<Link>`, but `SignUpForm.tsx:64` also lands users on `/intake` via `window.location.assign`. **(d)** `DRIFTED` — **the referenced screen is NOT dead**; `IntakeSubmitted` still renders (`IntakeWizard.tsx:194-195`). The real defect is copy-only guidance with **no link** (`page.tsx:77-89`). **(e)** `APPLIES` — `dev/questionnaire-preview/page.tsx` is a client page with no auth, no layout, no middleware. **(f)** `APPLIES` — `admin/active/page.tsx:20-33` is a stub linking back to `/admin/queue`. |

### Workstream E — Operational hardening

| ID      | Sev    | Status    | Verified finding                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ------- | ------ | --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **E-1** | HIGH   | `DRIFTED` | → **Timeout/retries ALREADY EXIST**: `Anthropic(max_retries=2, timeout=120.0)` (`llm.py:125-129`), and every completion streams (:147-160). → Pool citation stale: `db/session.py:19-25` sets only `pool_pre_ping=True` (SQLAlchemy defaults still yield 5+10). **Still real:** the AI call **holds the request-scoped DB session for its entire duration** (`zt.py:339-406`, `attack.py:442-512` → `engine.py:83-107` → `llm.py:234-243`), and **no `AbortSignal` exists anywhere** in `apps/web/src` (grep: zero hits; `lib/api.ts:75-80` passes no `signal`). |
| **E-2** | HIGH   | `APPLIES` | Confirmed, and the test proves nothing. `invoke` uses **only `db.flush()`** — at row-create (`llm.py:235`), in the failure handler (:248), and on completion (:264). `get_db` (`db/session.py:32-38`) yields then closes with **no commit**. The failure row is discarded on rollback. `tests/unit/test_llm_client.py:94-119` calls `db.commit()` **itself** at :113 and reads back in the **same session** at :115 — a commit no production path performs.                                                                                                      |
| **E-3** | MEDIUM | `DRIFTED` | **Worse than the plan states.** No mutex anywhere (grep `with_for_update                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | advisory_lock | FOR UPDATE`→ zero hits).`models/risk_register.py:31`has **no`**table_args**`at all** (contrast`uq_zt_assessments_service_version`, `zt_assessment.py:53-55`). → **The plan says "copy the open-draft guard CSF already has." That guard does not exist.** `routes/csf.py:350-387`does`prior version + 1` unconditionally (:357-358), identical to ZT (`zt.py:487-495`) and ATT&CK (`attack.py:252-259`). **All three need it built, not copied.** |
| **E-4** | MEDIUM | `APPLIES` | Confirmed. ZT returns `pillar_narratives`, `executive_summary`, `roadmap_summary` in the HTTP response only (`zt.py:452-458`); `ZtAssessment` has no such columns. The `mitre_map` prompt requests `executive_summary` + `top_blind_spots` (`jobs.py:96`) and `attack.py:523` consumes **only** `result.data["techniques"]` — paid for, then discarded.                                                                                                                                                                                                          |
| **E-5** | MEDIUM | `DRIFTED` | Copy verified verbatim; citation stale. _"Running in fixture mode — AI features are disabled."_ is at **`routes/admin.py:666-668`** (the plan's :476-478 is now `remove_client_domain`). `AiStatusBanner.tsx:44-46` says _"won't produce results."_ Grep for `simulated` across `apps/web/src` → **zero hits**.                                                                                                                                                                                                                                                  |
| **E-6** | LOW    | `APPLIES` | Confirmed. `scripts/_common.py:14-15` resolves `parents[3]`. In the container `/app/scripts/_common.py` has only three parents → **IndexError**. Compose mounts `./apps/api:/app` for api (:160) but mounts `packages/` **only for web** (:193), so the loaders' data files are absent from the image regardless.                                                                                                                                                                                                                                                |

### Workstream F — Process simplification and governance

| ID      | Sev    | Status    | Verified finding                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ------- | ------ | --------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **F-1** | MEDIUM | `DRIFTED` | Real; lines moved. Seed endpoint at **`csf.py:807-862`**; unseeded 409 at **`csf.py:1057-1061`**. → **Confirmed safe to auto-seed:** seeding loads existing `(tier, subcategory_code)` pairs and skips them (`csf.py:831-843`) — **idempotent**.                                                                                                                                                                                                                      |
| **F-2** | LOW    | `APPLIES` | Confirmed. `attack.py:455-459` and `zt.py:356-360` both 404 with _"Create an assessment first."_                                                                                                                                                                                                                                                                                                                                                                      |
| **F-3** | HIGH   | `APPLIES` | Confirmed in all three parts. Routes are gate/generate/export/latest only (`routes/risk.py:98,184,305,376`) — **no PATCH/DELETE/lock/approve**; entries written once with `origin="ai_generated"` (:258-259). Gate at `risk.py:72-88` checks only `_latest(...) is not None`. CSF harvest threshold hardcoded `r.maturity_tier < 3` (`risk.py:147`). → **Nuance:** ZT **does** honor a per-row target (:163-164) — the fixed-threshold criticism is **CSF-specific**. |

### Workstream G — Decision fixes

| ID      | Sev    | Status    | Verified finding                                                                                                                                                                                                                                                                                                                                                                     |
| ------- | ------ | --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **G-1** | MEDIUM | `APPLIES` | Confirmed. Client-facing labels at `AssessmentsView.tsx:40,55-58`. Client-visibility gates keyed on `RELEASED`: `csf.py:410-413`, `attack.py:304-307`, `zt.py:541-544`. `RELEASED` is declared in five models but **no route ever assigns it** — the only writer is `scripts/seed_demo.py`. **The app promises clients a state no production code path can reach.**                  |
| **G-2** | MEDIUM | `APPLIES` | Confirmed. `_client_tool_names` (`attack.py:405-424`) joins `CapabilityItem → CapabilityList → Service` filtered only on `client_id` and `kind == TECH_DEBT` (:416-419) — **no status filter, no version filter**. `CapabilityListStatus` (DRAFT/APPROVED/RELEASED) exists at `models/capability.py:44-47`, and `CapabilityList` carries both `version` (:67) and `status` (:68-72). |
| **G-3** | MEDIUM | `APPLIES` | Confirmed. `shield_llm_mode: Literal["fixture","live"] = "fixture"` (`config.py:54`). `assert_safe_for_runtime` (`config.py:101-109`, called from `main.py:39`) guards **only** redaction-off-in-prod and the placeholder JWT secret. **`SHIELD_DEMO` is referenced nowhere.** Production can silently serve canned AI output.                                                       |

### Workstream H — Security governance, documentation truth, operations

| ID      | Sev    | Status    | Verified finding                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ------- | ------ | --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **H-1** | HIGH   | `APPLIES` | Confirmed. `shield_idle_timeout_seconds` / `shield_forced_reauth_seconds` defined at `config.py:85-86` and referenced **nowhere else in app code** (only `.env.example:100-101`, `docker-compose.yml:32-33`, `README.md:101`). `/auth/refresh` (`routes/auth.py:317-340`) re-issues a pair with **no rotation or revocation**; logout (:343-359) is audit-only. `BUILD_REPORT.md:60` (OWASP A07) claims "compensating controls listed." **The controls are fictional.**                                                                                                                                                                                                                                                                        |
| **H-2** | HIGH   | `APPLIES` | Confirmed. Zero rate limiting (grep `slowapi\|limiter\|rate.limit\|throttle` → comments/docs only). `docker-compose.yml:174-175` explicitly states Redis "has no consumer today."                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| **H-3** | HIGH   | `APPLIES` | Confirmed. `docs/runbooks/` and `infra/terraform/` each contain **only a zero-byte `.gitkeep`**. No pg_dump/restore script exists. Meanwhile `docs/operations.md:24-28` **describes backups** and `:44-52` promises five runbooks that do not exist.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| **H-4** | MEDIUM | `DRIFTED` | Most claims true; **one is false.** True: `architecture.md:7` says "single-tenant"; `:13,43,46,59,62` claim Celery + `apps/worker`; `:72` claims an `audit_events` table (actual: `audit_entries`, ORM-enforced, no DB trigger); `:77` claims `redactor.unredact` (does not exist); `DECISIONS.md` has **two D-015 headings** (:112, :134); `admin.py:323` and `:376` both say "(admin/reviewer)" while gated ADMIN-only (:58); `docs/runbooks` empty; **no Work Order document exists anywhere** despite dozens of code comments citing it; D-009 promises `next-intl` (absent from `package.json`). → **FALSE:** `CHANGELOG.md` has **no `[3.0.0]` heading at all** — the real defect is **three** `[Unreleased]` variants (:5, :170, :304). |
| **H-5** | MEDIUM | `APPLIES` | Confirmed. `models/llm_call.py:42-83` has `service_id` but **no `client_id`**. No `/admin/ai-usage` endpoint; the only AI admin route is `ai-status`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| **H-6** | MEDIUM | `APPLIES` | Confirmed. No `run-ai` endpoint accepts a preview/dry-run parameter; grep for `dry_run\|preview` in `apps/api/app` hits only an unrelated message-thread `last_preview` field.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| **H-7** | LOW    | `APPLIES` | Confirmed. Audit table is `audit_entries` (`models/audit_entry.py:26`), immutable via ORM hooks (:45-61), written only through `audit/spine.py::audit()`. `AuditEntry` is **imported nowhere in `app/routes`**. No `/admin/audit` route or page. **The trail is write-only.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| **H-8** | MEDIUM | `APPLIES` | Confirmed. No `CsfActionItem` model; grep `CsfAction\|action_item\|due_date\|poam` in CSF models → nothing. No owner or due-date field anywhere in CSF models or exports.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |

### F.1 — Findings the source document did not contain

Surfaced by the audit; these are mine, not the document's.

| ID      | Sev    | Finding                                                                                                                                                                                                                   |
| ------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **X-1** | HIGH   | **Haiku's 64K output cap collides with `max_tokens=128000`.** Routing `mitre_map` to `claude-haiku-4-5` (Section 8 decision) before A-3 chunking lands will 400 or truncate. **A-3 must precede the model split.** (§0.6) |
| **X-2** | HIGH   | **No e2e harness exists at all.** Plan steps that say "extend the e2e spec" have nothing to extend. Bootstrapping Playwright is a prerequisite, not a sub-task. (§0.4)                                                    |
| **X-3** | MEDIUM | **`E-3`'s premise is inverted.** The plan says to copy CSF's open-draft guard to ZT/ATT&CK. **CSF has no such guard.** All three must be written.                                                                         |
| **X-4** | MEDIUM | **`B-6` is already implemented.** Acting on the document verbatim would have produced a redundant, conflict-prone rewrite of `tech_debt/exporters.py`.                                                                    |
| **X-5** | MEDIUM | **`C-1`'s marquee defect does not exist here.** No fabrication fixture. Writing the plan's fix (2) would edit a file that isn't in the tree.                                                                              |
| **X-6** | LOW    | **No `.env`; no API key.** Live-mode validation is impossible until supplied. (§0.5)                                                                                                                                      |

---

## A — Actions

Concrete code changes, restated against **verified** line numbers. Grouped by the sprint that will execute them.

### Sprint 0 — Validation harness (prerequisite; touches no application code)

- **A0-1 — Playwright in Docker.** Add a `playwright` service to `docker-compose.yml` using `mcr.microsoft.com/playwright:v1.5x-jammy`, on the compose network, `depends_on: [web, api]`, mounting `./e2e`. Base URL `http://web:3000`. Add `e2e/playwright.config.ts` (projects: chromium; `trace: "on-first-retry"`, `screenshot: "only-on-failure"`, `video: "retain-on-failure"`), `e2e/package.json`, and a smoke spec asserting the homepage renders.
- **A0-2 — Fixture-safe seeding.** Wire the existing `scripts/seed_demo.py` into a one-shot compose service so e2e has a deterministic client/admin.
- **A0-3 — CI e2e job.** Add a fourth job to `ci.yml` that brings up the stack and runs the Playwright container. Non-blocking (`continue-on-error`) for its first sprint, then made blocking.
- **A0-4 — Live-smoke scaffold.** Add `tests/live/test_live_smoke.py`, skipped unless `SHIELD_LIVE_SMOKE=1` **and** `ANTHROPIC_API_KEY` is present.

### Sprint 1 — Trustworthy core

- **A-1** — In `LLMClient.from_settings` (`llm.py:190`), when `shield_llm_mode == "live"`, `import anthropic` eagerly and raise a typed config error naming the missing package. Add a CI step `python -c "import anthropic"` inside the built image.
- **A-2** — Create `app/ai/schemas.py` holding **one constant per job** describing the response shape. Rewrite the CSF prompt (`jobs.py:50-53`) to demand `{"scores":[{"tier","subcategory_code","governance","policy","implementation","monitoring","improvement","what_we_found"}]}`. **Ground the payload** (`csf.py:1073-1076`): include the seeded tier list, questionnaire answers (tier + notes per subcategory), and evidence flags — redacted as always. Route, prompt, and fixture all import the same constant.
- **A-3** — Add `model: str | None` and `max_tokens: int | None` to `AIJob` (`engine.py:27-41`); thread through `run_job` → `LLMClient.invoke` → `provider.complete` (replacing the `128000` hardcode at `llm.py:149`). Chunk `mitre_map` **per tactic** (~40–90 techniques/call) and `csf_score` **per tier**. Merge in the route; **apply only after every chunk parses** — one bad chunk fails the whole run loudly. Each chunk is a separate `LLMClient.invoke`, so redaction + `llm_calls` audit rows still apply per call. **Then** set `mitre_map`/`csf_score` → `claude-haiku-4-5` (with per-job `max_tokens ≤ 64000`, per X-1) and leave `tech_debt_extract`/`zt_score`/`risk_synthesize` on the env default.
- **A-4** — Fix **both sides**: state exact lowercase snake_case tokens in the prompt (`jobs.py:110-112`), and make `_enum_or_none` (`risk.py:177-181`) normalize (lower, strip, spaces→underscores) before coercing. Count nulled entries during generation; return a warning field when any enum failed.
- **B-1** — In ZT finalize (`zt.py:1073`), resolve per-capability `target_stage` from answers with engagement fallback to `ServiceRequest.zt_target_stage`, default 3 only if neither exists; pass `target_stage=…, targets=…` exactly as `zt.py:901-908`. Print the resolved target in the deliverable summary line.
- **B-2** — Mirror for CSF finalize (`csf.py:1374`) using `ServiceRequest.csf_target_tier`.
- **B-3** — Add nullable `scored_at` to `CsfDimensionScore` (**additive migration**), set whenever a human or AI writes a row; seeding leaves it `null`. Gate export (`csf.py:1153`) with a typed **409** unless every in-scope row is scored **and** the assessment is approved; the message states how many rows remain. Belt-and-braces: exporters render `"Unscored"` for a null-scored row rather than Level 1. Do **not** clear `documents_stale` (`csf.py:1262`) unless the gate passed.
- **C-1** _(re-scoped per X-5)_ — In the extract route, return a typed **422** when `parse_inventory` yields zero data rows, **before** calling the LLM. Skip the `__truncated__` sentinel wherever rows are iterated (`extract.py:172-180`). **Do not** touch fixtures — the fabrication defect is not present.
- **C-2** — Reject at upload with a typed **415** ("Legacy .xls is not supported; re-save as .xlsx"). Remove `application/vnd.ms-excel` from `artifacts.py:43`. Remove `.xls` from `TechDebtWorkspace.tsx:236`, `Dropzone.tsx:23`, `IntakeDocumentsPanel.tsx:20,26`. Wrap the parse call so `BadZipFile`/`InvalidFileException` return the same typed 422 rather than a 500.
- **G-2** — Filter `_client_tool_names` (`attack.py:405-424`) to the **latest `APPROVED`** `CapabilityList` per tech-debt service, unioned across services. When empty, return a `warning` field on run-ai and render it in the workspace before the run applies.

### Sprint 2 — Solid operations

`A-5` (typed config errors only — the model-id sub-fix is void), `A-6` (contract test per job + gated live smoke), `C-3`–`C-8`, `D-1`–`D-3`, `E-1` (session release around the provider call + client `AbortSignal` + cancel UI; timeouts already exist), `E-2` (autonomous session for `llm_calls`; fix the self-proving test), `E-3` (advisory lock + unique constraint + **build** the open-draft guard in all three routes, per X-3), `E-4`, `E-5`, `F-1`, `F-2`, `G-3`, `H-2`, `H-5`, `H-6`.

### Sprint 3 — Complete deliverables and truth

`B-4`, `B-5` (roadmap + **DOCX** answers section; XLSX Answers already exists), **`B-6` — SKIPPED, already done**, `B-7` (four sub-fixes), `D-4` (six potholes), `E-6`, `F-3`, `G-1`, `H-1`, `H-3`, `H-4` (**with the CHANGELOG claim corrected**), `H-7`, `H-8`.

---

## B — Build Plan

Sprints are small, sequenced by dependency and client-facing risk. **One sprint at a time. Lead reviews every diff before the next sprint starts.**

### Sprint 0 — Validation Harness

|                |                                                                                                                                                                                                                                                                       |
| -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Goal**       | Make it possible to _prove_ anything. Playwright running in Docker against the composed stack; live-smoke scaffold armed but skipped.                                                                                                                                 |
| **Issues**     | X-2, X-6 (mitigation), A0-1…A0-4                                                                                                                                                                                                                                      |
| **Files**      | `docker-compose.yml` (add service), `e2e/playwright.config.ts`, `e2e/package.json`, `e2e/specs/smoke.spec.ts`, `.github/workflows/ci.yml`, `apps/api/tests/live/test_live_smoke.py`                                                                                   |
| **Subagent**   | Playwright QA subagent                                                                                                                                                                                                                                                |
| **Risks**      | Compose networking (`web:3000` vs `localhost:3000`); first `pnpm install` inside the web container is slow; browser image is ~1.5 GB. **Mitigation:** pin the Playwright image tag to the installed `@playwright/test` minor; do not touch the `web` service command. |
| **Acceptance** | `docker compose run --rm playwright` exits 0 on the smoke spec. `pytest` still reports **480 passed**. **Zero files under `apps/api/app` or `apps/web/src` are modified.**                                                                                            |

### Sprint 1 — Trustworthy Core

|                |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Goal**       | No fabricated data; no deliverable that contradicts its dashboard; live AI is structurally capable of succeeding.                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| **Issues**     | A-1, A-2, A-3 (+X-1), A-4, B-1, B-2, B-3, C-1 (re-scoped), C-2, G-2                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| **Files**      | `ai/llm.py`, `ai/engine.py`, `ai/jobs.py`, **`ai/schemas.py` (new)**, `routes/csf.py`, `routes/zt.py`, `routes/attack.py`, `routes/risk.py`, `routes/artifacts.py`, `routes/tech_debt.py`, `risk/engine.py`, `tech_debt/parsers.py`, `tech_debt/extract.py`, `models/csf_profile.py`, **new Alembic revision (additive)**, `TechDebtWorkspace.tsx`, `Dropzone.tsx`, `IntakeDocumentsPanel.tsx`                                                                                                                                                                                    |
| **Subagents**  | Backend remediation (A-1…A-4, B-1…B-3, G-2) · Security remediation (C-2 upload allowlist) · Frontend remediation (accept lists only) · Test/lint/build validation                                                                                                                                                                                                                                                                                                                                                                                                                 |
| **Risks**      | **A-3 chunking is the highest-risk change in the plan** — merge loss or double-application would corrupt an assessment. **Mitigation:** apply suggestions only after _all_ chunks parse; unit-test that the chunker covers all 633 technique codes exactly once. **B-3's migration is additive-only** (nullable column) — no backfill, no drop. **C-2 removes a MIME type** — verify no existing artifact rows depend on it.                                                                                                                                                      |
| **Acceptance** | Contract test: a prompt-shaped CSF response applied through the route changes ≥1 row. Chunker test: all 633 codes covered exactly once; one bad chunk aborts the whole run. `analyze_gaps` receives a non-default target in both finalize paths, and XLSX Gap Plan row count == dashboard `total_gap_count`. Seed→export returns **409**; score-all + approve → **200**, no `"Unscored"` cells. Header-only CSV → **422**, no LLM call, no `CapabilityList` row. `.xls` upload → **415**. Draft-only capability lists → empty tool universe **+ warning**. `pytest` ≥ 480 passed. |

### Sprint 2 — Solid Operations

|                |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Goal**       | Bounded, audited, concurrency-safe runtime; extraction survives real files; users are never stranded.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| **Issues**     | A-5, A-6, C-3…C-8, D-1, D-2, D-3, E-1…E-5 (+X-3), F-1, F-2, G-3, H-2, H-5, H-6                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| **Files**      | `ai/llm.py`, `db/session.py`, `config.py`, `main.py`, `routes/*.py`, `storage/s3.py`, `tech_debt/parsers.py`, `models/llm_call.py`, new Alembic revisions (additive), `apps/web/src/lib/api.ts`, `lib/intake/client.ts`, `AdminShell.tsx`, `AssessmentsView.tsx`, `AiStatusBanner.tsx`, messages/inbox views                                                                                                                                                                                                                                                                                                                                                                                                                |
| **Subagents**  | Backend (E-1…E-5, A-5, A-6, F-1, F-2, H-5, H-6) · Security (C-6, C-7, C-8, G-3, H-2) · Frontend (D-1, D-2, D-3, E-5 badge) · Playwright QA · Validation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| **Risks**      | **E-1's session split** changes transaction boundaries around every AI call — highest regression risk in the sprint; gate on the full 480-test suite plus new concurrency tests. **E-3's advisory lock** must degrade to a no-op on SQLite (the test DB). **H-2 rate limiting** could trip the e2e suite — set limits from config and keep e2e under threshold.                                                                                                                                                                                                                                                                                                                                                             |
| **Acceptance** | Provider stub that sleeps past the timeout → typed **504**, no state change. Provider raises → request 500s, **and a fresh session still finds the `FAILED` `llm_calls` row**. Two concurrent run-ai calls → one 200, one typed **409**. `prod + fixture + no SHIELD_DEMO` → **refuses to boot**; `prod + fixture + SHIELD_DEMO=1` → boots. 10 rapid logins from one IP → **429 + Retry-After**. Multi-sheet, `$`-formatted xlsx extracts with costs intact and reports the sheet used. Playwright: client submits → admin replies → client clicks **from My Assessments** into the thread and reads it (no `page.goto`). Playwright: fresh admin session picks a client **via the UI switcher** and reaches Risk Register. |

### Sprint 2 — Solid Operations — STEP 2 ✅ PASS (8 more fixes; A-6, C-3–C-8, E-3, F-1, F-2, C-8)

**Executed 2026-07-09.** Closes everything in Sprint 2 except E-4 and H-6.

**Subagents.** Two Opus agents on disjoint files (extraction/storage vs. routes/concurrency), plus the lead writing A-6 and repairing one cross-fix regression.

**Tests (quiescent tree).**

| Command                                    | Result                                                                                                 |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------ |
| `python -m pytest`                         | **581 passed, 8 skipped, 0 failed**, EXIT=0 (baseline 520 → +61 tests)                                 |
| `ruff check app tests` / `black --check`   | clean, 181 files                                                                                       |
| `bandit -c pyproject.toml -r app`          | **High: 0**; Mediums dropped 2 → 1                                                                     |
| `alembic upgrade → downgrade -1 → upgrade` | `0031` reversible                                                                                      |
| Unique constraint, verified functionally   | SQLite DDL shows `UNIQUE (client_id, version)`; real Postgres shows `uq_risk_registers_client_version` |
| `tsc --noEmit` (web)                       | zero errors                                                                                            |
| `prettier --check` (changed web files)     | clean                                                                                                  |
| Playwright smoke vs rebuilt stack          | **3 passed, EXIT=0**                                                                                   |

**A-6 — the contract tests, and proof they work.** This is the single highest-leverage fix in the plan: it is what would have caught the four live-mode breaks that shipped against a green suite. The tests import the shapes from `app/ai/schemas.py`, the field tuples from the routes (`_RUN_FIELDS`, `_DIM_FIELDS`), and the enum members from `app/risk/engine.py` — nothing is restated, so prompt and parser cannot drift without a test going red.

I proved they catch the _original_ defects by restoring each one:

- Restoring the pre-Sprint-1 CSF shape (`{"subcategories": [{"code": ...}]}`) →
  `AssertionError: route reads data['scores']`
- Restoring the risk prompt's display labels →
  `AssertionError: 'very_low' is a valid enum token but the prompt never offers it`

`test_every_registered_job_has_a_declared_shape` additionally prevents a _new_ job from shipping without a shape and drifting on day one.

**A cross-fix regression the tests could not see — caught and fixed by the lead.**

E-1 removed the pooled DB connection the synchronous AI call used to hold. E-3 then added a per-entity advisory lock held for the whole request, implemented as `db.get_bind().connect()` — which checks a connection out of **that very pool** and holds it across the provider call. On Postgres this silently reinstated the starvation E-1 had just removed.

It was invisible: the suite runs on SQLite, which takes the in-process-mutex branch and never opens a lock connection, so `test_pooled_connection_released_across_provider_call` stayed green while production would have held a connection per in-flight run. **A green test proving an invariant that does not hold in production is the exact pathology this remediation exists to eliminate.**

Fix: the lock now uses a dedicated `NullPool` engine (`app/db/locks.py::lock_engine`), so it opens a private connection and never competes with the pool serving every other endpoint. `tests/unit/test_e3_lock_pool.py` pins the invariant; reverting to `bind.connect()` yields:

```
AssertionError: run_lock called bind.connect() -- that borrows from the request pool
```

Credit where due: the subagent's _reasoning_ was excellent and unprompted. It independently rejected `pg_advisory_xact_lock` and `SELECT ... FOR UPDATE` because both are transaction-scoped and would be released by E-1's `db.rollback()`. The flaw was one line inside otherwise careful work.

**Findings that again contradict the remediation document.**

1. **E-3.** The document says to "copy the open-draft guard CSF already has." CSF has no such guard — `create_assessment` did `prior version + 1` unconditionally, exactly like ZT and ATT&CK. All three needed it **built**. (Third documented case where following the document verbatim would have produced a wrong edit.)
2. **C-3.** `tech_debt/parsers.py` really did have **zero** direct tests. `tests/unit/test_tech_debt_parsers.py` now exists.

**Fixes landed.** A-6 (contract tests); C-3 (multi-sheet + best-candidate selection, duplicate headers uniquified, overflow cells kept); C-4 (tolerant money/count parsing, unparseable values preserved into notes, `confidence_pct` clamped); C-5 (wrong-shape responses raise instead of minting an empty version); C-6 (magic-byte sniffing, `Content-Length` checked before buffering, in the API and the Next proxy); C-7 (`urllib` side-channel replaced with the backend's own `get()` plus timeouts; `StorageUnavailable` → typed 503 so an outage stops reading as permanent data loss); C-8 (evidence links routed through `require_artifact_in_tenant`); E-3 (advisory lock, unique constraint, open-draft guard in all three services); F-1 (CSF auto-seeds without setting `scored_at`, so B-3's export gate still holds — verified by test); F-2 (ATT&CK/ZT auto-create the draft on first Run AI).

**Pass/fail: PASS.**

**Remaining in Sprint 2: E-4 and H-6.**

**Follow-ups.**

1. `_MISSING_KEY_CODES` in `app/storage/s3.py` includes `NoSuchBucket`, which maps to a 410 ("your file is gone"). A missing _bucket_ is misconfiguration, not a lost object — arguably the same confusion C-7 exists to remove. One-line judgment call; flagged rather than changed under another agent's scope.
2. The sha256 upload-dedup half of C-8 remains unimplemented (it lives in `routes/artifacts.py`, which was owned by the other agent during Step 2).
3. Both subagents stalled on background waiters and were stopped by the lead after their work was complete; every claim was then verified directly rather than resumed.

### Sprint 3 — Complete Deliverables and Truth

|                |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Goal**       | Every export contains what the dashboard and spec promise; the risk register is governed; the docs stop lying.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| **Issues**     | B-4, B-5, B-7, D-4, E-6, F-3, G-1, H-1, H-3, H-4 (corrected), H-7, H-8. **B-6 explicitly skipped (already done).**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| **Files**      | `zt/exporters.py`, `csf/exporters.py`, `attack/exporters.py`, `risk/exporters.py`, `routes/risk.py`, `routes/csf.py`, `models/` (+`CsfActionItem`), Alembic (additive), `scripts/_common.py`, `docker-compose.yml`, `docs/architecture.md`, `README.md`, `BUILD_REPORT.md`, `CHANGELOG.md`, `DECISIONS.md`, `docs/runbooks/backup-restore.md` (new), backup/restore scripts (new), `apps/web` admin audit page                                                                                                                                                                                                                                                          |
| **Subagents**  | Backend (B-4, B-5, B-7, F-3, H-8) · Frontend (D-4, G-1, H-7) · Security (H-1, H-3) · Playwright QA · Validation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| **Risks**      | **F-3 is L-effort** and adds PATCH/lock/DELETE/approve to a module that has none — largest surface area; do it last, behind E-3's unique constraint. **H-4 is documentation-only** — zero behavior change; keep it in a separate commit so a doc revert never touches code. **H-3's restore drill** must not run against a developer's real volumes.                                                                                                                                                                                                                                                                                                                    |
| **Acceptance** | XLSX Gap Plan lists **all** gaps; PDF/DOCX titled "Top 20 of N". ZT Roadmap sheet row count == `build_roadmap` output; DOCX has an Answers section. Risk DOCX contains the 5×5 matrix; ATT&CK gaps sorted by defensible priority; every download routed through `deliverable_filename`. Risk: generate → edit → lock → regenerate (lock survives) → approve → export; export **before** approve is refused; the edit appears in the XLSX. `docker compose exec api python scripts/load_*.py` works as documented. Restore drill round-trips a seeded record in CI. `architecture.md` describes the multi-tenant, no-worker, synchronous-AI system that actually exists. |

---

## L — Live Validation

How each class of fix is proven. **Green fixture tests prove nothing about live mode** — that lesson is the origin of A-2, A-4, and A-6, and it governs this section.

### L.1 Playwright — bootstrapped, then used

**Access verification (gate, Sprint 0).** Before writing a single spec: `docker compose run --rm playwright npx playwright --version`. If that fails, `BLOCKED.md` is written and implementation stops.

**Runtime.** App started via `docker compose up -d db redis minio createbuckets api web`; tests execute **inside the compose network** against `http://web:3000`. No host `pnpm` needed. Traces on first retry, screenshots on failure, video retained on failure — all written to `e2e/test-results/` and cited in Evidence.

**Flows validated, mapped to fixes:**

| Spec                           | Proves                                                                          |
| ------------------------------ | ------------------------------------------------------------------------------- |
| `smoke.spec.ts`                | Harness works (Sprint 0)                                                        |
| `extraction-errors.spec.ts`    | C-1 (empty CSV → error pill, no data), C-2 (.xls → typed message)               |
| `playbook-export-gate.spec.ts` | B-3 (seed → export blocked; score + approve → allowed)                          |
| `client-thread.spec.ts`        | D-1 (submit → admin reply → client clicks **from My Assessments** and reads it) |
| `admin-switcher.spec.ts`       | D-2 (fresh session, pick client **via UI**, reach Risk Register)                |
| `fixture-badge.spec.ts`        | E-5 (every AI suggestion carries a "simulated" badge)                           |
| `risk-governance.spec.ts`      | F-3 (edit → lock → regenerate → approve → export)                               |

Note: the existing suite's habit of `page.goto` with API-resolved ids is exactly why D-1 and D-2 were invisible. **New specs click the real path.**

### L.2 Unit tests (pytest)

- **Contract tests (A-6) — the highest-leverage addition.** One schema constant per job in `ai/schemas.py`; the prompt text references it, the route validates against it, and a test generates a schema-conformant response and asserts the route applies it. Deliberately breaking a prompt shape locally **must** fail CI.
- **Export-content tests (B-1…B-7).** Open the generated XLSX/DOCX and compare numbers to engine output **for non-default targets**. Today the suite asserts only HTTP 200 + content-type on downloads — precisely why the target-mismatch defects survived a green suite.
- **Parser tests (C-3).** `parsers.py` has **zero** direct tests. Add multi-sheet, duplicate-header, ragged-row, BOM, 500-row-cap, sentinel-row, and binary-garbage cases.
- **Concurrency tests (E-3).** Two threads, one 409.
- **Audit-durability test (E-2).** Provider raises → request 500s → **a fresh session** finds the `FAILED` row. The current test commits by hand in the same session and therefore proves nothing; it will be rewritten.

### L.3 Live AI validation — the three layers

1. **Structural (no key).** Contract tests prove prompt/parser agreement. This is the layer that would have caught all four live-mode breaks.
2. **Behavioral (no key).** Playwright drives Run AI in fixture mode; asserts a real HTTP round trip mutated visible data and wrote an `llm_calls` row.
3. **Live (key required).** `tests/live/test_live_smoke.py`, `@pytest.mark.skipif(not (SHIELD_LIVE_SMOKE and ANTHROPIC_API_KEY))`. Smallest real call per job; asserts a parseable response and a committed `llm_calls` row, including on a forced failure.

**Layer 3 will report SKIPPED until a key is provided.** I will state that plainly in Evidence rather than implying live coverage.

### L.4 Lint / typecheck / build

Per sprint, matching CI exactly: `ruff` + `black --check` + `bandit` (api); `prettier --check` + `eslint` + `tsc --noEmit` + `next build` (web); `gitleaks`. Web checks run **inside the container** (no host `pnpm`).

### L.5 Manual review (lead)

Reserved for judgment the tests cannot make: prompt wording (A-2, A-4), the resolved-target line printed into deliverables (B-1/B-2), banner and badge copy (E-5), and every documentation claim in H-1/H-4 — read against the code, not against the previous doc.

---

## E — Evidence

Populated per sprint, after the lead reviews the diff. **Nothing is recorded here as passing that was not observed passing.**

### Baseline (recorded 2026-07-09, before any change)

| Item                    | Result                                                |
| ----------------------- | ----------------------------------------------------- |
| `pytest` (`apps/api`)   | **480 passed**, 567.51s                               |
| Web tests               | none exist                                            |
| Playwright              | **not installed**                                     |
| `docker compose config` | valid                                                 |
| Docker daemon           | running (29.5.3)                                      |
| `ANTHROPIC_API_KEY`     | **not set**                                           |
| Working tree            | clean, `main` @ `474729d`, in sync with `origin/main` |

### Sprint 0 — Validation Harness ✅ PASS

**Executed 2026-07-09. Lead reviewed the diff before recording this.**

**What changed.** Playwright bootstrapped from zero and proven against the composed stack. A test-only `playwright` service added to `docker-compose.yml` under a `test` profile. An e2e CI job added (non-blocking for one sprint). An env-gated live-smoke test added, skipped by default. **No file under `apps/api/app/` or `apps/web/src/` was touched.**

**Subagent.** None — the lead executed this directly. The bootstrap is a small, delicate infra change on the critical path, and the tasking's own rule ("do not let subagents make broad, unrelated changes") argued against delegating a change that spans compose, CI, and pytest config.

**Files changed (6 added, 2 modified):**

| File                                     | Change                                                                                                         |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `e2e/playwright.config.ts`               | new — env-switchable `baseURL`, trace-on-first-retry, screenshot+video on failure                              |
| `e2e/package.json`                       | new — `@playwright/test` pinned **exactly** `1.61.1`                                                           |
| `e2e/package-lock.json`                  | new — reproducible install                                                                                     |
| `e2e/specs/smoke.spec.ts`                | new — 3 tests                                                                                                  |
| `apps/api/tests/live/__init__.py`        | new                                                                                                            |
| `apps/api/tests/live/test_live_smoke.py` | new — env-gated, 8 tests                                                                                       |
| `docker-compose.yml`                     | modified — **added** `playwright` service + `playwright-node-modules` volume. **No existing service touched.** |
| `.github/workflows/ci.yml`               | modified — **added** `e2e` job. No existing job touched.                                                       |

**Tests run.**

| Command                                                                                                     | Result                                                                                                                      |
| ----------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `python -m pytest` (`apps/api`)                                                                             | **480 passed, 8 skipped, 0 failed, 0 errors** — baseline of 480 exactly preserved; the 8 skips are the new gated live tests |
| `python -m pytest tests/live` (no key)                                                                      | 8 skipped, with an explicit reason string                                                                                   |
| `SHIELD_LIVE_SMOKE=1 ANTHROPIC_API_KEY=<dummy> pytest …::test_every_registered_job_has_a_prompt_and_parser` | **5 passed** — proves the gate _arms_, and that all five jobs are registered with a prompt and parser                       |
| `docker compose config --quiet`                                                                             | valid                                                                                                                       |
| `docker compose config --services`                                                                          | `playwright` **absent** — `docker compose up` behaviour unchanged                                                           |
| `docker compose --profile test config --services`                                                           | `playwright` present                                                                                                        |

**Playwright validation.**

| Run                  | Command                                             | Result                                                   |
| -------------------- | --------------------------------------------------- | -------------------------------------------------------- |
| Positive             | `docker compose --profile test run --rm playwright` | **3 passed (4.7s), EXIT=0**                              |
| **Negative control** | same, with `PLAYWRIGHT_BASE_URL=http://web:9999`    | **3 failed, EXIT=1** — harness provably detects breakage |

The negative control matters: a suite that has never failed is not evidence of anything. Pointing it at a dead port produced 3 failures, a non-zero exit, and full artifacts.

**Artifacts.** `e2e/test-results/` produced 19 files on the host via the bind mount, including `trace.zip`, `test-failed-1.png`, and `video.webm`. Confirmed excluded from git by `.gitignore:51` (`git check-ignore -v` verified). CI uploads them via `actions/upload-artifact`.

**Environment facts established.**

- Playwright image: `mcr.microsoft.com/playwright:v1.61.1-noble`, 2.8 GB, bundles Playwright **1.61.1** and `chromium-1129`.
- App under test: Next.js 14.2.15, ready in 2.4 s; `GET /health` → `{"status":"ok","version":"0.1.0"}`.
- `seed_demo.py` resolves its root via `parents[1]` and therefore **works inside the container** — only `_common.py` uses the broken `parents[3]`. FIX E-6 is real but does **not** block the CI e2e seed step.

**Pass/fail: PASS.**

**Remaining issues / follow-ups.**

1. **Version pin is a coupled pair.** The image tag and `@playwright/test` must move together or the run dies with `Executable doesn't exist at /ms-playwright/chromium-1129`. My first draft used a floating `^1.49.1` against an image shipping 1.61.1 — caught before commit, comment added in `docker-compose.yml`. **Do not "helpfully" widen that constraint.**
2. **CI will pay a ~2.8 GB image pull per run.** Non-blocking today. Before flipping `continue-on-error: false` at the Sprint 1 exit, cache the image layer (or move to a slimmer variant).
3. **`.gitignore` already carried a Playwright section** before this sprint (`test-results/`, `playwright-report/`, `playwright/.cache/`). Someone scaffolded the intent; the harness never landed. Nothing to add.
4. **Live smoke remains SKIPPED** — no `ANTHROPIC_API_KEY` present. Per the recorded decision, it is scaffolded and arms automatically the moment a key is supplied. **No live Claude call has been made, and none is claimed.**

### Sprint 1 — Trustworthy Core ✅ PASS

**Executed 2026-07-09. Lead independently verified every subagent claim before recording it here.**

**Subagents used (Opus, narrow scope, disjoint file ownership).**

| Subagent                         | Fixes                               | Owned files                                                                          |
| -------------------------------- | ----------------------------------- | ------------------------------------------------------------------------------------ |
| AI core (backend)                | A-1, A-3 plumbing, A-2/A-4 prompts  | `app/ai/*`, `config.py`, compose + `.env.example` defaults                           |
| ZT (backend)                     | B-1                                 | `routes/zt.py`                                                                       |
| Risk (backend)                   | A-4 route half                      | `routes/risk.py`                                                                     |
| ATT&CK (backend)                 | A-3 chunking, G-2                   | `routes/attack.py`, `schemas/attack.py`                                              |
| CSF (backend)                    | A-2 payload, A-3 chunking, B-2, B-3 | `routes/csf.py`, `models/csf_profile.py`, `csf/playbook_export.py`, migration `0029` |
| Extraction (security + frontend) | C-1 (re-scoped), C-2                | `tech_debt/*`, `routes/artifacts.py`, 3 web accept lists                             |

**Tests run (quiescent tree — the only run whose result is meaningful).**

| Command                                                  | Result                                                             |
| -------------------------------------------------------- | ------------------------------------------------------------------ |
| `python -m pytest` (`apps/api`)                          | **497 passed, 8 skipped, 0 failed, 0 errors**, EXIT=0              |
| Delta vs baseline                                        | +17 new regression tests; **zero existing tests broken**           |
| `ruff check app tests`                                   | All checks passed                                                  |
| `black --check app tests`                                | Clean, 170 files                                                   |
| `alembic upgrade head` → `downgrade -1` → `upgrade head` | All succeed; `scored_at` added nullable, removed cleanly, re-added |
| Migration in real Postgres container                     | `alembic_version = 0029`, `scored_at` present                      |
| Prettier on every file Sprint 0+1 touched                | Clean                                                              |

**Playwright validation.** Stack rebuilt with all Sprint 1 changes; API healthy; `docker compose --profile test run --rm playwright` → **3 passed (8.2s), EXIT=0**. This proves migration `0029` applies at container boot and the new A-1 boot guard does not block fixture-mode startup.

**The 17 new regression tests, each pinning a specific fix:**

- B-1 `test_finalized_gap_count_matches_dashboard_not_default_target` — opens the real XLSX Gap Plan sheet
- B-2 `test_finalize_honors_client_target_tier`
- B-3 `test_playbook_export_blocked_until_scored_and_approved`
- A-2 `test_csf_run_ai_payload_is_grounded`
- A-3 `test_csf_run_ai_chunks_by_tier_exactly_once`, `test_csf_run_ai_one_bad_chunk_applies_nothing`, `test_run_ai_chunks_cover_every_code_exactly_once_and_merges`, `test_run_ai_bad_batch_aborts_and_applies_nothing`
- A-4 `test_display_cased_enums_coerce_and_derive_tier`, `test_hyphenated_case_also_normalizes`, `test_unknown_token_returns_none_and_warns`, `test_canonical_lowercase_tokens_still_work`
- G-2 `test_run_ai_draft_only_list_yields_empty_tools_and_warns`, `test_run_ai_approved_v2_excludes_v1_ghost_items`
- C-1 `test_extract_header_only_csv_422_no_llm_call_no_list`, `test_extract_caps_rows_at_500_and_reports_truncation`
- C-2 `test_upload_rejects_legacy_xls_with_actionable_message`, `test_extract_corrupt_xlsx_422_not_500`

**Every regression test was proven to fail against the un-fixed code.** Each subagent reverted its own fix, watched the test go red, and restored it. Sample evidence: B-1 → `assert 0 == 37` (finalized deliverable claimed zero gaps while the dashboard showed 37); A-4 → `assert None == 'very_low'` (`"Very Low"` coerced to `None`, nulling the tier); C-2 → `.xls` upload returned `201` with `mime_type: application/vnd.ms-excel` instead of `415`. A test that passes against the broken code is a false guarantee, and this repo already contained one (`test_llm_client.py` commits by hand to "prove" durability production does not have).

**Corrections the subagents made to _my_ instructions — recorded because they matter:**

1. I told the AI-core agent the likelihood scale was `very_low, low, moderate, high, very_high`. The real enum is `MEDIUM = "medium"`. It read `risk/engine.py`, used the true value, and flagged the discrepancy. Obeying me would have shipped A-4's fix with `moderate` in the prompt and `medium` in the enum — the identical silent-null bug, freshly reintroduced.
2. The ATT&CK agent could not surface G-2's warning without adding a field to `app/schemas/attack.py` (FastAPI's `response_model` strips unknown fields). It made the minimal addition and flagged the scope expansion rather than hiding it.
3. The extraction agent independently confirmed the remediation document is wrong about C-1: no `app/ai/fixtures.py`, no fabrication logic anywhere.

**Lead-applied corrections:**

- The extraction agent had no host `pnpm` and left `TechDebtWorkspace.tsx` and `Dropzone.tsx` unformatted. This would have broken the Web CI job. I formatted both and re-verified.
- I reviewed the ZT agent's scope expansion (dashboard `target_stage` default `3` → `None`). Accepted: precedence is now explicit query param → client's `ServiceRequest.zt_target_stage` → `DEFAULT_TARGET_STAGE`, and both dashboard and finalize call one helper (`_resolve_gap_targets`), so they cannot drift again. B-1 existed _because_ two call sites computed the same value independently.

**Pass/fail: PASS.**

**Remaining issues / follow-ups.**

1. **`main` is already prettier-dirty.** CI's `format:check` (prettier 3.9.4, pinned in `pnpm-lock.yaml`) flags **17 pre-existing files** at HEAD, in files no agent touched. The Web CI job is therefore already failing on `main`, independent of this work. Folded into H-4 (Sprint 3, docs/CI truth pass). I did not silently widen scope to fix it.
2. **`effective_max_tokens = max_tokens or 128000`** in `AnthropicProvider.complete`. Both Haiku jobs pin `32000`, so this is safe today. But a future job pinned to Haiku _without_ an explicit cap would request 128000 and take a 400 from Haiku's 64K ceiling — the X-1 coupling, one careless edit from returning. Consider a provider-level assertion in Sprint 2.
3. **The CSF subagent died mid-run** on an API/SSL error, after implementing all four of its fixes but before validating them. I verified each layer myself (`scored_at` nullable; 409 on unscored; 409 on unapproved; `documents_stale` cleared only after the gate; `"Unscored"` rendered; `target_tier` threaded into `analyze_gaps`; chunk-by-tier with apply-after-all-parse; `_ground()` sending answers, notes and evidence flags) rather than resuming it blindly.
4. **Live AI still unproven against the real API.** No `ANTHROPIC_API_KEY`. Contract tests (A-6) land in Sprint 2 and are the actual guard against the A-2/A-4 class of defect; the gated live smoke test remains armed and skipping.
5. **e2e specs for the new flows** (playbook export gate, extraction errors) are Sprint 2 per the plan. Sprint 1's proof is unit-level plus the smoke suite confirming the stack still boots and serves.

### Sprint 2 — Solid Operations — STEP 1 ✅ PASS (9 of 19 fixes)

**Executed 2026-07-09.** Sprint 2 is being landed in steps because five fixes all want `ai/llm.py`, `ai/engine.py` and `db/session.py` at once. Step 1 covers E-1, E-2, A-5, E-5, H-5, G-3, H-2, D-1, D-2, D-3.

**Subagents (Opus, disjoint ownership).**

| Subagent   | Fixes                              | Owned files                                                                                                                |
| ---------- | ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| AI runtime | E-1, E-2, A-5, E-5 backend, H-5    | `ai/llm.py`, `ai/engine.py`, `db/session.py`, `models/llm_call.py`, `routes/admin.py`, run-ai call sites, migration `0030` |
| Web        | D-1, D-2, D-3, E-5 web, E-1 client | `apps/web/src/**` only                                                                                                     |
| Security   | G-3, H-2                           | `config.py`, `main.py`, `middleware/ratelimit.py`, `routes/auth.py`, `.env.example`                                        |

**Tests (quiescent tree).**

| Command                                    | Result                                                                 |
| ------------------------------------------ | ---------------------------------------------------------------------- |
| `python -m pytest`                         | **520 passed, 8 skipped, 0 failed**, EXIT=0 (baseline 497 → +23 tests) |
| `ruff check app tests`                     | clean                                                                  |
| `black --check app tests`                  | clean, 175 files                                                       |
| `bandit -c pyproject.toml -r app`          | **High: 0** (Medium: 2, both pre-existing, untouched files)            |
| `alembic upgrade → downgrade -1 → upgrade` | `client_id` added nullable + indexed, removed cleanly, re-added        |
| Migration in real Postgres container       | `alembic_version = 0030`, `llm_calls.client_id` present                |
| `tsc --noEmit` (web, in container)         | **zero errors** across 26 changed files                                |
| `prettier --check` (all changed files)     | clean                                                                  |
| Playwright smoke vs rebuilt stack          | **3 passed, EXIT=0**                                                   |

**The E-2 proof, and why it took two attempts.** The pre-existing test (`test_invoke_records_failure_with_error_message`) called `db.commit()` itself and read the row back in the _same_ session — a commit no production path performs after a failure. It passed while proving nothing. The rewritten test commits nothing and reads from a fresh session.

To prove the new test is honest I reverted the fix — and my first revert was **wrong**: I swapped `open_autonomous_session(...)` for `db` but left the new `commit()` calls, so the request session committed, the row persisted, and the test _passed_. That looked like evidence the test was vacuous. Only a faithful revert (request session **and** `flush()`-only, no commit) produced the true failure:

```
sqlalchemy.exc.NoResultFound: No row was found when one was required
FAILED tests/unit/test_llm_client.py::test_invoke_records_failure_with_error_message
```

Reverting the line that _looks like_ the fix is not the same as reverting the fix.

**Design decisions worth keeping.**

- `open_autonomous_session(bind=...)` reuses the module `SessionLocal` and binds to the **caller's** engine. The test suite overrides `get_db` with per-test engines, so a naively autonomous session would have written the audit row into a different database and the E-2 test would have passed for the wrong reason. `expire_on_commit=False` keeps the detached row's columns readable so `JobResult.llm_call` still works for every caller.
- E-1's connection release is _observed_, not asserted by proxy: `test_pooled_connection_released_across_provider_call` probes `engine.pool.checkedout()` from **inside** the provider call and requires 0.
- `db.rollback()` (to return the connection) also **expires every ORM object the route holds**. The agent captured the needed ids before the rollback. Missing this would have produced intermittent failures under load, not a clean test failure.
- The `mode` field defaults to `"fixture"`. That fails **safe**: a route that forgets to set it badges its output as simulated rather than passing fixture data off as real analysis.
- H-2's limiter is applied by path-matching middleware in `main.py`, so per-user AI limits reached the five run-ai endpoints **without editing a single route file** another agent owned. It adds no dependency (`redis>=5.1` was already declared and unused), **fails open** if Redis is down, and is **off by default** so the suite stays inert without touching `conftest.py`.
- G-3 proof: reverting the guard yields `Failed: DID NOT RAISE RuntimeError` — production boots in fixture mode today.

**Pass/fail: PASS for Step 1.**

**Remaining in Sprint 2 after Step 1 (10 fixes):** A-6, C-3–C-8, E-3, E-4, F-1, F-2, H-6. Step 2 (below) closed all but E-4 and H-6.

**Follow-ups / new findings.**

1. **`main`'s Web CI job is already failing on two steps**, independent of this work: `prettier --check` flags 17 pre-existing files at HEAD (prettier 3.9.4, the exact version pinned in `pnpm-lock.yaml`), and `next lint` crashes with "Converting circular structure to JSON" on a pristine checkout. Verified by stashing all work and re-running. Folded into H-4 (Sprint 3). Not silently fixed here.
2. **Two self-inflicted environment errors, recorded for honesty.** (a) I patched `llm.py` while a background `pytest` was mid-run, contaminating it; killed, verified no `TEMP` markers survived, re-ran clean. (b) I ran `pnpm install` inside the web container to obtain `tsc`, which resolved a second Next variant into the pnpm store and broke the dev server with `Cannot find module '../lib/picocolors'`. Rebuilt the `node-modules-root`/`node-modules-web` volumes from the lockfile. Neither touched a line of the deliverable.
3. The AI-runtime subagent stalled for ~20 minutes on a full-suite run and was stopped; its work was already complete, and I validated every claim directly rather than resuming it.

### Sprint 3 — Complete Deliverables and Truth

> _Not started._

---

## Assumptions and decisions recorded (rule 9)

1. **Repo identity.** The plan names `SHIELD062626`; file trees are structurally identical and `package.json` still reads `shield062626`. **Proceeding against `SHIELD070826`.**
2. **`claude-opus-4-7` is valid.** A-5's model-id sub-fix is void; the typed-error sub-fix proceeds.
3. **A-3 gates the Haiku split** (X-1). Chunking lands first; per-job `max_tokens ≤ 64000` for Haiku jobs.
4. **B-6 is skipped** — already implemented. Rewriting it would be a pure regression risk.
5. **C-1 is re-scoped** — the fabrication fixture does not exist. Only the sentinel-row and zero-row-guard defects are fixed.
6. **E-3 builds the open-draft guard in all three routes**; CSF has none to copy.
7. **H-4's CHANGELOG claim is corrected** — no duplicate `[3.0.0]`; the real defect is three `[Unreleased]` variants.
8. **Playwright runs in Docker**, per the user's direction, against `http://web:3000`.
9. **Live AI validation is scaffolded but skipped** pending `ANTHROPIC_API_KEY`. No key will be invented or requested from a third party.
10. **No secrets, `.env` files, production config, or deployment config will be modified.** The `docker-compose.yml` change in Sprint 0 adds a **test-only service** and touches no existing service definition.

---

_Plan complete. No application code has been modified. Awaiting go-ahead to execute Sprint 0._
