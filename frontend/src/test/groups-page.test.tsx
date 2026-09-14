/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import GroupsPage from '@/klm/pages/GroupsPage';
import {
  klmApi,
  type KlmChannelConnection,
  type KlmMetaGroup,
  type KlmMetaGroupCapability,
  type KlmMetaGroupCapabilityGate,
  type KlmMetaGroupInviteLinkRotation,
  type KlmMetaGroupMessageCollection,
  type KlmMetaGroupParticipant,
  type KlmMetaGroupParticipantCollection,
} from '@/klm/api';

const permissionMock = vi.hoisted(() => vi.fn((_module: string, _action: string) => true));
const toastMock = vi.hoisted(() => vi.fn());

vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({
    can: permissionMock,
    state: { currentUser: { id: 'manager-1', name: 'Marina Gestora', email: 'marina@klmadvogados.com.br' } },
  }),
  useOptionalAppStore: () => ({ can: permissionMock, state: {} }),
}));

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }));

vi.mock('@/services/admin', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/admin')>();
  return { ...actual, fetchMyAccessPackage: async () => null };
});

const connection: KlmChannelConnection = {
  id: 'connection-groups-1',
  workspaceId: 'workspace-local',
  channel: 'whatsapp',
  provider: 'meta_cloud',
  connectionKey: 'whatsapp-primary',
  displayName: 'WhatsApp comercial',
  externalAccountId: 'phone-number-1',
  transportMode: 'cloud_api',
  status: 'configured_local',
  isDefault: true,
  capabilities: {},
  limits: {},
  routingPolicy: {},
  health: {},
  configuration: {},
  credentialReference: null,
  webhookState: 'unverified',
  version: 1,
  verifiedAt: null,
  connectedAt: null,
  lastSyncedAt: null,
  createdAt: '2026-07-22T14:00:00Z',
  updatedAt: '2026-07-22T14:00:00Z',
};

function gate(overrides: Partial<KlmMetaGroupCapabilityGate> = {}): KlmMetaGroupCapabilityGate {
  return {
    workspaceId: 'workspace-local',
    connectionId: connection.id,
    provider: 'meta_cloud',
    productMode: 'cloud_api',
    developerAppState: 'not_ready',
    developerAppReady: false,
    developerAppMissingRequirements: ['developer_app_not_configured'],
    requiredWebhookFields: ['group_lifecycle_update', 'group_participants_update', 'group_settings_update', 'group_status_update'],
    subscribedWebhookFields: [],
    obaEligibilityState: 'unknown',
    obaEvidenceSource: 'unverified',
    obaGateOpen: false,
    productModeSupported: true,
    coexistenceDenied: false,
    businessAppDenied: false,
    multiSolutionDenied: false,
    runtimeExternalActionsEnabled: false,
    externalCapabilityGateOpen: false,
    externalExecutionGateOpen: false,
    externalDispatchImplemented: false,
    localPreparationAvailable: true,
    capabilityDenialReasons: ['developer_app_not_configured', 'oba_eligibility_unknown'],
    executionDenialReasons: ['runtime_external_actions_disabled', 'external_dispatch_not_implemented'],
    limits: { inviteOnly: true, maxParticipants: 8, maxGroupsPerBusinessNumber: 10000 },
    observedAt: '2026-07-22T15:00:00Z',
    ...overrides,
  };
}

function capability(gateValue: KlmMetaGroupCapabilityGate): KlmMetaGroupCapability {
  return {
    id: 'capability-1',
    workspaceId: 'workspace-local',
    connectionId: connection.id,
    configured: gateValue.developerAppReady,
    phoneNumberId: 'phone-number-1',
    wabaId: 'waba-1',
    developerAppConfigured: gateValue.developerAppReady,
    webhookCallbackVerified: gateValue.developerAppReady,
    obaEvidenceReference: gateValue.obaGateOpen ? 'BM-EVIDENCE-2026' : null,
    obaEvidenceRecordedAt: gateValue.obaGateOpen ? '2026-07-22T14:30:00Z' : null,
    evidenceMetadata: {},
    version: 1,
    createdAt: '2026-07-22T14:30:00Z',
    updatedAt: '2026-07-22T14:30:00Z',
    gate: gateValue,
    deduplicated: false,
  };
}

function makeGroup(overrides: Partial<KlmMetaGroup>): KlmMetaGroup {
  const gateValue = gate();
  return {
    id: 'group-1',
    workspaceId: 'workspace-local',
    connectionId: connection.id,
    capabilityId: null,
    createdByUserId: 'manager-1',
    groupKind: 'local_internal',
    isLocalInternal: true,
    isOfficialMetaGroup: false,
    providerGroupId: null,
    providerRequestId: null,
    subject: 'Caso Silva x Unimed',
    description: 'Equipe interna do caso.',
    joinApprovalMode: 'auto_approve',
    state: 'local_ready',
    inviteLink: null,
    inviteLinkSimulated: false,
    participantCount: 1,
    createIdempotencyKey: 'group-key-1',
    requestPayloadSha256: 'sha-1',
    lastLifecycleAt: null,
    lastMessagePreview: 'Audiência marcada para sexta',
    lastMessageAt: '2026-07-25T10:00:00Z',
    pinnedMessage: null,
    version: 1,
    groupMetadata: {},
    createdAt: '2026-07-22T14:00:00Z',
    updatedAt: '2026-07-22T14:00:00Z',
    gate: gateValue,
    ...overrides,
  };
}

function makeParticipant(overrides: Partial<KlmMetaGroupParticipant>): KlmMetaGroupParticipant {
  return {
    id: 'participant-1',
    workspaceId: 'workspace-local',
    groupId: 'group-1',
    providerUserId: 'manager-1',
    waId: null,
    businessScopedUserId: null,
    username: 'Marina Gestora',
    joinRequestId: null,
    state: 'joined',
    source: 'invite_link',
    role: 'admin',
    joinedAt: '2026-07-22T14:00:00Z',
    leftAt: null,
    participantMetadata: {},
    createdAt: '2026-07-22T14:00:00Z',
    updatedAt: '2026-07-22T14:00:00Z',
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <GroupsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const emptyMessages: KlmMetaGroupMessageCollection = { items: [], total: 0 };
const emptyParticipants: KlmMetaGroupParticipantCollection = { items: [], total: 0 };

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  permissionMock.mockReset();
  permissionMock.mockReturnValue(true);
  toastMock.mockReset();
});

describe('GroupsPage', () => {
  it('renders the group list and the selected group thread from mocked data', async () => {
    const internalGroup = makeGroup({ id: 'group-internal-1', subject: 'Caso Silva x Unimed' });
    vi.spyOn(klmApi, 'omnichannelConnections').mockResolvedValue([connection]);
    vi.spyOn(klmApi, 'metaGroups').mockResolvedValue({ items: [internalGroup], total: 1 });
    vi.spyOn(klmApi, 'metaGroupCapability').mockResolvedValue(capability(gate()));
    vi.spyOn(klmApi, 'metaGroupMessages').mockResolvedValue({
      items: [{
        id: 'message-1',
        groupId: internalGroup.id,
        requestedByUserId: 'manager-1',
        text: 'Audiência marcada para sexta',
        message: { type: 'text', text: { body: 'Audiência marcada para sexta' } },
        state: 'local_ready',
        externalDispatchExecuted: false,
        createdAt: '2026-07-25T10:00:00Z',
      }],
      total: 1,
    });
    vi.spyOn(klmApi, 'metaGroupParticipants').mockResolvedValue({
      items: [makeParticipant({ id: 'participant-1', groupId: internalGroup.id })],
      total: 1,
    });

    renderPage();

    expect(await screen.findByTestId(`group-list-item-${internalGroup.id}`)).toHaveTextContent('Caso Silva x Unimed');
    expect(await screen.findByText('Audiência marcada para sexta')).toBeInTheDocument();
    const participantsList = await screen.findByTestId('group-participants-list');
    expect(within(participantsList).getByText('Marina Gestora')).toBeInTheDocument();
    expect(screen.getAllByText('interno').length).toBeGreaterThan(0);
  });

  it('opens the configuration assistant from the floating button and shows real step state', async () => {
    const internalGroup = makeGroup({ id: 'group-internal-2', subject: 'Grupo da equipe', inviteLink: null });
    vi.spyOn(klmApi, 'omnichannelConnections').mockResolvedValue([connection]);
    vi.spyOn(klmApi, 'metaGroups').mockResolvedValue({ items: [internalGroup], total: 1 });
    vi.spyOn(klmApi, 'metaGroupCapability').mockResolvedValue(capability(gate()));
    vi.spyOn(klmApi, 'metaGroupMessages').mockResolvedValue(emptyMessages);
    vi.spyOn(klmApi, 'metaGroupParticipants').mockResolvedValue(emptyParticipants);

    renderPage();
    await screen.findByTestId(`group-list-item-${internalGroup.id}`);

    fireEvent.click(screen.getByTestId('groups-assistant-fab'));

    const sheet = await screen.findByTestId('groups-assistant-sheet');
    expect(within(sheet).getByText('Assistente de configuração de grupos')).toBeInTheDocument();

    // Step 1 (connection/OBA) is pending because the mocked gate is not ready.
    const step1 = within(sheet).getByText('Conexão WhatsApp e OBA').closest('div')!.parentElement!;
    expect(within(step1).getByText('pendente')).toBeInTheDocument();

    // Step 2 (first group) is already done because one group exists.
    const step2 = within(sheet).getByText('Criar o primeiro grupo').closest('div')!.parentElement!;
    expect(within(step2).getByText('concluído')).toBeInTheDocument();

    // Step 3 (invite link) is pending because the selected group has no invite link yet.
    const step3 = within(sheet).getByText('Link de convite').closest('div')!.parentElement!;
    expect(within(step3).getByText('pendente')).toBeInTheDocument();

    // Step 4 is always informational best practices, never fabricated as "done".
    const step4 = within(sheet).getByText('Boas práticas da API de grupos da Meta').closest('div')!.parentElement!;
    expect(within(step4).getByText('boas práticas')).toBeInTheDocument();
    expect(within(step4).getByText(/Limite de 8 membros/)).toBeInTheDocument();
  });

  it('shows the honest gated state when an official group action cannot be executed', async () => {
    const officialGroup = makeGroup({
      id: 'group-official-1',
      subject: 'Grupo oficial de clientes',
      groupKind: 'meta_cloud_group',
      isLocalInternal: false,
      isOfficialMetaGroup: true,
      providerGroupId: 'meta-group-provider-1',
      inviteLink: null,
    });
    vi.spyOn(klmApi, 'omnichannelConnections').mockResolvedValue([connection]);
    vi.spyOn(klmApi, 'metaGroups').mockResolvedValue({ items: [officialGroup], total: 1 });
    vi.spyOn(klmApi, 'metaGroupCapability').mockResolvedValue(capability(gate()));
    vi.spyOn(klmApi, 'metaGroupMessages').mockResolvedValue(emptyMessages);
    vi.spyOn(klmApi, 'metaGroupParticipants').mockResolvedValue(emptyParticipants);

    const rotation: KlmMetaGroupInviteLinkRotation = {
      group: officialGroup,
      rotated: false,
      simulated: false,
      blockedReasons: ['external_dispatch_not_implemented', 'official_invite_link_requires_provider_webhook_ingestion'],
      deduplicated: false,
    };
    const rotateInviteLink = vi.spyOn(klmApi, 'rotateMetaGroupInviteLink').mockResolvedValue(rotation);

    renderPage();
    await screen.findByTestId(`group-list-item-${officialGroup.id}`);

    const generateLink = screen.getByRole('button', { name: 'Gerar link' });
    await waitFor(() => expect(generateLink).toBeEnabled());
    fireEvent.click(generateLink);

    await waitFor(() => expect(rotateInviteLink).toHaveBeenCalledWith(officialGroup.id));
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({
      title: 'Rotação preparada, aguardando ativação',
      description: rotation.blockedReasons[0],
    })));
  }, 15_000);
});
