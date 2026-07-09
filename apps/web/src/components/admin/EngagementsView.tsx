"use client";

import * as React from "react";

import {
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  EmptyState,
  StatusPill,
} from "@shield/design-system";

import { archiveService, listServices } from "@/lib/admin/client";
import type { AdminServiceRow } from "@/lib/admin/types";
import { SERVICE_LABELS } from "@/lib/intake/types";

function statusTone(
  status: string,
): "info" | "success" | "warning" | "neutral" {
  if (status === "released") return "success";
  if (status === "archived") return "neutral";
  if (status === "review") return "warning";
  return "info";
}

export function EngagementsView(): JSX.Element {
  const [services, setServices] = React.useState<AdminServiceRow[] | null>(
    null,
  );
  const [showArchived, setShowArchived] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [busyId, setBusyId] = React.useState<string | null>(null);

  const reload = React.useCallback(async () => {
    try {
      setServices(await listServices(showArchived));
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load engagements.",
      );
    }
  }, [showArchived]);

  React.useEffect(() => {
    void reload();
  }, [reload]);

  async function onArchive(s: AdminServiceRow): Promise<void> {
    if (
      !window.confirm(
        `Archive "${s.title}"? It will be removed from active lists. ` +
          `Its data is retained.`,
      )
    ) {
      return;
    }
    setBusyId(s.id);
    setError(null);
    try {
      await archiveService(s.id);
      await reload();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to archive engagement.",
      );
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <label className="flex items-center gap-2 text-sm text-ink-secondary">
        <input
          type="checkbox"
          checked={showArchived}
          onChange={(e) => setShowArchived(e.target.checked)}
        />
        Show archived
      </label>

      {error ? (
        <p className="text-sm text-status-danger-fg" role="alert">
          {error}
        </p>
      ) : null}

      {services === null ? (
        <p className="text-sm text-ink-tertiary">Loading engagements…</p>
      ) : services.length === 0 ? (
        <EmptyState
          title="No engagements"
          description="Engagements appear here once a service request is published or a client starts a self-assessment."
        />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Engagements ({services.length})</CardTitle>
          </CardHeader>
          <CardBody className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-border-subtle text-xs uppercase tracking-wider text-ink-tertiary">
                  <th className="py-2 pr-4 font-medium">Engagement</th>
                  <th className="py-2 pr-4 font-medium">Type</th>
                  <th className="py-2 pr-4 font-medium">Status</th>
                  <th className="py-2 pr-4 font-medium">Created</th>
                  <th className="py-2 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {services.map((s) => (
                  <tr
                    key={s.id}
                    className="border-b border-border-subtle last:border-b-0"
                  >
                    <td className="py-2 pr-4 font-medium text-ink-primary">
                      {s.title}
                    </td>
                    <td className="py-2 pr-4 text-ink-secondary">
                      {SERVICE_LABELS[s.kind] ?? s.kind}
                    </td>
                    <td className="py-2 pr-4">
                      <StatusPill tone={statusTone(s.status)} withDot>
                        {s.status}
                      </StatusPill>
                    </td>
                    <td className="py-2 pr-4 text-ink-secondary">
                      {new Date(s.created_at).toLocaleDateString()}
                    </td>
                    <td className="py-2">
                      {s.status === "archived" ? (
                        <span className="text-xs text-ink-tertiary">
                          Archived
                        </span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => void onArchive(s)}
                          disabled={busyId === s.id}
                          className="rounded-md border border-status-danger-border px-3 py-1 text-sm font-medium text-status-danger-fg hover:bg-status-danger-bg disabled:opacity-60"
                        >
                          Archive
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardBody>
        </Card>
      )}
    </div>
  );
}
