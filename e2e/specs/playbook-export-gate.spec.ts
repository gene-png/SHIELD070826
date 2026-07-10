import { test, expect, type Page } from "@playwright/test";

import { apiLogin, createCsfService, resolveClientId } from "../helpers/api";
import { signIn } from "../helpers/auth";
import {
  ADMIN_EMAIL,
  ADMIN_PASSWORD,
  CLIENT_LEGAL_NAME,
  escapeRegExp,
  unique,
} from "../helpers/env";

/**
 * FIX B-3 — the Playbook export gate.
 *
 * Before B-3, two clicks ("Seed Working Profiles" then "Export") produced a
 * professional five-artifact deliverable asserting Level 1 maturity for all 106
 * subcategories before anyone had assessed anything: seeding creates every
 * dimension row at 0, which the maturity math reads as a legitimate "Level 1".
 *
 * This spec seeds the Working Profiles and clicks Export immediately, and
 * asserts the export is BLOCKED with a message naming how many in-scope rows are
 * still unscored — and that NO deliverable link is produced. If the gate were
 * removed, the export would silently mint the artifacts and both assertions
 * would fail, so the test is not vacuous.
 *
 * The workspace is reached by CLICKING (Active Work → "Open"), never by
 * navigating to the service id. The empty service is created via the API only
 * as test setup, the same way `seed_demo.py` sets up state.
 */

async function openWorkspaceFromActiveWork(
  page: Page,
  title: string,
): Promise<void> {
  const row = page.getByRole("row", { name: new RegExp(escapeRegExp(title)) });
  await expect(row).toBeVisible();
  await row.getByRole("link", { name: /Open/ }).click();
  // EnsureActiveClient aligns the tenant, then the workspace renders its H1.
  await expect(
    page.getByRole("heading", { level: 1, name: "NIST CSF 2.0 Assessment" }),
  ).toBeVisible();
}

test("Playbook export is blocked until every in-scope row is scored (FIX B-3)", async ({
  page,
  request,
}) => {
  const title = unique("E2E CSF export-gate");

  // --- setup: an empty, editable CSF service under the tenant -------------
  const token = await apiLogin(request, ADMIN_EMAIL, ADMIN_PASSWORD);
  const clientId = await resolveClientId(request, token, CLIENT_LEGAL_NAME);
  await createCsfService(request, token, clientId, title);

  // --- reach the workspace by clicking ------------------------------------
  await signIn(page, ADMIN_EMAIL, ADMIN_PASSWORD, "/admin/active");
  await openWorkspaceFromActiveWork(page, title);

  // Start the assessment (a fresh draft).
  await page.getByRole("button", { name: "Start assessment" }).click();

  // Wait for the Playbook panel to finish its initial (empty) profile load
  // before seeding — otherwise the in-flight fetch can resolve after the seed
  // and overwrite the freshly-seeded rows back to empty.
  await expect(
    page.getByText(/Seed the Working Profiles to score/),
  ).toBeVisible();
  await page.getByRole("button", { name: /Seed Working Profiles/ }).click();

  // The seed succeeded once Export is offered (first seed can be cold).
  const exportBtn = page.getByRole("button", { name: "Export XLSX" });
  await expect(exportBtn).toBeVisible({ timeout: 30_000 });

  // Click Export immediately — before scoring anything.
  await exportBtn.click();

  // Blocked, and the message names the number of unscored in-scope rows.
  await expect(
    page.getByText(/\d+ in-scope subcategory row\(s\) are still unscored/),
  ).toBeVisible();

  // Non-vacuity: the blocked export produced NO deliverable download links.
  // (A passing gate renders links labelled "Data workbook (XLSX)",
  // "Executive briefing (PDF)", etc.)
  await expect(
    page.getByRole("link", { name: /Data workbook \(XLSX\)/ }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: /Executive briefing/ }),
  ).toHaveCount(0);
});
