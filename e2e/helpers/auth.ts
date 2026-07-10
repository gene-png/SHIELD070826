/**
 * UI sign-in helper — real credential authentication, not cookie injection.
 *
 * It authenticates through NextAuth's OWN credential flow (GET /api/auth/csrf
 * then POST /api/auth/callback/credentials), which is exactly what the sign-in
 * form's button does under the hood: the backend runs `authorize()` →
 * `POST /auth/login` + `GET /auth/me` and sets the encrypted session cookie.
 * No session or tenant cookie is fabricated; the same email+password a user
 * types are verified by the API.
 *
 * WHY NOT click the button directly? The e2e web tier runs `next dev` (React
 * StrictMode), which double-fetches `/api/auth/csrf` on mount. The form's
 * client-side `signIn()` fetches csrf again at submit time, and the two fetches
 * race: the csrf COOKIE ends up holding a different token than the one posted
 * in the body, so NextAuth rejects the submit with `?csrf=true` and never sets
 * the session cookie. Observed directly: request cookie token `9b8b539a…` vs
 * POST body csrfToken `928901bd…`. The failure is intermittent, so clicking the
 * button is unavoidably flaky here. Fetching csrf and posting the callback in a
 * single atomic step removes the race and makes sign-in deterministic (verified
 * 6/6). This is a harness limitation of the dev server, not a product bug — the
 * app authenticates fine for a human at localhost:3000.
 *
 * Everything the specs actually PROVE (reaching workspaces, the client thread,
 * the admin switcher) is still done by clicking; only the credential handshake
 * is performed through NextAuth's real endpoints.
 *
 * Requires the web service to run with NEXTAUTH_URL matching the browser's
 * origin (http://web:3000 in the compose network). See playwright.config.ts and
 * e2e/README.md — without it, NextAuth rejects the callback for every user.
 */
import { expect, type Page } from "@playwright/test";

export async function signIn(
  page: Page,
  email: string,
  password: string,
  callbackUrl = "/",
): Promise<void> {
  // Visit the real sign-in page (establishes the origin; also what a user sees).
  await page.goto("/sign-in");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();

  // Perform NextAuth's csrf -> callback handshake and report whether a real
  // session resulted. Returns the callback status + whether /api/auth/session
  // now carries a user.
  async function attemptSignIn(): Promise<{
    status: number;
    hasUser: boolean;
  }> {
    return page.evaluate(
      async ({ email, password, callbackUrl }) => {
        const csrf = await fetch("/api/auth/csrf").then((r) => r.json());
        const body = new URLSearchParams({
          email,
          password,
          csrfToken: csrf.csrfToken,
          callbackUrl,
          json: "true",
        });
        const res = await fetch("/api/auth/callback/credentials", {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: body.toString(),
        });
        const session = await fetch("/api/auth/session")
          .then((r) => r.json())
          .catch(() => ({}));
        return {
          status: res.status,
          hasUser: Boolean((session as { user?: unknown })?.user),
        };
      },
      { email, password, callbackUrl },
    );
  }

  // Retry the WHOLE handshake, not just the session read. On a COLD next-dev
  // stack (every CI run, and the first sign-in of any local run) the very first
  // hit compiles the NextAuth routes, and the csrf cookie set by GET
  // /api/auth/csrf can lose a race with the token posted to the callback — the
  // callback then returns 200 but sets no session cookie, so no amount of
  // re-reading the session helps. A fresh handshake (new csrf token + cookie)
  // wins once the routes are warm. This retries a KNOWN-flaky auth handshake
  // against a dev server, bounded to 3 tries; a genuinely bad credential fails
  // every attempt and still throws below. (The real cure is a production build —
  // no first-hit compile — tracked as the §8.6 follow-up.)
  let last = { status: 0, hasUser: false };
  for (let attempt = 0; attempt < 3; attempt++) {
    last = await attemptSignIn();
    if (last.hasUser) break;
    await page.waitForTimeout(1_000);
  }
  if (!last.hasUser) {
    throw new Error(
      `no session established for ${email} after 3 credential sign-in attempts ` +
        `(last callback status ${last.status})`,
    );
  }

  // Land on the destination the way the form would, then confirm we are not
  // bounced back to the sign-in gate.
  await page.goto(callbackUrl);
  await expect(page).not.toHaveURL(/\/sign-in/);
}
