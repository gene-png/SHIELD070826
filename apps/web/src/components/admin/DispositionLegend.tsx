"use client";

import {
  Card,
  CardBody,
  CardDescription,
  CardHeader,
  CardTitle,
  StatusPill,
} from "@shield/design-system";

/**
 * Explains the three dispositions a consultant assigns to each capability.
 * The distinction that matters most is Consolidate vs Cut: Cut removes a tool
 * (its full annual cost becomes savings), while Consolidate keeps the
 * capability but merges it into another tool to stop paying twice.
 */
export function DispositionLegend(): JSX.Element {
  return (
    <Card>
      <CardHeader>
        <CardTitle>What the dispositions mean</CardTitle>
        <CardDescription>
          Assign one to every capability. Only <b>Cut</b> rows count toward the
          estimated-savings figure; <b>Consolidate</b> savings are realized
          later through migration or renegotiation.
        </CardDescription>
      </CardHeader>
      <CardBody className="flex flex-col gap-3 text-sm text-ink-secondary">
        <div className="flex items-start gap-3">
          <StatusPill tone="success">Keep</StatusPill>
          <p className="max-w-prose">
            The tool is the <b>primary capability</b> in its category and stays
            as-is. Nothing changes and no spend is removed — this is the tool
            other overlapping tools would fold into.
          </p>
        </div>
        <div className="flex items-start gap-3">
          <StatusPill tone="warning">Consolidate</StatusPill>
          <p className="max-w-prose">
            The tool <b>overlaps</b> with another tool that does the same job.
            Rather than run both, fold this one into the tool you&apos;re
            keeping — migrate its usage over, or merge the contracts/licenses so
            you&apos;re not paying two vendors for one capability. Typically a{" "}
            <b>phased change</b>, so the savings come from negotiation or a
            later retirement, not immediately. Use this when a tool is worth
            keeping but shouldn&apos;t exist <i>separately</i>.
          </p>
        </div>
        <div className="flex items-start gap-3">
          <StatusPill tone="danger">Cut</StatusPill>
          <p className="max-w-prose">
            The tool is <b>redundant, unused, or already replaced</b> — remove
            it outright. Its full annual cost is counted as savings. Use this
            when there&apos;s nothing to migrate, or a tool you&apos;re keeping
            already covers it.
          </p>
        </div>
        <p className="text-xs text-ink-tertiary">
          Example: three EDR tools in one category → mark the best one{" "}
          <b>Keep</b>, mark a bundled/legacy one you&apos;ll migrate off{" "}
          <b>Consolidate</b>, and mark a shelfware license nobody uses{" "}
          <b>Cut</b>.
        </p>
      </CardBody>
    </Card>
  );
}
