"use client";

import { signIn } from "next-auth/react";
import { useSearchParams } from "next/navigation";
import * as React from "react";

export function SignInForm(): JSX.Element {
  const searchParams = useSearchParams();
  const callbackUrl = searchParams.get("callbackUrl") ?? "/";
  // Post-signup, SignUpForm sends users here with ?registered=1 when the
  // automatic sign-in couldn't complete. Acknowledge the new account so the
  // page doesn't look like a bare, unexplained sign-in prompt.
  const justRegistered = searchParams.get("registered") === "1";

  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [pending, setPending] = React.useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      const result = await signIn("credentials", {
        email,
        password,
        redirect: false,
      });
      if (!result || result.error) {
        setError("Invalid email or password.");
        setPending(false);
        return;
      }
      // Full-page navigation (not router.replace) so the server re-renders the
      // header/nav with the new session cookie - a soft nav can serve the
      // cached logged-out tree and leave the nav stale until a manual refresh.
      window.location.assign(callbackUrl);
    } catch {
      setError("Something went wrong. Try again.");
      setPending(false);
    }
  }

  return (
    <form className="flex flex-col gap-5" onSubmit={onSubmit} noValidate>
      {justRegistered ? (
        <div
          role="status"
          className="rounded-md border border-status-success-border bg-status-success-bg px-3 py-2 text-sm text-status-success-fg"
        >
          Account created — sign in to continue.
        </div>
      ) : null}
      <div className="flex flex-col gap-1.5">
        <label htmlFor="email" className="text-sm font-medium text-ink-primary">
          Email
        </label>
        <input
          id="email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="rounded-md border border-border bg-surface-card px-3 py-2 text-sm text-ink-primary placeholder:text-ink-tertiary focus:border-border-focus focus:outline-none"
          placeholder="you@example.gov"
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <label
          htmlFor="password"
          className="text-sm font-medium text-ink-primary"
        >
          Password
        </label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          required
          minLength={1}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="rounded-md border border-border bg-surface-card px-3 py-2 text-sm text-ink-primary focus:border-border-focus focus:outline-none"
        />
      </div>
      {error ? (
        <div
          role="alert"
          className="rounded-md border border-status-danger-border bg-status-danger-bg px-3 py-2 text-sm text-status-danger-fg"
        >
          {error}
        </div>
      ) : null}
      <button
        type="submit"
        disabled={pending}
        className="rounded-md bg-brand-500 px-4 py-2.5 text-sm font-semibold text-ink-on-accent hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {pending ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
