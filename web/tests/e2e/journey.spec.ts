import { expect, test } from "@playwright/test";
import manifest from "../../src/fixtures/manifest.json" with { type: "json" };

const LIVE = "http://127.0.0.1:4180";
const wrong = manifest.wrong.run_id;

test("list, open, trace and diff the canonical runs against the local server", async ({ page }) => {
  await page.goto(`${LIVE}/`);
  await expect(page.getByText("Live on scripted test double")).toBeVisible();
  await page.getByRole("rowheader").getByRole("link", { name: wrong, exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: wrong })).toBeVisible();

  await page.getByRole("button", { name: "Trace the output" }).click();
  await expect(page.getByText("The run broke at step 14. It was wrong from step 10. Nobody could see that until now.")).toBeVisible();
  await expect(page.locator(".node-origin")).toHaveCount(1);

  await page.goto(`${LIVE}/diff?a=${wrong}&b=${manifest.fork.run_id}`);
  await expect(page.getByText(`Steps 1–${manifest.fork.at_step - 1} are one log, stored once.`)).toBeVisible();
});

test("forking from the UI serves another run's recorded response and runs the rest live", async ({ page }) => {
  await page.goto(`${LIVE}/runs/${wrong}?step=${manifest.fork.at_step}`);
  const fork = page.getByRole("region", { name: /Step 9 model call/ }).locator(".fork");
  await expect(fork.getByText(/That response asks for record_invoice_field currency = "INR"/)).toBeVisible();

  const request = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith(`/api/runs/${wrong}/fork`));
  await fork.getByRole("button", { name: `Fork at step ${manifest.fork.at_step}` }).click();
  const body = (await request).postDataJSON();
  expect(body.at_step).toBe(manifest.fork.at_step);
  expect(body).not.toHaveProperty("at_seq");

  await expect(page).toHaveURL(new RegExp(`/runs/${wrong}-fork-`));
  await expect(page.getByText("Steps 1–8 are qwen-00's own log")).toBeVisible();
  // The substituted response asked for tools; they ran live in the fork and wrote INR.
  await expect(page.locator(".graph-rows").getByText('↦ invoice.currency = "INR"')).toBeVisible();
  await expect(page.locator(".graph-rows").getByText(/report\.total = \{"amount":492/)).toBeVisible();
  await expect(page.getByText("Reconciled. The total is $492.00 in USD.")).toBeVisible();
});

test("resuming a halted run requires a raised ceiling and continues live", async ({ page }) => {
  const halted = manifest.halted.run_id;
  await page.goto(`${LIVE}/runs/${halted}`);
  const panel = page.getByRole("region", { name: "Halted by the depth breaker" });
  await expect(panel.getByText("effect 15 exceeds ceiling of 15")).toBeVisible();
  await expect(panel.getByText(/deliberately tight/)).toBeVisible();

  const ceiling = panel.getByLabel("Raise the depth ceiling to");
  await ceiling.fill("15");
  await expect(panel.getByRole("button", { name: "Resume" })).toBeDisabled();
  await ceiling.fill("80");

  const request = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith(`/api/runs/${halted}/resume`));
  await panel.getByRole("button", { name: "Resume" }).click();
  expect((await request).postDataJSON()).toEqual({ breaker_overrides: { max_effects: 80 } });
  await expect(page).toHaveURL(new RegExp(`/runs/${halted}-resume-`));
  await expect(page.locator(".run-title-row").getByText("completed")).toBeVisible();
});
