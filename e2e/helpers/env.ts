/**
 * Credentials + endpoints for the click-path specs.
 *
 * Defaults are the documented `seed_demo.py` accounts, so a freshly-seeded
 * stack (what CI produces: `docker compose ... up` + `python scripts/seed_demo.py`)
 * works with zero configuration. Every value is overridable by env var so the
 * same specs run against a differently-provisioned stack without edits:
 *
 *   E2E_ADMIN_EMAIL / E2E_ADMIN_PASSWORD      — a platform admin
 *   E2E_CLIENT_EMAIL / E2E_CLIENT_PASSWORD    — a client-role user (for D-1)
 *   E2E_CLIENT_LEGAL_NAME                     — the tenant the admin opens
 *                                               fresh workspaces under (B-3/C/E)
 *
 * The API is reached at PLAYWRIGHT_API_URL (http://api:8000 inside the compose
 * network) purely for TEST SETUP — creating the empty service a spec then
 * drives entirely by clicking. No spec sets the tenant cookie via the API and
 * no spec navigates to a deep workspace URL by id; those two shortcuts are
 * exactly what hid D-1/D-2, so they are banned here.
 */

export const API_URL = process.env.PLAYWRIGHT_API_URL ?? "http://api:8000";

export const ADMIN_EMAIL =
  process.env.E2E_ADMIN_EMAIL ?? "admin@kentro.example";
export const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "DemoPass!2026";

export const CLIENT_EMAIL =
  process.env.E2E_CLIENT_EMAIL ?? "client@atlas.example";
export const CLIENT_PASSWORD =
  process.env.E2E_CLIENT_PASSWORD ?? "DemoPass!2026";

/** Legal-name substring of the tenant the admin opens fresh workspaces under. */
export const CLIENT_LEGAL_NAME =
  process.env.E2E_CLIENT_LEGAL_NAME ?? "Atlas Defense Solutions";

/** Escape a string for safe use inside a `new RegExp(...)`. */
export function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** A run-unique token so re-running a spec never collides with prior state. */
export function unique(prefix: string): string {
  return `${prefix} ${Date.now()}-${Math.floor(Math.random() * 1e4)}`;
}
