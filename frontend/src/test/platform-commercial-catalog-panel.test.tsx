/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const commercialApi = vi.hoisted(() => ({
  bootstrap: vi.fn(),
  createKey: vi.fn(),
  contracts: vi.fn(),
  discardDraft: vi.fn(),
  draft: vi.fn(),
  history: vi.fn(),
  offers: vi.fn(),
  previewDraft: vi.fn(),
  previewRestore: vi.fn(),
  publishDraft: vi.fn(),
  restore: vi.fn(),
  saveDraft: vi.fn(),
}));

vi.mock('@/services/platform-commercial-catalog', async () => {
  const actual = await vi.importActual<typeof import('@/services/platform-commercial-catalog')>('@/services/platform-commercial-catalog');
  return {
    ...actual,
    bootstrapCommercialOffers: commercialApi.bootstrap,
    commercialIdempotencyKey: commercialApi.createKey,
    discardCommercialOfferDraft: commercialApi.discardDraft,
    getCommercialOfferDraft: commercialApi.draft,
    getCommercialOfferHistory: commercialApi.history,
    listCommercialContracts: commercialApi.contracts,
    listCommercialOffers: commercialApi.offers,
    previewCommercialOfferDraft: commercialApi.previewDraft,
    previewCommercialOfferRestore: commercialApi.previewRestore,
    publishCommercialOfferDraft: commercialApi.publishDraft,
    restoreCommercialOffer: commercialApi.restore,
    saveCommercialOfferDraft: commercialApi.saveDraft,
  };
});

import { PlatformCommercialCatalogPanel } from '@/components/platform/PlatformCommercialCatalogPanel';
import { ApiError } from '@/services/http';
import {
  COMMERCIAL_ENTITLEMENT_KEYS,
  type CommercialEntitlementKey,
  type CommercialEntitlementSetting,
  type CommercialOfferSnapshotV2,
  type CommercialOfferVersion,
} from '@/services/platform-commercial-catalog';

const featureKeys = new Set<CommercialEntitlementKey>([
  'legal_suite', 'legal_signatures', 'legal_bulk_operations', 'legal_audit', 'legal_integrations',
  'digital_signature', 'calendar', 'email', 'webhooks', 'advanced_audit', 'success_manager',
]);

const unavailableLegalKeys = new Set<CommercialEntitlementKey>([
  'legal_cases', 'djen_terms', 'legal_ai_generations', 'legal_automations',
  'legal_templates', 'legal_signatures', 'legal_bulk_operations',
  'legal_audit', 'legal_integrations',
]);

function entitlement(key: CommercialEntitlementKey): CommercialEntitlementSetting {
  const feature = featureKeys.has(key);
  return {
    enabled: key !== 'legal_suite',
    unit: feature ? 'feature' : key,
    period: feature ? 'feature' : 'monthly',
    limitQuantity: feature ? null : 100,
    enforcement: 'hard',
    graceQuantity: null,
    composition: feature ? 'boolean_any' : 'maximum',
  };
}

function offer(
  sku: string,
  name: string,
  priceMonthlyCents: number | null,
  sortOrder: number,
  options: { recommended?: boolean; legal?: boolean } = {},
): CommercialOfferVersion {
  const entitlements = Object.fromEntries(
    COMMERCIAL_ENTITLEMENT_KEYS
      .filter((key) => key !== 'contacts')
      .map((key) => {
        const setting = entitlement(key);
        return [key, options.legal && unavailableLegalKeys.has(key)
          ? { ...setting, enabled: false, limitQuantity: null, graceQuantity: null }
          : setting];
      }),
  ) as CommercialOfferSnapshotV2['entitlements'];
  return {
    id: `version-${sku}`,
    offerId: `offer-${sku}`,
    sku,
    family: options.legal ? 'legal' : 'crm',
    kind: options.legal ? 'solution' : 'base_plan',
    legacyTier: null,
    version: 1,
    snapshot: {
      schemaVersion: '2.0',
      name,
      description: options.legal ? 'Operação jurídica completa.' : `Descrição ${name}`,
      isPublic: true,
      isActive: true,
      isRecommended: Boolean(options.recommended),
      isQuoteOnly: priceMonthlyCents == null,
      availableIntervals: ['monthly'],
      sortOrder,
      currency: 'BRL',
      priceMonthlyCents,
      priceAnnualCents: null,
      cta: options.legal ? 'Falar com especialista' : 'Quero uma demonstração',
      entitlements,
    },
    snapshotHash: `hash-${sku}`,
    publicationReason: 'Catálogo aprovado.',
    publishedByUserId: 'root-1',
    publishedAt: '2026-07-30T12:00:00Z',
  };
}

const offers = [
  offer('crm_start', 'Start', 26_900, 10),
  offer('crm_advanced', 'Advanced', 67_900, 20, { recommended: true }),
  offer('crm_evolution', 'Evolution', 124_900, 30),
  offer('legal_360', 'Jurídico 360', null, 40, { legal: true }),
];

function renderPanel(props: React.ComponentProps<typeof PlatformCommercialCatalogPanel> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><PlatformCommercialCatalogPanel {...props} /></QueryClientProvider>);
}

beforeEach(() => {
  Object.values(commercialApi).forEach((mock) => mock.mockReset());
  commercialApi.createKey.mockReturnValue('offer-publish-1');
  commercialApi.offers.mockResolvedValue({ items: offers, total: 4, limit: 50, offset: 0, hasMore: false });
  commercialApi.contracts.mockResolvedValue({ items: [], total: 0, limit: 10, offset: 0, hasMore: false });
  commercialApi.draft.mockImplementation(async () => {
    const lastSave = commercialApi.saveDraft.mock.results.at(-1)?.value;
    if (!lastSave) throw new ApiError(404, 'Sem rascunho');
    return (await lastSave).draft;
  });
  commercialApi.history.mockImplementation(async (sku) => ({
    sku,
    items: [{ offer: offers.find((item) => item.sku === sku) ?? offers[0], diff: [] }],
  }));
  commercialApi.saveDraft.mockImplementation(async (_sku, _version, payload) => ({
    draft: {
      id: 'draft-start',
      offerId: offers[0].offerId,
      sku: offers[0].sku,
      draftVersion: 1,
      basePublishedVersion: 1,
      snapshot: payload.snapshot,
      snapshotHash: 'draft-hash',
      status: 'active',
      reason: payload.reason,
      updatedByUserId: 'admin-1',
      updatedAt: '2026-07-30T13:00:00Z',
    },
    idempotentReplay: false,
  }));
  commercialApi.previewDraft.mockImplementation(async () => ({
    draft: (await commercialApi.saveDraft.mock.results.at(-1)?.value)?.draft,
    published: offers[0],
    diff: [{ path: 'entitlements.task_participants.limit_quantity', label: 'Participantes por tarefa', changeType: 'changed', before: 3, after: 7 }],
    warnings: [],
    unitEconomics: {
      assessmentVersion: 'ai-ledger-worst-route-v2',
      status: 'ready',
      eligible: true,
      requiresEvidence: true,
      evidenceSource: 'provider_confirmed_usage_plus_internal_rate_card',
      evaluatedAt: '2026-07-30T13:00:00Z',
      windowDays: 90,
      minimumConfirmedCalls: 30,
      minimumDistinctDays: 7,
      minimumObservedTokens: 50_000,
      observedCalls: 42,
      observedDays: 8,
      observedTokens: 80_000,
      blockers: [],
      diagnostics: ['A evidência observada é suficiente e o teste de custo interno passou.'],
    },
    eligible: true,
  }));
  commercialApi.publishDraft.mockImplementation(async () => ({
    offer: { ...offers[0], version: 2 },
    idempotentReplay: false,
  }));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('PlatformCommercialCatalogPanel', () => {
  it('renders the canonical three base offers and quote-only Jurídico 360 without exposing identifiers', async () => {
    renderPanel();

    expect(await screen.findByText('Start')).toBeInTheDocument();
    expect(screen.getByText('Advanced')).toBeInTheDocument();
    expect(screen.getByText('Evolution')).toBeInTheDocument();
    expect(screen.getAllByText('Jurídico 360').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/269,00/)).toBeInTheDocument();
    expect(screen.getByText(/679,00/)).toBeInTheDocument();
    expect(screen.getByText(/1\.249,00/)).toBeInTheDocument();
    expect(screen.getByText('Recomendado')).toBeInTheDocument();
    expect(screen.getByText('Sob consulta')).toBeInTheDocument();
    expect(screen.queryByText('offer-crm_start')).not.toBeInTheDocument();
    expect(await screen.findByText('Seu perfil pode consultar as revisões, mas não editar rascunhos.')).toBeInTheDocument();
  });

  it('requires a current impact preview and reason before root publishes a quantitative limit revision', async () => {
    renderPanel({ isPlatformRoot: true, canDraft: true, canPreview: true, canRestore: true });

    const limit = await screen.findByLabelText('Limite', { selector: '#commercial-limit-task_participants' });
    fireEvent.change(limit, { target: { value: '7' } });
    expect(screen.getByText('Alterações locais')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Motivo da alteração'), { target: { value: 'Aumentar colaboração do Start.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar rascunho' }));
    await waitFor(() => expect(commercialApi.saveDraft).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: 'Gerar prévia da revisão' }));
    expect(await screen.findByText(/1 campo\(s\) alterado\(s\)/)).toBeInTheDocument();
    const publish = screen.getByRole('button', { name: 'Publicar nova revisão' });
    expect(publish).toBeEnabled();
    fireEvent.click(publish);
    fireEvent.click(await screen.findByRole('button', { name: 'Confirmar publicação' }));

    await waitFor(() => expect(commercialApi.publishDraft).toHaveBeenCalledTimes(1));
    const [sku, version, publicationReason] = commercialApi.publishDraft.mock.calls[0];
    expect(sku).toBe('crm_start');
    expect(version).toBe(1);
    expect(publicationReason).toBe('Aumentar colaboração do Start.');
    expect(commercialApi.saveDraft.mock.calls[0][2].snapshot.entitlements.task_participants.limitQuantity).toBe(7);
  });

  it('lets an authorized platform admin save and preview but never publish a revision', async () => {
    renderPanel({ canDraft: true, canPreview: true, isPlatformRoot: false });

    const limit = await screen.findByLabelText('Limite', { selector: '#commercial-limit-task_participants' });
    fireEvent.change(limit, { target: { value: '8' } });
    fireEvent.change(screen.getByLabelText('Motivo da alteração'), { target: { value: 'Preparar novo limite para revisão.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar rascunho' }));

    await waitFor(() => expect(commercialApi.saveDraft).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: 'Gerar prévia da revisão' }));

    expect(await screen.findByText(/1 campo\(s\) alterado\(s\)/)).toBeInTheDocument();
    expect(screen.getByText('Você pode salvar e validar o rascunho. Somente o Super Admin publica a revisão.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Publicar nova revisão' })).not.toBeInTheDocument();
    expect(commercialApi.publishDraft).not.toHaveBeenCalled();
  });

  it('keeps the mutation draft visible without racing a post-response refetch', async () => {
    commercialApi.draft.mockRejectedValue(new ApiError(404, 'Sem rascunho'));
    renderPanel({ canDraft: true, canPreview: true, isPlatformRoot: false });

    const limit = await screen.findByLabelText('Limite', {
      selector: '#commercial-limit-task_participants',
    });
    fireEvent.change(limit, { target: { value: '11' } });
    fireEvent.change(screen.getByLabelText('Motivo da alteração'), {
      target: { value: 'Persistir o retorno canônico da mutation.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar rascunho' }));

    expect(await screen.findByText('Rascunho v1')).toBeInTheDocument();
    expect(commercialApi.draft).toHaveBeenCalledTimes(1);
  });

  it('hydrates a real V2 policy without contacts and blocks an enabled contact without a finite limit', async () => {
    renderPanel({ canDraft: true, canPreview: true, isPlatformRoot: true });

    const contacts = await screen.findByRole('switch', { name: 'Incluir Contatos' });
    expect(contacts).not.toBeChecked();
    fireEvent.click(contacts);

    expect(await screen.findByText('Contatos exige limite finito.')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Motivo da alteração'), {
      target: { value: 'Configurar a nova quota de contatos.' },
    });
    expect(screen.getByRole('button', { name: 'Salvar rascunho' })).toBeDisabled();
    expect(commercialApi.saveDraft).not.toHaveBeenCalled();
  });

  it('keeps draft actions disabled while the audit reason has fewer than eight characters', async () => {
    renderPanel({ canDraft: true, canPreview: true, isPlatformRoot: true });

    const limit = await screen.findByLabelText('Limite', {
      selector: '#commercial-limit-task_participants',
    });
    fireEvent.change(limit, { target: { value: '7' } });
    fireEvent.change(screen.getByLabelText('Motivo da alteração'), {
      target: { value: '1234567' },
    });

    expect(screen.getByRole('button', { name: 'Salvar rascunho' })).toBeDisabled();
    expect(commercialApi.saveDraft).not.toHaveBeenCalled();
  });

  it('blocks public publication when real AI cost evidence is insufficient', async () => {
    commercialApi.previewDraft.mockImplementation(async () => ({
      draft: (await commercialApi.saveDraft.mock.results.at(-1)?.value)?.draft,
      published: offers[0],
      diff: [{ path: 'is_public', label: 'Visibilidade pública', changeType: 'changed', before: false, after: true }],
      warnings: [],
      unitEconomics: {
        assessmentVersion: 'ai-ledger-worst-route-v2',
        status: 'insufficient_data',
        eligible: false,
        requiresEvidence: true,
        evidenceSource: 'provider_confirmed_usage_plus_internal_rate_card',
        evaluatedAt: '2026-07-30T13:00:00Z',
        windowDays: 90,
        minimumConfirmedCalls: 30,
        minimumDistinctDays: 7,
        minimumObservedTokens: 50_000,
        observedCalls: 0,
        observedDays: 0,
        observedTokens: 0,
        blockers: [{ code: 'AI_COST_SAMPLE_TOO_SMALL', message: 'Há 0 chamadas com custo confirmado; são exigidas 30.' }],
        diagnostics: ['A oferta pode permanecer interna; publicação pública continua bloqueada.'],
      },
      eligible: false,
    }));
    renderPanel({ canDraft: true, canPreview: true, isPlatformRoot: true });

    const limit = await screen.findByLabelText('Limite', { selector: '#commercial-limit-task_participants' });
    fireEvent.change(limit, { target: { value: '9' } });
    fireEvent.change(screen.getByLabelText('Motivo da alteração'), { target: { value: 'Preparar publicação com novo limite.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar rascunho' }));
    await waitFor(() => expect(commercialApi.saveDraft).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: 'Gerar prévia da revisão' }));

    expect(await screen.findByText('Evidência insuficiente')).toBeInTheDocument();
    expect(screen.getByText('Há 0 chamadas com custo confirmado; são exigidas 30.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Publicar nova revisão' })).toBeDisabled();
  });

  it('shows an honest empty state and only root can initialize the default catalog', async () => {
    commercialApi.offers.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0, hasMore: false });
    const { rerender } = renderPanel();
    expect(await screen.findByText('Catálogo ainda não inicializado')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Criar catálogo inicial' })).not.toBeInTheDocument();

    cleanup();
    commercialApi.bootstrap.mockResolvedValue({ items: offers, idempotentReplay: false });
    renderPanel({ isPlatformRoot: true, canDraft: true, canPreview: true });
    fireEvent.click(await screen.findByRole('button', { name: 'Criar catálogo inicial' }));
    await waitFor(() => expect(commercialApi.bootstrap).toHaveBeenCalledWith('offer-publish-1'));
  });

  it('keeps legal capabilities without runtime enforcement unavailable for sale', async () => {
    renderPanel({ isPlatformRoot: true });

    fireEvent.click(await screen.findByRole('button', { name: /Jurídico 360/ }));
    expect(screen.getAllByText('Sem enforcement')).toHaveLength(unavailableLegalKeys.size);
    expect(screen.getByRole('switch', { name: 'Incluir Casos jurídicos' })).toBeDisabled();
    expect(screen.getAllByText(/Indisponível para venda até existir consumo e bloqueio operacional comprovados/)).toHaveLength(unavailableLegalKeys.size);
  });
});
