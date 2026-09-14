/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const commercialApi = vi.hoisted(() => ({
  addAdjustment: vi.fn(),
  addBonus: vi.fn(),
  adjustments: vi.fn(),
  bonuses: vi.fn(),
  createContract: vi.fn(),
  createKey: vi.fn(),
  contract: vi.fn(),
  manifest: vi.fn(),
  offers: vi.fn(),
  providerState: vi.fn(),
  billingPortal: vi.fn(),
  preview: vi.fn(),
  previewRevision: vi.fn(),
  reviseContract: vi.fn(),
  updateBonus: vi.fn(),
}));
const requestApi = vi.hoisted(() => ({
  list: vi.fn(),
  resolve: vi.fn(),
  executeProvider: vi.fn(),
}));

vi.mock('@/services/platform-commercial-catalog', async () => {
  const actual = await vi.importActual<typeof import('@/services/platform-commercial-catalog')>('@/services/platform-commercial-catalog');
  return {
    ...actual,
    addCommercialAdjustment: commercialApi.addAdjustment,
    addEntitlementBonus: commercialApi.addBonus,
    commercialIdempotencyKey: commercialApi.createKey,
    createWorkspaceCommercialContract: commercialApi.createContract,
    getWorkspaceCapabilityManifest: commercialApi.manifest,
    getWorkspaceCommercialContract: commercialApi.contract,
    getWorkspaceCommercialProviderState: commercialApi.providerState,
    createWorkspaceCommercialBillingPortal: commercialApi.billingPortal,
    listCommercialAdjustments: commercialApi.adjustments,
    listCommercialOffers: commercialApi.offers,
    listEntitlementBonuses: commercialApi.bonuses,
    previewWorkspaceCommercialContract: commercialApi.preview,
    previewWorkspaceCommercialContractRevision: commercialApi.previewRevision,
    reviseWorkspaceCommercialContract: commercialApi.reviseContract,
    updateEntitlementBonus: commercialApi.updateBonus,
  };
});

vi.mock('@/services/commercial-self-service', async () => {
  const actual = await vi.importActual<typeof import('@/services/commercial-self-service')>('@/services/commercial-self-service');
  return {
    ...actual,
    fetchPlatformCommercialRequests: requestApi.list,
    resolvePlatformCommercialRequest: requestApi.resolve,
    executePlatformCommercialProviderChange: requestApi.executeProvider,
  };
});

import { WorkspaceCommercialSection } from '@/components/platform/WorkspaceCommercialSection';
import type { CommercialOfferVersion, EntitlementBonus, WorkspaceContract, WorkspaceContractPreview, WorkspaceContractRevisionPreview } from '@/services/platform-commercial-catalog';

function offer(sku: string, name: string, family: 'crm' | 'legal', price: number | null): CommercialOfferVersion {
  return {
    id: `version-${sku}`, offerId: `offer-${sku}`, sku, family, kind: family === 'crm' ? 'base_plan' : 'solution', legacyTier: null, version: 1,
    snapshot: {
      schemaVersion: '2.0', name, description: null, isPublic: true, isActive: true, isRecommended: name === 'Advanced', isQuoteOnly: price == null,
      availableIntervals: ['monthly'], sortOrder: family === 'crm' ? 20 : 40, currency: 'BRL', priceMonthlyCents: price, priceAnnualCents: null, cta: 'Demonstração',
      entitlements: {} as CommercialOfferVersion['snapshot']['entitlements'],
    },
    snapshotHash: `hash-${sku}`, publicationReason: 'Inicial', publishedByUserId: null, publishedAt: '2026-07-30T12:00:00Z',
  };
}

const offers = [offer('crm_advanced', 'Advanced', 'crm', 67_900), offer('legal_360', 'Jurídico 360', 'legal', null)];
const contract: WorkspaceContract = {
  id: 'contract-internal-uuid', workspaceId: 'workspace-1', workspaceLabel: 'Conta demonstrativa', status: 'active', currency: 'BRL', version: 1,
  lines: [{
    id: 'line-internal-uuid', offerId: 'offer-crm_advanced', policyVersionId: 'version-crm_advanced', sku: 'crm_advanced', offerFamily: 'crm', quantity: 1,
    listPriceCents: 67_900, contractedPriceCents: 67_900, billingInterval: 'monthly', entitlementOverrides: {}, effectiveFrom: '2026-07-30T12:00:00Z', effectiveUntil: null, status: 'active', version: 1,
  }],
  price: { listPriceCents: 67_900, contractedPriceCents: 67_900, recurringPriceCents: 67_900, invoiceCreditCents: 0, amountDueCents: 67_900 },
  createdAt: '2026-07-30T12:00:00Z', updatedAt: '2026-07-30T12:00:00Z', idempotentReplay: false,
};

const legalLine: WorkspaceContract['lines'][number] = {
  ...contract.lines[0],
  id: 'line-legal-only',
  offerId: 'offer-legal_360',
  policyVersionId: 'version-legal_360',
  sku: 'legal_360',
  offerFamily: 'legal',
  listPriceCents: 89_900,
  contractedPriceCents: 89_900,
  entitlementOverrides: {
    users: { enabled: true, limitQuantity: 3 },
    legal_areas: { enabled: true, limitQuantity: 2 },
    storage_bytes: { enabled: true, limitQuantity: 10 * 1024 ** 3 },
    monitored_processes: { enabled: true, limitQuantity: 50 },
    legal_documents: { enabled: true, limitQuantity: 1_000 },
    protocol_folders: { enabled: true, limitQuantity: 100 },
  } as WorkspaceContract['lines'][number]['entitlementOverrides'],
};

function chooseCoreOption(name: RegExp) {
  fireEvent.click(screen.getByRole('combobox', { name: 'Plano Core' }));
  fireEvent.click(screen.getByRole('option', { name }));
}

function relativeLocalDateTime(hours: number): string {
  const value = new Date(Date.now() + hours * 60 * 60 * 1_000);
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function renderSection(overrides: Partial<React.ComponentProps<typeof WorkspaceCommercialSection>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><WorkspaceCommercialSection
    workspaceId="workspace-1"
    canView
    canManageContracts
    canViewAdjustments
    canManageAdjustments
    canViewBonuses
    canManageBonuses
    {...overrides}
  /></QueryClientProvider>);
}

beforeEach(() => {
  Object.values(commercialApi).forEach((mock) => mock.mockReset());
  Object.values(requestApi).forEach((mock) => mock.mockReset());
  commercialApi.createKey.mockImplementation((scope: string) => `${scope}-key`);
  commercialApi.offers.mockResolvedValue({ items: offers, total: 2, limit: 50, offset: 0, hasMore: false });
  commercialApi.adjustments.mockResolvedValue([]);
  commercialApi.bonuses.mockResolvedValue([]);
  commercialApi.providerState.mockResolvedValue({
    providerState: 'ready',
    providerMessage: 'Stripe pronto.',
    customerLinked: false,
    subscriptionLinked: false,
    billingPortalAvailable: false,
    providerStatus: null,
  });
  requestApi.list.mockResolvedValue({ items: [], total: 0 });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('WorkspaceCommercialSection', () => {
  it('previews Core plus Jurídico 360 and creates the initial contract with a reason', async () => {
    commercialApi.contract.mockResolvedValue(null);
    const preview: WorkspaceContractPreview = {
      workspaceId: 'workspace-1',
      lines: [contract.lines[0], { ...contract.lines[0], id: null, sku: 'legal_360', offerFamily: 'legal', contractedPriceCents: 89_900, listPriceCents: 89_900 }],
      price: contract.price,
      manifest: { workspaceId: 'workspace-1', contractId: 'preview', contractVersion: 0, contractStatus: 'draft', activeProducts: ['crm_advanced', 'legal_360'], capabilities: {} as WorkspaceContractPreview['manifest']['capabilities'], generatedAt: '2026-07-30T12:00:00Z' },
      warnings: ['Jurídico 360 será configurado conforme proposta.'],
    };
    commercialApi.preview.mockResolvedValue(preview);
    commercialApi.createContract.mockResolvedValue(contract);
    renderSection();

    expect(await screen.findByText('Nenhum contrato canônico')).toBeInTheDocument();
    chooseCoreOption(/Advanced/);
    fireEvent.click(screen.getByRole('switch', { name: 'Adicionar Jurídico 360' }));
    fireEvent.change(screen.getByLabelText('Documentos jurídicos'), { target: { value: '1000' } });
    fireEvent.change(screen.getByLabelText('Pastas de protocolo'), { target: { value: '100' } });
    const legalPrice = screen.getByLabelText('Preço contratado (R$)');
    expect(legalPrice).toBeRequired();
    expect(screen.getByRole('button', { name: 'Gerar prévia e impacto' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Gerar prévia e impacto' }));
    expect(commercialApi.preview).not.toHaveBeenCalled();
    fireEvent.change(legalPrice, { target: { value: '0' } });
    expect(screen.getByRole('button', { name: 'Gerar prévia e impacto' })).toBeDisabled();
    fireEvent.change(legalPrice, { target: { value: '899.999' } });
    expect(screen.getByRole('button', { name: 'Gerar prévia e impacto' })).toBeDisabled();
    fireEvent.change(legalPrice, { target: { value: '899' } });
    for (const label of ['Usuários', 'Áreas jurídicas', 'Processos monitorados', 'Documentos jurídicos', 'Pastas de protocolo']) {
      const input = screen.getByLabelText(label);
      const originalValue = (input as HTMLInputElement).value;
      fireEvent.change(input, { target: { value: '1.5' } });
      expect(screen.getByRole('button', { name: 'Gerar prévia e impacto' })).toBeDisabled();
      fireEvent.change(input, { target: { value: originalValue } });
    }
    expect(screen.getByRole('button', { name: 'Gerar prévia e impacto' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Gerar prévia e impacto' }));
    expect(await screen.findByText('Advanced + Jurídico 360')).toBeInTheDocument();
    const reason = screen.getByLabelText('Motivo da alteração contratual');
    expect(reason).toHaveAttribute('maxlength', '500');
    fireEvent.change(reason, { target: { value: '1234567' } });
    expect(screen.getByRole('button', { name: 'Confirmar contrato' })).toBeDisabled();
    fireEvent.change(reason, { target: { value: '12345678' } });
    expect(screen.getByRole('button', { name: 'Confirmar contrato' })).toBeEnabled();
    fireEvent.change(reason, { target: { value: 'x'.repeat(501) } });
    expect(screen.getByRole('button', { name: 'Confirmar contrato' })).toBeDisabled();
    fireEvent.change(reason, { target: { value: 'Proposta comercial aceita pelo cliente.' } });
    expect(screen.getByRole('button', { name: 'Confirmar contrato' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar contrato' }));

    await waitFor(() => expect(commercialApi.createContract).toHaveBeenCalledTimes(1));
    const [payload, key] = commercialApi.createContract.mock.calls[0];
    expect(payload.lines.map((line: { sku: string }) => line.sku)).toEqual(['crm_advanced', 'legal_360']);
    expect(payload.lines.find((line: { sku: string }) => line.sku === 'legal_360').contractedPriceCents).toBe(89_900);
    expect(payload.reason).toBe('Proposta comercial aceita pelo cliente.');
    expect(key).toBe('workspace-contract-key');
  });

  it('does not infer a Core plan and creates an explicit Jurídico 360 standalone preview', async () => {
    commercialApi.contract.mockResolvedValue(null);
    commercialApi.preview.mockResolvedValue({
      workspaceId: 'workspace-1',
      lines: [legalLine],
      price: { ...contract.price, listPriceCents: 89_900, contractedPriceCents: 89_900, recurringPriceCents: 89_900, amountDueCents: 89_900 },
      manifest: { workspaceId: 'workspace-1', contractId: 'preview', contractVersion: 0, contractStatus: 'draft', activeProducts: ['legal_360'], capabilities: {} as WorkspaceContractPreview['manifest']['capabilities'], generatedAt: '2026-07-30T12:00:00Z' },
      warnings: [],
    } satisfies WorkspaceContractPreview);
    renderSection();

    expect(await screen.findByText('Nenhum contrato canônico')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Plano Core' })).toHaveTextContent('Somente Jurídico 360');
    fireEvent.click(screen.getByRole('switch', { name: 'Adicionar Jurídico 360' }));
    fireEvent.change(screen.getByLabelText('Preço contratado (R$)'), { target: { value: '899' } });
    fireEvent.change(screen.getByLabelText('Documentos jurídicos'), { target: { value: '1000' } });
    fireEvent.change(screen.getByLabelText('Pastas de protocolo'), { target: { value: '100' } });
    fireEvent.click(screen.getByRole('button', { name: 'Gerar prévia e impacto' }));

    await waitFor(() => expect(commercialApi.preview).toHaveBeenCalledTimes(1));
    expect(commercialApi.preview.mock.calls[0][0].lines.map((line: { sku: string }) => line.sku)).toEqual(['legal_360']);
  });

  it('removes Core only through preview while preserving the existing Jurídico 360 line', async () => {
    const combined = {
      ...contract,
      lines: [contract.lines[0], legalLine],
      price: { ...contract.price, listPriceCents: 157_800, contractedPriceCents: 157_800, recurringPriceCents: 157_800, amountDueCents: 157_800 },
    } satisfies WorkspaceContract;
    commercialApi.contract.mockResolvedValue(combined);
    commercialApi.manifest.mockResolvedValue({
      workspaceId: 'workspace-1', contractId: contract.id, contractVersion: 1, contractStatus: 'active', activeProducts: ['crm_advanced', 'legal_360'], capabilities: {}, generatedAt: '2026-07-30T12:00:00Z',
    });
    commercialApi.previewRevision.mockResolvedValue({
      contractId: contract.id,
      workspaceId: 'workspace-1',
      currentVersion: 1,
      targetVersion: 2,
      currentLines: combined.lines,
      lines: [legalLine],
      currentPrice: combined.price,
      price: { ...contract.price, listPriceCents: 89_900, contractedPriceCents: 89_900, recurringPriceCents: 89_900, amountDueCents: 89_900 },
      manifest: { workspaceId: 'workspace-1', contractId: contract.id, contractVersion: 2, contractStatus: 'active', activeProducts: ['legal_360'], capabilities: {} as WorkspaceContractRevisionPreview['manifest']['capabilities'], generatedAt: '2026-07-30T12:00:00Z' },
      changedFamilies: ['crm'], warnings: [], blockers: [], eligible: true,
    } satisfies WorkspaceContractRevisionPreview);
    renderSection();

    expect(await screen.findByText('Contrato versão 1')).toBeInTheDocument();
    chooseCoreOption(/Somente Jurídico 360/);
    fireEvent.click(screen.getByRole('button', { name: 'Gerar prévia e impacto' }));

    await waitFor(() => expect(commercialApi.previewRevision).toHaveBeenCalledTimes(1));
    expect(commercialApi.previewRevision.mock.calls[0][1].lines.map((line: { sku: string }) => line.sku)).toEqual(['legal_360']);
  });

  it('renders the resolved manifest and submits human-labelled adjustment and bonus forms without showing UUIDs', async () => {
    commercialApi.contract.mockResolvedValue(contract);
    commercialApi.manifest.mockResolvedValue({
      workspaceId: 'workspace-1', contractId: contract.id, contractVersion: 1, contractStatus: 'active', activeProducts: ['crm_advanced'], generatedAt: '2026-07-30T12:00:00Z',
      capabilities: {
        task_participants: { key: 'task_participants', contracted: true, enabled: true, permitted: true, limit: 10, graceLimit: null, used: 4, remaining: 6, unit: 'participants', period: 'concurrent', enforcement: 'hard', sources: [], denialReason: null, upgradeOffer: null },
      },
    });
    commercialApi.addAdjustment.mockResolvedValue({ adjustment: {}, idempotentReplay: false });
    commercialApi.addBonus.mockResolvedValue({ bonus: {}, idempotentReplay: false });
    renderSection();

    expect(await screen.findByText('Contrato versão 1')).toBeInTheDocument();
    expect(await screen.findByText('Participantes por tarefa')).toBeInTheDocument();
    expect(screen.getByText('4 / 10 · 6 restantes')).toBeInTheDocument();
    expect(screen.queryByText('contract-internal-uuid')).not.toBeInTheDocument();
    expect(screen.queryByText('line-internal-uuid')).not.toBeInTheDocument();

    const addButtons = screen.getAllByRole('button', { name: 'Adicionar' });
    fireEvent.click(addButtons[0]);
    fireEvent.change(screen.getByLabelText('Percentual'), { target: { value: '10' } });
    fireEvent.change(screen.getByLabelText('Motivo do ajuste'), { target: { value: '1234567' } });
    expect(screen.getByRole('button', { name: 'Registrar ajuste' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Motivo do ajuste'), { target: { value: 'Desconto comercial aprovado.' } });
    expect(screen.getByRole('button', { name: 'Registrar ajuste' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Registrar ajuste' }));
    await waitFor(() => expect(commercialApi.addAdjustment).toHaveBeenCalledTimes(1));
    expect(commercialApi.addAdjustment.mock.calls[0][1].value).toBe(1_000);

    fireEvent.click(screen.getAllByRole('button', { name: 'Adicionar' })[1]);
    fireEvent.change(screen.getByLabelText('Quantidade adicional'), { target: { value: '5' } });
    fireEvent.change(screen.getByLabelText('Motivo do bônus'), { target: { value: '1234567' } });
    expect(screen.getByRole('button', { name: 'Registrar bônus' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Motivo do bônus'), { target: { value: 'Bônus aprovado para a conta.' } });
    expect(screen.getByLabelText('Fim obrigatório')).toBeRequired();
    expect(screen.getByRole('button', { name: 'Registrar bônus' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Registrar bônus' }));
    expect(commercialApi.addBonus).not.toHaveBeenCalled();
    const bonusEnd = relativeLocalDateTime(48);
    fireEvent.change(screen.getByLabelText('Fim obrigatório'), { target: { value: bonusEnd } });
    expect(screen.getByRole('button', { name: 'Registrar bônus' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Registrar bônus' }));
    await waitFor(() => expect(commercialApi.addBonus).toHaveBeenCalledTimes(1));
    expect(commercialApi.addBonus.mock.calls[0][1]).toMatchObject({
      entitlement: 'ai_executions',
      mode: 'quantity_delta',
      quantity: 5,
      renewsEachPeriod: false,
      endsAt: new Date(bonusEnd).toISOString(),
    });
  });

  it('rejects invalid adjustment values and date windows without calling the API', async () => {
    commercialApi.contract.mockResolvedValue(contract);
    commercialApi.manifest.mockResolvedValue({
      workspaceId: 'workspace-1', contractId: contract.id, contractVersion: 1,
      contractStatus: 'active', activeProducts: ['crm_advanced'], capabilities: {},
      generatedAt: '2026-07-30T12:00:00Z',
    });
    renderSection();

    expect(await screen.findByText('Contrato versão 1')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Adicionar' })[0]);
    fireEvent.change(screen.getByLabelText('Motivo do ajuste'), { target: { value: 'Validação comercial local.' } });
    fireEvent.change(screen.getByLabelText('Percentual'), { target: { value: '10.123' } });
    const submit = screen.getByRole('button', { name: 'Registrar ajuste' });
    expect(submit).toBeDisabled();
    fireEvent.click(submit);
    expect(commercialApi.addAdjustment).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Percentual'), { target: { value: '101' } });
    expect(submit).toBeDisabled();
    fireEvent.click(submit);
    expect(commercialApi.addAdjustment).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('combobox', { name: 'Tipo' }));
    fireEvent.click(screen.getByRole('option', { name: 'Cortesia por período' }));
    expect(screen.queryByLabelText('Ciclos')).not.toBeInTheDocument();
    expect(screen.getByText('Cortesia integral entre início e fim.')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Início'), { target: { value: relativeLocalDateTime(24) } });
    fireEvent.change(screen.getByLabelText('Fim obrigatório'), { target: { value: relativeLocalDateTime(48) } });
    expect(submit).toBeEnabled();
    fireEvent.change(screen.getByLabelText('Início'), { target: { value: relativeLocalDateTime(48) } });
    fireEvent.change(screen.getByLabelText('Fim obrigatório'), { target: { value: relativeLocalDateTime(24) } });
    expect(submit).toBeDisabled();
    fireEvent.click(submit);
    expect(commercialApi.addAdjustment).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Início'), { target: { value: relativeLocalDateTime(-48) } });
    fireEvent.change(screen.getByLabelText('Fim obrigatório'), { target: { value: relativeLocalDateTime(-24) } });
    expect(submit).toBeDisabled();
    fireEvent.click(submit);
    expect(commercialApi.addAdjustment).not.toHaveBeenCalled();
  });

  it('rejects fractional capacity bonuses instead of truncating them', async () => {
    commercialApi.contract.mockResolvedValue(contract);
    commercialApi.manifest.mockResolvedValue({
      workspaceId: 'workspace-1', contractId: contract.id, contractVersion: 1,
      contractStatus: 'active', activeProducts: ['crm_advanced'], capabilities: {},
      generatedAt: '2026-07-30T12:00:00Z',
    });
    renderSection();

    expect(await screen.findByText('Contrato versão 1')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Adicionar' })[1]);
    fireEvent.change(screen.getByLabelText('Quantidade adicional'), { target: { value: '2.5' } });
    fireEvent.change(screen.getByLabelText('Motivo do bônus'), { target: { value: 'Bônus comercial aprovado.' } });
    fireEvent.change(screen.getByLabelText('Fim obrigatório'), { target: { value: relativeLocalDateTime(48) } });

    const submit = screen.getByRole('button', { name: 'Registrar bônus' });
    expect(submit).toBeDisabled();
    fireEvent.click(submit);
    expect(commercialApi.addBonus).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Quantidade adicional'), {
      target: { value: '9007199254740993' },
    });
    expect(submit).toBeDisabled();
    fireEvent.click(submit);
    expect(commercialApi.addBonus).not.toHaveBeenCalled();
  });

  it('allows a renewable bonus without an end date', async () => {
    commercialApi.contract.mockResolvedValue(contract);
    commercialApi.manifest.mockResolvedValue({
      workspaceId: 'workspace-1', contractId: contract.id, contractVersion: 1,
      contractStatus: 'active', activeProducts: ['crm_advanced'], capabilities: {},
      generatedAt: '2026-07-30T12:00:00Z',
    });
    commercialApi.addBonus.mockResolvedValue({ bonus: {}, idempotentReplay: false });
    renderSection();

    expect(await screen.findByText('Contrato versão 1')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Adicionar' })[1]);
    fireEvent.change(screen.getByLabelText('Quantidade adicional'), { target: { value: '5' } });
    fireEvent.change(screen.getByLabelText('Motivo do bônus'), { target: { value: 'Renovação comercial aprovada.' } });
    fireEvent.click(screen.getByRole('switch', { name: 'Renovar bônus a cada período' }));

    expect(screen.getByLabelText('Fim opcional')).not.toBeRequired();
    expect(screen.getByRole('button', { name: 'Registrar bônus' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Registrar bônus' }));

    await waitFor(() => expect(commercialApi.addBonus).toHaveBeenCalledTimes(1));
    expect(commercialApi.addBonus.mock.calls[0][1]).toMatchObject({
      renewsEachPeriod: true,
    });
    expect(commercialApi.addBonus.mock.calls[0][1]).not.toHaveProperty('endsAt');
  });

  it('preserves the end date while editing and toggling bonus renewal', async () => {
    const endsAt = new Date(relativeLocalDateTime(72)).toISOString();
    const existingBonus: EntitlementBonus = {
      id: 'bonus-1',
      workspaceId: 'workspace-1',
      entitlement: 'ai_executions',
      mode: 'quantity_delta',
      quantity: 5,
      startsAt: new Date(relativeLocalDateTime(-24)).toISOString(),
      endsAt,
      renewsEachPeriod: false,
      portability: 'preserve',
      reason: 'Bônus vigente aprovado.',
      source: 'manual',
      status: 'active',
      version: 3,
      createdAt: '2026-07-30T12:00:00Z',
    };
    commercialApi.contract.mockResolvedValue(contract);
    commercialApi.manifest.mockResolvedValue({
      workspaceId: 'workspace-1', contractId: contract.id, contractVersion: 1,
      contractStatus: 'active', activeProducts: ['crm_advanced'], capabilities: {},
      generatedAt: '2026-07-30T12:00:00Z',
    });
    commercialApi.bonuses.mockResolvedValue([existingBonus]);
    commercialApi.updateBonus.mockResolvedValue({ bonus: existingBonus, idempotentReplay: false });
    renderSection();

    expect(await screen.findByText('Bônus vigente aprovado.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Editar' }));
    const endInput = screen.getByLabelText('Fim obrigatório');
    const originalValue = (endInput as HTMLInputElement).value;
    expect(originalValue).not.toBe('');

    fireEvent.click(screen.getByRole('switch', { name: 'Renovar bônus a cada período' }));
    expect(screen.getByLabelText('Fim opcional')).toHaveValue(originalValue);
    fireEvent.click(screen.getByRole('switch', { name: 'Renovar bônus a cada período' }));
    expect(screen.getByLabelText('Fim obrigatório')).toHaveValue(originalValue);
    fireEvent.click(screen.getByRole('button', { name: 'Salvar bônus' }));

    await waitFor(() => expect(commercialApi.updateBonus).toHaveBeenCalledTimes(1));
    expect(commercialApi.updateBonus.mock.calls[0][0]).toBe('bonus-1');
    expect(commercialApi.updateBonus.mock.calls[0][1]).toBe(3);
    expect(commercialApi.updateBonus.mock.calls[0][2]).toMatchObject({
      endsAt,
      renewsEachPeriod: false,
    });
  });

  it('adds Jurídico 360 to an existing contract through versioned preview and If-Match command', async () => {
    commercialApi.contract.mockResolvedValue(contract);
    commercialApi.manifest.mockResolvedValue({
      workspaceId: 'workspace-1', contractId: contract.id, contractVersion: 1, contractStatus: 'active',
      activeProducts: ['crm_advanced'], capabilities: {}, generatedAt: '2026-07-30T12:00:00Z',
    });
    const revisionPreview: WorkspaceContractRevisionPreview = {
      contractId: contract.id,
      workspaceId: 'workspace-1',
      currentVersion: 1,
      targetVersion: 2,
      currentLines: contract.lines,
      lines: [contract.lines[0], { ...contract.lines[0], id: 'preview-legal', sku: 'legal_360', offerFamily: 'legal', contractedPriceCents: 89_900, listPriceCents: 89_900 }],
      currentPrice: contract.price,
      price: { ...contract.price, listPriceCents: 157_800, contractedPriceCents: 157_800, recurringPriceCents: 157_800, amountDueCents: 157_800 },
      manifest: { workspaceId: 'workspace-1', contractId: contract.id, contractVersion: 2, contractStatus: 'active', activeProducts: ['crm_advanced', 'legal_360'], capabilities: {} as WorkspaceContractRevisionPreview['manifest']['capabilities'], generatedAt: '2026-07-30T12:00:00Z' },
      changedFamilies: ['legal'], warnings: [], blockers: [], eligible: true,
    };
    commercialApi.previewRevision.mockResolvedValue(revisionPreview);
    commercialApi.reviseContract.mockResolvedValue({ ...contract, version: 2, lines: revisionPreview.lines });
    renderSection();

    expect(await screen.findByText('Revisar contrato vigente')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('switch', { name: 'Adicionar Jurídico 360' }));
    fireEvent.change(screen.getByLabelText('Preço contratado (R$)'), { target: { value: '899' } });
    fireEvent.change(screen.getByLabelText('Documentos jurídicos'), { target: { value: '1000' } });
    fireEvent.change(screen.getByLabelText('Pastas de protocolo'), { target: { value: '100' } });
    fireEvent.click(screen.getByRole('button', { name: 'Gerar prévia e impacto' }));
    expect(await screen.findByText('Advanced + Jurídico 360')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Motivo da alteração contratual'), { target: { value: 'Upgrade jurídico aprovado.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Aplicar nova versão' }));

    await waitFor(() => expect(commercialApi.reviseContract).toHaveBeenCalledTimes(1));
    expect(commercialApi.reviseContract.mock.calls[0][1]).toBe(1);
    expect(commercialApi.reviseContract.mock.calls[0][2].lines.map((line: { sku: string }) => line.sku)).toEqual(['crm_advanced', 'legal_360']);
  });

  it('fails closed for a Stripe-managed Core plan and opens the provider portal', async () => {
    commercialApi.contract.mockResolvedValue(contract);
    commercialApi.manifest.mockResolvedValue({
      workspaceId: 'workspace-1', contractId: contract.id, contractVersion: 1,
      contractStatus: 'active', activeProducts: ['crm_advanced'], capabilities: {},
      generatedAt: '2026-07-30T12:00:00Z',
    });
    commercialApi.providerState.mockResolvedValue({
      providerState: 'configured',
      providerMessage: 'Stripe configurado e autorizado.',
      customerLinked: true,
      subscriptionLinked: true,
      billingPortalAvailable: true,
      providerStatus: 'active',
    });
    commercialApi.billingPortal.mockResolvedValue({ portalUrl: 'https://billing.stripe.test/session' });
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    renderSection({ canRestoreContract: true });

    expect(await screen.findByText('Cobrança Core gerida pelo provedor')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Plano Core' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Abrir portal do provedor' }));

    await waitFor(() => expect(commercialApi.billingPortal).toHaveBeenCalledWith('workspace-1'));
    expect(open).toHaveBeenCalledWith(
      'https://billing.stripe.test/session',
      '_blank',
      'noopener,noreferrer',
    );
  });

  it('executes an Asaas plan change in the provider before closing the request', async () => {
    commercialApi.contract.mockResolvedValue(contract);
    commercialApi.manifest.mockResolvedValue({
      workspaceId: 'workspace-1', contractId: contract.id, contractVersion: 1,
      contractStatus: 'active', activeProducts: ['crm_advanced'], capabilities: {},
      generatedAt: '2026-07-30T12:00:00Z',
    });
    commercialApi.providerState.mockResolvedValue({
      provider: 'asaas', providerState: 'configured', providerMessage: 'Asaas configurado.',
      customerLinked: true, subscriptionLinked: true, billingPortalAvailable: false,
      providerStatus: 'ACTIVE',
    });
    const item = {
      id: 'request-1', workspaceId: 'workspace-1', requestedByUserId: 'user-1',
      requestType: 'change_base_plan', targetSku: 'crm_evolution',
      reason: 'Upgrade aprovado pelo titular.', status: 'pending', contractVersion: 1,
      version: 1, createdAt: '2026-08-13T12:00:00Z', resolvedAt: null,
      resolutionNote: null,
    } as const;
    requestApi.list.mockResolvedValue({ items: [item], total: 1 });
    requestApi.executeProvider.mockResolvedValue({ ...item, status: 'resolved', version: 2 });
    renderSection({ isPlatformRoot: true });

    fireEvent.click(await screen.findByRole('button', { name: 'Executar no Asaas' }));

    await waitFor(() => expect(requestApi.executeProvider).toHaveBeenCalledWith(
      'request-1',
      expect.objectContaining({ expectedVersion: 1 }),
    ));
  });

  it('preserves every annual legacy term while adding Jurídico 360', async () => {
    const annualContract = {
      ...contract,
      lines: [{
        ...contract.lines[0],
        billingInterval: 'annual' as const,
        listPriceCents: 650_000,
        contractedPriceCents: 650_000,
      }],
      price: {
        listPriceCents: 650_000,
        contractedPriceCents: 650_000,
        recurringPriceCents: 650_000,
        invoiceCreditCents: 0,
        amountDueCents: 650_000,
      },
    } satisfies WorkspaceContract;
    commercialApi.contract.mockResolvedValue(annualContract);
    commercialApi.manifest.mockResolvedValue({
      workspaceId: 'workspace-1', contractId: contract.id, contractVersion: 1,
      contractStatus: 'active', activeProducts: ['crm_advanced'], capabilities: {},
      generatedAt: '2026-07-30T12:00:00Z',
    });
    commercialApi.previewRevision.mockResolvedValue({
      contractId: contract.id, workspaceId: 'workspace-1', currentVersion: 1, targetVersion: 2,
      currentLines: annualContract.lines, lines: [...annualContract.lines, legalLine],
      currentPrice: annualContract.price, price: annualContract.price,
      manifest: { workspaceId: 'workspace-1', contractId: contract.id, contractVersion: 2, contractStatus: 'active', activeProducts: ['crm_advanced', 'legal_360'], capabilities: {} as WorkspaceContractRevisionPreview['manifest']['capabilities'], generatedAt: '2026-07-30T12:00:00Z' },
      changedFamilies: ['legal'], warnings: ['Ciclo anual legado preservado.'], blockers: [], eligible: true,
    } satisfies WorkspaceContractRevisionPreview);
    renderSection();

    expect(await screen.findByText('Contrato versão 1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('switch', { name: 'Adicionar Jurídico 360' }));
    fireEvent.change(screen.getByLabelText('Preço contratado (R$)'), { target: { value: '899' } });
    fireEvent.change(screen.getByLabelText('Documentos jurídicos'), { target: { value: '1000' } });
    fireEvent.change(screen.getByLabelText('Pastas de protocolo'), { target: { value: '100' } });
    fireEvent.click(screen.getByRole('button', { name: 'Gerar prévia e impacto' }));

    await waitFor(() => expect(commercialApi.previewRevision).toHaveBeenCalledTimes(1));
    const core = commercialApi.previewRevision.mock.calls[0][1].lines.find((line: { sku: string }) => line.sku === 'crm_advanced');
    expect(core).toMatchObject({
      policyVersionId: 'version-crm_advanced',
      contractedPriceCents: 650_000,
      billingInterval: 'annual',
      entitlementOverrides: {},
    });
  });
});
