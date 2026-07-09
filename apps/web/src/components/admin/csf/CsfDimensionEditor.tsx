"use client";

import * as React from "react";

import { Card, CardBody, CardHeader, CardTitle } from "@shield/design-system";

import {
  CsfProxyError,
  fetchProfile,
  patchDimensionScore,
} from "@/lib/csf/client";
import type {
  CsfDimensionScore,
  CsfDimensionScorePatch,
  CsfProfile,
} from "@/lib/csf/types";

export interface CsfDimensionEditorProps {
  serviceId: string;
  readOnly?: boolean;
  /** Called after any successful edit so the Enterprise roll-up can refresh. */
  onChanged?: () => void;
}

const TIERS: { value: string; label: string }[] = [
  { value: "high", label: "High" },
  { value: "moderate", label: "Moderate" },
  { value: "low", label: "Low" },
];

type DimKey =
  "governance" | "policy" | "implementation" | "monitoring" | "improvement";

const DIMS: { key: DimKey; label: string }[] = [
  { key: "governance", label: "Governance" },
  { key: "policy", label: "Policy & Process" },
  { key: "implementation", label: "Implementation" },
  { key: "monitoring", label: "Monitoring & Measurement" },
  { key: "improvement", label: "Continuous Improvement" },
];

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

export function CsfDimensionEditor({
  serviceId,
  readOnly = false,
  onChanged,
}: CsfDimensionEditorProps): JSX.Element {
  const [tier, setTier] = React.useState("high");
  const [profile, setProfile] = React.useState<CsfProfile | null>(null);
  const [code, setCode] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const loadTier = React.useCallback(
    async (t: string) => {
      try {
        const p = await fetchProfile(serviceId, t);
        setProfile(p);
        setCode((prev) =>
          prev && p.rows.some((r) => r.subcategory_code === prev)
            ? prev
            : (p.rows[0]?.subcategory_code ?? null),
        );
      } catch (err) {
        setError(describeError(err));
      }
    },
    [serviceId],
  );

  React.useEffect(() => {
    void loadTier(tier);
  }, [tier, loadTier]);

  const row = profile?.rows.find((r) => r.subcategory_code === code) ?? null;

  async function apply(patch: CsfDimensionScorePatch): Promise<void> {
    if (!row) return;
    setBusy(true);
    setError(null);
    try {
      const next = await patchDimensionScore(row.id, patch);
      setProfile((prev) =>
        prev
          ? {
              ...prev,
              rows: prev.rows.map((r) => (r.id === next.id ? next : r)),
            }
          : prev,
      );
      onChanged?.();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Edit dimension scores</CardTitle>
      </CardHeader>
      <CardBody className="flex flex-col gap-4">
        {error ? (
          <p className="text-sm text-status-danger-fg" role="alert">
            {error}
          </p>
        ) : null}

        <div className="flex flex-wrap items-center gap-3">
          <div className="flex gap-1" role="tablist" aria-label="Tier">
            {TIERS.map((t) => (
              <button
                key={t.value}
                type="button"
                role="tab"
                aria-selected={tier === t.value}
                onClick={() => setTier(t.value)}
                className={[
                  "rounded-md border px-3 py-1.5 text-xs font-semibold",
                  tier === t.value
                    ? "border-brand-500 bg-brand-500 text-ink-on-accent"
                    : "border-border bg-surface-card text-ink-secondary hover:bg-surface-sunken",
                ].join(" ")}
              >
                {t.label}
              </button>
            ))}
          </div>
          <select
            aria-label="Subcategory"
            value={code ?? ""}
            onChange={(e) => setCode(e.target.value)}
            className="rounded-md border border-border bg-surface-card px-2 py-1.5 text-sm text-ink-primary"
          >
            {(profile?.rows ?? []).map((r) => (
              <option key={r.id} value={r.subcategory_code}>
                {r.subcategory_code}
              </option>
            ))}
          </select>
        </div>

        {!row ? (
          <p className="text-sm text-ink-tertiary">Select a subcategory.</p>
        ) : (
          <div className="flex flex-col gap-3">
            {DIMS.map((d) => (
              <div
                key={d.key}
                className="flex flex-wrap items-center justify-between gap-2"
              >
                <span className="text-sm text-ink-secondary">{d.label}</span>
                <div
                  className="flex gap-1"
                  role="radiogroup"
                  aria-label={d.label}
                >
                  {[0, 1, 2].map((v) => {
                    const active = row[d.key] === v;
                    return (
                      <button
                        key={v}
                        type="button"
                        role="radio"
                        aria-checked={active}
                        disabled={readOnly || busy}
                        onClick={() => void apply({ [d.key]: v })}
                        className={[
                          "h-8 w-8 rounded-md border text-sm font-semibold",
                          active
                            ? "border-brand-500 bg-brand-500 text-ink-on-accent"
                            : "border-border bg-surface-card text-ink-secondary hover:bg-surface-sunken",
                          readOnly || busy
                            ? "cursor-not-allowed opacity-60"
                            : "",
                        ].join(" ")}
                      >
                        {v}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border-subtle pt-3 text-sm">
              <span className="text-ink-secondary">
                Total{" "}
                <span className="font-semibold text-ink-primary">
                  {row.total}
                </span>{" "}
                · Level{" "}
                <span className="font-semibold text-ink-primary">
                  L{row.level}
                </span>
                {row.evidence_capped ? (
                  <span className="ml-2 text-status-warning-fg">
                    (capped — no evidence)
                  </span>
                ) : null}
              </span>
              <label className="flex items-center gap-2">
                <span className="text-ink-secondary">Target</span>
                <select
                  value={row.target_level ?? ""}
                  disabled={readOnly || busy}
                  onChange={(e) =>
                    void apply({
                      target_level: e.target.value
                        ? Number(e.target.value)
                        : null,
                    })
                  }
                  className="rounded-md border border-border bg-surface-card px-2 py-1 text-sm text-ink-primary"
                >
                  <option value="">—</option>
                  {[1, 2, 3, 4, 5].map((l) => (
                    <option key={l} value={l}>
                      L{l}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="flex flex-wrap items-center gap-4 text-sm">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={row.has_evidence}
                  disabled={readOnly || busy}
                  onChange={(e) =>
                    void apply({ has_evidence: e.currentTarget.checked })
                  }
                />
                <span className="text-ink-secondary">Evidence on file</span>
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={row.in_scope}
                  disabled={readOnly || busy}
                  onChange={(e) =>
                    void apply({ in_scope: e.currentTarget.checked })
                  }
                />
                <span className="text-ink-secondary">In scope</span>
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={row.locked}
                  disabled={readOnly || busy}
                  onChange={(e) =>
                    void apply({ locked: e.currentTarget.checked })
                  }
                />
                <span className="text-ink-secondary">Lock vs AI reruns</span>
              </label>
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
