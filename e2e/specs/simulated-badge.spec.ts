import { test, expect, type Page } from "@playwright/test";

import {
  apiLogin,
  createTechDebtService,
  resolveClientId,
} from "../helpers/api";
import { signIn } from "../helpers/auth";
import {
  ADMIN_EMAIL,
  ADMIN_PASSWORD,
  CLIENT_LEGAL_NAME,
  escapeRegExp,
  unique,
} from "../helpers/env";

/**
 * FIX E-5 — fixture-mode AI is presented as *simulated*, never "disabled".
 *
 * E-5 has two surfaces:
 *   (a) the AI status banner, which must tell the user that suggestions are
 *       "simulated" (deterministic fixtures), NOT that AI is "disabled"; and
 *   (b) a "Simulated" badge rendered next to AI-generated suggestions after a
 *       Run-AI pass in fixture mode.
 *
 * This spec proves surface (a) end-to-end through the running app: it opens a
 * Tech Debt workspace (the only workspace that renders the AI status banner)
 * and asserts the banner says suggestions are simulated and does NOT say
 * "disabled". Reverting E-5's copy (the pre-fix wording was "Running in fixture
 * mode — AI features are disabled") flips both assertions, so the test is not
 * vacuous.
 *
 * Surface (b), the Run-AI "Simulated" badge, is intentionally NOT asserted here
 * because it is UNREACHABLE in the running application: in fixture mode every
 * Run-AI/extract job 500s with `KeyError: No fixture registered for purpose=…`.
 * The deployed fixture provider (app/ai/llm.py `_build_provider`, which returns
 * a bare `FixtureProvider()`) has no per-purpose fixture responses registered —
 * only the test suite registers them via `FixtureProvider.register(...)`. So a
 * user can never see the badge by clicking. This is a product gap, reported to
 * the caller rather than worked around; a test that had to change the product
 * to go green would not be evidence.
 */

async function openTechDebt(page: Page, title: string): Promise<void> {
  const row = page.getByRole("row", { name: new RegExp(escapeRegExp(title)) });
  await expect(row).toBeVisible();
  await row.getByRole("link", { name: /Open/ }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: "Tech Debt Review" }),
  ).toBeVisible();
}

test("Fixture-mode AI status reads 'simulated', not 'disabled' (FIX E-5)", async ({
  page,
  request,
}) => {
  const title = unique("E2E AI status");

  const token = await apiLogin(request, ADMIN_EMAIL, ADMIN_PASSWORD);
  const clientId = await resolveClientId(request, token, CLIENT_LEGAL_NAME);
  await createTechDebtService(request, token, clientId, title);

  await signIn(page, ADMIN_EMAIL, ADMIN_PASSWORD, "/admin/active");
  await openTechDebt(page, title);

  const banner = page.getByRole("status").filter({ hasText: "AI suggestions" });
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("AI suggestions are simulated.");
  // The whole point of E-5: it must NOT call fixture output "disabled".
  await expect(banner).not.toContainText(/disabled/i);
});
