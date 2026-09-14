import { randomUUID } from 'node:crypto';

import { expect, test } from './fixtures';

const API_URL = process.env.KLM_API_URL ?? 'http://127.0.0.1:8010';

type FlowRead = {
  id: string;
  is_active: boolean;
  current_revision: number;
};

function flowPayload(name: string, cyclic = false) {
  const nodes = [
    {
      id: 'trigger-1',
      type: 'trigger',
      position: { x: 100, y: 80 },
      data: { label: 'Entrada manual', event: 'manual', trigger_type: 'manual' },
    },
    {
      id: 'agent-1',
      type: 'agent',
      position: { x: 100, y: 260 },
      data: { label: 'Análise Hermes', agent: 'hermes-triagem' },
    },
    ...(cyclic ? [{
      id: 'agent-2',
      type: 'agent',
      position: { x: 360, y: 260 },
      data: { label: 'Revisão Hermes', agent: 'hermes-triagem' },
    }] : []),
  ];
  const edges = [
    { id: 'edge-forward', source: 'trigger-1', target: 'agent-1' },
    ...(cyclic ? [
      { id: 'edge-cycle-forward', source: 'agent-1', target: 'agent-2' },
      { id: 'edge-cycle-return', source: 'agent-2', target: 'agent-1' },
    ] : []),
  ];
  return {
    name,
    description: 'Flow descartável para provar os gates V3 no browser.',
    practice_area: 'universal',
    trigger: 'manual',
    trigger_config: { localOnly: true },
    nodes,
    edges,
    is_active: false,
  };
}

test('FlowBuilder usa validate → simulate e bloqueia publicação reprovada pelo runtime @desktop-only', async ({ page, request }) => {
  test.setTimeout(60_000);
  const sessionResponse = await request.post(`${API_URL}/api/v1/klm/local/session`);
  expect(sessionResponse.status(), await sessionResponse.text()).toBe(200);
  const session = await sessionResponse.json() as { accessToken?: string; access_token?: string };
  const token = session.accessToken ?? session.access_token;
  expect(token).toBeTruthy();
  const headers = { Authorization: `Bearer ${token}` };
  const suffix = randomUUID().slice(0, 8);

  const validResponse = await request.post(`${API_URL}/api/v1/flows`, {
    headers,
    data: flowPayload(`QA Flow V3 válido ${suffix}`),
  });
  expect(validResponse.status(), await validResponse.text()).toBe(201);
  const validFlow = await validResponse.json() as FlowRead;

  const invalidResponse = await request.post(`${API_URL}/api/v1/flows`, {
    headers,
    data: flowPayload(`QA Flow V3 cíclico ${suffix}`, true),
  });
  expect(invalidResponse.status(), await invalidResponse.text()).toBe(201);
  const invalidFlow = await invalidResponse.json() as FlowRead;

  try {
    const runtimeCalls: string[] = [];
    page.on('request', (outgoing) => {
      const path = new URL(outgoing.url()).pathname;
      if (outgoing.method() === 'POST' && ['/api/v1/flows/validate', '/api/v1/flows/simulate'].includes(path)) {
        runtimeCalls.push(path);
      }
    });

    await page.goto(`/automacoes/editor/${validFlow.id}`, { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.react-flow')).toBeVisible();

    const validateResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/flows/validate');
    await page.getByRole('button', { name: 'Compilar', exact: true }).click();
    const validateResponse = await validateResponsePromise;
    expect(validateResponse.status()).toBe(200);
    expect((await validateResponse.json() as { valid: boolean }).valid).toBe(true);
    await expect(page.getByRole('button', { name: 'Runtime validado' })).toBeVisible();

    const simulateResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/flows/simulate');
    await page.getByRole('button', { name: 'Simular custo', exact: true }).click();
    const simulateResponse = await simulateResponsePromise;
    expect(simulateResponse.status()).toBe(200);
    expect((await simulateResponse.json() as { valid: boolean }).valid).toBe(true);
    await page.getByRole('button', { name: 'Runtime validado' }).click();
    await expect(page.getByText(/Simulação: 2\/2 nós alcançáveis/)).toBeVisible();
    expect(runtimeCalls.slice(0, 2)).toEqual(['/api/v1/flows/validate', '/api/v1/flows/simulate']);

    runtimeCalls.length = 0;
    await page.goto(`/automacoes/editor/${invalidFlow.id}`, { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.react-flow')).toBeVisible();
    const blockedCompilePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/flows/validate');
    await page.getByRole('button', { name: 'Compilar', exact: true }).click();
    const blockedCompile = await blockedCompilePromise;
    expect(blockedCompile.status()).toBe(200);
    const blockedBody = await blockedCompile.json() as { valid: boolean; issues: Array<{ code: string }> };
    expect(blockedBody.valid).toBe(false);
    expect(blockedBody.issues.map((issue) => issue.code)).toContain('unbounded_cycle');
    await expect(page.getByRole('button', { name: 'Runtime bloqueou' })).toBeVisible();

    let updateRequests = 0;
    page.on('request', (outgoing) => {
      if (outgoing.method() === 'PUT' && new URL(outgoing.url()).pathname === `/api/v1/flows/${invalidFlow.id}`) {
        updateRequests += 1;
      }
    });
    const activationValidationPromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/flows/validate');
    await page.getByRole('button', { name: 'Ativar', exact: true }).click();
    const activationValidation = await activationValidationPromise;
    expect((await activationValidation.json() as { valid: boolean }).valid).toBe(false);
    await expect(page.getByRole('heading', { name: 'Validação do fluxo' })).toBeVisible();
    await expect(page.getByText(/Bloqueio · unbounded_cycle/)).toBeVisible();
    await page.waitForTimeout(300);
    expect(updateRequests).toBe(0);

    const persistedResponse = await request.get(`${API_URL}/api/v1/flows/${invalidFlow.id}`, { headers });
    expect(persistedResponse.status(), await persistedResponse.text()).toBe(200);
    expect((await persistedResponse.json() as FlowRead).is_active).toBe(false);
  } finally {
    for (const flowId of [validFlow.id, invalidFlow.id]) {
      const cleanup = await request.delete(`${API_URL}/api/v1/flows/${flowId}`, { headers });
      expect(cleanup.status(), await cleanup.text()).toBe(204);
    }
  }
});
