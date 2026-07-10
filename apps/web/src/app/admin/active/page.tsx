import type { Metadata } from "next";

import { ActiveWorkView } from "@/components/admin/ActiveWorkView";
import { Breadcrumbs } from "@/components/site/Breadcrumbs";

export const metadata: Metadata = { title: "Active Work" };

export default function ActiveWorkPage(): JSX.Element {
  return (
    <div className="flex flex-col gap-6">
      <Breadcrumbs items={[{ label: "Active Work" }]} />
      <div>
        <h1 className="text-2xl font-semibold text-ink-primary">Active Work</h1>
        <p className="mt-1 text-sm text-ink-secondary">
          Assessments currently in analysis across all clients. Open any
          workspace directly from here.
        </p>
      </div>
      <ActiveWorkView />
    </div>
  );
}
