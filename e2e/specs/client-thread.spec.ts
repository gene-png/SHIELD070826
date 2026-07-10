import { test, expect, type Page } from "@playwright/test";

import { signIn } from "../helpers/auth";
import {
  ADMIN_EMAIL,
  ADMIN_PASSWORD,
  CLIENT_EMAIL,
  CLIENT_PASSWORD,
  escapeRegExp,
  unique,
} from "../helpers/env";

/**
 * FIX D-1 — the client can reach an admin's reply by clicking.
 *
 * Before D-1, a submitted self-assessment card on "My Assessments" became a
 * bare status pill with no link, so admin replies on the thread were unseen.
 * The old e2e suite sidestepped exactly this by `page.goto`-ing the deep URL
 * with an API-resolved id.
 *
 * This spec is entirely click-driven: the client STARTS and SUBMITS a real
 * self-assessment, an admin replies on the thread from the workspace, and the
 * client returns to "My Assessments" and CLICKS the card to read the reply.
 * If the card were a link-less pill again, the final click would find no link
 * and the test would fail.
 */

async function startCsfSelfAssessment(page: Page, name: string): Promise<void> {
  await page.getByRole("button", { name: "+ Start a new assessment" }).click();
  await page.getByLabel("Assessment type").selectOption("nist_csf");
  await page.getByLabel(/Assessment name/).fill(name);
  await page.getByLabel("Target tier").selectOption({ index: 1 });
  await page.getByLabel("Impact profile").selectOption({ index: 1 });
  await page.getByRole("button", { name: "Start assessment" }).click();
  await page.waitForURL(/\/self-assessment\//, { timeout: 30_000 });
}

test("Client reads an admin reply by clicking into the assessment (FIX D-1)", async ({
  browser,
}) => {
  const name = unique("E2E Thread CSF");
  const reply = unique("REPLY-TOKEN");

  const clientCtx = await browser.newContext();
  const adminCtx = await browser.newContext();
  try {
    const clientPage = await clientCtx.newPage();
    const adminPage = await adminCtx.newPage();

    // --- client submits a self-assessment -------------------------------
    await signIn(clientPage, CLIENT_EMAIL, CLIENT_PASSWORD, "/assessments");
    await startCsfSelfAssessment(clientPage, name);
    await clientPage.getByRole("button", { name: "Submit for review" }).click();
    await expect(
      clientPage.getByText("Self-assessment submitted"),
    ).toBeVisible();

    // --- admin replies on the thread, reached by clicking ---------------
    await signIn(adminPage, ADMIN_EMAIL, ADMIN_PASSWORD, "/admin/active");
    const row = adminPage.getByRole("row", {
      name: new RegExp(escapeRegExp(name)),
    });
    await expect(row).toBeVisible();
    await row.getByRole("link", { name: /Open/ }).click();
    await expect(
      adminPage.getByRole("heading", {
        level: 1,
        name: "NIST CSF 2.0 Assessment",
      }),
    ).toBeVisible();

    const composer = adminPage.getByLabel("Write a message");
    await expect(composer).toBeVisible();
    await composer.fill(reply);
    // exact — the workspace also has a disabled "Send for evaluation" button.
    await adminPage.getByRole("button", { name: "Send", exact: true }).click();
    // The reply lands in the thread the admin is looking at.
    await expect(adminPage.getByText(reply)).toBeVisible();

    // --- client navigates from "My Assessments" by CLICKING -------------
    await clientPage.goto("/assessments");
    await expect(
      clientPage.getByRole("heading", { level: 1, name: "My assessments" }),
    ).toBeVisible();

    // The card is a real link now (the D-1 fix); a link-less pill would fail.
    const card = clientPage.getByRole("link", {
      name: new RegExp(escapeRegExp(name)),
    });
    await expect(card).toBeVisible();
    await card.click();

    // The reply is visible on the assessment the client clicked into, and it
    // is attributed to the admin ("SHIELD analyst").
    await expect(clientPage.getByText(reply)).toBeVisible();
    await expect(clientPage.getByText("SHIELD analyst").first()).toBeVisible();
  } finally {
    await clientCtx.close();
    await adminCtx.close();
  }
});
