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

import {
  AttackProxyError,
  finalizeAttackDeliverable,
} from "@/lib/attack/client";
import type {
  AttackAssessmentStatus,
  AttackDeliverable,
} from "@/lib/attack/types";

export interface AttackDeliverableCardProps {
  serviceId: string;
  assessmentStatus: AttackAssessmentStatus | null;
  deliverable: AttackDeliverable | null;
  onChange: (next: AttackDeliverable) => void;
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
  if (err instanceof AttackProxyError) {
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

export function AttackDeliverableCard({
  serviceId,
  assessmentStatus,
  deliverable,
  onChange,
}: AttackDeliverableCardProps): JSX.Element {
  const [busy, setBusy] = React.useState<"finalize" | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const canFinalize =
    assessmentStatus === "approved" || assessmentStatus === "released";

  async function onFinalize(): Promise<void> {
    setBusy("finalize");
    setError(null);
    try {
      const next = await finalizeAttackDeliverable(serviceId);
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
        <CardTitle>Deliverable</CardTitle>
        <CardDescription>
          Render the PDF + XLSX from the approved coverage assessment. The PDF
          includes the per-tactic coverage table + top-50 gap list; the XLSX
          carries the full ~600-row coverage matrix.
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
          <ul className="space-y-1 text-sm">
            {deliverable.pdf_artifact_id ? (
              <li>
                <a
                  href={`/api/proxy/artifacts/${deliverable.pdf_artifact_id}/download`}
                  className="text-brand-500 underline hover:text-brand-600"
                >
                  {deliverable.pdf_filename ?? "Download PDF"}
                </a>
              </li>
            ) : null}
            {deliverable.xlsx_artifact_id ? (
              <li>
                <a
                  href={`/api/proxy/artifacts/${deliverable.xlsx_artifact_id}/download`}
                  className="text-brand-500 underline hover:text-brand-600"
                >
                  {deliverable.xlsx_filename ?? "Download XLSX"}
                </a>
              </li>
            ) : null}
          </ul>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void onFinalize()}
            disabled={!canFinalize || busy !== null}
            className="rounded-md bg-brand-500 px-4 py-2 text-sm font-semibold text-ink-on-accent hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busy === "finalize"
              ? "Finalizing…"
              : deliverable
                ? "Re-finalize"
                : "Finalize"}
          </button>
          {!canFinalize && !deliverable ? (
            <span className="text-xs text-ink-tertiary">
              Approve the assessment to enable finalize.
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
