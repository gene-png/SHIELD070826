# SHIELD end-to-end tests (Playwright)

Click-path specs that prove a **user can reach** each fixed behaviour, run
inside the compose network against `http://web:3000`.

## Running

```bash
# 1. Bring the stack up. NEXTAUTH_URL MUST match the hostname the Playwright
#    browser uses (http://web:3000 inside the compose network). The compose
#    default is http://localhost:3000, which is correct for a human on the host
#    but breaks NextAuth for the in-network browser (see "Gotchas" below).
NEXTAUTH_URL=http://web:3000 SHIELD_LLM_MODE=fixture \
  docker compose up -d db redis minio createbuckets api web

# 2. Seed the demo tenant (admin@kentro.example / client@atlas.example, DemoPass!2026).
docker compose exec -T api python scripts/seed_demo.py

# 3. Run the suite. --no-deps stops `run` from recreating `web` back to the
#    compose default NEXTAUTH_URL.
docker compose --profile test run --rm --no-deps playwright
```

## Credentials

Specs read credentials from env vars, defaulting to the documented `seed_demo`
accounts so a freshly-seeded stack needs no configuration:

| var                                        | default                                  | used by                                       |
| ------------------------------------------ | ---------------------------------------- | --------------------------------------------- |
| `E2E_ADMIN_EMAIL` / `E2E_ADMIN_PASSWORD`   | `admin@kentro.example` / `DemoPass!2026` | all admin specs                               |
| `E2E_CLIENT_EMAIL` / `E2E_CLIENT_PASSWORD` | `client@atlas.example` / `DemoPass!2026` | `client-thread`                               |
| `E2E_CLIENT_LEGAL_NAME`                    | `Atlas Defense Solutions`                | tenant the admin opens fresh workspaces under |

Override them (`docker compose run -e E2E_ADMIN_EMAIL=… playwright …`) to run
against a stack seeded with different accounts.

## Gotchas (read before you lose an hour)

- **`NEXTAUTH_URL` must equal the browser's origin.** The browser reaches the
  app as `http://web:3000`; NextAuth v4 validates the request host against
  `NEXTAUTH_URL` for the credentials callback and CSRF. With the compose default
  (`http://localhost:3000`) the sign-in POST is rejected and no session cookie is
  ever set. Bring `web` up with `NEXTAUTH_URL=http://web:3000`. Do **not** change
  the compose default — that would break sign-in for a human at `localhost:3000`.
  Trade-off: while the stack is up this way, host-browser sign-in at
  `localhost:3000` will not work.

- **Sign-in is performed through NextAuth's real credential flow, not by
  clicking the button** (`helpers/auth.ts`). The web tier runs `next dev` (React
  StrictMode), which double-fetches `/api/auth/csrf`; the form's client-side
  `signIn()` then races that fetch and posts a csrf token that no longer matches
  the cookie, so the button is intermittently rejected. `helpers/auth.ts` fetches
  csrf and posts the callback atomically — the same endpoints, the same
  email+password, verified by the API — which is deterministic. It does **not**
  fabricate a session or tenant cookie. Everything each spec actually proves
  (reaching workspaces, the client thread, the admin switcher) is still driven by
  clicking.

- **Product gaps surfaced by these specs** (not worked around — reported): fixture
  mode registers no per-purpose AI fixtures in the running app
  (`apps/api/app/ai/llm.py::_build_provider`), so any Run-AI/extract 500s with
  `KeyError: No fixture registered`. That makes the Run-AI "Simulated" badge
  unreachable; `simulated-badge.spec.ts` therefore proves the AI status banner
  (the reachable half of E-5) and documents the badge gap inline.
