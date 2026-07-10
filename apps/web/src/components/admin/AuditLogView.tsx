"use client";

import * as React from "react";

import {
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  EmptyState,
} from "@shield/design-system";

import { auditCsvHref, fetchAuditLog } from "@/lib/admin/client";
import type { AdminAuditRow, AuditLogQuery } from "@/lib/admin/types";

const PAGE_SIZE = 50;

interface Filters {
  action: string;
  actor_id: string;
  target_type: string;
  client_id: string;
  start: string;
  end: string;
}

const EMPTY_FILTERS: Filters = {
  action: "",
  actor_id: "",
  target_type: "",
  client_id: "",
  start: "",
  end: "",
};

/** Build the wire query from the applied filters + current page offset. */
function toQuery(filters: Filters, offset: number): AuditLogQuery {
  return {
    action: filters.action.trim() || undefined,
    actor_id: filters.actor_id.trim() || undefined,
    target_type: filters.target_type.trim() || undefined,
    client_id: filters.client_id.trim() || undefined,
    start: filters.start ? new Date(filters.start).toISOString() : undefined,
    end: filters.end ? new Date(filters.end).toISOString() : undefined,
    limit: PAGE_SIZE,
    offset,
  };
}

/**
 * Read-only viewer for the append-only audit trail (FIX H-7). The platform
 * writes a thorough audit trail that previously had no reader without SQL
 * access; this surfaces it with filters, paging, and CSV export. It only ever
 * reads - there is no write path from this screen.
 */
export function AuditLogView(): JSX.Element {
  // `applied` drives the fetch; `draft` holds the in-progress form so typing
  // doesn't refetch on every keystroke.
  const [draft, setDraft] = React.useState<Filters>(EMPTY_FILTERS);
  const [applied, setApplied] = React.useState<Filters>(EMPTY_FILTERS);
  const [offset, setOffset] = React.useState(0);
  const [rows, setRows] = React.useState<AdminAuditRow[] | null>(null);
  const [total, setTotal] = React.useState(0);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let active = true;
    setRows(null);
    setError(null);
    fetchAuditLog(toQuery(applied, offset))
      .then((res) => {
        if (!active) return;
        setRows(res.rows);
        setTotal(res.total);
      })
      .catch((err) => {
        if (active) {
          setError(
            err instanceof Error
              ? err.message
              : "Failed to load the audit log.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, [applied, offset]);

  function onApply(e: React.FormEvent<HTMLFormElement>): void {
    e.preventDefault();
    setOffset(0);
    setApplied(draft);
  }

  function onReset(): void {
    setDraft(EMPTY_FILTERS);
    setApplied(EMPTY_FILTERS);
    setOffset(0);
  }

  function field(key: keyof Filters, value: string): void {
    setDraft((prev) => ({ ...prev, [key]: value }));
  }

  const pageStart = total === 0 ? 0 : offset + 1;
  const pageEnd = Math.min(offset + PAGE_SIZE, total);
  const hasPrev = offset > 0;
  const hasNext = offset + PAGE_SIZE < total;

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Filters</CardTitle>
        </CardHeader>
        <CardBody>
          <form
            onSubmit={onApply}
            className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
          >
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-ink-primary">Action</span>
              <input
                type="text"
                value={draft.action}
                onChange={(e) => field("action", e.target.value)}
                placeholder="e.g. user.created"
                className="rounded-md border border-border bg-surface-card px-3 py-2 text-ink-primary focus:border-brand-500 focus:outline-none"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-ink-primary">Actor ID</span>
              <input
                type="text"
                value={draft.actor_id}
                onChange={(e) => field("actor_id", e.target.value)}
                placeholder="user UUID"
                className="rounded-md border border-border bg-surface-card px-3 py-2 text-ink-primary focus:border-brand-500 focus:outline-none"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-ink-primary">Target type</span>
              <input
                type="text"
                value={draft.target_type}
                onChange={(e) => field("target_type", e.target.value)}
                placeholder="e.g. service"
                className="rounded-md border border-border bg-surface-card px-3 py-2 text-ink-primary focus:border-brand-500 focus:outline-none"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-ink-primary">Client ID</span>
              <input
                type="text"
                value={draft.client_id}
                onChange={(e) => field("client_id", e.target.value)}
                placeholder="client UUID"
                className="rounded-md border border-border bg-surface-card px-3 py-2 text-ink-primary focus:border-brand-500 focus:outline-none"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-ink-primary">From</span>
              <input
                type="datetime-local"
                value={draft.start}
                onChange={(e) => field("start", e.target.value)}
                className="rounded-md border border-border bg-surface-card px-3 py-2 text-ink-primary focus:border-brand-500 focus:outline-none"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-ink-primary">To</span>
              <input
                type="datetime-local"
                value={draft.end}
                onChange={(e) => field("end", e.target.value)}
                className="rounded-md border border-border bg-surface-card px-3 py-2 text-ink-primary focus:border-brand-500 focus:outline-none"
              />
            </label>
            <div className="flex items-end gap-2 sm:col-span-2 lg:col-span-3">
              <button
                type="submit"
                className="rounded-md bg-brand-500 px-4 py-2 text-sm font-semibold text-ink-on-accent hover:bg-brand-600"
              >
                Apply filters
              </button>
              <button
                type="button"
                onClick={onReset}
                className="rounded-md border border-border px-4 py-2 text-sm font-medium text-ink-secondary hover:text-ink-primary"
              >
                Reset
              </button>
              <a
                href={auditCsvHref(toQuery(applied, 0))}
                className="ml-auto rounded-md border border-border px-4 py-2 text-sm font-medium text-ink-secondary hover:text-ink-primary"
              >
                Download CSV
              </a>
            </div>
          </form>
        </CardBody>
      </Card>

      {error ? (
        <p className="text-sm text-status-danger-fg" role="alert">
          {error}
        </p>
      ) : rows === null ? (
        <p className="text-sm text-ink-tertiary">Loading audit trail…</p>
      ) : rows.length === 0 ? (
        <EmptyState
          title="No audit entries"
          description="No audit rows match these filters."
        />
      ) : (
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <CardTitle>Audit trail</CardTitle>
              <span className="text-xs text-ink-tertiary">
                {pageStart}–{pageEnd} of {total}
              </span>
            </div>
          </CardHeader>
          <CardBody className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-border-subtle text-xs uppercase tracking-wider text-ink-tertiary">
                  <th className="py-2 pr-4 font-medium">When</th>
                  <th className="py-2 pr-4 font-medium">Action</th>
                  <th className="py-2 pr-4 font-medium">Actor</th>
                  <th className="py-2 pr-4 font-medium">Target</th>
                  <th className="py-2 font-medium">Details</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.id}
                    className="border-b border-border-subtle align-top last:border-b-0"
                  >
                    <td className="py-2 pr-4 text-ink-secondary">
                      {new Date(r.at).toLocaleString()}
                    </td>
                    <td className="py-2 pr-4 font-medium text-ink-primary">
                      {r.action}
                    </td>
                    <td className="py-2 pr-4 text-ink-secondary">
                      {r.actor_email ?? r.actor_user_id ?? "system"}
                    </td>
                    <td className="py-2 pr-4 text-ink-secondary">
                      {r.target_type}
                      {r.target_id ? (
                        <span className="block text-xs text-ink-tertiary">
                          {r.target_id}
                        </span>
                      ) : null}
                    </td>
                    <td className="py-2 text-xs text-ink-tertiary">
                      {r.details ? (
                        <code className="break-all">
                          {JSON.stringify(r.details)}
                        </code>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardBody>
        </Card>
      )}

      {rows !== null && rows.length > 0 ? (
        <div className="flex items-center justify-between">
          <button
            type="button"
            onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            disabled={!hasPrev}
            className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-ink-secondary hover:text-ink-primary disabled:cursor-not-allowed disabled:opacity-50"
          >
            ← Previous
          </button>
          <button
            type="button"
            onClick={() => setOffset((o) => o + PAGE_SIZE)}
            disabled={!hasNext}
            className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-ink-secondary hover:text-ink-primary disabled:cursor-not-allowed disabled:opacity-50"
          >
            Next →
          </button>
        </div>
      ) : null}
    </div>
  );
}
