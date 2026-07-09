"use client";

export interface MessageRow {
  id: string;
  service_id: string;
  author_user_id: string | null;
  author_role: string | null;
  body: string;
  created_at: string;
  read_at: string | null;
}

export interface MessageList {
  messages: MessageRow[];
}

export interface InboxThread {
  service_id: string;
  service_title: string;
  service_kind: string;
  total: number;
  unread: number;
  last_preview: string | null;
  last_at: string | null;
}

export interface InboxResponse {
  threads: InboxThread[];
  unread_total: number;
}

export class MessagesProxyError extends Error {
  constructor(
    public readonly status: number,
    public readonly payload: unknown,
  ) {
    super(`Messages proxy ${status}`);
  }
}

async function jsonRequest<T>(
  url: string,
  init: { method?: "GET" | "POST"; body?: unknown } = {},
): Promise<T> {
  const res = await fetch(url, {
    method: init.method ?? "GET",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      ...(init.body !== undefined
        ? { "Content-Type": "application/json" }
        : {}),
    },
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  if (!res.ok) {
    let payload: unknown;
    try {
      payload = await res.json();
    } catch {
      payload = await res.text();
    }
    throw new MessagesProxyError(res.status, payload);
  }
  return (await res.json()) as T;
}

export async function fetchMessages(serviceId: string): Promise<MessageList> {
  return jsonRequest<MessageList>(`/api/proxy/services/${serviceId}/messages`);
}

export async function postMessage(
  serviceId: string,
  body: string,
): Promise<MessageRow> {
  return jsonRequest<MessageRow>(`/api/proxy/services/${serviceId}/messages`, {
    method: "POST",
    body: { body },
  });
}

export async function fetchInbox(): Promise<InboxResponse> {
  return jsonRequest<InboxResponse>("/api/proxy/messages/inbox");
}

export function describeMessagesError(err: unknown): string {
  if (err instanceof MessagesProxyError) {
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
