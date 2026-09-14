import { expect, test } from './fixtures';

test('status público usa telemetria persistida sem indicadores sintéticos', async ({ page }) => {
  const failures: string[] = [];
  page.on('pageerror', (error) => failures.push(`pageerror: ${error.message}`));
  page.on('response', (response) => {
    if (response.status() >= 500) failures.push(`http ${response.status()}: ${response.url()}`);
  });

  const response = await page.goto('/status', { waitUntil: 'domcontentloaded' });
  expect(response?.status()).toBeLessThan(400);
  await expect(page.getByRole('heading', { name: 'Status operacional' })).toBeVisible();
  await expect(page.getByRole('paragraph').filter({ hasText: 'Meu Executivo' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Componentes verificados' })).toBeVisible();
  await expect(page.getByText(/observação\(ões\) real\(is\)/)).toBeVisible();
  await expect(page.getByText('99.9% nos últimos 30 dias', { exact: true })).toHaveCount(0);
  await expect(page.getByText(/Desburocratize/)).toHaveCount(0);

  const bars = page.getByLabel('Disponibilidade diária observada').locator('span');
  await expect(bars).toHaveCount(30);
  const refreshResponse = page.waitForResponse((item) => (
    item.request().method() === 'GET' && item.url().endsWith('/api/v1/status')
  ));
  await page.getByRole('button', { name: 'Atualizar telemetria' }).click();
  expect((await refreshResponse).status()).toBe(200);

  const geometry = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
  expect(Math.max(geometry.document, geometry.body) - geometry.viewport).toBeLessThanOrEqual(1);
  expect(failures).toEqual([]);
});
