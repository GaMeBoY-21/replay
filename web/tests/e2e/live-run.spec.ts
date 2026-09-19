import { expect, test } from "@playwright/test";
import manifest from "../../src/fixtures/manifest.json" with { type: "json" };

// Each model call on this server takes three seconds: a live run is visibly in
// progress for a while, as it would be against a local model.
const SLOW = "http://127.0.0.1:4182";
const wrong = manifest.wrong.run_id;

test("a live fork opens at once, shows it is running, and reads keep working meanwhile", async ({ page }) => {
  await page.goto(`${SLOW}/runs/${wrong}?step=${manifest.fork.at_step}`);
  const fork = page.locator(".fork");
  await expect(fork.getByText(/asks for record_invoice_field currency = "INR"/)).toBeVisible();

  const clicked = Date.now();
  await fork.getByRole("button", { name: `Fork at step ${manifest.fork.at_step}` }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${wrong}-fork-`), { timeout: 2_000 });
  expect(Date.now() - clicked).toBeLessThan(2_000);
  const forkUrl = page.url();

  const note = page.getByRole("status").filter({ hasText: "Running live" });
  await expect(note).toBeVisible();
  await expect(note).toContainText(/\d:\d\d elapsed|starting/);
  // Steps recorded so far are on the graph before the run ends.
  await expect(page.locator(".graph-rows").getByText('↦ invoice.currency = "INR"')).toBeVisible();

  // Other pages read without waiting behind the live run.
  const listed = Date.now();
  await page.goto(`${SLOW}/`);
  const row = page.getByRole("rowheader").filter({ hasText: /-fork-/ }).locator("xpath=..");
  await expect(row.getByText("running")).toBeVisible({ timeout: 2_000 });
  expect(Date.now() - listed).toBeLessThan(2_500);

  // The run ends on its own; the page, polling, shows it.
  await page.goto(forkUrl);
  await expect(page.locator(".run-title-row").getByText("completed")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("status").filter({ hasText: "Running live" })).toHaveCount(0);
  await expect(page.getByText("Reconciled. The total is $492.00 in USD.")).toBeVisible();
});

test("cancelling a live run stops it cleanly, and a cancelled run continues without a ceiling", async ({ page }) => {
  // A resume, so this run's id differs from the fork the other test made.
  await page.goto(`${SLOW}/runs/${manifest.halted.run_id}`);
  const halt = page.getByRole("region", { name: "Halted by the depth breaker" });
  await halt.getByLabel("Raise the depth ceiling to").fill("81");
  await halt.getByRole("button", { name: "Resume" }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${manifest.halted.run_id}-resume-`), { timeout: 2_000 });

  await expect(page.getByText("Running live")).toBeVisible();
  const request = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith("/cancel"));
  await page.getByRole("button", { name: "Cancel" }).click();
  await request;
  await expect(page.getByText("Cancelling", { exact: true })).toBeVisible();

  // It stops at its next step: marked cancelled, with a Continue and no ceiling to raise.
  await expect(page.locator(".run-title-row").getByText("cancelled")).toBeVisible({ timeout: 20_000 });
  const cancelled = page.getByRole("region", { name: "Cancelled" });
  await expect(cancelled).toContainText("No ceiling was hit, so there is none to raise.");
  await expect(cancelled.getByLabel(/ceiling/)).toHaveCount(0);
  await expect(page.locator(".graph-rows").getByText("Cancelled by the operator. Suspended.")).toBeVisible();

  const resumed = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith("/resume"));
  await cancelled.getByRole("button", { name: "Continue" }).click();
  expect((await resumed).postDataJSON()).toEqual({});
  await expect(page).toHaveURL(/-resume-.*-resume-/, { timeout: 2_000 });
  await expect(page.locator(".run-title-row").getByText("completed")).toBeVisible({ timeout: 20_000 });
});
