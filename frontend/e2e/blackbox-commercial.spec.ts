import {
  authenticatePageForRole,
  expect,
  sessionForRole,
  test,
  type APIRequestContext,
  type Page,
} from './fixtures';

const API_URL = process.env.KLM_API_URL ?? 'http://127.0.0.1:8010';
const COMMERCIAL_GATE = process.env.KLM_E2E_COMMERCIAL === '1';

type EntitlementSetting = {
  enabled: boolean;
  unit: string;
  period: string;
  limitQuantity: number | null;
  graceQuantity: number | null;
  enforcement: string;
  composition: string;
};

type CommercialOffer = {
  id: string;
  sku: string;
  version: number;
  snapshot: {
    name: string;
    priceMonthlyCents: number | null;
    isQuoteOnly: boolean;
    entitlements: Record<string, EntitlementSetting>;
  };
};

type ContractLine = {
  id: string | null;
  sku: string;
  policyVersionId: string;
  contractedPriceCents: number;
  entitlementOverrides: Record<string, EntitlementSetting>;
  status: string;
};

type CommercialContract = {
  id: string;
  workspaceId: string;
  version: number;
  lines: ContractLine[];
  price: {
    contractedPriceCents: number;
    recurringPriceCents: number;
  };
};

function authHeaders(token: string, extra: Record<string, string> = {}): Record<string, string> {
  return { Authorization: `Bearer ${token}`, ...extra };
}

async function expectStatus(
  response: Awaited<ReturnType<APIRequestContext['get']>>,
  status: number,
): Promise<void> {
  expect(response.status(), await response.text()).toBe(status);
}

async function bootstrapCatalog(
  request: APIRequestContext,
  token: string,
  runId: string,
): Promise<Record<string, CommercialOffer>> {
  const response = await request.post(`${API_URL}/api/v1/platform/commercial/offers/bootstrap`, {
    headers: authHeaders(token, { 'Idempotency-Key': `e2e-commercial-bootstrap-${runId}` }),
  });
  await expectStatus(response, 201);
  const payload = await response.json() as { items: CommercialOffer[] };
  const catalog = Object.fromEntries(payload.items.map((offer) => [offer.sku, offer]));
  expect(Object.keys(catalog).sort()).toEqual([
    'crm_advanced',
    'crm_evolution',
    'crm_start',
    'legal_360',
  ]);
  return catalog;
}

async function createWorkspace(
  request: APIRequestContext,
  token: string,
  name: string,
): Promise<{ id: string; name: string }> {
  const response = await request.post(`${API_URL}/api/v1/platform/workspaces`, {
    headers: authHeaders(token),
    data: { name, setupMode: 'assisted' },
  });
  await expectStatus(response, 201);
  return response.json() as Promise<{ id: string; name: string }>;
}

function legalLine(offer: CommercialOffer): Record<string, unknown> {
  const setting = offer.snapshot.entitlements;
  return {
    sku: offer.sku,
    policyVersionId: offer.id,
    contractedPriceCents: 89_900,
    entitlementOverrides: {
      users: { ...setting.users, enabled: true, limitQuantity: 2 },
      legal_areas: { ...setting.legal_areas, enabled: true, limitQuantity: 3 },
      storage_bytes: {
        ...setting.storage_bytes,
        enabled: true,
        limitQuantity: 10 * 1024 ** 3,
      },
      monitored_processes: {
        ...setting.monitored_processes,
        enabled: true,
        limitQuantity: 0,
      },
      legal_documents: {
        ...setting.legal_documents,
        enabled: true,
        limitQuantity: 1_000,
      },
      protocol_folders: {
        ...setting.protocol_folders,
        enabled: true,
        limitQuantity: 100,
      },
    },
  };
}

async function createContract(
  request: APIRequestContext,
  token: string,
  runId: string,
  workspaceId: string,
  lines: Record<string, unknown>[],
): Promise<CommercialContract> {
  const existingResponse = await request.get(
    `${API_URL}/api/v1/platform/commercial/workspaces/${workspaceId}/contract`,
    { headers: authHeaders(token) },
  );
  if (existingResponse.status() === 200) {
    const existing = await existingResponse.json() as CommercialContract;
    const revisedResponse = await request.put(
      `${API_URL}/api/v1/platform/commercial/contracts/${existing.id}`,
      {
        headers: authHeaders(token, {
          'Idempotency-Key': `e2e-commercial-contract-revision-${runId}`,
          'If-Match': `"${existing.version}"`,
        }),
        data: {
          lines,
          reason: `Configuração contratual E2E integrada ${runId}.`,
        },
      },
    );
    await expectStatus(revisedResponse, 200);
    return revisedResponse.json() as Promise<CommercialContract>;
  }
  await expectStatus(existingResponse, 404);
  const response = await request.post(`${API_URL}/api/v1/platform/commercial/contracts`, {
    headers: authHeaders(token, { 'Idempotency-Key': `e2e-commercial-contract-${runId}` }),
    data: {
      workspaceId,
      lines,
      status: 'active',
      reason: `Contrato E2E integrado ${runId}.`,
    },
  });
  await expectStatus(response, 201);
  return response.json() as Promise<CommercialContract>;
}

async function openAccount360(page: Page, workspaceName: string): Promise<void> {
  await page.goto('/blackbox/workspaces', { waitUntil: 'domcontentloaded' });
  await page.getByRole('textbox', { name: 'Buscar contas' }).fill(workspaceName);
  await expect(page.getByText(workspaceName, { exact: true }).first()).toBeVisible();
  await page.getByRole('button', { name: 'Conta 360', exact: true }).first().click();
  await expect(page.getByRole('dialog', { name: workspaceName })).toBeVisible();
}

test.describe('Catálogo e contratos canônicos do Blackbox', () => {
  test.skip(!COMMERCIAL_GATE, 'Requer API e PostgreSQL locais descartáveis, sem mocks de frontend.');

  test('prova catálogo, Jurídico standalone/combinado e preservação de exceções na Conta 360 @desktop-only', async ({ page, request }) => {
    const runId = crypto.randomUUID().slice(0, 8);
    const root = await sessionForRole(request, 'platform_owner');
    await authenticatePageForRole(page, request, 'platform_owner');
    const catalog = await bootstrapCatalog(request, root.token, runId);

    expect(catalog.crm_start.snapshot).toMatchObject({
      name: 'Start',
      priceMonthlyCents: 26_900,
      isQuoteOnly: false,
    });
    expect(catalog.crm_advanced.snapshot).toMatchObject({
      name: 'Advanced',
      priceMonthlyCents: 67_900,
      isQuoteOnly: false,
    });
    expect(catalog.crm_evolution.snapshot).toMatchObject({
      name: 'Evolution',
      priceMonthlyCents: 124_900,
      isQuoteOnly: false,
    });
    expect(catalog.legal_360.snapshot).toMatchObject({
      name: 'Jurídico 360',
      priceMonthlyCents: null,
      isQuoteOnly: true,
    });

    await page.goto('/blackbox/plans', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: 'Estúdio de Planos' })).toBeVisible();
    const offerRegion = page.getByRole('region', { name: 'Ofertas comerciais' });
    for (const name of ['Start', 'Advanced', 'Evolution', 'Jurídico 360']) {
      await expect(offerRegion.getByRole('button').filter({ hasText: name })).toBeVisible();
    }
    await expect(offerRegion.getByText('R$ 269,00')).toBeVisible();
    await expect(offerRegion.getByText('R$ 679,00')).toBeVisible();
    await expect(offerRegion.getByText('R$ 1.249,00')).toBeVisible();
    await expect(offerRegion.getByText('Sob consulta')).toBeVisible();

    const standaloneWorkspace = await createWorkspace(
      request,
      root.token,
      `E2E Jurídico standalone ${runId}`,
    );
    const standalone = await createContract(
      request,
      root.token,
      `${runId}-standalone`,
      standaloneWorkspace.id,
      [legalLine(catalog.legal_360)],
    );
    expect(standalone.lines.filter((line) => line.status === 'active').map((line) => line.sku))
      .toEqual(['legal_360']);
    const standaloneManifestResponse = await request.get(
      `${API_URL}/api/v1/platform/commercial/workspaces/${standaloneWorkspace.id}/manifest`,
      { headers: authHeaders(root.token) },
    );
    await expectStatus(standaloneManifestResponse, 200);
    const standaloneManifest = await standaloneManifestResponse.json() as {
      activeProducts: string[];
      capabilities: Record<string, { permitted: boolean; limit: number | null }>;
    };
    expect(standaloneManifest.activeProducts).toEqual(['legal_360']);
    expect(standaloneManifest.capabilities.legal_suite.permitted).toBe(true);
    expect(standaloneManifest.capabilities.monitored_processes.limit).toBe(0);

    const combinedWorkspace = await createWorkspace(
      request,
      root.token,
      `E2E Core e Jurídico ${runId}`,
    );
    const combined = await createContract(
      request,
      root.token,
      `${runId}-combined`,
      combinedWorkspace.id,
      [
        { sku: 'crm_start', policyVersionId: catalog.crm_start.id },
        legalLine(catalog.legal_360),
      ],
    );
    expect(new Set(combined.lines.filter((line) => line.status === 'active').map((line) => line.sku)))
      .toEqual(new Set(['crm_start', 'legal_360']));

    const startsAt = new Date(Date.now() - 60_000).toISOString();
    const discountReason = `Desconto preservado E2E ${runId}`;
    const discountResponse = await request.post(
      `${API_URL}/api/v1/platform/commercial/contracts/${combined.id}/adjustments`,
      {
        headers: authHeaders(root.token, {
          'Idempotency-Key': `e2e-commercial-discount-${runId}`,
        }),
        data: {
          type: 'percentage_discount',
          value: 1_000,
          startsAt,
          stackGroup: `e2e-${runId}`,
          stackable: false,
          portability: 'preserve',
          reason: discountReason,
          source: 'manual',
        },
      },
    );
    await expectStatus(discountResponse, 201);

    const bonusReason = `Bônus preservado E2E ${runId}`;
    const bonusResponse = await request.post(
      `${API_URL}/api/v1/platform/commercial/workspaces/${combinedWorkspace.id}/bonuses`,
      {
        headers: authHeaders(root.token, {
          'Idempotency-Key': `e2e-commercial-bonus-${runId}`,
        }),
        data: {
          entitlement: 'tokens',
          mode: 'quantity_delta',
          quantity: 50_000,
          startsAt,
          renewsEachPeriod: true,
          portability: 'preserve',
          reason: bonusReason,
          source: 'manual',
        },
      },
    );
    await expectStatus(bonusResponse, 201);

    const currentLegal = combined.lines.find((line) => line.sku === 'legal_360');
    expect(currentLegal).toBeTruthy();
    const revisionLines = [
      { sku: 'crm_advanced', policyVersionId: catalog.crm_advanced.id },
      {
        sku: 'legal_360',
        policyVersionId: currentLegal!.policyVersionId,
        contractedPriceCents: currentLegal!.contractedPriceCents,
        entitlementOverrides: currentLegal!.entitlementOverrides,
      },
    ];
    const revisionPreview = await request.post(
      `${API_URL}/api/v1/platform/commercial/contracts/${combined.id}/revision-preview`,
      {
        headers: authHeaders(root.token),
        data: { expectedVersion: combined.version, lines: revisionLines },
      },
    );
    await expectStatus(revisionPreview, 200);
    expect((await revisionPreview.json() as { eligible: boolean }).eligible).toBe(true);

    const revisedResponse = await request.put(
      `${API_URL}/api/v1/platform/commercial/contracts/${combined.id}`,
      {
        headers: authHeaders(root.token, {
          'Idempotency-Key': `e2e-commercial-revision-${runId}`,
          'If-Match': `"${combined.version}"`,
        }),
        data: {
          lines: revisionLines,
          reason: `Upgrade Core preservando exceções ${runId}.`,
        },
      },
    );
    await expectStatus(revisedResponse, 200);
    const revised = await revisedResponse.json() as CommercialContract;
    expect(revised.version).toBe(combined.version + 1);
    expect(revised.price.recurringPriceCents).toBeLessThan(revised.price.contractedPriceCents);

    const [adjustmentsResponse, bonusesResponse, combinedManifestResponse] = await Promise.all([
      request.get(`${API_URL}/api/v1/platform/commercial/contracts/${combined.id}/adjustments`, {
        headers: authHeaders(root.token),
      }),
      request.get(`${API_URL}/api/v1/platform/commercial/workspaces/${combinedWorkspace.id}/bonuses`, {
        headers: authHeaders(root.token),
      }),
      request.get(`${API_URL}/api/v1/platform/commercial/workspaces/${combinedWorkspace.id}/manifest`, {
        headers: authHeaders(root.token),
      }),
    ]);
    await expectStatus(adjustmentsResponse, 200);
    await expectStatus(bonusesResponse, 200);
    await expectStatus(combinedManifestResponse, 200);
    const adjustments = await adjustmentsResponse.json() as Array<{ status: string; reason: string }>;
    const bonuses = await bonusesResponse.json() as Array<{ status: string; reason: string }>;
    const combinedManifest = await combinedManifestResponse.json() as {
      activeProducts: string[];
      capabilities: Record<string, { limit: number | null; sources: Array<{ type: string }> }>;
    };
    expect(adjustments).toContainEqual(expect.objectContaining({ status: 'active', reason: discountReason }));
    expect(bonuses).toContainEqual(expect.objectContaining({ status: 'active', reason: bonusReason }));
    expect(new Set(combinedManifest.activeProducts)).toEqual(new Set(['crm_advanced', 'legal_360']));
    expect(combinedManifest.capabilities.tokens.limit).toBe(2_050_000);
    expect(combinedManifest.capabilities.tokens.sources.map((source) => source.type))
      .toEqual(expect.arrayContaining(['plan', 'bonus']));

    await openAccount360(page, combinedWorkspace.name);
    const account = page.getByRole('dialog', { name: combinedWorkspace.name });
    await expect(account.getByText(`Contrato versão ${revised.version}`)).toBeVisible();
    await expect(account.getByText('Advanced + Jurídico 360')).toBeVisible();
    await expect(account.getByText(discountReason)).toBeVisible();
    await expect(account.getByText(bonusReason)).toBeVisible();
    await expect(account.getByText('Bônus de capacidade')).toBeVisible();
  });

  test('platform_admin rascunha e pré-visualiza, mas publish e restore falham fechados @desktop-only', async ({ page, request }) => {
    const runId = crypto.randomUUID().slice(0, 8);
    const root = await sessionForRole(request, 'platform_owner');
    const catalog = await bootstrapCatalog(request, root.token, `${runId}-admin`);
    const admin = await sessionForRole(request, 'platform_admin');
    await authenticatePageForRole(page, request, 'platform_admin');

    const preexistingDraft = await request.get(
      `${API_URL}/api/v1/platform/commercial/offers/crm_start/draft`,
      { headers: authHeaders(admin.token) },
    );
    if (preexistingDraft.status() === 200) {
      const draft = await preexistingDraft.json() as { draftVersion: number };
      const discarded = await request.post(
        `${API_URL}/api/v1/platform/commercial/offers/crm_start/draft/discard`,
        {
          headers: authHeaders(admin.token, {
            'Idempotency-Key': `e2e-commercial-discard-before-${runId}`,
            'If-Match': `"${draft.draftVersion}"`,
          }),
          data: { reason: `Limpar rascunho E2E anterior ${runId}.` },
        },
      );
      await expectStatus(discarded, 200);
    } else {
      await expectStatus(preexistingDraft, 404);
    }

    await page.goto('/blackbox/plans', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: 'Estúdio de Planos' })).toBeVisible();
    await expect(page.getByText('Consulta por permissão')).toBeVisible();
    await page.getByRole('region', { name: 'Ofertas comerciais' })
      .getByRole('button')
      .filter({ hasText: 'Start' })
      .click();

    const taskParticipantLimit = page.locator('#commercial-limit-task_participants');
    await expect(taskParticipantLimit).toBeEnabled();
    const currentLimit = Number(await taskParticipantLimit.inputValue());
    expect(Number.isFinite(currentLimit)).toBe(true);
    await taskParticipantLimit.fill(String(currentLimit + 1));
    await page.getByLabel('Motivo da alteração').fill(`Rascunho operacional E2E ${runId}.`);
    await page.getByRole('button', { name: 'Salvar rascunho' }).click();
    await expect(page.getByText('Rascunho v1', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Gerar prévia da revisão' }).click();
    await expect(page.getByText('Prévia persistida')).toBeVisible();
    await expect(page.getByText(/1 campo\(s\) alterado\(s\)/)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Publicar nova revisão' })).toHaveCount(0);

    const publishDenied = await request.post(
      `${API_URL}/api/v1/platform/commercial/offers/crm_start/draft/publish`,
      {
        headers: authHeaders(admin.token, {
          'Idempotency-Key': `e2e-commercial-denied-publish-${runId}`,
          'If-Match': '"1"',
        }),
        data: { reason: `Admin não publica ${runId}.` },
      },
    );
    await expectStatus(publishDenied, 403);

    const restorePreviewDenied = await request.post(
      `${API_URL}/api/v1/platform/commercial/offers/crm_start/restore-preview`,
      {
        headers: authHeaders(admin.token),
        data: {
          targetVersion: 1,
          expectedCurrentVersion: catalog.crm_start.version,
        },
      },
    );
    await expectStatus(restorePreviewDenied, 403);
    const restoreDenied = await request.post(
      `${API_URL}/api/v1/platform/commercial/offers/crm_start/restore`,
      {
        headers: authHeaders(admin.token, {
          'Idempotency-Key': `e2e-commercial-denied-restore-${runId}`,
        }),
        data: {
          targetVersion: 1,
          expectedCurrentVersion: catalog.crm_start.version,
          reason: `Admin não restaura ${runId}.`,
        },
      },
    );
    await expectStatus(restoreDenied, 403);

    const persistedDraft = await request.get(
      `${API_URL}/api/v1/platform/commercial/offers/crm_start/draft`,
      { headers: authHeaders(admin.token) },
    );
    await expectStatus(persistedDraft, 200);
    const draft = await persistedDraft.json() as { draftVersion: number };
    const discarded = await request.post(
      `${API_URL}/api/v1/platform/commercial/offers/crm_start/draft/discard`,
      {
        headers: authHeaders(admin.token, {
          'Idempotency-Key': `e2e-commercial-discard-after-${runId}`,
          'If-Match': `"${draft.draftVersion}"`,
        }),
        data: { reason: `Finalizar rascunho E2E ${runId}.` },
      },
    );
    await expectStatus(discarded, 200);
  });
});
