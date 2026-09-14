import { chromium } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';

const baseUrl = process.env.KLM_CAPTURE_BASE_URL ?? 'http://127.0.0.1:4173';
const outputDir = path.resolve(process.cwd(), 'public/site-media');
const captureOrigin = new URL(baseUrl);
if (captureOrigin.protocol !== 'http:' || !['127.0.0.1', 'localhost', '::1'].includes(captureOrigin.hostname)) {
  throw new Error('A captura de mídia do KLM Hub só pode apontar para o SPA local.');
}

async function ensureDir(dir) {
  await fs.mkdir(dir, { recursive: true });
}

async function waitForApp(page) {
  await page.waitForLoadState('domcontentloaded');
  await page.waitForLoadState('networkidle').catch(() => undefined);
  await page.waitForTimeout(1500);
}

async function captureMain(page, fileName) {
  await waitForApp(page);
  await page.locator('main').first().screenshot({
    path: path.join(outputDir, fileName),
    type: 'png',
    animations: 'disabled',
  });
}

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1600, height: 1100 },
  deviceScaleFactor: 1.5,
});

try {
  await ensureDir(outputDir);

  const page = await context.newPage();
  await page.goto(`${baseUrl}/app`, { waitUntil: 'domcontentloaded' });
  await waitForApp(page);

  await page.goto(`${baseUrl}/app/conversas`, { waitUntil: 'domcontentloaded' });
  await captureMain(page, 'hero-system.png');

  await page.goto(`${baseUrl}/app`, { waitUntil: 'domcontentloaded' });
  await captureMain(page, 'kpi-system.png');

  await page.goto(`${baseUrl}/app/funil`, { waitUntil: 'domcontentloaded' });
  await captureMain(page, 'crm-system.png');

  await page.goto(`${baseUrl}/app/agentes`, { waitUntil: 'domcontentloaded' });
  await captureMain(page, 'ia-system.png');

  await page.goto(`${baseUrl}/app/flows`, { waitUntil: 'domcontentloaded' });
  await captureMain(page, 'platform-system.png');
} finally {
  await context.close();
  await browser.close();
}
