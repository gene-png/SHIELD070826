"use client";

import type {
  AttackAssessment,
  AttackCatalog,
  AttackCoveragePatch,
  AttackCoverageRow,
  AttackDeliverable,
  AttackHeatmap,
  AttackRunAiResponse,
} from "./types";

interface JsonRequestInit {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
}

async function jsonRequest<T>(
  url: string,
  init: JsonRequestInit = {},
): Promise<T> {
  const { body, method = "GET", signal } = init;
  const res = await fetch(url, {
    method,
    cache: "no-store",
    headers: {
      Accept: "application/json",
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
  });
  if (!res.ok) {
    let payload: unknown;
    try {
      payload = await res.json();
    } catch {
      payload = await res.text();
    }
    throw new AttackProxyError(res.status, payload);
  }
  if (res.status === 204) {
    return undefined as unknown as T;
  }
  return (await res.json()) as T;
}

export class AttackProxyError extends Error {
  constructor(
    public readonly status: number,
    public readonly payload: unknown,
  ) {
    super(`ATT&CK proxy ${status}`);
  }
}

export async function fetchCatalog(): Promise<AttackCatalog> {
  return jsonRequest<AttackCatalog>("/api/proxy/attack/catalog");
}

export async function fetchLatestAssessment(
  serviceId: string,
): Promise<AttackAssessment | null> {
  try {
    return await jsonRequest<AttackAssessment>(
      `/api/proxy/attack/services/${serviceId}/assessments/latest`,
    );
  } catch (err) {
    if (err instanceof AttackProxyError && err.status === 404) {
      return null;
    }
    throw err;
  }
}

export async function createAssessment(
  serviceId: string,
): Promise<AttackAssessment> {
  return jsonRequest<AttackAssessment>(
    `/api/proxy/attack/services/${serviceId}/assessments`,
    { method: "POST" },
  );
}

export async function patchCoverage(
  coverageId: string,
  patch: AttackCoveragePatch,
): Promise<AttackCoverageRow> {
  return jsonRequest<AttackCoverageRow>(
    `/api/proxy/attack/coverage/${coverageId}`,
    { method: "PATCH", body: patch },
  );
}

export async function approveAssessment(
  assessmentId: string,
): Promise<AttackAssessment> {
  return jsonRequest<AttackAssessment>(
    `/api/proxy/attack/assessments/${assessmentId}/approve`,
    { method: "POST" },
  );
}

export async function runAttackAi(
  serviceId: string,
  signal?: AbortSignal,
): Promise<AttackRunAiResponse> {
  return jsonRequest<AttackRunAiResponse>(
    `/api/proxy/attack/services/${serviceId}/run-ai`,
    { method: "POST", signal },
  );
}

export async function fetchHeatmap(
  serviceId: string,
): Promise<AttackHeatmap | null> {
  try {
    return await jsonRequest<AttackHeatmap>(
      `/api/proxy/attack/services/${serviceId}/heatmap`,
    );
  } catch (err) {
    if (err instanceof AttackProxyError && err.status === 404) {
      return null;
    }
    throw err;
  }
}

export async function fetchLatestDeliverable(
  serviceId: string,
): Promise<AttackDeliverable | null> {
  try {
    return await jsonRequest<AttackDeliverable>(
      `/api/proxy/attack/services/${serviceId}/deliverables/latest`,
    );
  } catch (err) {
    if (err instanceof AttackProxyError && err.status === 404) {
      return null;
    }
    throw err;
  }
}

export async function finalizeAttackDeliverable(
  serviceId: string,
): Promise<AttackDeliverable> {
  return jsonRequest<AttackDeliverable>(
    `/api/proxy/attack/services/${serviceId}/deliverables/finalize`,
    { method: "POST" },
  );
}

export async function releaseAttackDeliverable(
  deliverableId: string,
): Promise<AttackDeliverable> {
  return jsonRequest<AttackDeliverable>(
    `/api/proxy/attack/deliverables/${deliverableId}/release`,
    { method: "POST" },
  );
}
