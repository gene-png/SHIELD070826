"use client";

import * as React from "react";
import Link from "next/link";

import { Card, CardBody, EmptyState } from "@shield/design-system";

import { ClientSwitcher } from "@/components/site/ClientSwitcher";
import { workspaceHref } from "@/lib/admin/types";
import { SERVICE_LABELS, type ServiceType } from "@/lib/intake/types";
import {
  describeMessagesError,
  fetchInbox,
  MessagesProxyError,
  type InboxThread,
} from "@/lib/messages/client";

function fmtTime(value: string | null): string {
  if (!value) return "";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function ThreadRow({ thread }: { thread: InboxThread }): JSX.Element {
  const kind = thread.service_kind as ServiceType;
  const href = workspaceHref(kind, thread.service_id);
  const label = SERVICE_LABELS[kind] ?? thread.service_kind;
  const inner = (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium text-ink-primary">
            {thread.service_title}
          </span>
          {thread.unread > 0 ? (
            <span className="rounded-full bg-brand-500 px-2 py-0.5 text-xs font-semibold text-ink-on-accent">
              {thread.unread} new
            </span>
          ) : null}
        </div>
        <p className="truncate text-sm text-ink-secondary">
          {label} · {thread.last_preview ?? "No messages"}
        </p>
      </div>
      <span className="shrink-0 text-xs text-ink-tertiary">
        {fmtTime(thread.last_at)}
      </span>
    </div>
  );
  return (
    <Card>
      <CardBody>
        {href ? (
          <Link href={href} className="block hover:opacity-80">
            {inner}
          </Link>
        ) : (
          inner
        )}
      </CardBody>
    </Card>
  );
}

export function InboxView(): JSX.Element {
  const [threads, setThreads] = React.useState<InboxThread[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [needsClient, setNeedsClient] = React.useState(false);

  React.useEffect(() => {
    let active = true;
    fetchInbox()
      .then((r) => {
        if (active) setThreads(r.threads);
      })
      .catch((err) => {
        if (!active) return;
        // No active client selected: the backend requires X-Client-Id for this
        // role and returns 400. Show the switcher instead of the raw error.
        if (err instanceof MessagesProxyError && err.status === 400) {
          setNeedsClient(true);
          return;
        }
        setError(describeMessagesError(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  if (loading) return <p className="text-sm text-ink-tertiary">Loading…</p>;
  if (needsClient)
    return (
      <EmptyState
        title="Pick a client first"
        description="Messages are scoped to one client. Choose one to see their threads:"
        action={<ClientSwitcher />}
      />
    );
  if (error)
    return (
      <p className="text-sm text-status-danger-fg" role="alert">
        {error}
      </p>
    );
  if (threads.length === 0)
    return (
      <EmptyState
        title="No message threads"
        description="Threads appear here once a service for the selected client has a conversation."
      />
    );

  return (
    <div className="flex flex-col gap-3">
      {threads.map((t) => (
        <ThreadRow key={t.service_id} thread={t} />
      ))}
    </div>
  );
}
