/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import ConversationsPage from '@/klm/pages/ConversationsPage';
import {
  klmApi,
  type KlmConversationCapabilities,
  type KlmMessage,
  type KlmOmnichannelContext,
  type KlmThread,
} from '@/klm/api';
import { mockEmptyCapabilityQueries } from '@/test/capability-fixtures';

const permissionMock = vi.hoisted(() => vi.fn((_module: string, _action: string) => true));

vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({
    can: permissionMock,
    state: {
      currentUser: {
        id: 'manager-1',
        name: 'Marina Gestora',
        email: 'marina@klmadvogados.com.br',
        role: 'workspace_admin',
      },
      session: { activeRole: 'workspace_admin' },
    },
  }),
  useOptionalAppStore: () => ({ can: permissionMock, state: {} }),
}));

const threadId = 'thread-collaboration-1';
const matterId = 'matter-collaboration-1';
const staffId = 'staff-responsible-1';

const thread: KlmThread = {
  id: threadId,
  workspaceId: 'workspace-local',
  matterId,
  clientId: 'client-1',
  assignedHumanId: staffId,
  primaryChannel: 'whatsapp',
  status: 'human_active',
  subject: 'medicamento_alto_custo',
  objectName: 'Zolgensma',
  ownerAgentKey: null,
  score: 90,
  unreadCount: 0,
  lastMessagePreview: 'Preciso de ajuda.',
  lastMessageAt: '2026-07-22T12:00:00Z',
  slaDueAt: null,
  threadMetadata: {},
};

const inboundMessage: KlmMessage = {
  id: 'message-client-1',
  threadId,
  matterId,
  channel: 'whatsapp',
  direction: 'inbound',
  authorType: 'client',
  authorLabel: 'Ana Paula Ribeiro',
  body: 'O plano voltou a negar o medicamento.',
  deliveryStatus: 'received',
  occurredAt: '2026-07-22T12:00:00Z',
  messageMetadata: {},
};

function context(activeThread: KlmThread, groupCapability = false, transportMode = 'cloud_api'): KlmOmnichannelContext {
  return {
    thread: activeThread,
    identity: null,
    channels: [{
      endpointId: 'endpoint-1', bindingId: 'binding-1', channel: 'whatsapp', provider: 'meta_cloud',
      connectionId: 'connection-1', connectionKey: 'whatsapp-cloud', connectionName: 'WhatsApp comercial',
      connectionStatus: 'configured_local', transportMode, externalAccountId: 'account-1',
      address: '+5561999999999', externalUserId: 'wa-user-1', consentState: 'granted', blockedAt: null,
      lastSeenAt: null, bindingCount: 1,
      capabilities: {
        outbound_messages: { supported: true, enabled: true, state: 'available' },
        ...(groupCapability ? { groupMessaging: { supported: true, enabled: true, state: 'available' } } : {}),
      },
    }],
    bindings: [], actionIntents: [], scheduledMessages: [], calls: [], automationRuns: [], timeline: [],
    externalGateOpen: false, generatedAt: '2026-07-22T12:00:00Z',
  };
}

function collaborationCapabilities(): KlmConversationCapabilities {
  return {
    workspaceId: 'workspace-local', threadId, groupId: null, localBridgeGateOpen: true, externalDeliveryGateOpen: false,
    participantCount: 2,
    capabilities: [
      'structured_references', 'user_mentions', 'team_mentions', 'group_participants',
    ].map((capability) => ({
      scope: 'native' as const, capability, status: 'supported' as const, provider: 'klm_native',
      officialCapabilityId: null, providerLinked: true, gateOpen: true, externalEffectVerified: false, reason: 'Fixture capability',
    })),
    observedAt: '2026-07-22T12:00:00Z',
  };
}

function seedPage({
  activeThread = thread,
  groupCapability = false,
  transportMode = 'cloud_api',
}: {
  activeThread?: KlmThread;
  groupCapability?: boolean;
  transportMode?: string;
} = {}) {
  mockEmptyCapabilityQueries();
  vi.spyOn(klmApi, 'threads').mockResolvedValue([activeThread]);
  vi.spyOn(klmApi, 'funnel').mockResolvedValue({
    boardVersion: 0, stages: [{ id: 'triagem_sdr', label: 'Triagem', count: 1 }], metrics: {},
    cards: [{ id: matterId, clientId: 'client-1', name: 'Ana Paula Ribeiro', subject: 'medicamento_alto_custo', object: 'Zolgensma', source: 'manual', owner: 'Marina Gestora', probability: 90, initialFee: 0, projectedRevenue: 0, slaDueAt: null, documents: 0, documentScore: 0, stage: 'triagem_sdr', microStatus: 'qualificado' }],
  });
  vi.spyOn(klmApi, 'followUps').mockResolvedValue([]);
  vi.spyOn(klmApi, 'flows').mockResolvedValue([]);
  vi.spyOn(klmApi, 'flowRuns').mockResolvedValue([]);
  vi.spyOn(klmApi, 'messages').mockResolvedValue([inboundMessage]);
  vi.spyOn(klmApi, 'omnichannelContext').mockResolvedValue(context(activeThread, groupCapability, transportMode));
  vi.spyOn(klmApi, 'omnichannelTeams').mockResolvedValue([{
    id: 'team-health', workspaceId: 'workspace-local', name: 'Equipe Saúde', slug: 'saude', description: 'Cobertura Saúde',
    teamType: 'practice', color: '#1c3254', capacity: 8, routingStrategy: 'manual', schedule: {}, isActive: true,
    memberCount: 4, availableCount: 3, openThreadCount: 1, createdAt: '2026-07-01T00:00:00Z', updatedAt: '2026-07-01T00:00:00Z',
  }]);
  vi.spyOn(klmApi, 'workspaceUsers').mockResolvedValue([{
    id: 'lawyer-2', name: 'Beatriz Advogada', email: 'beatriz@klmadvogados.com.br', phone: null,
    type: 'human', role: 'lawyer', initials: 'BA', color: '#1c3254', avatar: null,
    isActive: true, isInternalStaff: false,
  }]);
  vi.spyOn(klmApi, 'conversationReferences').mockResolvedValue([]);
  vi.spyOn(klmApi, 'conversationParticipants').mockResolvedValue({
    group: activeThread.threadMetadata.groupId ? {
      id: 'group-1', workspaceId: 'workspace-local', threadId, name: 'Grupo Saúde', createdByUserId: 'manager-1', createdAt: '2026-07-01T00:00:00Z',
    } : null,
    participants: [],
  });
  vi.spyOn(klmApi, 'conversationCollaborationCapabilities').mockResolvedValue(collaborationCapabilities());
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/app/conversas']}><ConversationsPage /></MemoryRouter></QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  permissionMock.mockReset();
  permissionMock.mockReturnValue(true);
});

describe('Conversations collaboration and staff availability integration', () => {
  it('keeps staff availability out of the conversation composer', async () => {
    seedPage();
    renderPage();

    await screen.findAllByText('Ana Paula Ribeiro');
    expect(screen.queryByRole('button', { name: 'Operar' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Escala, férias e mensagem de ausência' })).not.toBeInTheDocument();
  });

  it('sends one internal note, then attaches the selected reply and mention through structured collaboration APIs', async () => {
    seedPage();
    const saved: KlmMessage = { ...inboundMessage, id: 'message-note-1', channel: 'internal', direction: 'internal', authorType: 'human', authorLabel: 'Marina Gestora', body: 'Marina, revise a negativa.' };
    const sendInternal = vi.spyOn(klmApi, 'sendInternalMessage').mockResolvedValue(saved);
    const createReference = vi.spyOn(klmApi, 'createConversationReference').mockResolvedValue({ id: 'reference-1', workspaceId: 'workspace-local', threadId, sourceMessageId: saved.id, targetMessageId: inboundMessage.id, referenceType: 'reply', idempotencyKey: 'reference-key', createdByUserId: 'manager-1', createdAt: '2026-07-22T12:01:00Z', mentions: [] });
    const attachMentions = vi.spyOn(klmApi, 'attachConversationMentions');
    renderPage();

    await screen.findByText(inboundMessage.body);
    fireEvent.click(screen.getAllByRole('button', { name: 'Responder' }).at(-1)!);
    fireEvent.click(screen.getByRole('button', { name: 'Nota interna' }));
    fireEvent.click(screen.getByRole('button', { name: 'Mencionar usuário ou equipe' }));
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Selecionar menção' })).getByRole('option', { name: /Equipe Saúde/ }));
    fireEvent.change(screen.getByPlaceholderText('Escreva uma nota privada para agentes e equipe...'), { target: { value: 'Marina, revise a negativa.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar nota interna' }));

    await waitFor(() => expect(sendInternal).toHaveBeenCalledOnce());
    await waitFor(() => expect(createReference).toHaveBeenCalledWith(threadId, expect.objectContaining({
      sourceMessageId: 'message-note-1', targetMessageId: inboundMessage.id, referenceType: 'reply',
      mentions: [{ mentionType: 'team', targetId: 'team-health' }],
    })));
    expect(attachMentions).not.toHaveBeenCalled();
  });

  it('adds a native user or team participant in place and immediately refreshes the participant collection', async () => {
    seedPage();
    const nativeParticipant = {
      id: 'participant-team-health', workspaceId: 'workspace-local', threadId, groupId: 'native-group-1',
      participantType: 'team' as const, participantId: 'team-health', role: 'member' as const,
      resolvedLabel: 'Equipe Saúde', resolutionSource: 'workspace_team', resolvedAt: '2026-07-22T12:02:00Z',
      idempotencyKey: `spa-participant:${threadId}:team:team-health`, createdByUserId: 'manager-1', createdAt: '2026-07-22T12:02:00Z',
    };
    const listParticipants = vi.mocked(klmApi.conversationParticipants)
      .mockResolvedValueOnce({ group: null, participants: [] })
      .mockResolvedValue({
        group: { id: 'native-group-1', workspaceId: 'workspace-local', threadId, name: 'Colaboração interna', createdByUserId: 'manager-1', createdAt: '2026-07-22T12:02:00Z' },
        participants: [nativeParticipant],
      });
    const addParticipant = vi.spyOn(klmApi, 'addConversationParticipant').mockResolvedValue(nativeParticipant);
    renderPage();

    await screen.findByText(inboundMessage.body);
    fireEvent.click(screen.getByRole('button', { name: 'Adicionar participante interno' }));
    const dialog = screen.getByRole('dialog', { name: 'Participantes da conversa' });
    expect(within(dialog).getByRole('option', { name: /Beatriz Advogada/ })).toBeInTheDocument();
    expect(within(dialog).getByRole('option', { name: /Equipe Saúde/ })).toBeInTheDocument();
    expect(within(dialog).getByText(/não altera o grupo oficial do provedor/i)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('option', { name: /Equipe Saúde/ }));

    await waitFor(() => expect(addParticipant).toHaveBeenCalledWith(threadId, {
      participantType: 'team', participantId: 'team-health', role: 'member',
    }));
    await waitFor(() => expect(listParticipants.mock.calls.length).toBeGreaterThanOrEqual(2));
    await waitFor(() => expect(within(dialog).queryByRole('option', { name: /Equipe Saúde/ })).not.toBeInTheDocument());
    expect(within(dialog).getByRole('list', { name: 'Lista de participantes' })).toHaveTextContent('Equipe Saúde');
    expect(await screen.findByText(/foi adicionado à participação interna do Meu Executivo/)).toBeInTheDocument();
  });

  it('does not expose native participant management without the users.manage permission', async () => {
    permissionMock.mockImplementation((module: string, action: string) => module !== 'users' || action !== 'manage');
    seedPage();
    renderPage();

    await screen.findByText(inboundMessage.body);
    fireEvent.click(screen.getByRole('button', { name: /Conversa individual, \d+ participantes/ }));
    const dialog = screen.getByRole('dialog', { name: 'Participantes da conversa' });

    expect(within(dialog).queryByText('Participação interna Meu Executivo')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Adicionar participante interno' })).not.toBeInTheDocument();
    expect(klmApi.workspaceUsers).not.toHaveBeenCalled();
  });

  it('blocks creating a provider-group reply when the selected provider does not prove group messaging capability', async () => {
    seedPage({ activeThread: { ...thread, threadMetadata: { groupId: 'provider-group-1' } } });
    renderPage();

    await screen.findByText(inboundMessage.body);
    const replyFromMessage = screen.getByTitle('A conexão ainda não comprovou a capacidade de resposta em grupo.');
    expect(replyFromMessage).toBeDisabled();
    expect(replyFromMessage).toHaveAttribute('title', 'A conexão ainda não comprovou a capacidade de resposta em grupo.');
  });

  it('allows a provider-group reply only when the Cloud API route proves an enabled group capability', async () => {
    seedPage({
      activeThread: { ...thread, threadMetadata: { groupId: 'provider-group-1' } },
      groupCapability: true,
    });
    renderPage();

    await screen.findByText(inboundMessage.body);
    expect(screen.getByTitle('Responder com referência a esta mensagem')).toBeEnabled();
  });

  it('keeps provider-group replies and official group management blocked in Coexistence even when group capability is advertised', async () => {
    seedPage({
      activeThread: { ...thread, threadMetadata: { groupId: 'provider-group-1' } },
      groupCapability: true,
      transportMode: 'coexistence',
    });
    vi.mocked(klmApi.messages).mockResolvedValue([
      inboundMessage,
      {
        ...inboundMessage,
        id: 'message-internal-1',
        channel: 'internal',
        direction: 'internal',
        authorType: 'human',
        authorLabel: 'Marina Gestora',
        body: 'Nota privada para revisão da equipe.',
        messageMetadata: { visibility: 'internal_only', source: 'staff_note' },
      },
    ]);
    renderPage();

    await screen.findByText(inboundMessage.body);
    const replyFromMessage = screen.getByTitle('Grupos não são habilitados pela API oficial em números no modo Coexistência.');
    expect(replyFromMessage).toBeDisabled();
    expect(screen.getByTitle('Responder com referência a esta nota')).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: /Conversa em grupo, \d+ participantes/ }));
    const providerGate = within(screen.getByRole('dialog', { name: 'Participantes da conversa' }))
      .getByLabelText('Gestão do grupo oficial do provedor');
    expect(providerGate).toHaveTextContent('gate fechado');
    expect(providerGate).toHaveTextContent('Coexistência não oferece gestão de grupos oficiais');
  });
});
