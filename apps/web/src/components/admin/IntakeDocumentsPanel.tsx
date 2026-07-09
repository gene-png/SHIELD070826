"use client";

import * as React from "react";

import {
  Card,
  CardBody,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
  StatusPill,
} from "@shield/design-system";

import { type ArtifactSummary, listArtifacts } from "@/lib/intake/artifacts";

const SPREADSHEET_MIME = new Set([
  "text/csv",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
]);

/** Tech-debt extraction only accepts a tabular inventory (CSV/XLSX). */
function isInventory(a: ArtifactSummary): boolean {
  return SPREADSHEET_MIME.has(a.mime_type) || /\.(csv|xlsx)$/i.test(a.title);
}

export interface IntakeDocumentsPanelProps {
  /** Run AI extraction directly on a client-uploaded inventory. */
  onExtract: (artifactId: string) => void;
  extracting: boolean;
  /** Bumping this re-fetches the list (e.g. after a workspace upload). */
  reloadKey?: number;
}

/**
 * Surfaces the documents the client uploaded (during intake or otherwise)
 * inside the workspace, so the consultant can review them and extract a
 * capability list straight from an inventory without re-uploading. Relies on
 * the active tenant being aligned to this service's client (EnsureActiveClient)
 * so the artifacts list is scoped correctly.
 */
export function IntakeDocumentsPanel({
  onExtract,
  extracting,
  reloadKey = 0,
}: IntakeDocumentsPanelProps): JSX.Element {
  const [docs, setDocs] = React.useState<ArtifactSummary[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    setError(null);
    listArtifacts()
      .then((res) => {
        if (cancelled) return;
        setDocs(res.items.filter((a) => a.origin === "client_upload"));
      })
      .catch((err) => {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Couldn't load documents.",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Client documents</CardTitle>
        <CardDescription>
          Files uploaded for this assessment. Download to review, or extract a
          capability list directly from a CSV/XLSX inventory — no re-upload
          needed.
        </CardDescription>
      </CardHeader>
      <CardBody>
        {error ? (
          <p className="text-sm text-status-danger-fg" role="alert">
            {error}
          </p>
        ) : docs === null ? (
          <p className="text-sm text-ink-tertiary">Loading documents…</p>
        ) : docs.length === 0 ? (
          <EmptyState
            title="No client documents"
            description="The client didn't upload any files. Upload an inventory above instead."
          />
        ) : (
          <ul className="flex flex-col gap-1.5">
            {docs.map((a) => (
              <li
                key={a.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border-subtle bg-surface-card px-3 py-2 text-sm"
              >
                <div className="flex min-w-0 flex-col">
                  <span
                    className="truncate font-medium text-ink-primary"
                    title={a.title}
                  >
                    {a.title}
                  </span>
                  <span className="text-xs text-ink-tertiary">
                    {(a.size_bytes / 1024).toFixed(1)} KB ·{" "}
                    {new Date(a.uploaded_at).toLocaleDateString()}
                  </span>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <a
                    href={`/api/proxy/artifacts/${a.id}/download`}
                    className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-ink-primary hover:bg-surface-sunken"
                  >
                    Download
                  </a>
                  {isInventory(a) ? (
                    <button
                      type="button"
                      onClick={() => onExtract(a.id)}
                      disabled={extracting}
                      className="rounded-md bg-brand-500 px-3 py-1.5 text-sm font-semibold text-ink-on-accent hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {extracting ? "Extracting…" : "Extract from this"}
                    </button>
                  ) : (
                    <StatusPill tone="neutral">Not an inventory</StatusPill>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}
