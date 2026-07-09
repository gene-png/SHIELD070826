"use client";

import type {
  AssessmentCreateRequest,
  AssessmentResponse,
  IntakePatchRequest,
  IntakeStateResponse,
  IntakeSubmitRequest,
} from "./types";

/**
 * Client-side wrappers that call the same-origin proxy routes. The proxy
 * (apps/web/src/app/api/proxy/intake/...) attaches the user's bearer
 * token server-side, keeping the API host name and the access token off
 * the wire to the browser.
 */

/**
 * Turn a proxy error payload into a human-readable message. Prefers the
 * backend's typed detail (FastAPI `detail`, or a `{ error: { message } }`
 * envelope) and falls back to a friendly generic per status. Mirrors
 * describeMessagesError in ../messages/client.ts.
 */
function proxyErrorMessage(status: number, payload: unknown): string {
  const typed = payload as
    { error?: { message?: string }; detail?: string } | null | undefined;
  const detail = typed?.error?.message ?? typed?.detail;
  if (typeof detail === "string" && detail.trim().length > 0) {
    return detail;
  }
  if (status === 401 || status === 403) {
    return "You're not signed in, or your session has expired. Sign in and try again.";
  }
  if (status === 404) {
    return "We couldn't find that — it may have been removed.";
  }
  if (status === 504) {
    return "The request timed out. Please try again.";
  }
  if (status >= 500) {
    return "Something went wrong on our end. Please try again in a moment.";
  }
  return `Request failed (${status}).`;
}

class ProxyError extends Error {
  constructor(
    public readonly status: number,
    public readonly payload: unknown,
  ) {
    super(proxyErrorMessage(status, payload));
  }
}

export async function fetchIntake(): Promise<IntakeStateResponse> {
  const res = await fetch("/api/proxy/intake", { cache: "no-store" });
  if (!res.ok) {
    throw new ProxyError(res.status, await safeJson(res));
  }
  return (await res.json()) as IntakeStateResponse;
}

export async function patchIntake(
  body: IntakePatchRequest,
): Promise<IntakeStateResponse> {
  const res = await fetch("/api/proxy/intake", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ProxyError(res.status, await safeJson(res));
  }
  return (await res.json()) as IntakeStateResponse;
}

export async function submitIntake(
  body: IntakeSubmitRequest,
): Promise<IntakeStateResponse> {
  const res = await fetch("/api/proxy/intake/submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ProxyError(res.status, await safeJson(res));
  }
  return (await res.json()) as IntakeStateResponse;
}

export async function fetchAssessments(): Promise<AssessmentResponse[]> {
  const res = await fetch("/api/proxy/intake/assessments", {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new ProxyError(res.status, await safeJson(res));
  }
  return (await res.json()) as AssessmentResponse[];
}

export async function createAssessment(
  body: AssessmentCreateRequest,
): Promise<AssessmentResponse> {
  const res = await fetch("/api/proxy/intake/assessments", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ProxyError(res.status, await safeJson(res));
  }
  return (await res.json()) as AssessmentResponse;
}

async function safeJson(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    return await res.text();
  }
}

/**
 * True when an error is the backend's "finish your intake first" rejection
 * (422 raised because the organization profile is still pending). Lets the UI
 * offer a direct link to /intake instead of a bare error string.
 */
export function isIncompleteIntakeError(err: unknown): boolean {
  return (
    err instanceof ProxyError &&
    err.status === 422 &&
    /intake/i.test(err.message)
  );
}

export { ProxyError };
