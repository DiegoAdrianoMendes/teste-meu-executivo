import { randomUUID } from 'node:crypto';
import { authenticateWorkspacePage, expect, test } from './fixtures';

const API_URL = process.env.KLM_API_URL ?? 'http://127.0.0.1:8010';

type FinanceSeed = {
  runId: string;
  recordCount: number;
  costCenterId: string;
  clientId: string;
  contractId: string;
  matterId: string;
  financeEntryId: string;
  searchPrefix: string;
};

test('financeiro prova lookup paginado, drill-down e escopo da exportacao', async ({ page, request }) => {
  test.setTimeout(60_000);
  const token = await authenticateWorkspacePage(page, request);
  const runId = `fin_${randomUUID().replaceAll('-', '').slice(0, 12)}`;
  const headers = {
    Authorization: `Bearer ${token}`,
    'X-KLM-Local-Seed-Intent': runId,
  };
  const seeded = await request.post(`${API_URL}/api/v1/klm/local/finance-e2e-seed`, {
    headers,
    data: { runId, recordCount: 55 },
  });
  expect(seeded.status(), await seeded.text()).toBe(200);
  const seed = await seeded.json() as FinanceSeed;

  try {
    await page.goto('/app/financeiro');
    await page.getByRole('tab', { name: 'Visão Financeira' }).click();
    await expect(page.getByRole('heading', { name: 'Visão Financeira' })).toBeVisible();

    const clientLookup = page.getByLabel('Buscar cliente').locator('..').locator('..');
    await page.getByLabel('Buscar cliente').fill(seed.searchPrefix);
    await expect(clientLookup.getByRole('button', { name: 'Carregar mais resultados' })).toBeVisible();
    await clientLookup.getByRole('button', { name: 'Carregar mais resultados' }).click();
    const clientSelect = page.getByLabel('Cliente', { exact: true });
    await expect.poll(async () => clientSelect.locator('option').count()).toBeGreaterThan(55);
    await clientSelect.selectOption(seed.clientId);

    const explorer = page.getByRole('region', { name: 'Explorador financeiro hierárquico' });
    await page.getByRole('button', { name: 'Financeiro', exact: true }).click();
    await expect(explorer.getByRole('heading', { name: 'Composição por centro de custo' })).toBeVisible();
    await explorer.getByRole('button', { name: new RegExp(runId, 'i') }).first().click();
    await expect(explorer.getByRole('heading', { name: 'Composição por cliente' })).toBeVisible();
    await explorer.getByRole('button', { name: /Cliente 000/i }).click();
    await expect(explorer.getByRole('heading', { name: 'Composição por contrato' })).toBeVisible();
    await explorer.getByRole('button', { name: /Contrato.*000/i }).click();
    await expect(explorer.getByRole('heading', { name: 'Composição por caso' })).toBeVisible();
    await explorer.getByRole('button', { name: /Caso financeiro.*000/i }).click();
    await expect(explorer.getByRole('heading', { name: 'Composição por lançamento' })).toBeVisible();
    await expect(explorer.getByRole('button', { name: /Lançamento.*000/i })).toBeVisible();

    await page.getByLabel('Formato da exportação financeira').selectOption('xlsx');
    const exportRequest = page.waitForRequest((candidate) =>
      candidate.method() === 'POST' && candidate.url().endsWith('/api/v1/finance/exports'),
    );
    await page.getByRole('button', { name: 'Exportar relatório' }).click();
    const payload = (await exportRequest).postDataJSON() as { drill_context: Record<string, unknown> };
    expect(payload.drill_context).toMatchObject({
      costCenterId: seed.costCenterId,
      clientId: seed.clientId,
      contractId: seed.contractId,
      matterId: seed.matterId,
      source: 'finance-overview',
    });
    expect(payload.drill_context.breadcrumb).toBeTruthy();
    await expect(page.getByText(/Exportação completed/)).toBeVisible({ timeout: 30_000 });
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Baixar' }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/\.xlsx$/);
    expect(await download.failure()).toBeNull();
  } finally {
    const cleanup = await request.delete(`${API_URL}/api/v1/klm/local/finance-e2e-seed/${runId}`, { headers });
    expect(cleanup.status(), await cleanup.text()).toBe(200);
  }
});
