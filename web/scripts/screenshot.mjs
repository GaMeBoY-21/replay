// Screenshot a page of the running app.
//   node scripts/screenshot.mjs <url> <out.png> [width] [height] [light|dark] [full]
import { chromium } from "@playwright/test";

const [url, out, width = "1440", height = "900", theme = "dark", full = ""] = process.argv.slice(2);
const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: Number(width), height: Number(height) },
  colorScheme: theme === "light" ? "light" : "dark",
  deviceScaleFactor: 2,
});
await page.goto(url, { waitUntil: "networkidle" });
await page.evaluate(() => document.fonts.ready);
await page.screenshot({ path: out, fullPage: full === "full" });
const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
console.log(`${out}${overflow ? "  (horizontal overflow!)" : ""}`);
await browser.close();
