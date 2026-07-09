"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { ClientSwitcher } from "@/components/site/ClientSwitcher";
import { SignOutButton } from "@/components/site/SignOutButton";
import { SkipToContent } from "@/components/site/SkipToContent";

/**
 * Admin shell (Navigation_Spec §2): a persistent left sidebar so an admin can
 * jump anywhere from anywhere, a header carrying the signed-in identity, and a
 * skip-to-content link. The sidebar never disappears inside a workspace.
 */

interface NavItem {
  label: string;
  href: string;
}

const NAV: NavItem[] = [
  { label: "Intake Queue", href: "/admin/queue" },
  { label: "Active Work", href: "/admin/active" },
  { label: "Engagements", href: "/admin/engagements" },
  { label: "Risk Register", href: "/admin/risk-register" },
  { label: "Messages", href: "/admin/messages" },
  { label: "Users", href: "/admin/users" },
  { label: "Management", href: "/admin/management" },
];

export function AdminShell({
  email,
  children,
}: {
  email?: string | null;
  children: React.ReactNode;
}): JSX.Element {
  const pathname = usePathname();

  return (
    <div className="min-h-screen bg-surface-sunken">
      <SkipToContent />
      <div className="flex min-h-screen">
        <aside
          className="hidden w-60 shrink-0 flex-col border-r border-border-subtle bg-surface-card md:flex"
          aria-label="Admin navigation"
        >
          <Link
            href="/admin/queue"
            className="flex flex-col px-5 py-4 leading-tight"
          >
            <span className="text-lg font-semibold tracking-tight text-ink-primary">
              SHIELD
            </span>
            <span className="text-xs font-medium uppercase tracking-wider text-ink-tertiary">
              Admin console
            </span>
          </Link>
          <nav className="flex flex-col gap-1 px-3 py-2" aria-label="Primary">
            {NAV.map((item) => {
              const active =
                pathname === item.href || pathname.startsWith(`${item.href}/`);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={
                    "rounded-md px-3 py-2 text-sm font-medium " +
                    (active
                      ? "bg-brand-50 text-brand-600"
                      : "text-ink-secondary hover:bg-surface-sunken hover:text-ink-primary")
                  }
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex items-center justify-between border-b border-border-subtle bg-surface-card px-6 py-3">
            {/* Mobile-only inline nav so the sidebar's destinations stay reachable. */}
            <nav
              className="flex items-center gap-3 text-sm md:hidden"
              aria-label="Primary"
            >
              {NAV.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="font-medium text-ink-secondary hover:text-ink-primary"
                >
                  {item.label}
                </Link>
              ))}
            </nav>
            <div className="ml-auto flex items-center gap-3">
              <ClientSwitcher />
              <Link
                href="/admin/queue"
                className="rounded-md px-3 py-2 text-sm font-medium text-ink-secondary hover:text-ink-primary"
              >
                Home
              </Link>
              <Link
                href="/"
                className="rounded-md px-3 py-2 text-sm font-medium text-ink-secondary hover:text-ink-primary"
              >
                View public site
              </Link>
              {email ? (
                <span className="text-xs text-ink-tertiary">{email}</span>
              ) : null}
              <SignOutButton />
            </div>
          </header>
          <main
            id="main-content"
            className="mx-auto w-full max-w-6xl px-6 py-8"
          >
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}
