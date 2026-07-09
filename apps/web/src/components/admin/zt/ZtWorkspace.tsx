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

import {
  approveAssessment,
  createAssessment,
  fetchCatalog,
  fetchGapAnalysis,
  fetchLatestAssessment,
  fetchLatestDeliverable,
  fetchScore,
  patchAnswer,
  runZtAi,
  ZtProxyError,
} from "@/lib/zt/client";
import { isAbortError } from "@/lib/http";
import { SimulatedBadge } from "@/components/admin/SimulatedBadge";
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
} from "@/lib/zt/types";

import { MessageThread } from "@/components/messages/MessageThread";
import { StaleDocsNudge } from "@/components/admin/StaleDocsNudge";

import { ZtDeliverableCard } from "./ZtDeliverableCard";
import { ZtGapList } from "./ZtGapList";
import { ZtRoadmapCard } from "./ZtRoadmapCard";
import { ZtQuestionnaire } from "./ZtQuestionnaire";
import { ZtScoreCard } from "./ZtScoreCard";

export interface ZtWorkspaceProps {
  serviceId: string;
  framework: ZtFramework;
  serviceTitle: string;
}

/** Clamp a stored target stage to the selectable 2-4 range; default 3. */
function normalizeTarget(value: number | null | undefined): number {
  return value === 2 || value === 3 || value === 4 ? value : 3;
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

const FRAMEWORK_NAME: Record<ZtFramework, string> = {
  cisa_ztmm_2_0: "CISA ZTMM 2.0",
  dod_ztra: "DoD ZT Reference Architecture",
};

export function ZtWorkspace({
  serviceId,
  framework,
  serviceTitle,
}: ZtWorkspaceProps): JSX.Element {
  const [catalog, setCatalog] = React.useState<ZtCatalog | null>(null);
  const [assessment, setAssessment] = React.useState<ZtAssessment | null>(null);
  const [score, setScore] = React.useState<ZtScoreSummary | null>(null);
  const [gap, setGap] = React.useState<GapAnalysis | null>(null);
  const [deliverable, setDeliverable] = React.useState<ZtDeliverable | null>(
    null,
  );
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<"create" | "approve" | "run" | null>(
    null,
  );
  const [runResult, setRunResult] = React.useState<ZtRunAiResponse | null>(
    null,
  );
  const [targetStage, setTargetStage] = React.useState(3);
  const runAbortRef = React.useRef<AbortController | null>(null);

  const answersByCode = React.useMemo(() => {
    const out: Record<string, ZtAnswer> = {};
    if (assessment) {
      for (const a of assessment.answers) {
        out[a.capability_code] = a;
      }
    }
    return out;
  }, [assessment]);

  const refreshScoreAndGap = React.useCallback(
    async (currentTarget: number) => {
      try {
        const [s, g] = await Promise.all([
          fetchScore(serviceId),
          fetchGapAnalysis(serviceId, { targetStage: currentTarget }),
        ]);
        setScore(s);
        setGap(g);
      } catch {
        // Non-blocking; cards show their own loading state.
      }
    },
    [serviceId],
  );

  const initialLoad = React.useCallback(async () => {
    try {
      const cat = await fetchCatalog(framework);
      setCatalog(cat);
    } catch (err) {
      setLoadError(describeError(err));
      return;
    }
    try {
      const a = await fetchLatestAssessment(serviceId);
      setAssessment(a);
      if (a) {
        // Default the gap target to the client's chosen stage (set at intake).
        const t = normalizeTarget(a.client_target_stage);
        setTargetStage(t);
        await refreshScoreAndGap(t);
        try {
          const d = await fetchLatestDeliverable(serviceId);
          setDeliverable(d);
        } catch {
          // non-blocking
        }
      }
    } catch (err) {
      setLoadError(describeError(err));
    }
  }, [serviceId, framework, refreshScoreAndGap]);

  React.useEffect(() => {
    void initialLoad();
  }, [initialLoad]);

  async function onCreateAssessment(): Promise<void> {
    setBusy("create");
    try {
      const next = await createAssessment(serviceId);
      setAssessment(next);
      const t = normalizeTarget(next.client_target_stage);
      setTargetStage(t);
      await refreshScoreAndGap(t);
    } catch (err) {
      setLoadError(describeError(err));
    } finally {
      setBusy(null);
    }
  }

  async function onAnswerUpdate(
    answerId: string,
    patch: ZtAnswerPatch,
  ): Promise<void> {
    setAssessment((curr) => {
      if (!curr) return curr;
      return {
        ...curr,
        answers: curr.answers.map((a) =>
          a.id === answerId ? { ...a, ...patch } : a,
        ),
      };
    });
    try {
      const next = await patchAnswer(answerId, patch);
      setAssessment((curr) => {
        if (!curr) return curr;
        return {
          ...curr,
          answers: curr.answers.map((a) => (a.id === answerId ? next : a)),
        };
      });
      await refreshScoreAndGap(targetStage);
    } catch (err) {
      setLoadError(describeError(err));
      const a = await fetchLatestAssessment(serviceId);
      setAssessment(a);
    }
  }

  async function onApprove(): Promise<void> {
    if (!assessment) return;
    setBusy("approve");
    try {
      const next = await approveAssessment(assessment.id);
      setAssessment(next);
    } catch (err) {
      setLoadError(describeError(err));
    } finally {
      setBusy(null);
    }
  }

  async function onChangeTargetStage(next: number): Promise<void> {
    setTargetStage(next);
    if (assessment) {
      const g = await fetchGapAnalysis(serviceId, { targetStage: next });
      setGap(g);
    }
  }

  async function onRunAi(): Promise<void> {
    const controller = new AbortController();
    runAbortRef.current = controller;
    setBusy("run");
    setRunResult(null);
    setLoadError(null);
    try {
      const result = await runZtAi(serviceId, controller.signal);
      setRunResult(result);
      // Re-pull so the questionnaire + score reflect the AI's suggestions.
      const a = await fetchLatestAssessment(serviceId);
      setAssessment(a);
      await refreshScoreAndGap(targetStage);
    } catch (err) {
      if (isAbortError(err)) {
        setLoadError("AI run canceled. No changes were applied.");
      } else {
        setLoadError(describeError(err));
      }
    } finally {
      runAbortRef.current = null;
      setBusy(null);
    }
  }

  function onCancelRun(): void {
    runAbortRef.current?.abort();
  }

  const readOnly =
    assessment?.status === "approved" || assessment?.status === "released";

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-500">
            {FRAMEWORK_NAME[framework]}
          </p>
          <h1 className="text-3xl font-semibold text-ink-primary">
            {serviceTitle}
          </h1>
          <p className="max-w-prose text-sm text-ink-secondary">
            Score each capability against the 4-stage maturity model. Coverage +
            per-pillar rollup update on every edit; prioritized remediation gaps
            surface alongside the score.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {assessment ? (
            <StatusPill
              tone={
                assessment.status === "approved" ||
                assessment.status === "released"
                  ? "success"
                  : assessment.status === "submitted"
                    ? "warning"
                    : "info"
              }
              withDot
            >
              {assessment.status === "draft"
                ? `Draft v${assessment.version}`
                : assessment.status === "submitted"
                  ? `Submitted v${assessment.version}`
                  : assessment.status === "approved"
                    ? `Approved v${assessment.version}`
                    : `Released v${assessment.version}`}
            </StatusPill>
          ) : (
            <StatusPill tone="neutral" withDot>
              No assessment yet
            </StatusPill>
          )}
          {assessment ? (
            <button
              type="button"
              onClick={() => void onApprove()}
              disabled={
                busy !== null ||
                (assessment.status !== "draft" &&
                  assessment.status !== "submitted")
              }
              className="rounded-md bg-brand-500 px-4 py-2 text-sm font-semibold text-ink-on-accent hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {assessment.status === "approved"
                ? "Approved"
                : assessment.status === "released"
                  ? "Released"
                  : busy === "approve"
                    ? "Approving…"
                    : assessment.status === "submitted"
                      ? "Approve client inputs"
                      : "Approve"}
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void onCreateAssessment()}
              disabled={busy !== null || !catalog}
              className="rounded-md bg-brand-500 px-4 py-2 text-sm font-semibold text-ink-on-accent hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {busy === "create" ? "Creating…" : "Start assessment"}
            </button>
          )}
        </div>
      </header>

      {assessment?.status === "submitted" ? (
        <div className="rounded-md border border-status-warning-border bg-status-warning-bg px-4 py-3 text-sm text-status-warning-fg">
          <span className="font-semibold">
            Client self-assessment submitted.
          </span>{" "}
          Review and edit their answers below for completeness and accuracy,
          then <span className="font-medium">Approve client inputs</span> and
          send for evaluation in the deliverable section.
        </div>
      ) : null}

      {loadError ? (
        <Card>
          <CardHeader>
            <CardTitle>Couldn&apos;t load the assessment</CardTitle>
          </CardHeader>
          <CardBody>
            <p className="text-sm text-status-danger-fg" role="alert">
              {loadError}
            </p>
          </CardBody>
        </Card>
      ) : null}

      {!catalog ? (
        <p className="text-sm text-ink-tertiary" aria-live="polite">
          Loading catalog…
        </p>
      ) : !assessment ? (
        <EmptyState
          title="No Zero Trust assessment yet"
          description="Click 'Start assessment' to create a fresh v1 with one empty answer per capability."
        />
      ) : (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Run AI (zt_score)</CardTitle>
            </CardHeader>
            <CardBody className="flex flex-col gap-3">
              <p className="text-sm text-ink-secondary">
                Suggest a current and target maturity stage per capability (on
                this framework&apos;s scale) plus per-pillar narratives. Locked
                rows are left untouched.
              </p>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => void onRunAi()}
                  disabled={busy !== null || readOnly}
                  className="rounded-md bg-brand-500 px-4 py-2 text-sm font-semibold text-ink-on-accent hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {busy === "run" ? "Running…" : "Run AI"}
                </button>
                {busy === "run" ? (
                  <button
                    type="button"
                    onClick={onCancelRun}
                    className="rounded-md border border-border-default px-4 py-2 text-sm font-semibold text-ink-primary hover:bg-surface-muted"
                  >
                    Cancel
                  </button>
                ) : null}
              </div>
              {runResult ? (
                <p className="text-sm text-ink-secondary" aria-live="polite">
                  Updated{" "}
                  <span className="font-semibold text-ink-primary">
                    {runResult.changed.length}
                  </span>{" "}
                  field
                  {runResult.changed.length === 1 ? "" : "s"} across{" "}
                  {
                    new Set(runResult.changed.map((c) => c.capability_code))
                      .size
                  }{" "}
                  capabilit
                  {new Set(runResult.changed.map((c) => c.capability_code))
                    .size === 1
                    ? "y"
                    : "ies"}
                  .{runResult.mode === "fixture" ? <SimulatedBadge /> : null}
                </p>
              ) : null}
            </CardBody>
          </Card>
          <ZtScoreCard score={score} />
          <MessageThread serviceId={serviceId} />
          <ZtGapList
            analysis={gap}
            targetStage={targetStage}
            onChangeTargetStage={(s) => void onChangeTargetStage(s)}
            stages={catalog.stages}
          />
          <ZtRoadmapCard analysis={gap} />
          <StaleDocsNudge stale={assessment.documents_stale} />
          <ZtDeliverableCard
            serviceId={serviceId}
            assessmentStatus={assessment.status}
            deliverable={deliverable}
            onChange={setDeliverable}
          />
          <ZtQuestionnaire
            catalog={catalog}
            answersByCode={answersByCode}
            readOnly={readOnly}
            onAnswerUpdate={onAnswerUpdate}
          />
        </>
      )}
    </div>
  );
}
