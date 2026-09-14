/** @vitest-environment jsdom */
import { cleanup, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { MetaChannelStudio } from '@/components/social/MetaChannelStudio';

// Origem: docs/PLANO_META_MAXIMO_2026-07-23.md seção 5 (Fase 4) item 3 e 6;
// docs/AUDITORIA_META_CANAIS_2026-07-23.md achados Messenger (MetaChannelStudio.tsx:1131-1177
// promete "broadcast quando a política permitir" com policy_summary digitado à mão).

const metaChannelsApi = vi.hoisted(() => ({
  fetchMetaConnections: vi.fn(),
  fetchMetaConversations: vi.fn(),
  fetchMetaComments: vi.fn(),
  fetchApprovedMessages: vi.fn(),
  fetchAutomationRules: vi.fn(),
  fetchMetaCampaigns: vi.fn(),
  createMetaConnection: vi.fn(),
  createMetaConversation: vi.fn(),
  createApprovedMessage: vi.fn(),
  createAutomationRule: vi.fn(),
  createMetaCampaign: vi.fn(),
  createMetaNote: vi.fn(),
  fetchMetaConversation: vi.fn(),
  fetchMetaDrawer: vi.fn(),
  generateMetaCopilot: vi.fn(),
  moveMetaConversationStage: vi.fn(),
  refreshMetaFollowStatus: vi.fn(),
  sendMetaMessage: vi.fn(),
  updateMetaMatter: vi.fn(),
}));

vi.mock('@/services/metaChannels', async () => {
  const actual = await vi.importActual<typeof import('@/services/metaChannels')>('@/services/metaChannels');
  return {
    ...actual,
    fetchMetaConnections: metaChannelsApi.fetchMetaConnections,
    fetchMetaConversations: metaChannelsApi.fetchMetaConversations,
    fetchMetaComments: metaChannelsApi.fetchMetaComments,
    fetchApprovedMessages: metaChannelsApi.fetchApprovedMessages,
    fetchAutomationRules: metaChannelsApi.fetchAutomationRules,
    fetchMetaCampaigns: metaChannelsApi.fetchMetaCampaigns,
    createMetaConnection: metaChannelsApi.createMetaConnection,
    createMetaConversation: metaChannelsApi.createMetaConversation,
    createApprovedMessage: metaChannelsApi.createApprovedMessage,
    createAutomationRule: metaChannelsApi.createAutomationRule,
    createMetaCampaign: metaChannelsApi.createMetaCampaign,
    createMetaNote: metaChannelsApi.createMetaNote,
    fetchMetaConversation: metaChannelsApi.fetchMetaConversation,
    fetchMetaDrawer: metaChannelsApi.fetchMetaDrawer,
    generateMetaCopilot: metaChannelsApi.generateMetaCopilot,
    moveMetaConversationStage: metaChannelsApi.moveMetaConversationStage,
    refreshMetaFollowStatus: metaChannelsApi.refreshMetaFollowStatus,
    sendMetaMessage: metaChannelsApi.sendMetaMessage,
    updateMetaMatter: metaChannelsApi.updateMetaMatter,
  };
});

function renderStudio(mode: 'entrypoints' | 'connections' = 'entrypoints') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MetaChannelStudio channel="messenger" mode={mode} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('MetaChannelStudio — Messenger reengajamento pós-2026', () => {
  it('re-rotula a superfície de campanhas, some com "broadcast" e explica as regras do BR', async () => {
    metaChannelsApi.fetchMetaConnections.mockResolvedValue([
      {
        id: 'conn-1',
        channelType: 'MESSENGER',
        accountName: 'Página Oficial',
        username: 'klmadvogados.page',
        externalAccountId: 'page-001',
        scopes: [],
        capabilityMatrix: {
          pageMessaging: { supported: false, reason: 'Conexão ainda não verificada' },
          linksInText: { supported: true, reason: 'Suportado localmente' },
        },
        status: 'connected',
        webhookRegistered: false,
        updatedAt: '2026-07-23T00:00:00Z',
      },
    ]);
    metaChannelsApi.fetchMetaConversations.mockResolvedValue([]);
    metaChannelsApi.fetchApprovedMessages.mockResolvedValue([]);
    metaChannelsApi.fetchAutomationRules.mockResolvedValue([]);
    metaChannelsApi.fetchMetaCampaigns.mockResolvedValue([]);

    renderStudio('entrypoints');

    expect(await screen.findByText('Reengajamento (janela de 24h) e mídia paga')).toBeInTheDocument();
    expect(screen.queryByText(/broadcast/i)).not.toBeInTheDocument();

    expect(screen.getByText('Regras do BR pós-2026 para Messenger/Instagram')).toBeInTheDocument();
    expect(screen.getByText(/Tags de reengajamento/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Saiba mais na documentação oficial da Meta/i })).toHaveAttribute(
      'href',
      expect.stringContaining('developers.facebook.com'),
    );

    // Campos manuais de elegíveis/bloqueados viram somente leitura.
    const eligibleInput = screen.getByLabelText(/Elegíveis \(calculado pelo backend\)/i);
    const blockedInput = screen.getByLabelText(/Bloqueados \(calculado pelo backend\)/i);
    expect(eligibleInput).toBeDisabled();
    expect(blockedInput).toBeDisabled();
    expect(
      screen.getByText(/Elegíveis\/bloqueados são somente leitura/i),
    ).toBeInTheDocument();

    // Badge de gate persistente (não só toast pontual).
    expect(screen.getByText('APIs externas bloqueadas')).toBeInTheDocument();
  });

  it('mostra a capability matrix honesta com razão de não suportado, sem esconder chaves', async () => {
    metaChannelsApi.fetchMetaConnections.mockResolvedValue([
      {
        id: 'conn-2',
        channelType: 'MESSENGER',
        accountName: 'Página Nova',
        username: 'klmadvogados.nova',
        externalAccountId: 'page-002',
        scopes: [],
        capabilityMatrix: {
          pageMessaging: { supported: false, reason: 'Conexão ainda não verificada (sem webhook confirmado pela Meta); capacidade indisponível.' },
          pageComments: { supported: false, reason: 'Conexão ainda não verificada (sem webhook confirmado pela Meta); capacidade indisponível.' },
          linksInText: { supported: true, reason: 'Suportado localmente (inbox manual/CRM); não depende de transporte externo da Meta.' },
        },
        status: 'pending',
        webhookRegistered: false,
        updatedAt: '2026-07-23T00:00:00Z',
      },
    ]);
    metaChannelsApi.fetchMetaConversations.mockResolvedValue([]);
    metaChannelsApi.fetchApprovedMessages.mockResolvedValue([]);
    metaChannelsApi.fetchAutomationRules.mockResolvedValue([]);
    metaChannelsApi.fetchMetaCampaigns.mockResolvedValue([]);

    renderStudio('connections');

    const connectionName = await screen.findByText('Página Nova');
    const connectionCard = connectionName.closest('div.rounded-lg.border') as HTMLElement;
    const pageMessagingBadge = within(connectionCard).getByText('pageMessaging');
    const linksBadge = within(connectionCard).getByText('linksInText');
    expect(pageMessagingBadge).toHaveAttribute('title', expect.stringContaining('não verificada'));
    expect(linksBadge).toHaveAttribute('title', expect.stringContaining('Suportado localmente'));
  });
});
