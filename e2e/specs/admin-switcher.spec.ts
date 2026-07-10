import { test, expect } from "@playwright/test";

import { signIn } from "../helpers/auth";
import { ADMIN_EMAIL, ADMIN_PASSWORD, CLIENT_LEGAL_NAME } from "../helpers/env";

/**
 * FIX D-2 — the admin client switcher gates a tenant-scoped page.
 *
 * The Risk Register is generated per client. From a FRESH admin session with no
 * active-client cookie, the page must show a "pick a client" gate, and the
 * admin must be able to pick a client using the visible switcher in the admin
 * header — after which the gated page loads. Crucially, the active client is
 * chosen through the UI, never set via the API (that is what hid this bug).
 *
 * Each Playwright test gets its own fresh browser context, so there is no
 * active-client cookie to start with — exactly the "fresh admin session" the
 * fix is about.
 */

test("Admin reaches the Risk Register by picking a client in the header switcher (FIX D-2)", async ({
  page,
}) => {
  // Sign in and reach the Risk Register by CLICKING the nav — no goto to a
  // tenant-scoped deep link, no cookie injection.
  await signIn(page, ADMIN_EMAIL, ADMIN_PASSWORD, "/admin/queue");
  await page.getByRole("link", { name: "Risk Register" }).first().click();
  await page.waitForURL(/\/admin\/risk-register/);

  // With no client selected, the page is gated.
  await expect(
    page.getByRole("heading", { name: "Pick a client first" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { level: 1, name: "Risk Register" }),
  ).toHaveCount(0);

  // Non-vacuity: the switcher lives in the admin header. If it were removed the
  // admin could not select a tenant and this locator would fail.
  const switcher = page.locator("header").getByLabel("Active client");
  await expect(switcher).toBeVisible();

  // Pick the client through the visible UI control. This posts to
  // /api/active-client and sets the active-client cookie via the UI — NOT via
  // the API, which is what hid this bug. (The dashboard reads the active client
  // on mount, so the register renders for the picked client on the next render;
  // reloading the same URL surfaces it without changing the selected tenant.)
  await switcher.selectOption({ label: `Atlas (${CLIENT_LEGAL_NAME})` });
  await expect(switcher).toHaveValue(/.+/); // a real client id is now selected
  await page.reload();

  // The gate clears and the client-scoped Risk Register loads.
  await expect(
    page.getByRole("heading", { level: 1, name: "Risk Register" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Pick a client first" }),
  ).toHaveCount(0);
});
