"use client";

import type {
  GapAnalysis,
  ZtAnswer,
  ZtAnswerPatch,
  ZtAssessment,
  ZtCatalog,
  ZtDeliverable,
  ZtFramework,
  ZtRunAiResponse,
  ZtScoreSummary,
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
    throw new ZtProxyError(res.status, payload);
  }
  if (res.status === 204) {
    return undefined as unknown as T;
  }
  return (await res.json()) as T;
}

export class ZtProxyError extends Error {
  constructor(
    public readonly status: number,
    public readonly payload: unknown,
  ) {
    super(`ZT proxy ${status}`);
  }
}

export async function fetchCatalog(framework: ZtFramework): Promise<ZtCatalog> {
  return jsonRequest<ZtCatalog>(`/api/proxy/zt/catalog?framework=${framework}`);
}

export async function fetchLatestAssessment(
  serviceId: string,
): Promise<ZtAssessment | null> {
  try {
    return await jsonRequest<ZtAssessment>(
      `/api/proxy/zt/services/${serviceId}/assessments/latest`,
    );
  } catch (err) {
    if (err instanceof ZtProxyError && err.status === 404) {
      return null;
    }
    throw err;
  }
}

export async function createAssessment(
  serviceId: string,
): Promise<ZtAssessment> {
  return jsonRequest<ZtAssessment>(
    `/api/proxy/zt/services/${serviceId}/assessments`,
    { method: "POST" },
  );
}

export async function patchAnswer(
  answerId: string,
  patch: ZtAnswerPatch,
): Promise<ZtAnswer> {
  return jsonRequest<ZtAnswer>(`/api/proxy/zt/answers/${answerId}`, {
    method: "PATCH",
    body: patch,
  });
}

// --- Client self-assessment (the client fills + submits their own draft) ---

export async function fetchSelfAssessment(
  serviceId: string,
): Promise<ZtAssessment | null> {
  try {
    return await jsonRequest<ZtAssessment>(
      `/api/proxy/zt/services/${serviceId}/self-assessment`,
    );
  } catch (err) {
    if (err instanceof ZtProxyError && err.status === 404) {
      return null;
    }
    throw err;
  }
}

export async function patchSelfAssessmentAnswer(
  answerId: string,
  patch: ZtAnswerPatch,
): Promise<ZtAnswer> {
  return jsonRequest<ZtAnswer>(
    `/api/proxy/zt/self-assessment/answers/${answerId}`,
    { method: "PATCH", body: patch },
  );
}

export async function submitSelfAssessment(
  serviceId: string,
  body: { target_stage?: number },
): Promise<ZtAssessment> {
  return jsonRequest<ZtAssessment>(
    `/api/proxy/zt/services/${serviceId}/self-assessment/submit`,
    { method: "POST", body },
  );
}

export async function runZtAi(
  serviceId: string,
  signal?: AbortSignal,
): Promise<ZtRunAiResponse> {
  return jsonRequest<ZtRunAiResponse>(
    `/api/proxy/zt/services/${serviceId}/run-ai`,
    { method: "POST", signal },
  );
}

export async function approveAssessment(
  assessmentId: string,
): Promise<ZtAssessment> {
  return jsonRequest<ZtAssessment>(
    `/api/proxy/zt/assessments/${assessmentId}/approve`,
    { method: "POST" },
  );
}

export async function fetchScore(
  serviceId: string,
): Promise<ZtScoreSummary | null> {
  try {
    return await jsonRequest<ZtScoreSummary>(
      `/api/proxy/zt/services/${serviceId}/score`,
    );
  } catch (err) {
    if (err instanceof ZtProxyError && err.status === 404) {
      return null;
    }
    throw err;
  }
}

export async function fetchGapAnalysis(
  serviceId: string,
  opts: { targetStage?: number; topN?: number } = {},
): Promise<GapAnalysis | null> {
  const params = new URLSearchParams();
  if (opts.targetStage !== undefined) {
    params.set("target_stage", String(opts.targetStage));
  }
  if (opts.topN !== undefined) {
    params.set("top_n", String(opts.topN));
  }
  const qs = params.toString();
  const url = `/api/proxy/zt/services/${serviceId}/gap-analysis${qs ? `?${qs}` : ""}`;
  try {
    return await jsonRequest<GapAnalysis>(url);
  } catch (err) {
    if (err instanceof ZtProxyError && err.status === 404) {
      return null;
    }
    throw err;
  }
}

export async function fetchLatestDeliverable(
  serviceId: string,
): Promise<ZtDeliverable | null> {
  try {
    return await jsonRequest<ZtDeliverable>(
      `/api/proxy/zt/services/${serviceId}/deliverables/latest`,
    );
  } catch (err) {
    if (err instanceof ZtProxyError && err.status === 404) {
      return null;
    }
    throw err;
  }
}

export async function finalizeZtDeliverable(
  serviceId: string,
): Promise<ZtDeliverable> {
  return jsonRequest<ZtDeliverable>(
    `/api/proxy/zt/services/${serviceId}/deliverables/finalize`,
    { method: "POST" },
  );
}

export async function releaseZtDeliverable(
  deliverableId: string,
): Promise<ZtDeliverable> {
  return jsonRequest<ZtDeliverable>(
    `/api/proxy/zt/deliverables/${deliverableId}/release`,
    { method: "POST" },
  );
}
