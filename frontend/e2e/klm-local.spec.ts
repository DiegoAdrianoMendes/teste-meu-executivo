import { randomUUID } from 'node:crypto';
import {
  ensurePublishedAgentDeployment,
  expect,
  test,
  type APIRequestContext,
  type Page,
} from './fixtures';

const API_URL = process.env.KLM_API_URL ?? 'http://127.0.0.1:8010';

const routes = [
  ['/app', 'Início'],
  ['/app/conversas', 'Conversas'],
  ['/app/disparos', 'Disparos e listas'],
  ['/app/equipe', 'Equipe interna'],
  ['/app/leads', 'CRM'],
  ['/app/funil', 'Funil de vendas'],
  ['/app/kpis', 'KPIs e métricas'],
  ['/app/rastreamento', 'Rastreamento'],
  ['/app/integracoes', 'Integrações e webhooks'],
  ['/app/qualidade', 'Agentes'],
  ['/app/playbooks', 'Playbooks'],
  ['/app/ferramentas', 'Ferramentas'],
  ['/app/flows', 'Flows'],
  ['/app/omnichannel', 'Conversas'],
  ['/app/comentarios', 'Comentários e mensagens sociais'],
  ['/app/agenda', 'Agenda'],
  ['/app/agenda/configuracoes', 'Disponibilidade e booking'],
  ['/app/contratos', 'Contratos'],
  ['/app/financeiro', 'Financeiro'],
  ['/app/peticoes', 'Petições'],
  ['/app/audio', 'Configurações'],
  ['/app/agentes', 'Agentes'],
  ['/app/areas', 'Áreas e times'],
  ['/app/ti', 'TI Agent'],
  ['/app/configuracoes', 'Configurações'],
  ['/app/atividade', 'Atividade'],
  ['/app/aprovacoes', 'Aprovações'],
] as const;

function captureRuntimeFailures(page: Page) {
  const failures: string[] = [];
  page.on('console', (message) => {
    if (message.type() !== 'error' || message.text().startsWith('Failed to load resource:')) return;
    const location = message.location().url ? ` @ ${message.location().url}` : '';
    failures.push(`console: ${message.text()}${location}`);
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

async function retryTransientRequest<T>(operation: () => Promise<T>, attempts = 3): Promise<T> {
  let lastError: unknown;

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return await operation();
    } catch (error) {
      lastError = error;
      if (attempt < attempts) {
        await new Promise((resolve) => setTimeout(resolve, attempt * 150));
      }
    }
  }

  throw lastError;
}

function meetingDeleteUrl(meeting: { id: string; version: number }): string {
  return `${API_URL}/api/v1/meetings/${meeting.id}?expectedVersion=${encodeURIComponent(meeting.version)}`;
}

function browserDateTimeLocalValue(value: Date): string {
  const localWallClock = new Date(value.getTime() - value.getTimezoneOffset() * 60_000);
  return localWallClock.toISOString().slice(0, 16);
}

async function inviteAndActivateAgendaMember(
  request: APIRequestContext,
  input: {
    headers: Record<string, string>;
    name: string;
    email: string;
    runId: string;
  },
): Promise<{ id: string; name: string }> {
  const invitationResponse = await request.post(`${API_URL}/api/v1/admin/team-members`, {
    headers: {
      ...input.headers,
      'Idempotency-Key': `e2e-agenda-invite:${input.runId}:${randomUUID()}`,
    },
    data: {
      name: input.name,
      email: input.email,
      workspaceRole: 'legal_manager',
      permissionGrants: [],
      permissionDenials: [],
    },
  });
  expect(invitationResponse.status(), await invitationResponse.text()).toBe(202);
  const invitation = await invitationResponse.json() as {
    userId: string;
    invitationId: string;
  };

  const deliveryResponse = await request.get(
    `${API_URL}/api/v1/klm/local/invitations/${invitation.invitationId}/delivery`,
    { headers: input.headers },
  );
  expect(deliveryResponse.status(), await deliveryResponse.text()).toBe(200);
  const delivery = await deliveryResponse.json() as { link: string };
  const invitationToken = new URL(delivery.link, 'http://localhost').searchParams.get('token');
  expect(invitationToken).toBeTruthy();

  const acceptanceResponse = await request.post(
    `${API_URL}/api/v1/auth/invitations/${invitation.invitationId}/accept`,
    {
      headers: { 'Idempotency-Key': `e2e-agenda-accept:${input.runId}:${randomUUID()}` },
      data: {
        token: invitationToken,
        password: `E2e!Agenda-${input.runId}-9x`,
      },
    },
  );
  expect(acceptanceResponse.status(), await acceptanceResponse.text()).toBe(200);
  return { id: invitation.userId, name: input.name };
}

async function restoreFunnelCard(
  request: APIRequestContext,
  headers: Record<string, string>,
  card: { id: string; stage: string; cardOrder?: number },
) {
  const latestResponse = await retryTransientRequest(() => request.get(`${API_URL}/api/v1/klm/funnel`, { headers }));
  const latest = await latestResponse.json();
  const current = latest.cards.find((item: { id: string }) => item.id === card.id);
  return retryTransientRequest(() =>
    request.patch(`${API_URL}/api/v1/klm/funnel/${card.id}/stage`, {
      headers,
      data: {
        columnId: card.stage,
        sourceColumnId: current?.stage ?? card.stage,
        position: card.cardOrder ?? 0,
        expectedBoardVersion: latest.boardVersion,
        operationId: randomUUID(),
      },
    }),
  );
}

test.afterEach(async ({ request }) => {
  try {
    const sessionResponse = await request.post(`${API_URL}/api/v1/klm/local/session`);
    if (sessionResponse.status() !== 200) return;
    const session = await sessionResponse.json();
    const token = session.accessToken ?? session.access_token;
    await request.delete(`${API_URL}/api/v1/klm/local/verification-artifacts`, {
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    // A falha original do teste continua sendo a evidência principal.
  }
});

test('todas as rotas do app renderizam sem erro ou overflow', async ({ page }) => {
  // Esta matriz preserva as 27 verificações que antes tinham 30 s cada.
  // O orçamento maior vale somente para o conjunto; cada tela continua com
  // seu timeout estrito, portanto uma rota lenta ainda reprova isoladamente.
  test.setTimeout(120_000)

  const failures = captureRuntimeFailures(page);
  for (const [index, [path, title]] of routes.entries()) {
    if (index === 0) {
      const response = await page.goto(path, { waitUntil: 'domcontentloaded' });
      expect(response?.status()).toBeLessThan(400);
    } else {
      const previousUrl = page.url()

      // Cria a entrada seguinte e usa back/forward reais do navegador. Assim
      // o React Router recebe um PopStateEvent confiável, como na navegação do
      // usuário, sem recarregar a aplicação e cancelar requests em andamento.
      await page.evaluate((nextPath) => {
        window.history.pushState({}, '', nextPath)
        window.history.back()
      }, path)
      await expect(page).toHaveURL(previousUrl)
      await page.goForward({ waitUntil: 'commit' })
    }

    const canonicalPath = path === '/app/omnichannel' ? '/app/conversas' : path
    await expect(page).toHaveURL(new RegExp(`${canonicalPath.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`))
    // O shell mantém o título de contexto como h1; páginas internas podem
    // começar em h2 sem perder a hierarquia acessível.
    await expect(page.getByRole('heading', { name: title, exact: true }).first()).toBeVisible();
    await page.waitForTimeout(250);

    const geometry = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      layoutViewport: window.innerWidth,
      document: document.documentElement.scrollWidth,
      body: document.body.scrollWidth,
      viewportHeight: window.innerHeight,
      shellHeight: document.querySelector('[data-testid="klm-shell"]')?.getBoundingClientRect().height ?? Number.NaN,
      shellBottom: document.querySelector('[data-testid="klm-shell"]')?.getBoundingClientRect().bottom ?? Number.NaN,
      routeTop: document.querySelector('[data-testid="klm-route-scroll"]')?.getBoundingClientRect().top ?? Number.NaN,
      routeBottom: document.querySelector('[data-testid="klm-route-scroll"]')?.getBoundingClientRect().bottom ?? Number.NaN,
      routeOverflowY: getComputedStyle(document.querySelector('[data-testid="klm-route-scroll"]')!).overflowY,
      rootOverflow: getComputedStyle(document.documentElement).overflow,
      bodyOverflow: getComputedStyle(document.body).overflow,
    }));
    expect(Math.max(geometry.document, geometry.body) - geometry.viewport).toBeLessThanOrEqual(1);
    expect(Math.abs(geometry.layoutViewport - geometry.viewport)).toBeLessThanOrEqual(1);
    expect(Number.isFinite(geometry.shellHeight)).toBe(true);
    expect(Math.abs(geometry.shellHeight - geometry.viewportHeight)).toBeLessThanOrEqual(1);
    expect(geometry.shellBottom).toBeLessThanOrEqual(geometry.viewportHeight + 1);
    expect(geometry.routeTop).toBeGreaterThanOrEqual(0);
    expect(geometry.routeBottom).toBeLessThanOrEqual(geometry.shellBottom + 1);
    expect(geometry.routeOverflowY).toBe('auto');
    expect(geometry.rootOverflow).toBe('hidden');
    expect(geometry.bodyOverflow).toBe('hidden');
    await page.evaluate(() => window.scrollTo({ top: 1_000 }));
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    expect(failures, `falhas de runtime em ${path}`).toEqual([]);
    failures.length = 0;
  }
});

test('conversas limita os painéis ao shell e rola a lista dentro do box', async ({ page }) => {
  await page.goto('/app/conversas', { waitUntil: 'domcontentloaded' });
  await expect(page.getByTestId('conversation-contact').first()).toBeVisible();

  const geometry = await page.evaluate(() => {
    const shell = document.querySelector('[data-testid="klm-shell"]')!;
    const panel = document.querySelector('[data-testid="conversation-list-scroll"]')!.closest('section')!;
    const scroller = document.querySelector('[data-testid="conversation-list-scroll"]')!;
    return {
      shellBottom: shell.getBoundingClientRect().bottom,
      panelBottom: panel.getBoundingClientRect().bottom,
      panelTop: panel.getBoundingClientRect().top,
      clientHeight: scroller.clientHeight,
      scrollHeight: scroller.scrollHeight,
      overflowY: getComputedStyle(scroller).overflowY,
    };
  });

  expect(geometry.panelTop).toBeGreaterThanOrEqual(0);
  expect(geometry.panelBottom).toBeLessThanOrEqual(geometry.shellBottom + 1);
  expect(geometry.overflowY).toBe('auto');
  expect(geometry.scrollHeight).toBeGreaterThan(geometry.clientHeight);

  const scroller = page.getByTestId('conversation-list-scroll');
  await scroller.evaluate((element) => element.scrollTo({ top: 500, behavior: 'instant' }));
  expect(await scroller.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
});

test('assistente nativo opera dentro do editor da conversa sem trocar de tela', async ({ page }) => {
  await page.goto('/app/conversas', { waitUntil: 'domcontentloaded' });
  if ((page.viewportSize()?.width ?? 0) < 1024) {
    await expect(page.getByTestId('conversation-contact').first()).toBeVisible();
    await page.getByTestId('conversation-contact').first().click();
  }
  const composer = page.getByTestId('conversation-composer');
  const assistant = page.getByTestId('conversation-composer-assistant');
  await expect(composer).toBeVisible();
  await expect(assistant).toBeVisible();

  const geometry = await page.evaluate(() => {
    const composerBox = document.querySelector('[data-testid="conversation-composer"]')!.getBoundingClientRect();
    const assistantBox = document.querySelector('[data-testid="conversation-composer-assistant"]')!.getBoundingClientRect();
    return {
      composer: { left: composerBox.left, top: composerBox.top, right: composerBox.right, bottom: composerBox.bottom },
      assistant: { left: assistantBox.left, top: assistantBox.top, right: assistantBox.right, bottom: assistantBox.bottom },
    };
  });
  expect(geometry.assistant.left).toBeGreaterThanOrEqual(geometry.composer.left);
  expect(geometry.assistant.top).toBeGreaterThanOrEqual(geometry.composer.top);
  expect(geometry.assistant.right).toBeLessThanOrEqual(geometry.composer.right);
  expect(geometry.assistant.bottom).toBeLessThanOrEqual(geometry.composer.bottom);

  const urlBefore = page.url();
  await assistant.click();
  const panel = page.getByLabel('Assistente Meu Executivo no campo de resposta');
  await expect(panel).toBeVisible();
  await expect(panel.getByText('Assistente Meu Executivo')).toBeVisible();
  expect(page.url()).toBe(urlBefore);
});

test('flows usa painéis limitados no desktop e workspace empilhado nos viewports menores', async ({ page }) => {
  await page.goto('/app/flows', { waitUntil: 'domcontentloaded' });
  await expect(page.getByTestId('flow-workspace')).toBeVisible();

  if ((page.viewportSize()?.width ?? 0) < 1280) {
    const geometry = await page.evaluate(() => {
      const pageRoot = document.querySelector('[data-testid="flows-page"]')!;
      const route = document.querySelector('[data-testid="klm-route-scroll"]')!;
      return {
        pageClientHeight: pageRoot.clientHeight,
        pageScrollHeight: pageRoot.scrollHeight,
        pageOverflowY: getComputedStyle(pageRoot).overflowY,
        routeBottom: route.getBoundingClientRect().bottom,
        viewportHeight: window.innerHeight,
      };
    });

    expect(geometry.pageOverflowY).toBe('auto');
    expect(geometry.pageScrollHeight).toBeGreaterThan(geometry.pageClientHeight);
    expect(geometry.routeBottom).toBeLessThanOrEqual(geometry.viewportHeight + 1);

    const pageRoot = page.getByTestId('flows-page');
    await pageRoot.evaluate((element) => element.scrollTo({ top: 500, behavior: 'instant' }));
    expect(await pageRoot.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    return;
  }

  const geometry = await page.evaluate(() => {
    const rect = (testId: string) => document.querySelector(`[data-testid="${testId}"]`)!.getBoundingClientRect();
    const route = rect('klm-route-scroll');
    const workspace = rect('flow-workspace');
    const palette = document.querySelector('[data-testid="flow-block-palette"]')!;
    const paletteRect = palette.getBoundingClientRect();
    const inspector = rect('flow-inspector');
    return {
      routeBottom: route.bottom,
      workspaceBottom: workspace.bottom,
      paletteBottom: paletteRect.bottom,
      inspectorBottom: inspector.bottom,
      paletteClientHeight: palette.clientHeight,
      paletteScrollHeight: palette.scrollHeight,
      paletteOverflowY: getComputedStyle(palette).overflowY,
    };
  });

  expect(geometry.workspaceBottom).toBeLessThanOrEqual(geometry.routeBottom + 1);
  expect(geometry.paletteBottom).toBeLessThanOrEqual(geometry.routeBottom + 1);
  expect(geometry.inspectorBottom).toBeLessThanOrEqual(geometry.routeBottom + 1);
  expect(geometry.paletteOverflowY).toBe('auto');
  expect(geometry.paletteScrollHeight).toBeGreaterThan(geometry.paletteClientHeight);

  const palette = page.getByTestId('flow-block-palette');
  await palette.evaluate((element) => element.scrollTo({ top: 500, behavior: 'auto' }));
  expect(await palette.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
});

test('playbook studio preserva a rolagem longa da rota sem mover o documento', async ({ page, request }) => {
  test.setTimeout(45_000);
  const sessionResponse = await request.post(`${API_URL}/api/v1/klm/local/session`);
  const session = await sessionResponse.json();
  const token = session.accessToken ?? session.access_token;
  const playbooksResponse = await request.get(`${API_URL}/api/v1/playbooks`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(playbooksResponse.status()).toBe(200);
  const playbooks = await playbooksResponse.json();
  const baseline = playbooks.find((item: { name: string }) => item.name === 'Baseline de negativas de plano de saúde');
  expect(baseline).toBeTruthy();

  await page.goto(`/app/playbooks/${baseline.id}`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: 'Baseline de negativas de plano de saúde', exact: true })).toBeVisible();

  const geometry = await page.evaluate(() => {
    const route = document.querySelector('[data-testid="klm-route-scroll"]')!;
    return {
      clientHeight: route.clientHeight,
      scrollHeight: route.scrollHeight,
      overflowY: getComputedStyle(route).overflowY,
      rootOverflow: getComputedStyle(document.documentElement).overflow,
      bodyOverflow: getComputedStyle(document.body).overflow,
    };
  });

  expect(geometry.overflowY).toBe('auto');
  expect(geometry.scrollHeight).toBeGreaterThan(geometry.clientHeight);
  expect(geometry.rootOverflow).toBe('hidden');
  expect(geometry.bodyOverflow).toBe('hidden');

  const route = page.getByTestId('klm-route-scroll');
  await route.evaluate((element) => element.scrollTo({ top: 500, behavior: 'instant' }));
  expect(await route.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
});

test('agenda cria, edita e exclui reunião local sem chamar o Google', async ({ page }) => {
  const googleRequests: string[] = [];
  page.on('request', (request) => {
    if (/googleapis\.com|accounts\.google\.com/.test(request.url())) googleRequests.push(request.url());
  });
  await page.goto('/app/agenda', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { level: 1, name: 'Agenda', exact: true }).first()).toBeVisible();

  await page.getByRole('button', { name: 'Conectar Google', exact: true }).click();
  const googleDialog = page.getByRole('dialog');
  await expect(googleDialog.getByRole('heading', { name: 'Google Agenda' })).toBeVisible();
  await expect(googleDialog.getByText('Conexão externa ainda bloqueada', { exact: true })).toBeVisible();
  await expect(googleDialog.getByRole('button', { name: 'Abrir configurações' })).toBeEnabled();
  await googleDialog.getByRole('button', { name: 'Fechar' }).click();

  const suffix = randomUUID().slice(0, 8);
  const title = `QA descartável reunião agenda ${suffix}`;
  const editedTitle = `${title} revisada`;
  await page.getByRole('button', { name: 'Nova reunião', exact: true }).click();
  const meetingDialog = page.getByRole('dialog');
  await meetingDialog.getByLabel('Título').fill(title);
  const createRequestPromise = page.waitForRequest((request) => (
    request.method() === 'POST' && request.url().endsWith('/api/v1/meetings')
  ));
  await meetingDialog.getByRole('button', { name: 'Criar reunião' }).click();
  const createRequest = await createRequestPromise;
  expect(createRequest.headers()['idempotency-key']).toMatch(/^[a-z0-9][a-z0-9:_-]{7,119}$/i);
  await expect(page.getByText('Reunião salva na agenda.', { exact: true })).toBeVisible();

  const search = page.getByPlaceholder('Buscar compromisso');
  await search.fill(title);
  await page.getByRole('button', { name: new RegExp(title) }).first().click();
  await page.getByRole('dialog').getByLabel('Título').fill(editedTitle);
  await page.getByRole('dialog').getByRole('button', { name: 'Salvar alterações' }).click();
  await search.fill(editedTitle);
  await page.getByRole('button', { name: new RegExp(editedTitle) }).first().click();
  await page.getByRole('dialog').getByRole('button', { name: 'Excluir' }).click();
  const confirmation = page.getByRole('alertdialog');
  await expect(confirmation.getByRole('heading', { name: 'Excluir esta reunião?' })).toBeVisible();
  await confirmation.getByRole('button', { name: 'Excluir' }).click();
  await expect(page.getByRole('button', { name: new RegExp(editedTitle) })).toHaveCount(0);
  expect(googleRequests).toEqual([]);
});

test('agenda vincula equipe e tarefa por rótulos e audita confirmação de conflito @desktop-only', async ({ page, request }) => {
  const sessionResponse = await request.post(`${API_URL}/api/v1/klm/local/session`);
  expect(sessionResponse.status(), await sessionResponse.text()).toBe(200);
  const session = await sessionResponse.json() as {
    accessToken?: string;
    access_token?: string;
    user: { id: string; name: string };
  };
  const token = session.accessToken ?? session.access_token;
  expect(token).toBeTruthy();
  const headers = { Authorization: `Bearer ${token}` };
  const runId = randomUUID().slice(0, 8);
  let conflictSource: { id: string; version: number } | null = null;
  let createdMeeting: {
    id: string;
    version: number;
    matterId: string | null;
    linkedTaskId: string | null;
    responsibleUserId: string | null;
    participants: Array<{
      userId: string;
      role: string;
      rsvp: string;
      reminderMinutesBefore: number | null;
    }>;
  } | null = null;

  try {
  const attendee = await inviteAndActivateAgendaMember(request, {
    headers,
    name: `Participante Agenda ${runId}`,
    email: `gestora-qa-e2e-agenda-${runId}-participante@example.com`,
    runId: `${runId}-participante`,
  });
  const organizer = await inviteAndActivateAgendaMember(request, {
    headers,
    name: `Organizador Agenda ${runId}`,
    email: `gestora-qa-e2e-agenda-${runId}-organizador@example.com`,
    runId: `${runId}-organizador`,
  });

  const ingestResponse = await request.post(`${API_URL}/api/v1/klm/omnichannel/email-ingest`, {
    headers,
    data: {
      messageId: `verify-email-agenda-${runId}`,
      senderEmail: `agenda-${runId}@local.invalid`,
      senderName: 'Lead de verificação',
      subject: `Caso agenda colaborativa ${runId}`,
      body: 'Caso descartável para comprovar o vínculo humano entre tarefa e compromisso.',
    },
  });
  expect(ingestResponse.status(), await ingestResponse.text()).toBe(201);
  const ingested = await ingestResponse.json() as { matterId: string };

  const funnelResponse = await request.get(`${API_URL}/api/v1/klm/funnel`, { headers });
  expect(funnelResponse.status(), await funnelResponse.text()).toBe(200);
  const funnel = await funnelResponse.json() as {
    cards: Array<{ id: string; name: string; subject: string }>;
  };
  const matter = funnel.cards.find((card) => card.id === ingested.matterId);
  expect(matter, 'O caso criado pela API precisa aparecer no seletor humano da agenda.').toBeTruthy();

  const taskTitle = `Tarefa vinculável agenda ${runId}`;
  const taskResponse = await request.post(
    `${API_URL}/api/v1/matters/${ingested.matterId}/tasks`,
    {
      headers,
      data: {
        title: taskTitle,
        description: 'Tarefa descartável vinculada ao compromisso colaborativo.',
        dueAt: new Date(Date.now() + 3 * 86_400_000).toISOString(),
        priority: 'alta',
        idempotencyKey: `e2e-agenda-task-${runId}`,
      },
    },
  );
  expect(taskResponse.status(), await taskResponse.text()).toBe(201);
  const task = await taskResponse.json() as { id: string; title: string };

  const startsAt = new Date(Date.now() + 36 * 60 * 60 * 1000);
  startsAt.setMinutes(0, 0, 0);
  const endsAt = new Date(startsAt.getTime() + 60 * 60 * 1000);
  const conflictingResponse = await request.post(`${API_URL}/api/v1/meetings`, {
    headers: {
      ...headers,
      'Idempotency-Key': `e2e-agenda-conflict-source:${runId}`,
    },
    data: {
      matterId: ingested.matterId,
      responsibleUserId: session.user.id,
      title: `QA descartável origem conflito agenda ${runId}`,
      startsAt: startsAt.toISOString(),
      endsAt: endsAt.toISOString(),
      location: 'Google Meet',
      meetingUrl: `https://meet.google.com/qa-${runId}`,
      participants: [
        {
          userId: session.user.id,
          role: 'organizer',
          rsvp: 'accepted',
          reminderMinutesBefore: 10,
        },
        {
          userId: attendee.id,
          role: 'attendee',
          rsvp: 'accepted',
          reminderMinutesBefore: 30,
        },
      ],
    },
  });
  expect(conflictingResponse.status(), await conflictingResponse.text()).toBe(201);
  conflictSource = await conflictingResponse.json() as { id: string; version: number };

  const targetTitle = `QA descartável agenda colaborativa ${runId}`;
    await page.goto('/app/agenda', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { level: 1, name: 'Agenda', exact: true }).first()).toBeVisible();
    await page.getByRole('button', { name: 'Nova reunião', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Título').fill(targetTitle);
    await dialog.getByLabel('Início').fill(browserDateTimeLocalValue(startsAt));
    await dialog.getByLabel('Término').fill(browserDateTimeLocalValue(endsAt));

    await dialog.getByLabel('Caso vinculado').click();
    await page.getByRole('option', {
      name: `${matter!.name} · ${matter!.subject}`,
      exact: true,
    }).click();

    await dialog.getByLabel('Organizador do compromisso').click();
    await page.getByRole('option', { name: organizer.name, exact: true }).click();
    await dialog.getByLabel('RSVP do organizador').click();
    await page.getByRole('option', { name: 'Confirmado', exact: true }).click();
    await dialog.getByLabel('Lembrete do organizador').click();
    await page.getByRole('option', { name: '10 minutos antes', exact: true }).click();

    await dialog.getByLabel('Pesquisar tarefa do caso').fill(taskTitle);
    await dialog.getByRole('combobox', { name: 'Tarefa vinculada', exact: true }).click();
    await page.getByRole('option', {
      name: `${taskTitle} · Em andamento`,
      exact: true,
    }).click();
    await expect(dialog.getByText(`Vínculo selecionado: ${taskTitle}.`, { exact: true })).toBeVisible();

    await dialog.getByLabel('Pesquisar participante interno').fill(attendee.name);
    await dialog.getByRole('button', {
      name: `Adicionar ${attendee.name} como participante`,
      exact: true,
    }).click();
    await dialog.getByLabel(`RSVP de ${attendee.name}`).click();
    await page.getByRole('option', { name: 'Confirmado', exact: true }).click();
    await dialog.getByLabel(`Lembrete de ${attendee.name}`).click();
    await page.getByRole('option', { name: '30 min antes', exact: true }).click();

    await dialog.getByLabel('Pesquisar participante interno').fill(session.user.name);
    await dialog.getByRole('button', {
      name: `Adicionar ${session.user.name} como observador`,
      exact: true,
    }).click();
    await dialog.getByLabel(`RSVP de ${session.user.name}`).click();
    await page.getByRole('option', { name: 'Talvez', exact: true }).click();
    await dialog.getByLabel(`Lembrete de ${session.user.name}`).click();
    await page.getByRole('option', { name: '1 hora antes', exact: true }).click();

    const conflictResponsePromise = page.waitForResponse((response) => (
      response.request().method() === 'POST'
      && response.url().endsWith('/api/v1/meetings')
      && response.status() === 409
    ));
    await dialog.getByRole('button', { name: 'Criar reunião', exact: true }).click();
    const conflictResponse = await conflictResponsePromise;
    expect((await conflictResponse.json()).detail).toMatchObject({
      code: 'MEE-008',
      participantUserIds: [attendee.id],
    });

    const conflictAlert = dialog.getByRole('alert', { name: 'Aviso de conflito' });
    await expect(conflictAlert).toBeVisible();
    await expect(conflictAlert.getByText(attendee.name, { exact: false })).toBeVisible();
    await conflictAlert.getByLabel('Confirmar sobreposição de participantes').click();
    const conflictReason = conflictAlert.getByLabel('Motivo da confirmação');
    const createButton = dialog.getByRole('button', { name: 'Criar reunião', exact: true });
    await expect(conflictReason).toHaveAttribute('maxlength', '500');
    await conflictReason.fill('1234567');
    await expect(createButton).toBeDisabled();

    const acceptedReason = `Conflito autorizado para o QA ${runId}.`;
    await conflictReason.fill(acceptedReason);
    await expect(createButton).toBeEnabled();
    const persistedResponsePromise = page.waitForResponse((response) => (
      response.request().method() === 'POST'
      && response.url().endsWith('/api/v1/meetings')
      && response.status() === 201
    ));
    await createButton.click();
    const persistedResponse = await persistedResponsePromise;
    const persistedRequest = persistedResponse.request().postDataJSON() as Record<string, unknown>;
    expect(persistedRequest).toMatchObject({
      matterId: ingested.matterId,
      responsibleUserId: organizer.id,
      linkedTaskId: task.id,
      confirmParticipantConflicts: true,
      conflictReason: acceptedReason,
      participants: expect.arrayContaining([
        expect.objectContaining({
          userId: organizer.id,
          role: 'organizer',
          rsvp: 'accepted',
          reminderMinutesBefore: 10,
        }),
        expect.objectContaining({
          userId: attendee.id,
          role: 'attendee',
          rsvp: 'accepted',
          reminderMinutesBefore: 30,
        }),
        expect.objectContaining({
          userId: session.user.id,
          role: 'observer',
          rsvp: 'tentative',
          reminderMinutesBefore: 60,
        }),
      ]),
    });
    createdMeeting = await persistedResponse.json();
    expect(createdMeeting).toMatchObject({
      matterId: ingested.matterId,
      responsibleUserId: organizer.id,
      linkedTaskId: task.id,
      participants: expect.arrayContaining([
        expect.objectContaining({ userId: organizer.id, role: 'organizer' }),
        expect.objectContaining({ userId: attendee.id, role: 'attendee' }),
        expect.objectContaining({ userId: session.user.id, role: 'observer' }),
      ]),
    });
    await expect(page.getByText('Reunião salva na agenda.', { exact: true })).toBeVisible();

    const meetingsResponse = await request.get(`${API_URL}/api/v1/meetings`, { headers });
    expect(meetingsResponse.status(), await meetingsResponse.text()).toBe(200);
    const meetings = await meetingsResponse.json() as Array<typeof createdMeeting>;
    expect(meetings.find((meeting) => meeting?.id === createdMeeting?.id)).toMatchObject({
      linkedTaskId: task.id,
      responsibleUserId: organizer.id,
    });

    const activityResponse = await request.get(`${API_URL}/api/v1/klm/activity?limit=1000`, { headers });
    expect(activityResponse.status(), await activityResponse.text()).toBe(200);
    const activity = await activityResponse.json() as Array<{
      eventType: string;
      entityId: string | null;
      afterData: Record<string, unknown> | null;
    }>;
    expect(activity.find((event) => (
      event.eventType === 'meeting_created'
      && event.entityId === createdMeeting?.id
    ))?.afterData).toMatchObject({
      participantConflictConfirmation: {
        confirmed: true,
        reason: acceptedReason,
      },
    });
  } finally {
    try {
      if (createdMeeting) {
        const deleted = await request.delete(meetingDeleteUrl(createdMeeting), { headers });
        expect(deleted.status(), await deleted.text()).toBe(204);
      }
      if (conflictSource) {
        const conflictDeleted = await request.delete(meetingDeleteUrl(conflictSource), { headers });
        expect(conflictDeleted.status(), await conflictDeleted.text()).toBe(204);
      }
    } finally {
      const cleanup = await request.delete(`${API_URL}/api/v1/klm/local/verification-artifacts`, { headers });
      expect(cleanup.status(), await cleanup.text()).toBe(200);
    }
  }
});

test('agenda recebe booking externo em tempo real sem recarregar', async ({ page, request }) => {
  const failures = captureRuntimeFailures(page);
  await page.goto('/app/agenda', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { level: 1, name: 'Agenda', exact: true }).first()).toBeVisible();
  await expect(page.getByText('Booking em tempo real', { exact: true })).toBeVisible();
  await expect.poll(
    () => page.evaluate(() => document.documentElement.dataset.klmRealtimeStatus),
    { timeout: 20_000 },
  ).toBe('connected');

  const sessionResponse = await request.post(`${API_URL}/api/v1/klm/local/session`);
  expect(sessionResponse.status()).toBe(200);
  const session = await sessionResponse.json();
  const token = session.accessToken ?? session.access_token;
  const headers = { Authorization: `Bearer ${token}` };
  const suffix = randomUUID().slice(0, 8);
  const title = `QA descartável booking realtime ${suffix}`;
  const startsAt = new Date(Date.now() + 2 * 60 * 60 * 1000);
  const created = await request.post(`${API_URL}/api/v1/meetings`, {
    headers: { ...headers, 'Idempotency-Key': `e2e-realtime:${randomUUID()}` },
    data: {
      title,
      startsAt: startsAt.toISOString(),
      endsAt: new Date(startsAt.getTime() + 60 * 60 * 1000).toISOString(),
      location: 'Google Meet',
    },
  });
  expect(created.status()).toBe(201);
  const meeting = await created.json();

  await expect(page.getByText('Novo agendamento', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('Ao vivo', { exact: true })).toBeVisible();
  await expect(page.getByText(title, { exact: true }).first()).toBeVisible();
  expect(failures).toEqual([]);

  const deleted = await request.delete(meetingDeleteUrl(meeting), { headers });
  expect(deleted.status()).toBe(204);
});

test('booking público cria reunião vertical com sala Teams e vínculo pendente', async ({ page, request }) => {
  const failures = captureRuntimeFailures(page);
  const sessionResponse = await request.post(`${API_URL}/api/v1/klm/local/session`);
  expect(sessionResponse.status()).toBe(200);
  const session = await sessionResponse.json();
  const token = session.accessToken ?? session.access_token;
  const headers = { Authorization: `Bearer ${token}` };
  const suffix = randomUUID().slice(0, 8);
  const meetingUrl = `https://teams.microsoft.com/l/meetup-join/${suffix}`;

  const linkResponse = await request.post(`${API_URL}/api/v1/agenda/booking-links`, {
    headers,
    data: {
      title: `QA descartável consulta pública ${suffix}`,
      description: 'Escolha um horário para a conversa inicial.',
      durationMinutes: 45,
      meetingProvider: 'microsoft_teams',
      meetingUrl,
      maxUses: 1,
    },
  });
  expect(linkResponse.status()).toBe(201);
  const link = await linkResponse.json();

  await page.goto(link.bookingPath, { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { level: 1, name: 'QA descartável consulta pública ' + suffix })).toBeVisible();
  await expect(page.getByText('Primeira reunião sempre on-line', { exact: true })).toBeVisible();

  const firstSlot = page.locator('button').filter({ hasText: /^\d{2}:\d{2}$/ }).first();
  await expect(firstSlot).toBeVisible();
  await firstSlot.click();
  await page.getByLabel('Nome completo').fill(`Lead E2E ${suffix}`);
  await page.getByLabel('WhatsApp').fill('(61) 99999-9999');
  await page.getByLabel('Algo importante antes da reunião?').fill('Negativa urgente de tratamento.');
  await page.getByRole('button', { name: 'Confirmar agendamento' }).click();

  await expect(page.getByRole('heading', { level: 1, name: 'Sua reunião está reservada.' })).toBeVisible();
  const roomLink = page.getByRole('link', { name: 'Abrir sala da reunião' });
  await expect(roomLink).toHaveAttribute('href', meetingUrl);
  await expect(page.getByText('Microsoft Teams', { exact: true })).toBeVisible();
  expect(failures).toEqual([]);

  const meetingsResponse = await request.get(`${API_URL}/api/v1/meetings`, { headers });
  expect(meetingsResponse.status()).toBe(200);
  const meetings = await meetingsResponse.json();
  const createdMeeting = meetings.find((meeting: { title: string }) => meeting.title === `Reunião inicial · Lead E2E ${suffix}`);
  expect(createdMeeting?.matterId).toBeNull();
  expect(createdMeeting?.meetingUrl).toBe(meetingUrl);
  if (createdMeeting?.id) {
    const deleted = await request.delete(meetingDeleteUrl(createdMeeting), { headers });
    expect(deleted.status()).toBe(204);
  }
});

test('agenda associa órfão ao card, registra no-show e mostra contexto no hover', async ({ page, request }) => {
  const failures = captureRuntimeFailures(page);
  const sessionResponse = await request.post(`${API_URL}/api/v1/klm/local/session`);
  expect(sessionResponse.status()).toBe(200);
  const session = await sessionResponse.json();
  const token = session.accessToken ?? session.access_token;
  const headers = { Authorization: `Bearer ${token}` };
  const funnelResponse = await request.get(`${API_URL}/api/v1/klm/funnel`, { headers });
  expect(funnelResponse.status()).toBe(200);
  const funnel = await funnelResponse.json();
  const card = funnel.cards[0];
  expect(card?.id).toBeTruthy();

  const suffix = randomUUID().slice(0, 8);
  const title = `QA descartável reunião contextual ${suffix}`;
  const meetingUrl = `https://meet.google.com/${suffix}`;
  const startsAt = new Date(Date.now() + 26 * 60 * 60 * 1000);
  const createResponse = await request.post(`${API_URL}/api/v1/meetings`, {
    headers: { ...headers, 'Idempotency-Key': `e2e-context:${randomUUID()}` },
    data: {
      title,
      startsAt: startsAt.toISOString(),
      endsAt: new Date(startsAt.getTime() + 45 * 60 * 1000).toISOString(),
      location: 'Google Meet',
      meetingUrl,
      notes: 'Revisar negativa e documentos antes da reunião.',
    },
  });
  expect(createResponse.status()).toBe(201);
  const meeting = await createResponse.json();

  await page.goto(`/app/agenda?meeting=${meeting.id}`, { waitUntil: 'domcontentloaded' });
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name: 'Detalhes da reunião' })).toBeVisible();
  await expect(dialog.getByText('Sala on-line pronta', { exact: true })).toBeVisible();
  await expect(dialog.getByRole('link', { name: 'Entrar' })).toHaveAttribute('href', meetingUrl);
  await expect(dialog.getByText('Agendamento órfão', { exact: true })).toBeVisible();
  await expect(dialog.getByText('Briefing para a reunião', { exact: true })).toBeVisible();

  await dialog.getByLabel('Caso vinculado').click();
  await page.getByRole('option', { name: `${card.name} · ${card.subject}` }).click();
  await dialog.getByLabel('Status da reunião').click();
  await page.getByRole('option', { name: 'Não compareceu' }).click();
  await dialog.getByRole('button', { name: 'Salvar alterações' }).click();
  await expect(page.getByText('Reunião atualizada.', { exact: true })).toBeVisible();

  const meetingsResponse = await request.get(`${API_URL}/api/v1/meetings`, { headers });
  const meetings = await meetingsResponse.json();
  const updated = meetings.find((item: { id: string }) => item.id === meeting.id);
  expect(updated?.matterId).toBe(card.id);
  expect(updated?.status).toBe('no_show');

  const search = page.getByPlaceholder('Buscar compromisso');
  await search.fill(title);
  const meetingButton = page.getByRole('button', { name: new RegExp(title) }).first();
  await expect(meetingButton).toBeVisible();
  await meetingButton.hover();
  await expect(page.getByText('Hermes Agenda · contexto local', { exact: true })).toBeVisible();
  expect(failures).toEqual([]);

  const deleted = await request.delete(meetingDeleteUrl(updated), { headers });
  expect(deleted.status()).toBe(204);
});

test('POST /meetings preserva uma criação idempotente e rejeita reuso divergente', async ({ request }) => {
  const sessionResponse = await request.post(`${API_URL}/api/v1/klm/local/session`);
  expect(sessionResponse.status()).toBe(200);
  const session = await sessionResponse.json();
  const token = session.accessToken ?? session.access_token;
  const idempotencyKey = `e2e-meeting-replay:${randomUUID()}`;
  const headers = {
    Authorization: `Bearer ${token}`,
    'Idempotency-Key': idempotencyKey,
  };
  const suffix = randomUUID().slice(0, 8);
  const startsAt = new Date(Date.now() + 30 * 60 * 60 * 1000);
  const payload = {
    title: `QA idempotência reunião ${suffix}`,
    startsAt: startsAt.toISOString(),
    endsAt: new Date(startsAt.getTime() + 30 * 60 * 1000).toISOString(),
    location: 'Online',
  };

  const first = await request.post(`${API_URL}/api/v1/meetings`, {
    headers,
    data: payload,
  });
  expect(first.status()).toBe(201);
  const firstMeeting = await first.json();

  const replay = await request.post(`${API_URL}/api/v1/meetings`, {
    headers,
    data: payload,
  });
  expect(replay.status()).toBe(201);
  const replayedMeeting = await replay.json();
  expect(replayedMeeting.id).toBe(firstMeeting.id);
  expect(replayedMeeting).toEqual(firstMeeting);

  const mismatched = await request.post(`${API_URL}/api/v1/meetings`, {
    headers,
    data: { ...payload, title: `${payload.title} divergente` },
  });
  expect(mismatched.status()).toBe(409);
  expect((await mismatched.json()).detail.code).toBe('MEETING_IDEMPOTENCY_PAYLOAD_MISMATCH');

  const meetingsResponse = await request.get(`${API_URL}/api/v1/meetings`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(meetingsResponse.status()).toBe(200);
  const meetings = await meetingsResponse.json();
  expect(meetings.filter((meeting: { id: string }) => meeting.id === firstMeeting.id)).toHaveLength(1);

  const deleted = await request.delete(meetingDeleteUrl(firstMeeting), {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(deleted.status()).toBe(204);
});

test('comentários sociais alternam para automação comercial completa', async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  await page.goto('/app/comentarios', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Automações', exact: true }).click();

  await expect(page.getByRole('heading', { name: 'Automação por comentário', exact: true })).toBeVisible();
  await expect(page.getByPlaceholder('medicamento, cirurgia, home care')).toBeVisible();
  await expect(page.getByText('Executar junto com a IA', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Criar e ativar', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Abrir editor visual em tempo real', exact: true })).toHaveAttribute('href', '/app/flows');

  await page.getByRole('button', { name: 'Fila social', exact: true }).click();
  await expect(page.getByText('Interações públicas', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Facebook', exact: true }).click();
  await expect(page.getByPlaceholder('Buscar pessoa, comentário ou palavra-chave')).toBeVisible();
  expect(failures).toEqual([]);
});

test('rastreamento exibe moeda completa com centavos e sem K ou M', async ({ page }) => {
  await page.goto('/app/rastreamento', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { level: 1, name: 'Rastreamento', exact: true })).toBeVisible();
  await expect(page.getByText(/R\$ [\d.]+,\d{2}/).first()).toBeVisible();

  await expect.poll(async () => {
    const text = await page.locator('main').innerText();
    return text.match(/R\$ [\d.]+,\d{2}/g)?.length ?? 0;
  }).toBeGreaterThanOrEqual(3);
  const content = await page.locator('main').innerText();
  expect(content.match(/R\$ [\d.]+,\d{2}/g)?.length ?? 0).toBeGreaterThanOrEqual(3);
  expect(content).not.toMatch(/R\$\s*[\d.,]+\s*[kKmM]\b/);
});

test('tema escuro persiste depois de recarregar', async ({ page, request }) => {
  const sessionResponse = await request.post(`${API_URL}/api/v1/klm/local/session`);
  expect(sessionResponse.status()).toBe(200);
  const session = await sessionResponse.json();
  const token = session.accessToken ?? session.access_token;
  const resetResponse = await request.put(`${API_URL}/api/v1/users/me/preferences`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { theme: 'light' },
  });
  expect(resetResponse.status()).toBe(200);

  await page.goto('/app');
  const html = page.locator('html');
  await expect(html).not.toHaveClass(/dark/);

  const openThemeMenu = async () => {
    const action = page.getByRole('button', { name: /Usar tema (claro|escuro)/ });
    if (!await action.isVisible()) {
      await page.getByRole('button', { name: /Abrir perfil de/ }).click();
    }
    await expect(action).toBeVisible();
    return action;
  };

  const darkResponse = page.waitForResponse((response) => (
    response.request().method() === 'PUT'
    && response.url().endsWith('/api/v1/users/me/preferences')
  ));
  let themeAction = await openThemeMenu();
  await expect(themeAction).toHaveText('Usar tema escuro');
  await themeAction.click();
  const savedDark = await darkResponse;
  expect(savedDark.status()).toBe(200);
  expect(savedDark.request().postDataJSON()).toMatchObject({ theme: 'dark' });
  await expect(html).toHaveClass(/dark/);

  await page.reload();
  await expect(html).toHaveClass(/dark/);
  const lightResponse = page.waitForResponse((response) => (
    response.request().method() === 'PUT'
    && response.url().endsWith('/api/v1/users/me/preferences')
  ));
  themeAction = await openThemeMenu();
  await expect(themeAction).toHaveText('Usar tema claro');
  await themeAction.click();
  expect((await lightResponse).status()).toBe(200);
  await expect(html).not.toHaveClass(/dark/);
});

test('configurações avançadas abrem a aba de integrações pelo endereço contextual', async ({ page }) => {
  await page.goto('/app/configuracoes/avancadas?tab=integracoes');

  const integrationsTab = page.getByRole('tab', { name: 'Integrações', exact: true });
  await expect(integrationsTab).toBeVisible();
  await expect(integrationsTab).toHaveAttribute('data-state', 'active');
  await expect(page.getByText('Registro de canais', { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/app\/configuracoes\/avancadas\?tab=integracoes/);
});

test('admin configura usuário e privacidade dos leads', async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  await page.goto('/app/configuracoes');
  await page.getByRole('button', { name: /Usuários e permissões/ }).click();
  await expect(page.getByRole('heading', { level: 2, name: 'Usuários e permissões' })).toBeVisible();
  await page.getByRole('button', { name: 'Convidar pessoa' }).click();

  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name: 'Convidar pessoa' })).toBeVisible();
  await expect(dialog.getByText('Aceite pendente', { exact: true })).toBeVisible();
  await expect(dialog.getByText(/A pessoa define a própria senha ao aceitar/)).toBeVisible();
  await expect(dialog.getByLabel(/senha/i)).toHaveCount(0);
  await expect(dialog.getByText('Dados sensíveis dos leads')).toBeVisible();
  await expect(dialog.getByText('Visíveis e editáveis', { exact: true })).toBeVisible();
  await dialog.getByRole('switch', { name: 'Permitir acesso aos dados sensíveis dos leads' }).click();
  await expect(dialog.getByText('Ocultos', { exact: true })).toBeVisible();

  await dialog.getByRole('tab', { name: 'Permissões' }).click();
  await expect(dialog.getByText('Permissões efetivas')).toBeVisible();
  await dialog.getByRole('button', { name: 'Cancelar' }).click();
  await expect(dialog).not.toBeVisible();
  expect(failures).toEqual([]);
});

test('revogação de dados sensíveis limpa a SPA sem recarregar', async ({ page, request }, testInfo) => {
  test.setTimeout(60_000);
  const ownerResponse = await request.post(`${API_URL}/api/v1/klm/local/session`);
  expect(ownerResponse.status()).toBe(200);
  const ownerSession = await ownerResponse.json();
  const ownerToken = ownerSession.accessToken ?? ownerSession.access_token;
  const ownerHeaders = { Authorization: `Bearer ${ownerToken}` };

  const funnelResponse = await request.get(`${API_URL}/api/v1/klm/funnel`, { headers: ownerHeaders });
  expect(funnelResponse.status()).toBe(200);
  const firstCard = (await funnelResponse.json()).cards[0];
  expect(firstCard).toBeTruthy();

  const suffix = `${testInfo.project.name}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`
    .replace(/[^a-z0-9-]/gi, '-')
    .toLowerCase();
  const password = 'SenhaRestrita#2026';
  const invitedEmail = `gestora-qa-e2e-${suffix}@example.com`;
  const createResponse = await request.post(`${API_URL}/api/v1/admin/team-members`, {
    headers: {
      ...ownerHeaders,
      'Idempotency-Key': `qa-invite-${suffix}`,
    },
    data: {
      name: 'Gestora sob política',
      email: invitedEmail,
      workspaceRole: 'legal_manager',
      permissionGrants: [],
      permissionDenials: [],
    },
  });
  expect(createResponse.status()).toBe(202);
  const member = await createResponse.json();

  const deliveryResponse = await request.get(
    `${API_URL}/api/v1/klm/local/invitations/${member.invitationId}/delivery`,
    { headers: ownerHeaders },
  );
  expect(deliveryResponse.status()).toBe(200);
  const delivery = await deliveryResponse.json();
  const invitationToken = new URL(delivery.link, 'http://localhost').searchParams.get('token');
  expect(invitationToken).toBeTruthy();

  const acceptResponse = await request.post(
    `${API_URL}/api/v1/auth/invitations/${member.invitationId}/accept`,
    {
      headers: { 'Idempotency-Key': `qa-accept-${suffix}` },
      data: { token: invitationToken, password },
    },
  );
  expect(acceptResponse.status()).toBe(200);

  const loginResponse = await request.post(`${API_URL}/api/v1/auth/login`, {
    data: { email: invitedEmail, password },
  });
  expect(loginResponse.status()).toBe(200);
  const restrictedSession = await loginResponse.json();
  if (process.env.KLM_E2E_PRODUCTION === '1') {
    await page.goto('/status', { waitUntil: 'domcontentloaded' });
    await page.evaluate((session) => {
      localStorage.setItem('klm_e2e_auth_override', '1');
      sessionStorage.setItem('klm_token', session.accessToken ?? session.access_token);
      sessionStorage.setItem('klm_user', JSON.stringify(session.user));
      sessionStorage.setItem('klm_session', JSON.stringify(session.session));
      localStorage.removeItem('klm_user');
      localStorage.removeItem('klm_session');
    }, restrictedSession);
  } else {
    await page.route('**/api/v1/klm/local/session', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(restrictedSession) });
    });
  }

  await page.goto(`/app/leads?matter=${firstCard.id}`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByText(firstCard.name, { exact: true }).first()).toBeVisible();

  const revokeResponse = await request.patch(
    `${API_URL}/api/v1/admin/team-members/${member.membershipId}`,
    {
      headers: ownerHeaders,
      data: {
        permissionDenials: [
          { module: 'lead_data', action: 'view', scope: 'workspace' },
          { module: 'lead_data', action: 'edit', scope: 'workspace' },
        ],
      },
    },
  );
  expect(revokeResponse.status()).toBe(200);

  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.getByText('Dados ocultos', { exact: true })).toBeVisible();
  await expect(page.getByText(firstCard.name, { exact: true })).toHaveCount(0);
  await expect.poll(async () => page.evaluate(() => {
    const raw = sessionStorage.getItem('klm_session');
    if (!raw) return false;
    const session = JSON.parse(raw);
    return session.permissions.some((permission: { module: string; action: string }) =>
      permission.module === 'lead_data' && permission.action === 'view');
  })).toBe(false);
});

test('runtime mantém todos os gates externos fechados', async ({ request }) => {
  const response = await request.get(`${API_URL}/api/v1/klm/runtime`);
  expect(response.status()).toBe(200);
  const runtime = await response.json();
  expect(runtime).toMatchObject({
    mode: 'local_simulation',
    localOnly: true,
    externalActionsEnabled: false,
    awsEnabled: false,
    onlineApproved: false,
  });
  expect(runtime.providers.models).toBe('local_fake');
  expect(new Set(Object.entries(runtime.providers)
    .filter(([provider]) => provider !== 'models')
    .map(([, mode]) => mode))).toEqual(new Set(['offline']));
});

test('simulação visual respeita o congelamento da área sem ação externa', async ({ page, request }) => {
  const failures = captureRuntimeFailures(page);
  const sessionResponse = await request.post(`${API_URL}/api/v1/klm/local/session`);
  const session = await sessionResponse.json();
  const token = session.accessToken ?? session.access_token;
  const headers = { Authorization: `Bearer ${token}` };
  await ensurePublishedAgentDeployment(request, token, 'orquestrador');
  const suffix = randomUUID().replaceAll('-', '');
  const qaLead = await request.post(`${API_URL}/api/v1/klm/omnichannel/email-ingest`, {
    headers,
    data: {
      messageId: `verify-email-agent-ui-${suffix}`,
      senderEmail: `${suffix}@local.invalid`,
      senderName: 'Lead de verificação',
      subject: 'Negativa de exame',
      body: 'Caso descartável para validar o botão da equipe de agentes.',
    },
  });
  expect(qaLead.status()).toBe(201);
  const qaContext = await qaLead.json();

  try {
    await page.goto(`/app/agentes?matter=${qaContext.matterId}`);
    const button = page.getByRole('button', { name: 'Simular time' });
    await expect(button).toBeEnabled();
    const simulationResponse = page.waitForResponse((response) => (
      response.request().method() === 'POST'
      && response.url().endsWith('/api/v1/klm/agents/simulations/intake')
    ));
    await button.click();
    const simulation = await simulationResponse;
    expect(simulation.status()).toBe(409);
    expect(simulation.request().postDataJSON()).toMatchObject({ matterId: qaContext.matterId });
    const simulationResult = await simulation.json();
    expect(simulationResult).toMatchObject({ detail: { code: 'AREA_FROZEN' } });
    await expect(page.getByText('A equipe não pôde ser simulada. Confirme se a área do caso foi liberada para esta conta.')).toBeVisible();
    expect(failures).toEqual([expect.stringContaining('http 409:')]);
  } finally {
    try {
      const cleanup = await request.delete(`${API_URL}/api/v1/klm/local/verification-artifacts`, { headers });
      expect(cleanup.status()).toBe(200);
    } catch {
      // A timed-out page may close the fixture context; cleanup must not hide
      // the assertion that actually failed.
    }
  }
});

test('conversa abre proposta e reunião no contexto correto', async ({ page, request }, testInfo) => {
  test.setTimeout(120_000);
  const failures = captureRuntimeFailures(page);
  await page.goto('/app/conversas', { waitUntil: 'domcontentloaded' });
  await expect(page.getByTestId('conversation-data-status')).toHaveCount(0);

  const mobileBackButton = page.getByTitle('Voltar às conversas');
  if (await mobileBackButton.isVisible()) await mobileBackButton.click();
  const contactIndex = testInfo.project.name === 'mobile-393' ? 1 : 0;
  await page.getByTestId('conversation-contact').nth(contactIndex).click();

  if (testInfo.project.name === 'mobile-393') {
    const identityBox = await page.getByTestId('conversation-active-identity').boundingBox();
    const actionsBox = await page.getByTestId('conversation-active-mobile-actions').boundingBox();
    expect(identityBox).toBeTruthy();
    expect(actionsBox).toBeTruthy();
    if (identityBox && actionsBox) {
      expect(identityBox.y + identityBox.height).toBeLessThanOrEqual(actionsBox.y);
      expect(actionsBox.x + actionsBox.width).toBeLessThanOrEqual(393);
    }
  }

  const conversationUrl = page.url();
  await page.getByRole('button', { name: 'Proposta', exact: true }).click();
  const proposalPreparation = page.getByRole('dialog', { name: 'Preparar proposta', exact: true });
  await expect(proposalPreparation).toBeVisible();
  const generateProposal = proposalPreparation.getByRole('button', { name: 'Gerar proposta e abrir prévia', exact: true });
  await expect(generateProposal).toBeEnabled({ timeout: 30_000 });
  await generateProposal.click();
  await expect(page.getByText(/Caso movido para Proposta e versão \d+ registrada\. Nenhum link foi enviado ao cliente\./)).toBeVisible();
  const proposalPreview = page.getByRole('dialog', { name: 'Prévia da proposta', exact: true });
  await expect(proposalPreview).toBeVisible();
  await expect(proposalPreview.getByTestId('proposal-preview')).toBeVisible();
  await expect(proposalPreview.getByRole('heading', { name: 'Seu tratamento não pode esperar.' })).toBeVisible();
  expect(page.url()).toBe(conversationUrl);
  await page.keyboard.press('Escape');
  await expect(proposalPreview).not.toBeVisible();

  await page.getByRole('button', { name: 'Agendar', exact: true }).click();
  const schedulingDialog = page.getByRole('dialog', { name: /Agendar reunião com/ });
  await expect(schedulingDialog).toBeVisible();
  const availableSlot = schedulingDialog.locator('[aria-label="Horários disponíveis"] button').first();
  await expect(availableSlot).toBeVisible();
  await availableSlot.click();
  const bookingResponse = page.waitForResponse((response) => (
    response.request().method() === 'POST'
    && response.url().endsWith('/api/v1/agenda/internal-bookings')
  ));
  await schedulingDialog.getByRole('button', { name: 'Confirmar horário', exact: true }).click();
  const bookingHttpResponse = await bookingResponse;
  expect(bookingHttpResponse.ok()).toBe(true);
  const createdMeeting = await bookingHttpResponse.json();
  const createdMeetingId = createdMeeting.id as string | undefined;
  const createdMeetingVersion = createdMeeting.version as number | undefined;
  await expect(page.getByRole('button', { name: 'Agendar outro horário', exact: true })).toBeVisible();
  await expect(schedulingDialog).not.toBeVisible();
  expect(page.url()).toBe(conversationUrl);
  expect(failures).toEqual([]);

  expect(createdMeetingId).toBeTruthy();
  expect(createdMeetingVersion).toBeTruthy();
  const cleanupSessionResponse = await request.post(`${API_URL}/api/v1/klm/local/session`);
  expect(cleanupSessionResponse.status()).toBe(200);
  const cleanupSession = await cleanupSessionResponse.json();
  const cleanupToken = cleanupSession.accessToken ?? cleanupSession.access_token;
  const cleanupResponse = await request.delete(
    meetingDeleteUrl({
      id: createdMeetingId as string,
      version: createdMeetingVersion as number,
    }),
    { headers: { Authorization: `Bearer ${cleanupToken}` } },
  );
  expect(cleanupResponse.status()).toBe(204);
});

test('as cinco ferramentas contextuais permanecem na conversa em todos os viewports', async ({ page }, testInfo) => {
  const failures = captureRuntimeFailures(page);
  await page.goto('/app/conversas', { waitUntil: 'domcontentloaded' });

  const mobileBackButton = page.getByTitle('Voltar às conversas');
  if (await mobileBackButton.isVisible()) await mobileBackButton.click();
  const contactIndex = testInfo.project.name === 'mobile-393' ? 1 : 0;
  await page.getByTestId('conversation-contact').nth(contactIndex).click();

  const conversationUrl = page.url();
  const contextActions = page.getByTestId('conversation-context-actions');
  const tools: Array<{ action: string; heading: string | RegExp }> = [
    { action: 'CRM', heading: 'Caso 360 e CRM' },
    { action: 'Pedir docs', heading: 'Solicitar documentos' },
    { action: 'Agendar', heading: /Agendar reunião com/ },
    { action: 'Proposta', heading: 'Preparar proposta' },
    { action: 'Áudio', heading: 'Áudio assistido' },
  ];

  for (const tool of tools) {
    const button = contextActions.getByRole('button', { name: tool.action, exact: true });
    await expect(button).toBeVisible();
    await expect(button).toBeEnabled();
    await button.click();
    const heading = typeof tool.heading === 'string'
      ? page.getByRole('heading', { name: tool.heading, exact: true })
      : page.getByRole('heading', { name: tool.heading });
    await expect(heading).toBeVisible();
    const dialog = page.getByRole('dialog').filter({ has: heading });
    await expect(dialog).toBeVisible();
    await expect(dialog.locator('iframe')).toHaveCount(0);
    expect(page.url()).toBe(conversationUrl);
    await page.keyboard.press('Escape');
    await expect(dialog).not.toBeVisible();
    await expect(page.locator('[role="dialog"]:visible')).toHaveCount(0);
    expect(page.url()).toBe(conversationUrl);
  }

  expect(failures).toEqual([]);
});

test('conversa ativa follow-up ou recuperação pelo catálogo de flows', async ({ page }, testInfo) => {
  const failures = captureRuntimeFailures(page);
  await page.goto('/app/conversas', { waitUntil: 'domcontentloaded' });

  const mobileBackButton = page.getByTitle('Voltar às conversas');
  if (await mobileBackButton.isVisible()) await mobileBackButton.click();
  const contactIndex = testInfo.project.name === 'mobile-393' ? 1 : 0;
  await page.getByTestId('conversation-contact').nth(contactIndex).click();

  const flowControl = page.getByRole('button', { name: 'Fluxo', exact: true });
  await flowControl.click();
  const flowDialog = page.getByRole('dialog');
  await expect(flowDialog.getByRole('heading', { name: 'Ativar fluxo no atendimento' })).toBeVisible();
  await expect(flowDialog.getByRole('option', { name: /Follow-up: documentos pendentes/ })).toBeVisible();
  await flowDialog.getByRole('option', { name: /Recuperação: oportunidade sem resposta/ }).click();
  const cancelExistingRun = flowDialog.getByRole('button', { name: 'Encerrar execução' });
  if (await cancelExistingRun.isVisible()) {
    await cancelExistingRun.click();
    await expect(cancelExistingRun).not.toBeVisible();
    await expect(flowDialog.getByRole('button', { name: /Ativar neste caso|Executar novamente/ })).toBeEnabled();
  }
  await flowDialog.getByRole('button', { name: /Ativar neste caso|Executar novamente/ }).click();

  await expect(flowDialog).not.toBeVisible();
  await expect(page.getByText(/0 envios externos/).first()).toBeVisible();
  await flowControl.click();
  const activeFlowDialog = page.getByRole('dialog');
  await expect(activeFlowDialog.getByText('Última execução', { exact: true })).toBeVisible();
  await expect(activeFlowDialog.getByText('Recuperação: oportunidade sem resposta', { exact: true }).first()).toBeVisible();
  const closeActiveRun = activeFlowDialog.getByRole('button', { name: 'Encerrar execução' });
  await closeActiveRun.click();
  await expect(closeActiveRun).not.toBeVisible();
  await expect(activeFlowDialog.getByRole('button', { name: /Ativar neste caso|Executar novamente/ })).toBeEnabled();
  await page.keyboard.press('Escape');
  await expect(activeFlowDialog).not.toBeVisible();
  expect(failures).toEqual([]);
});

test('página de flows abre o registro escolhido e oferece novo fluxo', async ({ page, request }) => {
  const failures = captureRuntimeFailures(page);
  const sessionResponse = await request.post(`${API_URL}/api/v1/klm/local/session`);
  expect(sessionResponse.status()).toBe(200);
  const session = await sessionResponse.json();
  const token = session.accessToken ?? session.access_token;
  const flowsResponse = await request.get(`${API_URL}/api/v1/flows`, { headers: { Authorization: `Bearer ${token}` } });
  expect(flowsResponse.status()).toBe(200);
  const flows = await flowsResponse.json();
  const recovery = flows.find((flow: { name: string }) => flow.name.startsWith('Recuperação:'));
  const followUp = flows.find((flow: { name: string }) => flow.name.startsWith('Follow-up:'));
  expect(recovery).toBeTruthy();
  expect(followUp).toBeTruthy();

  await page.goto(`/app/flows?flow=${recovery.id}`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByText(`Fluxo: ${recovery.name}`, { exact: true })).toBeVisible();
  await page.getByRole('combobox', { name: 'Fluxo em edição' }).click();
  await page.locator(`[role="option"][data-flow-id="${followUp.id}"]`).click();
  await expect(page).toHaveURL(new RegExp(`/app/flows\\?flow=${followUp.id}$`));
  await expect(page.getByText(`Fluxo: ${followUp.name}`, { exact: true })).toBeVisible();

  await page.getByRole('button', { name: /Triagem inicial.*5 etapas.*modelo local/ }).click();
  await expect(page.getByText('Não salvo', { exact: true })).toBeVisible();
  await expect(page.locator('.react-flow__node').filter({ hasText: 'SDR' })).toBeVisible();
  await page.getByRole('button', { name: 'Restaurar' }).click();
  await expect(page.getByText(new RegExp(`Revisão ${followUp.current_revision}`))).toBeVisible();

  await page.getByRole('button', { name: 'Novo fluxo' }).click();
  await expect(page).toHaveURL(/\/automacoes\/editor\?return=/);
  expect(new URL(page.url()).searchParams.get('return')).toBe(`/app/flows?flow=${followUp.id}`);
  await expect(page.getByRole('button', { name: 'Meu Fluxo', exact: true })).toBeVisible();
  expect(failures).toEqual([]);
});

test('funil arrasta, persiste e recarrega a nova etapa', async ({ page, request }, testInfo) => {
  test.setTimeout(60_000);
  const failures = captureRuntimeFailures(page);
  let realtimeSocketCount = 0;
  page.on('websocket', (socket) => {
    if (new URL(socket.url()).pathname === '/api/v1/ws') realtimeSocketCount += 1;
  });
  await page.goto('/app/funil', { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => document.documentElement.dataset.klmRealtimeStatus)).toBe('connected');
  const token = await page.evaluate(() => sessionStorage.getItem('klm_token'));
  expect(token).toBeTruthy();
  const headers = { Authorization: `Bearer ${token}` };
  const funnelResponse = await request.get(`${API_URL}/api/v1/klm/funnel`, { headers });
  expect(funnelResponse.status()).toBe(200);
  const funnel = await funnelResponse.json();
  const visibleCards = page.locator('[data-card-id]:visible');
  await expect(visibleCards.first()).toBeVisible();
  const visibleCardId = await visibleCards.first().getAttribute('data-card-id');
  expect(visibleCardId).toBeTruthy();
  const card = funnel.cards.find((item: { id: string }) => item.id === visibleCardId);
  expect(card, 'o card visível deve pertencer à sessão autenticada consultada pela API').toBeTruthy();

  const source = page.locator(`[data-testid="funnel-card-${card.id}"]:visible`).first();
  await expect(source).toBeVisible();
  const targetStage = card.stage === 'novo_lead' ? 'triagem_sdr' : 'novo_lead';

  if (testInfo.project.name !== 'desktop-1440') {
    await source.click();
    const cardWorkspace = page.getByRole('dialog').filter({
      has: page.getByRole('heading', { name: card.name, exact: true }),
    });
    await expect(cardWorkspace).toBeVisible();
    const moveResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'PATCH'
        && response.url().endsWith(`/api/v1/klm/funnel/${card.id}/stage`));
    try {
      await cardWorkspace.getByRole('combobox', { name: 'Mover lead para etapa' }).click();
      const targetLabel = funnel.stages.find((stage: { id: string }) => stage.id === targetStage)?.label;
      await page.getByRole('option', { name: targetLabel, exact: true }).click();
      expect((await moveResponsePromise).status()).toBe(200);
      await page.waitForTimeout(1_200);
      expect(realtimeSocketCount).toBe(1);
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(page.getByTestId(`funnel-mobile-stage-${targetStage}`).getByTestId(`funnel-card-${card.id}`)).toBeVisible();
      expect(failures).toEqual([]);
    } finally {
      const restore = await restoreFunnelCard(request, headers, card);
      expect(restore.status()).toBe(200);
    }
    return;
  }

  const dragHandle = page.getByTestId(`funnel-drag-handle-${card.id}`);
  await expect(dragHandle).toBeVisible();
  const gripIcon = page.getByTestId(`funnel-grip-icon-${card.id}`);
  await expect(gripIcon).toBeVisible();
  const target = page.getByTestId(`funnel-column-${targetStage}`);
  const moveResponsePromise = page.waitForResponse((response) =>
    response.request().method() === 'PATCH'
      && response.url().endsWith(`/api/v1/klm/funnel/${card.id}/stage`), { timeout: 15_000 });

  try {
    await gripIcon.scrollIntoViewIfNeeded();
    await expect(gripIcon).toBeInViewport();
    const sourceBox = await gripIcon.boundingBox();
    const targetBox = await target.boundingBox();
    expect(sourceBox).toBeTruthy();
    expect(targetBox).toBeTruthy();
    if (sourceBox && targetBox) {
      await page.mouse.move(sourceBox.x + sourceBox.width / 2, sourceBox.y + sourceBox.height / 2);
      await page.mouse.down();
      await page.mouse.move(sourceBox.x + sourceBox.width / 2 + 12, sourceBox.y + sourceBox.height / 2 + 6, { steps: 4 });
      await page.waitForTimeout(250);
      await page.mouse.move(targetBox.x + targetBox.width / 2, targetBox.y + Math.min(230, targetBox.height / 2), { steps: 24 });
      await page.waitForTimeout(350);
      await page.mouse.up();
    }
    const moveResponse = await moveResponsePromise;
    expect(moveResponse.status()).toBe(200);
    await expect(target.getByTestId(`funnel-card-${card.id}`)).toBeVisible();
    await expect(page.getByText(/Movimento salvo:|O card foi movido|A orquestração foi enfileirada/)).toBeVisible();
    await page.waitForTimeout(1_200);
    expect(realtimeSocketCount).toBe(1);

    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId(`funnel-column-${targetStage}`).getByTestId(`funnel-card-${card.id}`)).toBeVisible();
    expect(failures).toEqual([]);
  } finally {
    const restore = await restoreFunnelCard(request, headers, card);
    expect(restore.status()).toBe(200);
  }
});

test('flow adiciona, move e persiste bloco tipado', async ({ page, request }, testInfo) => {
  test.setTimeout(60_000);
  const failures = captureRuntimeFailures(page);
  const sessionResponse = await request.post(`${API_URL}/api/v1/klm/local/session`);
  expect(sessionResponse.status()).toBe(200);
  const session = await sessionResponse.json();
  const token = session.accessToken ?? session.access_token;
  const headers = { Authorization: `Bearer ${token}` };
  const flowsResponse = await request.get(`${API_URL}/api/v1/flows`, { headers });
  expect(flowsResponse.status()).toBe(200);
  const flows = await flowsResponse.json();
  const sourceFlow = flows.find((item: { name: string }) => item.name.startsWith(
    testInfo.project.name === 'mobile-393' ? 'Recuperação:' : 'Follow-up:',
  )) ?? flows[0];
  expect(sourceFlow).toBeTruthy();
  const createFlow = await request.post(`${API_URL}/api/v1/flows`, {
    headers,
    data: {
      name: `QA descartável ${testInfo.project.name} ${randomUUID().slice(0, 8)}`,
      description: 'Flow transitório do teste drag and drop.',
      practice_area: sourceFlow.practice_area,
      trigger: sourceFlow.trigger,
      trigger_config: sourceFlow.trigger_config,
      nodes: sourceFlow.nodes,
      edges: sourceFlow.edges,
      is_active: false,
    },
  });
  expect(createFlow.status()).toBe(201);
  const flow = await createFlow.json();
  const originalNodeIds = new Set(flow.nodes.map((node: { id: string }) => node.id));

  try {
    await page.goto(`/app/flows?flow=${flow.id}`, { waitUntil: 'domcontentloaded' });
    let runRequests = 0;
    page.on('request', (outgoing) => {
      if (outgoing.method() === 'POST' && outgoing.url().endsWith(`/api/v1/flows/${flow.id}/run`)) runRequests += 1;
    });
    const paletteBlock = page.getByRole('button', { name: 'Adicionar bloco Aprovação humana' });
    const canvas = page.locator('.react-flow').first();
    await expect(canvas).toBeVisible();
    await expect(paletteBlock).toBeEnabled();
    const graphNodes = page.locator('.react-flow__node');
    const graphNodeCountBefore = await graphNodes.count();
    if (testInfo.project.name === 'desktop-1440') {
      const dropZone = page.locator('.react-flow__pane').first();
      const dropZoneBox = await dropZone.boundingBox();
      expect(dropZoneBox).toBeTruthy();
      await paletteBlock.dragTo(dropZone, {
        targetPosition: {
          x: (dropZoneBox?.width ?? 600) * 0.68,
          y: (dropZoneBox?.height ?? 500) * 0.62,
        },
      });
    } else {
      await paletteBlock.tap();
    }
    await expect(page.getByText('Não salvo', { exact: true })).toBeVisible();
    await expect(graphNodes).toHaveCount(graphNodeCountBefore + 1);
    const dryRun = page.getByRole('button', { name: 'Dry-run' });
    await expect(dryRun).toBeDisabled();
    await expect(dryRun).toHaveAttribute('title', 'Abra Flows a partir de um caso para executar o dry-run');
    expect(runRequests).toBe(0);
    const addedNode = page.locator('.react-flow__node[data-id^="local-"]').filter({ hasText: 'Aprovação humana' }).last();
    await expect(addedNode).toBeVisible();
    const addedNodeId = await addedNode.getAttribute('data-id');
    expect(addedNodeId).toBeTruthy();

    const saveResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'PUT' && response.url().endsWith(`/api/v1/flows/${flow.id}`));
    await page.getByRole('button', { name: 'Salvar local' }).click();
    const saveResponse = await saveResponsePromise;
    expect(saveResponse.status()).toBe(200);
    await expect(page.getByText(new RegExp(`Revisão ${flow.current_revision + 1}`))).toBeVisible();

    const persistedResponse = await request.get(`${API_URL}/api/v1/flows/${flow.id}`, { headers });
    expect(persistedResponse.status()).toBe(200);
    const persisted = await persistedResponse.json();
    const added = persisted.nodes.find((node: { id: string }) => node.id === addedNodeId);
    expect(originalNodeIds.has(addedNodeId as string)).toBe(false);
    expect(added).toMatchObject({
      id: addedNodeId,
      type: 'human_approval',
      data: { label: 'Aprovação humana' },
      config: { reason: 'Revisar o próximo passo antes de prosseguir.' },
    });
    expect(Number.isFinite(added.position?.x)).toBe(true);
    expect(Number.isFinite(added.position?.y)).toBe(true);

    let persistedPosition = { ...added.position };
    if (testInfo.project.name === 'desktop-1440') {
      await addedNode.scrollIntoViewIfNeeded();
      const box = await addedNode.boundingBox();
      expect(box).toBeTruthy();
      if (box) {
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
        await page.mouse.down();
        await page.mouse.move(box.x + box.width / 2 + 90, box.y + box.height / 2 + 55, { steps: 12 });
        await page.mouse.up();
      }
      await expect(page.getByText('Não salvo', { exact: true })).toBeVisible();
      const moveSavePromise = page.waitForResponse((response) =>
        response.request().method() === 'PUT' && response.url().endsWith(`/api/v1/flows/${flow.id}`));
      await page.getByRole('button', { name: 'Salvar local' }).click();
      expect((await moveSavePromise).status()).toBe(200);

      const moved = await (await request.get(`${API_URL}/api/v1/flows/${flow.id}`, { headers })).json();
      const movedAdded = moved.nodes.find((node: { id: string }) => node.id === added.id);
      expect(movedAdded.position).not.toEqual(added.position);
      persistedPosition = { ...movedAdded.position };
    }

    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.locator('.react-flow__node').filter({ hasText: 'Aprovação humana' }).last()).toBeVisible();
    const afterReload = await (await request.get(`${API_URL}/api/v1/flows/${flow.id}`, { headers })).json();
    expect(afterReload.nodes.find((node: { id: string }) => node.id === added.id)?.position).toEqual(persistedPosition);
    expect(failures).toEqual([]);
  } finally {
    const cleanup = await request.delete(`${API_URL}/api/v1/flows/${flow.id}`, { headers });
    expect(cleanup.status()).toBe(204);
  }
});

test('funil move card por teclado e anuncia em português', { tag: '@desktop-only' }, async ({ page, request }) => {
  const sessionResponse = await request.post(`${API_URL}/api/v1/klm/local/session`);
  const session = await sessionResponse.json();
  const token = session.accessToken ?? session.access_token;
  const headers = { Authorization: `Bearer ${token}` };
  const funnel = await (await request.get(`${API_URL}/api/v1/klm/funnel`, { headers })).json();
  const card = funnel.cards.find((item: { stage: string }) => item.stage === 'novo_lead');
  expect(card).toBeTruthy();
  const sourceStageIndex = funnel.stages.findIndex((stage: { id: string }) => stage.id === card.stage);
  const targetStage = funnel.stages[sourceStageIndex + 1].id;
  const targetStageLabel = funnel.stages[sourceStageIndex + 1].label;

  await page.goto('/app/funil', { waitUntil: 'domcontentloaded' });
  const dragHandle = page.getByTestId(`funnel-card-${card.id}`);
  await dragHandle.scrollIntoViewIfNeeded();
  await expect(dragHandle).toBeInViewport();
  // The route can render the card before DragDropContext has attached its
  // keyboard sensors, especially after a long serial browser run. Wait for
  // the real funnel query to report the connected state before starting the
  // gesture; this keeps the assertion strict without adding a blind sleep.
  await expect(page.getByText('CRM: conectado', { exact: true })).toBeVisible();
  await dragHandle.focus();
  await expect(dragHandle).toHaveAttribute('aria-describedby');
  const instructionsId = await dragHandle.getAttribute('aria-describedby');
  await expect(page.locator(`[id="${instructionsId}"]`)).toContainText('Pressione espaço para levantar o card');

  const moveResponsePromise = page.waitForResponse((response) =>
    response.request().method() === 'PATCH'
      && response.url().endsWith(`/api/v1/klm/funnel/${card.id}/stage`));
  const announcement = page.locator('[id^="rfd-announcement-"]');
  try {
    await dragHandle.press('Space');
    await expect(announcement).toContainText(`Card ${card.name} selecionado`);
    await dragHandle.press('ArrowRight');
    await expect(announcement).toContainText(`na etapa ${targetStageLabel}`);
    await dragHandle.press('Space');
    expect((await moveResponsePromise).status()).toBe(200);
    await expect(announcement).toContainText(
      `Card movido para ${targetStageLabel}`,
    );
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId(`funnel-column-${targetStage}`).getByTestId(`funnel-card-${card.id}`)).toBeVisible();
  } finally {
    const restore = await restoreFunnelCard(request, headers, card);
    expect(restore.status()).toBe(200);
  }
});
