"use client";

import * as React from "react";

interface AiStatus {
  mode: string;
  provider: string;
  model: string;
  ready: boolean;
  detail: string;
}

/**
 * Tells the consultant how AI features will behave. Fixture mode still
 * produces deterministic results, so it gets an informational note (not a
 * "disabled" warning) plus the flag to flip for real analysis. Live-but-
 * misconfigured gets a warning. Renders nothing when live AI is ready or
 * while the status is still loading.
 */
export function AiStatusBanner(): JSX.Element | null {
  const [status, setStatus] = React.useState<AiStatus | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    fetch("/api/proxy/admin/ai-status", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: AiStatus | null) => {
        if (!cancelled) setStatus(d);
      })
      .catch(() => {
        /* non-blocking */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!status) return null;

  // Fixture mode DOES produce output — deterministic fixtures. Say so, so a
  // consultant never mistakes simulated results for real analysis.
  if (status.mode === "fixture") {
    return (
      <div
        role="status"
        className="rounded-md border border-status-info-border bg-status-info-bg px-4 py-3 text-sm text-status-info-fg"
      >
        <span className="font-semibold">AI suggestions are simulated.</span>{" "}
        Run-AI steps return deterministic fixtures for demo and testing, not
        real model output. Set{" "}
        <code className="rounded bg-surface-sunken px-1 py-0.5 font-mono text-xs">
          SHIELD_LLM_MODE=live
        </code>{" "}
        for real analysis.
      </div>
    );
  }

  // Live mode selected but not usable (e.g. missing key): genuinely won't run.
  if (!status.ready) {
    return (
      <div
        role="status"
        className="rounded-md border border-status-warning-border bg-status-warning-bg px-4 py-3 text-sm text-status-warning-fg"
      >
        <span className="font-semibold">AI is not live.</span> {status.detail}{" "}
        Extraction and other AI steps won&apos;t produce results until this is
        configured.
      </div>
    );
  }

  return null;
}
