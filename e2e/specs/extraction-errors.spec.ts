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
 * FIX C-1 / C-2 — extraction error surfaces on the Tech Debt dropzone.
 *
 * C-1: a header-only CSV (no data rows) must produce a clear error and must NOT
 *      mint a capability-list version.
 * C-2: a legacy .xls must produce the actionable "re-save as .xlsx" message,
 *      and the dropzone must no longer advertise `.xls` as an accepted format.
 *
 * Reached by clicking (Active Work → "Open" on the Tech Debt row). The empty
 * service is created via the API as setup only.
 */

async function openTechDebt(page: Page, title: string): Promise<void> {
  const row = page.getByRole("row", { name: new RegExp(escapeRegExp(title)) });
  await expect(row).toBeVisible();
  await row.getByRole("link", { name: /Open/ }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: "Tech Debt Review" }),
  ).toBeVisible();
}

test("Tech Debt extraction surfaces clear errors and mints no bogus list (FIX C-1/C-2)", async ({
  page,
  request,
}) => {
  const title = unique("E2E TechDebt extract");

  const token = await apiLogin(request, ADMIN_EMAIL, ADMIN_PASSWORD);
  const clientId = await resolveClientId(request, token, CLIENT_LEGAL_NAME);
  await createTechDebtService(request, token, clientId, title);

  await signIn(page, ADMIN_EMAIL, ADMIN_PASSWORD, "/admin/active");
  await openTechDebt(page, title);

  // A brand-new service has no capability list yet.
  await expect(page.getByText("No list yet")).toBeVisible();

  // The upload card's dropzone is the first file input on the page (it is
  // rendered before the supporting-documents panel).
  const fileInput = page.locator('input[type="file"]').first();

  // --- C-2 dropzone contract: `.xls` is no longer advertised --------------
  const accept = (await fileInput.getAttribute("accept")) ?? "";
  expect(accept).not.toBe("");
  const tokens = accept.split(",").map((t) => t.trim());
  expect(tokens).not.toContain(".xls"); // .xlsx is fine; a bare .xls is the bug
  expect(accept).toMatch(/\.xlsx/);

  // --- C-1: header-only CSV -> clear error, NO version created -------------
  await fileInput.setInputFiles({
    name: "inventory-header-only.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("name,vendor,category,annual_cost_usd\n"),
  });

  // The first extraction can be cold (heavy parser imports), so allow headroom.
  await expect(
    page.getByText(
      /No data rows found in this file; check that the inventory is on the first sheet with a header row\./,
    ),
  ).toBeVisible({ timeout: 30_000 });

  // No capability list version was minted by the failed extraction.
  await expect(page.getByText("No list yet")).toBeVisible();
  await expect(page.getByText(/Capability list v\d+/)).toHaveCount(0);

  // --- C-2: legacy .xls -> actionable "re-save as .xlsx" message ----------
  await fileInput.setInputFiles({
    name: "legacy-inventory.xls",
    mimeType: "application/vnd.ms-excel",
    buffer: Buffer.from("legacy binary xls contents"),
  });

  await expect(
    page.getByText(
      /Legacy \.xls is not supported; re-save the file as \.xlsx and upload again\./,
    ),
  ).toBeVisible();

  // Still no list after either failed upload.
  await expect(page.getByText("No list yet")).toBeVisible();
  await expect(page.getByText(/Capability list v\d+/)).toHaveCount(0);
});
