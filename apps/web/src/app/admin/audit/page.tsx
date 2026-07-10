import type { Metadata } from "next";

import { AuditLogView } from "@/components/admin/AuditLogView";
import { Breadcrumbs } from "@/components/site/Breadcrumbs";

export const metadata: Metadata = { title: "Audit Log" };

export default function AuditLogPage(): JSX.Element {
  return (
    <div className="flex flex-col gap-6">
      <Breadcrumbs items={[{ label: "Audit Log" }]} />
      <div>
        <h1 className="text-2xl font-semibold text-ink-primary">Audit Log</h1>
        <p className="mt-1 text-sm text-ink-secondary">
          The platform&apos;s append-only audit trail: every state-changing
          action, across all clients. Read-only — filter, page, and export to
          CSV.
        </p>
      </div>
      <AuditLogView />
    </div>
  );
}
