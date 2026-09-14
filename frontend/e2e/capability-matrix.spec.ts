import { authenticateWorkspacePage, expect, test } from './fixtures';

const contextualRoutes = [
  '/app/agenda',
  '/app/agentes',
  '/app/aprovacoes',
  '/app/atividade',
  '/app/configuracoes',
  '/app/contratos',
  '/app/conversas',
  '/app/equipe',
  '/app/ferramentas',
  '/app/financeiro',
  '/app/flows',
  '/app/funil',
  '/app/kpis',
  '/app/leads',
  '/app/omnichannel',
  '/app/qualidade',
] as const;

// These routes require a tenant workspace. Relying on the application's local
// auto-bootstrap made a fresh Playwright page race its preference/session
// bootstrap after earlier cases had changed the shared disposable workspace.
// Install one explicit workspace session per test instead of inheriting
// browser-local state from another journey.
test.beforeEach(async ({ page, request }) => {
  await authenticateWorkspacePage(page, request);
});

test('matriz de capacidades distingue runtime, exposição e incorporação no desktop e mobile', async ({ page }) => {
  const failures: string[] = [];
  page.on('pageerror', (error) => failures.push(`pageerror: ${error.message}`));
  page.on('response', (response) => {
    if (response.status() >= 500 && response.url().includes('127.0.0.1:8010')) {
      failures.push(`http ${response.status()}: ${response.url()}`);
    }
  });

  await page.goto('/app/ferramentas', { waitUntil: 'domcontentloaded' });
  await page.getByRole('tab', { name: 'Cobertura nativa' }).click();

  await expect(page.getByText('Matriz de incorporação', { exact: true })).toBeVisible();
  await expect(page.getByText('123/135 disponíveis · 0 verificadas', { exact: true })).toBeVisible();
  await expect(page.getByText('Ponte de contato, conversa, texto e webhook', { exact: true })).toBeVisible();
  await expect(page.getByText('Aprovada nativa').first()).toBeVisible();
  await expect(page.getByText('disponibilidade: compatibilidade interna', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('fonte nativa', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('validação pendente', { exact: true }).first()).toBeVisible();

  await page.getByRole('button', { name: 'Automação' }).click();
  await expect(page.getByText('Editor visual de workflows', { exact: true })).toBeVisible();
  await expect(page.getByText('Contas, contatos e leads', { exact: true })).not.toBeVisible();

  await page.getByRole('button', { name: 'Bloqueadas' }).click();
  await expect(page.getByText('Modelos, embeddings, crawling, voz e canais externos', { exact: true })).toBeVisible();

  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    page: document.documentElement.scrollWidth,
  }));
  expect(dimensions.page).toBeLessThanOrEqual(dimensions.viewport + 1);
  expect(failures).toEqual([]);
});

test('gaveta contextual torna recursos long-tail visíveis e pesquisáveis', async ({ page }) => {
  await page.goto('/app/leads', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Recursos conectados' }).click();
  let dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name: 'Recursos conectados' })).toBeVisible();
  await expect(dialog.getByText('Contas, contatos e leads', { exact: true })).toBeVisible();
  await dialog.getByPlaceholder('Buscar recurso nesta tela').fill('Mesclagem');
  await expect(dialog.getByText('Mesclagem e árvore de casos', { exact: true })).toBeVisible();
  await expect(dialog.getByText('Contas, contatos e leads', { exact: true })).not.toBeVisible();
  await page.keyboard.press('Escape');

  await page.goto('/app/configuracoes', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Recursos conectados' }).click();
  dialog = page.getByRole('dialog');
  await dialog.getByPlaceholder('Buscar recurso nesta tela').fill('pagamento');
  await expect(dialog.getByText('E-mail, calendário, pagamento e notificações reais', { exact: true })).toBeVisible();
  await expect(dialog.getByText('simulação e gate', { exact: true }).first()).toBeVisible();
  await expect(dialog.getByText('efeito controlado', { exact: true }).first()).toBeVisible();

  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    page: document.documentElement.scrollWidth,
  }));
  expect(dimensions.page).toBeLessThanOrEqual(dimensions.viewport + 1);
});

for (const route of contextualRoutes) {
  test(`rota ${route} possui entrada contextual`, { tag: '@desktop-only' }, async ({ page }) => {
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    await page.goto(route, { waitUntil: 'domcontentloaded' });
    const trigger = page.getByRole('button', { name: 'Recursos conectados' });
    await expect(
      trigger,
      `${route} deve manter a entrada de recursos conectados; erros: ${pageErrors.join(' | ') || 'nenhum'}`,
    ).toBeVisible();
    try {
      await trigger.click();
    } catch (error) {
      throw new Error(
        `${route} perdeu a entrada contextual durante a interação; erros: ${pageErrors.join(' | ') || 'nenhum'}`,
        { cause: error },
      );
    }
    const dialog = page.getByRole('dialog');
    const dialogText = await dialog.textContent();
    await expect(
      dialog.locator('article').first(),
      `${route} deve expor ao menos uma capacidade contextual; URL atual: ${page.url()}; conteúdo: ${dialogText}; erros: ${pageErrors.join(' | ') || 'nenhum'}`,
    ).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).not.toBeVisible();
  });
}

test('ação contextual abre a capacidade no domínio nativo KLM', { tag: '@desktop-only' }, async ({ page }) => {
  await page.goto('/app/leads', { waitUntil: 'domcontentloaded' });
  await expect.poll(() => new URL(page.url()).searchParams.get('matter')).not.toBeNull();
  const matterId = new URL(page.url()).searchParams.get('matter');
  await page.getByRole('button', { name: 'Recursos conectados' }).click();
  const dialog = page.getByRole('dialog');
  const contactCapability = dialog.locator('article').filter({ hasText: 'Contatos' }).first();
  await expect(contactCapability).toBeVisible();

  await contactCapability.getByRole('button', { name: 'Abrir no Meu Executivo' }).click();
  await expect.poll(() => new URL(page.url()).pathname).toBe('/app/capacidades/communications/contacts');
  expect(new URL(page.url()).searchParams.get('matter')).toBe(matterId);
  await expect(page.getByText(/Chatwoot|Rocket\.Chat|ChatterMate|BottleCRM|Abrir motor/)).toHaveCount(0);
});
