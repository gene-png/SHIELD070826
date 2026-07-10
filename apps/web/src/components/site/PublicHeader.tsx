import { getServerSession } from "next-auth";
import Link from "next/link";

import { ApiError, apiFetch } from "@/lib/api";
import { authOptions } from "@/lib/auth/options";
import { ClientSwitcher } from "@/components/site/ClientSwitcher";
import { SignOutButton } from "@/components/site/SignOutButton";

export async function PublicHeader(): Promise<JSX.Element> {
  const session = await getServerSession(authOptions);
  const role = session?.role;
  // Role-aware home: admins land on their console, everyone else on the site.
  const homeHref = role === "admin" ? "/admin/queue" : "/";

  // Surface an Intake link for a signed-in client whose intake isn't finished
  // yet. Without it, /intake is reachable only from the marketing hero and a
  // post-signup redirect, stranding anyone who navigates away mid-intake.
  let intakeIncomplete = false;
  if (session && role !== "admin" && session.accessToken) {
    try {
      const intake = await apiFetch<{ intake_completed_at: string | null }>(
        "/intake",
        { bearer: session.accessToken },
      );
      intakeIncomplete = !intake.intake_completed_at;
    } catch (err) {
      // A failed lookup must never break the header; just hide the link. A 401
      // means the token lapsed and the user will be bounced to sign-in anyway.
      if (!(err instanceof ApiError)) {
        intakeIncomplete = false;
      }
    }
  }

  return (
    <header className="border-b border-border-subtle bg-surface-card">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
        <Link href={homeHref} className="flex flex-col leading-tight">
          <span className="text-lg font-semibold tracking-tight text-ink-primary">
            SHIELD
          </span>
          <span className="text-xs font-medium uppercase tracking-wider text-ink-tertiary">
            by Kentro
          </span>
        </Link>
        <nav aria-label="Primary" className="flex items-center gap-4 text-sm">
          <Link
            href={homeHref}
            className="rounded-md px-3 py-2 font-medium text-ink-secondary hover:text-ink-primary"
          >
            Home
          </Link>
          {session ? (
            <>
              {intakeIncomplete ? (
                <Link
                  href="/intake"
                  className="rounded-md px-3 py-2 font-medium text-brand-500 hover:text-brand-600"
                >
                  Finish intake
                </Link>
              ) : null}
              <Link
                href="/assessments"
                className="rounded-md px-3 py-2 font-medium text-ink-secondary hover:text-ink-primary"
              >
                My Assessments
              </Link>
              <Link
                href="/messages"
                className="rounded-md px-3 py-2 font-medium text-ink-secondary hover:text-ink-primary"
              >
                Messages
              </Link>
              <Link
                href="/account"
                className="rounded-md px-3 py-2 font-medium text-ink-secondary hover:text-ink-primary"
              >
                Account
              </Link>
              {role === "admin" ? (
                <Link
                  href="/admin/queue"
                  className="rounded-md px-3 py-2 font-medium text-ink-secondary hover:text-ink-primary"
                >
                  Admin
                </Link>
              ) : null}
              {role === "admin" ? <ClientSwitcher /> : null}
              <SignOutButton />
            </>
          ) : (
            <>
              <Link
                href="/sign-in"
                className="rounded-md px-3 py-2 font-medium text-ink-secondary hover:text-ink-primary"
              >
                Sign in
              </Link>
              <Link
                href="/sign-up"
                className="rounded-md bg-brand-500 px-3 py-2 font-semibold text-ink-on-accent hover:bg-brand-600"
              >
                Get started
              </Link>
            </>
          )}
        </nav>
      </div>
    </header>
  );
}
