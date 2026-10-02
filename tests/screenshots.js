/** Captures the screenshots used by README.md.
 *
 *  Usage: node tests/screenshots.js [baseUrl]
 *
 *  Needs PLAYWRIGHT_PATH (or a local playwright) and CHROME_PATH, same as
 *  ui_test.js. Files are written to docs/ so they can be committed.
 */
const fs = require("fs");
const path = require("path");

const BASE = process.argv[2] || "http://127.0.0.1:8765";
const OUT = path.join(__dirname, "..", "docs");
const FIXTURES = path.join(__dirname, "..", "testdata", "passports");

(async () => {
  const playwright = require(process.env.PLAYWRIGHT_PATH || "playwright");
  const browser = await playwright.chromium.launch(
    process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}
  );
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  fs.mkdirSync(OUT, { recursive: true });

  await page.goto(BASE, { waitUntil: "load" });
  await page.waitForSelector("#grid");

  const files = fs
    .readdirSync(FIXTURES)
    .filter((n) => /\.png$/i.test(n))
    .sort()
    .map((n) => path.join(FIXTURES, n));
  await page.setInputFiles("#file-input", files);
  await page.waitForFunction(() => document.querySelectorAll(".card").length === 3);
  await page.waitForFunction(() =>
    Array.from(document.querySelectorAll(".card .thumb img")).every((i) => i.complete && i.naturalWidth)
  );
  await page.selectOption("#sort-key", "name");
  await page.waitForTimeout(3600); // let the toasts fade out
  await page.screenshot({ path: path.join(OUT, "workspace.png") });
  console.log("wrote docs/workspace.png");

  await page.locator(".card .thumb img").first().click();
  await page.waitForSelector("#lightbox:not([hidden])");
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(OUT, "preview.png") });
  console.log("wrote docs/preview.png");

  // Browse this project's own test fixtures, so the sidebar screenshot does not
  // expose anyone's personal folders. ?demo=1 hides the quick-access roots.
  await page.goto(`${BASE}/?demo=1`, { waitUntil: "load" });
  await page.click("#btn-open-browser");
  await page.waitForSelector("#browser-roots, #browser-note:not([hidden])");
  await page.evaluate((dir) => window.imageToPdf.browseTo(dir), FIXTURES);
  await page.waitForFunction(() => document.querySelectorAll("#browser-list .browser-item").length > 0);
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(OUT, "browser.png") });
  console.log("wrote docs/browser.png");

  await browser.close();
})().catch((error) => {
  console.error(error);
  process.exit(1);
});

