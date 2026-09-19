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
