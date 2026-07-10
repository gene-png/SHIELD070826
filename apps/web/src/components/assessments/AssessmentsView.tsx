"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import {
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  EmptyState,
  StatusPill,
} from "@shield/design-system";

import {
  createAssessment,
  fetchAssessments,
  isIncompleteIntakeError,
} from "@/lib/intake/client";
import {
  CSF_PROFILES,
  CSF_TARGET_TIERS,
  ASSESSMENT_SERVICE_TYPES,
  SELF_ASSESSMENT_SERVICE_TYPES,
  SERVICE_LABELS,
  ZT_TARGET_STAGES,
  clientAssessmentHref,
  type CsfProfile,
  type AssessmentResponse,
  type ServiceType,
} from "@/lib/intake/types";

function statusTone(
  assessment: string | null,
  serviceStatus: string,
): "info" | "warning" | "success" | "neutral" {
  const s = assessment ?? serviceStatus;
  if (s === "released") return "success";
  if (s === "approved") return "success";
  if (s === "submitted") return "warning";
  if (s === "draft" || s === "in_progress") return "info";
  return "neutral";
}

function statusLabel(e: AssessmentResponse): string {
  switch (e.assessment_status) {
    case "draft":
      return "In progress — not submitted";
    case "submitted":
      return "Submitted — under review";
    case "approved":
      return "Approved";
    case "released":
      // v1 deliverables are handed over out of band (the consultant downloads
      // and shares them); there is no in-app "release" action. Show honest copy
      // instead of a "released" label nothing in the product can satisfy.
      return "Complete";
    default:
      return e.status === "released" ? "Complete" : "In progress";
  }
}

export function AssessmentsView(): JSX.Element {
  const router = useRouter();
  const [assessments, setAssessments] = React.useState<
    AssessmentResponse[] | null
  >(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);

  // Create form state
  const [creating, setCreating] = React.useState(false);
  const [svcType, setSvcType] = React.useState<ServiceType>("nist_csf");
  const [name, setName] = React.useState("");
  const [csfTier, setCsfTier] = React.useState<number | null>(null);
  const [csfProfile, setCsfProfile] = React.useState<CsfProfile | null>(null);
  const [ztStage, setZtStage] = React.useState<number | null>(null);
  const [submitting, setSubmitting] = React.useState(false);
  const [createError, setCreateError] = React.useState<string | null>(null);
  const [createNeedsIntake, setCreateNeedsIntake] = React.useState(false);

  const load = React.useCallback(async () => {
    try {
      setAssessments(await fetchAssessments());
    } catch (err) {
      setLoadError(
        err instanceof Error ? err.message : "Failed to load assessments.",
      );
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const isCsf = svcType === "nist_csf";
  const isZt = svcType === "zero_trust_cisa" || svcType === "zero_trust_dod";
  const targetsReady = isCsf
    ? csfTier !== null && csfProfile !== null
    : isZt
      ? ztStage !== null
      : false;

  async function onCreate(): Promise<void> {
    setSubmitting(true);
    setCreateError(null);
    setCreateNeedsIntake(false);
    try {
      const created = await createAssessment({
        service_type: svcType,
        name: name.trim() || undefined,
        csf_target_tier: isCsf ? (csfTier ?? undefined) : undefined,
        csf_profile: isCsf ? (csfProfile ?? undefined) : undefined,
        zt_target_stage: isZt ? (ztStage ?? undefined) : undefined,
      });
      // Drop the client straight into the new assessment's self-assessment.
      router.push(
        `/self-assessment/${created.service_id}?type=${created.service_type}`,
      );
    } catch (err) {
      setCreateNeedsIntake(isIncompleteIntakeError(err));
      setCreateError(
        err instanceof Error ? err.message : "Couldn't start the assessment.",
      );
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-3xl font-semibold text-ink-primary">
            My assessments
          </h1>
          <p className="max-w-prose text-sm text-ink-secondary">
            Each assessment is its own project and workspace. You can run as
            many as you need in parallel — even more than one of the same
            assessment type.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setCreating((v) => !v)}
          className="rounded-md bg-brand-500 px-4 py-2 text-sm font-semibold text-ink-on-accent hover:bg-brand-600"
        >
          {creating ? "Close" : "+ Start a new assessment"}
        </button>
      </header>

      {creating ? (
        <Card>
          <CardHeader>
            <CardTitle>Start a new assessment</CardTitle>
          </CardHeader>
          <CardBody className="flex flex-col gap-4">
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-ink-primary">
                Assessment type
              </span>
              <select
                value={svcType}
                onChange={(e) => {
                  setSvcType(e.target.value as ServiceType);
                  setCsfTier(null);
                  setCsfProfile(null);
                  setZtStage(null);
                }}
                className="rounded-md border border-border bg-surface-card px-3 py-2 text-ink-primary focus:border-brand-500 focus:outline-none"
              >
                {ASSESSMENT_SERVICE_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {SERVICE_LABELS[t]}
                  </option>
                ))}
              </select>
            </label>

            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-ink-primary">
                Assessment name{" "}
                <span className="font-normal text-ink-tertiary">
                  (optional)
                </span>
              </span>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Cloud Platform — Q3 review"
                className="rounded-md border border-border bg-surface-card px-3 py-2 text-ink-primary focus:border-brand-500 focus:outline-none"
              />
            </label>

            {isCsf ? (
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="flex flex-col gap-1 text-sm">
                  <span className="font-medium text-ink-primary">
                    Target tier
                  </span>
                  <select
                    value={csfTier ?? ""}
                    onChange={(e) =>
                      setCsfTier(e.target.value ? Number(e.target.value) : null)
                    }
                    className="rounded-md border border-border bg-surface-card px-3 py-2 text-ink-primary focus:border-brand-500 focus:outline-none"
                  >
                    <option value="">Select a tier…</option>
                    {CSF_TARGET_TIERS.map((t) => (
                      <option key={t.value} value={t.value}>
                        {t.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="font-medium text-ink-primary">
                    Impact profile
                  </span>
                  <select
                    value={csfProfile ?? ""}
                    onChange={(e) =>
                      setCsfProfile(
                        (e.target.value || null) as CsfProfile | null,
                      )
                    }
                    className="rounded-md border border-border bg-surface-card px-3 py-2 text-ink-primary focus:border-brand-500 focus:outline-none"
                  >
                    <option value="">Select a profile…</option>
                    {CSF_PROFILES.map((p) => (
                      <option key={p.value} value={p.value}>
                        {p.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            ) : null}

            {isZt ? (
              <label className="flex flex-col gap-1 text-sm">
                <span className="font-medium text-ink-primary">
                  Target stage
                </span>
                <select
                  value={ztStage ?? ""}
                  onChange={(e) =>
                    setZtStage(e.target.value ? Number(e.target.value) : null)
                  }
                  className="max-w-xs rounded-md border border-border bg-surface-card px-3 py-2 text-ink-primary focus:border-brand-500 focus:outline-none"
                >
                  <option value="">Select a stage…</option>
                  {ZT_TARGET_STAGES[
                    svcType as "zero_trust_cisa" | "zero_trust_dod"
                  ].map((s) => (
                    <option key={s.value} value={s.value}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}

            {createError ? (
              <div role="alert" className="flex flex-col gap-2 text-sm">
                <p className="text-status-danger-fg">{createError}</p>
                {createNeedsIntake ? (
                  <Link
                    href="/intake"
                    className="self-start rounded-md bg-brand-500 px-4 py-2 font-semibold text-ink-on-accent hover:bg-brand-600"
                  >
                    Finish intake →
                  </Link>
                ) : null}
              </div>
            ) : null}

            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => void onCreate()}
                disabled={submitting || !targetsReady}
                className="rounded-md bg-brand-500 px-5 py-2.5 text-sm font-semibold text-ink-on-accent hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {submitting ? "Starting…" : "Start assessment"}
              </button>
              {!targetsReady ? (
                <span className="text-xs text-ink-tertiary">
                  Pick a target before starting.
                </span>
              ) : null}
            </div>
          </CardBody>
        </Card>
      ) : null}

      {loadError ? (
        <Card>
          <CardHeader>
            <CardTitle>Couldn&apos;t load your assessments</CardTitle>
          </CardHeader>
          <CardBody>
            <p className="text-sm text-status-danger-fg" role="alert">
              {loadError}
            </p>
          </CardBody>
        </Card>
      ) : assessments === null ? (
        <p className="text-sm text-ink-tertiary" aria-live="polite">
          Loading your assessments…
        </p>
      ) : assessments.length === 0 ? (
        <EmptyState
          title="No assessments yet"
          description="Start your first assessment to begin a self-assessment."
        />
      ) : (
        <ul className="flex flex-col gap-3">
          {assessments.map((e) => {
            const isSelfAssessment = SELF_ASSESSMENT_SERVICE_TYPES.includes(
              e.service_type,
            );
            const isDraft = isSelfAssessment && e.assessment_status === "draft";
            const cta = isDraft ? "Continue →" : "Open →";
            return (
              <li key={e.service_id}>
                <Link
                  href={clientAssessmentHref(e.service_type, e.service_id)}
                  className="block rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                >
                  <Card className="transition-colors hover:border-border-strong">
                    <CardBody className="flex flex-wrap items-center justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-base font-semibold text-ink-primary">
                          {e.title}
                        </p>
                        <p className="text-sm text-ink-secondary">
                          {SERVICE_LABELS[e.service_type]}
                        </p>
                      </div>
                      <div className="flex items-center gap-3">
                        <StatusPill
                          tone={statusTone(e.assessment_status, e.status)}
                          withDot
                        >
                          {statusLabel(e)}
                        </StatusPill>
                        <span className="text-sm font-semibold text-brand-500">
                          {cta}
                        </span>
                      </div>
                    </CardBody>
                  </Card>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
