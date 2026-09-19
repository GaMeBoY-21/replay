import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import manifest from "../../src/fixtures/manifest.json" with { type: "json" };

const LIVE = "http://127.0.0.1:4180";
const PAGES = {
  "run list": "/",
  "wrong run, traced": `/runs/${manifest.wrong.run_id}?trace=output`,
  "decision fork": `/runs/${manifest.fork.run_id}?step=${manifest.fork.at_step}`,
  diff: `/diff?a=${manifest.wrong.run_id}&b=${manifest.fork.run_id}`,
  corpus: "/corpus",
  "halted run": `/runs/${manifest.halted.run_id}`,
};

for (const scheme of ["dark", "light"] as const) {
  for (const [name, path] of Object.entries(PAGES)) {
    test(`${name} passes axe in the ${scheme} theme, with no horizontal overflow at 1280x720`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" });
      await page.goto(`${LIVE}${path}`);
      await page.locator("main h1").first().waitFor();
      await page.waitForLoadState("networkidle");
      const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
      const found = results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`);
      expect(found).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    });
  }
}

test("the run graph and the fork control are reachable by keyboard, with a visible focus", async ({ page }) => {
  await page.goto(`${LIVE}/runs/${manifest.wrong.run_id}?step=9`);
  await page.locator(".graph-rows button").first().waitFor();
  const reached: string[] = [];
  for (let i = 0; i < 40; i++) {
    await page.keyboard.press("Tab");
    const focused = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      if (!el) return "";
      const outline = getComputedStyle(el).outlineStyle;
      return `${el.tagName}|${el.getAttribute("aria-label") ?? el.textContent?.trim().slice(0, 40)}|${outline}`;
    });
    reached.push(focused);
  }
  const row = reached.find((f) => f.startsWith("BUTTON|Step 9, model call"));
  expect(row, reached.join("\n")).toBeTruthy();
  expect(row!.endsWith("|none")).toBe(false);
  expect(reached.some((f) => f.startsWith("BUTTON|Fork at step 9"))).toBe(true);
});
