// Screenshot the deployed site's key pages, and fail if any is stuck loading.
//   node scripts/screenshot-site.mjs <site-url> <out-dir>
import { chromium } from "@playwright/test";

const [site, outDir] = process.argv.slice(2);
const pages = {
  "aws-runs": "/",
  "aws-corpus": "/corpus",
  "aws-trace": "/runs/qwen-00?trace=output",
  "aws-diff": "/diff?a=qwen-00&b=qwen-00-fork-8",
  "aws-halted": "/runs/halt-03",
};
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", deviceScaleFactor: 2 });
let failed = false;
for (const [name, path] of Object.entries(pages)) {
  const started = Date.now();
  await page.goto(site + path, { waitUntil: "networkidle" });
  await page.locator("main h1").first().waitFor({ timeout: 15_000 });
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
  const loading = await page.getByText(/^Loading/).count();
  const live = await page.getByText("Replaying recordings — no model here.").count();
  await page.screenshot({ path: `${outDir}/${name}.png`, fullPage: false });
  console.log(`${name}.png  ${path}  ${Date.now() - started} ms  loading-left:${loading}  replay-only-note:${live}`);
  if (loading) failed = true;
}
await browser.close();
process.exit(failed ? 1 : 0);
