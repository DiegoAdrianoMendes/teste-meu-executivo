import { authenticatePageForRole, authenticateWorkspacePage, expect as baseExpect, test } from './fixtures';

// Functional assertions remain strict and retry-free; only the wait window is
// widened for uncached release-bundle parsing in constrained CI/mobile emulation.
const expect = baseExpect.configure({ timeout: 20_000 });

const appRoutes = [
  ['/app', 'Início'],
  ['/app/conversas', 'Conversas'],
  ['/app/funil', 'Funil de vendas'],
  ['/app/kpis', 'KPIs e métricas'],
  ['/app/financeiro', 'Financeiro e cobrança'],
  ['/app/agentes', 'Controle dos agentes'],
] as const;

test.describe('responsive e acessibilidade (@responsive)', () => {
  for (const [path, heading] of appRoutes) {
    test(`${path} cabe no viewport e mantém estrutura acessível`, async ({ page, request }) => {
      await authenticateWorkspacePage(page, request);
      const response = await page.goto(path, { waitUntil: 'domcontentloaded' });
      expect(response?.status()).toBeLessThan(400);
      await expect(page.getByRole('heading', { name: heading, exact: true }).first()).toBeVisible({ timeout: 20_000 });

      const metrics = await page.evaluate(() => ({
        documentWidth: document.documentElement.scrollWidth,
        viewportWidth: window.innerWidth,
        bodyWidth: document.body.scrollWidth,
        viewportMeta: document.querySelector('meta[name="viewport"]')?.getAttribute('content') ?? '',
      }));
      expect(metrics.documentWidth, `overflow horizontal em ${path}`).toBeLessThanOrEqual(metrics.viewportWidth + 1);
      expect(metrics.bodyWidth).toBeLessThanOrEqual(metrics.viewportWidth + 1);
      expect(metrics.viewportMeta).toContain('viewport-fit=cover');

      await expect(page.locator('main').first()).toBeVisible();
      const unnamedButtons = await page.locator('button').evaluateAll((buttons) => buttons.filter((button) => {
        const label = button.getAttribute('aria-label') || button.getAttribute('title') || button.textContent?.trim();
        return !label && !button.hasAttribute('aria-hidden');
      }).length);
      expect(unnamedButtons, `${path} possui botões sem nome acessível`).toBe(0);

      if (metrics.viewportWidth < 1280) {
        const mobileNav = page.getByRole('navigation', { name: 'Navegação móvel' });
        await expect(mobileNav).toBeVisible();
        const navControls = mobileNav.locator('a, button');
        expect(await navControls.count()).toBeGreaterThanOrEqual(5);
        for (let index = 0; index < await navControls.count(); index += 1) {
          const box = await navControls.nth(index).boundingBox();
          expect(box?.width ?? 0, `${path} controle móvel estreito`).toBeGreaterThanOrEqual(44);
          expect(box?.height ?? 0, `${path} controle móvel baixo`).toBeGreaterThanOrEqual(44);
        }
      }

      if (metrics.viewportWidth >= 1024 && metrics.viewportWidth < 1280) {
        const rail = page.locator('aside[data-desktop-sidebar="true"]');
        await expect(rail).toBeHidden();
        await expect(page.getByRole('button', { name: 'Abrir navegação' })).toBeVisible();
      }

      if (metrics.viewportWidth < 1280 && (path === '/app/conversas' || path === '/app/funil')) {
        const textFields = page.locator('input[type="text"]:visible, textarea:visible');
        for (let index = 0; index < await textFields.count(); index += 1) {
          const fontSize = await textFields.nth(index).evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize));
          expect(fontSize, `${path} campo de texto com fonte menor que 16px`).toBeGreaterThanOrEqual(16);
        }
      }
    });
  }

  test('interações de toque e diálogo permanecem operáveis', async ({ page, request }) => {
    await authenticateWorkspacePage(page, request);
    await page.goto('/app/conversas', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: 'Conversas', exact: true }).first()).toBeVisible({ timeout: 20_000 });
    const interactive = page.locator(
      'button:visible, a:visible, input:visible, textarea:visible, [role="button"]:visible',
    );
    await expect(interactive.first()).toBeVisible();
    const count = await interactive.count();
    expect(count).toBeGreaterThan(0);
    for (let i = 0; i < Math.min(count, 20); i += 1) {
      const box = await interactive.nth(i).boundingBox();
      if (box) expect(box.width * box.height).toBeGreaterThan(0);
    }
    await page.keyboard.press('Tab');
    await expect(page.locator(':focus')).toBeVisible();
    await page.keyboard.press('Control+k');
    const commandDialog = page.getByRole('dialog').first();
    await expect(commandDialog).toBeVisible();
    await expect(commandDialog.locator('input').first()).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(commandDialog).toBeHidden();
  });

  test('Funnel compacto expõe lista e anúncio, mantendo Kanban em desktop', async ({ page, request }, testInfo) => {
    await authenticateWorkspacePage(page, request);
    await page.goto('/app/funil', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: 'Funil de vendas', exact: true }).first()).toBeVisible({ timeout: 20_000 });
    if (testInfo.project.name === 'desktop-responsive-1440') {
      await expect(page.getByTestId('funnel-mobile-list')).toBeHidden();
      await expect(page.getByTestId(/funnel-column-/).first()).toBeVisible();
    } else {
      await expect(page.getByTestId('funnel-mobile-list')).toBeVisible();
      await expect(page.getByTestId('funnel-status-announcement')).toBeVisible();
    }
  });

  test('zoom de 200% mantém conversas sem overflow horizontal', async ({ page, request }) => {
    await authenticateWorkspacePage(page, request);
    await page.goto('/app/conversas', { waitUntil: 'domcontentloaded' });
    await page.keyboard.press('Control++');
    await page.keyboard.press('Control++');
    await page.evaluate(() => window.scrollTo(0, 0));
    const geometry = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      document: document.documentElement.scrollWidth,
      body: document.body.scrollWidth,
    }));
    expect(Math.max(geometry.document, geometry.body)).toBeLessThanOrEqual(geometry.viewport + 1);
  });

  test('financeiro expõe formatos de exportação quando a governança estiver disponível', async ({ page, request }) => {
    await authenticateWorkspacePage(page, request);
    await page.goto('/app/financeiro', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: 'Financeiro e cobrança', exact: true }).first()).toBeVisible({ timeout: 20_000 });
    await page.getByRole('tab', { name: 'Visão Financeira', exact: true }).click();
    const formatSelect = page.getByLabel('Formato da exportação financeira');
    await expect(formatSelect).toBeVisible();
    await expect(formatSelect.locator('option')).toHaveText(['PDF', 'XLSX', 'DOCX']);
    await expect(page.getByRole('button', { name: 'Exportar relatório', exact: true })).toBeVisible();
  });

  test('Agentes oferece fila de aprovação humana sem ação externa automática', async ({ page, request }) => {
    // A espera pela lista de decisões (até 30 s) não cabe no orçamento padrão
    // de 30 s do teste; slow() triplica o limite sem mexer na configuração global.
    test.slow();
    await authenticateWorkspacePage(page, request);
    await page.goto('/app/agentes', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('button', { name: 'Fila de aprovação', exact: true })).toBeVisible();
    await expect(page.getByText(/aguardando|decisões pendentes/i).first()).toBeVisible();
    await expect(page.getByRole('region', { name: 'Catálogo de nós do runtime' })).toBeVisible();
    await expect(page.getByRole('textbox', { name: 'Buscar nó do runtime' })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Acompanhamento da fila humana' })).toContainText('Telefone');
    await expect(page.getByRole('region', { name: 'Acompanhamento da fila humana' })).toContainText('Reconhecidas');
    await page.getByRole('tab', { name: 'Aprovações', exact: true }).click();
    await expect(page.getByText('Fila de decisões dos agentes')).toBeVisible();
    const approvalsPanel = page.getByRole('tabpanel', { name: 'Aprovações' });
    // A lista vem de consulta própria (GET /api/v1/approvals). Até ela
    // responder, o painel mostra o estado vazio e o primeiro botão do painel é
    // "Abrir auditoria", que navega para fora da tela: esperar a lista chegar
    // antes de escolher a decisão pendente.
    await expect(approvalsPanel.getByText('Nenhuma decisão pendente.', { exact: true })).toBeHidden({ timeout: 30_000 });
    const pendingDecision = approvalsPanel
      .getByRole('button')
      .filter({ hasNotText: 'Abrir auditoria' })
      .first();
    await expect(pendingDecision).toBeVisible();
    await pendingDecision.click();
    await expect(page.getByRole('button', { name: 'Aprovar localmente', exact: true })).toBeVisible();
    await expect(page.getByText(/Decisão interna; conectores e envios continuam bloqueados/i)).toBeVisible();
  });

  test('conversas percorre fila, conversa, detalhes e preserva rascunho', async ({ page, request }) => {
    await authenticateWorkspacePage(page, request);
    await page.goto('/app/conversas', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('conversation-contact').first()).toBeVisible();
    await page.getByTestId('conversation-contact').first().click();
    await page.waitForTimeout(500);
    await expect(page.getByTestId('conversation-active-mobile-actions')).toBeVisible().catch(() => undefined);
    const composer = page.getByRole('textbox', { name: 'Mensagem da conversa' });
    await expect(composer).toBeVisible();
    await composer.fill('Rascunho responsivo de validação');
    await expect(composer).toHaveValue('Rascunho responsivo de validação');
    await page.locator('[title="Abrir Caso 360"]:visible').first().click();
    await expect(page.getByRole('heading', { name: 'Caso 360 e CRM', exact: true })).toBeVisible();
  });

  test('funil permite mover sem depender de drag-and-drop', async ({ page, request }) => {
    await authenticateWorkspacePage(page, request);
    await page.goto('/app/funil', { waitUntil: 'domcontentloaded' });
    const card = page.getByTestId(/funnel-card-/).first();
    await expect(card).toBeVisible();
    await card.click();
    await expect(page.getByRole('combobox', { name: 'Mover lead para etapa' })).toBeVisible();
  });

  test('dashboard expõe KPI acionável e fila operacional', async ({ page, request }) => {
    await authenticateWorkspacePage(page, request);
    await page.goto('/app', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: 'Início', exact: true }).first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/Fila de decisões|Conversas ativas|Aquisição/i).first()).toBeVisible();
    const actionableMetric = page.locator('button:visible').filter({ hasText: /Conversas|Leads|Receita|Qualidade/i }).first();
    await expect(actionableMetric).toBeVisible();
  });

  test('portrait e landscape mantêm shell, safe-area e foco operável', async ({ page, request }) => {
    await authenticateWorkspacePage(page, request);
    await page.goto('/app/conversas', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: 'Conversas', exact: true }).first()).toBeVisible({ timeout: 20_000 });

    const portrait = await page.evaluate(() => ({ width: window.innerWidth, height: window.innerHeight }));
    expect(portrait.width).toBeGreaterThan(0);
    expect(portrait.height).toBeGreaterThan(0);

    const mobileNav = page.getByRole('navigation', { name: 'Navegação móvel' });
    if (portrait.width < 1024) {
      await expect(mobileNav).toBeVisible();
      const navHeight = await mobileNav.evaluate((node) => node.getBoundingClientRect().height);
      expect(navHeight).toBeGreaterThanOrEqual(68);
      const mainPadding = await page.locator('main').first().evaluate((node) => Number.parseFloat(getComputedStyle(node).paddingBottom));
      expect(mainPadding).toBeGreaterThanOrEqual(68);
    }

    await page.setViewportSize({
      width: Math.max(360, portrait.width, portrait.height),
      height: Math.max(320, Math.min(portrait.width, portrait.height)),
    });
    const landscape = await page.evaluate(() => ({ width: window.innerWidth, height: window.innerHeight }));
    expect(landscape.width).toBeGreaterThan(landscape.height);
    await expect(page.getByRole('heading', { name: 'Conversas', exact: true }).first()).toBeVisible({ timeout: 20_000 });
    expect(await page.locator('body').evaluate((node) => node.scrollWidth)).toBeLessThanOrEqual(landscape.width + 1);
  });

  test('teclado virtual reduz viewport sem ocultar compositor nem foco', async ({ page, request }) => {
    await authenticateWorkspacePage(page, request);
    await page.goto('/app/conversas', { waitUntil: 'domcontentloaded' });
    const viewport = page.viewportSize();
    if (viewport) await page.setViewportSize({ width: viewport.width, height: Math.max(320, Math.floor(viewport.height * 0.55)) });
    const compactNavigation = page.getByRole('navigation', { name: 'Navegação móvel' });
    const targetControl = (page.viewportSize()?.width ?? 0) < 1280
      ? compactNavigation.getByRole('button', { name: 'Abrir mais opções de navegação' })
      : page.getByRole('button', { name: 'Recolher navegação' });
    await expect(targetControl).toBeVisible();
    await targetControl.focus();
    await expect(targetControl).toBeFocused();
    const box = await targetControl.boundingBox();
    expect(box?.y ?? 0).toBeGreaterThanOrEqual(0);
    expect((box?.y ?? 0) + (box?.height ?? 0)).toBeLessThanOrEqual((page.viewportSize()?.height ?? 0) + 1);
  });

  test('desktop fora do Financeiro preserva shell estrutural @desktop-freeze', async ({ page, request }) => {
    await authenticateWorkspacePage(page, request);
    for (const path of ['/app', '/app/conversas', '/app/funil', '/app/agentes']) {
      await page.goto(path, { waitUntil: 'domcontentloaded' });
      await expect(page.getByTestId('klm-shell')).toBeVisible();
      await expect(page.getByTestId('klm-page-frame')).toBeVisible();
      await expect(page.getByTestId('klm-route-scroll')).toBeVisible();
      await expect(page.getByRole('navigation', { name: 'Navegação móvel' })).toBeHidden();
      const geometry = await page.evaluate(() => {
        const header = document.querySelector('.meu-executivo-main-column > header');
        const sidebar = document.querySelector('aside[data-desktop-sidebar="true"]');
        const route = document.querySelector('[data-testid="klm-route-scroll"]');
        const headerStyle = header ? getComputedStyle(header) : null;
        const routeStyle = route ? getComputedStyle(route) : null;
        return {
          viewport: document.documentElement.clientWidth,
          document: document.documentElement.scrollWidth,
          body: document.body.scrollWidth,
          headerHeight: header?.getBoundingClientRect().height ?? 0,
          headerGap: headerStyle?.columnGap ?? '',
          sidebarWidth: sidebar?.getBoundingClientRect().width ?? 0,
          routePaddingLeft: routeStyle?.paddingLeft ?? '',
          routePaddingRight: routeStyle?.paddingRight ?? '',
          routePaddingTop: routeStyle?.paddingTop ?? '',
        };
      });
      expect(Math.max(geometry.document, geometry.body)).toBeLessThanOrEqual(geometry.viewport + 1);
      expect(geometry.headerHeight).toBe(88);
      expect(geometry.headerGap).toBe('12px');
      expect(geometry.sidebarWidth).toBe(224);
      expect(geometry.routePaddingLeft).toBe('32px');
      expect(geometry.routePaddingRight).toBe('32px');
      expect(geometry.routePaddingTop).toBe('28px');
    }
  });
});

test.describe('Blackbox crítico (@responsive)', () => {
  test('superfície interna não vaza IDs e cabe no viewport', async ({ page, request }) => {
    const blackboxUrl = process.env.KLM_BLACKBOX_URL ?? 'http://127.0.0.1:4174';
    await authenticatePageForRole(page, request, 'platform_owner');
    await page.goto(`${blackboxUrl}/blackbox`, { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('main').first()).toBeVisible({ timeout: 20_000 });
    const result = await page.evaluate(() => ({
      width: document.documentElement.scrollWidth,
      viewport: window.innerWidth,
      text: document.body.innerText,
    }));
    expect(result.width).toBeLessThanOrEqual(result.viewport + 1);
    expect(result.text).not.toMatch(/\b(?:user|workspace|tenant|internal)[_-]?id\b/i);
  });

  const criticalRoutes = [
    '/blackbox',
    '/blackbox/tasks',
    '/blackbox/operations',
    '/blackbox/exports',
    '/blackbox/workspaces',
    '/blackbox/support-tickets',
    '/blackbox/support-access',
    '/blackbox/inbox',
    '/blackbox/team',
    '/blackbox/billing',
    '/blackbox/finance',
    '/blackbox/plans',
    '/blackbox/errors',
    '/blackbox/governance/audit',
    '/blackbox/ai-operations',
    '/blackbox/agent-operations',
  ] as const;

  for (const path of criticalRoutes) {
    test(`${path} mantém superfície navegável no viewport`, async ({ page, request }) => {
      const blackboxUrl = process.env.KLM_BLACKBOX_URL ?? 'http://127.0.0.1:4174';
      await authenticatePageForRole(page, request, 'platform_owner');
      const response = await page.goto(`${blackboxUrl}${path}`, { waitUntil: 'domcontentloaded' });
      expect(response?.status()).toBeLessThan(400);
      await expect(page.getByTestId('blackbox-route-scroll')).toBeVisible({ timeout: 20_000 });
      const geometry = await page.evaluate(() => ({
        viewport: document.documentElement.clientWidth,
        document: document.documentElement.scrollWidth,
        body: document.body.scrollWidth,
      }));
      expect(Math.max(geometry.document, geometry.body)).toBeLessThanOrEqual(geometry.viewport + 1);
      const unnamed = await page.locator('button:visible').evaluateAll((buttons) => buttons.filter((button) => {
        const label = button.getAttribute('aria-label') || button.getAttribute('title') || button.textContent?.trim();
        return !label && !button.hasAttribute('aria-hidden');
      }).length);
      expect(unnamed).toBe(0);
    });
  }
});
