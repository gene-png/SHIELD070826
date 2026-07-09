"use client";

import * as React from "react";

import {
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  StatusPill,
} from "@shield/design-system";

import {
  createUser,
  deactivateUser,
  listClients,
  listUsers,
  reactivateUser,
  type ClientSummary,
} from "@/lib/admin/client";
import type { AdminUserDetail } from "@/lib/admin/types";

export function UsersView(): JSX.Element {
  const [users, setUsers] = React.useState<AdminUserDetail[] | null>(null);
  const [clients, setClients] = React.useState<ClientSummary[]>([]);
  const [error, setError] = React.useState<string | null>(null);
  const [busyId, setBusyId] = React.useState<string | null>(null);

  // New-user form state.
  const [email, setEmail] = React.useState("");
  const [name, setName] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [role, setRole] = React.useState<"admin" | "client">("client");
  const [clientId, setClientId] = React.useState("");
  const [creating, setCreating] = React.useState(false);
  const [formError, setFormError] = React.useState<string | null>(null);

  const reload = React.useCallback(async () => {
    try {
      const [u, c] = await Promise.all([listUsers(), listClients()]);
      setUsers(u);
      setClients(c);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load users.");
    }
  }, []);

  React.useEffect(() => {
    void reload();
  }, [reload]);

  async function onCreate(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    setFormError(null);
    if (role === "client" && !clientId) {
      setFormError("Pick a client tenant for a client user.");
      return;
    }
    setCreating(true);
    try {
      await createUser({
        email: email.trim(),
        password,
        display_name: name.trim(),
        role,
        client_id: role === "client" ? clientId : null,
      });
      setEmail("");
      setName("");
      setPassword("");
      setRole("client");
      setClientId("");
      await reload();
    } catch (err) {
      setFormError(
        err instanceof Error ? err.message : "Failed to create user.",
      );
    } finally {
      setCreating(false);
    }
  }

  async function onToggleActive(u: AdminUserDetail): Promise<void> {
    const verb = u.is_active ? "deactivate" : "reactivate";
    if (
      u.is_active &&
      !window.confirm(
        `Deactivate ${u.email}? They will be unable to sign in. ` +
          `Deactivated accounts are permanently purged after 365 days of no login.`,
      )
    ) {
      return;
    }
    setBusyId(u.id);
    setError(null);
    try {
      if (u.is_active) {
        await deactivateUser(u.id);
      } else {
        await reactivateUser(u.id);
      }
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to ${verb} user.`);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>Create a user</CardTitle>
        </CardHeader>
        <CardBody>
          <form
            onSubmit={(e) => void onCreate(e)}
            className="flex flex-col gap-3"
          >
            <div className="flex flex-wrap gap-3">
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Full name"
                aria-label="Full name"
                className="min-w-[12rem] flex-1 rounded-md border border-border bg-surface-card px-3 py-2 text-sm"
              />
              <input
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                type="email"
                placeholder="email@company.com"
                aria-label="Email"
                className="min-w-[14rem] flex-1 rounded-md border border-border bg-surface-card px-3 py-2 text-sm"
              />
            </div>
            <div className="flex flex-wrap gap-3">
              <input
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                type="password"
                placeholder="Temp password (12+ chars)"
                aria-label="Password"
                minLength={12}
                className="min-w-[14rem] flex-1 rounded-md border border-border bg-surface-card px-3 py-2 text-sm"
              />
              <select
                value={role}
                onChange={(e) => setRole(e.target.value as "admin" | "client")}
                aria-label="Role"
                className="rounded-md border border-border bg-surface-card px-3 py-2 text-sm"
              >
                <option value="client">Client</option>
                <option value="admin">Admin</option>
              </select>
              {role === "client" ? (
                <select
                  value={clientId}
                  onChange={(e) => setClientId(e.target.value)}
                  aria-label="Client tenant"
                  className="min-w-[12rem] rounded-md border border-border bg-surface-card px-3 py-2 text-sm"
                >
                  <option value="">Select client…</option>
                  {clients.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.legal_name}
                    </option>
                  ))}
                </select>
              ) : null}
            </div>
            <div className="flex items-center gap-3">
              <button
                type="submit"
                disabled={
                  creating ||
                  !email.trim() ||
                  !name.trim() ||
                  password.length < 12
                }
                className="rounded-md bg-brand-500 px-4 py-2 text-sm font-semibold text-ink-on-accent hover:bg-brand-600 disabled:opacity-60"
              >
                {creating ? "Creating…" : "Create user"}
              </button>
              {role === "admin" ? (
                <span className="text-xs text-ink-tertiary">
                  Admins are cross-tenant. Only admins can create other admins.
                </span>
              ) : null}
            </div>
            {formError ? (
              <p className="text-sm text-status-danger-fg" role="alert">
                {formError}
              </p>
            ) : null}
          </form>
        </CardBody>
      </Card>

      {error ? (
        <p className="text-sm text-status-danger-fg" role="alert">
          {error}
        </p>
      ) : null}

      {users === null ? (
        <p className="text-sm text-ink-tertiary">Loading users…</p>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>All accounts ({users.length})</CardTitle>
          </CardHeader>
          <CardBody className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-border-subtle text-xs uppercase tracking-wider text-ink-tertiary">
                  <th className="py-2 pr-4 font-medium">User</th>
                  <th className="py-2 pr-4 font-medium">Role</th>
                  <th className="py-2 pr-4 font-medium">Status</th>
                  <th className="py-2 pr-4 font-medium">Last login</th>
                  <th className="py-2 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr
                    key={u.id}
                    className="border-b border-border-subtle last:border-b-0"
                  >
                    <td className="py-2 pr-4">
                      <div className="font-medium text-ink-primary">
                        {u.display_name ?? "—"}
                      </div>
                      <div className="text-xs text-ink-tertiary">{u.email}</div>
                    </td>
                    <td className="py-2 pr-4">
                      <StatusPill
                        tone={u.role === "admin" ? "info" : "neutral"}
                      >
                        {u.role}
                      </StatusPill>
                    </td>
                    <td className="py-2 pr-4">
                      <StatusPill
                        tone={u.is_active ? "success" : "warning"}
                        withDot
                      >
                        {u.is_active ? "Active" : "Deactivated"}
                      </StatusPill>
                    </td>
                    <td className="py-2 pr-4 text-ink-secondary">
                      {u.last_login_at
                        ? new Date(u.last_login_at).toLocaleDateString()
                        : "Never"}
                    </td>
                    <td className="py-2">
                      <button
                        type="button"
                        onClick={() => void onToggleActive(u)}
                        disabled={busyId === u.id}
                        className={
                          "rounded-md border px-3 py-1 text-sm font-medium disabled:opacity-60 " +
                          (u.is_active
                            ? "border-status-danger-border text-status-danger-fg hover:bg-status-danger-bg"
                            : "border-border text-ink-primary hover:bg-surface-sunken")
                        }
                      >
                        {u.is_active ? "Deactivate" : "Reactivate"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardBody>
        </Card>
      )}
    </div>
  );
}
