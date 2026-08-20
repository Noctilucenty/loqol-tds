#!/usr/bin/env node
/**
 * End-to-end smoke test of the built front end.
 *
 * The Python suite covers the form model and the API, but it never renders a
 * page - so a React hooks-order violation shipped to production once and took
 * the entire seller flow down with a blank screen. Anything that walks the flow
 * in a real browser would have caught it in seconds. This does.
 *
 * Playwright is intentionally not a dependency of the app - the production image
 * has no business downloading browsers. Install it when you want to run this:
 *
 *   npm --prefix web i -D playwright && npx playwright install chromium
 *
 * Usage:  node scripts/smoke.mjs [baseUrl]
 * Exits non-zero on any page error, console error, or failed step.
 */

import { chromium } from "playwright";

const BASE = process.argv[2] || "http://127.0.0.1:8000";
const failures = [];
const step = (name, ok, detail = "") => {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failures.push(name);
};

const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: 1280, height: 950 },
  // The stylesheet honours prefers-reduced-motion, so this switches off the
  // entrance animations. Without it Playwright's click-stability check races
  // an element that is still sliding into place and fails intermittently.
  reducedMotion: "reduce",
});
const page = await ctx.newPage();

const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(e.message));
page.on("console", (m) => {
  // A 401 is expected while probing an unauthenticated route.
  if (m.type() === "error" && !m.text().includes("401")) pageErrors.push(m.text());
});

console.log(`smoke: ${BASE}`);

// --- agent side ------------------------------------------------------------
await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded", timeout: 60000 });
await page.waitForTimeout(1200);
await page.getByRole("button", { name: /Try it without signing up/ }).click();
await page.waitForURL("**/agent", { timeout: 30000 });
step("demo workspace signs in", true);

// Wait on the element, never on a stopwatch. The deal page loads two requests
// before it renders, and a fixed sleep turns a slow instance into a red build.
await page.locator("a.deal").first().click();
const dealHeader = await page.locator(".detail-grid").waitFor({ timeout: 30000 })
  .then(() => true).catch(() => false);
step("deal detail renders", dealHeader);
step("Section I editor is present",
     (await page.getByText("Disclosure coordination").count()) > 0);

await page.getByRole("button", { name: /Create link/ }).click();
await page.locator(".linkbox code").waitFor({ timeout: 30000 });
const link = await page.locator(".linkbox code").innerText();
step("seller link issued", link.includes("/s/"));

// --- seller side -----------------------------------------------------------
const seller = await ctx.newPage();
const sellerErrors = [];
seller.on("pageerror", (e) => sellerErrors.push(e.message));
seller.on("console", (m) => {
  if (m.type() === "error" && !m.text().includes("401")) sellerErrors.push(m.text());
});

await seller.goto(link, { waitUntil: "domcontentloaded", timeout: 60000 });
await seller.locator("h1").waitFor({ timeout: 30000 });
step("seller landing renders", (await seller.locator("h1").count()) > 0);

await seller.getByRole("button", { name: /Start|Pick up/ }).click();
// Wait for the landing screen to actually go. Waiting on "h1,h2" resolved
// instantly against the landing page's own heading, so the assertion that
// exists to catch a render crash passed trivially.
await seller.locator(".gate-title").waitFor({ state: "detached", timeout: 30000 });
await seller.locator("h1,h2").first().waitFor({ timeout: 30000 });
// This is the assertion that would have caught the hooks crash: after Start,
// the flow must still be rendering something.
step("flow renders past the landing screen", (await seller.locator("h1,h2").count()) > 0);

let reachedReview = false;
let voiceSeen = false;
for (let i = 0; i < 70; i++) {
  const heading = (await seller.locator("h1,h2").first().textContent().catch(() => "")) || "";
  if (heading.includes("check a few things")) { reachedReview = true; break; }
  if ((await seller.locator(".voice").count()) > 0) voiceSeen = true;

  const addr = seller.locator("input.input").first();
  if ((await addr.count()) && heading.includes("right property address")) {
    await addr.fill("1247 Sepulveda Blvd, Culver City, CA 90230");
    await seller.waitForTimeout(900);
  }
  for (const label of ["Yes, I live here", "No"]) {
    const b = seller.getByRole("button", { name: label, exact: true });
    if (await b.count()) { await b.first().click(); await seller.waitForTimeout(200); break; }
  }
  const inputs = seller.locator("input.input, textarea.input");
  const n = await inputs.count();
  for (let j = 0; j < n; j++) {
    const el = inputs.nth(j);
    if ((await el.inputValue()) === "") {
      const type = await el.getAttribute("type");
      await el.fill(type === "number" ? "2" : "Kitchen and both bathrooms");
      await seller.waitForTimeout(780);
    }
  }
  const next = seller.getByRole("button", { name: /^(Continue|Review)$/ });
  if (!(await next.count())) break;
  if (!(await next.isEnabled())) {
    const skip = seller.getByRole("button", { name: "Skip for now" });
    if (await skip.count()) { await skip.click(); await seller.waitForTimeout(220); continue; }
    break;
  }
  await next.click();
  await seller.waitForTimeout(300);
}

step("a voice-lane question offered the assistant", voiceSeen);
step("seller reaches the review step", reachedReview);
step("review lists something to settle or confirms it is clean",
     (await seller.locator(".rec, .rec-submit").count()) > 0);

// --- no crashes anywhere ---------------------------------------------------
step("no agent-side page errors", pageErrors.length === 0, pageErrors.slice(0, 2).join(" | "));
step("no seller-side page errors", sellerErrors.length === 0, sellerErrors.slice(0, 2).join(" | "));

await browser.close();

if (failures.length) {
  console.error(`\nsmoke FAILED: ${failures.join(", ")}`);
  process.exit(1);
}
console.log("\nsmoke passed");
