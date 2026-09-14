/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SaasBillingPanel } from '@/components/admin/SaasBillingPanel';

const billingApi = vi.hoisted(() => ({
  catalog: vi.fn(),
  closure: vi.fn(),
  command: vi.fn(),
  exportIntent: vi.fn(),
  intents: vi.fn(),
  portal: vi.fn(),
}));
const commercialApi = vi.hoisted(() => ({
  account: vi.fn(),
  billingPortal: vi.fn(),
  catalog: vi.fn(),
  checkout: vi.fn(),
  create: vi.fn(),
  preview: vi.fn(),
}));

vi.mock('@/services/local-billing', () => ({
  createLocalBillingClosureIntent: billingApi.closure,
  createLocalBillingExportIntent: billingApi.exportIntent,
  executeLocalBillingCommand: billingApi.command,
  fetchLocalBillingIntents: billingApi.intents,
  fetchLocalBillingPortal: billingApi.portal,
  fetchLocalPlanCatalog: billingApi.catalog,
}));
vi.mock('@/services/commercial-self-service', () => ({
  fetchCommercialAccount: commercialApi.account,
  fetchPublicCommercialCatalog: commercialApi.catalog,
  createCommercialBillingPortal: commercialApi.billingPortal,
  createCommercialCheckoutSession: commercialApi.checkout,
  createCommercialRequest: commercialApi.create,
  previewCommercialRequest: commercialApi.preview,
}));

const entitlements = [{
  entitlement: 'users',
  enabled: true,
  limitQuantity: 10,
  hardLimit: true,
  source: 'plan_entitlement',
  configured: true,
}];

const portal = {
  workspaceId: 'workspace-1',
  subscription: {
    id: 'subscription-1',
    workspaceId: 'workspace-1',
    planId: 'plan-pro',
    planTier: 'pro',
    planName: 'Pro',
    status: 'active',
    interval: 'monthly',
    currentPeriodStart: '2026-07-01T00:00:00Z',
    currentPeriodEnd: '2026-08-01T00:00:00Z',
    amountCents: 69700,
    currency: 'BRL',
  },
  access: {
    workspaceId: 'workspace-1',
    subscriptionId: 'subscription-1',
    state: 'active',
    stateVersion: 2,
    readAllowed: true,
    writeAllowed: true,
    reasonCode: 'subscription_active',
    evaluatedAt: '2026-07-19T00:00:00Z',
  },
  entitlements,
  recentIntents: [],
  simulated: true,
  externalEffects: 0,
};

function renderPanel(canonicalManaged = true) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SaasBillingPanel canonicalManaged={canonicalManaged} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('SaasBillingPanel', () => {
  it('uses canonical self-service by default and never infers a simulator from missing capability data', async () => {
    billingApi.portal.mockResolvedValue(portal);
    billingApi.intents.mockResolvedValue([]);
    commercialApi.account.mockResolvedValue({
      contract: {
        id: 'contract-1', workspaceId: 'workspace-1', workspaceLabel: 'Escritório',
        status: 'active', currency: 'BRL', version: 2,
        lines: [{ sku: 'crm_advanced', contractedPriceCents: 67900, billingInterval: 'monthly', status: 'active' }],
        price: { listPriceCents: 67900, contractedPriceCents: 67900, recurringPriceCents: 67900, invoiceCreditCents: 0, amountDueCents: 67900 },
        createdAt: '2026-07-01T00:00:00Z', updatedAt: '2026-08-01T00:00:00Z',
      },
      requests: [], automaticCheckoutAvailable: false, billingPortalAvailable: false, providerSubscriptionLinked: false, providerSubscriptionStatus: null, providerState: 'not_configured',
      providerMessage: 'Cobrança automática não vinculada.',
    });
    commercialApi.catalog.mockResolvedValue({ items: [], generatedAt: '2026-08-12T00:00:00Z', state: 'not_published' });

    renderPanel();

    expect(await screen.findByText('Assinatura e produtos')).toBeInTheDocument();
    expect(screen.getByText('Cobrança assistida')).toBeInTheDocument();
    expect(screen.getByText('Alterar produtos')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Fazer upgrade' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Fazer downgrade' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Simular atraso' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Cancelar assinatura' })).not.toBeInTheDocument();
    expect(billingApi.catalog).not.toHaveBeenCalled();
  });

  it('uses hosted checkout only after a valid preview when no provider subscription exists', async () => {
    commercialApi.account.mockResolvedValue({
      contract: {
        id: 'contract-1', workspaceId: 'workspace-1', workspaceLabel: 'Escritório',
        status: 'trialing', currency: 'BRL', version: 3,
        lines: [{ sku: 'crm_start', contractedPriceCents: 26900, billingInterval: 'monthly', status: 'active' }],
        price: { listPriceCents: 26900, contractedPriceCents: 26900, recurringPriceCents: 26900, invoiceCreditCents: 0, amountDueCents: 26900 },
        createdAt: '2026-07-01T00:00:00Z', updatedAt: '2026-08-01T00:00:00Z',
      },
      requests: [], automaticCheckoutAvailable: true, billingPortalAvailable: false, providerSubscriptionLinked: false, providerSubscriptionStatus: null,
      providerState: 'configured', providerMessage: 'Checkout seguro disponível.',
      activeProvider: null, availableProviders: ['stripe'],
    });
    commercialApi.catalog.mockResolvedValue({
      items: [{ sku: 'crm_advanced', name: 'Advanced', description: null, family: 'crm', kind: 'base_plan', isRecommended: true, isQuoteOnly: false, priceMonthlyCents: 67900, currency: 'BRL', cta: 'Assinar', highlights: [] }],
      generatedAt: '2026-08-12T00:00:00Z', state: 'available',
    });
    commercialApi.preview.mockResolvedValue({
      requestType: 'change_base_plan', targetSku: 'crm_advanced', contract: {},
      revision: { currentVersion: 3, targetVersion: 4, currentPrice: { amountDueCents: 26900 }, price: { amountDueCents: 67900 }, warnings: [], blockers: [], eligible: true },
      warnings: [], eligible: true,
    });
    commercialApi.checkout.mockReturnValue(new Promise(() => {}));

    renderPanel(true);
    fireEvent.click(await screen.findByRole('combobox', { name: 'Novo plano' }));
    fireEvent.click(await screen.findByText(/Advanced/));
    fireEvent.change(screen.getByPlaceholderText(/Explique a necessidade/), { target: { value: 'Contratação aprovada pelo titular.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Gerar prévia' }));
    await screen.findByText('Prévia validada');
    fireEvent.click(screen.getByRole('button', { name: 'Ir para pagamento seguro' }));

    await waitFor(() => expect(commercialApi.checkout).toHaveBeenCalledWith({
      targetSku: 'crm_advanced', expectedContractVersion: 3,
      reason: 'Contratação aprovada pelo titular.',
      provider: 'stripe',
    }));
    expect(commercialApi.create).not.toHaveBeenCalled();
  });

  it('routes provider-managed Core plan changes to Stripe without creating a local request', async () => {
    commercialApi.account.mockResolvedValue({
      contract: {
        id: 'contract-1', workspaceId: 'workspace-1', workspaceLabel: 'Escritório',
        status: 'active', currency: 'BRL', version: 7,
        lines: [{ sku: 'crm_advanced', contractedPriceCents: 67900, billingInterval: 'monthly', status: 'active' }],
        price: { listPriceCents: 67900, contractedPriceCents: 67900, recurringPriceCents: 67900, invoiceCreditCents: 0, amountDueCents: 67900 },
        createdAt: '2026-07-01T00:00:00Z', updatedAt: '2026-08-01T00:00:00Z',
      },
      requests: [], automaticCheckoutAvailable: true, billingPortalAvailable: true,
      providerSubscriptionLinked: true, providerSubscriptionStatus: 'active',
      providerState: 'configured', providerMessage: 'Assinatura vinculada ao Stripe.',
      activeProvider: 'stripe', availableProviders: ['stripe'],
    });
    commercialApi.catalog.mockResolvedValue({
      items: [{ sku: 'crm_evolution', name: 'Evolution', description: null, family: 'crm', kind: 'base_plan', isRecommended: false, isQuoteOnly: false, priceMonthlyCents: 124900, currency: 'BRL', cta: 'Assinar', highlights: [] }],
      generatedAt: '2026-08-12T00:00:00Z', state: 'available',
    });
    commercialApi.billingPortal.mockReturnValue(new Promise(() => {}));

    renderPanel(true);
    fireEvent.click(await screen.findByRole('combobox', { name: 'Novo plano' }));
    fireEvent.click(await screen.findByText(/Evolution/));

    expect(screen.getByText('Alteração gerenciada pelo provedor')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Gerar prévia' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Confirmar solicitação' })).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Abrir portal seguro' })[0]);

    await waitFor(() => expect(commercialApi.billingPortal).toHaveBeenCalledTimes(1));
    expect(commercialApi.preview).not.toHaveBeenCalled();
    expect(commercialApi.create).not.toHaveBeenCalled();
  });

  it('never starts a second checkout for an active Asaas subscription', async () => {
    commercialApi.account.mockResolvedValue({
      contract: {
        id: 'contract-1', workspaceId: 'workspace-1', workspaceLabel: 'Escritório',
        status: 'active', currency: 'BRL', version: 8,
        lines: [{ sku: 'crm_start', contractedPriceCents: 26900, billingInterval: 'monthly', status: 'active' }],
        price: { listPriceCents: 26900, contractedPriceCents: 26900, recurringPriceCents: 26900, invoiceCreditCents: 0, amountDueCents: 26900 },
        createdAt: '2026-07-01T00:00:00Z', updatedAt: '2026-08-01T00:00:00Z',
      },
      requests: [], automaticCheckoutAvailable: true, billingPortalAvailable: false,
      providerSubscriptionLinked: true, providerSubscriptionStatus: 'active',
      providerState: 'configured', providerMessage: 'Assinatura vinculada ao Asaas.',
      activeProvider: 'asaas', availableProviders: ['stripe', 'asaas', 'mercado_pago'],
    });
    commercialApi.catalog.mockResolvedValue({
      items: [{ sku: 'crm_advanced', name: 'Advanced', description: null, family: 'crm', kind: 'base_plan', isRecommended: true, isQuoteOnly: false, priceMonthlyCents: 67900, currency: 'BRL', cta: 'Assinar', highlights: [] }],
      generatedAt: '2026-08-12T00:00:00Z', state: 'available',
    });
    commercialApi.preview.mockResolvedValue({
      requestType: 'change_base_plan', targetSku: 'crm_advanced', contract: {},
      revision: { currentVersion: 8, targetVersion: 9, currentPrice: { amountDueCents: 26900 }, price: { amountDueCents: 67900 }, warnings: [], blockers: [], eligible: true },
      warnings: [], eligible: true,
    });
    commercialApi.create.mockReturnValue(new Promise(() => {}));

    renderPanel(true);
    fireEvent.click(await screen.findByRole('combobox', { name: 'Novo plano' }));
    fireEvent.click(await screen.findByText(/Advanced/));
    fireEvent.change(screen.getByPlaceholderText(/Explique a necessidade/), { target: { value: 'Upgrade autorizado pelo titular.' } });

    expect(screen.getByText('Alteração gerenciada pelo provedor')).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: 'Meio de pagamento da assinatura' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Ir para pagamento seguro' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Gerar prévia' }));
    await screen.findByText('Prévia validada');
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar solicitação' }));

    await waitFor(() => expect(commercialApi.create).toHaveBeenCalledWith({
      requestType: 'change_base_plan', targetSku: 'crm_advanced', expectedContractVersion: 8,
      reason: 'Upgrade autorizado pelo titular.',
    }));
    expect(commercialApi.checkout).not.toHaveBeenCalled();
  });

  it('shows the real local lifecycle and changes plan through the typed API', async () => {
    billingApi.portal.mockResolvedValue(portal);
    billingApi.intents.mockResolvedValue([]);
    billingApi.catalog.mockResolvedValue([
      {
        id: 'plan-starter', name: 'Starter', tier: 'starter', priceMonthlyCents: 29700,
        priceAnnuallyCents: 285120, currency: 'BRL', isActive: true, isPublic: true,
        sortOrder: 1, entitlements,
      },
      {
        id: 'plan-pro', name: 'Pro', tier: 'pro', priceMonthlyCents: 69700,
        priceAnnuallyCents: 669120, currency: 'BRL', isActive: true, isPublic: true,
        sortOrder: 2, entitlements,
      },
      {
        id: 'plan-business', name: 'Business', tier: 'business', priceMonthlyCents: 149700,
        priceAnnuallyCents: 1437120, currency: 'BRL', isActive: true, isPublic: true,
        sortOrder: 3, entitlements,
      },
    ]);
    billingApi.command.mockResolvedValue({ action: 'upgrade', replayed: false, intent: {}, portal });
    billingApi.exportIntent.mockResolvedValue({ action: 'export', replayed: false, intent: {}, portal });

    renderPanel(false);

    expect((await screen.findAllByText(/697,00/)).length).toBeGreaterThan(0);
    expect(screen.getByText('0 efeitos externos')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Fazer upgrade' }));
    await waitFor(() => expect(billingApi.command).toHaveBeenCalledWith('upgrade', {
      planTier: 'business',
      interval: 'monthly',
    }));

    fireEvent.click(screen.getByRole('button', { name: 'Preparar exportação' }));
    await waitFor(() => expect(billingApi.exportIntent).toHaveBeenCalledWith({
      exportFormat: 'zip',
      scopes: ['workspace', 'users', 'cases', 'documents', 'audit'],
      reason: 'Exportação solicitada pelo administrador do workspace',
    }));
  });
});
