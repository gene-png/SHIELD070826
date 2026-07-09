"use client";

import * as React from "react";

import {
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  cn,
} from "@shield/design-system";

import { CsfQuestionnaire } from "@/components/admin/csf/CsfQuestionnaire";
import { SelfAssessmentSubmitted } from "@/components/self-assessment/SelfAssessmentSubmitted";
import {
  fetchCatalog,
  fetchSelfAssessment,
  patchSelfAssessmentAnswer,
  submitSelfAssessment,
} from "@/lib/csf/client";
import type {
  CatalogSubcategory,
  CsfAnswer,
  CsfAnswerPatch,
  CsfAssessment,
  CsfCatalog,
} from "@/lib/csf/types";

const PROFILE_RANK: Record<string, number> = { LOW: 0, MOD: 1, HIGH: 2 };
const PROFILE_LABEL: Record<string, string> = {
  LOW: "Low",
  MOD: "Moderate",
  HIGH: "High",
};

/**
 * Narrow the catalog to the subcategories that apply at the client's impact
 * profile (LOW ⊆ MOD ⊆ HIGH), dropping now-empty categories/functions. No
 * profile -> the full catalog.
 */
function filterCatalogByProfile(
  catalog: CsfCatalog,
  profile: string | null,
): CsfCatalog {
  if (!profile) return catalog;
  const max = PROFILE_RANK[profile] ?? 2;
  const inScope = (s: CatalogSubcategory): boolean =>
    (PROFILE_RANK[s.min_profile] ?? 0) <= max;
  const functions = catalog.functions
    .map((fn) => ({
      ...fn,
      categories: fn.categories
        .map((cat) => ({
          ...cat,
          subcategories: cat.subcategories.filter(inScope),
        }))
        .filter((cat) => cat.subcategories.length > 0),
    }))
    .filter((fn) => fn.categories.length > 0);
  const total = functions.reduce(
    (n, fn) =>
      n + fn.categories.reduce((m, c) => m + c.subcategories.length, 0),
    0,
  );
  return { ...catalog, functions, total_subcategories: total };
}

export function CsfSelfAssessment({
  serviceId,
}: {
  serviceId: string;
}): JSX.Element {
  const [catalog, setCatalog] = React.useState<CsfCatalog | null>(null);
  const [assessment, setAssessment] = React.useState<CsfAssessment | null>(
    null,
  );
  const [target, setTarget] = React.useState<number>(3);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);
  const [submitError, setSubmitError] = React.useState<string | null>(null);
  const [submitted, setSubmitted] = React.useState(false);

  React.useEffect(() => {
    let cancelled = false;
    Promise.all([fetchCatalog(), fetchSelfAssessment(serviceId)])
      .then(([cat, a]) => {
        if (cancelled) return;
        setCatalog(cat);
        setAssessment(a);
        if (a?.client_target_tier) setTarget(a.client_target_tier);
        if (a && a.status !== "draft") setSubmitted(true);
      })
      .catch((err) => {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : "Failed to load.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [serviceId]);

  const answersByCode = React.useMemo(() => {
    const map: Record<string, CsfAnswer> = {};
    for (const a of assessment?.answers ?? []) map[a.subcategory_code] = a;
    return map;
  }, [assessment]);

  const filteredCatalog = React.useMemo(
    () =>
      catalog
        ? filterCatalogByProfile(catalog, assessment?.client_profile ?? null)
        : null,
    [catalog, assessment],
  );

  async function onAnswerUpdate(
    answerId: string,
    patch: CsfAnswerPatch,
  ): Promise<void> {
    setAssessment((curr) =>
      curr
        ? {
            ...curr,
            answers: curr.answers.map((a) =>
              a.id === answerId ? ({ ...a, ...patch } as CsfAnswer) : a,
            ),
          }
        : curr,
    );
    try {
      const updated = await patchSelfAssessmentAnswer(answerId, patch);
      setAssessment((curr) =>
        curr
          ? {
              ...curr,
              answers: curr.answers.map((a) =>
                a.id === answerId ? updated : a,
              ),
            }
          : curr,
      );
    } catch {
      // Best-effort optimistic save; a reload reconciles if it failed.
    }
  }

  async function onSubmit(): Promise<void> {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const next = await submitSelfAssessment(serviceId, {
        target_tier: target,
      });
      setAssessment(next);
      setSubmitted(true);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Submit failed.");
    } finally {
      setSubmitting(false);
    }
  }

  if (loadError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Couldn&apos;t load your self-assessment</CardTitle>
        </CardHeader>
        <CardBody>
          <p className="text-sm text-status-danger-fg">{loadError}</p>
        </CardBody>
      </Card>
    );
  }
  if (!catalog || !assessment || !filteredCatalog) {
    return (
      <p className="text-sm text-ink-tertiary">Loading your self-assessment…</p>
    );
  }
  // Coverage is over the in-scope (profile-filtered) subcategories only.
  const inScopeCodes = new Set(
    filteredCatalog.functions.flatMap((fn) =>
      fn.categories.flatMap((c) => c.subcategories.map((s) => s.code)),
    ),
  );
  const answeredCount = assessment.answers.filter(
    (a) => inScopeCodes.has(a.subcategory_code) && a.maturity_tier !== null,
  ).length;
  const total = inScopeCodes.size;
  const profileLabel = assessment.client_profile
    ? (PROFILE_LABEL[assessment.client_profile] ?? null)
    : null;
  const targetTiers = catalog.tiers.filter((t) => t.tier >= 2);

  // Post-submission: answers stay visible but read-only. The message thread
  // below (rendered by the page) remains active for follow-up.
  if (submitted) {
    return (
      <div className="flex flex-col gap-6">
        <SelfAssessmentSubmitted />
        <Card>
          <CardHeader>
            <CardTitle>Your submitted answers</CardTitle>
          </CardHeader>
          <CardBody>
            <p className="mb-4 text-sm text-ink-secondary">
              These are the responses you submitted for review. They&apos;re
              read-only now — your consultant will follow up in the messages
              below if anything needs another look.
            </p>
            <CsfQuestionnaire
              catalog={filteredCatalog}
              answersByCode={answersByCode}
              onAnswerUpdate={onAnswerUpdate}
              readOnly
            />
          </CardBody>
        </Card>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>1. Your maturity target</CardTitle>
        </CardHeader>
        <CardBody className="flex flex-col gap-3">
          <p className="text-sm text-ink-secondary">
            Pick the maturity tier you want to reach. We measure the gap between
            your answers below and this goal to recommend what to prioritize.
          </p>
          <div className="flex flex-wrap gap-2">
            {targetTiers.map((t) => (
              <button
                key={t.tier}
                type="button"
                onClick={() => setTarget(t.tier)}
                aria-pressed={target === t.tier}
                className={cn(
                  "rounded-md border px-4 py-2 text-left text-sm transition-colors",
                  target === t.tier
                    ? "border-brand-500 bg-brand-50 text-ink-primary"
                    : "border-border bg-surface-card text-ink-secondary hover:border-border-strong",
                )}
              >
                <span className="block font-semibold">
                  Tier {t.tier} · {t.short_label}
                </span>
                <span className="block text-xs text-ink-tertiary">
                  {t.description}
                </span>
              </button>
            ))}
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle>2. Self-assessment</CardTitle>
            <span className="text-sm text-ink-tertiary">
              {answeredCount} of {total} answered
            </span>
          </div>
        </CardHeader>
        <CardBody>
          <p className="mb-2 text-sm text-ink-secondary">
            For each outcome, choose the tier that best reflects your
            organization today. Answer what you can — your consultant reviews
            everything before anything is processed.
          </p>
          {profileLabel ? (
            <p className="mb-4 text-xs text-ink-tertiary">
              Showing the {total} outcomes that apply to your{" "}
              <span className="font-medium text-ink-secondary">
                {profileLabel} impact
              </span>{" "}
              profile.
            </p>
          ) : null}
          <CsfQuestionnaire
            catalog={filteredCatalog}
            answersByCode={answersByCode}
            onAnswerUpdate={onAnswerUpdate}
          />
        </CardBody>
      </Card>

      {submitError ? (
        <div
          role="alert"
          className="rounded-md border border-status-danger-border bg-status-danger-bg px-4 py-3 text-sm text-status-danger-fg"
        >
          {submitError}
        </div>
      ) : null}

      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-ink-tertiary">
          You can submit now and your consultant will follow up if anything
          needs more detail.
        </p>
        <button
          type="button"
          onClick={() => void onSubmit()}
          disabled={submitting}
          className="rounded-md bg-brand-500 px-5 py-2.5 text-sm font-semibold text-ink-on-accent hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {submitting ? "Submitting…" : "Submit for review"}
        </button>
      </div>
    </div>
  );
}
