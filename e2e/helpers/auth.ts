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

  const status = await page.evaluate(
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
      return res.status;
    },
    { email, password, callbackUrl },
  );
  if (status >= 400) {
    throw new Error(`credential sign-in POST failed for ${email}: ${status}`);
  }

  // Confirm a real session exists (deterministic; not a retry over flakiness).
  const session = (await page.evaluate(() =>
    fetch("/api/auth/session").then((r) => r.json()),
  )) as { user?: unknown };
  if (!session?.user) {
    throw new Error(
      `no session established for ${email} after credential sign-in`,
    );
  }

  // Land on the destination the way the form would, then confirm we are not
  // bounced back to the sign-in gate.
  await page.goto(callbackUrl);
  await expect(page).not.toHaveURL(/\/sign-in/);
}
