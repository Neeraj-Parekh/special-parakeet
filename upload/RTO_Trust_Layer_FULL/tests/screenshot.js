/**
 * Screenshot script for `screenshot.yml` — captures 4 full-page screenshots
 * of the running FastAPI app at 1280x800, so the pitch deck can embed them
 * as live URLs (`https://<owner>.github.io/<repo>/<name>.png`).
 *
 * Pages captured:
 *   /docs          → screenshots/openapi-docs.png   (Swagger UI auto-gen)
 *   /health        → screenshots/health.png         (the JSON health endpoint rendered as a page)
 *   /              → screenshots/dashboard.png     (the dashboard if mounted at /, else /docs fallback)
 *   /risk/score    → screenshots/score-endpoint.png (405/404 response page — captures the FastAPI error renderer)
 *
 * Run via: `node tests/screenshot.js` (after `npx playwright install --with-deps chromium`).
 *
 * Track J (Day 3) §D item — release-day artifacts.
 */

const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.env.BASE_URL || 'http://localhost:8000';
const OUT_DIR = path.resolve(__dirname, '..', 'screenshots');

const VIEWPORT = { width: 1280, height: 800 };

// Pages to capture. The dashboard entry falls back to /docs if `/` returns
// a non-200 status (the dashboard isn't always mounted at root in CI mode).
const PAGES = [
  { url: `${BASE}/docs`, name: 'openapi-docs.png', expectOk: true },
  { url: `${BASE}/health`, name: 'health.png', expectOk: true },
  { url: `${BASE}/`, name: 'dashboard.png', expectOk: false, fallback: `${BASE}/docs` },
  // /risk/score requires a POST body — without it the API returns 405
  // (method not allowed) which is exactly what we want to capture for
  // the pitch (shows the endpoint exists + is enforced).
  { url: `${BASE}/risk/score`, name: 'score-endpoint.png', expectOk: false },
];

(async () => {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  console.log(`[screenshots] output dir: ${OUT_DIR}`);
  console.log(`[screenshots] base URL: ${BASE}`);

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);

  let ok = 0;
  let fail = 0;

  for (const p of PAGES) {
    const outPath = path.join(OUT_DIR, p.name);
    try {
      const response = await page.goto(p.url, { waitUntil: 'networkidle' });
      const status = response ? response.status() : 0;
      console.log(`[screenshots] ${p.url} → HTTP ${status}`);

      // If `/` returns a non-200 (e.g. 404 because no dashboard is mounted),
      // fall back to /docs so the dashboard.png is still a useful image
      // rather than a 404 page. The fallback is recorded in the screenshot
      // log so a reviewer can spot the substitution.
      if (!response || (!response.ok() && p.fallback)) {
        console.log(`[screenshots]   falling back to ${p.fallback}`);
        await page.goto(p.fallback, { waitUntil: 'networkidle' });
      }

      // Give the SPA (Swagger UI is a small one) a beat to finish rendering
      // before the screenshot — fullPage screenshots catch the entire
      // rendered DOM.
      await page.waitForTimeout(500);
      await page.screenshot({ path: outPath, fullPage: true });
      console.log(`[screenshots]   saved → ${outPath} (${fs.statSync(outPath).size} bytes)`);
      ok++;
    } catch (err) {
      console.error(`[screenshots]   FAILED on ${p.url}: ${err.message}`);
      // Capture whatever rendered (or a blank page if nothing did).
      try {
        await page.screenshot({ path: outPath, fullPage: true });
        console.log(`[screenshots]   saved (on-failure) → ${outPath}`);
      } catch (e2) {
        console.error(`[screenshots]   could not capture on-failure: ${e2.message}`);
      }
      fail++;
    }
  }

  await page.close();
  await context.close();
  await browser.close();

  console.log(`[screenshots] done: ${ok} ok, ${fail} fail`);
  // Exit non-zero if ALL captures failed — a partial failure (3/4) is
  // acceptable because the pitch deck can substitute. A 4/4 failure means
  // the API never came up or Playwright broke.
  if (ok === 0) {
    console.error('::error::all screenshots failed');
    process.exit(1);
  }
  process.exit(0);
})();
