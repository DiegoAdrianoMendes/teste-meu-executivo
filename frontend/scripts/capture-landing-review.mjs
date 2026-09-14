import { chromium } from '@playwright/test';

const baseUrl = process.env.DESB_REVIEW_URL ?? 'http://127.0.0.1:4173';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } });

try {
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3000);
  await page.waitForSelector('text=Transforme conversas no WhatsApp em contratos fechados.', { timeout: 15000 });
  await page.waitForTimeout(1200);

  await page.screenshot({ path: '/tmp/desb-landing-hero.png', fullPage: false, animations: 'disabled' });
  await page.screenshot({ path: '/tmp/desb-landing-full.png', fullPage: true, animations: 'disabled' });
} finally {
  await page.close();
  await browser.close();
}
