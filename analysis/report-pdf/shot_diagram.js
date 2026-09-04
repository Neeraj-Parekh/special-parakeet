// Screenshot diagram HTML at 2x device scale for 300dpi print embedding.
// (Diagram PNG pipeline per pdf SKILL.md — NOT a page.pdf() document render.)
import { chromium } from 'playwright';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1400, height: 640 },
    deviceScaleFactor: 2,
  });
  await page.goto('file:///home/z/my-project/analysis/report-pdf/diagram_arch.html');
  await page.waitForTimeout(400);
  const el = await page.$('.canvas');
  await el.screenshot({ path: '/home/z/my-project/analysis/report-pdf/diagram_arch.png' });
  await browser.close();
  console.log('diagram png written');
})();
