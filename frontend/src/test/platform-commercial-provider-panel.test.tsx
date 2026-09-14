/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const commercialApi = vi.hoisted(() => ({
  bindings: vi.fn(),
  catalog: vi.fn(),
  configure: vi.fn(),
}));

vi.mock('@/services/commercial-self-service', () => ({
  configurePlatformCommercialProviderPriceBinding: commercialApi.configure,
  fetchPlatformCommercialProviderPriceBindings: commercialApi.bindings,
  fetchPublicCommercialCatalog: commercialApi.catalog,
}));

import { PlatformCommercialProviderPanel } from '@/components/platform/PlatformCommercialProviderPanel';

function renderPanel(isPlatformRoot: boolean) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PlatformCommercialProviderPanel isPlatformRoot={isPlatformRoot} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  Object.values(commercialApi).forEach((mock) => mock.mockReset());
  commercialApi.bindings.mockResolvedValue({
    items: [{
      id: 'binding-1', sku: 'crm_advanced', offerVersion: 3, provider: 'stripe',
      billingInterval: 'monthly', providerPriceId: 'price_advanced_v3', status: 'active',
      version: 1, updatedAt: '2026-08-12T12:00:00Z',
    }],
  });
  commercialApi.catalog.mockResolvedValue({
    items: [{
      sku: 'crm_advanced', name: 'Advanced', description: null, family: 'crm',
      kind: 'base_plan', isRecommended: true, isQuoteOnly: false,
      priceMonthlyCents: 67_900, currency: 'BRL', cta: 'Assinar', highlights: [],
    }],
    generatedAt: '2026-08-12T12:00:00Z', state: 'available',
  });
  commercialApi.configure.mockResolvedValue({});
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('PlatformCommercialProviderPanel', () => {
  it('lets root bind a published offer version with an explicit reason', async () => {
    renderPanel(true);

    expect(await screen.findByText('crm_advanced · versão 3')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('combobox', { name: 'Oferta publicada' }));
    fireEvent.click(await screen.findByText('Advanced · crm_advanced'));
    fireEvent.change(screen.getByLabelText('Price ID publicado no Stripe'), {
      target: { value: 'price_advanced_v4' },
    });
    fireEvent.change(screen.getByLabelText('Motivo'), {
      target: { value: 'Publicar preço mensal aprovado.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar vínculo' }));

    await waitFor(() => expect(commercialApi.configure).toHaveBeenCalledWith({
      sku: 'crm_advanced',
      provider: 'stripe',
      providerPriceId: 'price_advanced_v4',
      reason: 'Publicar preço mensal aprovado.',
    }));
  });

  it('keeps provider mutation controls unavailable to non-root staff', async () => {
    renderPanel(false);

    expect(await screen.findByText('crm_advanced · versão 3')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Salvar vínculo' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Price ID publicado no Stripe')).not.toBeInTheDocument();
  });
});
