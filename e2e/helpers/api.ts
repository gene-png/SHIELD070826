/**
 * Minimal API helpers for TEST SETUP ONLY.
 *
 * These create the *empty* fixtures a spec then drives by clicking (e.g. a
 * fresh, editable CSF or Tech-Debt service). They never touch the tenant
 * cookie and never hand a workspace id to `page.goto` — the spec reaches the
 * workspace through the app's own navigation (Active Work → "Open"), which is
 * the behaviour the click-path suite exists to prove.
 *
 * Auth uses the API's own `/auth/login` (the same endpoint NextAuth calls),
 * so no privileged backdoor is introduced.
 */
import { type APIRequestContext } from "@playwright/test";

import { API_URL } from "./env";

export async function apiLogin(
  request: APIRequestContext,
  email: string,
  password: string,
): Promise<string> {
  const res = await request.post(`${API_URL}/auth/login`, {
    data: { email, password },
  });
  if (!res.ok()) {
    throw new Error(
      `POST /auth/login failed for ${email}: ${res.status()} ${await res.text()}`,
    );
  }
  return (await res.json()).access_token as string;
}

interface ClientSummary {
  id: string;
  legal_name: string;
}

/** Resolve a tenant id by a substring of its legal name (admin token). */
export async function resolveClientId(
  request: APIRequestContext,
  token: string,
  legalNameSubstr: string,
): Promise<string> {
  const res = await request.get(`${API_URL}/admin/clients`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok()) {
    throw new Error(
      `GET /admin/clients failed: ${res.status()} ${await res.text()}`,
    );
  }
  const body = (await res.json()) as { clients: ClientSummary[] };
  const match = body.clients.find((c) =>
    c.legal_name.includes(legalNameSubstr),
  );
  if (!match) {
    throw new Error(
      `no client whose legal_name contains ${JSON.stringify(legalNameSubstr)}; ` +
        `have: ${body.clients.map((c) => c.legal_name).join(", ")}`,
    );
  }
  return match.id;
}

async function createService(
  request: APIRequestContext,
  token: string,
  clientId: string,
  path: string,
  kind: string,
  title: string,
): Promise<string> {
  const res = await request.post(`${API_URL}${path}`, {
    headers: { Authorization: `Bearer ${token}`, "X-Client-Id": clientId },
    data: { kind, title },
  });
  if (!res.ok()) {
    throw new Error(`POST ${path} failed: ${res.status()} ${await res.text()}`);
  }
  return (await res.json()).id as string;
}

/** Open a fresh, empty (editable) NIST CSF service for a tenant. */
export function createCsfService(
  request: APIRequestContext,
  token: string,
  clientId: string,
  title: string,
): Promise<string> {
  return createService(
    request,
    token,
    clientId,
    "/csf/services",
    "nist_csf",
    title,
  );
}

/** Open a fresh, empty (editable) Tech Debt service for a tenant. */
export function createTechDebtService(
  request: APIRequestContext,
  token: string,
  clientId: string,
  title: string,
): Promise<string> {
  return createService(
    request,
    token,
    clientId,
    "/tech-debt/services",
    "tech_debt",
    title,
  );
}
