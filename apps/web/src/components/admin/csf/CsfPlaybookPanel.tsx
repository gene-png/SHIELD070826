"use client";

import * as React from "react";

import {
  Card,
  CardBody,
  CardDescription,
  CardHeader,
  CardTitle,
  DataTable,
  type DataTableColumn,
} from "@shield/design-system";

import {
  CsfProxyError,
  exportPlaybook,
  fetchEnterpriseProfile,
  runCsfAi,
  seedProfiles,
} from "@/lib/csf/client";
import { isAbortError } from "@/lib/http";
import { SimulatedBadge } from "@/components/admin/SimulatedBadge";

import { CsfDimensionEditor } from "./CsfDimensionEditor";
import type {
  CsfPlaybookExport,
  CsfRunAiResponse,
  EnterpriseProfile,
  EnterpriseSubcategory,
} from "@/lib/csf/types";

export interface CsfPlaybookPanelProps {
  serviceId: string;
  readOnly?: boolean;
}

function describeError(err: unknown): string {
  if (err instanceof CsfProxyError) {
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

const PRIORITY_COLOR: Record<string, { bg: string; fg: string }> = {
  P1: { bg: "#fee2e2", fg: "#991b1b" },
  P2: { bg: "#ffedd5", fg: "#9a3412" },
  P3: { bg: "#f1f5f9", fg: "#475569" },
};

function tierLevels(row: EnterpriseSubcategory): string {
  const order: [string, string][] = [
    ["high", "H"],
    ["moderate", "M"],
    ["low", "L"],
  ];
  return order
    .filter(([t]) => row.tier_levels[t] != null)
    .map(([t, abbr]) => `${abbr}${row.tier_levels[t]}`)
    .join("  ");
}

const COLUMNS: DataTableColumn<EnterpriseSubcategory>[] = [
  { key: "code", header: "Subcategory", cell: (r) => r.subcategory_code },
  { key: "name", header: "Outcome", cell: (r) => r.name },
  { key: "tiers", header: "Tiers", cell: (r) => tierLevels(r) },
  {
    key: "ent",
    header: "Enterprise",
    align: "center",
    cell: (r) => `L${r.enterprise_level}`,
  },
  {
    key: "rule",
    header: "Rule",
    align: "center",
    cell: (r) => `#${r.rollup_rule}`,
  },
  {
    key: "target",
    header: "Target",
    align: "center",
    cell: (r) => (r.target_level ? `L${r.target_level}` : "—"),
  },
  {
    key: "priority",
    header: "Gap",
    align: "center",
    cell: (r) => {
      if (!r.gap) return <span className="text-ink-tertiary">—</span>;
      const c = PRIORITY_COLOR[r.priority ?? "P3"] ?? PRIORITY_COLOR.P3;
      return (
        <span
          className="inline-block rounded-full px-2 py-0.5 text-xs font-semibold"
          style={{ backgroundColor: c.bg, color: c.fg }}
        >
          {r.priority ?? "gap"}
        </span>
      );
    },
  },
];

export function CsfPlaybookPanel({
  serviceId,
  readOnly = false,
}: CsfPlaybookPanelProps): JSX.Element {
  const [enterprise, setEnterprise] = React.useState<EnterpriseProfile | null>(
    null,
  );
  const [loading, setLoading] = React.useState(true);
  const [busy, setBusy] = React.useState<"seed" | "run" | "export" | null>(
    null,
  );
  const [runResult, setRunResult] = React.useState<CsfRunAiResponse | null>(
    null,
  );
  const [exportResult, setExportResult] =
    React.useState<CsfPlaybookExport | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const runAbortRef = React.useRef<AbortController | null>(null);

  const reload = React.useCallback(async () => {
    const ent = await fetchEnterpriseProfile(serviceId);
    setEnterprise(ent);
  }, [serviceId]);

  React.useEffect(() => {
    let active = true;
    (async () => {
      try {
        const ent = await fetchEnterpriseProfile(serviceId);
        if (active) setEnterprise(ent);
      } catch (err) {
        if (active) setError(describeError(err));
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [serviceId]);

  async function onSeed(): Promise<void> {
    setBusy("seed");
    setError(null);
    try {
      await seedProfiles(serviceId);
      await reload();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(null);
    }
  }

  async function onRunAi(): Promise<void> {
    const controller = new AbortController();
    runAbortRef.current = controller;
    setBusy("run");
    setError(null);
    setRunResult(null);
    try {
      setRunResult(await runCsfAi(serviceId, controller.signal));
      await reload();
    } catch (err) {
      if (isAbortError(err)) {
        setError("AI run canceled. No changes were applied.");
      } else {
        setError(describeError(err));
      }
    } finally {
      runAbortRef.current = null;
      setBusy(null);
    }
  }

  function onCancelRun(): void {
    runAbortRef.current?.abort();
  }

  async function onExport(): Promise<void> {
    setBusy("export");
    setError(null);
    try {
      setExportResult(await exportPlaybook(serviceId));
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(null);
    }
  }

  const seeded = (enterprise?.subcategories.length ?? 0) > 0;
  const gapCount = enterprise?.subcategories.filter((s) => s.gap).length ?? 0;

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>Full Playbook — Working Profiles</CardTitle>
          <CardDescription>
            Tiered (HIGH / MODERATE / LOW) five-dimension scoring rolled up to
            one Enterprise level per subcategory via the weighted-floor rules.
            AI suggests the dimensions; code computes every level, the cap, and
            the roll-up.
          </CardDescription>
        </CardHeader>
        <CardBody className="flex flex-col gap-4">
          {error ? (
            <p className="text-sm text-status-danger-fg" role="alert">
              {error}
            </p>
          ) : null}

          <div className="flex flex-wrap items-center gap-2">
            {!seeded ? (
              <button
                type="button"
                onClick={() => void onSeed()}
                disabled={busy !== null || readOnly}
                className="rounded-md bg-brand-500 px-4 py-2 text-sm font-semibold text-ink-on-accent hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {busy === "seed" ? "Seeding…" : "Seed Working Profiles"}
              </button>
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => void onRunAi()}
                  disabled={busy !== null || readOnly}
                  className="rounded-md bg-brand-500 px-4 py-2 text-sm font-semibold text-ink-on-accent hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {busy === "run" ? "Running…" : "Run AI (csf_score)"}
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
              </>
            )}
            {seeded ? (
              <button
                type="button"
                onClick={() => void onExport()}
                disabled={busy !== null}
                className="rounded-md border border-border-default px-4 py-2 text-sm font-semibold text-ink-primary hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-60"
              >
                {busy === "export" ? "Exporting…" : "Export XLSX"}
              </button>
            ) : null}
            {exportResult ? (
              <span className="flex flex-wrap items-center gap-3 text-sm font-medium">
                {exportResult.artifacts.map((a) => (
                  <a
                    key={a.kind}
                    href={`/api/proxy/artifacts/${a.artifact_id}/download`}
                    className="text-brand-500 hover:underline"
                    title={a.filename}
                  >
                    {a.label}
                  </a>
                ))}
              </span>
            ) : null}
            {seeded ? (
              <span className="text-sm text-ink-secondary">
                {enterprise?.tiers_in_use.length ?? 0} tier(s) in use ·{" "}
                <span className="font-semibold text-ink-primary">
                  {gapCount}
                </span>{" "}
                subcategor{gapCount === 1 ? "y" : "ies"} with a gap
              </span>
            ) : null}
          </div>

          {runResult ? (
            <p className="text-sm text-ink-secondary" aria-live="polite">
              AI updated{" "}
              <span className="font-semibold text-ink-primary">
                {runResult.changed.length}
              </span>{" "}
              field
              {runResult.changed.length === 1 ? "" : "s"} across{" "}
              {new Set(runResult.changed.map((c) => c.subcategory_code)).size}{" "}
              subcategor
              {new Set(runResult.changed.map((c) => c.subcategory_code))
                .size === 1
                ? "y"
                : "ies"}
              .{runResult.mode === "fixture" ? <SimulatedBadge /> : null}
            </p>
          ) : null}

          {loading ? (
            <p className="text-sm text-ink-tertiary">Loading…</p>
          ) : seeded ? (
            <DataTable
              columns={COLUMNS}
              rows={enterprise?.subcategories ?? []}
              rowKey={(r) => r.subcategory_code}
            />
          ) : (
            <p className="text-sm text-ink-secondary">
              Seed the Working Profiles to score the ~106 subcategories across
              the tiers your client uses, then Run AI to draft the dimension
              scores.
            </p>
          )}
        </CardBody>
      </Card>
      {seeded ? (
        <CsfDimensionEditor
          serviceId={serviceId}
          readOnly={readOnly}
          onChanged={() => void reload()}
        />
      ) : null}
    </div>
  );
}
