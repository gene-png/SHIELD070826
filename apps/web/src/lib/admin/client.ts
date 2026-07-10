"use client";

import type {
  AdminAuditListResponse,
  AdminIntakeQueueResponse,
  AdminServiceRow,
  AdminUserDetail,
  AuditLogQuery,
  FulfillServiceRequestResponse,
} from "./types";

export async function fetchIntakeQueue(): Promise<AdminIntakeQueueResponse> {
  const res = await fetch("/api/proxy/admin/intake-queue", {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`Failed to load intake queue (${res.status}).`);
  }
  return (await res.json()) as AdminIntakeQueueResponse;
}

export async function fulfillServiceRequest(
  requestId: string,
): Promise<FulfillServiceRequestResponse> {
  const res = await fetch(
    `/api/proxy/admin/service-requests/${requestId}/fulfill`,
    { method: "POST" },
  );
  if (!res.ok) {
    let detail = `Failed to publish (${res.status}).`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return (await res.json()) as FulfillServiceRequestResponse;
}

// --- Client + domain management (Work Order B2) -----------------------------

export interface ClientSummary {
  id: string;
  legal_name: string;
  dba_name: string | null;
  industry: string | null;
  size_band: string | null;
  intake_completed_at: string | null;
  created_at: string;
}

export interface DomainRow {
  id: string;
  client_id: string;
  domain: string;
  created_at: string;
}

async function _detail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: string };
    if (body?.detail) return body.detail;
  } catch {
    /* keep default */
  }
  return `Request failed (${res.status}).`;
}

export async function listClients(): Promise<ClientSummary[]> {
  const res = await fetch("/api/proxy/admin/clients", { cache: "no-store" });
  if (!res.ok) throw new Error(await _detail(res));
  return ((await res.json()) as { clients: ClientSummary[] }).clients;
}

export async function createClient(body: {
  legal_name: string;
  industry?: string;
}): Promise<ClientSummary> {
  const res = await fetch("/api/proxy/admin/clients", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await _detail(res));
  return (await res.json()) as ClientSummary;
}

export async function listDomains(cid: string): Promise<DomainRow[]> {
  const res = await fetch(`/api/proxy/admin/clients/${cid}/domains`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(await _detail(res));
  return ((await res.json()) as { domains: DomainRow[] }).domains;
}

export async function addDomain(
  cid: string,
  domain: string,
): Promise<DomainRow> {
  const res = await fetch(`/api/proxy/admin/clients/${cid}/domains`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ domain }),
  });
  if (!res.ok) throw new Error(await _detail(res));
  return (await res.json()) as DomainRow;
}

export async function removeDomain(cid: string, did: string): Promise<void> {
  const res = await fetch(`/api/proxy/admin/clients/${cid}/domains/${did}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await _detail(res));
}

// --- User account management ------------------------------------------------

export async function listUsers(): Promise<AdminUserDetail[]> {
  const res = await fetch("/api/proxy/admin/users", { cache: "no-store" });
  if (!res.ok) throw new Error(await _detail(res));
  return ((await res.json()) as { users: AdminUserDetail[] }).users;
}

export async function createUser(body: {
  email: string;
  password: string;
  display_name: string;
  role: "admin" | "client";
  client_id?: string | null;
}): Promise<AdminUserDetail> {
  const res = await fetch("/api/proxy/admin/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await _detail(res));
  return (await res.json()) as AdminUserDetail;
}

export async function deactivateUser(id: string): Promise<void> {
  const res = await fetch(`/api/proxy/admin/users/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await _detail(res));
}

export async function reactivateUser(id: string): Promise<AdminUserDetail> {
  const res = await fetch(`/api/proxy/admin/users/${id}/reactivate`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(await _detail(res));
  return (await res.json()) as AdminUserDetail;
}

// --- Services / engagements -------------------------------------------------

export async function listServices(
  includeArchived = false,
): Promise<AdminServiceRow[]> {
  const qs = includeArchived ? "?include_archived=true" : "";
  const res = await fetch(`/api/proxy/admin/services${qs}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(await _detail(res));
  return ((await res.json()) as { services: AdminServiceRow[] }).services;
}

export async function archiveService(id: string): Promise<void> {
  const res = await fetch(`/api/proxy/admin/services/${id}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await _detail(res));
}

// --- Audit log (FIX H-7) ----------------------------------------------------

/** Turn an AuditLogQuery into a query string, dropping empty/undefined fields. */
export function auditQueryString(query: AuditLogQuery): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export async function fetchAuditLog(
  query: AuditLogQuery,
): Promise<AdminAuditListResponse> {
  const res = await fetch(`/api/proxy/admin/audit${auditQueryString(query)}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(await _detail(res));
  return (await res.json()) as AdminAuditListResponse;
}

/** Href for the CSV export of the current filter set (paging is irrelevant). */
export function auditCsvHref(query: AuditLogQuery): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (key === "limit" || key === "offset") continue;
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  params.set("format", "csv");
  return `/api/proxy/admin/audit?${params.toString()}`;
}
