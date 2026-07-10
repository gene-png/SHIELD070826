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
 * FIX B-3 — the Playbook export gate (both directions).
 *
 * Before B-3, two clicks ("Seed Working Profiles" then "Export") produced a
 * professional five-artifact deliverable asserting Level 1 maturity for all 106
 * subcategories before anyone had assessed anything: seeding creates every
 * dimension row at 0, which the maturity math reads as a legitimate "Level 1".
 *
 * Test 1 proves the gate BLOCKS: seed, Export immediately, and assert the export
 * is refused with a message naming how many in-scope rows are still unscored,
 * and that NO deliverable link is produced.
 *
 * Test 2 proves the gate OPENS on the legitimate path: seed, Run AI (which
 * scores every in-scope row — reachable only since X-8 made fixture-mode AI
 * work), approve the assessment, then Export succeeds and the deliverable links
 * appear. It doubles as the end-to-end proof of X-8 and of E-5's "Simulated"
 * badge (surface b): the badge renders next to the Run-AI result in fixture
 * mode. Both halves matter — a gate that only ever blocks is as useless as one
 * that never does.
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

test("Export succeeds once Run-AI scores every row and the assessment is approved (FIX B-3 happy path; also X-8 + E-5 badge)", async ({
  page,
  request,
}) => {
  // This is the heaviest flow in the suite and it needs its own budget. On a
  // COLD stack it cold-compiles two routes the other specs never reach — the
  // csf_score Run-AI job and the full five-artifact Playbook export render —
  // on top of running the AI over every in-scope subcategory. Measured ~125s
  // cold (vs ~40s warm), so the default 120s test timeout is just too tight.
  // 240s is ~2x the observed cold cost; a real hang still fails at an action
  // timeout long before it.
  test.setTimeout(240_000);
  const title = unique("E2E CSF export-ok");

  const token = await apiLogin(request, ADMIN_EMAIL, ADMIN_PASSWORD);
  const clientId = await resolveClientId(request, token, CLIENT_LEGAL_NAME);
  await createCsfService(request, token, clientId, title);

  await signIn(page, ADMIN_EMAIL, ADMIN_PASSWORD, "/admin/active");
  await openWorkspaceFromActiveWork(page, title);

  await page.getByRole("button", { name: "Start assessment" }).click();
  await expect(
    page.getByText(/Seed the Working Profiles to score/),
  ).toBeVisible();
  await page.getByRole("button", { name: /Seed Working Profiles/ }).click();
  await expect(page.getByRole("button", { name: "Export XLSX" })).toBeVisible({
    timeout: 30_000,
  });

  // Run AI. In fixture mode this now returns grounded suggestions for every
  // in-scope subcategory (X-8) and stamps each row scored. Before X-8 this 500'd.
  await page.getByRole("button", { name: "Run AI (csf_score)" }).click();
  // The result line reports how many fields the AI wrote...
  await expect(page.getByText(/AI updated/)).toBeVisible({ timeout: 90_000 });
  // ...and — because we are in fixture mode — carries the "Simulated" badge, so
  // a consultant can never mistake the draft for real analysis (E-5 surface b).
  await expect(page.getByText("Simulated", { exact: true })).toBeVisible();

  // Approve the assessment (the second export precondition beyond scoring). The
  // label is "Approve" or "Approve client inputs"; the regex excludes the
  // post-approval "Approved" state.
  await page.getByRole("button", { name: /Approve( client inputs)?$/ }).click();

  // WAIT for the approval to commit before exporting. `.click()` returns when
  // the click dispatches, not when the approve request finishes; on a cold
  // stack the approve route compile delays the commit, so a straight-to-export
  // click races ahead and the server 409s ("must be approved before
  // exporting"). The button flips to a disabled "Approved" once it lands — that
  // is the real signal the export gate's approval precondition is satisfied.
  await expect(page.getByRole("button", { name: "Approved" })).toBeVisible();

  // Now the gate opens: Export produces the real deliverable links. Export is
  // the heaviest route (it renders five artifacts — XLSX, PDF, two Word, HTML),
  // and on a fresh CI stack this is its first, cold-compiled hit, so it gets the
  // most headroom of any assertion in the suite.
  await page.getByRole("button", { name: "Export XLSX" }).click();
  await expect(
    page.getByRole("link", { name: /Data workbook \(XLSX\)/ }),
  ).toBeVisible({ timeout: 90_000 });
});
