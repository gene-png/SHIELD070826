"use client";

import * as React from "react";
import Link from "next/link";

import {
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  StatusPill,
} from "@shield/design-system";

import { MessageThread } from "@/components/messages/MessageThread";
import { fetchAssessments } from "@/lib/intake/client";
import { SERVICE_LABELS, type AssessmentResponse } from "@/lib/intake/types";

const TIMELINE_STEPS = [
  "Started",
  "Under review",
  "Approved",
  "Report released",
] as const;

/** Furthest lifecycle step reached, from either status field. */
function reachedStep(assessment: string | null, serviceStatus: string): number {
  const s = assessment ?? serviceStatus;
  if (s === "released") return 3;
  if (s === "approved") return 2;
  if (s === "submitted" || s === "in_review") return 1;
  return 0;
}

function statusTone(
  assessment: string | null,
  serviceStatus: string,
): "info" | "warning" | "success" | "neutral" {
  const s = assessment ?? serviceStatus;
  if (s === "released" || s === "approved") return "success";
  if (s === "submitted") return "warning";
  if (s === "draft" || s === "in_progress") return "info";
  return "neutral";
}

export function AssessmentDetailView({
  serviceId,
}: {
  serviceId: string;
}): JSX.Element {
  const [assessment, setAssessment] = React.useState<AssessmentResponse | null>(
    null,
  );
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let active = true;
    fetchAssessments()
      .then((rows) => {
        if (!active) return;
        setAssessment(rows.find((r) => r.service_id === serviceId) ?? null);
      })
      .catch((err) => {
        if (active) {
          setError(
            err instanceof Error ? err.message : "Failed to load this service.",
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [serviceId]);

  if (loading) {
    return (
      <p className="text-sm text-ink-tertiary" aria-live="polite">
        Loading…
      </p>
    );
  }

  if (error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Couldn&apos;t load this service</CardTitle>
        </CardHeader>
        <CardBody className="flex flex-col items-start gap-3">
          <p className="text-sm text-status-danger-fg" role="alert">
            {error}
          </p>
          <Link
            href="/assessments"
            className="rounded-md bg-brand-500 px-4 py-2 text-sm font-semibold text-ink-on-accent hover:bg-brand-600"
          >
            Back to My Assessments
          </Link>
        </CardBody>
      </Card>
    );
  }

  if (!assessment) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>We couldn&apos;t find that assessment</CardTitle>
        </CardHeader>
        <CardBody className="flex flex-col items-start gap-3">
          <p className="text-sm text-ink-secondary">
            It may have been removed, or it belongs to a different account.
          </p>
          <Link
            href="/assessments"
            className="rounded-md bg-brand-500 px-4 py-2 text-sm font-semibold text-ink-on-accent hover:bg-brand-600"
          >
            Back to My Assessments
          </Link>
        </CardBody>
      </Card>
    );
  }

  const reached = reachedStep(assessment.assessment_status, assessment.status);
  const created = new Date(assessment.created_at);

  return (
    <div className="flex flex-col gap-6">
      <header className="space-y-1">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-500">
          Assessment
        </p>
        <h1 className="text-3xl font-semibold text-ink-primary">
          {assessment.title}
        </h1>
        <p className="text-sm text-ink-secondary">
          {SERVICE_LABELS[assessment.service_type]}
        </p>
      </header>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle>Status</CardTitle>
            <StatusPill
              tone={statusTone(assessment.assessment_status, assessment.status)}
              withDot
            >
              {TIMELINE_STEPS[reached]}
            </StatusPill>
          </div>
        </CardHeader>
        <CardBody className="flex flex-col gap-4">
          <ol className="flex flex-col gap-2">
            {TIMELINE_STEPS.map((label, i) => {
              const done = i <= reached;
              return (
                <li key={label} className="flex items-center gap-3">
                  <span
                    aria-hidden
                    className={
                      "flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold " +
                      (done
                        ? "bg-status-success-bg text-status-success-fg"
                        : "bg-surface-sunken text-ink-tertiary")
                    }
                  >
                    {done ? "✓" : i + 1}
                  </span>
                  <span
                    className={
                      "text-sm " +
                      (done
                        ? "font-medium text-ink-primary"
                        : "text-ink-tertiary")
                    }
                  >
                    {label}
                  </span>
                </li>
              );
            })}
          </ol>
          <p className="text-xs text-ink-tertiary">
            Started {created.toLocaleDateString()}. We&apos;ll message you here
            if we need anything, and share the results directly.
          </p>
        </CardBody>
      </Card>

      <MessageThread serviceId={serviceId} />
    </div>
  );
}
