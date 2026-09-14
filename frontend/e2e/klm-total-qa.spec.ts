import {
  authenticateWorkspacePage,
  expect,
  test,
  type APIRequestContext,
  type Locator,
  type Page,
} from './fixtures';

const API_URL = process.env.KLM_API_URL ?? 'http://127.0.0.1:8010';
const routes = [
  '/app',
  '/app/conversas',
  '/app/equipe',
  '/app/leads',
  '/app/funil',
  '/app/kpis',
  '/app/rastreamento',
  '/app/qualidade',
  '/app/ferramentas',
  '/app/flows',
  '/app/omnichannel',
  '/app/comentarios',
  '/app/agenda',
  '/app/agenda/configuracoes',
  '/app/contratos',
  '/app/financeiro',
  '/app/peticoes',
  '/app/audio',
  '/app/agentes',
  '/app/areas',
  '/app/ti',
  '/app/configuracoes',
  '/app/atividade',
  '/app/aprovacoes',
] as const;

type AuthContext = {
  headers: { Authorization: string };
  matterId: string;
  threadId: string;
};

function captureRuntimeFailures(page: Page) {
  const failures: string[] = [];
  page.on('console', (message) => {
    // Resource failures are classified below from the response event, where
    // the URL is available. The generic console message cannot distinguish
    // the expected local refresh miss from an unexpected API failure.
    if (message.type() !== 'error' || message.text().startsWith('Failed to load resource:')) return;
    failures.push(`console: ${message.text()}`);
  });
  page.on('pageerror', (error) => failures.push(`pageerror: ${error.message}`));
  page.on('response', (response) => {
    const expectedLocalBootstrapRefreshMiss = (
      process.env.KLM_E2E_PRODUCTION !== '1'
      && response.status() === 401
      && new URL(response.url()).pathname === '/api/v1/auth/refresh'
    );
    if (
      response.status() >= 400
      && !expectedLocalBootstrapRefreshMiss
      && !(response.status() === 404 && response.url().includes('/api/v1/playbooks/effective'))
    ) {
      failures.push(`http ${response.status()}: ${response.url()}`);
    }
  });
  return failures;
}

async function activateControl(page: Page, control: Locator, useTouch: boolean) {
  await expect(control).toBeVisible();
  if (!useTouch) {
    await control.click();
    return;
  }

  await control.scrollIntoViewIfNeeded();

  const readTouchPoint = () => control.evaluate((element) => {
    const bounds = element.getBoundingClientRect();
    const layoutX = bounds.left + bounds.width / 2;
    const layoutY = bounds.top + bounds.height / 2;
    const hit = document.elementFromPoint(layoutX, layoutY);
    return {
      x: layoutX - (window.visualViewport?.offsetLeft ?? 0),
      y: layoutY - (window.visualViewport?.offsetTop ?? 0),
      hitTarget: hit === element || Boolean(hit && element.contains(hit)),
      controlName: element.getAttribute('aria-label') ?? element.textContent?.trim() ?? element.tagName,
      hitName: hit?.getAttribute('aria-label') ?? hit?.textContent?.trim() ?? hit?.tagName ?? 'none',
    };
  });
  await expect.poll(async () => (await readTouchPoint()).hitTarget, {
    message: 'O controle deve terminar a rolagem com o centro livre para toque',
  }).toBe(true);
  const point = await readTouchPoint();
  expect(
    point.hitTarget,
    `O centro de "${point.controlName}" está coberto por "${point.hitName}"`,
  ).toBe(true);
  await page.mouse.click(point.x, point.y);
}

async function anaContext(request: APIRequestContext): Promise<AuthContext> {
  const sessionResponse = await request.post(`${API_URL}/api/v1/klm/local/session`);
  expect(sessionResponse.status()).toBe(200);
  const session = await sessionResponse.json();
  const token = session.accessToken ?? session.access_token;
  const headers = { Authorization: `Bearer ${token}` };
  const funnelResponse = await request.get(`${API_URL}/api/v1/klm/funnel`, { headers });
  expect(funnelResponse.status()).toBe(200);
  const funnel = await funnelResponse.json();
  const matter = funnel.cards.find((card: { name: string; object?: string }) => card.name === 'Ana Paula Ribeiro' || card.object === 'Zolgensma');
  expect(matter?.id).toBeTruthy();
  const threadsResponse = await request.get(`${API_URL}/api/v1/klm/omnichannel/threads`, { headers });
  expect(threadsResponse.status()).toBe(200);
  const threads = await threadsResponse.json();
  const thread = threads.find((item: { matterId?: string | null }) => item.matterId === matter.id);
  expect(thread?.id).toBeTruthy();
  return { headers, matterId: matter.id, threadId: thread.id };
}

async function openCaseInspector(page: Page, context: AuthContext) {
  await page.goto(`/app/conversas?thread=${context.threadId}&matter=${context.matterId}`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { level: 1, name: 'Conversas', exact: true })).toBeVisible();
  // Desktop and responsive layouts keep their own mounted inspector. Keep a
  // live locator for the visible instance so rerenders cannot pin the test to
  // the hidden companion mount.
  const panel = page.locator('[data-testid="conversation-case-360"]:visible').last();
  const openButton = page.locator('button[title*="Caso 360"]:visible').first();
  await expect.poll(async () => await panel.isVisible() || await openButton.isVisible()).toBe(true);
  if (!await panel.isVisible()) {
    await openButton.click();
  }
  await expect(panel).toBeVisible();
  return panel;
}

async function expectAuthenticatedRoute(page: Page, route: string) {
  const expectedPath = route === '/app/omnichannel' ? '/app/conversas' : route;
  await expect(page.locator('h1, h2').first()).toBeVisible();
  await expect(page).toHaveURL((url) => url.pathname === expectedPath);
  await expect(page.getByRole('heading', { name: 'Entrar', exact: true })).toHaveCount(0);
  await expect(page.getByRole('main')).toHaveCount(1);
  await expect(page.getByRole('main')).toBeVisible();
}

test.beforeEach(async ({ page, request }) => {
  await authenticateWorkspacePage(page, request);
});

for (const route of routes) {
  test(`${route} não possui controle visível sem nome acessível`, async ({ page }) => {
    await page.goto(route, { waitUntil: 'domcontentloaded' });
    await expectAuthenticatedRoute(page, route);
    await page.waitForTimeout(150);

    const unnamed = await page.locator('main button, main a[href], main input:not([type="hidden"]), main textarea, main select, main [role="button"], main [role="switch"], main [role="combobox"], main [role="tab"]').evaluateAll((elements) => {
      const visible = (element: Element) => {
        const node = element as HTMLElement;
        const style = window.getComputedStyle(node);
        return style.visibility !== 'hidden' && style.display !== 'none' && node.getClientRects().length > 0;
      };
      const label = (element: Element) => {
        const labelledBy = element.getAttribute('aria-labelledby')
          ?.split(/\s+/)
          .map((id) => document.getElementById(id)?.textContent ?? '')
          .join(' ');
        const explicit = element.id ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`)?.textContent : '';
        const implicit = element.closest('label')?.textContent;
        return [
          element.getAttribute('aria-label'),
          labelledBy,
          explicit,
          implicit,
          element.getAttribute('title'),
          element.getAttribute('placeholder'),
          element.textContent,
          element.getAttribute('alt'),
        ].find((value) => value?.trim())?.trim() ?? '';
      };
      return elements.filter(visible).filter((element) => !label(element)).map((element) => element.outerHTML.slice(0, 180));
    });

    expect(unnamed, `Controles sem nome em ${route}:\n${unnamed.join('\n')}`).toEqual([]);
  });
}

for (const route of routes) {
  test(`${route} responde a todas as abas, opções e switches visíveis`, async ({ page }, testInfo) => {
    test.setTimeout(60_000);
    const failures = captureRuntimeFailures(page);
    const useTouch = testInfo.project.name.includes('mobile');
    await page.goto(route, { waitUntil: 'domcontentloaded' });
    await expectAuthenticatedRoute(page, route);
    await page.waitForTimeout(100);

    const tabCount = await page.getByRole('tab').count();
    for (let index = 0; index < tabCount; index += 1) {
      const tab = page.getByRole('tab').nth(index);
      if (!await tab.isVisible() || await tab.isDisabled()) continue;
      await tab.evaluate((element) => element.scrollIntoView({ block: 'center', inline: 'center' }));
      await tab.click();
      await expect(tab).toHaveAttribute('data-state', 'active');
    }

    const switchCount = await page.getByRole('switch').count();
    for (let index = 0; index < switchCount; index += 1) {
      const control = page.getByRole('switch').nth(index);
      if (!await control.isVisible() || await control.isDisabled()) continue;
      const original = await control.getAttribute('aria-checked');
      await activateControl(page, control, useTouch);
      await expect(control).toHaveAttribute('aria-checked', original === 'true' ? 'false' : 'true');
      await activateControl(page, control, useTouch);
      await expect(control).toHaveAttribute('aria-checked', original ?? 'false');
    }

    const comboCount = await page.getByRole('combobox').count();
    for (let comboIndex = 0; comboIndex < comboCount; comboIndex += 1) {
      const combo = page.getByRole('combobox').nth(comboIndex);
      if (!await combo.isVisible() || await combo.isDisabled()) continue;
      await combo.evaluate((element) => element.scrollIntoView({ block: 'center', inline: 'center' }));

      if (await combo.evaluate((element) => element.tagName === 'SELECT')) {
        const originalValue = await combo.inputValue();
        const nativeOptions = combo.locator('option');
        const nativeOptionCount = await nativeOptions.count();
        for (let optionIndex = 0; optionIndex < nativeOptionCount; optionIndex += 1) {
          const option = nativeOptions.nth(optionIndex);
          if (await option.isDisabled()) continue;
          const value = await option.getAttribute('value');
          if (value === null) continue;
          await combo.selectOption(value);
          await expect(combo).toHaveValue(value);
        }
        await combo.selectOption(originalValue);
        await expect(combo).toHaveValue(originalValue);
        continue;
      }

      await activateControl(page, combo, useTouch);
      const listbox = page.getByRole('listbox');
      await expect(listbox).toBeVisible();
      const options = listbox.getByRole('option');
      const optionCount = await options.count();
      let originalOptionIndex = 0;
      for (let optionIndex = 0; optionIndex < optionCount; optionIndex += 1) {
        if (await options.nth(optionIndex).getAttribute('aria-selected') === 'true') {
          originalOptionIndex = optionIndex;
          break;
        }
      }
      await page.keyboard.press('Escape');
      for (let optionIndex = 0; optionIndex < optionCount; optionIndex += 1) {
        const currentCombo = page.getByRole('combobox').nth(comboIndex);
        if (!await currentCombo.isVisible()) break;
        await currentCombo.evaluate((element) => element.scrollIntoView({ block: 'center', inline: 'center' }));
        await activateControl(page, currentCombo, useTouch);
        const currentListbox = page.getByRole('listbox');
        await expect(currentListbox).toBeVisible();
        const option = currentListbox.getByRole('option').nth(optionIndex);
        if (!await option.isVisible() || await option.getAttribute('aria-disabled') === 'true') {
          await page.keyboard.press('Escape');
          continue;
        }
        await activateControl(page, option, useTouch);
        await expect(page.getByRole('listbox')).toHaveCount(0);
      }

      const currentCombo = page.getByRole('combobox').nth(comboIndex);
      if (await currentCombo.isVisible()) {
        await currentCombo.evaluate((element) => element.scrollIntoView({ block: 'center', inline: 'center' }));
        await activateControl(page, currentCombo, useTouch);
        const currentListbox = page.getByRole('listbox');
        await expect(currentListbox).toBeVisible();
        await activateControl(page, currentListbox.getByRole('option').nth(originalOptionIndex), useTouch);
        await expect(page.getByRole('listbox')).toHaveCount(0);
      }
    }

    expect(failures, `Falhas de runtime em ${route}`).toEqual([]);
  });
}

test('Conversa Caso 360 expõe dossiê completo, download, agenda e ferramentas contextuais', async ({ page, request }, testInfo) => {
  test.setTimeout(90_000);
  const failures = captureRuntimeFailures(page);
  const context = await anaContext(request);
  const useTouch = testInfo.project.name !== 'desktop-1440';
  await page.addInitScript(() => {
    Object.defineProperty(window, 'open', {
      configurable: true,
      value: (url?: string | URL) => {
        document.documentElement.dataset.qaOpenedUrl = String(url ?? '');
        return null;
      },
    });
  });
  const panel = await openCaseInspector(page, context);

  if (testInfo.project.name.includes('mobile')) {
    await page.waitForTimeout(550);
    const viewport = page.viewportSize();
    const bounds = await panel.boundingBox();
    expect(viewport).not.toBeNull();
    expect(bounds, 'O Caso 360 móvel deve possuir geometria mensurável').not.toBeNull();
    expect(bounds!.x, 'O Caso 360 móvel não pode começar fora da tela').toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width, 'O Caso 360 móvel não pode ultrapassar a tela').toBeLessThanOrEqual(viewport!.width + 1);
    expect(bounds!.width, 'O Caso 360 móvel deve aproveitar a largura útil').toBeGreaterThan(viewport!.width * 0.8);
  }

  for (const section of [
    'Dados do cliente e card',
    'Origem e atribuição',
    'Saúde e estratégia jurídica',
    'Arquivos e documentos',
    'Contrato, procuração e proposta',
    'Agenda',
    'Honorários e financeiro',
    'Medicamento e tratamento',
    'RAG do caso',
    'Tarefas e pendências',
    'Log interno dos agentes',
    'Linha do tempo',
  ]) {
    await expect(panel.getByText(new RegExp(`^${section}`)).first()).toBeVisible();
  }

  await expect(panel.getByText('Ana Paula Ribeiro', { exact: true }).first()).toBeVisible();
  await expect(panel.getByText('Zolgensma', { exact: true }).first()).toBeVisible();
  await expect(panel.getByText('R$ 6.068.815,22', { exact: true }).first()).toBeVisible();

  const activeCaseDialog = page.getByRole('dialog', { name: 'Caso 360 e CRM' });
  const download = await activeCaseDialog.isVisible()
    ? activeCaseDialog.getByRole('button', { name: /^Baixar / }).first()
    : page.locator('[data-testid="conversation-case-360"]:visible').getByRole('button', { name: /^Baixar / }).first();
  await expect(download).toBeEnabled();
  const downloadResponse = page.waitForResponse((response) => response.request().method() === 'GET' && /documents\/.+download/.test(response.url()));
  await download.click();
  const downloadTicket = await downloadResponse;
  expect(downloadTicket.status()).toBe(200);
  const { downloadUrl } = await downloadTicket.json();
  const downloadedFile = await request.get(downloadUrl);
  expect(downloadedFile.status()).toBe(200);
  expect(downloadedFile.headers()['content-type']).toContain('application/pdf');
  expect((await downloadedFile.body()).subarray(0, 5).toString()).toBe('%PDF-');
  await expect.poll(() => page.locator('html').getAttribute('data-qa-opened-url')).not.toBeNull();

  await panel.getByRole('button', { name: 'Ver disponibilidades' }).click();
  const scheduleDialog = page.getByRole('dialog', { name: /Agendar reunião com/ });
  await expect(scheduleDialog).toBeVisible();
  await expect(scheduleDialog.locator('[aria-label="Horários disponíveis"] button').first()).toBeVisible();
  await scheduleDialog.getByRole('button', { name: 'Cancelar', exact: true }).click();
  if (useTouch) await expect(panel).not.toBeVisible();

  const conversationUrl = page.url();
  const ensureInspectorOpen = async () => {
    if (useTouch) await page.waitForTimeout(350);
    if (!await panel.isVisible()) {
      const openButton = page.locator('button[title*="Caso 360"]:visible').first();
      await expect(openButton).toBeVisible();
      await activateControl(page, openButton, useTouch);
      await expect(panel).toBeVisible();
    }
    if (useTouch) {
      await expect.poll(async () => {
        const viewport = page.viewportSize();
        const bounds = await panel.boundingBox();
        return Boolean(viewport && bounds
          && bounds.x >= 0
          && bounds.x + bounds.width <= viewport.width + 1);
      }).toBe(true);
    }
  };
  const contextualActions = [
    ['Abrir cliente 360', 'Cliente 360', false],
    ['Rastreamento', 'Rastreamento da origem', false],
    ['Documentos e RAG', 'Fontes locais do RAG', true],
    ['Contratos', 'Contratos e procurações', false],
    ['Financeiro', 'Financeiro do caso', false],
    ['Consultar fontes', 'Fontes locais do RAG', true],
    ['Auditoria', 'Histórico e auditoria', false],
  ] as const;
  for (const [name, title, hasNestedDialog] of contextualActions) {
    await test.step(`Ferramenta contextual: ${name}`, async () => {
      await ensureInspectorOpen();
      await activateControl(page, panel.getByRole('button', { name, exact: true }).first(), useTouch);
      const contextSheet = page.getByRole('dialog', { name: title, exact: true });
      await expect(contextSheet).toBeVisible();
      expect(page.url()).toBe(conversationUrl);
      await contextSheet.getByRole('button', { name: 'Close', exact: true }).click();
      await expect.poll(async () => (
        await contextSheet.count() === 0
        || await contextSheet.getAttribute('data-state') === 'closed'
      )).toBe(true);
      if (hasNestedDialog) {
        const parentSheet = page.getByRole('dialog', { name: 'Documentos e RAG', exact: true });
        await expect(parentSheet).toBeVisible();
        await parentSheet.getByRole('button', { name: 'Close', exact: true }).click();
        await expect(parentSheet).not.toBeVisible();
      }
    });
  }

  await ensureInspectorOpen();
  await activateControl(page, panel.getByRole('button', { name: 'Abrir agenda', exact: true }), useTouch);
  await expect(page.getByRole('dialog', { name: /Agendar reunião com/ })).toBeVisible();
  expect(page.url()).toBe(conversationUrl);
  await page.keyboard.press('Escape');
  expect(failures).toEqual([]);
});

test('CMED calcula KEYTRUDA, persiste no card, aparece em Conversas e é removido ao final', async ({ page, request }) => {
  test.setTimeout(60_000);
  const failures = captureRuntimeFailures(page);
  const context = await anaContext(request);
  let estimateId: string | null = null;

  try {
    await page.goto(`/app/leads?matter=${context.matterId}`, { waitUntil: 'domcontentloaded' });
    const panel = page.getByTestId('treatment-cost-panel');
    await expect(panel).toBeVisible();
    await panel.getByLabel('Buscar medicamento no catálogo CMED').fill('KEYTRUDA');
    const medication = panel.getByRole('button').filter({ hasText: 'KEYTRUDA' }).first();
    await expect(medication).toContainText('R$ 47.378,36');
    await medication.click();
    await panel.getByLabel('Apresentações por ciclo').fill('2');
    await panel.getByLabel('Número de ciclos').fill('3');
    await panel.getByLabel('Intervalo entre ciclos').fill('21');
    await panel.getByLabel('Duração total prescrita').fill('63');
    await panel.getByLabel('Referência da prescrição').fill('Prescrição QA · página 2');
    await panel.getByLabel('Notas internas do tratamento').fill('Cenário de QA removido automaticamente.');
    await panel.getByLabel('Status da revisão do tratamento').click();
    await page.getByRole('option', { name: 'Revisado', exact: true }).click();
    await expect(panel.getByText('R$ 284.270,16', { exact: true }).last()).toBeVisible();
    await expect(panel.getByText(/R\$ 100\.000,00 a R\$ 300\.000,00/)).toBeVisible();

    const saveResponse = page.waitForResponse((response) => response.request().method() === 'PUT' && response.url().endsWith(`/medications/matters/${context.matterId}/treatment-estimates`));
    await panel.getByRole('button', { name: /Salvar no dossiê|Atualizar cálculo/ }).click();
    const savedResponse = await saveResponse;
    expect(savedResponse.status()).toBe(200);
    const saved = await savedResponse.json();
    estimateId = saved.id;
    expect(saved).toMatchObject({
      presentationsPerCycle: 2,
      cycleCount: 3,
      totalTreatmentCost: 284270.16,
      reviewStatus: 'revisado',
      appliedToMatterEconomicValue: false,
    });
    await expect(page.getByText('Custo do tratamento salvo no dossiê.', { exact: true })).toBeVisible();

    const conversationPanel = await openCaseInspector(page, context);
    await expect(conversationPanel.getByText('KEYTRUDA', { exact: true })).toBeVisible();
    await expect(conversationPanel.getByText('R$ 284.270,16', { exact: true })).toBeVisible();
    await expect(conversationPanel.getByText('Fonte: Prescrição QA · página 2', { exact: true })).toBeVisible();
    expect(failures).toEqual([]);
  } finally {
    const estimatesResponse = await request.get(`${API_URL}/api/v1/medications/matters/${context.matterId}/treatment-estimates`, { headers: context.headers });
    if (estimatesResponse.status() === 200) {
      const estimates = await estimatesResponse.json();
      const qaEstimates = estimates.filter((item: { id: string; medicationReferenceId: string; prescriptionReference?: string | null }) => (
        item.id === estimateId
        || item.prescriptionReference === 'Prescrição QA · página 2'
      ));
      for (const estimate of qaEstimates) {
        const cleanup = await request.delete(`${API_URL}/api/v1/medications/matters/${context.matterId}/treatment-estimates/${estimate.id}`, { headers: context.headers });
        expect(cleanup.status()).toBe(204);
      }
    }
  }
});

test('política de cadência salva, simula atraso humano e é restaurada', async ({ page, request }) => {
  const failures = captureRuntimeFailures(page);
  const context = await anaContext(request);
  const baselineResponse = await request.get(`${API_URL}/api/v1/klm/messaging-cadence/settings`, { headers: context.headers });
  expect(baselineResponse.status()).toBe(200);
  const baseline = await baselineResponse.json();
  const changedMinimum = baseline.preDelayMinMs < baseline.preDelayMaxMs
    ? baseline.preDelayMinMs + 1
    : Math.max(0, baseline.preDelayMinMs - 1);

  try {
    await page.goto('/app/configuracoes', { waitUntil: 'domcontentloaded' });
    const panel = page.getByTestId('messaging-cadence-settings');
    await expect(panel).toBeVisible();
    await panel.getByLabel('Atraso inicial mínimo').fill(String(changedMinimum));
    const saveResponse = page.waitForResponse((response) => response.request().method() === 'PUT' && response.url().endsWith('/api/v1/klm/messaging-cadence/settings'));
    await panel.getByRole('button', { name: 'Salvar política', exact: true }).click();
    expect((await saveResponse).status()).toBe(200);
    await expect(page.getByText('Cadência salva e registrada na auditoria.', { exact: true })).toBeVisible();

    await panel.getByLabel('Mensagem para simular cadência').fill('Revisei seus documentos e vou explicar o próximo passo agora.');
    const previewResponse = page.waitForResponse((response) => response.request().method() === 'POST' && response.url().endsWith('/api/v1/klm/messaging-cadence/preview'));
    await panel.getByRole('button', { name: 'Simular', exact: true }).click();
    const preview = await (await previewResponse).json();
    expect(preview.totalDelayMs).toBeGreaterThanOrEqual(preview.typingDurationMs);
    await expect(panel.getByText('Antes de digitar', { exact: true })).toBeVisible();
    await expect(panel.getByText('Digitando', { exact: true })).toBeVisible();
    await expect(panel.getByText('Total', { exact: true })).toBeVisible();
    expect(failures).toEqual([]);
  } finally {
    const restore = await request.put(`${API_URL}/api/v1/klm/messaging-cadence/settings`, { headers: context.headers, data: baseline });
    expect(restore.status()).toBe(200);
  }
});

test('controles reparados de áreas, KPI, TI, áudio e omnichannel executam ação observável', async ({ page, request }, testInfo) => {
  test.setTimeout(90_000);
  const failures = captureRuntimeFailures(page);
  const context = await anaContext(request);

  await page.goto('/app/areas', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Duplicar', exact: true }).click();
  const blueprintDialog = page.getByRole('dialog', { name: 'Duplicar blueprint' });
  await expect(blueprintDialog.getByLabel('Nome da área')).toHaveValue(/\(cópia\)$/);
  await blueprintDialog.getByRole('button', { name: 'Cancelar' }).click();

  await page.goto('/app/kpis', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: /Aquisição/ }).last().click();
  await page.getByRole('button', { name: 'Ver definição', exact: true }).click();
  const kpiDialog = page.getByRole('dialog', { name: 'Aquisição' });
  await expect(kpiDialog).toContainText('Fonte');
  await expect(kpiDialog).toContainText('sistema');
  await expect(kpiDialog).toContainText(/\d+ registros/);
  await page.keyboard.press('Escape');

  await page.goto('/app/ti', { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Selecionar evidência local').setInputFiles({
    name: 'evidencia-qa.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('QA local'),
  });
  await expect(page.getByRole('button', { name: /evidencia-qa\.txt/ })).toBeVisible();
  await page.getByRole('tab', { name: 'Fila técnica' }).click();
  await page.getByTestId('technical-ticket-row').first().click();
  const ticketDialog = page.getByRole('dialog');
  await expect(ticketDialog).toBeVisible();
  await expect(ticketDialog).toContainText('Tela');
  await page.getByRole('button', { name: 'Fechar', exact: true }).click();

  await page.goto(`/app/audio?thread=${context.threadId}`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('button', { name: 'Abrir fontes', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Abrir fontes', exact: true }).click();
  await expect.poll(() => new URL(page.url()).pathname).toBe('/app/leads');
  expect(new URL(page.url()).searchParams.get('matter')).toBe(context.matterId);
  expect(new URL(page.url()).searchParams.get('thread')).toBe(context.threadId);
  expect(new URL(page.url()).searchParams.get('panel')).toBe('rag');

  await page.goto(`/app/omnichannel?thread=${context.threadId}&matter=${context.matterId}`, { waitUntil: 'domcontentloaded' });
  if (testInfo.project.name === 'mobile-393') {
    await expect(page.getByRole('heading', { name: 'Ana Paula Ribeiro', exact: true })).toBeVisible();
  }
  const openClientWorkspace = page.getByRole('button', { name: 'Abrir cliente 360', exact: true });
  if (!await openClientWorkspace.isVisible()) {
    await page.getByRole('button', {
      name: /^Abrir (?:painel )?Caso 360(?: de .*)?$/,
    }).first().click();
    await expect(page.getByRole('dialog', { name: 'Caso 360 e CRM', exact: true })).toBeVisible();
  }
  await openClientWorkspace.click();
  const clientWorkspace = page.getByRole('dialog', { name: 'Cliente 360', exact: true });
  await expect(clientWorkspace).toBeVisible();
  await expect(clientWorkspace).toContainText('Ana Paula Ribeiro');
  expect(new URL(page.url()).pathname).toBe('/app/conversas');
  expect(new URL(page.url()).searchParams.get('matter')).toBe(context.matterId);
  expect(new URL(page.url()).searchParams.get('thread')).toBe(context.threadId);
  expect(failures).toEqual([]);
});
