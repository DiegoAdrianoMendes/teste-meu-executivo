/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

const navigateMock = vi.hoisted(() => vi.fn());
const omnichannelEventsMock = vi.hoisted(() => vi.fn());
const omnichannelConnectionsMock = vi.hoisted(() => vi.fn());

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => navigateMock };
});

vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({ can: () => true }),
}));

vi.mock('@/klm/api', () => ({
  klmApi: {
    omnichannelEvents: omnichannelEventsMock,
    omnichannelConnections: omnichannelConnectionsMock,
    omnichannelAutomationRules: vi.fn().mockResolvedValue([]),
    createOmnichannelAction: vi.fn(),
    createOmnichannelAutomationRule: vi.fn(),
    updateOmnichannelAutomationRule: vi.fn(),
    validateOmnichannelConnection: vi.fn(),
    createOmnichannelConnection: vi.fn(),
  },
}));

import SocialCommentsPage from '@/klm/pages/SocialCommentsPage';

const instagramEvent = {
  event: {
    id: 'event-123',
    threadId: 'thread-123',
    parentEventId: null,
    provider: 'meta_instagram',
    connectionKey: 'instagram:principal',
    eventType: 'post_comment',
    externalEventId: 'comment-123',
    actorExternalId: 'ig-user-1',
    visibility: 'public',
    publicUrl: null,
    actionability: 'reply_required',
    deadlineAt: null,
    occurredAt: '2026-07-20T18:00:00Z',
    receivedAt: '2026-07-20T18:00:01Z',
    correlation: {},
    eventPayload: {},
    eventMetadata: {},
  },
  thread: {
    id: 'thread-123',
    workspaceId: 'workspace-1',
    matterId: 'matter-123',
    clientId: 'client-123',
    assignedHumanId: null,
    primaryChannel: 'instagram',
    status: 'open',
    subject: 'negativa',
    objectName: 'medicamento',
    ownerAgentKey: 'sdr',
    score: 91,
    unreadCount: 1,
    lastMessagePreview: 'Preciso deste medicamento',
    lastMessageAt: '2026-07-20T18:00:00Z',
    slaDueAt: null,
    threadMetadata: {},
  },
  identity: {
    id: 'identity-1',
    clientId: 'client-123',
    matterId: 'matter-123',
    displayName: 'Ana Paula',
    canonicalPhone: '+5511999990000',
    canonicalEmail: null,
    identityState: 'verified',
    source: 'instagram',
    mergeVersion: 1,
    identityMetadata: {},
  },
  message: {
    id: 'message-123',
    threadId: 'thread-123',
    matterId: 'matter-123',
    channel: 'instagram',
    direction: 'inbound',
    authorType: 'client',
    authorLabel: 'Ana Paula',
    body: 'Preciso deste medicamento',
    deliveryStatus: 'received',
    occurredAt: '2026-07-20T18:00:00Z',
    messageMetadata: {},
  },
  connection: {
    id: 'connection-1',
    displayName: 'Instagram principal',
    externalAccountId: 'ig-klm',
    transportMode: 'official_api',
    status: 'configured_local',
    isDefault: true,
    capabilities: {
      public_reply: { supported: true },
      private_reply: { supported: true },
      comment_like: { supported: true },
      comment_hide: { supported: true },
      comment_delete: { supported: true },
    },
  },
  channel: 'instagram',
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SocialCommentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

beforeEach(() => {
  omnichannelConnectionsMock.mockResolvedValue([]);
});

describe('SocialCommentsPage', () => {
  it('opens a public comment in the same canonical conversation', async () => {
    omnichannelEventsMock.mockResolvedValue({
      items: [instagramEvent],
      hasMore: false,
      generatedAt: '2026-07-20T18:01:00Z',
    });
    renderPage();

    expect((await screen.findAllByText('Ana Paula')).length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('Preciso deste medicamento').length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: /Abrir atendimento/i }));

    expect(navigateMock).toHaveBeenCalledWith(
      '/app/conversas?thread=thread-123&matter=matter-123',
    );
  });

  it('marks TikTok as pending without requesting the channel', async () => {
    omnichannelEventsMock.mockResolvedValue({ items: [], hasMore: false, generatedAt: '2026-07-20T18:01:00Z' });
    renderPage();
    await screen.findByText('Nenhum comentário canônico neste filtro.');

    const tiktok = screen.getByRole('button', { name: /TikTok.*em breve/i });
    expect(tiktok).toBeDisabled();
    expect(tiktok).toHaveAttribute('title', 'TikTok ficará disponível em uma próxima etapa');
  });

  it('offers a public reply followed by a configurable private sequence', async () => {
    omnichannelEventsMock.mockResolvedValue({ items: [instagramEvent], hasMore: false, generatedAt: '2026-07-20T18:01:00Z' });
    renderPage();

    expect(await screen.findByRole('group', { name: 'Tipo de resposta' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Comentário + sequência' })).toBeInTheDocument();
    expect(screen.getByLabelText('Mensagem privada 1')).toBeInTheDocument();
    expect(screen.getByLabelText('Mensagem privada 2')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Mensagem' }));
    expect(screen.getByLabelText('Mensagem privada 3')).toBeInTheDocument();
  });

  it('keeps ad accounts out of social accounts and opens the native account settings', async () => {
    omnichannelEventsMock.mockResolvedValue({ items: [], hasMore: false, generatedAt: '2026-07-20T18:01:00Z' });
    const common = {
      workspaceId: 'workspace-1',
      connectionKey: 'fixture',
      externalAccountId: null,
      status: 'configured_local',
      isDefault: false,
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
      lastSyncedAt: null,
      createdAt: '2026-07-20T18:00:00Z',
      updatedAt: '2026-07-20T18:00:00Z',
    };
    omnichannelConnectionsMock.mockResolvedValue([
      { ...common, id: 'ads-1', displayName: 'Meta Ads principal', channel: 'facebook', provider: 'meta_ads', transportMode: 'official_api' },
      { ...common, id: 'social-1', displayName: 'Instagram principal', channel: 'instagram', provider: 'meta_instagram', transportMode: 'official_api' },
    ]);
    renderPage();

    fireEvent.click(screen.getByRole('button', { name: 'Conexões' }));
    expect(await screen.findByText('Instagram principal')).toBeInTheDocument();
    expect(screen.queryByText('Meta Ads principal')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Configurar conta oficial/i }));
    expect(navigateMock).toHaveBeenCalledWith('/app/configuracoes/avancadas?tab=integracoes');
  });
});
