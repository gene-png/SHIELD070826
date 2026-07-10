"use client";

import * as React from "react";
import Link from "next/link";

import {
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  EmptyState,
  StatusPill,
} from "@shield/design-system";

import {
  listClients,
  listServices,
  type ClientSummary,
} from "@/lib/admin/client";
import { workspaceHref, type AdminServiceRow } from "@/lib/admin/types";
import { SERVICE_LABELS } from "@/lib/intake/types";

function statusTone(
  status: string,
): "info" | "success" | "warning" | "neutral" {
  if (status === "review") return "warning";
  if (status === "in_progress") return "info";
  return "neutral";
}

/**
 * Cross-client index of in-progress work. Every non-archived service is an
 * open workspace the consultant may need to jump into; this doubles as the
 * workspace index the admin console otherwise lacked. Built entirely from the
 * existing admin listings (services + clients) - no new backend surface.
 */
export function ActiveWorkView(): JSX.Element {
  const [services, setServices] = React.useState<AdminServiceRow[] | null>(
    null,
  );
  const [clients, setClients] = React.useState<ClientSummary[]>([]);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let active = true;
    Promise.all([listServices(false), listClients()])
      .then(([svcs, cls]) => {
        if (!active) return;
        setServices(svcs);
        setClients(cls);
      })
      .catch((err) => {
        if (active) {
          setError(
            err instanceof Error ? err.message : "Failed to load active work.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const clientName = React.useCallback(
    (id: string): string =>
      clients.find((c) => c.id === id)?.legal_name ?? "Unknown client",
    [clients],
  );

  return (
    <div className="flex flex-col gap-4">
      {error ? (
        <p className="text-sm text-status-danger-fg" role="alert">
          {error}
        </p>
      ) : services === null ? (
        <p className="text-sm text-ink-tertiary">Loading active work…</p>
      ) : services.length === 0 ? (
        <EmptyState
          title="Nothing in progress"
          description="Publish a request from the intake queue, or wait for a client to start a self-assessment, and it will appear here."
        />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>In progress ({services.length})</CardTitle>
          </CardHeader>
          <CardBody className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-border-subtle text-xs uppercase tracking-wider text-ink-tertiary">
                  <th className="py-2 pr-4 font-medium">Client</th>
                  <th className="py-2 pr-4 font-medium">Engagement</th>
                  <th className="py-2 pr-4 font-medium">Type</th>
                  <th className="py-2 pr-4 font-medium">Status</th>
                  <th className="py-2 font-medium">Workspace</th>
                </tr>
              </thead>
              <tbody>
                {services.map((s) => {
                  const href = workspaceHref(s.kind, s.id);
                  return (
                    <tr
                      key={s.id}
                      className="border-b border-border-subtle last:border-b-0"
                    >
                      <td className="py-2 pr-4 text-ink-secondary">
                        {clientName(s.client_id)}
                      </td>
                      <td className="py-2 pr-4 font-medium text-ink-primary">
                        {s.title}
                      </td>
                      <td className="py-2 pr-4 text-ink-secondary">
                        {SERVICE_LABELS[s.kind] ?? s.kind}
                      </td>
                      <td className="py-2 pr-4">
                        <StatusPill tone={statusTone(s.status)} withDot>
                          {s.status}
                        </StatusPill>
                      </td>
                      <td className="py-2">
                        {href ? (
                          <Link
                            href={href}
                            className="text-sm font-semibold text-brand-500 hover:text-brand-600"
                          >
                            Open →
                          </Link>
                        ) : (
                          <span className="text-xs text-ink-tertiary">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </CardBody>
        </Card>
      )}
    </div>
  );
}
