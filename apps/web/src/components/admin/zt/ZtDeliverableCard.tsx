"use client";

import * as React from "react";

import {
  Card,
  CardBody,
  CardDescription,
  CardHeader,
  CardTitle,
  StatusPill,
} from "@shield/design-system";

import { finalizeZtDeliverable, ZtProxyError } from "@/lib/zt/client";
import type { ZtAssessmentStatus, ZtDeliverable } from "@/lib/zt/types";

export interface ZtDeliverableCardProps {
  serviceId: string;
  assessmentStatus: ZtAssessmentStatus | null;
  deliverable: ZtDeliverable | null;
  onChange: (next: ZtDeliverable) => void;
}

function fmtTime(value: string | null): string {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function describeError(err: unknown): string {
  if (err instanceof ZtProxyError) {
    const payload = err.payload as
      { error?: { message?: string }; detail?: string } | undefined;
    return (
      payload?.error?.message ??
      payload?.detail ??
      `Request failed (${err.status}).`
    );
  }
  return err instanceof Error ? err.message : "Request failed.";
}

export function ZtDeliverableCard({
  serviceId,
  assessmentStatus,
  deliverable,
  onChange,
}: ZtDeliverableCardProps): JSX.Element {
  const [busy, setBusy] = React.useState<"finalize" | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const canFinalize =
    assessmentStatus === "approved" || assessmentStatus === "released";

  async function onFinalize(): Promise<void> {
    setBusy("finalize");
    setError(null);
    try {
      const next = await finalizeZtDeliverable(serviceId);
      onChange(next);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Evaluation &amp; report</CardTitle>
        <CardDescription>
          Once you&apos;ve reviewed and approved the inputs, send for evaluation
          to run the gap analysis and build an interactive <b>HTML dashboard</b>{" "}
          (opens in a new tab) plus downloadable <b>XLSX</b>, <b>DOCX</b>, and{" "}
          <b>PDF</b>. Reports are admin-only — share them outside the app.
          Re-running on the same day appends <code>_v2</code> to the filename.
        </CardDescription>
      </CardHeader>
      <CardBody className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-2">
          {deliverable ? (
            <>
              <StatusPill tone="info" withDot>
                {`Finalized v${deliverable.version}`}
              </StatusPill>
              <span className="text-xs text-ink-tertiary">
                Finalized {fmtTime(deliverable.finalized_at)}
              </span>
            </>
          ) : (
            <StatusPill tone="neutral" withDot>
              Not finalized yet
            </StatusPill>
          )}
        </div>

        {deliverable ? (
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-2">
              {deliverable.html_artifact_id ? (
                <a
                  href={`/api/proxy/artifacts/${deliverable.html_artifact_id}/view`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 rounded-md bg-brand-500 px-4 py-2 text-sm font-semibold text-ink-on-accent hover:bg-brand-600"
                >
                  <span aria-hidden>📊</span> Open HTML dashboard
                </a>
              ) : null}
              {deliverable.docx_artifact_id ? (
                <a
                  href={`/api/proxy/artifacts/${deliverable.docx_artifact_id}/download`}
                  className="inline-flex items-center gap-2 rounded-md border border-border px-4 py-2 text-sm font-semibold text-ink-primary hover:bg-surface-sunken"
                >
                  <span aria-hidden>📄</span> DOCX executive view
                </a>
              ) : null}
              {deliverable.xlsx_artifact_id ? (
                <a
                  href={`/api/proxy/artifacts/${deliverable.xlsx_artifact_id}/download`}
                  className="inline-flex items-center gap-2 rounded-md border border-border px-4 py-2 text-sm font-semibold text-ink-primary hover:bg-surface-sunken"
                >
                  <span aria-hidden>📊</span> XLSX analysis
                </a>
              ) : null}
            </div>
            {deliverable.pdf_artifact_id ? (
              <a
                href={`/api/proxy/artifacts/${deliverable.pdf_artifact_id}/download`}
                className="w-fit text-xs text-ink-tertiary underline hover:text-brand-600"
              >
                Also download PDF ({deliverable.pdf_filename ?? "report.pdf"})
              </a>
            ) : null}
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void onFinalize()}
            disabled={!canFinalize || busy !== null}
            className="rounded-md bg-brand-500 px-4 py-2 text-sm font-semibold text-ink-on-accent hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busy === "finalize"
              ? "Sending…"
              : deliverable
                ? "Re-run evaluation"
                : "Send for evaluation"}
          </button>
          {!canFinalize && !deliverable ? (
            <span className="text-xs text-ink-tertiary">
              Approve the client inputs to enable evaluation.
            </span>
          ) : null}
        </div>

        {error ? (
          <p className="text-sm text-status-danger-fg" role="alert">
            {error}
          </p>
        ) : null}
      </CardBody>
    </Card>
  );
}
