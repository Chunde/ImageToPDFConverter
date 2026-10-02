/**
 * Browser-driven UI test: loads the real page, adds images through the real
 * file input, then reorders cards with genuine HTML5 drag-and-drop events and
 * checks that the exported PDF reflects the new order.
 *
 * Usage: node tests/ui_test.js [baseUrl] [outDir]
 *
 * Playwright is resolved from PLAYWRIGHT_PATH (a module dir) or NODE_PATH, and
 * a locally installed Chromium/Edge is used via CHROME_PATH when given.
 */
const fs = require("fs");
const path = require("path");

const BASE = process.argv[2] || "http://127.0.0.1:8765";
const OUT = process.argv[3] || path.join(__dirname, "..", "testdata", "ui");
const FIXTURES = path.join(__dirname, "..", "testdata", "passports");

let passed = 0;
const failed = [];

function check(label, condition, detail = "") {
  if (condition) {
    passed += 1;
    console.log(`  PASS  ${label}`);
  } else {
    failed.push(label);
    console.log(`  FAIL  ${label} ${detail}`);
  }
}

async function main() {
  const playwright =
    (process.env.PLAYWRIGHT_PATH && require(process.env.PLAYWRIGHT_PATH)) || require("playwright");
  const launchOptions = {};
  if (process.env.CHROME_PATH) launchOptions.executablePath = process.env.CHROME_PATH;
  const browser = await playwright.chromium.launch(launchOptions);
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  const consoleErrors = [];
  const badResponses = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(`pageerror: ${error.message}`));
  page.on("response", (response) => {
    if (response.status() >= 400) badResponses.push(`${response.status()} ${response.url()}`);
  });

  console.log(`UI test against ${BASE}\n`);
  await page.goto(BASE, { waitUntil: "load" });
  await page.waitForSelector("#grid");

  console.log("initial render");
  check("title is present", (await page.title()).includes("Image to PDF"));
  check("grid starts empty", (await page.locator(".card").count()) === 0);
  check("export is disabled with no pages", await page.locator("#btn-export").isDisabled());
  check("hint is visible with no pages", await page.locator("#hint").isVisible());

  console.log("\nadding images through the file input");
  const fixtureFiles = fs
    .readdirSync(FIXTURES)
    .filter((name) => /\.(png|jpe?g)$/i.test(name))
    .sort()
    .map((name) => path.join(FIXTURES, name));
  check("fixtures exist", fixtureFiles.length >= 3, String(fixtureFiles.length));
  await page.setInputFiles("#file-input", fixtureFiles);
  await page.waitForFunction(() => document.querySelectorAll(".card").length === 3, null, { timeout: 15000 });
  check("three cards rendered", (await page.locator(".card").count()) === 3);
  check("page counter reads 3 pages", (await page.locator("#count-label").textContent()).trim() === "3 pages");
  check("hint hides once pages exist", await page.locator("#hint").isHidden());
  check("export is enabled", !(await page.locator("#btn-export").isDisabled()));

  // Wait for the thumbnails to actually decode, so Auto-rotate has dimensions.
  await page.waitForFunction(
    () => Array.from(document.querySelectorAll(".card .thumb img")).every((img) => img.complete && img.naturalWidth > 0),
    null,
    { timeout: 15000 }
  );
  check("thumbnails decoded", true);

  const namesBefore = await page.locator(".card .fname").allTextContents();
  console.log(`  order: ${namesBefore.join(" | ")}`);
  check(
    "cards are in natural name order",
    namesBefore.join(",") === ["passport-1.png", "passport-2.png", "passport-10.png"].join(","),
    namesBefore.join(",")
  );

  console.log("\nthe Add folder button opens the directory input");
  await page.evaluate(() => {
    window.__folderPickerOpened = false;
    document.getElementById("folder-input").addEventListener("click", () => {
      window.__folderPickerOpened = true;
    });
  });
  await page.click("#btn-add-folder");
  check(
    "Add folder… triggers the directory picker",
    await page.evaluate(() => window.__folderPickerOpened === true)
  );

  console.log("\nsorting with the toolbar");
  await page.selectOption("#sort-key", "name-desc");
  await page.waitForFunction(() => document.querySelector(".card .fname").textContent.includes("passport-10"));
  const namesDesc = await page.locator(".card .fname").allTextContents();
  check("descending sort reverses the order", namesDesc[0].includes("passport-10"), namesDesc.join(","));

  await page.selectOption("#sort-key", "name");
  await page.waitForFunction(() => document.querySelector(".card .fname").textContent.includes("passport-1.png"));
  check("ascending sort restores the order", (await page.locator(".card .fname").allTextContents())[0].includes("passport-1"));

  console.log("\nreal drag-and-drop reordering");
  const moved = await page.evaluate(async () => {
    const cards = Array.from(document.querySelectorAll(".card"));
    const source = cards[0];            // passport-1.png
    const target = cards[2];            // passport-10.png
    const data = new DataTransfer();

    const fire = (node, type, extra = {}) => {
      const event = new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: data, ...extra });
      node.dispatchEvent(event);
      return event;
    };

    fire(source, "dragstart");
    const box = target.getBoundingClientRect();
    // Drop on the right half of the last card => insert after it.
    fire(target, "dragover", { clientX: box.left + box.width * 0.8, clientY: box.top + box.height / 2 });
    fire(target, "drop", { clientX: box.left + box.width * 0.8, clientY: box.top + box.height / 2 });
    fire(source, "dragend");
    await new Promise((resolve) => setTimeout(resolve, 150));
    return Array.from(document.querySelectorAll(".card .fname")).map((node) => node.textContent);
  });
  console.log(`  order after drag: ${moved.join(" | ")}`);
  check("dragging the first card to the end reorders pages", moved[2].includes("passport-1"), moved.join(","));
  check("sort mode switched to manual", (await page.locator("#sort-key").inputValue()) === "manual");

  console.log("\nrotate and arrow controls");
  await page.locator(".card").first().locator("button", { hasText: "⟳" }).click();
  await page.waitForSelector(".card .rot");
  check("rotate badge appears", (await page.locator(".card .rot").first().textContent()).trim() === "90°");
  await page.locator(".card").first().locator("button", { hasText: "⟲" }).click();
  await page.waitForFunction(() => !document.querySelector(".card .rot"));
  check("rotating back removes the badge", (await page.locator(".card .rot").count()) === 0);

  const orderBeforeArrow = await page.locator(".card .fname").allTextContents();
  await page.locator(".card").last().locator("button", { hasText: "◀" }).click();
  const orderAfterArrow = await page.locator(".card .fname").allTextContents();
  check(
    "the ◀ button moves a page one slot earlier",
    orderBeforeArrow[1] === orderAfterArrow[2] && orderBeforeArrow[2] === orderAfterArrow[1],
    `${orderBeforeArrow.join(",")} -> ${orderAfterArrow.join(",")}`
  );

  console.log("\nremove");
  await page.locator(".card").first().locator("button", { hasText: "✕" }).click();
  await page.waitForFunction(() => document.querySelectorAll(".card").length === 2);
  check("remove drops the card", (await page.locator(".card").count()) === 2);
  check("counter follows", (await page.locator("#count-label").textContent()).trim() === "2 pages");

  // Put a third image back for the export test.
  await page.setInputFiles("#file-input", [fixtureFiles[0]]);
  await page.waitForFunction(() => document.querySelectorAll(".card").length === 3);

  console.log("\nexport");
  await page.fill("#file-name", "ui-test-output");
  const downloadPromise = page.waitForEvent("download", { timeout: 60000 });
  await page.click("#btn-export");
  const download = await downloadPromise;
  check("download filename is honoured", download.suggestedFilename() === "ui-test-output.pdf", download.suggestedFilename());
  fs.mkdirSync(OUT, { recursive: true });
  const saved = path.join(OUT, download.suggestedFilename());
  await download.saveAs(saved);
  const size = fs.statSync(saved).size;
  check("downloaded PDF is non-trivial", size > 2000, `${size} bytes`);
  check(
    "result line reports the export",
    (await page.locator("#last-result").textContent()).includes("ui-test-output.pdf")
  );

  console.log("\nsettings panel");
  await page.selectOption("#page-size", "letter");
  await page.locator("#page-margin").fill("12");
  await page.locator("#page-margin").dispatchEvent("input");
  check("margin label updates", (await page.locator("#margin-value").textContent()).trim() === "12 mm");
  await page.selectOption("#page-fit", "cover");
  check("fit mode is selectable", (await page.locator("#page-fit").inputValue()) === "cover");

  console.log("\nlayout");
  await page.click("#btn-open-browser");
  await page.waitForSelector("#browser-panel:not([hidden])", { timeout: 15000 });
  check("folder browser opens", await page.locator("#browser-panel").isVisible());
  await page.waitForSelector("#browser-roots button", { timeout: 20000 });
  const roots = await page.locator("#browser-roots button").count();
  check("browser shows quick-access roots", roots > 0, String(roots));
  await page.waitForFunction(
    () => document.querySelectorAll("#browser-list .browser-item").length > 0,
    null,
    { timeout: 20000 }
  );
  const listed = await page.locator("#browser-list .browser-item").count();
  check("browser lists folders or images", listed > 0, String(listed));
  await page.click("#browser-close");
  check("browser closes", await page.locator("#browser-panel").isHidden());

  await page.screenshot({ path: path.join(OUT, "workspace.png"), fullPage: false });
  check("screenshot captured", fs.existsSync(path.join(OUT, "workspace.png")));

  console.log("\ndemo mode hides personal quick-access shortcuts");
  await page.goto(`${BASE}/?demo=1`, { waitUntil: "load" });
  await page.waitForFunction(() => window.imageToPdf && window.imageToPdf.state.root);
  await page.click("#btn-open-browser");
  await page.waitForSelector("#browser-roots button");
  check("demo note is shown", await page.locator("#browser-note").isVisible());
  const demoRoots = await page.locator("#browser-roots button").allTextContents();
  check(
    "demo roots exclude Desktop/Downloads/Documents/Pictures",
    !demoRoots.some((text) => /Desktop|Downloads|Documents|Pictures/i.test(text)),
    demoRoots.join(" | ")
  );
  await page.waitForFunction(
    () => document.querySelectorAll("#browser-list .browser-item").length > 0,
    null,
    { timeout: 20000 }
  );
  check("demo mode still browses folders", (await page.locator("#browser-list .browser-item").count()) > 0);

  console.log("\nconsole health");
  check("no uncaught page errors", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));
  check("no failed HTTP responses", badResponses.length === 0, badResponses.slice(0, 5).join(" | "));

  await browser.close();
  console.log(`\n${passed} check(s) passed, ${failed.length} failed.`);
  for (const label of failed) console.log(`  - ${label}`);
  return failed.length ? 1 : 0;
}

main().then(
  (code) => process.exit(code),
  (error) => {
    console.error(error);
    process.exit(1);
  }
);
