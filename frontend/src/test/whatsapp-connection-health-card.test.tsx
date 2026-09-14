/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { WhatsAppConnectionHealthCard } from '@/klm/components/WhatsAppConnectionHealthCard';
import * as whatsappService from '@/services/whatsapp';
import type { WhatsAppConnectionHealth } from '@/types';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

function health(overrides: Partial<WhatsAppConnectionHealth>): WhatsAppConnectionHealth {
  return {
    connectionId: 'conn-1',
    tier: 'tier_250',
    dailyLimit: 250,
    dailyUsed: 0,
    dailyRemaining: 250,
    windowStartedAt: '2026-07-23T12:00:00Z',
    qualityRating: null,
    rampUpStage: 0,
    lastSyncedAt: null,
    syncedFromMeta: false,
    syncMessage: 'Sincronização com a Meta desabilitada neste ambiente; exibindo apenas dados locais do KLM.',
    honest: true,
    ...overrides,
  };
}

function renderCard(connectionId = 'conn-1') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <WhatsAppConnectionHealthCard connectionId={connectionId} />
    </QueryClientProvider>,
  );
}

describe('WhatsAppConnectionHealthCard', () => {
  it('shows a loading state while fetching', async () => {
    vi.spyOn(whatsappService, 'fetchWhatsAppConnectionHealth').mockImplementation(
      () => new Promise(() => {}),
    );
    renderCard();
    expect(screen.getByText(/Carregando saúde do número/)).toBeInTheDocument();
  });

  it('shows an empty state when the query fails', async () => {
    vi.spyOn(whatsappService, 'fetchWhatsAppConnectionHealth').mockRejectedValue(new Error('boom'));
    renderCard();
    expect(await screen.findByText(/Sem dados de saúde do número/)).toBeInTheDocument();
  });

  it('renders tier usage, quality badge and the honest local-data notice when not synced', async () => {
    vi.spyOn(whatsappService, 'fetchWhatsAppConnectionHealth').mockResolvedValue(
      health({ dailyUsed: 120, dailyRemaining: 130, qualityRating: 'amarelo' }),
    );
    renderCard();

    expect(await screen.findByText(/Qualidade média/)).toBeInTheDocument();
    expect(screen.getByText(/250 destinatários\/dia/)).toBeInTheDocument();
    expect(screen.getByText(/120 de 250 destinatários únicos usados hoje \(48%\)/)).toBeInTheDocument();
    expect(screen.getByText(/Sincronização com a Meta desabilitada neste ambiente/)).toBeInTheDocument();
  });

  it('hides the local-data notice once synced from Meta', async () => {
    vi.spyOn(whatsappService, 'fetchWhatsAppConnectionHealth').mockResolvedValue(
      health({ syncedFromMeta: true, qualityRating: 'verde' }),
    );
    renderCard();

    expect(await screen.findByText(/Qualidade alta/)).toBeInTheDocument();
    expect(screen.queryByText(/Sincronização com a Meta desabilitada neste ambiente/)).not.toBeInTheDocument();
  });

  it('renders unlimited tier without a progress bar percentage', async () => {
    vi.spyOn(whatsappService, 'fetchWhatsAppConnectionHealth').mockResolvedValue(
      health({ tier: 'unlimited', dailyLimit: null, dailyRemaining: null, dailyUsed: 5000 }),
    );
    renderCard();

    expect(await screen.findByText(/Ilimitado/)).toBeInTheDocument();
    expect(screen.getByText(/5\.000 destinatários únicos hoje/)).toBeInTheDocument();
  });
});
