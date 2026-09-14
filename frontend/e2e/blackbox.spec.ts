import {
  authenticatePageForRole,
  expect,
  sessionForRole,
  test,
  type APIRequestContext,
  type Page,
} from './fixtures';

const API_URL = process.env.KLM_API_URL ?? 'http://127.0.0.1:8010';
const BLACKBOX_URL = process.env.KLM_BLACKBOX_URL ?? 'http://127.0.0.1:4174';

type SessionIdentity = {
  id: string;
  name: string;
};

type WorkspaceSession = {
  token: string;
  user: SessionIdentity;
  session: {
    activeWorkspace: {
      id: string;
      name: string;
      slug: string;
    };
  };
};

type SeededCollaborativeTask = {
  tenantToken: string;
  tenantUser: SessionIdentity;
  workspace: WorkspaceSession['session']['activeWorkspace'];
  matterId: string;
  task: {
    id: string;
    title: string;
    version: number;
    dueAt: string;
    reviewDueAt: string;
    participants: Array<{
      userId: string;
      role: string;
      dueAt: string | null;
      requiredForCompletion: boolean;
    }>;
  };
  ownerName: string;
  assigneeName: string;
  reviewerName: string;
  ownerDueAt: string;
  assigneeDueAt: string;
  reviewDueAt: string;
  generalDueAt: string;
  comment: string;
};

function bearer(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

function isoAfter(days: number): string {
  return new Date(Date.now() + days * 86_400_000).toISOString();
}

function persistedWallClock(value: string): string {
  return value.replace(/\.\d+(?=Z|[+-]\d{2}:?\d{2}$|$)/, '').replace(/Z|[+-]\d{2}:?\d{2}$/, '');
}

async function localWorkspaceSession(request: APIRequestContext): Promise<WorkspaceSession> {
  const response = await request.post(`${API_URL}/api/v1/klm/local/session`);
  expect(response.status()).toBe(200);
  const raw = await response.json() as {
    accessToken?: string;
    access_token?: string;
    user: SessionIdentity;
    session: WorkspaceSession['session'];
  };
  const token = raw.accessToken ?? raw.access_token;
  expect(token).toBeTruthy();
  expect(raw.session.activeWorkspace?.id).toBeTruthy();
  return { token: token as string, user: raw.user, session: raw.session };
}

async function inviteAndActivateWorkspaceMember(
  request: APIRequestContext,
  input: {
    rootToken: string;
    tenantToken: string;
    workspaceId: string;
    name: string;
    email: string;
    accessPackage: string;
    runId: string;
  },
): Promise<{ userId: string; name: string }> {
  const invitationResponse = await request.post(`${API_URL}/api/v1/platform/people/invitations`, {
    headers: {
      ...bearer(input.rootToken),
      'Idempotency-Key': `e2e-invite-${input.runId}-${crypto.randomUUID()}`,
    },
    data: {
      workspaceId: input.workspaceId,
      name: input.name,
      email: input.email,
      accessPackage: input.accessPackage,
    },
  });
  expect(invitationResponse.status(), await invitationResponse.text()).toBe(201);
  const invitation = await invitationResponse.json() as { id: string; user_id: string };

  const deliveryResponse = await request.get(
    `${API_URL}/api/v1/klm/local/invitations/${invitation.id}/delivery`,
    { headers: bearer(input.tenantToken) },
  );
  expect(deliveryResponse.status(), await deliveryResponse.text()).toBe(200);
  const delivery = await deliveryResponse.json() as { id: string; code: string };

  const acceptance = await request.post(
    `${API_URL}/api/v1/auth/invitations/${invitation.id}/accept`,
    {
      headers: { 'Idempotency-Key': `e2e-accept-${input.runId}-${crypto.randomUUID()}` },
      data: {
        deliveryId: delivery.id,
        code: delivery.code,
        name: input.name,
        password: `E2e!MeuExecutivo-${input.runId}-9x`,
      },
    },
  );
  expect(acceptance.status(), await acceptance.text()).toBe(200);
  return { userId: invitation.user_id, name: input.name };
}

async function seedTenantCollaborativeTask(
  request: APIRequestContext,
  rootToken: string,
  runId: string,
): Promise<SeededCollaborativeTask> {
  const tenant = await localWorkspaceSession(request);
  const workspace = tenant.session.activeWorkspace;
  const assigneeName = `Executor E2E ${runId}`;
  const reviewerName = `Revisor E2E ${runId}`;
  const assignee = await inviteAndActivateWorkspaceMember(request, {
    rootToken,
    tenantToken: tenant.token,
    workspaceId: workspace.id,
    name: assigneeName,
    email: `executor-${runId}@example.com`,
    accessPackage: 'associate_lawyer',
    runId,
  });
  const reviewer = await inviteAndActivateWorkspaceMember(request, {
    rootToken,
    tenantToken: tenant.token,
    workspaceId: workspace.id,
    name: reviewerName,
    email: `revisor-${runId}@example.com`,
    accessPackage: 'senior_lawyer',
    runId,
  });

  const matterResponse = await request.get(`${API_URL}/api/v1/matters?limit=1`, {
    headers: bearer(tenant.token),
  });
  expect(matterResponse.status(), await matterResponse.text()).toBe(200);
  const matters = await matterResponse.json() as Array<{ id: string }>;
  expect(matters.length, 'O seed local precisa possuir ao menos um caso para a jornada de tarefas.').toBeGreaterThan(0);

  const ownerDueAt = isoAfter(3);
  const assigneeDueAt = isoAfter(2);
  const reviewDueAt = isoAfter(6);
  const generalDueAt = isoAfter(7);
  const title = `Tarefa privada colaborativa ${runId}`;
  const taskResponse = await request.post(
    `${API_URL}/api/v1/matters/${matters[0].id}/tasks`,
    {
      headers: bearer(tenant.token),
      data: {
        title,
        description: `Conteúdo tenant privado e exclusivo da sessão ${runId}.`,
        ownerId: tenant.user.id,
        participants: [
          {
            userId: tenant.user.id,
            role: 'owner',
            dueAt: ownerDueAt,
            requiredForCompletion: true,
          },
          {
            userId: assignee.userId,
            role: 'assignee',
            dueAt: assigneeDueAt,
            requiredForCompletion: true,
          },
          {
            userId: reviewer.userId,
            role: 'reviewer',
            dueAt: reviewDueAt,
            requiredForCompletion: false,
          },
        ],
        dueAt: generalDueAt,
        reviewDueAt,
        reviewRequired: true,
        priority: 'alta',
        idempotencyKey: `e2e-task-${runId}-${crypto.randomUUID()}`,
      },
    },
  );
  expect(taskResponse.status(), await taskResponse.text()).toBe(201);
  let task = await taskResponse.json() as SeededCollaborativeTask['task'];
  const comment = `Atualização privada da tarefa ${runId}`;
  const commentResponse = await request.post(`${API_URL}/api/v1/tasks/${task.id}/comments`, {
    headers: bearer(tenant.token),
    data: { expectedVersion: task.version, comment },
  });
  expect(commentResponse.status(), await commentResponse.text()).toBe(200);
  task = await commentResponse.json() as SeededCollaborativeTask['task'];

  return {
    tenantToken: tenant.token,
    tenantUser: tenant.user,
    workspace,
    matterId: matters[0].id,
    task,
    ownerName: tenant.user.name,
    assigneeName,
    reviewerName,
    ownerDueAt,
    assigneeDueAt,
    reviewDueAt,
    generalDueAt,
    comment,
  };
}

async function ensureAdministrativeSession(
  request: APIRequestContext,
  token: string,
): Promise<string> {
  const headers = bearer(token);
  const statusResponse = await request.get(`${API_URL}/api/v1/auth/mfa`, { headers });
  expect(statusResponse.status(), await statusResponse.text()).toBe(200);
  const mfa = await statusResponse.json() as { enabled: boolean };
  if (!mfa.enabled) {
    const enrollmentResponse = await request.post(`${API_URL}/api/v1/auth/mfa/enroll`, { headers });
    expect(enrollmentResponse.status(), await enrollmentResponse.text()).toBe(200);
    const enrollment = await enrollmentResponse.json() as { challengeId: string; simulatedCode: string };
    expect(enrollment.simulatedCode).toBeTruthy();
    const verifiedEnrollment = await request.post(
      `${API_URL}/api/v1/auth/mfa/enroll/${enrollment.challengeId}/verify`,
      { headers, data: { code: enrollment.simulatedCode } },
    );
    expect(verifiedEnrollment.status(), await verifiedEnrollment.text()).toBe(200);
  }

  const challengeResponse = await request.post(`${API_URL}/api/v1/auth/admin-sessions/challenge`, { headers });
  expect(challengeResponse.status(), await challengeResponse.text()).toBe(200);
  const challenge = await challengeResponse.json() as { challengeId: string; simulatedCode: string };
  expect(challenge.simulatedCode).toBeTruthy();
  const verification = await request.post(
    `${API_URL}/api/v1/auth/admin-sessions/challenge/${challenge.challengeId}/verify`,
    { headers, data: { code: challenge.simulatedCode } },
  );
  expect(verification.status(), await verification.text()).toBe(200);
  const session = await verification.json() as { administrativeSessionToken: string };
  expect(session.administrativeSessionToken).toBeTruthy();
  return session.administrativeSessionToken;
}

async function openSupportTaskDrawer(
  page: Page,
  task: { id: string; title: string },
) {
  const filteredPage = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/tasks'
      && url.searchParams.get('scope') === 'workspace'
      && url.searchParams.get('search') === task.title
      && response.status() === 200;
  });
  await page.getByRole('textbox', { name: 'Buscar tarefas da conta em suporte' }).fill(task.title);
  await filteredPage;
  await page.getByRole('heading', { name: task.title, exact: true }).click();
  const drawer = page.getByRole('dialog', { name: task.title });
  await expect(drawer).toBeVisible();
  return drawer;
}

const routes = [
  '/blackbox', '/blackbox/tasks', '/blackbox/operations', '/blackbox/exports', '/blackbox/workspaces', '/blackbox/growth-crm',
  '/blackbox/support-tickets', '/blackbox/inbox', '/blackbox/support-access',
  '/blackbox/token-packages', '/blackbox/billing', '/blackbox/plans',
  '/blackbox/benefits', '/blackbox/coupons', '/blackbox/performance',
  '/blackbox/advanced-reports', '/blackbox/creative-library',
  '/blackbox/announcements', '/blackbox/team', '/blackbox/team/continuity',
  '/blackbox/configuration-studio', '/blackbox/errors', '/blackbox/ai-operations',
  '/blackbox/ai-costs', '/blackbox/meta-rates', '/blackbox/meta-health',
  '/blackbox/infrastructure', '/blackbox/official-whatsapp', '/blackbox/people',
  '/blackbox/site', '/blackbox/blog', '/blackbox/media', '/blackbox/seo',
  '/blackbox/google-business', '/blackbox/settings',
  '/blackbox/governance/integrations', '/blackbox/governance/logs',
  '/blackbox/governance/audit', '/blackbox/governance/security',
  '/blackbox/governance/compliance', '/blackbox/governance/data_governance',
  '/blackbox/governance/releases', '/blackbox/governance/feature_flags',
] as const;

function pageErrors(page: Page): string[] {
  const failures: string[] = [];
  page.on('pageerror', (error) => failures.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error' && !message.text().startsWith('Failed to load resource:')) {
      failures.push(message.text());
    }
  });
  return failures;
}

test.beforeEach(async ({ page, request }) => {
  await authenticatePageForRole(page, request, 'platform_owner');
});

for (const path of routes) {
  test(`${path} abre para o Super Admin sem bloqueio ou overflow @desktop-only`, async ({ page }) => {
    const failures = pageErrors(page);
    const response = await page.goto(path, { waitUntil: 'domcontentloaded' });
    expect(response?.status()).toBeLessThan(400);
    await expect(page.getByText('Meu Executivo', { exact: true }).last()).toBeVisible();
    await expect(page.getByText('Módulo não autorizado')).toHaveCount(0);
    await expect(page.getByText('Identidade sem acesso ao Blackbox')).toHaveCount(0);
    await expect(page.getByTestId('blackbox-route-scroll')).toBeVisible();
    await page.waitForTimeout(150);
    const geometry = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      document: document.documentElement.scrollWidth,
      body: document.body.scrollWidth,
      routeOverflow: getComputedStyle(document.querySelector('[data-testid="blackbox-route-scroll"]')!).overflowY,
    }));
    expect(Math.max(geometry.document, geometry.body) - geometry.viewport).toBeLessThanOrEqual(1);
    expect(geometry.routeOverflow).toBe('auto');
    expect(failures).toEqual([]);
  });
}

test('workspace não é servido pelo bundle interno @desktop-only', async ({ page }) => {
  await page.goto('/app', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: 'Ambiente interno isolado' })).toBeVisible();
});

test('equipe usa páginas reais e lookup pesquisável sem carregar todos os funcionários @desktop-only', async ({ page }) => {
  const legacyStaffRequests: string[] = [];
  const legacyTeamRequests: string[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (path === '/api/v1/platform/staff') {
      legacyStaffRequests.push(request.url());
    }
    if (path === '/api/v1/platform/organization/teams') {
      legacyTeamRequests.push(request.url());
    }
  });
  const teamPageResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/platform/organization/teams/page'
      && url.searchParams.get('limit') === '5';
  });
  await page.goto('/blackbox/team', { waitUntil: 'domcontentloaded' });
  const teamPageResponse = await teamPageResponsePromise;
  expect(teamPageResponse.status()).toBe(200);
  const teamPage = await teamPageResponse.json() as { items: unknown[]; total: number; hasMore: boolean };
  expect(Array.isArray(teamPage.items)).toBe(true);
  expect(teamPage.total).toBeGreaterThanOrEqual(teamPage.items.length);
  expect(typeof teamPage.hasMore).toBe('boolean');
  await expect(page.getByText(/funcionário\(s\)/)).toBeVisible();
  await page.getByRole('button', { name: 'Equipe', exact: true }).click();
  const teamDialog = page.getByRole('dialog', { name: 'Nova equipe' });
  await teamDialog.getByRole('button', { name: 'Selecionar gestor da equipe' }).click();
  const lookupDialog = page.getByRole('dialog', { name: 'Selecionar gestor da equipe' });
  const lookupResponsePromise = page.waitForResponse((response) => (
    response.url().includes('/api/v1/platform/lookups/query') && response.request().method() === 'POST'
  ));
  await lookupDialog.getByRole('textbox', { name: 'Pesquisar Selecionar gestor da equipe' }).fill('suporte');
  const lookupResponse = await lookupResponsePromise;
  expect(lookupResponse.status()).toBe(200);
  expect(legacyStaffRequests).toEqual([]);
  await page.keyboard.press('Escape');
  await expect(lookupDialog).toBeHidden();
  await teamDialog.getByRole('button', { name: 'Cancelar' }).click();
  await expect(teamDialog).toBeHidden();
  await page.getByRole('button', { name: 'Membro', exact: true }).click();
  const memberDialog = page.getByRole('dialog', { name: 'Adicionar membro' });
  await memberDialog.getByRole('button', { name: 'Selecionar equipe do funcionário' }).click();
  const teamLookup = page.getByRole('dialog', { name: 'Selecionar equipe do funcionário' });
  const searchedTeams = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/platform/organization/teams/page'
      && url.searchParams.get('search') === 'suporte'
      && url.searchParams.get('limit') === '20';
  });
  await teamLookup.getByRole('textbox', { name: 'Pesquisar Selecionar equipe do funcionário' }).fill('suporte');
  expect((await searchedTeams).status()).toBe(200);
  expect(legacyTeamRequests).toEqual([]);
});

test('continuidade da equipe usa histórico paginado e não a janela legada @desktop-only', async ({ page }) => {
  const legacyRequests: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname === '/api/v1/platform/staff-reassignments') {
      legacyRequests.push(request.url());
    }
  });
  const historyResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/platform/staff-reassignments/page'
      && url.searchParams.get('limit') === '10'
      && url.searchParams.get('offset') === '0';
  });

  await page.goto('/blackbox/team/continuity', { waitUntil: 'domcontentloaded' });
  const historyResponse = await historyResponsePromise;
  expect(historyResponse.status()).toBe(200);
  const payload = await historyResponse.json() as {
    items: unknown[]; total: number; limit: number; offset: number; hasMore: boolean;
  };
  expect(payload.limit).toBe(10);
  expect(payload.offset).toBe(0);
  expect(payload.total).toBeGreaterThanOrEqual(payload.items.length);
  expect(typeof payload.hasMore).toBe('boolean');
  await expect(page.getByRole('heading', { name: 'Continuidade da equipe' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Anterior' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Próxima' })).toBeVisible();
  expect(legacyRequests).toEqual([]);
});

test('administração das filas usa paginação server-side sem listas legadas limitadas @desktop-only', async ({ page }) => {
  const legacyRequests: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (
      url.pathname === '/api/v1/platform/organization/operational-assignments'
      || url.pathname === '/api/v1/platform/organization/reviews'
    ) {
      legacyRequests.push(request.url());
    }
  });
  const assignmentResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/platform/organization/operational-assignments/page'
      && url.searchParams.get('limit') === '10'
      && url.searchParams.get('offset') === '0';
  });
  const reviewResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/platform/organization/reviews/page'
      && url.searchParams.get('limit') === '10'
      && url.searchParams.get('offset') === '0';
  });

  await page.goto('/blackbox/tasks', { waitUntil: 'domcontentloaded' });
  await page.getByRole('tab', { name: 'Work items' }).click();
  const [assignmentResponse, reviewResponse] = await Promise.all([
    assignmentResponsePromise,
    reviewResponsePromise,
  ]);
  expect(assignmentResponse.status()).toBe(200);
  expect(reviewResponse.status()).toBe(200);
  const assignments = await assignmentResponse.json() as { items: unknown[]; total: number; hasMore: boolean };
  const reviews = await reviewResponse.json() as { items: unknown[]; total: number; hasMore: boolean };
  expect(Array.isArray(assignments.items)).toBe(true);
  expect(assignments.total).toBeGreaterThanOrEqual(assignments.items.length);
  expect(typeof assignments.hasMore).toBe('boolean');
  expect(Array.isArray(reviews.items)).toBe(true);
  expect(reviews.total).toBeGreaterThanOrEqual(reviews.items.length);
  expect(typeof reviews.hasMore).toBe('boolean');
  await expect(page.getByRole('heading', { name: 'Administração das filas' })).toBeVisible();
  expect(legacyRequests).toEqual([]);
});

test('visão root de modelos de IA percorre múltiplas contas sem trocar tenant na mesma sessão @desktop-only', async ({ page }) => {
  const modelsResponsePromise = page.waitForResponse((response) => (
    new URL(response.url()).pathname === '/api/v1/platform/ai-models'
  ));
  await page.goto('/blackbox/ai-costs', { waitUntil: 'domcontentloaded' });
  const response = await modelsResponsePromise;
  expect(response.status()).toBe(200);
  await expect(page.getByTestId('platform-ai-models-panel')).toBeVisible();
  await expect(page.getByText('Não foi possível carregar a visão de modelos')).toHaveCount(0);
  await expect(page.getByText('Defaults globais por classe')).toBeVisible();
});

test('SAC cria ticket com lookups pesquisáveis sem coleções fixas @desktop-only', async ({ page }) => {
  const legacyCollections: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (
      (url.pathname === '/api/v1/platform/workspaces' && url.searchParams.get('limit') === '200')
      || (url.pathname === '/api/v1/platform/people' && url.searchParams.get('limit') === '200')
      || url.pathname === '/api/v1/platform/staff'
    ) {
      legacyCollections.push(request.url());
    }
  });
  await page.goto('/blackbox/support-tickets', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Filtros avançados' }).click();
  const staffFilterResponsePromise = page.waitForResponse((candidate) => {
    if (
      new URL(candidate.url()).pathname !== '/api/v1/platform/lookups/query'
      || candidate.request().method() !== 'POST'
    ) return false;
    return candidate.request().postDataJSON()?.purpose === 'support_ticket_filter';
  });
  await page.getByRole('button', { name: 'Filtrar por responsável' }).click();
  const staffFilterResponse = await staffFilterResponsePromise;
  expect(staffFilterResponse.status()).toBe(200);
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Abrir ticket' }).click();
  const createDialog = page.getByRole('dialog', { name: 'Abrir ticket pela equipe do SaaS' });
  await createDialog.getByRole('button', { name: 'Conta do novo ticket' }).click();
  const lookup = page.getByRole('dialog', { name: 'Conta do novo ticket' });
  const response = await page.waitForResponse((candidate) => (
    candidate.url().includes('/api/v1/platform/lookups/query')
    && candidate.request().method() === 'POST'
  ));
  expect(response.status()).toBe(200);
  await expect(lookup.getByRole('textbox', { name: 'Pesquisar Conta do novo ticket' })).toBeVisible();
  expect(legacyCollections).toEqual([]);
});

test('continuidade usa lookups com escopo próprio sem carregar o diretório completo @desktop-only', async ({ page }) => {
  const legacyStaffRequests: string[] = [];
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/v1/platform/staff') {
      legacyStaffRequests.push(request.url());
    }
  });

  await page.goto('/blackbox/team/continuity', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: 'Continuidade da equipe do SaaS' })).toBeVisible();

  const sourceResponsePromise = page.waitForResponse((candidate) => {
    if (
      new URL(candidate.url()).pathname !== '/api/v1/platform/lookups/query'
      || candidate.request().method() !== 'POST'
    ) return false;
    return candidate.request().postDataJSON()?.purpose === 'staff_reassignment_source';
  });
  await page.getByRole('button', { name: 'Selecionar funcionário de origem' }).click();
  expect((await sourceResponsePromise).status()).toBe(200);
  await page.keyboard.press('Escape');

  const targetResponsePromise = page.waitForResponse((candidate) => {
    if (
      new URL(candidate.url()).pathname !== '/api/v1/platform/lookups/query'
      || candidate.request().method() !== 'POST'
    ) return false;
    return candidate.request().postDataJSON()?.purpose === 'staff_reassignment_target';
  });
  await page.getByRole('button', { name: 'Selecionar funcionário substituto' }).click();
  expect((await targetResponsePromise).status()).toBe(200);
  expect(legacyStaffRequests).toEqual([]);
});

test('inbox e logs usam contratos cursor no browser @desktop-only', async ({ page }) => {
  const inboxResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/platform/inbox/conversations'
      && url.searchParams.get('limit') === '50';
  });
  await page.goto('/blackbox/inbox', { waitUntil: 'domcontentloaded' });
  const inboxResponse = await inboxResponsePromise;
  expect(inboxResponse.status()).toBe(200);
  const inboxPage = await inboxResponse.json();
  expect(typeof inboxPage.hasMore).toBe('boolean');
  expect('nextCursor' in inboxPage).toBe(true);
  await expect(page.getByRole('button', { name: 'Anterior' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Próxima' })).toBeVisible();

  const logsResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/platform/governance/operational-logs'
      && url.searchParams.get('limit') === '50';
  });
  await page.goto('/blackbox/governance/logs', { waitUntil: 'domcontentloaded' });
  const logsResponse = await logsResponsePromise;
  expect(logsResponse.status()).toBe(200);
  const logsPage = await logsResponse.json();
  expect(typeof logsPage.hasMore).toBe('boolean');
  expect('nextCursor' in logsPage).toBe(true);
  const logsCard = page.getByText('Logs operacionais persistidos', { exact: true })
    .locator('..').locator('..');
  await expect(logsCard.getByRole('button', { name: 'Anterior' })).toBeVisible();
  await expect(logsCard.getByRole('button', { name: 'Próxima' })).toBeVisible();
});

test('inbox atribui responsável por lookup autorizado sem coleção fixa @desktop-only', async ({ page }) => {
  const legacyAssigneeRequests: string[] = [];
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/v1/platform/inbox/assignees') {
      legacyAssigneeRequests.push(request.url());
    }
  });
  const contactName = `Contato lookup ${Date.now()}`;

  await page.goto('/blackbox/inbox', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Registrar conversa' }).click();
  const dialog = page.getByRole('dialog', { name: 'Registrar conversa recebida' });
  await dialog.getByLabel('Contato').fill(contactName);
  await dialog.getByLabel('Mensagem recebida').fill('Mensagem local para comprovar o seletor de responsável.');
  const createResponsePromise = page.waitForResponse((response) => (
    new URL(response.url()).pathname === '/api/v1/platform/inbox/conversations'
    && response.request().method() === 'POST'
  ));
  await dialog.getByRole('button', { name: 'Registrar' }).click();
  expect((await createResponsePromise).status()).toBe(201);
  await page.getByRole('button', { name: new RegExp(contactName) }).click();
  await expect(page.getByRole('heading', { name: contactName })).toBeVisible();

  const lookupResponsePromise = page.waitForResponse((response) => {
    if (
      new URL(response.url()).pathname !== '/api/v1/platform/lookups/query'
      || response.request().method() !== 'POST'
    ) return false;
    return response.request().postDataJSON()?.purpose === 'inbox_assignee';
  });
  await page.getByRole('button', { name: 'Responsável da conversa' }).click();
  expect((await lookupResponsePromise).status()).toBe(200);
  expect(legacyAssigneeRequests).toEqual([]);
});

test('sessões de segurança usam cursor e total global honesto @desktop-only', async ({ page }) => {
  const responsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/platform/governance/security/overview'
      && url.searchParams.get('limit') === '50';
  });
  await page.goto('/blackbox/governance/security', { waitUntil: 'domcontentloaded' });
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  const payload = await response.json();
  expect(typeof payload.summary.activeUserSessions).toBe('number');
  expect(typeof payload.hasMore).toBe('boolean');
  expect(payload.nextCursor === null || typeof payload.nextCursor === 'string').toBe(true);
  const sessionsCard = page.getByText('Sessões ativas e postura de identidade', { exact: true })
    .locator('..').locator('..');
  await expect(sessionsCard.getByRole('button', { name: 'Anterior' })).toBeVisible();
  await expect(sessionsCard.getByRole('button', { name: 'Próxima' })).toBeVisible();
});

test('erros usam paginação real e não convertem falha em lista vazia @desktop-only', async ({ page }) => {
  const responsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/error-logs'
      && url.searchParams.get('limit') === '25'
      && url.searchParams.get('skip') === '0';
  });
  await page.goto('/blackbox/errors', { waitUntil: 'domcontentloaded' });
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  const payload = await response.json();
  expect(typeof payload.total).toBe('number');
  expect(Array.isArray(payload.items)).toBe(true);
  if (payload.total > 0) {
    await expect(page.getByRole('button', { name: 'Anterior' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Próxima' })).toBeVisible();
  }
  await expect(page.getByText('Erros indisponíveis')).toHaveCount(0);
});

test('releases usam total e paginação server-side @desktop-only', async ({ page }) => {
  const responsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/platform/governance/releases/records/page'
      && url.searchParams.get('limit') === '50'
      && url.searchParams.get('offset') === '0';
  });
  await page.goto('/blackbox/governance/releases', { waitUntil: 'domcontentloaded' });
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  const payload = await response.json();
  expect(typeof payload.total).toBe('number');
  expect(typeof payload.hasMore).toBe('boolean');
  expect(Array.isArray(payload.items)).toBe(true);
  if (payload.total > 0) {
    await expect(page.getByRole('button', { name: 'Anterior' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Próxima' })).toBeVisible();
  } else {
    await expect(page.getByText('Nenhuma release observada neste ambiente.')).toBeVisible();
  }
});

test('comunicados usam paginação server-side e total real @desktop-only', async ({ page }) => {
  const responsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/platform/notices'
      && url.searchParams.get('limit') === '25'
      && url.searchParams.get('offset') === '0';
  });
  await page.goto('/blackbox/announcements', { waitUntil: 'domcontentloaded' });
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  const payload = await response.json();
  expect(typeof payload.total).toBe('number');
  expect(Array.isArray(payload.items)).toBe(true);
  if (payload.total > 0) {
    await expect(page.getByRole('button', { name: 'Anterior' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Próxima' })).toBeVisible();
  }
});

test('editorial, mídia e Google Business usam coleções paginadas @desktop-only', async ({ page }) => {
  const cases = [
    { path: '/blackbox/blog', apiPath: '/api/v1/platform/blog/posts/page', limit: '25' },
    { path: '/blackbox/media', apiPath: '/api/v1/platform/blog/media/assets/page', limit: '24' },
    { path: '/blackbox/google-business', apiPath: '/api/v1/platform/blog/google-business/jobs/page', limit: '25' },
  ] as const;

  for (const item of cases) {
    const responsePromise = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname === item.apiPath
        && url.searchParams.get('limit') === item.limit
        && url.searchParams.get('offset') === '0';
    });
    await page.goto(item.path, { waitUntil: 'domcontentloaded' });
    const response = await responsePromise;
    expect(response.status()).toBe(200);
    const payload = await response.json();
    expect(typeof payload.total).toBe('number');
    expect(typeof payload.hasMore).toBe('boolean');
    expect(Array.isArray(payload.items)).toBe(true);
    await expect(page.getByRole('button', { name: 'Anterior' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Próxima' })).toBeVisible();
  }
});

test('Estúdio pesquisa contas autorizadas sem carregar a coleção integral @desktop-only', async ({ page }) => {
  const legacyCollections: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname === '/api/v1/platform/configuration-studio/workspaces') {
      legacyCollections.push(request.url());
    }
    if (/\/workspaces\/[^/]+\/drafts$/.test(url.pathname)) {
      legacyCollections.push(request.url());
    }
  });
  const initialResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/platform/configuration-studio/workspaces/page'
      && url.searchParams.get('limit') === '20'
      && url.searchParams.get('offset') === '0';
  });
  const exactAccessPromise = page.waitForResponse((response) => (
    /\/api\/v1\/platform\/configuration-studio\/workspaces\/[^/]+\/access$/.test(
      new URL(response.url()).pathname,
    )
  ));
  const draftsPagePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return /\/workspaces\/[^/]+\/drafts\/page$/.test(url.pathname)
      && url.searchParams.get('limit') === '10'
      && url.searchParams.get('offset') === '0';
  });
  await page.goto('/blackbox/configuration-studio', { waitUntil: 'domcontentloaded' });
  const initialResponse = await initialResponsePromise;
  expect(initialResponse.status()).toBe(200);
  const initialPayload = await initialResponse.json();
  expect(typeof initialPayload.total).toBe('number');
  if (initialPayload.total > 0) {
    const exactAccess = await exactAccessPromise;
    expect(exactAccess.status()).toBe(200);
    const draftsPage = await draftsPagePromise;
    expect(draftsPage.status()).toBe(200);
    const draftsPayload = await draftsPage.json();
    expect(typeof draftsPayload.total).toBe('number');
    expect(typeof draftsPayload.hasMore).toBe('boolean');
  }
  expect(legacyCollections).toEqual([]);

  await page.getByRole('button', { name: 'Conta que receberá a configuração' }).click();
  const dialog = page.getByRole('dialog', { name: 'Selecionar conta do Estúdio' });
  const searchResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/platform/configuration-studio/workspaces/page'
      && url.searchParams.get('search') === 'workspace';
  });
  await dialog.getByPlaceholder('Pesquisar por nome ou slug').fill('workspace');
  const searchResponse = await searchResponsePromise;
  expect(searchResponse.status()).toBe(200);
  await expect(dialog.getByRole('button', { name: 'Anterior' })).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Próxima' })).toBeVisible();
});

test('IA, tokens e acesso assistido pesquisam qualquer conta sem lista truncada @desktop-only', async ({ page }) => {
  const legacyWorkspaceCollections: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname === '/api/v1/platform/workspaces' && url.searchParams.has('limit')) {
      legacyWorkspaceCollections.push(request.url());
    }
  });

  for (const [path, selectorName] of [
    ['/blackbox/ai-costs', 'Selecionar conta para política de IA'],
    ['/blackbox/token-packages', 'Selecionar conta para liberação de tokens'],
    ['/blackbox/support-access', 'Selecionar conta para acesso administrativo'],
  ] as const) {
    await page.goto(path, { waitUntil: 'domcontentloaded' });
    const trigger = page.getByRole('button', { name: selectorName });
    await expect(trigger).toBeVisible();
    const lookupPromise = page.waitForResponse((response) => (
      new URL(response.url()).pathname === '/api/v1/platform/lookups/query'
      && response.request().method() === 'POST'
    ));
    await trigger.click();
    const lookupResponse = await lookupPromise;
    expect(lookupResponse.status()).toBe(200);
    await page.keyboard.press('Escape');
  }

  expect(legacyWorkspaceCollections).toEqual([]);
});

test('fila de pacotes de tokens usa paginação real e exige motivo administrativo @desktop-only', async ({ page }) => {
  const legacyRequests: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (/\/platform\/workspaces\/[^/]+\/ai-token-package-requests$/.test(url.pathname)) {
      legacyRequests.push(request.url());
    }
  });

  await page.goto('/blackbox/token-packages', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Selecionar conta para liberação de tokens' }).click();
  const dialog = page.getByRole('dialog', { name: 'Selecionar conta para liberação de tokens' });
  const option = dialog.getByRole('option').first();
  await expect(option).toBeVisible();
  const pageResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return /\/platform\/workspaces\/[^/]+\/ai-token-package-requests\/page$/.test(url.pathname)
      && url.searchParams.get('limit') === '10'
      && url.searchParams.get('offset') === '0';
  });
  await option.click();
  const pageResponse = await pageResponsePromise;
  expect(pageResponse.status()).toBe(200);
  const payload = await pageResponse.json() as {
    items: unknown[]; total: number; limit: number; offset: number; hasMore: boolean;
  };
  expect(payload.limit).toBe(10);
  expect(payload.offset).toBe(0);
  expect(payload.total).toBeGreaterThanOrEqual(payload.items.length);
  expect(typeof payload.hasMore).toBe('boolean');
  await expect(page.getByLabel('Motivo da decisão')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Anterior' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Próxima' })).toBeVisible();
  expect(legacyRequests).toEqual([]);
});

test('operação de IA navega no histórico completo do gateway @desktop-only', async ({ page }) => {
  const eventsResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/platform/ai-operations/events'
      && url.searchParams.get('limit') === '12'
      && url.searchParams.get('offset') === '0'
      && url.searchParams.get('only_fallbacks') === 'true';
  });

  await page.goto('/blackbox/ai-operations', { waitUntil: 'domcontentloaded' });
  const eventsResponse = await eventsResponsePromise;
  expect(eventsResponse.status()).toBe(200);
  const payload = await eventsResponse.json() as {
    items: unknown[]; total: number; limit: number; offset: number; hasMore: boolean;
  };
  expect(payload.limit).toBe(12);
  expect(payload.offset).toBe(0);
  expect(payload.total).toBeGreaterThanOrEqual(payload.items.length);
  expect(typeof payload.hasMore).toBe('boolean');
  await expect(page.getByText('Eventos do gateway')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Anterior' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Próxima' })).toBeVisible();

  const allEventsResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/platform/ai-operations/events'
      && url.searchParams.get('offset') === '0'
      && !url.searchParams.has('only_fallbacks');
  });
  await page.getByRole('button', { name: 'Só fallbacks' }).click();
  expect((await allEventsResponsePromise).status()).toBe(200);
});

test('trilha de acesso assistido usa histórico paginado e não a coleção legada @desktop-only', async ({ page, request }) => {
  const legacyEventRequests: string[] = [];
  page.on('request', (request) => {
    const pathname = new URL(request.url()).pathname;
    if (/\/platform\/support-access\/requests\/[^/]+\/events$/.test(pathname)) {
      legacyEventRequests.push(request.url());
    }
  });

  const root = await sessionForRole(request, 'platform_owner');
  const administrativeSessionToken = await ensureAdministrativeSession(request, root.token);
  await authenticatePageForRole(page, request, 'platform_owner');
  const workspaceResponse = await request.get(
    `${API_URL}/api/v1/platform/workspaces/page?limit=1&offset=0&status=active`,
    { headers: bearer(root.token) },
  );
  expect(workspaceResponse.status(), await workspaceResponse.text()).toBe(200);
  const payload = await workspaceResponse.json() as { items: Array<{ id: string; name: string }> };
  const workspace = payload.items[0];
  if (!workspace) throw new Error('Nenhuma conta disponível para a prova de acesso assistido.');
  const createResponse = await request.post(
    `${API_URL}/api/v1/platform/support-access/root-direct/${workspace.id}`,
    {
      headers: {
        ...bearer(root.token),
        'X-Administrative-Session': administrativeSessionToken,
      },
      data: { reason: 'Prova E2E da trilha paginada do acesso administrativo.' },
    },
  );
  expect(createResponse.status(), await createResponse.text()).toBe(200);

  await page.goto('/blackbox/support-access', { waitUntil: 'domcontentloaded' });
  await expect(page.getByText('Meu Executivo', { exact: true }).last()).toBeVisible();

  const trigger = page.getByRole('button', { name: 'Selecionar conta para acesso administrativo' });
  await trigger.click();
  const dialog = page.getByRole('dialog');
  await dialog.getByPlaceholder('Pesquisar conta').fill(workspace.name);
  const requestsPagePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/platform/support-access/requests/page'
      && url.searchParams.get('limit') === '25'
      && url.searchParams.get('offset') === '0';
  });
  await dialog.getByRole('option', { name: new RegExp(workspace.name, 'i') }).first().click();
  expect((await requestsPagePromise).status()).toBe(200);

  const eventPagePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return /\/platform\/support-access\/requests\/[^/]+\/events\/page$/.test(url.pathname)
      && url.searchParams.get('limit') === '25'
      && url.searchParams.get('offset') === '0';
  });
  await page.getByRole('button', { name: /Ver trilha/ }).first().click();
  const eventResponse = await eventPagePromise;
  expect(eventResponse.status()).toBe(200);
  const eventPage = await eventResponse.json();
  expect(typeof eventPage.total).toBe('number');
  expect(typeof eventPage.hasMore).toBe('boolean');
  expect(Array.isArray(eventPage.items)).toBe(true);
  await expect(page.getByRole('button', { name: 'Anterior' }).last()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Próxima' }).last()).toBeVisible();
  expect(legacyEventRequests).toEqual([]);
});

test('usuários e assinaturas usam totais e paginação do servidor @desktop-only', async ({ page }) => {
  const legacyWorkspaceCollections: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname === '/api/v1/platform/workspaces' && url.searchParams.get('limit') === '200') {
      legacyWorkspaceCollections.push(request.url());
    }
  });
  const peopleResponsePromise = page.waitForResponse((response) => (
    response.url().includes('/api/v1/platform/people?limit=50&offset=0')
    && response.request().method() === 'GET'
  ));
  await page.goto('/blackbox/people', { waitUntil: 'domcontentloaded' });
  const peopleResponse = await peopleResponsePromise;
  expect(peopleResponse.status()).toBe(200);
  const peoplePage = await peopleResponse.json();
  expect(peoplePage.total).toBe(peoplePage.count);
  expect(typeof peoplePage.hasMore).toBe('boolean');
  await expect(page.getByText('Todos os usuários')).toBeVisible();
  await page.getByRole('button', { name: 'Convidar usuário' }).click();
  const inviteLookupPromise = page.waitForResponse((response) => (
    new URL(response.url()).pathname === '/api/v1/platform/lookups/query'
    && response.request().method() === 'POST'
  ));
  await page.getByRole('button', { name: 'Conta do convite' }).click();
  const inviteLookup = await inviteLookupPromise;
  expect(inviteLookup.status()).toBe(200);
  expect(legacyWorkspaceCollections).toEqual([]);
  await page.keyboard.press('Escape');

  const subscriptionsResponsePromise = page.waitForResponse((response) => (
    response.url().includes('/api/v1/platform/subscriptions?limit=50&offset=0')
    && response.request().method() === 'GET'
  ));
  await page.goto('/blackbox/billing', { waitUntil: 'domcontentloaded' });
  const subscriptionsResponse = await subscriptionsResponsePromise;
  expect(subscriptionsResponse.status()).toBe(200);
  const subscriptionsPage = await subscriptionsResponse.json();
  expect(subscriptionsPage.total).toBe(subscriptionsPage.count);
  expect(typeof subscriptionsPage.hasMore).toBe('boolean');
  expect(subscriptionsPage.items.length).toBeGreaterThan(0);
  await expect(page.getByText('Assinaturas por workspace')).toBeVisible();

  const legacyEventRequests: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (/^\/api\/v1\/platform\/subscriptions\/[^/]+\/events$/.test(url.pathname)) {
      legacyEventRequests.push(request.url());
    }
  });
  const eventPageResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return /\/api\/v1\/platform\/subscriptions\/[^/]+\/events\/page$/.test(url.pathname)
      && url.searchParams.get('limit') === '10'
      && url.searchParams.get('offset') === '0';
  });
  const peoplePageResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/platform/people'
      && url.searchParams.get('workspaceId') === subscriptionsPage.items[0].workspace_id
      && url.searchParams.get('limit') === '8'
      && url.searchParams.get('offset') === '0';
  });
  const firstSubscription = subscriptionsPage.items[0] as { workspace_name: string };
  await page.getByRole('button', { name: firstSubscription.workspace_name, exact: true }).click();
  const [eventPageResponse, peoplePageResponse] = await Promise.all([
    eventPageResponsePromise,
    peoplePageResponsePromise,
  ]);
  expect(eventPageResponse.status()).toBe(200);
  expect(peoplePageResponse.status()).toBe(200);
  const eventPage = await eventPageResponse.json() as {
    items: unknown[]; total: number; limit: number; offset: number; hasMore: boolean;
  };
  expect(eventPage.limit).toBe(10);
  expect(eventPage.offset).toBe(0);
  expect(eventPage.total).toBeGreaterThanOrEqual(eventPage.items.length);
  expect(typeof eventPage.hasMore).toBe('boolean');
  await expect(page.getByText('Histórico da assinatura')).toBeVisible();
  await expect(page.getByText('Usuários da conta')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Anterior' }).last()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Próxima' }).last()).toBeVisible();
  expect(legacyEventRequests).toEqual([]);
});

test('relatórios avançados usam página analítica real sem limite fixo no navegador @desktop-only', async ({ page }) => {
  const responsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/platform/blackbox-studios/reports'
      && url.searchParams.get('limit') === '25'
      && url.searchParams.get('offset') === '0';
  });

  await page.goto('/blackbox/advanced-reports', { waitUntil: 'domcontentloaded' });
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  const payload = await response.json() as {
    rows: unknown[];
    totalRows: number;
    rowsWithData: number;
    limit: number;
    offset: number;
    hasMore: boolean;
  };
  expect(payload.limit).toBe(25);
  expect(payload.offset).toBe(0);
  expect(payload.totalRows).toBeGreaterThanOrEqual(payload.rows.length);
  expect(payload.rowsWithData).toBeLessThanOrEqual(payload.totalRows);
  expect(typeof payload.hasMore).toBe('boolean');
  await expect(page.getByRole('heading', { name: 'Relatórios avançados' })).toBeVisible();
  if (payload.rows.length > 0) {
    await expect(page.getByRole('button', { name: 'Anterior' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Próxima' })).toBeVisible();
  }
});

test('desempenho usa páginas autoritativas nos três rankings @desktop-only', async ({ page }) => {
  const paths = [
    '/api/v1/platform/blackbox-studios/performance/workspace-team',
    '/api/v1/platform/blackbox-studios/performance/saas-team',
    '/api/v1/platform/blackbox-studios/performance/workspace-benchmark',
  ];
  const responsePromises = paths.map((path) => page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === path
      && url.searchParams.get('limit') === '25'
      && url.searchParams.get('offset') === '0';
  }));

  await page.goto('/blackbox/performance', { waitUntil: 'domcontentloaded' });
  const responses = await Promise.all(responsePromises);
  for (const response of responses) {
    expect(response.status()).toBe(200);
    const payload = await response.json() as {
      rows: unknown[];
      total: number;
      limit: number;
      offset: number;
      hasMore: boolean;
    };
    expect(payload.limit).toBe(25);
    expect(payload.offset).toBe(0);
    expect(payload.total).toBeGreaterThanOrEqual(payload.rows.length);
    expect(typeof payload.hasMore).toBe('boolean');
  }
  await expect(page.getByRole('heading', { name: 'Desempenho e gamificação' })).toBeVisible();
  await expect(page.getByText(/de \d+/).first()).toBeVisible();
});

test('biblioteca criativa filtra e pagina no servidor sem coleção legada @desktop-only', async ({ page }) => {
  const legacyRequests: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname === '/api/v1/platform/blackbox-studios/creative-library') {
      legacyRequests.push(request.url());
    }
  });
  const responsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/platform/blackbox-studios/creative-library/page'
      && url.searchParams.get('status') === 'active'
      && url.searchParams.get('limit') === '25'
      && url.searchParams.get('offset') === '0';
  });

  await page.goto('/blackbox/creative-library', { waitUntil: 'domcontentloaded' });
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  const payload = await response.json() as {
    items: unknown[];
    total: number;
    limit: number;
    offset: number;
    hasMore: boolean;
  };
  expect(payload.limit).toBe(25);
  expect(payload.offset).toBe(0);
  expect(payload.total).toBeGreaterThanOrEqual(payload.items.length);
  expect(typeof payload.hasMore).toBe('boolean');
  await expect(page.getByRole('heading', { name: 'Biblioteca master de campanhas' })).toBeVisible();
  await expect(page.getByText(`${payload.total} item(ns) no filtro atual.`)).toBeVisible();
  expect(legacyRequests).toEqual([]);
});

test('CRM pagina cada estágio sem carregar o pipeline integral @desktop-only', async ({ page }) => {
  const stages = ['prospect', 'contatado', 'demo_agendada', 'trial', 'proposta', 'pagante'];
  const legacyPipelineRequests: string[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (
      path === '/api/v1/platform/growth/pipeline'
      || path === '/api/v1/platform/growth/assignees'
    ) {
      legacyPipelineRequests.push(request.url());
    }
  });
  const responsePromises = stages.map((stage) => page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/platform/growth/prospects'
      && url.searchParams.get('estagio') === stage
      && url.searchParams.get('limit') === '25'
      && url.searchParams.get('offset') === '0'
      && response.request().method() === 'GET';
  }));

  await page.goto('/blackbox/growth-crm', { waitUntil: 'domcontentloaded' });
  const responses = await Promise.all(responsePromises);
  for (const response of responses) {
    expect(response.status()).toBe(200);
    const payload = await response.json();
    expect(payload.total).toBe(payload.count);
    expect(typeof payload.hasMore).toBe('boolean');
  }
  await expect(page.getByText('Prospects no funil')).toBeVisible();

  const prospectName = `Prospect lookup ${Date.now()}`;
  await page.getByRole('button', { name: 'Novo prospect' }).click();
  const createDialog = page.getByRole('dialog', { name: 'Novo prospect' });
  await createDialog.getByLabel('Nome do escritório *').fill(prospectName);
  const createResponsePromise = page.waitForResponse((response) => (
    new URL(response.url()).pathname === '/api/v1/platform/growth/prospects'
    && response.request().method() === 'POST'
  ));
  await createDialog.getByRole('button', { name: 'Criar prospect' }).click();
  expect((await createResponsePromise).status()).toBe(201);
  await page.getByText(prospectName, { exact: true }).click();
  await page.getByRole('button', { name: 'Editar cadastro' }).click();
  const editDialog = page.getByRole('dialog', { name: 'Editar prospect' });
  const assigneeLookupPromise = page.waitForResponse((response) => {
    if (
      new URL(response.url()).pathname !== '/api/v1/platform/lookups/query'
      || response.request().method() !== 'POST'
    ) return false;
    return response.request().postDataJSON()?.purpose === 'growth_assignee';
  });
  await editDialog.getByRole('button', { name: 'Responsável comercial' }).click();
  expect((await assigneeLookupPromise).status()).toBe(200);
  expect(legacyPipelineRequests).toEqual([]);
});

test('planos usam catálogo e histórico paginados @desktop-only', async ({ page }) => {
  const plansResponsePromise = page.waitForResponse((response) => (
    response.url().includes('/api/v1/platform/plan-policies/page?limit=10&offset=0')
    && response.request().method() === 'GET'
  ));
  const versionStatuses: number[] = [];
  const versionPayloads: Array<Promise<{ total: number; hasMore: boolean }>> = [];
  page.on('response', (response) => {
    const url = new URL(response.url());
    if (url.pathname.endsWith('/versions/page')
      && url.searchParams.get('limit') === '10'
      && url.searchParams.get('offset') === '0'
      && response.request().method() === 'GET') {
      versionStatuses.push(response.status());
      versionPayloads.push(response.json());
    }
  });

  await page.goto('/blackbox/plans', { waitUntil: 'domcontentloaded' });
  await page.getByRole('tab', { name: 'Legado (somente leitura)' }).click();
  const plansResponse = await plansResponsePromise;
  expect(plansResponse.status()).toBe(200);
  const plansPage = await plansResponse.json();
  expect(typeof plansPage.total).toBe('number');
  expect(typeof plansPage.hasMore).toBe('boolean');
  if (plansPage.total > 0) {
    await expect.poll(() => versionStatuses.length).toBeGreaterThan(0);
    expect(versionStatuses[0]).toBe(200);
    const versionsPage = await versionPayloads[0];
    expect(typeof versionsPage.total).toBe('number');
    expect(typeof versionsPage.hasMore).toBe('boolean');
    await expect(page.getByText('Políticas dos planos')).toBeVisible();
  } else {
    await expect(page.getByText('Nenhum plano disponível para edição.')).toBeVisible();
  }
});

test('configurações globais e governança usam histórico paginado real @desktop-only', async ({ page, request }) => {
  const apiUrl = process.env.KLM_API_URL ?? 'http://127.0.0.1:8010';
  const root = await sessionForRole(request, 'platform_owner');
  const headers = { Authorization: `Bearer ${root.token}` };
  const currentBrand = await request.get(
    `${apiUrl}/api/v1/platform/configurations/brand.identity`,
    { headers },
  );
  if (currentBrand.status() === 404) {
    const created = await request.put(
      `${apiUrl}/api/v1/platform/configurations/brand.identity`,
      {
        headers,
        data: {
          payload: { name: 'Meu Executivo', source: 'e2e_local' },
          schemaVersion: '1.0',
          reason: 'Preparação local da prova de paginação do histórico global.',
        },
      },
    );
    expect(created.status()).toBe(200);
  } else {
    expect(currentBrand.status()).toBe(200);
  }
  const legacyHistoryRequests: string[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (
      /\/platform\/configurations\/[^/]+\/versions$/.test(path)
      || /\/platform\/governance\/[^/]+\/versions$/.test(path)
    ) {
      legacyHistoryRequests.push(request.url());
    }
  });

  const configurationPagePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/platform/configurations/brand.identity/versions/page'
      && url.searchParams.get('limit') === '10'
      && url.searchParams.get('offset') === '0';
  });
  await page.goto('/blackbox/settings', { waitUntil: 'domcontentloaded' });
  const configurationResponse = await configurationPagePromise;
  expect(configurationResponse.status()).toBe(200);
  const configurationPage = await configurationResponse.json() as {
    items: unknown[]; total: number; limit: number; offset: number; hasMore: boolean;
  };
  expect(configurationPage.limit).toBe(10);
  expect(configurationPage.offset).toBe(0);
  expect(configurationPage.total).toBeGreaterThanOrEqual(configurationPage.items.length);
  expect(typeof configurationPage.hasMore).toBe('boolean');
  await expect(page.getByRole('heading', { name: 'Configurações globais' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Anterior' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Próxima' })).toBeVisible();

  const governancePagePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/platform/governance/security/versions/page'
      && url.searchParams.get('limit') === '10'
      && url.searchParams.get('offset') === '0';
  });
  await page.goto('/blackbox/governance/security', { waitUntil: 'domcontentloaded' });
  const governanceResponse = await governancePagePromise;
  expect(governanceResponse.status()).toBe(200);
  const governancePage = await governanceResponse.json() as {
    items: unknown[]; total: number; limit: number; offset: number; hasMore: boolean;
  };
  expect(governancePage.limit).toBe(10);
  expect(governancePage.offset).toBe(0);
  expect(governancePage.total).toBeGreaterThanOrEqual(governancePage.items.length);
  expect(typeof governancePage.hasMore).toBe('boolean');
  await expect(page.getByRole('heading', { name: 'Histórico' })).toBeVisible();
  expect(legacyHistoryRequests).toEqual([]);
});

test('Conta 360 pagina responsáveis e marcos sem coleções integrais @desktop-only', async ({ page }) => {
  const legacyAccountCollections: string[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (
      /\/platform\/organization\/workspaces\/[^/]+\/assignments$/.test(path)
      || /\/platform\/organization\/workspaces\/[^/]+\/milestones$/.test(path)
    ) {
      legacyAccountCollections.push(request.url());
    }
  });
  const assignmentsPromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return /\/platform\/organization\/workspaces\/[^/]+\/assignments\/page$/.test(url.pathname)
      && url.searchParams.get('limit') === '5'
      && url.searchParams.get('offset') === '0';
  });
  const milestonesPromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return /\/platform\/organization\/workspaces\/[^/]+\/milestones\/page$/.test(url.pathname)
      && url.searchParams.get('limit') === '5'
      && url.searchParams.get('offset') === '0';
  });

  await page.goto('/blackbox/workspaces', { waitUntil: 'domcontentloaded' });
  const openAccount = page.getByRole('button', { name: 'Conta 360', exact: true }).first();
  await expect(openAccount).toBeVisible();
  await openAccount.click();
  const [assignmentsResponse, milestonesResponse] = await Promise.all([
    assignmentsPromise,
    milestonesPromise,
  ]);
  expect(assignmentsResponse.status()).toBe(200);
  expect(milestonesResponse.status()).toBe(200);
  for (const response of [assignmentsResponse, milestonesResponse]) {
    const payload = await response.json() as {
      items: unknown[]; total: number; limit: number; offset: number; hasMore: boolean;
    };
    expect(payload.limit).toBe(5);
    expect(payload.offset).toBe(0);
    expect(payload.total).toBeGreaterThanOrEqual(payload.items.length);
    expect(typeof payload.hasMore).toBe('boolean');
  }
  await expect(page.getByText('Responsabilidades e onboarding')).toBeVisible();
  expect(legacyAccountCollections).toEqual([]);
});

test('incidentes usam cursor opaco e total aberto da plataforma @desktop-only', async ({ page }) => {
  const incidentsResponsePromise = page.waitForResponse((response) => (
    response.url().includes('/api/v1/platform/governance/security/incidents?limit=25')
    && response.request().method() === 'GET'
  ));
  await page.goto('/blackbox/governance/security', { waitUntil: 'domcontentloaded' });
  const incidentsResponse = await incidentsResponsePromise;
  expect(incidentsResponse.status()).toBe(200);
  const incidentsPage = await incidentsResponse.json();
  expect(typeof incidentsPage.openCount).toBe('number');
  expect(typeof incidentsPage.hasMore).toBe('boolean');
  expect(incidentsPage.nextCursor === null || typeof incidentsPage.nextCursor === 'string').toBe(true);
  await expect(page.getByText('Gestão de incidentes')).toBeVisible();
});

test('auditoria de cupom usa contrato paginado real @desktop-only', async ({ page, request }) => {
  const session = await sessionForRole(request, 'platform_owner');
  const apiUrl = process.env.KLM_API_URL ?? 'http://127.0.0.1:8010';
  const suffix = Date.now();
  const code = `E2E-AUDIT-${suffix}`;
  const created = await request.post(`${apiUrl}/api/v1/platform/saas-benefits/coupons`, {
    headers: { Authorization: `Bearer ${session.token}` },
    data: {
      code,
      name: `Cupom auditável ${suffix}`,
      description: 'Validação do histórico paginado no browser.',
      benefitType: 'trial_extension',
      unit: 'days',
      amount: 5,
      allowedPlanCodes: [],
      totalLimit: 10,
      perWorkspaceLimit: 1,
      autoApply: true,
      reason: 'Criação E2E para validar auditoria paginada.',
    },
  });
  expect(created.status()).toBe(201);

  await page.goto('/blackbox/coupons', { waitUntil: 'domcontentloaded' });
  const auditResponsePromise = page.waitForResponse((response) => (
    response.url().includes('/audit/page?limit=20&offset=0')
    && response.request().method() === 'GET'
  ));
  await page.getByRole('button', { name: `Auditoria de ${code}` }).click();
  const auditResponse = await auditResponsePromise;
  expect(auditResponse.status()).toBe(200);
  await page.getByRole('tab', { name: 'Auditoria' }).click();
  await expect(page.getByText('created')).toBeVisible();
  await expect(page.getByText('1–1 de 1')).toBeVisible();
});

test('central de comandos preserva teclado, foco e anúncio de estado @accessibility', async ({ page }) => {
  await page.goto('/blackbox', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('button', { name: 'Abrir busca global' })).toBeVisible();
  await page.keyboard.press('Control+K');
  const dialog = page.getByRole('dialog', { name: 'Busca global e comandos' });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText('Ações autorizadas')).toBeVisible();
  await expect(dialog.getByRole('button', { name: /Criar conta/ })).toBeVisible();
  const search = dialog.getByRole('textbox', { name: 'Pesquisar no Blackbox' });
  await expect(search).toBeFocused();
  await search.fill('consulta sem resultado e2e');
  await expect(
    dialog.getByText(/A consulta foi concluída sem resultados autorizados|A busca está indisponível/),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
});

test('central executa ação rápida tipada e auditável sem navegar @role-journey', async ({ page, request }) => {
  const session = await sessionForRole(request, 'platform_owner');
  const apiUrl = process.env.KLM_API_URL ?? 'http://127.0.0.1:8010';
  const leadName = `Comando E2E ${Date.now()}`;
  const created = await request.post(`${apiUrl}/api/v1/platform/growth/prospects`, {
    headers: { Authorization: `Bearer ${session.token}` },
    data: { nomeEscritorio: leadName, origem: 'outro' },
  });
  expect(created.status()).toBe(201);
  const lead = await created.json() as { id: string };

  await page.goto('/blackbox', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('button', { name: 'Abrir busca global' })).toBeVisible();
  await page.keyboard.press('Control+K');
  const dialog = page.getByRole('dialog', { name: 'Busca global e comandos' });
  await dialog.getByRole('textbox', { name: 'Pesquisar no Blackbox' }).fill(leadName);
  const leadRow = dialog.getByText(leadName, { exact: true }).locator('..').locator('..');
  await expect(leadRow.getByRole('button', { name: /Assumir lead/ })).toBeVisible();
  await leadRow.getByRole('button', { name: /Assumir lead/ }).click();
  const confirmation = page.getByRole('alertdialog', { name: 'Assumir lead' });
  await expect(confirmation).toContainText('A rota de domínio revalidará sua permissão');
  await confirmation.getByRole('button', { name: 'Confirmar ação' }).click();
  await expect(page.getByText('Assumir lead concluído.', { exact: true })).toBeVisible();

  const manifest = await request.get(`${apiUrl}/api/v1/platform/session-manifest`, {
    headers: { Authorization: `Bearer ${session.token}` },
  });
  const actor = await manifest.json() as { userId: string };
  const detail = await request.get(`${apiUrl}/api/v1/platform/growth/prospects/${lead.id}`, {
    headers: { Authorization: `Bearer ${session.token}` },
  });
  expect(detail.status()).toBe(200);
  expect((await detail.json() as { owner_user_id: string }).owner_user_id).toBe(actor.userId);
});

test('central inicia acesso root direto com motivo e identidade preservada @role-journey', async ({ page, request }) => {
  const root = await sessionForRole(request, 'platform_owner');
  const administrativeSessionToken = await ensureAdministrativeSession(request, root.token);
  await authenticatePageForRole(page, request, 'platform_owner');
  await page.addInitScript((token) => {
    sessionStorage.setItem('klm_admin_session_token', token);
  }, administrativeSessionToken);
  await page.goto('/blackbox', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('button', { name: 'Abrir busca global' })).toBeVisible();
  await page.keyboard.press('Control+K');
  const dialog = page.getByRole('dialog', { name: 'Busca global e comandos' });
  await dialog.getByRole('textbox', { name: 'Pesquisar no Blackbox' }).fill('Meu Executivo Local');
  const accountRow = dialog.getByText('Meu Executivo Local', { exact: true }).first().locator('..').locator('..');
  await expect(accountRow.getByRole('button', { name: /Entrar como Super Admin/ })).toBeVisible();
  await accountRow.getByRole('button', { name: /Entrar como Super Admin/ }).click();
  const confirmation = page.getByRole('alertdialog', { name: 'Entrar como Super Admin' });
  await confirmation.getByRole('textbox', { name: 'Motivo do comando' }).fill('Diagnóstico E2E do acesso direto root.');
  const activationResponsePromise = page.waitForResponse((response) => (
    response.request().method() === 'POST'
    && response.url().includes('/api/v1/platform/support-access/root-direct/')
  ));
  await confirmation.getByRole('button', { name: 'Confirmar ação' }).click();
  const activationResponse = await activationResponsePromise;
  expect(activationResponse.status()).toBe(200);
  await page.waitForTimeout(100);
  const commandError = page.getByText('Não foi possível executar o comando', { exact: true });
  if (await commandError.isVisible()) {
    throw new Error(await commandError.locator('..').innerText());
  }
  await expect(page).toHaveURL(/\/blackbox\/support-session$/);
  await expect(page.getByText('Super Admin — acesso direto: Meu Executivo Local')).toBeVisible();
  await page.getByRole('button', { name: 'Voltar ao Blackbox' }).click();
  await expect(page).toHaveURL(/\/blackbox$/);
});

const roleLabels: Record<string, string> = {
  platform_owner: 'Titular da plataforma',
  platform_admin: 'Administrador operacional',
  sales_sdr: 'Vendas / SDR',
  commercial_manager: 'Gestão comercial',
  onboarding: 'Onboarding',
  customer_success: 'Customer Success',
  support: 'Suporte',
  technical_support: 'Suporte técnico',
  finance_billing: 'Financeiro e cobrança',
  marketing_content: 'Marketing e conteúdo',
  community_manager: 'Gestão de comunidade',
  compliance_security: 'Compliance e segurança',
  product_operations: 'Produto e operações',
  engineering: 'Engenharia / TI',
};

for (const [rolePackage, roleLabel] of Object.entries(roleLabels)) {
  test(`${rolePackage} recebe home operacional e decisão de autorização coerente @role-journey`, async ({ browser, request }) => {
    test.skip(process.env.KLM_E2E_ROLE_JOURNEYS !== '1', 'Requer seeds locais dos 14 papéis.');
    const context = await browser.newContext({ baseURL: process.env.KLM_BLACKBOX_URL ?? 'http://127.0.0.1:4174' });
    const page = await context.newPage();
    const token = await authenticatePageForRole(page, request, rolePackage);
    const dashboard = await request.get(`${process.env.KLM_API_URL ?? 'http://127.0.0.1:8010'}/api/v1/platform/dashboard/home`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(dashboard.status()).toBe(200);
    const dashboardPayload = await dashboard.json() as {
      role: string;
      roleLabel: string;
      metrics: Array<{ key: string; value: number | null; quality: string }>;
      actions: Array<{ key: string; label: string; href: string; module: string; action: string }>;
      sourceHealth: Array<{ key: string; status: string }>;
    };
    expect(dashboardPayload).toMatchObject({ role: rolePackage, roleLabel });
    expect(dashboardPayload.metrics.length).toBeGreaterThanOrEqual(3);
    expect(dashboardPayload.metrics.length).toBeLessThanOrEqual(6);
    expect(dashboardPayload.actions.length).toBeGreaterThan(0);
    expect(dashboardPayload.sourceHealth.length).toBeGreaterThan(0);
    for (const metric of dashboardPayload.metrics) {
      expect(metric.value === null || typeof metric.value === 'number').toBe(true);
      expect(metric.quality).not.toBe('');
    }
    expect(JSON.stringify(dashboardPayload)).not.toMatch(
      /"(?:body|message|document|email|phone|privateContent)"\s*:/i,
    );
    await page.goto('/blackbox', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: roleLabel, exact: true })).toBeVisible();
    await expect(page.getByText('Módulo não autorizado')).toHaveCount(0);
    const firstAction = dashboardPayload.actions[0];
    await page.getByRole('link', { name: firstAction.label, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`${firstAction.href.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?:[?#].*)?$`));
    await expect(page.getByText('Módulo não autorizado')).toHaveCount(0);

    const rootOnly = await request.get(`${process.env.KLM_API_URL ?? 'http://127.0.0.1:8010'}/api/v1/platform/token-usage`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (rolePackage === 'platform_owner') {
      expect(rootOnly.status()).not.toBe(403);
    } else {
      expect(rootOnly.status()).toBe(403);
    }
    await context.close();
  });
}

test('tarefa interna colaborativa preserva owner, executores, reviewer, prazos e histórico @operations', async ({ page, request }) => {
  test.skip(process.env.KLM_E2E_OPERATIONS !== '1', 'Requer API e banco local descartável.');
  test.setTimeout(90_000);
  const root = await sessionForRole(request, 'platform_owner');
  const owner = await sessionForRole(request, 'support');
  const assignee = await sessionForRole(request, 'technical_support');
  const reviewer = await sessionForRole(request, 'compliance_security');
  const observer = await sessionForRole(request, 'product_operations');
  const ownerUser = owner.user as SessionIdentity;
  const assigneeUser = assignee.user as SessionIdentity;
  const reviewerUser = reviewer.user as SessionIdentity;
  const observerUser = observer.user as SessionIdentity;
  const runId = crypto.randomUUID().slice(0, 8);
  const title = `Operação colaborativa interna ${runId}`;
  const comment = `Atualização operacional auditável ${runId}`;
  const ownerDueAt = isoAfter(3);
  const assigneeDueAt = isoAfter(2);
  const reviewDueAt = isoAfter(6);
  const dueAt = isoAfter(7);

  const createdResponse = await request.post(`${API_URL}/api/v1/platform/tasks`, {
    headers: bearer(root.token),
    data: {
      title,
      description: 'Jornada E2E real para colaboração entre funções internas do SaaS.',
      priority: 'P1',
      dueAt,
      ownerUserId: ownerUser.id,
      ownerDueAt,
      reviewerUserId: reviewerUser.id,
      reviewDueAt,
      reviewRequired: true,
      participants: [
        {
          userId: assigneeUser.id,
          role: 'assignee',
          personalDueAt: assigneeDueAt,
          requiredForCompletion: true,
        },
        {
          userId: observerUser.id,
          role: 'observer',
          requiredForCompletion: false,
        },
      ],
      reason: `Criação E2E da tarefa colaborativa ${runId}`,
    },
  });
  expect(createdResponse.status(), await createdResponse.text()).toBe(201);
  let task = await createdResponse.json() as {
    id: string;
    version: number;
    ownerUserId: string;
    reviewerUserId: string;
    ownerDueAt: string;
    reviewDueAt: string;
    dueAt: string;
    participants: Array<{ userId: string; role: string; personalDueAt: string | null }>;
  };
  const commentResponse = await request.post(`${API_URL}/api/v1/platform/tasks/${task.id}/comments`, {
    headers: bearer(root.token),
    data: { expectedVersion: task.version, comment },
  });
  expect(commentResponse.status(), await commentResponse.text()).toBe(200);
  task = await commentResponse.json() as typeof task;

  expect(task).toMatchObject({
    ownerUserId: ownerUser.id,
    reviewerUserId: reviewerUser.id,
  });
  // SQLite preserva o relógio UTC, mas não serializa o sufixo de timezone.
  // PostgreSQL mantém o offset; comparar o wall-clock torna a mesma prova
  // portável sem aceitar alteração de dia/hora/minuto/segundo.
  expect(persistedWallClock(task.ownerDueAt)).toBe(persistedWallClock(ownerDueAt));
  expect(persistedWallClock(task.reviewDueAt)).toBe(persistedWallClock(reviewDueAt));
  expect(persistedWallClock(task.dueAt)).toBe(persistedWallClock(dueAt));
  const persistedAssignee = task.participants.find((participant) => participant.userId === assigneeUser.id);
  expect(persistedAssignee).toMatchObject({ role: 'assignee' });
  expect(persistedWallClock(persistedAssignee?.personalDueAt ?? '')).toBe(persistedWallClock(assigneeDueAt));
  expect(task.participants).toEqual(expect.arrayContaining([
    expect.objectContaining({ userId: observerUser.id, role: 'observer' }),
  ]));
  const eventsResponse = await request.get(`${API_URL}/api/v1/platform/tasks/${task.id}/events?limit=200&offset=0`, {
    headers: bearer(root.token),
  });
  expect(eventsResponse.status(), await eventsResponse.text()).toBe(200);
  const eventPage = await eventsResponse.json() as {
    items: Array<{ eventType: string; comment: string | null }>;
  };
  expect(eventPage.items).toEqual(expect.arrayContaining([
    expect.objectContaining({ eventType: 'created' }),
    expect.objectContaining({ eventType: 'comment_added', comment }),
  ]));

  await page.goto('/blackbox/tasks', { waitUntil: 'domcontentloaded' });
  await page.getByRole('tab', { name: 'P0/P1' }).click();
  await page.getByRole('textbox', { name: 'Buscar tarefas internas' }).fill(title);
  await page.getByText(title, { exact: true }).click();
  await expect(page.getByText('Prazo geral', { exact: true })).toBeVisible();
  await expect(page.getByText('Prazo do responsável', { exact: true })).toBeVisible();
  await expect(page.getByText('Prazo da revisão', { exact: true })).toBeVisible();
  const renderedDeadlines = await Promise.all([
    page.getByText('Prazo geral', { exact: true }).locator('..').textContent(),
    page.getByText('Prazo do responsável', { exact: true }).locator('..').textContent(),
    page.getByText('Prazo da revisão', { exact: true }).locator('..').textContent(),
  ]);
  expect(new Set(renderedDeadlines).size).toBe(3);
  await page.getByRole('tab', { name: 'Equipe', exact: true }).click();
  await expect(page.getByText(ownerUser.name, { exact: true })).toBeVisible();
  await expect(page.getByText(assigneeUser.name, { exact: true })).toBeVisible();
  await expect(page.getByText(observerUser.name, { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Revisão', exact: true }).click();
  await expect(page.getByText(reviewerUser.name, { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Comentários', exact: true }).click();
  await expect(page.getByText(comment, { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Histórico', exact: true }).click();
  await expect(page.getByText('Tarefa criada', { exact: true })).toBeVisible();
  await expect(page.getByText('Comentário adicionado', { exact: true })).toBeVisible();
});

test('Conta 360 preserva metadados seguros e só revela tarefas em root_direct @operations', async ({ page, request }) => {
  test.skip(process.env.KLM_E2E_OPERATIONS !== '1', 'Requer API e banco local descartável.');
  test.setTimeout(90_000);
  const root = await sessionForRole(request, 'platform_owner');
  const platformAdmin = await sessionForRole(request, 'platform_admin');
  const administrativeSessionToken = await ensureAdministrativeSession(request, root.token);
  await authenticatePageForRole(page, request, 'platform_owner');
  await page.addInitScript((token) => {
    sessionStorage.setItem('klm_admin_session_token', token);
  }, administrativeSessionToken);
  const runId = crypto.randomUUID().slice(0, 8);
  const seeded = await seedTenantCollaborativeTask(request, root.token, runId);
  expect(seeded.task.participants).toEqual(expect.arrayContaining([
    expect.objectContaining({
      userId: seeded.tenantUser.id,
      role: 'owner',
      requiredForCompletion: true,
    }),
    expect.objectContaining({ role: 'assignee', requiredForCompletion: true }),
    expect.objectContaining({ role: 'reviewer', requiredForCompletion: false }),
  ]));
  expect(new Set([
    ...seeded.task.participants
      .filter((participant) => ['owner', 'assignee', 'reviewer'].includes(participant.role))
      .map((participant) => persistedWallClock(participant.dueAt ?? '')),
    persistedWallClock(seeded.task.reviewDueAt),
    persistedWallClock(seeded.task.dueAt),
  ]).size).toBeGreaterThanOrEqual(4);

  const overviewResponse = await request.get(
    `${API_URL}/api/v1/platform/workspaces/${seeded.workspace.id}/overview`,
    { headers: bearer(root.token) },
  );
  expect(overviewResponse.status(), await overviewResponse.text()).toBe(200);
  const overview = await overviewResponse.json() as {
    tasks: Record<string, unknown> & { total: number; content_included: boolean };
  };
  expect(overview.tasks.total).toBeGreaterThan(0);
  expect(overview.tasks.content_included).toBe(false);
  expect(JSON.stringify(overview.tasks)).not.toContain(seeded.task.title);
  expect(JSON.stringify(overview.tasks)).not.toContain(seeded.comment);

  const ordinaryWorkspaceScope = await request.get(`${API_URL}/api/v1/tasks`, {
    headers: bearer(seeded.tenantToken),
    params: { scope: 'workspace', search: seeded.task.title },
  });
  expect(ordinaryWorkspaceScope.status()).toBe(403);

  const adminRootDirect = await request.post(
    `${API_URL}/api/v1/platform/support-access/root-direct/${seeded.workspace.id}`,
    {
      headers: bearer(platformAdmin.token),
      data: { reason: `Tentativa E2E sem bypass root ${runId}` },
    },
  );
  expect(adminRootDirect.status()).toBe(403);

  await page.goto(`/blackbox/workspaces?q=${encodeURIComponent(seeded.workspace.name)}`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Conta 360', exact: true }).first().click();
  const safeSummary = page.getByTestId('workspace-task-safe-summary');
  await expect(safeSummary.getByRole('heading', { name: 'Tarefas da conta' })).toBeVisible();
  await expect(safeSummary).not.toContainText(seeded.task.title);
  await expect(page.getByText(seeded.task.title, { exact: true })).toHaveCount(0);
  await page.getByLabel('Motivo do acesso direto').fill(
    `Diagnóstico da tarefa colaborativa E2E ${runId}.`,
  );
  await safeSummary.getByRole('button', { name: 'Entrar e abrir tarefas' }).click();

  await expect(page).toHaveURL(/\/blackbox\/support-session$/);
  await expect(page.getByText(`Super Admin — acesso direto: ${seeded.workspace.name}`)).toBeVisible();
  await page.getByRole('button', { name: 'Tarefas', exact: true }).click();
  const drawer = await openSupportTaskDrawer(page, seeded.task);
  await drawer.getByRole('tab', { name: 'Equipe', exact: true }).click();
  await expect(drawer.getByText(seeded.ownerName, { exact: true })).toBeVisible();
  await expect(drawer.getByText(seeded.assigneeName, { exact: true })).toBeVisible();
  await expect(drawer.getByText(seeded.reviewerName, { exact: true })).toBeVisible();
  await expect(drawer.getByText('Executor obrigatório', { exact: true })).toHaveCount(2);
  await drawer.getByRole('tab', { name: 'Revisão', exact: true }).click();
  await expect(drawer.getByText('A revisão está configurada, mas ainda não foi submetida.')).toBeVisible();
  await drawer.getByRole('tab', { name: 'Comentários', exact: true }).click();
  await expect(drawer.getByText(seeded.comment, { exact: true })).toBeVisible();
  await drawer.getByRole('tab', { name: 'Histórico', exact: true }).click();
  await expect(drawer.getByText('Tarefa criada', { exact: true })).toBeVisible();
  await expect(drawer.getByText('Comentário adicionado', { exact: true })).toBeVisible();

  await page.keyboard.press('Escape');
  await expect(drawer).toBeHidden();
  await page.getByRole('button', { name: 'Voltar ao Blackbox' }).click();
  await expect(page).toHaveURL(/\/blackbox$/);
  await expect(page.getByText(seeded.task.title, { exact: true })).toHaveCount(0);
});

test('suporte aprovado revela a mesma tarefa somente após aprovação tenant e MFA @operations', async ({ browser, request }) => {
  test.skip(process.env.KLM_E2E_OPERATIONS !== '1', 'Requer API e banco local descartável.');
  test.setTimeout(90_000);
  const root = await sessionForRole(request, 'platform_owner');
  const support = await sessionForRole(request, 'support');
  const seeded = await seedTenantCollaborativeTask(request, root.token, crypto.randomUUID().slice(0, 8));
  const runId = crypto.randomUUID().slice(0, 8);
  const ticketReference = `SUP-E2E-${runId}`;
  const requested = await request.post(`${API_URL}/api/v1/platform/support-access/requests`, {
    headers: bearer(support.token),
    data: {
      workspaceId: seeded.workspace.id,
      reason: `Diagnóstico E2E aprovado pelo titular da conta ${runId}.`,
      ticketReference,
      requestedScopes: ['tasks:read', 'tasks:write'],
      durationMinutes: 15,
    },
  });
  expect(requested.status(), await requested.text()).toBe(201);
  const grant = await requested.json() as { id: string; state: string };
  expect(grant.state).toBe('requested');

  const approved = await request.post(
    `${API_URL}/api/v1/workspaces/support-access/requests/${grant.id}/approve`,
    {
      headers: bearer(seeded.tenantToken),
      data: { approvedScopes: ['tasks:read', 'tasks:write'], durationMinutes: 15 },
    },
  );
  expect(approved.status(), await approved.text()).toBe(200);
  expect((await approved.json() as { state: string }).state).toBe('approved');
  const administrativeSessionToken = await ensureAdministrativeSession(request, support.token);

  const context = await browser.newContext({ baseURL: BLACKBOX_URL });
  const page = await context.newPage();
  await authenticatePageForRole(page, request, 'support');
  await page.addInitScript((token) => {
    sessionStorage.setItem('klm_admin_session_token', token);
  }, administrativeSessionToken);
  await page.goto('/blackbox/support-access', { waitUntil: 'domcontentloaded' });
  await expect(page.getByText(seeded.task.title, { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Selecionar conta para acesso administrativo' }).click();
  const accountLookup = page.getByRole('dialog', { name: 'Selecionar conta para acesso administrativo' });
  await accountLookup.getByPlaceholder('Pesquisar conta').fill(seeded.workspace.name);
  await accountLookup.getByRole('option', { name: new RegExp(seeded.workspace.name) }).click();
  await expect(page.getByText('MFA confirmado', { exact: true })).toBeVisible();
  const grantCard = page.getByText(`Ticket ${ticketReference}`, { exact: true }).locator('xpath=ancestor::article');
  await expect(grantCard.getByRole('button', { name: 'Ativar com MFA' })).toBeEnabled();
  await grantCard.getByRole('button', { name: 'Ativar com MFA' }).click();
  const ready = page.getByTestId('support-access-ready');
  await expect(ready).toBeVisible();
  await ready.getByRole('button', { name: 'Entrar no workspace' }).click();

  await expect(page).toHaveURL(/\/blackbox\/support-session$/);
  await expect(page.getByText(`Modo suporte aprovado: ${seeded.workspace.name}`)).toBeVisible();
  await page.getByRole('button', { name: 'Tarefas', exact: true }).click();
  await expect(page.getByText('Conteúdo tenant autorizado nesta sessão')).toBeVisible();
  const drawer = await openSupportTaskDrawer(page, seeded.task);
  await drawer.getByRole('tab', { name: 'Equipe', exact: true }).click();
  await expect(drawer.getByText(seeded.ownerName, { exact: true })).toBeVisible();
  await expect(drawer.getByText(seeded.assigneeName, { exact: true })).toBeVisible();
  await expect(drawer.getByText(seeded.reviewerName, { exact: true })).toBeVisible();
  await drawer.getByRole('tab', { name: 'Revisão', exact: true }).click();
  await expect(drawer.getByText('A revisão está configurada, mas ainda não foi submetida.')).toBeVisible();
  await drawer.getByRole('tab', { name: 'Comentários', exact: true }).click();
  await expect(drawer.getByText(seeded.comment, { exact: true })).toBeVisible();
  await drawer.getByRole('tab', { name: 'Histórico', exact: true }).click();
  await expect(drawer.getByText('Tarefa criada', { exact: true })).toBeVisible();
  await expect(drawer.getByText('Comentário adicionado', { exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(drawer).toBeHidden();
  await page.getByRole('button', { name: 'Sair do modo suporte' }).click();
  await expect(page.getByText(seeded.task.title, { exact: true })).toHaveCount(0);
  await context.close();
});

test('platform_owner cria exportação assíncrona e observa progresso @operations', async ({ browser, request }) => {
  test.skip(process.env.KLM_E2E_OPERATIONS !== '1', 'Requer API, worker e storage local privados.');
  const context = await browser.newContext({ baseURL: process.env.KLM_BLACKBOX_URL ?? 'http://127.0.0.1:4174' });
  const page = await context.newPage();
  await authenticatePageForRole(page, request, 'platform_owner');
  await page.goto('/blackbox/exports');
  await expect(page.getByRole('heading', { name: 'Central de Downloads' })).toBeVisible();
  await page.getByRole('button', { name: /Gerar arquivo/i }).click();
  await expect(page.getByText(/Na fila|Gerando|Pronto/).first()).toBeVisible();
  await context.close();
});

test('platform_owner executa lote pela UI e prepara restauração @operations', async ({ browser, request }) => {
  test.skip(process.env.KLM_E2E_OPERATIONS !== '1', 'Requer API, worker e banco local descartável.');
  const context = await browser.newContext({ baseURL: process.env.KLM_BLACKBOX_URL ?? 'http://127.0.0.1:4174' });
  const page = await context.newPage();
  const token = await authenticatePageForRole(page, request, 'platform_owner');
  const runId = crypto.randomUUID().slice(0, 8);
  const workspaceName = `E2E lote restaurável ${runId}`;
  const created = await request.post(`${process.env.KLM_API_URL ?? 'http://127.0.0.1:8010'}/api/v1/platform/workspaces`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { name: workspaceName, setupMode: 'assisted' },
  });
  expect(created.status()).toBe(201);

  await page.goto('/blackbox/operations', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: 'Central de Operações em Massa' })).toBeVisible();
  await page.getByRole('button', { name: 'Selecionar Contas' }).click();
  await page.getByRole('textbox', { name: 'Pesquisar Selecionar Contas' }).fill(workspaceName);
  await page.getByRole('option', { name: new RegExp(workspaceName) }).click();
  await page.getByRole('button', { name: 'Concluir (1)' }).click();
  await page.getByRole('textbox', { name: 'Motivo operacional' }).fill('Validação E2E de lote com restauração contextual');
  await page.getByRole('button', { name: 'Gerar prévia' }).click();
  const preview = page.getByTestId('bulk-preview');
  await expect(preview.getByText('Elegíveis').locator('..').getByText('1')).toBeVisible();
  await preview.getByRole('textbox').fill('CONFIRMAR 1 SUSPEND');
  await preview.getByRole('button', { name: /Executar 1 alteração/ }).click();

  const activeJobCard = page.getByText('Job em acompanhamento', { exact: true }).locator('..').locator('..');
  await expect(activeJobCard.getByText(/1 sucesso\(s\)/)).toBeVisible({ timeout: 30_000 });
  await activeJobCard.getByRole('button', { name: 'Preparar restauração' }).click();
  const restore = page.getByRole('dialog', { name: 'Prévia de restauração' });
  await expect(restore.getByText(/1 compatível/)).toBeVisible();
  await context.close();
});

test('lote preserva sucesso parcial, exporta resultado e restaura somente o compatível @operations', async ({ browser, request }) => {
  test.skip(process.env.KLM_E2E_OPERATIONS !== '1', 'Requer API, worker e banco local descartável.');
  const apiUrl = process.env.KLM_API_URL ?? 'http://127.0.0.1:8010';
  const context = await browser.newContext({ baseURL: process.env.KLM_BLACKBOX_URL ?? 'http://127.0.0.1:4174' });
  const page = await context.newPage();
  const token = await authenticatePageForRole(page, request, 'platform_owner');
  const headers = { Authorization: `Bearer ${token}` };
  const runId = crypto.randomUUID().slice(0, 8);
  const names = [`E2E lote parcial A ${runId}`, `E2E lote parcial B ${runId}`];
  const workspaces = [] as Array<{ id: string; name: string }>;
  for (const name of names) {
    const created = await request.post(`${apiUrl}/api/v1/platform/workspaces`, {
      headers,
      data: { name, setupMode: 'assisted' },
    });
    expect(created.status()).toBe(201);
    workspaces.push(await created.json() as { id: string; name: string });
  }

  await page.goto('/blackbox/operations', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Selecionar Contas' }).click();
  const search = page.getByRole('textbox', { name: 'Pesquisar Selecionar Contas' });
  for (const workspace of workspaces) {
    await search.fill(workspace.name);
    await page.getByRole('option', { name: new RegExp(workspace.name) }).click();
  }
  await page.getByRole('button', { name: 'Concluir (2)' }).click();
  await page.getByRole('textbox', { name: 'Motivo operacional' }).fill('Validação E2E de sucesso parcial e compensação segura');
  await page.getByRole('button', { name: 'Gerar prévia' }).click();
  const preview = page.getByTestId('bulk-preview');
  await expect(preview.getByText('Elegíveis').locator('..').getByText('2')).toBeVisible();

  // A seleção e a prévia permanecem congeladas. Esta mutação concorrente deve
  // transformar somente o segundo item em conflito, sem desfazer o primeiro.
  const concurrent = await request.patch(`${apiUrl}/api/v1/platform/workspaces/${workspaces[1].id}`, {
    headers,
    data: { onboardingCompleted: true, reason: `Concorrência E2E ${runId}` },
  });
  expect(concurrent.status()).toBe(200);

  await preview.getByRole('textbox').fill('CONFIRMAR 2 SUSPEND');
  await preview.getByRole('button', { name: /Executar 2 alteração/ }).click();
  const activeJobCard = page.getByText('Job em acompanhamento', { exact: true }).locator('..').locator('..');
  await expect(activeJobCard.getByText('partial', { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(activeJobCard.getByText(/1 sucesso\(s\), 0 falha\(s\), 1 conflito\(s\), 0 cancelado\(s\)/)).toBeVisible();

  await activeJobCard.getByRole('button', { name: 'Exportar resultados' }).click();
  await expect(activeJobCard.getByText('Exportação criada')).toBeVisible();
  await activeJobCard.getByRole('button', { name: 'Preparar restauração' }).click();
  const restore = page.getByRole('dialog', { name: 'Prévia de restauração' });
  await expect(restore.getByText(/1 compatível/)).toBeVisible();
  await context.close();
});

test('lote enfileirado cancela com segurança e de forma idempotente @cancellation', async ({ browser, request }) => {
  test.skip(
    process.env.KLM_E2E_CANCELLATION !== '1',
    'Requer worker pausado para provar deterministicamente o estado queued.',
  );
  const apiUrl = process.env.KLM_API_URL ?? 'http://127.0.0.1:8010';
  const context = await browser.newContext({ baseURL: process.env.KLM_BLACKBOX_URL ?? 'http://127.0.0.1:4174' });
  const page = await context.newPage();
  const token = await authenticatePageForRole(page, request, 'platform_owner');
  const headers = { Authorization: `Bearer ${token}` };
  const runId = crypto.randomUUID().slice(0, 8);
  const workspaceName = `E2E cancelamento seguro ${runId}`;
  const created = await request.post(`${apiUrl}/api/v1/platform/workspaces`, {
    headers,
    data: { name: workspaceName, setupMode: 'assisted' },
  });
  expect(created.status()).toBe(201);

  await page.route('**/api/v1/platform/operations/bulk/jobs', async (route) => {
    await route.continue({
      headers: {
        ...route.request().headers(),
        'X-KLM-Local-Dispatch-Delay-Ms': '3000',
      },
    });
  });

  await page.goto('/blackbox/operations', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Selecionar Contas' }).click();
  await page.getByRole('textbox', { name: 'Pesquisar Selecionar Contas' }).fill(workspaceName);
  await page.getByRole('option', { name: new RegExp(workspaceName) }).click();
  await page.getByRole('button', { name: 'Concluir (1)' }).click();
  await page.getByRole('textbox', { name: 'Motivo operacional' }).fill('Validação E2E de cancelamento cooperativo em fila');
  await page.getByRole('button', { name: 'Gerar prévia' }).click();
  const preview = page.getByTestId('bulk-preview');
  await preview.getByRole('textbox').fill('CONFIRMAR 1 SUSPEND');
  const jobResponsePromise = page.waitForResponse((response) => (
    response.request().method() === 'POST'
    && response.url().endsWith('/api/v1/platform/operations/bulk/jobs')
  ));
  await preview.getByRole('button', { name: /Executar 1 alteração/ }).click();
  const jobResponse = await jobResponsePromise;
  expect(jobResponse.status()).toBe(202);
  const job = await jobResponse.json() as { id: string };

  await expect(page.getByText('queued', { exact: true }).first()).toBeVisible();
  await page.getByRole('button', { name: 'Cancelar com segurança' }).click();
  await expect(page.getByText('cancelled', { exact: true }).first()).toBeVisible();
  const repeated = await request.post(
    `${apiUrl}/api/v1/platform/operations/bulk/jobs/${job.id}/cancel`,
    { headers },
  );
  expect(repeated.status()).toBe(202);
  expect((await repeated.json() as { status: string }).status).toBe('cancelled');
  await context.close();
});

test('paginação e seleção global preservam snapshot em mais de 250 contas @pagination', async ({ browser, request }) => {
  test.skip(
    process.env.KLM_E2E_PAGINATION !== '1',
    'Cria 251 contas locais isoladas por runId e requer worker para a prévia assíncrona.',
  );
  test.setTimeout(240_000);
  const apiUrl = process.env.KLM_API_URL ?? 'http://127.0.0.1:8010';
  const context = await browser.newContext({ baseURL: process.env.KLM_BLACKBOX_URL ?? 'http://127.0.0.1:4174' });
  const page = await context.newPage();
  const token = await authenticatePageForRole(page, request, 'platform_owner');
  const headers = { Authorization: `Bearer ${token}` };
  const runId = process.env.KLM_E2E_RUN_ID ?? crypto.randomUUID().slice(0, 8);
  const prefix = `E2E paginação ${runId}`;

  const existingResponse = await request.get(`${apiUrl}/api/v1/platform/workspaces/page`, {
    headers,
    params: { limit: 1, search: prefix },
  });
  expect(existingResponse.status()).toBe(200);
  const existing = await existingResponse.json() as { total: number };
  const seededTotal = Math.max(existing.total, 251);
  const selectedTotal = seededTotal - 2;
  for (let index = existing.total; index < seededTotal; index += 1) {
    const created = await request.post(`${apiUrl}/api/v1/platform/workspaces`, {
      headers,
      data: { name: `${prefix} ${String(index).padStart(3, '0')}`, setupMode: 'assisted' },
    });
    expect(created.status(), `falha ao criar conta ${index}`).toBe(201);
  }

  await page.goto(`/blackbox/workspaces?q=${encodeURIComponent(prefix)}&pageSize=100`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByText(`Exibindo 1–100 de ${seededTotal}`)).toBeVisible({ timeout: 30_000 });
  await page.getByRole('button', { name: 'Próxima', exact: true }).click();
  await expect(page).toHaveURL(/page=2/);
  await expect(page.getByText(`Exibindo 101–200 de ${seededTotal}`)).toBeVisible();
  await page.getByRole('button', { name: 'Anterior', exact: true }).click();
  await expect(page.getByText(`Exibindo 1–100 de ${seededTotal}`)).toBeVisible();

  await page.getByRole('button', { name: `Selecionar todos os ${seededTotal} resultados` }).click();
  const checkboxes = page.getByRole('checkbox', { name: new RegExp(`^Selecionar ${prefix}`) });
  await checkboxes.nth(0).uncheck();
  await checkboxes.nth(1).uncheck();
  await expect(page.getByText(new RegExp(`${selectedTotal} conta\\(s\\) em uma seleção global congelada por filtro`))).toBeVisible();
  await page.getByPlaceholder('Explique o motivo operacional (mínimo de 8 caracteres).').fill('Validação E2E de paginação e snapshot global');
  await page.getByRole('button', { name: 'Gerar prévia e validar impacto' }).click();
  const preview = page.getByTestId('bulk-preview');
  await expect(preview.getByText('Alvos').locator('..').getByText(String(selectedTotal))).toBeVisible({ timeout: 60_000 });

  const lateWorkspace = await request.post(`${apiUrl}/api/v1/platform/workspaces`, {
    headers,
    data: { name: `${prefix} posterior ${crypto.randomUUID().slice(0, 8)}`, setupMode: 'assisted' },
  });
  expect(lateWorkspace.status()).toBe(201);
  await expect(preview.getByText('Alvos').locator('..').getByText(String(selectedTotal))).toBeVisible();

  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('textbox', { name: 'Buscar contas' }).fill(`${prefix} novo filtro`);
  await expect(page.getByText(/0 conta\(s\) em uma seleção explícita/)).toBeVisible();
  await expect(page).toHaveURL(/q=E2E\+pagina%C3%A7%C3%A3o/);
  await context.close();
});

test('Audit Center exibe diff semântico e restaura uma conta com auditoria @operations', async ({ browser, request }) => {
  test.skip(process.env.KLM_E2E_OPERATIONS !== '1', 'Requer API, worker e banco local descartável.');
  const apiUrl = process.env.KLM_API_URL ?? 'http://127.0.0.1:8010';
  const context = await browser.newContext({ baseURL: process.env.KLM_BLACKBOX_URL ?? 'http://127.0.0.1:4174' });
  const page = await context.newPage();
  const token = await authenticatePageForRole(page, request, 'platform_owner');
  const headers = { Authorization: `Bearer ${token}` };
  const runId = crypto.randomUUID().slice(0, 8);
  const workspaceName = `E2E diff auditável ${runId}`;
  const reason = `E2E alteração contextual ${runId}`;
  const restoreReason = `E2E restauração contextual ${runId}`;
  const created = await request.post(`${apiUrl}/api/v1/platform/workspaces`, {
    headers,
    data: { name: workspaceName, setupMode: 'assisted' },
  });
  expect(created.status()).toBe(201);
  const workspace = await created.json() as { id: string };
  const updated = await request.patch(`${apiUrl}/api/v1/platform/workspaces/${workspace.id}`, {
    headers,
    data: { onboardingCompleted: true, reason },
  });
  expect(updated.status()).toBe(200);

  await page.goto('/blackbox/governance/audit', { waitUntil: 'domcontentloaded' });
  await page.getByRole('textbox', { name: 'Buscar eventos de auditoria' }).fill(reason);
  await expect(page.getByText(reason)).toBeVisible();
  await page.getByText(/campo\(s\) alterado\(s\)/).click();
  await expect(page.getByText('Onboarding concluído', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Prévia de restauração' }).click();

  const restore = page.getByRole('dialog', { name: 'Prévia de restauração' });
  await expect(restore).toBeVisible();
  await expect(restore.getByText('Onboarding concluído', { exact: true })).toBeVisible();
  await restore.getByLabel('Motivo').fill(restoreReason);
  await restore.getByLabel('Confirmação').fill(`RESTAURAR ${workspace.id}`);
  await restore.getByRole('button', { name: 'Criar restauração' }).click();
  await expect(restore.getByText('Job: succeeded')).toBeVisible({ timeout: 30_000 });

  await expect.poll(async () => {
    const audit = await request.get(`${apiUrl}/api/v1/platform/audit-events`, {
      headers,
      params: { search: restoreReason },
    });
    if (audit.status() !== 200) return null;
    const payload = await audit.json() as { items: Array<{ action: string; restorationOfEventId: string | null }> };
    return payload.items.find((event) => event.action === 'restored') ?? null;
  }, { timeout: 30_000 }).not.toBeNull();
  await context.close();
});
