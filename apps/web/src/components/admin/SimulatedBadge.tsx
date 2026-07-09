/**
 * Marker shown next to AI-generated output that came from deterministic
 * fixtures (SHIELD_LLM_MODE=fixture) rather than a real model call. Callers
 * render it only when a run-ai response reports `mode === "fixture"`, so a
 * consultant never mistakes simulated suggestions for real analysis.
 */
export function SimulatedBadge(): JSX.Element {
  return (
    <span className="ml-2 inline-block rounded-full border border-status-info-border bg-status-info-bg px-2 py-0.5 text-xs font-semibold text-status-info-fg">
      Simulated
    </span>
  );
}
