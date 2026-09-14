/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';

import { OfficialMetaGroupSheet } from '@/klm/components/OfficialMetaGroupSheet';
import ConversationsPage from '@/klm/pages/ConversationsPage';
import {
  klmApi,
  type KlmConversationCapabilities,
  type KlmFunnel,
  type KlmMetaGroup,
  type KlmMetaGroupCapability,
  type KlmMetaGroupCapabilityGate,
  type KlmMetaGroupOperation,
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

vi.mock('@/services/playbooks', () => ({
  getEffectivePlaybook: () => Promise.reject(new Error('Playbook indisponível no teste.')),
}));

const connection = {
  id: 'connection-meta-1',
  name: 'WhatsApp comercial',
  provider: 'meta_cloud',
  productMode: 'cloud_api',
  phoneNumberId: 'phone-number-1',
};

const requiredWebhooks: KlmMetaGroupCapabilityGate['requiredWebhookFields'] = [
  'group_lifecycle_update',
  'group_participants_update',
  'group_settings_update',
  'group_status_update',
];

function blockedGate(): KlmMetaGroupCapabilityGate {
  return {
    workspaceId: 'workspace-local',
    connectionId: connection.id,
    provider: 'meta_cloud',
    productMode: 'coexistence',
    developerAppState: 'not_ready',
    developerAppReady: false,
    developerAppMissingRequirements: [
      'developer_app_not_configured',
      'webhook_callback_not_verified',
      'webhook_subscription_missing:group_participants_update',
      'webhook_subscription_missing:group_settings_update',
      'webhook_subscription_missing:group_status_update',
    ],
    requiredWebhookFields: requiredWebhooks,
    subscribedWebhookFields: ['group_lifecycle_update'],
    obaEligibilityState: 'unknown',
    obaEvidenceSource: 'unverified',
    obaGateOpen: false,
    productModeSupported: false,
    coexistenceDenied: true,
    businessAppDenied: false,
    multiSolutionDenied: false,
    runtimeExternalActionsEnabled: false,
    externalCapabilityGateOpen: false,
    externalExecutionGateOpen: false,
    externalDispatchImplemented: false,
    localPreparationAvailable: true,
    capabilityDenialReasons: [
      'developer_app_not_configured',
      'webhook_callback_not_verified',
      'webhook_subscription_missing:group_participants_update',
      'webhook_subscription_missing:group_settings_update',
      'webhook_subscription_missing:group_status_update',
      'oba_eligibility_unknown',
      'coexistence_unsupported',
    ],
    executionDenialReasons: [
      'developer_app_not_configured',
      'webhook_callback_not_verified',
      'oba_eligibility_unknown',
      'coexistence_unsupported',
      'runtime_external_actions_disabled',
      'external_dispatch_not_implemented',
    ],
    limits: { inviteOnly: true, maxParticipants: 8, maxGroupsPerBusinessNumber: 10000 },
    observedAt: '2026-07-22T15:00:00Z',
  };
}

function readyGate(): KlmMetaGroupCapabilityGate {
  return {
    ...blockedGate(),
    productMode: 'cloud_api',
    developerAppState: 'ready',
    developerAppReady: true,
    developerAppMissingRequirements: [],
    subscribedWebhookFields: requiredWebhooks,
    obaEligibilityState: 'eligible',
    obaEvidenceSource: 'meta_business_manager',
    obaGateOpen: true,
    productModeSupported: true,
    coexistenceDenied: false,
    externalCapabilityGateOpen: true,
    externalExecutionGateOpen: false,
    capabilityDenialReasons: [],
    executionDenialReasons: ['runtime_external_actions_disabled', 'external_dispatch_not_implemented'],
  };
}

function capability(gate: KlmMetaGroupCapabilityGate): KlmMetaGroupCapability {
  const configured = gate.developerAppReady;
  return {
    id: configured ? 'capability-1' : null,
    workspaceId: 'workspace-local',
    connectionId: connection.id,
    configured,
    phoneNumberId: 'phone-number-1',
    wabaId: configured ? 'waba-1' : null,
    developerAppConfigured: gate.developerAppReady,
    webhookCallbackVerified: gate.developerAppReady,
    obaEvidenceReference: gate.obaGateOpen ? 'BM-EVIDENCE-2026-07-22' : null,
    obaEvidenceRecordedAt: gate.obaGateOpen ? '2026-07-22T14:30:00Z' : null,
    evidenceMetadata: {},
    version: configured ? 3 : 0,
    createdAt: configured ? '2026-07-22T14:30:00Z' : null,
    updatedAt: configured ? '2026-07-22T14:30:00Z' : null,
    gate,
    deduplicated: false,
  };
}

function group(
  gate: KlmMetaGroupCapabilityGate,
  kind: KlmMetaGroup['groupKind'],
  id: string,
  subject: string,
): KlmMetaGroup {
  const official = kind === 'meta_cloud_group';
  return {
    id,
    workspaceId: 'workspace-local',
    connectionId: connection.id,
    capabilityId: official ? 'capability-1' : null,
    createdByUserId: 'manager-1',
    groupKind: kind,
    isLocalInternal: !official,
    isOfficialMetaGroup: official,
    providerGroupId: official ? 'meta-group-provider-1' : null,
    providerRequestId: official ? 'meta-request-1' : null,
    subject,
    description: official ? 'Grupo projetado por lifecycle verificado.' : 'Colaboração interna do escritório.',
    joinApprovalMode: 'approval_required',
    state: official ? 'active' : 'local_ready',
    inviteLink: null,
    inviteLinkSimulated: false,
    participantCount: official ? 5 : 3,
    createIdempotencyKey: `group-key-${id}`,
    requestPayloadSha256: `sha-${id}`,
    lastLifecycleAt: official ? '2026-07-22T14:45:00Z' : null,
    lastMessagePreview: null,
    lastMessageAt: null,
    pinnedMessage: null,
    version: 1,
    groupMetadata: {},
    createdAt: '2026-07-22T14:00:00Z',
    updatedAt: '2026-07-22T14:45:00Z',
    gate,
  };
}

function operation(
  operationType: KlmMetaGroupOperation['operationType'],
  groupId: string | null,
): KlmMetaGroupOperation {
  return {
    id: `operation-${operationType}`,
    workspaceId: 'workspace-local',
    groupId,
    connectionId: connection.id,
    requestedByUserId: 'manager-1',
    operationType,
    state: 'local_ready',
    idempotencyKey: `idempotency-${operationType}`,
    requestPayloadSha256: `sha-${operationType}`,
    requestPayload: {},
    capabilitySnapshot: {},
    externalDispatchExecuted: false,
    createdAt: '2026-07-22T15:00:00Z',
    updatedAt: '2026-07-22T15:00:00Z',
  };
}

function renderSheet({
  gate,
  canConfigure = true,
  canPrepareOperations = true,
}: {
  gate: KlmMetaGroupCapabilityGate;
  canConfigure?: boolean;
  canPrepareOperations?: boolean;
}) {
  const officialGroup = group(gate, 'meta_cloud_group', 'official-group-1', 'Clientes Saúde Oficial');
  const internalGroup = group(gate, 'local_internal', 'internal-group-1', 'Equipe interna do caso');
  vi.spyOn(klmApi, 'metaGroupCapability').mockResolvedValue(capability(gate));
  vi.spyOn(klmApi, 'metaGroups').mockResolvedValue({ items: [internalGroup, officialGroup], total: 2 });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  const result = render(
    <QueryClientProvider client={client}>
      <OfficialMetaGroupSheet
        open
        onOpenChange={vi.fn()}
        connection={connection}
        canConfigure={canConfigure}
        canPrepareOperations={canPrepareOperations}
      />
    </QueryClientProvider>,
  );
  return { ...result, officialGroup, internalGroup };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  permissionMock.mockReset();
  permissionMock.mockReturnValue(true);
});

describe('Official Meta groups sheet', () => {
  it('shows official constraints and keeps official preparation blocked in Coexistence', async () => {
    const gate = blockedGate();
    const prepareOfficial = vi.spyOn(klmApi, 'prepareOfficialMetaGroupCreation');
    const prepareMessage = vi.spyOn(klmApi, 'prepareMetaGroupMessage');
    renderSheet({ gate });

    const dialog = await screen.findByRole('dialog', { name: 'Grupos oficiais WhatsApp' });
    await waitFor(() => expect(within(dialog).getByText('Developers')).toBeInTheDocument());
    expect(within(dialog).getByText('OBA')).toBeInTheDocument();
    expect(within(dialog).getAllByText('Coexistence')).toHaveLength(2);
    expect(within(dialog).getByText('Multi-solution')).toBeInTheDocument();
    expect(within(dialog).getByText(/não promete ativação nesse modo/i)).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Razões de bloqueio')).toHaveTextContent('assinatura obrigatória de webhook');
    expect(within(dialog).getByText('10.000')).toBeInTheDocument();
    expect(within(dialog).getByText('8')).toBeInTheDocument();
    const webhooksSection = within(dialog).getByRole('heading', { name: 'Webhooks exigidos' }).closest('section')!;
    expect(within(webhooksSection).getByText('Participantes do grupo').closest('div')).toHaveTextContent('Ausente');

    expect(within(dialog).getByRole('listitem', { name: 'Grupo interno Meu Executivo: Equipe interna do caso' })).toHaveTextContent('Grupo interno Meu Executivo');
    expect(within(dialog).getByRole('listitem', { name: 'Grupo oficial Meta: Clientes Saúde Oficial' })).toHaveTextContent('Grupo oficial Meta');

    fireEvent.change(within(dialog).getByLabelText('Nome do grupo oficial'), { target: { value: 'Conselho oficial' } });
    expect(within(dialog).getByRole('button', { name: 'Preparar criação oficial sem envio' })).toBeDisabled();
    expect(within(dialog).getByRole('button', { name: 'Preparar mensagem' })).toBeDisabled();

    fireEvent.change(within(dialog).getByLabelText('Nome do grupo interno'), { target: { value: 'Conselho interno' } });
    expect(within(dialog).getByRole('button', { name: 'Criar grupo interno Meu Executivo' })).toBeEnabled();
    expect(prepareOfficial).not.toHaveBeenCalled();
    expect(prepareMessage).not.toHaveBeenCalled();
  });

  it('uses distinct internal and official endpoints and only prepares official payloads', async () => {
    const gate = readyGate();
    const internalCreated = group(gate, 'local_internal', 'internal-created-2', 'Comitê interno');
    const createInternal = vi.spyOn(klmApi, 'createInternalMetaGroup').mockResolvedValue({
      group: internalCreated,
      operation: operation('create_group', internalCreated.id),
      deduplicated: false,
    });
    const prepareOfficial = vi.spyOn(klmApi, 'prepareOfficialMetaGroupCreation').mockResolvedValue({
      operation: operation('prepare_official_group_creation', null),
      gate,
      groupProjectionCreated: false,
      projectionSource: 'verified_group_lifecycle_update',
      providerRequestPath: '/phone-number-1/groups',
      providerRequestPayloadReady: true,
      providerRequestPayload: { subject: 'Conselho oficial' },
      externalDispatchExecuted: false,
      blockedReasons: [],
      deduplicated: false,
    });
    const { officialGroup } = renderSheet({ gate });
    const prepareMessage = vi.spyOn(klmApi, 'prepareMetaGroupMessage').mockResolvedValue({
      group: officialGroup,
      operation: operation('prepare_group_message', officialGroup.id),
      recipientType: 'group',
      toGroupId: officialGroup.providerGroupId,
      providerPayloadReady: true,
      providerPayload: { recipient_type: 'group', to: officialGroup.providerGroupId },
      externalDispatchExecuted: false,
      blockedReasons: ['runtime_external_actions_disabled', 'external_dispatch_not_implemented'],
      deduplicated: false,
    });

    const dialog = await screen.findByRole('dialog', { name: 'Grupos oficiais WhatsApp' });
    await waitFor(() => expect(within(dialog).getAllByText('Capability ready').length).toBeGreaterThanOrEqual(1));
    expect(within(dialog).getByText('Execução externa fechada')).toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText('Nome do grupo interno'), { target: { value: 'Comitê interno' } });
    fireEvent.change(within(dialog).getByLabelText('Descrição interna'), { target: { value: 'Somente escritório' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Criar grupo interno Meu Executivo' }));
    await waitFor(() => expect(createInternal).toHaveBeenCalledWith(connection.id, {
      subject: 'Comitê interno',
      description: 'Somente escritório',
      joinApprovalMode: 'auto_approve',
    }));
    expect(prepareOfficial).not.toHaveBeenCalled();

    fireEvent.change(within(dialog).getByLabelText('Nome do grupo oficial'), { target: { value: 'Conselho oficial' } });
    fireEvent.change(within(dialog).getByLabelText('Descrição oficial'), { target: { value: 'Preparação para lifecycle' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Preparar criação oficial sem envio' }));
    await waitFor(() => expect(prepareOfficial).toHaveBeenCalledWith(connection.id, {
      subject: 'Conselho oficial',
      description: 'Preparação para lifecycle',
      joinApprovalMode: 'auto_approve',
    }));
    expect(await within(dialog).findByText(/Nenhum grupo foi criado ou entregue na Meta/i)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Preparar mensagem' }));
    fireEvent.change(within(dialog).getByLabelText('Mensagem oficial a preparar'), { target: { value: 'Atualização do caso.' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Preparar payload sem envio' }));
    await waitFor(() => expect(prepareMessage).toHaveBeenCalledWith(officialGroup.id, 'Atualização do caso.'));
    expect(await within(dialog).findByText('Payload oficial preparado e auditado localmente. Nenhuma mensagem foi enviada à Meta.')).toBeInTheDocument();
    expect(createInternal).toHaveBeenCalledOnce();
    expect(prepareOfficial).toHaveBeenCalledOnce();
  });

  it('does not render mutable controls for users without the required permissions', async () => {
    renderSheet({ gate: readyGate(), canConfigure: false, canPrepareOperations: false });

    const dialog = await screen.findByRole('dialog', { name: 'Grupos oficiais WhatsApp' });
    await waitFor(() => expect(within(dialog).getByText(/ações mutáveis de evidência não são exibidas/i)).toBeInTheDocument());
    expect(within(dialog).getByText(/ações mutáveis de preparação não são exibidas/i)).toBeInTheDocument();
    expect(within(dialog).queryByLabelText('Phone Number ID')).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: 'Salvar evidência local' })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: 'Criar grupo interno Meu Executivo' })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: 'Preparar criação oficial sem envio' })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: 'Preparar mensagem' })).not.toBeInTheDocument();
  });

  it('records capability evidence locally without claiming external verification or dispatch', async () => {
    const gate = readyGate();
    const current = capability(gate);
    const configure = vi.spyOn(klmApi, 'configureMetaGroupCapability').mockResolvedValue({ ...current, version: 4 });
    renderSheet({ gate });

    const dialog = await screen.findByRole('dialog', { name: 'Grupos oficiais WhatsApp' });
    const saveEvidence = await within(dialog).findByRole('button', { name: 'Salvar evidência local' });
    await waitFor(() => expect(saveEvidence).toBeEnabled());
    fireEvent.click(saveEvidence);

    await waitFor(() => expect(configure).toHaveBeenCalledWith(connection.id, expect.objectContaining({
      expectedVersion: 3,
      phoneNumberId: 'phone-number-1',
      wabaId: 'waba-1',
      developerAppConfigured: true,
      webhookCallbackVerified: true,
      subscribedWebhookFields: requiredWebhooks,
      obaEligibilityState: 'eligible',
      obaEvidenceSource: 'meta_business_manager',
      obaEvidenceReference: 'BM-EVIDENCE-2026-07-22',
      evidenceMetadata: { source: 'conversations_official_meta_group_sheet' },
    })));
    expect(await within(dialog).findByText('Evidência salva no Meu Executivo com versão e auditoria. Nenhuma API da Meta foi acionada.')).toBeInTheDocument();
  });
});

const pageThread: KlmThread = {
  id: 'thread-meta-groups-1',
  workspaceId: 'workspace-local',
  matterId: null,
  clientId: 'client-meta-groups-1',
  assignedHumanId: null,
  primaryChannel: 'whatsapp',
  status: 'open',
  subject: 'orientacao_inicial',
  objectName: 'Atendimento',
  ownerAgentKey: null,
  score: 80,
  unreadCount: 1,
  lastMessagePreview: 'Olá.',
  lastMessageAt: '2026-07-22T15:00:00Z',
  slaDueAt: null,
  threadMetadata: { clientName: 'Ana Cliente' },
};

const pageFunnel: KlmFunnel = { boardVersion: 0, stages: [], metrics: {}, cards: [] };

function pageContext(): KlmOmnichannelContext {
  return {
    thread: pageThread,
    identity: null,
    channels: [{
      endpointId: 'endpoint-meta-1',
      bindingId: 'binding-meta-1',
      channel: 'whatsapp',
      provider: 'meta_cloud',
      connectionId: connection.id,
      connectionKey: 'whatsapp-cloud',
      connectionName: connection.name,
      connectionStatus: 'configured_local',
      transportMode: 'cloud_api',
      externalAccountId: 'phone-number-1',
      address: '+5561999999999',
      externalUserId: 'wa-user-1',
      consentState: 'granted',
      blockedAt: null,
      lastSeenAt: null,
      bindingCount: 1,
      capabilities: {},
    }],
    bindings: [],
    actionIntents: [],
    scheduledMessages: [],
    calls: [],
    automationRuns: [],
    timeline: [],
    externalGateOpen: false,
    generatedAt: '2026-07-22T15:00:00Z',
  };
}

function pageCollaborationCapabilities(): KlmConversationCapabilities {
  return {
    workspaceId: 'workspace-local',
    threadId: pageThread.id,
    groupId: null,
    localBridgeGateOpen: true,
    externalDeliveryGateOpen: false,
    participantCount: 1,
    capabilities: [],
    observedAt: '2026-07-22T15:00:00Z',
  };
}

function RouteProbe() {
  const location = useLocation();
  return <output data-testid="meta-groups-route">{location.pathname}{location.search}</output>;
}

function seedConversationsPage() {
  mockEmptyCapabilityQueries();
  vi.spyOn(klmApi, 'threads').mockResolvedValue([pageThread]);
  vi.spyOn(klmApi, 'funnel').mockResolvedValue(pageFunnel);
  vi.spyOn(klmApi, 'followUps').mockResolvedValue([]);
  vi.spyOn(klmApi, 'flows').mockResolvedValue([]);
  vi.spyOn(klmApi, 'flowRuns').mockResolvedValue([]);
  vi.spyOn(klmApi, 'messages').mockResolvedValue([]);
  vi.spyOn(klmApi, 'omnichannelContext').mockResolvedValue(pageContext());
  vi.spyOn(klmApi, 'omnichannelTeams').mockResolvedValue([]);
  vi.spyOn(klmApi, 'workspaceUsers').mockResolvedValue([]);
  vi.spyOn(klmApi, 'conversationReferences').mockResolvedValue([]);
  vi.spyOn(klmApi, 'conversationParticipants').mockResolvedValue({ group: null, participants: [] });
  vi.spyOn(klmApi, 'conversationCollaborationCapabilities').mockResolvedValue(pageCollaborationCapabilities());
  const gate = readyGate();
  const readCapability = vi.spyOn(klmApi, 'metaGroupCapability').mockResolvedValue(capability(gate));
  vi.spyOn(klmApi, 'metaGroups').mockResolvedValue({ items: [], total: 0 });
  return readCapability;
}

describe('Conversations official Meta groups integration', () => {
  it('opens the selected WhatsApp connection in one click without leaving Conversations', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1440 });
    const readCapability = seedConversationsPage();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/app/conversas']}><RouteProbe /><ConversationsPage /></MemoryRouter>
      </QueryClientProvider>,
    );

    const openButton = await screen.findByRole('button', { name: 'Grupos WhatsApp' });
    await waitFor(() => expect(screen.getByTestId('meta-groups-route')).toHaveTextContent(`/app/conversas?thread=${pageThread.id}`));
    const routeBefore = screen.getByTestId('meta-groups-route').textContent;
    fireEvent.click(openButton);

    const dialog = await screen.findByRole('dialog', { name: 'Grupos oficiais WhatsApp' });
    expect(within(dialog).getByText(/WhatsApp comercial · leitura de readiness/i)).toBeInTheDocument();
    expect(readCapability).toHaveBeenCalledWith(connection.id);
    expect(screen.getByTestId('meta-groups-route')).toHaveTextContent(routeBefore!);
    expect(screen.getByText('Central de atendimento')).toBeInTheDocument();
  });
});
