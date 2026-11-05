/**
 * Langfuse UI screenshot capture
 * Usage: node scripts/screenshot_langfuse.mjs
 *
 * Reads credentials from .env (LANGFUSE_INIT_USER_EMAIL, LANGFUSE_INIT_USER_PASSWORD)
 * Saves screenshots to images/screenshots/
 */

import { chromium } from "playwright";
import { readFileSync, mkdirSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dir = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dir, "..");
const OUT = join(ROOT, "images", "screenshots");

// ── load .env ─────────────────────────────────────────────────────────────
function loadEnv() {
  const env = {};
  try {
    const lines = readFileSync(join(ROOT, ".env"), "utf8").split("\n");
    for (const line of lines) {
      const m = line.match(/^([A-Z_][A-Z0-9_]*)=(.*)$/);
      if (m) env[m[1]] = m[2].replace(/^["']|["']$/g, "");
    }
  } catch {}
  return env;
}

const env = loadEnv();
const BASE = env.LANGFUSE_BASE_URL || "http://localhost:3000";
const EMAIL = env.LANGFUSE_INIT_USER_EMAIL || "";
const PASSWORD = env.LANGFUSE_INIT_USER_PASSWORD || "";

if (!EMAIL || !PASSWORD) {
  console.error("Missing LANGFUSE_INIT_USER_EMAIL or LANGFUSE_INIT_USER_PASSWORD in .env");
  process.exit(1);
}

mkdirSync(OUT, { recursive: true });

// ── helper ────────────────────────────────────────────────────────────────
async function shot(page, name, waitFor = null) {
  if (waitFor) await page.waitForSelector(waitFor, { timeout: 15000 }).catch(() => {});
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.screenshot({
    path: join(OUT, `${name}.png`),
    fullPage: false,
  });
  console.log(`  ✓ ${name}.png`);
}

// ── main ──────────────────────────────────────────────────────────────────
async function main() {
  console.log(`Capturing Langfuse UI at ${BASE}\n`);

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1440, height: 900 });

  // ── login ──────────────────────────────────────────────────────────────
  await page.goto(`${BASE}/auth/sign-in`);
  await page.waitForSelector('input[name="email"]', { timeout: 15000 });
  await page.fill('input[name="email"]', EMAIL);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button[type="submit"]');

  // wait until we leave the sign-in page (indicates successful login)
  try {
    await page.waitForFunction(
      () => !window.location.pathname.includes('/auth/sign-in'),
      { timeout: 20000 }
    );
  } catch {
    // take debug screenshot to see what happened
    await page.screenshot({ path: join(OUT, "debug-login.png") });
    console.error("  ✗ login failed — saved debug-login.png");
    await browser.close();
    process.exit(1);
  }
  console.log("  ✓ logged in →", page.url(), "\n");

  // ── 1. tracing overview ───────────────────────────────────────────────
  await page.goto(`${BASE}/project/ask-orbisa/traces`);
  await shot(page, "01-tracing-overview", '[data-testid="traces-table"], table, .traces');

  // ── 2. single trace (most recent) ────────────────────────────────────
  const firstTrace = page.locator("table tbody tr, [data-testid='trace-row']").first();
  if (await firstTrace.count() > 0) {
    await firstTrace.click();
    await page.waitForURL(`${BASE}/**/traces/**`, { timeout: 10000 }).catch(() => {});
    await shot(page, "02-trace-detail");
  }

  // ── 3. experiments ────────────────────────────────────────────────────
  await page.goto(`${BASE}/project/ask-orbisa/experiments`);
  await shot(page, "03-experiments-list", "table, [data-testid='experiments']");

  // ── 4. experiment run detail (prod-baseline-full) ─────────────────────
  const expRow = page.locator("table tbody tr").first();
  if (await expRow.count() > 0) {
    await expRow.click();
    await page.waitForLoadState("networkidle").catch(() => {});
    await shot(page, "04-experiment-run-detail");
  }

  // ── 5. datasets ───────────────────────────────────────────────────────
  await page.goto(`${BASE}/project/ask-orbisa/datasets`);
  await shot(page, "05-datasets-list", "table");

  // ── 6. dataset orbisa-truth items ────────────────────────────────────
  const dsRow = page.locator("table tbody tr").first();
  if (await dsRow.count() > 0) {
    await dsRow.click();
    await page.waitForLoadState("networkidle").catch(() => {});
    await shot(page, "06-dataset-orbisa-truth");
  }

  // ── 7. prompts ────────────────────────────────────────────────────────
  await page.goto(`${BASE}/project/ask-orbisa/prompts`);
  await shot(page, "07-prompts-list", "table");

  // ── 8. prompt detail ─────────────────────────────────────────────────
  const promptRow = page.locator("table tbody tr").first();
  if (await promptRow.count() > 0) {
    await promptRow.click();
    await page.waitForLoadState("networkidle").catch(() => {});
    await shot(page, "08-prompt-detail");
  }

  // ── 9. evaluators (after setup.py) ───────────────────────────────────
  // Navigate via sidebar to avoid stale UI state
  await page.goto(`${BASE}/project/ask-orbisa/evals/evaluators/cmw45ypjm0007pc7tsaoz8lk7`);
  await page.waitForLoadState("networkidle").catch(() => {});
  // dismiss any toast errors
  await page.locator('button[aria-label="Close"], [data-radix-toast-close]').first().click().catch(() => {});
  await shot(page, "09-evaluators");

  // ── 10. score configs ─────────────────────────────────────────────────
  await page.goto(`${BASE}/project/ask-orbisa/evals/score-configs`);
  await page.waitForLoadState("networkidle").catch(() => {});
  await shot(page, "10-score-configs", "table, h1");

  // ── 11. annotation queues ─────────────────────────────────────────────
  await page.goto(`${BASE}/project/ask-orbisa/evals/annotation-queues`);
  await page.waitForLoadState("networkidle").catch(() => {});
  await shot(page, "11-annotation-queues", "table, h1");

  await browser.close();
  console.log(`\nDone. Screenshots saved to: images/screenshots/`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
