import { authenticateWorkspacePage, expect, test, type APIRequestContext, type Page } from './fixtures';
import { nativeDisplayText } from '../src/klm/native-display';

const API_URL = process.env.KLM_API_URL ?? 'http://127.0.0.1:8010';

type NativeDomain = 'communications' | 'collaboration' | 'automation' | 'crm';

type NativeCapability = {
  id: string;
  domain: NativeDomain;
  key: string;
  label: string;
  nativeRoute: string;
  disposition: string;
  verified: boolean;
  externalEffectVerified: boolean;
};

function canonicalRoute(capability: NativeCapability) {
  return `/app/capacidades/${capability.domain}/${capability.key}`;
}

async function nativeRegistry(request: APIRequestContext): Promise<{
  capabilities: NativeCapability[];
  verified: boolean;
}> {
  const requireVerifiedRelease = process.env.KLM_REQUIRE_VERIFIED_OSS === '1';
  const sessionResponse = await request.post(`${API_URL}/api/v1/klm/local/session`);
  expect(sessionResponse.status()).toBe(200);
  const session = await sessionResponse.json();
  const token = session.accessToken ?? session.access_token;
  const response = await request.get(`${API_URL}/api/v1/capabilities`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(response.status()).toBe(200);
  const registry = await response.json();
  expect(registry.summary).toMatchObject({
    total: 135,
    available: 123,
    externalEffectVerified: 0,
    byDomain: {
      communications: 39,
      collaboration: 33,
      automation: 27,
      crm: 36,
    },
  });
  expect(['unverified', 'verified']).toContain(registry.verification.state);
  const verified = registry.verification.state === 'verified';
  const verifiedCapabilities = registry.capabilities.filter(
    (capability: NativeCapability) => capability.verified,
  );
  expect(registry.summary.verified).toBe(verifiedCapabilities.length);
  if (requireVerifiedRelease) {
    expect(registry.verification).toMatchObject({
      state: 'verified',
      checks: 5,
    });
    expect(registry.summary).toMatchObject({
      verified: 135,
      externalEffectVerified: 0,
    });
  }
  expect(verifiedCapabilities).toHaveLength(verified ? 135 : 0);
  return { capabilities: registry.capabilities, verified };
}

function runtimeFailures(page: Page): string[] {
  const failures: string[] = [];
  page.on('pageerror', (error) => failures.push(`pageerror: ${error.message}`));
  page.on('response', (response) => {
    if (response.status() >= 500) failures.push(`http ${response.status()}: ${response.url()}`);
  });
  return failures;
}

test('todas as 135 capacidades abrem e expõem seus comandos na experiência nativa', { tag: '@desktop-only' }, async ({ page, request }, testInfo) => {
  test.setTimeout(900_000);
  await authenticateWorkspacePage(page, request);
  const { capabilities, verified } = await nativeRegistry(request);
  const failures = runtimeFailures(page);
  const timings: Array<{ id: string; elapsedMs: number }> = [];
  let exposedActions = 0;

  expect(capabilities).toHaveLength(135);
  for (const capability of capabilities) {
    expect(capability.verified).toBe(verified);
    expect(capability.externalEffectVerified).toBe(false);
    expect(['communications', 'collaboration', 'automation', 'crm']).toContain(capability.domain);
    expect(capability.nativeRoute).toBe(canonicalRoute(capability));

    const startedAt = Date.now();
    const route = canonicalRoute(capability);
    const response = await page.goto(route, { waitUntil: 'domcontentloaded' });
    expect(response?.status(), route).toBeLessThan(400);
    const workspace = page.getByTestId('native-capability-workspace');
    await expect(workspace, capability.nativeRoute).toBeVisible({ timeout: 15_000 });
    await expect(workspace.getByRole('heading', { level: 1, name: nativeDisplayText(capability.label), exact: true })).toBeVisible();
    await expect(workspace.getByText('Fonte:', { exact: true })).toBeVisible();
    await expect(workspace.locator('iframe')).toHaveCount(0);

    const actionRegion = workspace.getByRole('region', { name: 'Ações da capacidade' });
    await expect(actionRegion, `${capability.nativeRoute} sem ações`).toBeVisible();
    const actionButtons = actionRegion.getByRole('button');
    const actionCount = await actionButtons.count();
    expect(actionCount, `${capability.nativeRoute} sem comandos anunciados`).toBeGreaterThan(0);
    exposedActions += actionCount;
    for (let actionIndex = 0; actionIndex < actionCount; actionIndex += 1) {
      const action = actionButtons.nth(actionIndex);
      await expect(action).toBeVisible();
      if (await action.isEnabled()) {
        await action.click();
        const dialog = page.getByRole('dialog');
        await expect(dialog).toBeVisible();
        await expect(dialog.getByRole('button', { name: 'Cancelar' })).toBeVisible();
        await dialog.getByRole('button', { name: 'Cancelar' }).click();
        await expect(dialog).not.toBeVisible();
      } else {
        await expect(action).toHaveAttribute('title', /.+/);
      }
    }

    const geometry = await page.evaluate(() => ({
      viewport: window.innerWidth,
      document: document.documentElement.scrollWidth,
      body: document.body.scrollWidth,
    }));
    expect(
      Math.max(geometry.document, geometry.body) - geometry.viewport,
      `${capability.nativeRoute} possui overflow horizontal`,
    ).toBeLessThanOrEqual(1);
    timings.push({ id: capability.id, elapsedMs: Date.now() - startedAt });
  }

  const ordered = timings.map((item) => item.elapsedMs).sort((left, right) => left - right);
  const p95 = ordered[Math.ceil(ordered.length * 0.95) - 1];
  await testInfo.attach('native-route-timings.json', {
    body: JSON.stringify({
      p95,
      exposedActions,
      slowest: [...timings].sort((left, right) => right.elapsedMs - left.elapsedMs).slice(0, 20),
      routes: timings,
    }, null, 2),
    contentType: 'application/json',
  });
  console.info(`[capabilities-native] p95=${p95}ms actions=${exposedActions}`);
  console.info(`[capabilities-native-ids] ${JSON.stringify(timings.map((item) => item.id).sort())}`);
  expect(p95, 'latência p95 da navegação nativa').toBeLessThan(5_000);
  expect(exposedActions, 'ações nativas anunciadas nas 135 rotas').toBe(511);
  expect(failures).toEqual([]);
});

test('a experiência nativa permanece legível e acionável no mobile', { tag: '@mobile-only' }, async ({ page, request }) => {
  test.setTimeout(120_000);
  await authenticateWorkspacePage(page, request);
  const { capabilities, verified } = await nativeRegistry(request);
  const representatives = (['communications', 'collaboration', 'automation', 'crm'] as const)
    .map((domain) => capabilities.find((capability) => capability.domain === domain))
    .filter((capability): capability is NativeCapability => Boolean(capability));
  const failures = runtimeFailures(page);

  expect(representatives).toHaveLength(4);
  for (const capability of representatives) {
    expect(capability.verified).toBe(verified);
    await page.goto(canonicalRoute(capability), { waitUntil: 'domcontentloaded' });
    const workspace = page.getByTestId('native-capability-workspace');
    await expect(workspace).toBeVisible();
    await expect(workspace.getByRole('heading', { level: 1, name: nativeDisplayText(capability.label), exact: true })).toBeVisible();
    await expect(workspace.getByRole('region', { name: 'Ações da capacidade' })).toBeVisible();
    if (capability.domain === 'crm') {
      await workspace.getByRole('tab', { name: 'Diagnóstico do motor' }).click();
      await expect(workspace.getByRole('tabpanel', { name: 'Diagnóstico do motor' })).toBeVisible();
    }

    const geometry = await page.evaluate(() => ({
      viewport: window.innerWidth,
      document: document.documentElement.scrollWidth,
      body: document.body.scrollWidth,
    }));
    expect(Math.max(geometry.document, geometry.body) - geometry.viewport).toBeLessThanOrEqual(1);
  }
  expect(failures).toEqual([]);
});

test('ações seguras dos quatro domínios concluem round-trip pela UI sem rede externa', { tag: '@desktop-only' }, async ({ page, request }) => {
  test.setTimeout(120_000);
  await authenticateWorkspacePage(page, request);
  const cases = [
    {
      route: '/app/capacidades/communications/inboxes-routing',
      action: 'Atualizar dados',
      finalButton: 'Confirmar operação',
      expectedResult: 'succeeded',
    },
    {
      route: '/app/capacidades/collaboration/enterprise-controls',
      action: 'Simular',
      finalButton: 'Confirmar operação',
      expectedResult: 'succeeded',
    },
    {
      route: '/app/capacidades/automation/agents-rbac',
      action: 'Sincronizar',
      finalButton: 'Confirmar operação',
      expectedResult: 'succeeded',
    },
    {
      route: '/app/capacidades/crm/external-integrations',
      action: 'Salvar configuração da integração (simulação)',
      finalButton: 'Confirmar operação',
      expectedResult: 'succeeded',
    },
  ] as const;

  for (const operation of cases) {
    await page.goto(operation.route, { waitUntil: 'domcontentloaded' });
    const workspace = page.getByTestId('native-capability-workspace');
    const actionRegion = workspace.getByRole('region', { name: 'Ações da capacidade' });
    await actionRegion.getByRole('button', { name: operation.action, exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('button', { name: 'Revisar ação' }).click();

    const actionResponse = page.waitForResponse((response) => (
      response.request().method() === 'POST'
      && response.url().includes('/api/v1/capabilities/domains/')
      && response.url().endsWith('/actions')
    ));
    await dialog.getByRole('button', { name: operation.finalButton, exact: true }).click();
    const response = await actionResponse;
    expect(response.status(), operation.route).toBe(200);
    const payload = await response.json();
    expect(payload.status).toBe(operation.expectedResult);
    expect(payload.result.externalNetworkExecuted).toBe(false);
    await expect(dialog).not.toBeVisible();
  }
});
