import { expect, test } from "@playwright/test";
import manifest from "../../src/fixtures/manifest.json" with { type: "json" };

// A deployment with no model: it replays recordings and cannot run the agent.
const REPLAY_ONLY = "http://127.0.0.1:4181";

test("fork and resume are shown as unavailable, with the reason, and nothing is sent", async ({ page }) => {
  const posts: string[] = [];
  page.on("request", (r) => r.method() === "POST" && posts.push(r.url()));

  await page.goto(`${REPLAY_ONLY}/runs/${manifest.halted.run_id}`);
  await expect(page.getByText("Replaying recordings — no model here.")).toBeVisible();
  const halt = page.getByRole("region", { name: "Halted by the depth breaker" });
  await expect(halt.getByText("This deployment replays recordings.")).toBeVisible();
  await expect(halt).toContainText("Resuming live needs a model; run it locally to try it.");
  await expect(halt.getByRole("button", { name: "Resume" })).toHaveCount(0);

  await page.goto(`${REPLAY_ONLY}/runs/${manifest.wrong.run_id}?step=${manifest.fork.at_step}`);
  const fork = page.locator(".fork");
  await expect(fork).toContainText("Forking live needs a model; run it locally to try it.");
  await expect(fork.getByRole("button")).toHaveCount(0);

  // Everything that only reads still works.
  await page.getByRole("button", { name: "Trace the output" }).click();
  await expect(page.locator(".node-origin")).toHaveCount(1);
  expect(posts).toEqual([]);
});
