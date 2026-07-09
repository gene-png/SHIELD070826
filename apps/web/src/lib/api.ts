/**
 * Server-side fetch helper for the FastAPI backend.
 *
 * Always runs on the server (don't import from a "use client" file). The
 * client never talks directly to the API - calls flow through this module
 * inside Server Components / Server Actions / route handlers. That keeps
 * the API host name and the Bearer token off the wire to the browser.
 *
 * Multi-tenant: if the request has a `shield_active_client_id` cookie set
 * by the client switcher, we forward it as `X-Client-Id` so the backend
 * scopes queries to that tenant for admin/reviewer users.
 */

import { cookies } from "next/headers";

const BASE_URL = process.env.API_BASE_URL ?? "http://api:8000";

export const ACTIVE_CLIENT_COOKIE = "shield_active_client_id";

/**
 * Default upstream timeout. Bounds every call so a hung backend surfaces as a
 * clean 504 instead of leaving the UI stuck on "Running" forever (and never
 * lets a late response commit after the user already saw an error). Long AI
 * runs can raise this per-call via `timeoutMs`.
 */
const DEFAULT_TIMEOUT_MS = 120_000;

type Json = Record<string, unknown> | unknown[];

export interface ApiOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: Json;
  bearer?: string;
  headers?: Record<string, string>;
  cache?: RequestCache;
  /** Override or suppress the cookie-derived X-Client-Id. Pass empty string to suppress. */
  clientId?: string;
  /** Client-side timeout in ms. Defaults to DEFAULT_TIMEOUT_MS; 0 disables it. */
  timeoutMs?: number;
  /** Caller-supplied abort signal, chained with the timeout above. */
  signal?: AbortSignal;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly correlationId: string | undefined,
    public readonly payload: unknown,
  ) {
    super(`API ${status}`);
  }
}

export async function apiFetch<T>(
  path: string,
  opts: ApiOptions = {},
): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(opts.headers ?? {}),
  };
  if (opts.bearer) {
    headers.Authorization = `Bearer ${opts.bearer}`;
  }
  // Forward the active client id for tenant scoping. Explicit clientId
  // option wins; falling back to the cookie set by the client switcher.
  let activeClient: string | undefined;
  if (opts.clientId !== undefined) {
    activeClient = opts.clientId || undefined;
  } else {
    try {
      activeClient = cookies().get(ACTIVE_CLIENT_COOKIE)?.value;
    } catch {
      // cookies() throws if called outside a request scope - safe to ignore.
      activeClient = undefined;
    }
  }
  if (activeClient) {
    headers["X-Client-Id"] = activeClient;
  }
  let body: BodyInit | undefined;
  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  // Bound the call with an abort timeout, chained with any caller signal so
  // either source cancels the request.
  const controller = new AbortController();
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const timer =
    timeoutMs > 0 ? setTimeout(() => controller.abort(), timeoutMs) : null;
  if (opts.signal) {
    if (opts.signal.aborted) {
      controller.abort();
    } else {
      opts.signal.addEventListener("abort", () => controller.abort(), {
        once: true,
      });
    }
  }
  let res: Response;
  try {
    res = await fetch(url, {
      method: opts.method ?? "GET",
      headers,
      body,
      cache: opts.cache ?? "no-store",
      signal: controller.signal,
    });
  } catch (err) {
    if (controller.signal.aborted) {
      throw new ApiError(504, undefined, {
        error: { message: "The request timed out. Please try again." },
      });
    }
    throw err;
  } finally {
    if (timer) clearTimeout(timer);
  }
  const correlationId = res.headers.get("X-Request-ID") ?? undefined;
  if (!res.ok) {
    let payload: unknown = undefined;
    try {
      payload = await res.json();
    } catch {
      payload = await res.text();
    }
    throw new ApiError(res.status, correlationId, payload);
  }
  // 204 No Content: don't try to JSON-parse.
  if (res.status === 204) {
    return undefined as unknown as T;
  }
  return (await res.json()) as T;
}
