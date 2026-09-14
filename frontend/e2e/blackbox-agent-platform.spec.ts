import { expect, test, type APIRequestContext } from '@playwright/test';

import { authenticatePageForRole, sessionForRole } from './fixtures';

const API_URL = process.env.KLM_API_URL ?? 'http://127.0.0.1:8010';

function bearer(token: string) {
  return { Authorization: `Bearer ${token}` };
}

async function ensureAdministrativeSession(request: APIRequestContext, token: string) {
  const headers = bearer(token);
  const status = await request.get(`${API_URL}/api/v1/auth/mfa`, { headers });
  expect(status.status(), await status.text()).toBe(200);
  const mfa = await status.json() as { enabled: boolean };
  if (!mfa.enabled) {
    const enrollmentResponse = await request.post(`${API_URL}/api/v1/auth/mfa/enroll`, { headers });
    expect(enrollmentResponse.status(), await enrollmentResponse.text()).toBe(200);
    const enrollment = await enrollmentResponse.json() as { challengeId: string; simulatedCode: string };
    const verified = await request.post(`${API_URL}/api/v1/auth/mfa/enroll/${enrollment.challengeId}/verify`, {
      headers,
      data: { code: enrollment.simulatedCode },
    });
    expect(verified.status(), await verified.text()).toBe(200);
  }
  const challengeResponse = await request.post(`${API_URL}/api/v1/auth/admin-sessions/challenge`, { headers });
  expect(challengeResponse.status(), await challengeResponse.text()).toBe(200);
  const challenge = await challengeResponse.json() as { challengeId: string; simulatedCode: string };
  const verification = await request.post(`${API_URL}/api/v1/auth/admin-sessions/challenge/${challenge.challengeId}/verify`, {
    headers,
    data: { code: challenge.simulatedCode },
  });
  expect(verification.status(), await verification.text()).toBe(200);
  return (await verification.json() as { administrativeSessionToken: string }).administrativeSessionToken;
}

type AgentRunListItem = { id: string; status: string; attemptCount?: number };

async function findRun(
  request: APIRequestContext,
  token: string,
  workspaceId: string,
  runId: string,
): Promise<AgentRunListItem | null | `HTTP-${number}`> {
  let offset = 0;
  let hasMore = true;
  while (hasMore) {
    const response = await request.get(`${API_URL}/api/v1/agent-platform/runs?workspaceId=${workspaceId}&limit=100&offset=${offset}`, {
      headers: bearer(token),
    });
    if (response.status() !== 200) return `HTTP-${response.status()}`;
    const body = await response.json() as { items: AgentRunListItem[]; hasMore: boolean };
    const match = body.items.find((item) => item.id === runId);
    if (match) return match;
    hasMore = body.hasMore;
    offset += body.items.length;
    if (body.items.length === 0) break;
  }
  return null;
}

async function waitForRun(
  request: APIRequestContext,
  token: string,
  workspaceId: string,
  runId: string,
  statuses: string[],
  options: { minAttemptCount?: number } = {},
) {
  const accepted = new RegExp(`^(${statuses.join('|')})$`);
  const minAttemptCount = options.minAttemptCount ?? 0;
  // O valor devolvido pelo poll é o estado real da última leitura: em caso de
  // falha, a mensagem do Playwright mostra o que foi observado, não um texto
  // fixo avaliado antes do poll começar.
  await expect.poll(async () => {
    const found = await findRun(request, token, workspaceId, runId);
    if (found === null) return 'missing';
    if (typeof found === 'string') return found;
    if ((found.attemptCount ?? 0) < minAttemptCount) {
      return `${found.status} (tentativa ${found.attemptCount ?? 0}, esperada >= ${minAttemptCount})`;
    }
    return found.status;
  }, {
    message: `execução ${runId} deveria alcançar ${statuses.join(', ')}${minAttemptCount ? ` a partir da tentativa ${minAttemptCount}` : ''}`,
    timeout: 90_000,
    intervals: [250, 500, 1_000, 2_000],
  }).toMatch(accepted);
}

test('root_direct opera deployment e checkpoint humano sem abrir o manifesto geral @operations', async ({ page, request }) => {
  test.setTimeout(300_000);
  const tenant = await request.post(`${API_URL}/api/v1/klm/local/session`);
  expect(tenant.status(), await tenant.text()).toBe(200);
  const tenantBody = await tenant.json() as { accessToken: string; session: { activeWorkspace: { id: string; name: string } } };
  const workspaceId = tenantBody.session.activeWorkspace.id;
  const workspaceName = tenantBody.session.activeWorkspace.name;
  const root = await sessionForRole(request, 'platform_owner');
  const administrativeSessionToken = await ensureAdministrativeSession(request, root.token);

  await authenticatePageForRole(page, request, 'platform_owner');
  await page.addInitScript((token) => sessionStorage.setItem('klm_admin_session_token', token), administrativeSessionToken);
  await page.goto('/blackbox', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('button', { name: 'Abrir busca global' })).toBeVisible();
  await page.keyboard.press('Control+K');
  const command = page.getByRole('dialog', { name: 'Busca global e comandos' });
  await command.getByRole('textbox', { name: 'Pesquisar no Blackbox' }).fill(workspaceName);
  const accountRow = command.getByText(workspaceName, { exact: true }).first().locator('..').locator('..');
  await accountRow.getByRole('button', { name: /Entrar como Super Admin/ }).click();
  const confirmation = page.getByRole('alertdialog', { name: 'Entrar como Super Admin' });
  await confirmation.getByRole('textbox', { name: 'Motivo do comando' }).fill('Operação E2E governada do runtime de agentes.');
  await confirmation.getByRole('button', { name: 'Confirmar ação' }).click();
  await expect(page).toHaveURL(/\/blackbox\/support-session$/);
  await expect(page.locator('[data-support-session-type]')).toHaveAttribute('data-support-session-type', 'root_direct');
  await expect(page.locator('[data-support-session-type]')).toHaveAttribute('data-can-view-agents', 'true');
  const catalogLoaded = page.waitForResponse((response) => response.url().includes('/api/v1/agent-platform/catalog?workspaceId='));
  const agentSurface = page.locator('[data-support-surface="agent-platform"]');
  await expect(agentSurface).toBeAttached();
  await agentSurface.scrollIntoViewIfNeeded();
  await agentSurface.click();
  await expect(page.getByTestId('agent-platform-operations')).toBeVisible();
  const catalogBrowserResponse = await catalogLoaded;
  expect(catalogBrowserResponse.status(), await catalogBrowserResponse.text()).toBe(200);
  const supportToken = (await catalogBrowserResponse.request().allHeaders()).authorization?.replace(/^Bearer\s+/i, '');
  expect(supportToken).toBeTruthy();

  const catalog = await catalogBrowserResponse.json() as { agents: Array<{ key: string }> };
  const deploymentsResponse = await request.get(`${API_URL}/api/v1/agent-platform/deployments?workspaceId=${workspaceId}&limit=100&offset=0`, {
    headers: bearer(supportToken as string),
  });
  expect(deploymentsResponse.status(), await deploymentsResponse.text()).toBe(200);
  const deployments = await deploymentsResponse.json() as { items: Array<{ agentKey?: string; agent_key?: string }> };
  const usedKeys = new Set(deployments.items.map((item) => item.agentKey ?? item.agent_key));
  const agentKey = catalog.agents.find((item) => !usedKeys.has(item.key))?.key;
  expect(agentKey, 'o banco descartável precisa ter ao menos uma definição ainda sem deployment').toBeTruthy();

  const forbiddenManifest = await request.get(`${API_URL}/api/v1/platform/session-manifest`, {
    headers: bearer(supportToken as string),
  });
  expect(forbiddenManifest.status()).toBe(403);

  const deploymentForm = page.getByRole('form', { name: 'Novo deployment governado' });
  await deploymentForm.getByRole('combobox', { name: 'Agente do deployment' }).selectOption(agentKey as string);
  await deploymentForm.getByRole('group', { name: 'Ferramentas autorizadas' }).getByRole('checkbox').first().check();
  await deploymentForm.getByText('Fontes de conhecimento').locator('..').getByRole('checkbox').first().check();
  await deploymentForm.getByRole('spinbutton', { name: 'Orçamento mensal de tokens' }).fill('1');
  const deploymentCreated = page.waitForResponse((response) => response.url().includes('/api/v1/agent-platform/deployments?workspaceId=') && response.request().method() === 'POST');
  await deploymentForm.getByRole('button', { name: 'Criar deployment' }).click();
  const createdResponse = await deploymentCreated;
  expect(createdResponse.status(), await createdResponse.text()).toBe(200);
  const deploymentId = (await createdResponse.json() as { id: string }).id;

  const deploymentSection = page.getByRole('region', { name: 'Deployments dos agentes' });
  const deploymentCard = deploymentSection.locator('article').filter({ hasText: agentKey as string });
  await deploymentCard.getByRole('button', { name: 'Validar gates' }).click();
  await expect(deploymentCard.getByText(/Todos os 11 gates foram calculados/)).toBeVisible();
  await deploymentCard.getByRole('button', { name: 'Publicar' }).click();
  await expect(deploymentCard.getByText('active', { exact: true })).toBeVisible();

  const budgetSeedRunId = `agent-budget-${crypto.randomUUID().slice(0, 24)}`;
  const budgetSeed = await request.post(`${API_URL}/api/v1/klm/local/agent-budget-e2e-seed`, {
    headers: {
      ...bearer(tenantBody.accessToken),
      'X-KLM-Local-Seed-Intent': budgetSeedRunId,
    },
    data: { deploymentId, runId: budgetSeedRunId },
  });
  expect(budgetSeed.status(), await budgetSeed.text()).toBe(200);

  const runForm = page.getByRole('form', { name: 'Nova execução contextual do agente' });
  await runForm.getByRole('combobox', { name: 'Agente ativo para execução' }).selectOption(agentKey as string);
  const firstRunResponsePromise = page.waitForResponse((response) => response.url().includes('/api/v1/agent-platform/runs/context?workspaceId=') && response.request().method() === 'POST');
  await runForm.getByRole('button', { name: 'Criar execução governada' }).click();
  const firstRunResponse = await firstRunResponsePromise;
  expect(firstRunResponse.status(), await firstRunResponse.text()).toBe(200);
  const firstRunId = (await firstRunResponse.json() as { id: string }).id;
  await waitForRun(request, supportToken as string, workspaceId, firstRunId, ['waiting_human']);

  const calls = page.getByRole('region', { name: 'Intervenções humanas pendentes' });
  const call = calls.locator(`button[data-agent-run-id="${firstRunId}"]`);
  await expect(call).toContainText('Sistema:');
  await expect(call).toContainText('Telefone:');
  await expect(call).not.toContainText('Dono acompanhando: indisponível');
  await call.click();
  await page.getByRole('button', { name: 'Reconhecer chamada' }).click();
  await expect(page.getByText('Chamada reconhecida.', { exact: true })).toBeVisible();
  const beforeApproval = await findRun(request, supportToken as string, workspaceId, firstRunId);
  expect(beforeApproval, 'a execução precisa constar na listagem antes da aprovação').not.toBeNull();
  expect(typeof beforeApproval, 'a listagem de execuções precisa responder 200 antes da aprovação').toBe('object');
  const attemptBeforeApproval = (beforeApproval as AgentRunListItem).attemptCount ?? 0;
  const resolvedDecisionPromise = page.waitForResponse((response) => response.url().includes('/api/v1/agent-platform/decisions/') && response.url().includes('/resolve') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Aprovar e retomar' }).click();
  const resolvedDecision = await resolvedDecisionPromise;
  expect(resolvedDecision.status(), await resolvedDecision.text()).toBe(200);
  // A aprovação libera a retomada durável; o endpoint não executa o agente
  // inline. Com worker no harness (como no CI) a execução sai de `queued` em
  // centenas de milissegundos, então o contrato observável é a tentativa
  // seguinte da mesma execução, em qualquer estado posterior à fila. A
  // conclusão assíncrona é coberta na suíte do worker.
  await waitForRun(
    request,
    supportToken as string,
    workspaceId,
    firstRunId,
    ['queued', 'running', 'waiting_human', 'completed', 'failed'],
    { minAttemptCount: attemptBeforeApproval + 1 },
  );

  const thirdRunResponsePromise = page.waitForResponse((response) => response.url().includes('/api/v1/agent-platform/runs/context?workspaceId=') && response.request().method() === 'POST');
  await runForm.getByRole('button', { name: 'Criar execução governada' }).click();
  const thirdRunResponse = await thirdRunResponsePromise;
  expect(thirdRunResponse.status(), await thirdRunResponse.text()).toBe(200);
  const thirdRun = await thirdRunResponse.json() as { id: string; status: string };
  expect(['queued', 'waiting_human']).toContain(thirdRun.status);
  await waitForRun(request, supportToken as string, workspaceId, thirdRun.id, ['waiting_human']);
  const newestRun = page.getByRole('region', { name: 'Execuções recentes dos agentes' }).locator(`article[data-agent-run-id="${thirdRun.id}"]`);
  await newestRun.getByRole('button', { name: 'Cancelar' }).click();
  await expect(newestRun.getByText('cancelled', { exact: true })).toBeVisible();
  const beforeRequeue = await findRun(request, supportToken as string, workspaceId, thirdRun.id);
  expect(beforeRequeue, 'a execução cancelada precisa constar na listagem antes de reenfileirar').not.toBeNull();
  expect(typeof beforeRequeue, 'a listagem de execuções precisa responder 200 antes de reenfileirar').toBe('object');
  const attemptBeforeRequeue = (beforeRequeue as AgentRunListItem).attemptCount ?? 0;
  await newestRun.getByRole('button', { name: 'Reenfileirar' }).click();
  // Reenfileirar abre a tentativa seguinte e devolve a execução ao worker; com
  // provedor fake ela pode terminar em `failed` antes do poll, o que ainda
  // prova a saída de `cancelled` pela tentativa nova.
  await waitForRun(
    request,
    supportToken as string,
    workspaceId,
    thirdRun.id,
    ['queued', 'running', 'waiting_human', 'completed', 'failed'],
    { minAttemptCount: attemptBeforeRequeue + 1 },
  );

  const finalDeployment = await request.get(`${API_URL}/api/v1/agent-platform/deployments?workspaceId=${workspaceId}&limit=100&offset=0`, {
    headers: bearer(supportToken as string),
  });
  const finalItems = (await finalDeployment.json() as { items: Array<{ id: string; status: string }> }).items;
  expect(finalItems.find((item) => item.id === deploymentId)?.status).toBe('active');
});
