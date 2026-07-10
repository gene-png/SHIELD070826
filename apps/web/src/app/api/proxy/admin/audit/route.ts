/**
 * GET /api/proxy/admin/audit - read the append-only audit trail (admin only,
 * FIX H-7). Cross-tenant by design; forwards the supported filter, paging, and
 * export query params. JSON by default; `format=csv` streams the CSV export
 * straight through (apiFetch only parses JSON, so CSV bypasses it).
 */

import { getServerSession } from "next-auth";
import { NextResponse } from "next/server";

import { ApiError, apiFetch } from "@/lib/api";
import { authOptions } from "@/lib/auth/options";

const API_BASE_URL = process.env.API_BASE_URL ?? "http://api:8000";

const FORWARDED_PARAMS = [
  "action",
  "actor_id",
  "target_type",
  "target_id",
  "client_id",
  "start",
  "end",
  "limit",
  "offset",
  "format",
] as const;

function forwardedQuery(request: Request): string {
  const src = new URL(request.url).searchParams;
  const out = new URLSearchParams();
  for (const key of FORWARDED_PARAMS) {
    const value = src.get(key);
    if (value !== null && value !== "") {
      out.set(key, value);
    }
  }
  const qs = out.toString();
  return qs ? `?${qs}` : "";
}

export async function GET(request: Request): Promise<NextResponse | Response> {
  const session = await getServerSession(authOptions);
  const bearer = session?.accessToken;
  if (!bearer) {
    return NextResponse.json(
      { error: { code: 401, message: "Not signed in." } },
      { status: 401 },
    );
  }

  const query = forwardedQuery(request);
  const isCsv = new URL(request.url).searchParams.get("format") === "csv";

  if (isCsv) {
    // Stream the CSV body through unchanged; apiFetch would try to JSON-parse
    // it. The active-client cookie is irrelevant here (cross-tenant endpoint).
    const upstream = await fetch(`${API_BASE_URL}/admin/audit${query}`, {
      headers: { Authorization: `Bearer ${bearer}` },
      cache: "no-store",
    });
    if (!upstream.ok) {
      return NextResponse.json(
        { error: { code: upstream.status, message: "Audit export failed." } },
        { status: upstream.status },
      );
    }
    return new Response(await upstream.text(), {
      status: 200,
      headers: {
        "Content-Type": "text/csv",
        "Content-Disposition": 'attachment; filename="audit-log.csv"',
      },
    });
  }

  try {
    const result = await apiFetch<unknown>(`/admin/audit${query}`, {
      bearer,
      clientId: "",
    });
    return NextResponse.json(result);
  } catch (err) {
    if (err instanceof ApiError) {
      return NextResponse.json(err.payload ?? { error: { code: err.status } }, {
        status: err.status,
      });
    }
    return NextResponse.json(
      { error: { message: "Upstream admin/audit call failed." } },
      { status: 502 },
    );
  }
}
