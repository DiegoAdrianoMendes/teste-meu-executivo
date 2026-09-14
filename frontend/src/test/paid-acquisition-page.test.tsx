/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import PaidAcquisitionPage from '@/klm/pages/PaidAcquisitionPage';
import {
  klmApi,
  type KlmCampaignDraft,
  type KlmPaidAcquisitionFunnelSummary,
  type KlmPaidAcquisitionSyncStatus,
} from '@/klm/api';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <PaidAcquisitionPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const notSyncedStatus: KlmPaidAcquisitionSyncStatus = {
  gateOpen: false,
  connected: false,
  provider: 'meta_ads',
  lastSyncedAt: null,
  syncedCampaigns: 0,
  syncedReceipts: 0,
  lastReceiptSource: null,
  state: 'not_synced',
  message: 'Nenhum sync do Meta Ads registrado ainda.',
};

const emptyFunnel: KlmPaidAcquisitionFunnelSummary = {
  dateFrom: '2026-06-23',
  dateTo: '2026-07-23',
  rows: [],
  totals: {
    campaignKey: '__totals__',
    campaignExternalId: null,
    campaignName: 'Total',
    provider: 'meta_ads',
    channelHint: null,
    spend: 0,
    spendCurrency: 'BRL',
    spendOrigin: 'sem_dado',
    conversations: 0,
    qualifiedLeads: 0,
    contracts: 0,
    revenue: 0,
    cpl: null,
    cac: null,
    roas: null,
  },
};

describe('PaidAcquisitionPage', () => {
  it('renders empty states honestly when nothing is synced yet', async () => {
    vi.spyOn(klmApi, 'paidAcquisitionSyncStatus').mockResolvedValue(notSyncedStatus);
    vi.spyOn(klmApi, 'paidAcquisitionCampaigns').mockResolvedValue([]);
    vi.spyOn(klmApi, 'paidAcquisitionFunnelSummary').mockResolvedValue(emptyFunnel);

    renderPage();

    expect(await screen.findByText('Aquisição Paga')).toBeInTheDocument();
    expect(await screen.findByText('Nenhuma sincronização ainda')).toBeInTheDocument();
    expect(await screen.findByText('Nenhuma campanha com dado no período')).toBeInTheDocument();
    expect(await screen.findByText('Nenhum rascunho ainda. Crie uma campanha para começar.')).toBeInTheDocument();
  });

  it('renders the funnel table with CPL/CAC/ROAS once data exists', async () => {
    vi.spyOn(klmApi, 'paidAcquisitionSyncStatus').mockResolvedValue({
      ...notSyncedStatus,
      connected: true,
      state: 'synced_manual',
      syncedCampaigns: 1,
      message: 'Sincronização honesta a partir de snapshot local.',
    });
    vi.spyOn(klmApi, 'paidAcquisitionCampaigns').mockResolvedValue([]);
    vi.spyOn(klmApi, 'paidAcquisitionFunnelSummary').mockResolvedValue({
      dateFrom: '2026-06-23',
      dateTo: '2026-07-23',
      rows: [
        {
          campaignKey: 'cmp-1',
          campaignExternalId: 'cmp-1',
          campaignName: 'Campanha CTWA teste',
          provider: 'meta_ads',
          channelHint: null,
          spend: 500,
          spendCurrency: 'BRL',
          spendOrigin: 'manual',
          conversations: 2,
          qualifiedLeads: 1,
          contracts: 1,
          revenue: 3000,
          cpl: 250,
          cac: 500,
          roas: 6,
        },
      ],
      totals: {
        ...emptyFunnel.totals,
        spend: 500,
        conversations: 2,
        qualifiedLeads: 1,
        contracts: 1,
        revenue: 3000,
        cpl: 250,
        cac: 500,
        roas: 6,
      },
    });

    renderPage();

    expect(await screen.findByText('Campanha CTWA teste')).toBeInTheDocument();
    expect(await screen.findByText('Origem: snapshot manual')).toBeInTheDocument();
    expect(screen.getAllByText(/6,00x|6\.00x/).length).toBeGreaterThan(0);
  });

  it('validates the campaign builder and blocks submit without an active connection', async () => {
    vi.spyOn(klmApi, 'paidAcquisitionSyncStatus').mockResolvedValue(notSyncedStatus);
    vi.spyOn(klmApi, 'paidAcquisitionCampaigns').mockResolvedValue([]);
    vi.spyOn(klmApi, 'paidAcquisitionFunnelSummary').mockResolvedValue(emptyFunnel);
    vi.spyOn(klmApi, 'omnichannelConnections').mockResolvedValue([]);
    const prepareSpy = vi.spyOn(klmApi, 'prepareCampaignDraft');

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /nova campanha/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/Nenhuma conexão ativa para Click-to-WhatsApp/i),
      ).toBeInTheDocument();
    });

    const submitButton = screen.getByRole('button', { name: /enviar para aprovação/i });
    expect(submitButton).toBeDisabled();
    fireEvent.click(submitButton);
    expect(prepareSpy).not.toHaveBeenCalled();
  });

  it('submits the campaign draft for approval once the form is valid', async () => {
    vi.spyOn(klmApi, 'paidAcquisitionSyncStatus').mockResolvedValue(notSyncedStatus);
    vi.spyOn(klmApi, 'paidAcquisitionCampaigns').mockResolvedValue([]);
    vi.spyOn(klmApi, 'paidAcquisitionFunnelSummary').mockResolvedValue(emptyFunnel);
    vi.spyOn(klmApi, 'omnichannelConnections').mockResolvedValue([
      {
        id: 'conn-1',
        workspaceId: 'ws-1',
        channel: 'whatsapp',
        provider: 'whatsapp_cloud_api',
        connectionKey: 'wa-1',
        displayName: 'WhatsApp Principal',
        externalAccountId: 'acct-1',
        transportMode: 'official_api',
        status: 'connected',
        isDefault: true,
        capabilities: {},
        limits: {},
        routingPolicy: {},
        health: {},
        configuration: {},
        credentialReference: null,
        webhookState: 'not_configured',
        version: 1,
        verifiedAt: null,
        connectedAt: null,
      } as never,
    ]);
    const draft: KlmCampaignDraft = {
      id: 'draft-1',
      workspaceId: 'ws-1',
      channel: 'ctwa',
      channelLabel: 'Click-to-WhatsApp',
      status: 'awaiting_approval',
      provider: 'meta_ads',
      marketingApiPayload: { campaign: { name: 'Campanha via teste' } },
      reason: 'Rascunho aguardando aprovação humana.',
      requestedByUserId: 'user-1',
      createdAt: '2026-07-23T12:00:00Z',
      updatedAt: '2026-07-23T12:00:00Z',
    };
    const prepareSpy = vi.spyOn(klmApi, 'prepareCampaignDraft').mockResolvedValue(draft);

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /nova campanha/i }));
    await screen.findByText('WhatsApp Principal');

    fireEvent.change(screen.getByPlaceholderText('Ex.: CTWA — Direito de Família'), {
      target: { value: 'Campanha via teste' },
    });
    fireEvent.change(screen.getByPlaceholderText('150.00'), { target: { value: '100' } });
    fireEvent.change(screen.getByPlaceholderText('Fale com um advogado agora'), {
      target: { value: 'Fale agora' },
    });
    fireEvent.change(screen.getByPlaceholderText('Atendimento imediato via WhatsApp.'), {
      target: { value: 'Resposta rápida.' },
    });
    fireEvent.change(screen.getByPlaceholderText('Olá! Vi seu anúncio, posso ajudar?'), {
      target: { value: 'Olá! Posso ajudar?' },
    });

    const submitButton = await screen.findByRole('button', { name: /enviar para aprovação/i });
    await waitFor(() => expect(submitButton).not.toBeDisabled());
    fireEvent.click(submitButton);

    await waitFor(() => expect(prepareSpy).toHaveBeenCalledTimes(1));
    const callArgs = prepareSpy.mock.calls[0][0];
    expect(callArgs.channel).toBe('ctwa');
    expect(callArgs.campaignName).toBe('Campanha via teste');
    // Sem interação explícita no seletor, o front não força um connectionId:
    // o backend resolve a conexão padrão ativa do canal quando omitido.
    expect(callArgs.connectionId).toBeUndefined();
  });
});
