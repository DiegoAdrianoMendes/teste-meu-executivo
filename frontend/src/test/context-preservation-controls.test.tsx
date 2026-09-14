/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import type { ReactElement, ReactNode } from 'react';

import { MessageInput } from '@/components/mensagens/MessageInput';
import { KlmShell } from '@/klm/components/KlmShell';
import { klmApi, type KlmCalendarSettings, type KlmFunnel, type KlmThread } from '@/klm/api';
import AgendaPage from '@/klm/pages/AgendaPage';
import AgendaSettingsPage from '@/klm/pages/AgendaSettingsPage';
import AgentsPage from '@/klm/pages/AgentsPage';
import AudioPage from '@/klm/pages/AudioPage';
import FlowsPage from '@/klm/pages/FlowsPage';
import OmnichannelPage from '@/klm/pages/OmnichannelPage';
import * as adminService from '@/services/admin';

const permissionMock = vi.hoisted(() => vi.fn<(module: string, action: string) => boolean>(() => true));
const logoutMock = vi.hoisted(() => vi.fn(async () => undefined));

vi.mock('@/store/app-context', () => {
  const store = {
    can: permissionMock,
    logout: logoutMock,
    state: {
      currentUser: {
        id: 'user-local',
        name: 'Kleist Monteiro',
        email: 'kleisle@example.test',
        initials: 'KM',
        role: 'Administrador',
      },
      activeWorkspace: { id: 'workspace-1', name: 'Meu Escritório', slug: 'meu-escritorio' },
      session: {
        activeRole: 'workspace_owner',
        activeWorkspace: { id: 'workspace-1', name: 'Meu Escritório', slug: 'meu-escritorio' },
        availableWorkspaces: [{ id: 'membership-1' }],
      },
    },
  };

  return {
    useAppStore: () => store,
    useOptionalAppStore: () => store,
  };
});

vi.mock('@/hooks/use-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/klm/components/NativeContextTools', () => ({ NativeContextTools: () => null }));
vi.mock('@/klm/components/NativeModuleBar', () => ({ NativeModuleBar: () => null }));
vi.mock('@/klm/components/NativeCRMOperationsPanel', () => ({ NativeCRMOperationsPanel: () => null }));
vi.mock('@/klm/components/NativeAgentOperationsPanel', () => ({ NativeAgentOperationsPanel: () => null }));
vi.mock('@/klm/components/NativeFlowRuntimePanel', () => ({ NativeFlowRuntimePanel: () => null }));
vi.mock('@/klm/components/NativeInboxRoutingPanel', () => ({ NativeInboxRoutingPanel: () => null }));

vi.mock('@xyflow/react', () => ({
  Background: () => null,
  Controls: () => null,
  ReactFlow: ({ children }: { children?: ReactNode }) => <div data-testid="flow-canvas">{children}</div>,
  ReactFlowProvider: ({ children }: { children?: ReactNode }) => <>{children}</>,
  addEdge: (edge: unknown, edges: unknown[]) => [...edges, edge],
  applyEdgeChanges: (_changes: unknown, edges: unknown[]) => edges,
  applyNodeChanges: (_changes: unknown, nodes: unknown[]) => nodes,
  useReactFlow: () => ({ screenToFlowPosition: (position: { x: number; y: number }) => position }),
}));

const matterA = 'matter-a';
const matterB = 'matter-b';
const threadA = 'thread-a';
const threadB = 'thread-b';

const cards: KlmFunnel['cards'] = [
  {
    id: matterB,
    clientId: 'client-b',
    name: 'Bruno Caso B',
    subject: 'home_care',
    object: 'Home care',
    source: 'indicacao',
    owner: 'Jurídico',
    probability: 95,
    initialFee: 3000,
    projectedRevenue: 12000,
    slaDueAt: null,
    documents: 1,
    documentScore: 30,
    stage: 'triagem_sdr',
    microStatus: 'triagem',
  },
  {
    id: matterA,
    clientId: 'client-a',
    name: 'Ana Caso A',
    subject: 'medicamento_alto_custo',
    object: 'Medicamento A',
    source: 'google_ads',
    owner: 'SDR',
    probability: 80,
    initialFee: 5000,
    projectedRevenue: 20000,
    slaDueAt: null,
    documents: 2,
    documentScore: 75,
    stage: 'documentos',
    microStatus: 'coleta',
  },
];

const funnel: KlmFunnel = {
  boardVersion: 1,
  stages: [],
  metrics: {},
  cards,
};

const threads: KlmThread[] = [
  {
    id: threadB,
    workspaceId: 'workspace-1',
    matterId: matterB,
    clientId: 'client-b',
    assignedHumanId: null,
    primaryChannel: 'audio',
    status: 'open',
    subject: 'home_care',
    objectName: 'Home care',
    ownerAgentKey: 'juridico_saude',
    score: 99,
    unreadCount: 0,
    lastMessagePreview: 'Mensagem do caso B',
    lastMessageAt: '2026-07-18T10:00:00Z',
    slaDueAt: null,
    threadMetadata: { contactName: 'Bruno' },
  },
  {
    id: threadA,
    workspaceId: 'workspace-1',
    matterId: matterA,
    clientId: 'client-a',
    assignedHumanId: null,
    primaryChannel: 'whatsapp',
    status: 'open',
    subject: 'medicamento_alto_custo',
    objectName: 'Medicamento A',
    ownerAgentKey: 'sdr_saude',
    score: 80,
    unreadCount: 0,
    lastMessagePreview: 'Mensagem do caso A',
    lastMessageAt: '2026-07-18T09:00:00Z',
    slaDueAt: null,
    threadMetadata: { contactName: 'Ana' },
  },
];

const runtimeFixture = {
  mode: 'local_simulation',
  localOnly: true,
  localInfrastructure: true,
  externalActionsEnabled: false,
  awsEnabled: false,
  modelApisEnabled: false,
  onlineApproved: false,
  localBootstrapEnabled: true,
  providers: { googleWorkspace: 'offline' },
} as const;

const calendarSettings: KlmCalendarSettings = {
  timezone: 'America/Sao_Paulo',
  provider: 'manual',
  connectionStatus: 'manual_ready',
  accountEmail: null,
  calendarId: null,
  defaultMeetingUrl: 'https://meet.example.test/sala',
  defaultDurationMinutes: 45,
  slotIntervalMinutes: 30,
  minimumNoticeHours: 2,
  bookingHorizonDays: 30,
  bufferBeforeMinutes: 0,
  bufferAfterMinutes: 10,
  weeklyAvailability: Array.from({ length: 7 }, (_, weekday) => ({
    weekday,
    enabled: weekday < 5,
    windows: weekday < 5 ? [{ start: '09:00', end: '18:00' }] : [],
  })),
  bookingPage: {
    enabled: true,
    title: 'Agende sua conversa',
    description: 'Escolha um horário.',
    confirmationMessage: 'Horário confirmado.',
    showOfficePhone: false,
    officePhone: null,
  },
};

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location-probe">{location.pathname}{location.search}</output>;
}

function renderPage(ui: ReactElement, entry: string) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function mockShellQueries() {
  vi.spyOn(klmApi, 'dashboard').mockResolvedValue({
    metrics: { leads: 2, qualified: 2, activeThreads: 2, pendingApprovals: 0, dueFollowUps: 0, externalActions: 0 },
    funnel: [],
    decisions: [],
    runtime: runtimeFixture,
  });
  vi.spyOn(klmApi, 'quality').mockResolvedValue({ score: 90, metrics: {}, dimensions: [], reviewQueue: [], generatedAt: '2026-07-18T12:00:00Z' });
  vi.spyOn(klmApi, 'funnel').mockResolvedValue(funnel);
  vi.spyOn(klmApi, 'threads').mockResolvedValue(threads);
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
  permissionMock.mockReset();
  permissionMock.mockReturnValue(true);
  logoutMock.mockClear();
});

describe('context preservation and honest controls', () => {
  it.each([
    ['Tarefa', '/app/tarefas?action=new&matter=matter-a'],
    ['Compromisso ou audiência', '/app/agenda?action=new&matter=matter-a'],
    ['Caso 360', '/app/demandas?action=new&matter=matter-a'],
    ['Processo', '/app/processos?action=new&matter=matter-a'],
  ])('routes legal quick creation for %s to its real page', async (label, expectedLocation) => {
    window.localStorage.setItem('meu-executivo:workspace-mode:workspace-1:user-local', 'legal');
    mockShellQueries();
    renderPage(<><KlmShell /><LocationProbe /></>, `/app?matter=${matterA}`);

    fireEvent.click(await screen.findByRole('button', { name: 'Criar item jurídico' }));
    fireEvent.click(await screen.findByRole('button', { name: label }));

    expect(screen.getByTestId('location-probe')).toHaveTextContent(expectedLocation);
  });

  it.each([
    ['Tarefa', 'tasks', 'view'],
    ['Tarefa', 'tasks', 'create'],
    ['Compromisso ou audiência', 'lista', 'view'],
    ['Compromisso ou audiência', 'kanban', 'edit'],
    ['Caso 360', 'lista', 'view'],
    ['Caso 360', 'legal', 'manage'],
    ['Processo', 'lista', 'view'],
    ['Processo', 'processos', 'manage'],
    ['Solicitar aprovação', 'aprovacoes', 'create'],
  ])('hides legal quick creation for %s without %s:%s', async (label, deniedModule, deniedAction) => {
    window.localStorage.setItem('meu-executivo:workspace-mode:workspace-1:user-local', 'legal');
    permissionMock.mockImplementation((module: string, action: string) => (
      module !== deniedModule || action !== deniedAction
    ));
    mockShellQueries();
    renderPage(<KlmShell />, '/app');

    fireEvent.click(await screen.findByRole('button', { name: 'Criar item jurídico' }));

    expect(screen.queryByRole('button', { name: label })).not.toBeInTheDocument();
  });

  it.each(['general', 'commercial'] as const)('keeps quick creation out of preserved %s mode', async (mode) => {
    window.localStorage.setItem('meu-executivo:workspace-mode:workspace-1:user-local', mode);
    mockShellQueries();
    renderPage(<KlmShell />, '/app');

    expect(await screen.findByPlaceholderText('Buscar lead ou caso')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Criar item jurídico' })).not.toBeInTheDocument();
  });

  it('opens a functional account menu from the profile avatar', async () => {
    mockShellQueries();
    renderPage(<><KlmShell /><LocationProbe /></>, '/app/conversas');

    fireEvent.click(screen.getByRole('button', { name: 'Abrir perfil de Kleist Monteiro' }));

    expect(await screen.findByTestId('profile-menu')).toBeVisible();
    expect(screen.getByText('kleisle@example.test')).toBeVisible();
    expect(screen.getByText('Meu Escritório')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Minhas preferências' }));
    expect(screen.getByTestId('location-probe')).toHaveTextContent('/app/preferencias');
  });

  it('runs a flow only against the matter and thread carried in the URL', async () => {
    vi.spyOn(klmApi, 'flows').mockResolvedValue([{
      id: 'flow-a',
      name: 'Fluxo A',
      description: 'Fluxo contextual',
      practice_area: 'saude',
      is_template: false,
      is_active: true,
      trigger: 'manual',
      trigger_config: {},
      nodes: [{ id: 'start', type: 'trigger', label: 'Início', position: { x: 10, y: 10 }, data: { label: 'Início', category: 'Gatilho', description: 'Começar' }, config: {} }],
      edges: [],
      run_count: 0,
      current_revision_id: 'revision-a',
      current_revision: 1,
      version: 1,
    }]);
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(funnel);
    vi.spyOn(klmApi, 'threads').mockResolvedValue(threads);
    const dryRun = vi.spyOn(klmApi, 'dryRunFlow').mockResolvedValue({
      flow_id: 'flow-a',
      matter_id: matterA,
      status: 'completed',
      trace: [],
      max_steps: 100,
      external_actions_executed: 0,
      context: { thread_id: threadA },
    });

    renderPage(<FlowsPage />, `/app/flows?matter=${matterA}&thread=${threadA}&flow=flow-a`);

    const button = await screen.findByRole('button', { name: 'Dry-run' });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);

    await waitFor(() => expect(dryRun).toHaveBeenCalledWith('flow-a', matterA, { thread_id: threadA }));
    expect(dryRun).not.toHaveBeenCalledWith('flow-a', matterB, expect.anything());
  });

  it('never gives Agents the first thread from another matter', async () => {
    vi.spyOn(klmApi, 'agents').mockResolvedValue({
      policyVersion: 'test',
      runtime: 'local',
      agents: [{
        key: 'sdr',
        name: 'SDR',
        role: 'Triagem',
        group: 'Comercial',
        state: 'active',
        qualityScore: 95,
        queueSize: 0,
        description: 'Qualifica a demanda.',
        tools: ['matter_read'],
        gate: 'Humano decide.',
        permissions: { readMatter: true, writeCrm: false, createDraft: true, sendExternal: false, requestHuman: true },
        modelPolicy: { strategy: 'local', provider: 'local', model: 'simulator', taskClass: 'coordination', modelApiEnabled: false, localModelEnabled: true, simulatorAvailable: true, channelDeliveryEnabled: false, externalEnabled: false },
      }],
      metrics: { modeled: 1, active: 1, paused: 0, qualityAverage: 95, runs: 0, waitingHuman: 0, externalActionsExecuted: 0, modelStrategy: 'local', modelApisEnabled: false, localModelsEnabled: true, executionMode: 'local', modelTokens: 0 },
    });
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(funnel);
    vi.spyOn(klmApi, 'threads').mockResolvedValue([threads[0]]);
    vi.spyOn(klmApi, 'approvals').mockResolvedValue([]);
    vi.spyOn(klmApi, 'agentRuns').mockResolvedValue([]);
    const messages = vi.spyOn(klmApi, 'messages').mockResolvedValue([]);
    const runAgent = vi.spyOn(klmApi, 'runAgent').mockResolvedValue({ runs: [], messageIds: [], approvalId: null, deduplicated: false, externalActionsExecuted: 0, runtime: 'local', policyVersion: 'test' });

    renderPage(<AgentsPage />, `/app/agentes?matter=${matterA}`);
    fireEvent.click(await screen.findByRole('button', { name: 'Executar no caso atual' }));

    await waitFor(() => expect(runAgent).toHaveBeenCalledWith(expect.objectContaining({ matterId: matterA, threadId: undefined })));
    expect(messages).not.toHaveBeenCalled();
  });

  it('keeps Audio unavailable instead of opening another matter audio thread', async () => {
    vi.spyOn(klmApi, 'threads').mockResolvedValue([threads[0]]);
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(funnel);
    const messages = vi.spyOn(klmApi, 'messages').mockResolvedValue([]);
    vi.spyOn(klmApi, 'externalIntents').mockResolvedValue([]);

    renderPage(<AudioPage />, `/app/audio?matter=${matterA}`);

    expect(await screen.findByText(/Ana Caso A · medicamento alto custo/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Registrar gate' })).toBeDisabled();
    expect(messages).not.toHaveBeenCalled();
    expect(screen.queryByText('Bruno')).not.toBeInTheDocument();
  });

  it('does not select a client implicitly in Agents or Audio without URL context', async () => {
    vi.spyOn(klmApi, 'agents').mockResolvedValue({
      policyVersion: 'test',
      runtime: 'local',
      agents: [{
        key: 'sdr',
        name: 'SDR',
        role: 'Triagem',
        group: 'Comercial',
        state: 'active',
        qualityScore: 95,
        queueSize: 0,
        description: 'Qualifica a demanda.',
        tools: ['matter_read'],
        gate: 'Humano decide.',
        permissions: { readMatter: true, writeCrm: false, createDraft: true, sendExternal: false, requestHuman: true },
        modelPolicy: { strategy: 'local', provider: 'local', model: 'simulator', taskClass: 'coordination', modelApiEnabled: false, localModelEnabled: true, simulatorAvailable: true, channelDeliveryEnabled: false, externalEnabled: false },
      }],
      metrics: { modeled: 1, active: 1, paused: 0, qualityAverage: 95, runs: 0, waitingHuman: 0, externalActionsExecuted: 0, modelStrategy: 'local', modelApisEnabled: false, localModelsEnabled: true, executionMode: 'local', modelTokens: 0 },
    });
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(funnel);
    vi.spyOn(klmApi, 'threads').mockResolvedValue(threads);
    vi.spyOn(klmApi, 'approvals').mockResolvedValue([]);
    vi.spyOn(klmApi, 'agentRuns').mockResolvedValue([]);
    const messages = vi.spyOn(klmApi, 'messages').mockResolvedValue([]);
    vi.spyOn(klmApi, 'externalIntents').mockResolvedValue([]);

    const agentsView = renderPage(<AgentsPage />, '/app/agentes');
    expect(await screen.findByRole('button', { name: 'Executar no caso atual' })).toBeDisabled();
    agentsView.unmount();

    renderPage(<AudioPage />, '/app/audio');
    expect((await screen.findAllByText(/Atendimento sem card vinculado/i)).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Registrar gate' })).toBeDisabled();
    expect(messages).not.toHaveBeenCalled();
  });

  it('selects Omnichannel by matter and carries matter plus thread into Agenda', async () => {
    vi.spyOn(klmApi, 'threads').mockResolvedValue(threads);
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(funnel);
    vi.spyOn(klmApi, 'messages').mockResolvedValue([]);
    vi.spyOn(klmApi, 'matter').mockResolvedValue({
      id: matterA,
      columnId: 'documentos',
      microStatus: 'coleta',
      demandType: 'Medicamento',
      leadSource: 'google_ads',
      tags: [],
      executiveSummary: null,
      healthSubtheme: 'medicamento_alto_custo',
      healthSubthemeCustom: null,
      healthObjectName: 'Medicamento A',
      healthInsurer: null,
      healthDenialReason: null,
      medicalUrgencyHours: null,
      economicValueAnnual: null,
      leadScore: 80,
      manualContextRevision: 0,
      manualContextUpdatedAt: null,
      nextAction: null,
      docScore: 75,
      productionScore: null,
      claimValue: null,
      client: { id: 'client-a', name: 'Ana Caso A', email: null, phone: null },
      documents: [],
      timeline: [],
      knowledgeChunks: [],
      revenueProjection: null,
      honorariumEstimate: null,
      campaignAttribution: null,
    });

    renderPage(
      <Routes>
        <Route path="/app/omnichannel" element={<><OmnichannelPage /><LocationProbe /></>} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>,
      `/app/omnichannel?matter=${matterA}`,
    );

    await waitFor(() => expect(screen.getByTestId('location-probe')).toHaveTextContent(`/app/omnichannel?matter=${matterA}&thread=${threadA}`));
    fireEvent.click(screen.getByRole('button', { name: 'Agenda' }));
    expect(screen.getByTestId('location-probe')).toHaveTextContent(`/app/agenda?matter=${matterA}&thread=${threadA}&action=new`);
  });

  it('retries AgendaSettings after an API error and preserves its return context', async () => {
    vi.spyOn(klmApi, 'calendarSettings').mockRejectedValueOnce(new Error('Agenda local indisponível')).mockResolvedValue(calendarSettings);
    vi.spyOn(klmApi, 'bookingLinks').mockResolvedValue([]);

    renderPage(
      <Routes>
        <Route path="/app/agenda/configuracoes" element={<><AgendaSettingsPage /><LocationProbe /></>} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>,
      `/app/agenda/configuracoes?matter=${matterA}&thread=${threadA}`,
    );

    expect(await screen.findByRole('heading', { name: 'Não foi possível carregar a configuração da agenda' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Tentar novamente' }));
    expect(await screen.findByRole('heading', { name: 'Disponibilidade e booking' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Voltar à Agenda' }));
    expect(screen.getByTestId('location-probe')).toHaveTextContent(`/app/agenda?matter=${matterA}&thread=${threadA}`);
  });

  it('refreshes Google Agenda data through the real local APIs without claiming an external sync', async () => {
    const meetings = vi.spyOn(klmApi, 'meetings').mockResolvedValue([]);
    vi.spyOn(klmApi, 'followUps').mockResolvedValue([]);
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(funnel);
    const runtime = vi.spyOn(klmApi, 'runtime').mockResolvedValue({ ...runtimeFixture, externalActionsEnabled: true, providers: { googleWorkspace: 'connected' } });
    vi.spyOn(klmApi, 'dailyBriefing').mockResolvedValue({
      day: '2026-07-18',
      timezone: 'America/Sao_Paulo',
      total: 0,
      linkedCount: 0,
      orphanCount: 0,
      missingRoomCount: 0,
      summary: 'Sem reuniões.',
      priorities: [],
      items: [],
      generationMode: 'local_context',
      generatedBy: 'Agenda local',
      generatedAt: '2026-07-18T12:00:00Z',
    });
    vi.spyOn(klmApi, 'unifiedAgenda').mockResolvedValue([]);
    vi.spyOn(adminService, 'fetchTeamMembers').mockResolvedValue([]);
    const externalIntent = vi.spyOn(klmApi, 'createExternalIntent').mockRejectedValue(new Error('Não deveria ser chamado'));

    renderPage(<AgendaPage />, '/app/agenda');

    fireEvent.click(await screen.findByRole('button', { name: 'Sincronizar' }));
    fireEvent.click(screen.getByRole('button', { name: 'Atualizar agenda local' }));

    await waitFor(() => expect(meetings.mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(runtime.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(externalIntent).not.toHaveBeenCalled();
  });

  it('persists a contextual approval from the shell instead of merely navigating', async () => {
    mockShellQueries();
    const createApproval = vi.spyOn(klmApi, 'createApproval').mockResolvedValue({
      id: 'approval-a',
      cardId: matterA,
      clientName: 'Ana Caso A',
      type: 'manual_contextual',
      title: 'Aprovação em Flows',
      description: null,
      priority: 'media',
      status: 'pending',
      requestedAt: '2026-07-18T12:00:00Z',
      resolutionReason: null,
    });

    renderPage(<><KlmShell /><LocationProbe /></>, `/app/flows?matter=${matterA}&thread=${threadA}`);

    expect(screen.getByTestId('location-probe')).toHaveTextContent(`/app/flows?matter=${matterA}&thread=${threadA}`);
    expect((await screen.findAllByRole('link', { name: 'Agentes' }))[0]).toHaveAttribute('href', `/app/agentes?matter=${matterA}&thread=${threadA}`);
    fireEvent.click(screen.getByRole('button', { name: 'Pedir aprovação' }));
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Caso da aprovação' })).toHaveTextContent('Ana Caso A'));
    fireEvent.click(screen.getByRole('button', { name: 'Criar solicitação' }));

    await waitFor(() => expect(createApproval).toHaveBeenCalledWith(expect.objectContaining({
      matterId: matterA,
      approvalType: 'manual_contextual',
      description: expect.stringContaining(`Conversa: ${threadA}`),
    })));
    expect(createApproval.mock.calls[0][0].description).toContain(`Origem: /app/flows?matter=${matterA}&thread=${threadA}`);
  });

  it('inserts emoji and exposes unavailable media controls as disabled', async () => {
    const onSend = vi.fn();
    render(<MessageInput onSend={onSend} />);

    expect(screen.getByRole('button', { name: 'Anexos indisponíveis neste canal' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Gravação de voz indisponível neste canal' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Inserir emoji' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Inserir 🙂' }));
    expect(screen.getByRole('textbox', { name: 'Mensagem' })).toHaveValue('🙂');
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Mensagem' }), { key: 'Enter' });

    await waitFor(() => expect(onSend).toHaveBeenCalledWith('🙂'));
  });
});
