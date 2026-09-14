/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import { TooltipProvider } from '@/components/ui/tooltip';
import ConversationsPage from '@/klm/pages/ConversationsPage';
import { klmApi, type KlmFlow, type KlmFlowRun, type KlmFunnel, type KlmMatterDetail, type KlmMeeting, type KlmMessage, type KlmOmnichannelContext, type KlmOmnichannelCopilot, type KlmThread, type KlmTrackingSummary } from '@/klm/api';
import { ApiError } from '@/services/http';
import type { WhatsAppConnectionSummary } from '@/types';
import * as localStorageService from '@/services/local-storage';
import { mockEmptyCapabilityQueries } from '@/test/capability-fixtures';

const whatsappServiceMocks = vi.hoisted(() => ({
  fetchWhatsAppConnections: vi.fn().mockResolvedValue([]),
  startWhatsAppConversation: vi.fn(),
}));

const campaignServiceMocks = vi.hoisted(() => ({
  fetchMetaTemplates: vi.fn(),
}));

vi.mock('@/services/whatsapp', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/whatsapp')>()),
  fetchWhatsAppConnections: whatsappServiceMocks.fetchWhatsAppConnections,
  startWhatsAppConversation: whatsappServiceMocks.startWhatsAppConversation,
}));

vi.mock('@/services/campaigns', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/campaigns')>()),
  fetchMetaTemplates: campaignServiceMocks.fetchMetaTemplates,
}));

const permissionMock = vi.hoisted(() => vi.fn((_module: string, _action: string) => true));

// V36: simula a transição pendente do React Router 7, em que `useSearchParams` ainda não
// reflete o `?thread=` gravado por `selectConversation` (janela de 1 a 3 s sob CPU lenta).
const routerControl = vi.hoisted(() => ({ freezeSearchParams: false, frozenParams: new URLSearchParams('') }));
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return {
    ...actual,
    useSearchParams: (...args: Parameters<typeof actual.useSearchParams>) => {
      const [params, setParams] = actual.useSearchParams(...args);
      if (!routerControl.freezeSearchParams) return [params, setParams] as const;
      return [routerControl.frozenParams, setParams] as const;
    },
  };
});

vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({
    can: permissionMock,
    state: {
      currentUser: { name: 'Kleist Monteiro', email: 'local@klmadvogados.com.br' },
    },
  }),
  useOptionalAppStore: () => ({
    can: permissionMock,
    state: {
      currentUser: { name: 'Kleist Monteiro', email: 'local@klmadvogados.com.br' },
    },
  }),
}));

vi.mock('@/services/admin', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/admin')>();
  return { ...actual, fetchMyAccessPackage: async () => null };
});

const matterId = '428c5996-f1ab-420f-ba3f-24d20280d452';
const threadId = '167c7903-9206-4aa6-9ccf-809e591b4d89';
const secondMatterId = '6e75bc9f-99cb-4cc4-94a2-722e31f72a13';
const secondThreadId = 'f3d59735-bf89-4576-8da8-366d16fe3642';

const thread: KlmThread = {
  id: threadId,
  workspaceId: 'workspace-local',
  matterId,
  clientId: 'client-local',
  assignedHumanId: null,
  primaryChannel: 'whatsapp',
  status: 'waiting_human',
  subject: 'medicamento_alto_custo',
  objectName: 'Zolgensma',
  ownerAgentKey: 'sdr_saude',
  version: 1,
  score: 94,
  unreadCount: 1,
  lastMessagePreview: 'Preciso de ajuda.',
  lastMessageAt: '2026-07-13T12:00:00Z',
  lastInboundAt: '2099-07-13T12:00:00Z',
  slaDueAt: '2026-07-13T15:00:00Z',
  threadMetadata: {},
};

const funnel: KlmFunnel = {
  boardVersion: 0,
  stages: [{ id: 'triagem_sdr', label: 'Triagem', count: 1 }],
  metrics: {},
  cards: [{
    id: matterId,
    clientId: 'client-local',
    name: 'Ana Paula Ribeiro',
    subject: 'medicamento_alto_custo',
    object: 'Zolgensma',
    source: 'google_ads',
    owner: 'SDR Saúde',
    probability: 94,
    initialFee: 7500,
    projectedRevenue: 1307500,
    slaDueAt: '2026-07-13T15:00:00Z',
    documents: 4,
    documentScore: 88,
    stage: 'triagem_sdr',
    microStatus: 'qualificado',
  }],
};

const secondThread: KlmThread = {
  ...thread,
  id: secondThreadId,
  matterId: secondMatterId,
  clientId: 'client-second',
  objectName: 'Home care 24h',
  score: 86,
  lastMessagePreview: 'Enviei o relatório.',
};

const twoThreadFunnel: KlmFunnel = {
  ...funnel,
  stages: [{ id: 'triagem_sdr', label: 'Triagem', count: 2 }],
  cards: [
    ...funnel.cards,
    {
      ...funnel.cards[0],
      id: secondMatterId,
      clientId: 'client-second',
      name: 'Bruno Cardoso',
      subject: 'home_care',
      object: 'Home care 24h',
      probability: 86,
    },
  ],
};

function matterDetail(id = matterId, name = 'Ana Paula Ribeiro'): KlmMatterDetail {
  return {
    id,
    columnId: 'triagem_sdr',
    microStatus: 'qualificado',
    demandType: 'Negativa de medicamento',
    leadSource: 'google_ads',
    tags: [],
    executiveSummary: 'Negativa em análise.',
    healthSubtheme: 'medicamento_alto_custo',
    healthSubthemeCustom: null,
    healthObjectName: id === matterId ? 'Zolgensma' : 'Home care 24h',
    healthInsurer: 'Plano local',
    healthDenialReason: 'Fora do rol',
    medicalUrgencyHours: 24,
    economicValueAnnual: 100000,
    leadScore: 94,
    manualContextRevision: 0,
    manualContextUpdatedAt: null,
    nextAction: 'Validar documentos',
    docScore: 88,
    productionScore: 40,
    claimValue: 100000,
    client: { id: `client-${id}`, name, email: `${name.split(' ')[0].toLowerCase()}@example.com`, phone: '+5561999999999' },
    documents: [],
    timeline: [],
    knowledgeChunks: [],
    revenueProjection: null,
    honorariumEstimate: null,
    campaignAttribution: { source: 'google', medium: 'cpc', campaign: 'negativa', channel: 'search' },
  };
}

const availableStart = '2030-07-18T14:00:00Z';

function mockCaseQueries() {
  vi.spyOn(klmApi, 'matter').mockImplementation(async (id) => matterDetail(id, id === matterId ? 'Ana Paula Ribeiro' : 'Bruno Cardoso'));
  vi.spyOn(klmApi, 'legalArtifacts').mockResolvedValue([]);
  vi.spyOn(klmApi, 'legalArtifact').mockImplementation(async (id) => ({
    id,
    workspaceId: 'workspace-local',
    matterId,
    artifactKey: `artifact-key-${matterId}`,
    version: 1,
    kind: 'proposal',
    title: 'Proposta de atuação · Zolgensma',
    status: 'draft',
    content: {
      clientName: 'Ana Paula Ribeiro',
      demandLabel: 'Negativa de medicamento',
      objectName: 'Zolgensma',
      financialConditions: { fixedFee: null, successPercentage: null, reviewStatus: 'rascunho' },
    },
    sourceSnapshot: { matterId },
    createdByUserId: 'user-local',
    approvalItemId: null,
    createdAt: '2026-07-18T12:00:00Z',
    updatedAt: '2026-07-18T12:00:00Z',
  }));
  vi.spyOn(klmApi, 'createLegalArtifact').mockImplementation(async (payload) => ({
    id: `artifact-${payload.matterId}`,
    workspaceId: 'workspace-local',
    matterId: payload.matterId,
    artifactKey: payload.artifactKey ?? `artifact-key-${payload.matterId}`,
    version: payload.expectedVersion ? payload.expectedVersion + 1 : 1,
    kind: payload.kind,
    title: payload.title,
    status: 'draft',
    content: payload.content,
    sourceSnapshot: { matterId: payload.matterId },
    createdByUserId: 'user-local',
    approvalItemId: null,
    createdAt: '2026-07-18T12:00:00Z',
    updatedAt: '2026-07-18T12:00:00Z',
  }));
  vi.spyOn(klmApi, 'meetingsForMatter').mockResolvedValue([]);
  vi.spyOn(klmApi, 'bookingLinksForMatter').mockResolvedValue([]);
  vi.spyOn(klmApi, 'financeEntries').mockResolvedValue([]);
  vi.spyOn(klmApi, 'availability').mockResolvedValue({
    timezone: 'America/Sao_Paulo',
    durationMinutes: 45,
    meetingProvider: 'google_meet',
    connectionStatus: 'manual_ready',
    meetingUrl: 'https://meet.google.com/local-test',
    slots: [{ startsAt: availableStart, endsAt: '2030-07-18T14:45:00Z' }],
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const flows: KlmFlow[] = [
  {
    id: 'flow-follow-up', name: 'Follow-up: documentos pendentes', description: 'Retomada da coleta de documentos.', practice_area: 'saude_suplementar', is_template: false, is_active: true, trigger: 'manual', trigger_config: { category: 'follow_up' }, nodes: [{ id: 'start' }, { id: 'wait' }], edges: [], run_count: 3, current_revision_id: 'rev-follow-up', current_revision: 1, version: 1,
  },
  {
    id: 'flow-recovery', name: 'Recuperação: oportunidade sem resposta', description: 'Retomada respeitosa de oportunidades.', practice_area: 'saude_suplementar', is_template: false, is_active: true, trigger: 'manual', trigger_config: { category: 'recovery' }, nodes: [{ id: 'start' }, { id: 'review' }, { id: 'message' }], edges: [], run_count: 1, current_revision_id: 'rev-recovery', current_revision: 1, version: 1,
  },
  {
    id: 'flow-custom', name: 'Fluxo personalizado do escritório', description: 'Criado na página de Fluxos.', practice_area: 'saude_suplementar', is_template: false, is_active: true, trigger: 'manual', trigger_config: {}, nodes: [{ id: 'custom' }], edges: [], run_count: 0, current_revision_id: 'rev-custom', current_revision: 1, version: 1,
  },
];

function omnichannelContext(activeThread: KlmThread): KlmOmnichannelContext {
  const route = (
    suffix: string,
    connectionKey: string,
    connectionName: string,
    transportMode: string,
  ): KlmOmnichannelContext['channels'][number] => ({
    endpointId: `endpoint-${suffix}`,
    bindingId: `binding-${suffix}`,
    channel: 'whatsapp',
    provider: 'meta_cloud',
    connectionId: `connection-${suffix}`,
    connectionKey,
    connectionName,
    connectionStatus: 'configured_local',
    transportMode,
    externalAccountId: `account-${suffix}`,
    address: '+5561999999999',
    externalUserId: `wa-user-${activeThread.id}`,
    consentState: 'granted',
    blockedAt: null,
    lastSeenAt: '2026-07-13T12:00:00Z',
    bindingCount: 1,
    capabilities: {
      outbound_messages: { supported: true, enabled: true, state: 'configuration_required' },
      media: { supported: true, enabled: true, state: 'configuration_required' },
      templates: { supported: true, enabled: true, state: 'configuration_required' },
      flows: { supported: true, enabled: true, state: 'configuration_required' },
    },
  });
  return {
    thread: activeThread,
    identity: null,
    channels: [
      route('cloud', 'whatsapp-cloud-comercial', 'WhatsApp comercial', 'cloud_api'),
      route('coexistence', 'whatsapp-coexistence-comercial', 'WhatsApp no aplicativo', 'coexistence'),
    ],
    bindings: [],
    actionIntents: [],
    scheduledMessages: [],
    calls: [],
    automationRuns: [],
    timeline: [],
    externalGateOpen: false,
    generatedAt: '2026-07-13T12:00:00Z',
  };
}

function renderPage(initialEntry = '/app/conversas') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <MemoryRouter initialEntries={[initialEntry]}><RouteProbe /><ConversationsPage /></MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

function RouteProbe() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <div data-testid="conversation-router-probe">
      <output data-testid="conversation-route">{location.pathname}{location.search}</output>
      <button type="button" data-testid="navigate-first-thread" onClick={() => navigate(`/app/conversas?thread=${threadId}&matter=${matterId}`)}>Primeira conversa</button>
      <button type="button" data-testid="navigate-second-thread" onClick={() => navigate(`/app/conversas?thread=${secondThreadId}&matter=${secondMatterId}`)}>Segunda conversa</button>
      <button type="button" data-testid="navigate-back" onClick={() => navigate(-1)}>Voltar no histórico</button>
      <button type="button" data-testid="navigate-forward" onClick={() => navigate(1)}>Avançar no histórico</button>
    </div>
  );
}

function useDesktopViewport() {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1600 });
  vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
    matches: query.includes('min-width'),
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

function useTabletViewport() {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 834 });
  vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
    matches: query.includes('max-width: 1023px'),
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

function useMobileViewport() {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
  vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
    matches: query.includes('max-width: 1023px'),
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

function mockConnectedData(messages: KlmMessage[] = []) {
  mockEmptyCapabilityQueries();
  mockCaseQueries();
  vi.spyOn(klmApi, 'threads').mockResolvedValue([thread]);
  vi.spyOn(klmApi, 'funnel').mockResolvedValue(funnel);
  vi.spyOn(klmApi, 'followUps').mockResolvedValue([]);
  vi.spyOn(klmApi, 'flows').mockResolvedValue(flows);
  vi.spyOn(klmApi, 'flowRuns').mockResolvedValue([]);
  vi.spyOn(klmApi, 'messages').mockResolvedValue(messages);
  vi.spyOn(klmApi, 'omnichannelContext').mockResolvedValue(omnichannelContext(thread));
}

function mockTwoThreadData(messages: (threadId: string) => Promise<KlmMessage[]> = async () => []) {
  mockEmptyCapabilityQueries();
  mockCaseQueries();
  vi.spyOn(klmApi, 'threads').mockResolvedValue([thread, secondThread]);
  vi.spyOn(klmApi, 'funnel').mockResolvedValue(twoThreadFunnel);
  vi.spyOn(klmApi, 'followUps').mockResolvedValue([]);
  vi.spyOn(klmApi, 'flows').mockResolvedValue(flows);
  vi.spyOn(klmApi, 'flowRuns').mockResolvedValue([]);
  vi.spyOn(klmApi, 'messages').mockImplementation(messages);
  vi.spyOn(klmApi, 'omnichannelContext').mockImplementation(async (requestedThreadId) => (
    omnichannelContext(requestedThreadId === secondThreadId ? secondThread : thread)
  ));
}

function selectContact(name: string) {
  const contact = screen.getAllByTestId('conversation-contact').find((item) => item.textContent?.includes(name));
  expect(contact).toBeDefined();
  fireEvent.click(contact!);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  whatsappServiceMocks.fetchWhatsAppConnections.mockReset().mockResolvedValue([]);
  whatsappServiceMocks.startWhatsAppConversation.mockReset();
  campaignServiceMocks.fetchMetaTemplates.mockReset();
  permissionMock.mockReset();
  permissionMock.mockReturnValue(true);
});

describe('conversation tools', () => {
  it('keeps inbox cards concise and exposes only the operational lead context', async () => {
    useDesktopViewport();
    mockConnectedData();

    renderPage();

    const contact = await screen.findByTestId('conversation-contact');
    expect(within(contact).getByText('Área')).toBeVisible();
    expect(within(contact).getByText('Área não informada')).toBeVisible();
    expect(within(contact).getByText('Subnicho')).toBeVisible();
    expect(within(contact).getByText('Produto')).toBeVisible();
    expect(within(contact).getByText('Zolgensma')).toBeVisible();
    expect(within(contact).getByRole('group', { name: /Potencial de Ana Paula Ribeiro/ })).toBeVisible();
    expect(within(contact).getByLabelText(/Indicador de tempo de resposta|Atendimento em dia/)).toBeVisible();
    expect(within(contact).queryByText('Preciso de ajuda.')).not.toBeInTheDocument();
    expect(contact).not.toHaveTextContent(/\d+d\s+\d+h/);
    expect(screen.getByTestId('conversation-policy-state')).toHaveTextContent('Sem política · área não informada');
  });

  it('classifies lead potential from zero to five stars without leaving the inbox', async () => {
    useDesktopViewport();
    mockConnectedData();
    const updatedThread = {
      ...thread,
      version: 2,
      score: 60,
    };
    vi.mocked(klmApi.threads).mockResolvedValueOnce([thread]).mockResolvedValue([updatedThread]);
    const updateThread = vi.spyOn(klmApi, 'updateThread').mockResolvedValue(updatedThread);

    renderPage();

    const contact = await screen.findByTestId('conversation-contact');
    expect(within(contact).getByRole('group', { name: 'Potencial de Ana Paula Ribeiro: 5 de 5 estrelas' })).toBeVisible();
    fireEvent.click(within(contact).getByRole('button', { name: 'Classificar potencial de Ana Paula Ribeiro com 3 estrelas' }));

    await waitFor(() => expect(updateThread).toHaveBeenCalledWith(threadId, { score: 60, expectedVersion: 1 }));
    await waitFor(() => expect(within(contact).getByRole('group', { name: 'Potencial de Ana Paula Ribeiro: 3 de 5 estrelas' })).toBeVisible());
    expect(screen.getByTestId('conversation-route')).toHaveTextContent('/app/conversas');
  });

  it('edits and persists the selected follow-up without leaving the conversation', async () => {
    useMobileViewport();
    mockConnectedData();
    const currentFollowUp = {
      id: 'follow-up-1',
      matterId,
      threadId,
      channel: 'whatsapp' as const,
      status: 'scheduled' as const,
      dueAt: '2030-07-18T15:00:00Z',
      attempt: 0,
      maxAttempts: 8,
      reason: 'Retomar documentos.',
    };
    vi.mocked(klmApi.followUps).mockResolvedValue([currentFollowUp]);
    const updateFollowUp = vi.spyOn(klmApi, 'updateFollowUp').mockResolvedValue({
      ...currentFollowUp,
      dueAt: '2030-07-20T16:30:00Z',
      reason: 'Confirmar documentação e reunião.',
    });

    renderPage();

    fireEvent.click(await screen.findByTestId('conversation-contact'));
    const editFollowUp = await screen.findByRole('button', { name: /Editar follow-up/ });
    expect(editFollowUp).not.toHaveClass('hidden');
    fireEvent.click(editFollowUp);
    expect(await screen.findByRole('dialog', { name: 'Editar follow-up' })).toBeVisible();
    fireEvent.change(screen.getByLabelText('Data e hora'), { target: { value: '2030-07-20T13:30' } });
    fireEvent.change(screen.getByLabelText('Motivo e orientação'), { target: { value: 'Confirmar documentação e reunião.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar follow-up' }));

    await waitFor(() => expect(updateFollowUp).toHaveBeenCalledWith(
      'follow-up-1',
      expect.objectContaining({
        dueAt: expect.any(String),
        reason: 'Confirmar documentação e reunião.',
        status: 'scheduled',
      }),
    ));
    expect(await screen.findByText(/Follow-up reagendado/)).toBeVisible();
    expect(screen.getByTestId('conversation-route')).toHaveTextContent('/app/conversas');
  });

  it('replaces free text with the approved-template picker outside the WhatsApp 24-hour window', async () => {
    useDesktopViewport();
    mockConnectedData();
    const expiredThread = {
      ...thread,
      lastInboundAt: '2020-01-01T12:00:00Z',
    };
    vi.mocked(klmApi.threads).mockResolvedValue([expiredThread]);
    vi.mocked(klmApi.omnichannelContext).mockResolvedValue(omnichannelContext(expiredThread));

    renderPage();

    expect(await screen.findByText('A janela de 24 horas encerrou. Só é possível enviar um modelo aprovado.')).toBeVisible();
    expect(screen.getByTestId('conversation-composer')).toHaveClass('sr-only');

    fireEvent.click(screen.getByRole('button', { name: 'Escolher modelo' }));

    expect(await screen.findByRole('dialog', { name: 'Enviar modelo aprovado' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Vários' })).toBeEnabled();
  });

  it('edits Case 360 values in place and persists a manual revision', async () => {
    useDesktopViewport();
    mockConnectedData();
    const updated = {
      ...matterDetail(),
      manualContextRevision: 1,
      manualContextUpdatedAt: '2026-07-20T18:45:00Z',
      client: { ...matterDetail().client, phone: '+5511987654321' },
    };
    let persisted = false;
    const updateCase360 = vi.spyOn(klmApi, 'updateCase360').mockImplementation(async () => {
      persisted = true;
      return updated;
    });
    // Após salvar, a invalidação de funnel/threads faz a query do caso refazer o
    // fetch; o mock precisa refletir a persistência (detalhe antigo antes de
    // salvar, atualizado depois), senão o refetch sobrescreve o setQueryData
    // com o telefone antigo.
    vi.spyOn(klmApi, 'matter').mockImplementation(async (id) => (
      id === matterId && persisted ? updated : matterDetail(id, id === matterId ? 'Ana Paula Ribeiro' : 'Bruno Cardoso')
    ));

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Editar WhatsApp' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'WhatsApp' }), { target: { value: '+5511987654321' } });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar WhatsApp' }));

    await waitFor(() => expect(updateCase360).toHaveBeenCalledWith(matterId, {
      client: { phone: '+5511987654321' },
      matter: {},
    }));
    expect(await screen.findByText('+5511987654321')).toBeInTheDocument();
    expect(await screen.findByText(/^v1 ·/)).toBeInTheDocument();
  }, 10_000);

  it('keeps the Case 360 editor open when persistence fails', async () => {
    useDesktopViewport();
    mockConnectedData();
    vi.spyOn(klmApi, 'updateCase360').mockRejectedValue(new Error('Conflito de revisão manual.'));

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Editar WhatsApp' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'WhatsApp' }), { target: { value: '+5511987654321' } });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar WhatsApp' }));

    expect((await screen.findAllByText('Conflito de revisão manual.')).length).toBeGreaterThan(0);
    expect(screen.getByRole('textbox', { name: 'WhatsApp' })).toHaveValue('+5511987654321');
    expect(screen.getByRole('button', { name: 'Salvar WhatsApp' })).toBeEnabled();
  }, 10_000);

  it('offers saved advanced filters and executes supported conditions on the API', async () => {
    useDesktopViewport();
    mockTwoThreadData();
    const filteredThreads = vi.spyOn(klmApi, 'filteredThreads').mockResolvedValue([thread]);

    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Filtros avançados' }));
    expect(screen.getByText(/Combine score, atendente, equipe, tipo, prioridade/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Adicionar condição' }));

    const comboboxes = screen.getAllByRole('combobox');
    fireEvent.click(comboboxes[1]);
    fireEvent.click(await screen.findByRole('option', { name: 'Score do lead' }));
    fireEvent.click(screen.getAllByRole('combobox')[2]);
    fireEvent.click(await screen.findByRole('option', { name: 'é maior ou igual a' }));
    fireEvent.change(screen.getByPlaceholderText('0'), { target: { value: '90' } });
    fireEvent.click(screen.getByRole('button', { name: 'Aplicar filtros' }));

    await waitFor(() => expect(filteredThreads).toHaveBeenCalledWith(expect.objectContaining({
      scoreMin: 90,
      limit: 500,
    })));
    await waitFor(() => expect(screen.getAllByTestId('conversation-contact')).toHaveLength(1));
  });

  it('filters campaign conversations and derives no-return from canonical thread metadata', async () => {
    mockTwoThreadData();
    vi.spyOn(klmApi, 'threads').mockResolvedValue([
      {
        ...thread,
        threadMetadata: {
          broadcast: {
            totalSent: 1,
            campaignIds: ['campaign-recovery'],
            lastCampaignId: 'campaign-recovery',
            lastCampaignTitle: 'Recuperação de propostas',
            lastSentAt: '2026-07-13T12:00:00Z',
            awaitingReply: true,
            deliveryFailed: false,
          },
        },
      },
      secondThread,
    ]);

    renderPage('/app/conversas?broadcast=awaiting_reply&campaign=campaign-recovery');

    expect(await screen.findAllByText('Ana Paula Ribeiro')).not.toHaveLength(0);
    expect(screen.getAllByTestId('conversation-contact')).toHaveLength(1);
    expect(screen.getByTestId('conversation-contact')).not.toHaveTextContent('Recuperação de propostas');
    expect(screen.queryByText('Bruno Cardoso')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Sem disparo' }));
    expect(await screen.findByText('Bruno Cardoso')).toBeInTheDocument();
    expect(screen.getAllByTestId('conversation-contact')).toHaveLength(1);
  });

  it('keeps the high-frequency conversation tools available in one click', async () => {
    mockConnectedData();

    renderPage();

    const actions = await screen.findByTestId('conversation-one-click-actions');
    const pendenciesButton = within(actions).getByRole('button', { name: 'Abrir pendências desta conversa' });
    expect(pendenciesButton).toBeEnabled();
    expect(within(actions).getByRole('button', { name: 'Agendar reunião agora' })).toBeEnabled();
    expect(within(actions).getByRole('button', { name: 'Preparar proposta agora' })).toBeEnabled();
    expect(screen.getByTestId('conversation-workspace-toolbar')).toBeInTheDocument();
    expect(screen.queryByText(/Encerrado: Recuperação: oportunidade sem resposta/i)).not.toBeInTheDocument();

    fireEvent.click(pendenciesButton);
    expect(await screen.findByText('Pendências da conversa')).toBeVisible();
  });

  it('opens original and edit history in place without changing the conversation route', async () => {
    mockConnectedData([{
      id: 'message-evidence-ui',
      threadId,
      matterId,
      channel: 'whatsapp',
      direction: 'inbound',
      authorType: 'client',
      authorLabel: 'Ana Paula',
      body: 'A negativa foi hoje.',
      deliveryStatus: 'received',
      occurredAt: '2026-07-21T18:31:00Z',
      messageMetadata: {},
      contentLifecycleState: 'edited',
      evidenceEventCount: 2,
      evidenceRevisionCount: 2,
      evidenceChainSha256: 'b'.repeat(64),
    }]);
    const media = {
      storageObjectId: null,
      objectId: null,
      storageObjectVersion: null,
      sha256: null,
      byteSize: null,
      mimeType: null,
      filename: null,
      providerMediaId: null,
    };
    const captured = {
      id: 'evidence-original',
      chainId: 'chain-1',
      revision: 1,
      contentRevision: 1,
      messageId: 'message-evidence-ui',
      threadId,
      eventType: 'captured' as const,
      observationState: 'observed' as const,
      provider: 'meta_cloud',
      connectionKey: 'whatsapp:commercial',
      externalMessageId: 'wamid.ui.1',
      contentType: 'text',
      bodySnapshot: 'A negativa foi ontem.',
      structuredContent: {},
      eventPayload: {},
      eventPayloadSha256: 'a'.repeat(64),
      sourceEnvelopeSha256: 'c'.repeat(64),
      signatureSha256: 'd'.repeat(64),
      previousEvidenceSha256: null,
      evidenceSha256: 'e'.repeat(64),
      media,
      providerOccurredAt: '2026-07-21T18:30:00Z',
      receivedAt: '2026-07-21T18:30:01Z',
    };
    const edited = {
      ...captured,
      id: 'evidence-edited',
      revision: 2,
      contentRevision: 2,
      eventType: 'edited' as const,
      bodySnapshot: 'A negativa foi hoje.',
      previousEvidenceSha256: captured.evidenceSha256,
      evidenceSha256: 'b'.repeat(64),
      media: {
        ...media,
        storageObjectId: 'media-version-record',
        objectId: 'media-logical-object',
        storageObjectVersion: 3,
        sha256: 'f'.repeat(64),
        byteSize: 2048,
        mimeType: 'audio/ogg',
        filename: 'audio-original.ogg',
        providerMediaId: 'meta-audio-1',
      },
    };
    vi.spyOn(klmApi, 'messageEvidence').mockResolvedValue({
      messageId: 'message-evidence-ui',
      threadId,
      chainId: 'chain-1',
      contentLifecycleState: 'edited',
      eventCount: 2,
      contentRevisionCount: 2,
      integrityStatus: 'verified',
      chainHeadSha256: 'b'.repeat(64),
      originalSnapshot: captured,
      currentSnapshot: edited,
      events: [captured, edited],
      providerNotes: ['Somente eventos entregues pelo provedor são registrados.'],
      sensitiveDataHidden: false,
      generatedAt: '2026-07-21T18:31:01Z',
    });
    const mediaBlob = new Blob(['preserved-media'], { type: 'audio/ogg' });
    const downloadMedia = vi.spyOn(localStorageService, 'downloadLocalStorageObject').mockResolvedValue(mediaBlob);
    const saveMedia = vi.spyOn(localStorageService, 'saveBlob').mockImplementation(() => undefined);
    const initialEntry = `/app/conversas?matter=${matterId}&thread=${threadId}`;

    renderPage(initialEntry);

    fireEvent.click(await screen.findByRole('button', { name: '2 registros' }));
    expect(await screen.findByRole('heading', { name: 'Histórico e integridade' })).toBeInTheDocument();
    expect(await screen.findByText('A negativa foi ontem.')).toBeInTheDocument();
    expect(screen.getByText('A negativa foi hoje.', { selector: 'blockquote' })).toBeInTheDocument();
    expect(screen.getByText('Cadeia íntegra nesta verificação')).toBeInTheDocument();
    expect(screen.getByTestId('conversation-route')).toHaveTextContent(initialEntry);
    expect(klmApi.messageEvidence).toHaveBeenCalledWith(threadId, 'message-evidence-ui');
    fireEvent.click(screen.getByRole('button', { name: 'Baixar esta versão preservada' }));
    await waitFor(() => expect(downloadMedia).toHaveBeenCalledWith('media-logical-object', 3));
    expect(saveMedia).toHaveBeenCalledWith(mediaBlob, 'audio-original.ogg');
  });

  it('keeps all contextual actions together and visible in the conversation workspace', async () => {
    mockConnectedData();

    renderPage();

    const actions = await screen.findByTestId('conversation-context-actions');
    for (const label of ['CRM', 'Pedir docs', 'Agendar', 'Proposta', 'Áudio']) {
      const button = within(actions).getByRole('button', { name: label });
      expect(button).toBeVisible();
      expect(button).toHaveAttribute('aria-haspopup', 'dialog');
    }
    expect(screen.getByRole('button', { name: 'Fluxo' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Disparo individual' })).toBeVisible();
    expect(within(actions).queryByRole('button', { name: 'Operar' })).not.toBeInTheDocument();
    expect(within(actions).queryByRole('button', { name: 'Fluxo' })).not.toBeInTheDocument();
    expect(actions).toHaveClass('grid');
  });

  it('runs the native assistant inside the composer and returns the reviewed reply to the same field', async () => {
    useDesktopViewport();
    mockConnectedData();
    const copilotResult = {
      action: 'suggest_reply',
      engine: 'native-evidence-v6',
      content: 'Entendi. Envie a prescrição e a negativa do plano para seguirmos com a análise.',
      suggestions: [],
      unansweredQuestions: [],
      tasks: [],
      evidenceMessageIds: ['message-1'],
      confidence: 0.94,
      requiresModel: false,
      externalRequests: 0,
      generatedAt: '2026-07-22T12:00:00Z',
    } satisfies KlmOmnichannelCopilot;
    const copilot = vi.spyOn(klmApi, 'omnichannelCopilot').mockResolvedValue(copilotResult);
    const initialEntry = `/app/conversas?matter=${matterId}&thread=${threadId}`;

    renderPage(initialEntry);

    const composer = await screen.findByTestId('conversation-composer');
    const assistantButton = screen.getByTestId('conversation-composer-assistant');
    expect(composer.parentElement).toHaveClass('relative');
    expect(composer.parentElement).toContainElement(assistantButton);
    fireEvent.change(composer, { target: { value: 'rascunho do atendimento' } });
    fireEvent.click(assistantButton);

    const assistant = await screen.findByLabelText('Assistente Meu Executivo no campo de resposta');
    expect(within(assistant).getByText('Assistente Meu Executivo')).toBeVisible();
    fireEvent.click(within(assistant).getByRole('button', { name: 'Responder' }));

    await waitFor(() => expect(copilot).toHaveBeenCalledWith(threadId, expect.objectContaining({
      action: 'suggest_reply',
      tone: 'acolhedor',
    })));
    expect(await within(assistant).findByDisplayValue(copilotResult.content)).toBeVisible();
    expect(composer).toHaveValue('rascunho do atendimento');

    fireEvent.click(within(assistant).getByRole('button', { name: 'Colocar no campo de resposta' }));

    await waitFor(() => expect(composer).toHaveValue(copilotResult.content));
    expect(screen.getByTestId('conversation-route')).toHaveTextContent(initialEntry);
    expect(screen.queryByLabelText('Assistente Meu Executivo no campo de resposta')).not.toBeInTheDocument();
  });

  it('keeps the explicit selection and an open sheet while ?thread= is still propagating (V36)', async () => {
    useTabletViewport();
    mockConnectedData();
    vi.spyOn(klmApi, 'threads')
      .mockResolvedValueOnce([thread])
      .mockResolvedValue([{ ...thread, score: thread.score + 1 }]);
    routerControl.freezeSearchParams = true;
    try {
      renderPage('/app/conversas');

      fireEvent.click(await screen.findByTestId('conversation-contact'));
      expect(await screen.findByTestId('conversation-active-header')).toBeVisible();
      fireEvent.click(screen.getByRole('button', { name: /^Proposta$/ }));
      expect(await screen.findByRole('heading', { name: 'Preparar proposta' })).toBeInTheDocument();

      // A lista de contatos é recalculada dentro da janela (evento realtime -> refetch de threads com
      // dado novo). Antes da correção, o efeito de seleção lia "compacto sem ?thread" como "voltar à
      // lista": zerava a seleção, fechava todas as folhas e a lista piscava.
      act(() => {
        window.dispatchEvent(new CustomEvent('klm:realtime', {
          detail: { event: 'OMNICHANNEL_MESSAGE_CREATED', data: { thread_id: threadId } },
        }));
      });
      await waitFor(() => expect(klmApi.threads).toHaveBeenCalledTimes(2), { timeout: 3_000 });
      await act(() => new Promise((resolve) => setTimeout(resolve, 100)));

      expect(screen.getByTestId('conversation-active-header')).toBeVisible();
      expect(screen.getByRole('heading', { name: 'Preparar proposta' })).toBeInTheDocument();
      expect(screen.getByTestId('conversation-route')).toHaveTextContent(`thread=${threadId}`);
    } finally {
      routerControl.freezeSearchParams = false;
    }
  });

  it('keeps the conversation list stable when returning on the tablet layout', async () => {
    useTabletViewport();
    mockConnectedData();

    renderPage(`/app/conversas?thread=${threadId}&matter=${matterId}`);

    fireEvent.click(await screen.findByTitle('Voltar às conversas'));

    expect(await screen.findByTestId('conversation-contact')).toBeVisible();
    expect(screen.getByTestId('conversation-route')).toHaveTextContent('/app/conversas');
    expect(screen.getByTestId('conversation-route')).not.toHaveTextContent('thread=');
    expect(screen.getByTestId('conversation-route')).not.toHaveTextContent('matter=');

    fireEvent.click(screen.getByTestId('conversation-contact'));

    expect(await screen.findByTestId('conversation-active-header')).toBeVisible();
    expect(screen.getByTestId('conversation-route')).toHaveTextContent(`thread=${threadId}`);
    expect(screen.getByTestId('conversation-route')).toHaveTextContent(`matter=${matterId}`);
  });

  it('opens Caso 360 from the active contact photo without changing the conversation route', async () => {
    mockConnectedData();
    const initialEntry = `/app/conversas?matter=${matterId}&thread=${threadId}`;

    renderPage(initialEntry);

    const profileButton = await screen.findByRole('button', { name: 'Abrir Caso 360 de Ana Paula Ribeiro' });
    fireEvent.click(profileButton);

    expect(await screen.findByText('Caso 360')).toBeVisible();
    expect(screen.getByTestId('conversation-route')).toHaveTextContent(initialEntry);
  });

  it('rehydrates the selected conversation across same-component browser back and forward navigation', async () => {
    useDesktopViewport();
    mockTwoThreadData();

    renderPage(`/app/conversas?thread=${threadId}&matter=${matterId}`);

    expect(await screen.findByTestId('conversation-active-header')).toHaveTextContent('Ana Paula Ribeiro');
    fireEvent.click(screen.getByTestId('navigate-second-thread'));

    expect(await screen.findByTestId('conversation-active-header')).toHaveTextContent('Bruno Cardoso');
    expect(screen.getByTestId('conversation-route')).toHaveTextContent(`thread=${secondThreadId}`);
    expect(screen.getByTestId('conversation-route')).toHaveTextContent(`matter=${secondMatterId}`);

    fireEvent.click(screen.getByTestId('navigate-back'));

    expect(await screen.findByTestId('conversation-active-header')).toHaveTextContent('Ana Paula Ribeiro');
    expect(screen.getByTestId('conversation-route')).toHaveTextContent(`thread=${threadId}`);

    fireEvent.click(screen.getByTestId('navigate-forward'));

    expect(await screen.findByTestId('conversation-active-header')).toHaveTextContent('Bruno Cardoso');
    expect(screen.getByTestId('conversation-route')).toHaveTextContent(`thread=${secondThreadId}`);
    expect(screen.getByTestId('conversation-route')).toHaveTextContent(`matter=${secondMatterId}`);
  });

  it.each([
    { action: 'CRM', surface: 'Caso 360 e CRM' },
    { action: 'Fluxo', surface: 'Ativar fluxo no atendimento' },
    { action: 'Pedir docs', surface: 'Solicitar documentos' },
    { action: 'Agendar', surface: 'Agendar reunião com Ana Paula Ribeiro' },
    { action: 'Proposta', surface: 'Preparar proposta' },
    { action: 'Disparo individual', surface: 'Disparos e listas' },
    { action: 'Áudio', surface: 'Áudio assistido' },
  ])('opens $action as a contextual surface without changing the conversation route', async ({ action, surface }) => {
    mockConnectedData();
    const initialEntry = `/app/conversas?matter=${matterId}&thread=${threadId}`;

    renderPage(initialEntry);

    expect((await screen.findAllByText('Ana Paula Ribeiro')).length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByRole('button', { name: action })[0]);
    expect(await screen.findByRole('heading', { name: surface })).toBeInTheDocument();
    expect(screen.getByTestId('conversation-route')).toHaveTextContent(initialEntry);
  });

  it('prepares native rich WhatsApp content in a modal without leaving the conversation', async () => {
    mockConnectedData();
    const richMessage: KlmMessage = {
      id: 'rich-buttons-message',
      threadId,
      matterId,
      channel: 'whatsapp',
      direction: 'outbound',
      authorType: 'human',
      authorLabel: 'Kleist Monteiro',
      body: 'Como deseja continuar?',
      contentType: 'interactive_buttons',
      deliveryStatus: 'local_draft',
      occurredAt: '2026-07-21T18:00:00Z',
      messageMetadata: {},
    };
    const sendRichMessage = vi.spyOn(klmApi, 'sendRichMessage').mockResolvedValue(richMessage);
    const initialEntry = `/app/conversas?matter=${matterId}&thread=${threadId}`;

    renderPage(initialEntry);

    expect((await screen.findAllByText('Ana Paula Ribeiro')).length).toBeGreaterThan(0);
    const composer = screen.getByPlaceholderText('Escreva ou aprove a sugestão. Nada sai como humano sem você enviar.');
    fireEvent.change(composer, { target: { value: 'Como deseja continuar?' } });
    const attachmentButton = screen.getByTestId('conversation-composer-attachment');
    expect(composer.parentElement).toContainElement(attachmentButton);
    fireEvent.click(attachmentButton);

    const dialog = await screen.findByRole('dialog', { name: 'Conteúdo avançado da mensagem' });
    const buttonsTab = within(dialog).getByRole('tab', { name: 'Botões' });
    fireEvent.mouseDown(buttonsTab, { button: 0, ctrlKey: false });
    await waitFor(() => expect(buttonsTab).toHaveAttribute('data-state', 'active'));
    expect(within(dialog).getByLabelText('Mensagem principal')).toHaveValue('Como deseja continuar?');
    expect(within(dialog).getAllByText('Como deseja continuar?').length).toBeGreaterThan(0);
    expect(dialog.querySelector('iframe')).not.toBeInTheDocument();
    expect(screen.getByTestId('conversation-route')).toHaveTextContent(initialEntry);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Preparar envio' }));

    await waitFor(() => expect(sendRichMessage).toHaveBeenCalledWith(
      threadId,
      {
        body: 'Como deseja continuar?',
        contentType: 'interactive_buttons',
        templateId: null,
        language: null,
        messageMetadata: {
          attachments: [],
          structuredContent: {
            kind: 'reply_buttons',
            body: 'Como deseja continuar?',
            header: null,
            footer: null,
            buttons: [
              { id: 'agendar', title: 'Agendar' },
              { id: 'falar_advogado', title: 'Falar com advogado' },
            ],
          },
          richComposerMode: 'buttons',
          externalActionsExecuted: 0,
        },
      },
      'whatsapp',
      'Kleist Monteiro',
      {
        endpointId: 'endpoint-cloud',
        bindingId: 'binding-cloud',
        connectionId: 'connection-cloud',
        connectionKey: 'whatsapp-cloud-comercial',
        transport: 'cloud_api',
      },
    ));
    expect(screen.queryByRole('dialog', { name: 'Conteúdo avançado da mensagem' })).not.toBeInTheDocument();
    expect(await screen.findByText(/nenhuma API externa foi acionada/i)).toBeInTheDocument();
    expect(screen.getByTestId('conversation-route')).toHaveTextContent(initialEntry);
  });

  it('opens Case 360 destinations inside a contextual sheet and preserves the conversation route', async () => {
    useDesktopViewport();
    mockConnectedData();
    vi.spyOn(klmApi, 'tracking').mockResolvedValue({
      periodDays: 30,
      periodStart: '2026-06-18T00:00:00Z',
      totalEvents: 0,
      identifiedJourneys: 0,
      leads: 0,
      qualifiedLeads: 0,
      meetings: 0,
      clients: 0,
      adSpend: 0,
      attributableRevenue: 0,
      ignoredValueEvents: 0,
      conversionRate: null,
      cac: null,
      roas: null,
      byChannel: [],
      eventCoverage: [],
      timeTrend: [],
      journeys: [],
      remarketing: [],
      generatedAt: '2026-07-18T12:00:00Z',
    } as unknown as KlmTrackingSummary);
    const initialEntry = `/app/conversas?matter=${matterId}&thread=${threadId}`;

    renderPage(initialEntry);

    expect((await screen.findAllByText('Ana Paula Ribeiro')).length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByRole('button', { name: 'CRM' })[0]);
    const caseDialog = await screen.findByRole('dialog');
    fireEvent.click(within(caseDialog).getByRole('button', { name: 'Rastreamento' }));

    expect(await screen.findByRole('heading', { name: 'Rastreamento da origem' })).toBeInTheDocument();
    const trackingSurface = screen.getByLabelText('Contexto: Rastreamento da origem');
    expect(await within(trackingSurface).findByText('Rastreamento e aquisição', { selector: 'h2' })).toBeInTheDocument();
    expect(trackingSurface.querySelector('iframe')).not.toBeInTheDocument();
    expect(screen.getByTestId('conversation-route')).toHaveTextContent(initialEntry);
  }, 15_000);

  it('keeps an automated reply hidden while the agent is preparing it', async () => {
    mockConnectedData([{
      id: 'cadence-preparing',
      threadId,
      matterId,
      channel: 'whatsapp',
      direction: 'outbound',
      authorType: 'agent',
      authorLabel: 'SDR Saúde',
      body: 'Esta resposta só pode aparecer depois da cadência.',
      deliveryStatus: 'local_draft',
      occurredAt: '2026-07-17T12:00:00Z',
      messageMetadata: {
        deliveryCadence: {
          typingStartsAt: '2099-07-17T12:00:00Z',
          notBefore: '2099-07-17T12:00:10Z',
          showTypingIndicator: true,
        },
      },
    }]);

    renderPage();

    expect(await screen.findByText('preparando resposta')).toBeInTheDocument();
    expect(screen.queryByText('Esta resposta só pode aparecer depois da cadência.')).not.toBeInTheDocument();
  });

  it('shows the typing indicator without revealing the automated reply early', async () => {
    mockConnectedData([{
      id: 'cadence-typing',
      threadId,
      matterId,
      channel: 'whatsapp',
      direction: 'outbound',
      authorType: 'agent',
      authorLabel: 'Jurídico Saúde',
      body: 'Conteúdo ainda protegido pelo horário notBefore.',
      deliveryStatus: 'local_draft',
      occurredAt: '2026-07-17T12:00:00Z',
      messageMetadata: {
        deliveryCadence: {
          typingStartsAt: '2020-07-17T12:00:00Z',
          notBefore: '2099-07-17T12:00:00Z',
          showTypingIndicator: true,
        },
      },
    }]);

    renderPage();

    expect(await screen.findByText('digitando')).toBeInTheDocument();
    expect(screen.queryByText('Conteúdo ainda protegido pelo horário notBefore.')).not.toBeInTheDocument();
  });

  it('calls the local APIs for documents, meetings, and proposal using the selected case', async () => {
    mockConnectedData();
    const initialEntry = `/app/conversas?thread=${threadId}&matter=${matterId}`;
    const createFollowUp = vi.spyOn(klmApi, 'createFollowUp').mockResolvedValue({
      id: 'follow-local', matterId, threadId, channel: 'whatsapp', status: 'scheduled', dueAt: '2026-07-14T06:00:00Z', attempt: 0, maxAttempts: 8, reason: 'Documentos',
    });
    const meeting: KlmMeeting = { id: 'meeting-local', version: 1, matterId, title: 'Reunião online: Ana Paula Ribeiro', startsAt: '2026-07-13T17:00:00Z', endsAt: '2026-07-13T17:45:00Z', status: 'scheduled', location: 'Online', meetingUrl: null, notes: 'Local' };
    const createMeeting = vi.spyOn(klmApi, 'createInternalBooking').mockResolvedValue(meeting);
    const moveFunnel = vi.spyOn(klmApi, 'moveFunnel').mockResolvedValue({
      matterId,
      columnId: 'proposta',
      microStatus: 'proposta_em_revisao',
      cardOrder: 0,
      boardVersion: 1,
      operationId: '00000000-0000-4000-8000-000000000001',
      agentDispatched: false,
      deduplicated: false,
    });

    renderPage(initialEntry);
    expect((await screen.findAllByText('Ana Paula Ribeiro')).length).toBeGreaterThan(0);
    expect(within(screen.getByTestId('conversation-workspace-toolbar')).getByText('1 conversa')).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: 'Pedir docs' }));
    expect(await screen.findByRole('heading', { name: 'Solicitar documentos' })).toBeInTheDocument();
    expect(createFollowUp).not.toHaveBeenCalled();
    const prepareDocumentRequest = screen.getByRole('button', { name: 'Preparar pedido e follow-up' });
    await waitFor(() => expect(prepareDocumentRequest).toBeEnabled());
    fireEvent.click(prepareDocumentRequest);
    await waitFor(() => expect(createFollowUp).toHaveBeenCalledWith(expect.objectContaining({ matterId, threadId, channel: 'whatsapp' })));
    expect(await screen.findByText(/Pedido de documentos preparado e follow-up local agendado/)).toBeInTheDocument();
    expect(screen.getByTestId('conversation-route')).toHaveTextContent(initialEntry);

    fireEvent.click(screen.getByRole('button', { name: 'Agendar' }));
    fireEvent.click(await screen.findByRole('button', { name: '11:00' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar horário' }));
    await waitFor(() => expect(createMeeting).toHaveBeenCalledWith(expect.objectContaining({ matterId, startsAt: availableStart, durationMinutes: 45 })));
    expect(await screen.findByRole('button', { name: 'Agendar outro horário' })).toBeInTheDocument();
    expect(screen.getByTestId('conversation-route')).toHaveTextContent(initialEntry);

    fireEvent.click(screen.getByRole('button', { name: /^Proposta$/ }));
    expect(await screen.findByRole('heading', { name: 'Preparar proposta' })).toBeInTheDocument();
    expect(moveFunnel).not.toHaveBeenCalled();
    const generateProposal = screen.getByRole('button', { name: 'Gerar proposta e abrir prévia' });
    await waitFor(() => expect(generateProposal).toBeEnabled());
    fireEvent.click(generateProposal);
    await waitFor(() => expect(moveFunnel).toHaveBeenCalledWith(
      matterId,
      'proposta',
      expect.objectContaining({
        expectedBoardVersion: 0,
        sourceColumnId: 'triagem_sdr',
        operationId: expect.any(String),
      }),
    ));
    expect(await screen.findByRole('heading', { name: 'Prévia da proposta' })).toBeInTheDocument();
    const proposalSurface = screen.getByLabelText('Prévia contextual da proposta');
    expect(await within(proposalSurface).findByTestId('proposal-preview')).toBeInTheDocument();
    expect(proposalSurface.querySelector('iframe')).not.toBeInTheDocument();
    expect(screen.getByTestId('conversation-route')).toHaveTextContent(initialEntry);
    expect((screen.getByPlaceholderText('Escreva ou aprove a sugestão. Nada sai como humano sem você enviar.') as HTMLTextAreaElement).value).toContain(`/proposta/${matterId}`);
  }, 10_000);

  it('reloads the funnel and retries proposal once after an optimistic conflict', async () => {
    mockConnectedData();
    const latestFunnel = { ...funnel, boardVersion: 1 };
    vi.mocked(klmApi.funnel)
      .mockResolvedValueOnce(funnel)
      .mockResolvedValueOnce(latestFunnel);
    const moveFunnel = vi.spyOn(klmApi, 'moveFunnel')
      .mockRejectedValueOnce(new ApiError(409, 'Versão desatualizada'))
      .mockResolvedValueOnce({
        matterId,
        columnId: 'proposta',
        microStatus: 'proposta_em_revisao',
        cardOrder: 0,
        boardVersion: 2,
        operationId: '00000000-0000-4000-8000-000000000002',
        agentDispatched: false,
        deduplicated: false,
      });

    renderPage();
    expect((await screen.findAllByText('Ana Paula Ribeiro')).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: /^Proposta$/ }));
    const generateProposal = await screen.findByRole('button', { name: 'Gerar proposta e abrir prévia' });
    await waitFor(() => expect(generateProposal).toBeEnabled());
    fireEvent.click(generateProposal);

    await waitFor(() => expect(moveFunnel).toHaveBeenCalledTimes(2));
    const firstCommand = moveFunnel.mock.calls[0][2];
    const retryCommand = moveFunnel.mock.calls[1][2];
    expect(firstCommand).toMatchObject({ expectedBoardVersion: 0, sourceColumnId: 'triagem_sdr' });
    expect(retryCommand).toMatchObject({ expectedBoardVersion: 1, sourceColumnId: 'triagem_sdr' });
    expect(retryCommand.operationId).toBe(firstCommand.operationId);
    expect(await screen.findByRole('heading', { name: 'Prévia da proposta' })).toBeInTheDocument();
  });

  it('shows an honest API error without rendering contacts or handoff controls', async () => {
    mockEmptyCapabilityQueries();
    vi.spyOn(klmApi, 'threads').mockRejectedValue(new Error('offline'));
    vi.spyOn(klmApi, 'funnel').mockRejectedValue(new Error('offline'));
    vi.spyOn(klmApi, 'followUps').mockResolvedValue([]);
    vi.spyOn(klmApi, 'flows').mockResolvedValue([]);
    const updateThread = vi.spyOn(klmApi, 'updateThread');

    renderPage();
    const dataStatus = await screen.findByTestId('conversation-data-status');
    await waitFor(() => {
      expect(dataStatus).toHaveTextContent('Não foi possível carregar as conversas');
      expect(dataStatus).toHaveTextContent('A tela permanecerá vazia até que a API volte a responder.');
    });
    expect(screen.queryAllByTestId('conversation-contact')).toHaveLength(0);
    expect(screen.queryByRole('button', { name: 'Assumir' })).not.toBeInTheDocument();
    expect(screen.queryByText('Ana Paula R.')).not.toBeInTheDocument();
    expect(screen.queryByText('Zolgensma')).not.toBeInTheDocument();
    expect(updateThread).not.toHaveBeenCalled();
  });

  it('renders a truthful empty workspace without seeding contacts or messages', async () => {
    mockEmptyCapabilityQueries();
    vi.spyOn(klmApi, 'threads').mockResolvedValue([]);
    vi.spyOn(klmApi, 'funnel').mockResolvedValue({ boardVersion: 0, stages: [], metrics: {}, cards: [] });
    vi.spyOn(klmApi, 'followUps').mockResolvedValue([]);
    vi.spyOn(klmApi, 'flows').mockResolvedValue([]);
    whatsappServiceMocks.fetchWhatsAppConnections.mockResolvedValueOnce([{
      id: 'whatsapp-connection-1',
      connectionType: 'OFFICIAL_MANUAL',
      isDefault: true,
      status: 'connected',
      webhookRegistered: true,
      updatedAt: '2026-08-07T12:00:00Z',
    } satisfies WhatsAppConnectionSummary]);

    renderPage();

    expect((await screen.findAllByText(/Nenhuma conversa persistida/)).length).toBeGreaterThan(0);
    expect(await screen.findByText(/O WhatsApp está conectado/i)).toBeInTheDocument();
    expect(screen.getByText(/WhatsApp conectado\. Novos atendimentos/i)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Conectar um canal' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Gerenciar conexão' })).toHaveAttribute('href', '/app/conexoes');
    expect(screen.queryAllByTestId('conversation-contact')).toHaveLength(0);
    expect(screen.queryByText('Meu plano negou o Zolgensma.')).not.toBeInTheDocument();
    expect(screen.queryByText('SLA 02:14')).not.toBeInTheDocument();
    expect(screen.queryByText(/Urgência: 72h/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Disparo: docs pendentes/)).not.toBeInTheDocument();
  });

  it('starts a new WhatsApp conversation with an approved Meta template', async () => {
    mockEmptyCapabilityQueries();
    vi.spyOn(klmApi, 'threads').mockResolvedValue([]);
    vi.spyOn(klmApi, 'funnel').mockResolvedValue({ boardVersion: 0, stages: [], metrics: {}, cards: [] });
    vi.spyOn(klmApi, 'followUps').mockResolvedValue([]);
    vi.spyOn(klmApi, 'flows').mockResolvedValue([]);
    whatsappServiceMocks.fetchWhatsAppConnections.mockResolvedValue([{
      id: 'whatsapp-connection-1',
      connectionType: 'OFFICIAL_MANUAL',
      isDefault: true,
      status: 'connected',
      webhookRegistered: true,
      displayPhoneNumber: '+55 61 99999-9999',
      updatedAt: '2026-08-07T12:00:00Z',
    } satisfies WhatsAppConnectionSummary]);
    campaignServiceMocks.fetchMetaTemplates.mockResolvedValue([{
      id: 'template-1',
      whatsappConnectionId: 'whatsapp-connection-1',
      name: 'primeiro_contato',
      category: 'UTILITY',
      language: 'pt_BR',
      status: 'approved',
      components: [{ type: 'BODY', text: 'Olá {{1}}, podemos conversar?' }],
      rawPayload: {},
      lastSyncedAt: '2026-08-07T12:00:00Z',
      updatedAt: '2026-08-07T12:00:00Z',
    }]);
    whatsappServiceMocks.startWhatsAppConversation.mockResolvedValue({
      contact: { id: 'contact-1' },
      messages: [{ messageMetadata: { canonical: { threadId: 'thread-1' } } }],
    });

    renderPage();
    const newConversationButton = await screen.findByRole('button', { name: 'Nova conversa' });
    await waitFor(() => expect(newConversationButton).toBeEnabled());
    fireEvent.click(newConversationButton);
    expect(await screen.findByRole('heading', { name: 'Nova conversa no WhatsApp' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Nome do contato'), { target: { value: 'Joana Cliente' } });
    fireEvent.change(screen.getByLabelText('Telefone com DDI'), { target: { value: '(61) 99999-9999' } });
    fireEvent.click(screen.getByLabelText('Template aprovado'));
    fireEvent.click(await screen.findByText(/primeiro_contato/));
    fireEvent.change(screen.getByLabelText('Variáveis do corpo'), { target: { value: 'Joana' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enviar template e abrir conversa' }));

    await waitFor(() => expect(whatsappServiceMocks.startWhatsAppConversation).toHaveBeenCalledWith({
      connectionId: 'whatsapp-connection-1',
      metaTemplateId: 'template-1',
      phone: '5561999999999',
      contactName: 'Joana Cliente',
      headerParameters: [],
      bodyParameters: ['Joana'],
    }));
  });

  it('distinguishes an empty persisted history from a failed history request', async () => {
    mockConnectedData();
    const { unmount } = renderPage();
    expect(await screen.findByText('Nenhuma mensagem registrada')).toBeInTheDocument();
    expect(screen.queryByText('Meu plano negou o Zolgensma. Tenho a negativa e o relatório médico em PDF.')).not.toBeInTheDocument();

    unmount();
    vi.restoreAllMocks();
    mockConnectedData();
    vi.mocked(klmApi.messages).mockRejectedValue(new Error('history offline'));
    renderPage();

    expect(await screen.findByText('Não foi possível carregar o histórico')).toBeInTheDocument();
    expect(screen.queryByText('Nenhuma mensagem registrada')).not.toBeInTheDocument();
    expect(screen.queryByText('Meu plano negou o Zolgensma. Tenho a negativa e o relatório médico em PDF.')).not.toBeInTheDocument();
  });

  it('persists human handoff on the selected real thread', async () => {
    mockConnectedData();
    const updateThread = vi.spyOn(klmApi, 'updateThread').mockResolvedValue({ ...thread, status: 'human_active' });

    renderPage();
    expect((await screen.findAllByText('Ana Paula Ribeiro')).length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByRole('button', { name: 'Assumir' })[0]);

    await waitFor(() => expect(updateThread).toHaveBeenCalledWith(threadId, { status: 'human_active' }));
    expect(await screen.findByText('Você assumiu o atendimento.')).toBeInTheDocument();
  });

  it('keeps thread-only tools available and blocks case tools for an orphan conversation', async () => {
    mockEmptyCapabilityQueries();
    mockCaseQueries();
    const orphanThread: KlmThread = {
      ...thread,
      id: 'orphan-thread',
      matterId: null,
      subject: null,
      objectName: null,
      ownerAgentKey: null,
      threadMetadata: { clientName: 'Contato sem card' },
    };
    vi.spyOn(klmApi, 'threads').mockResolvedValue([orphanThread]);
    vi.spyOn(klmApi, 'funnel').mockResolvedValue({ boardVersion: 0, stages: [], metrics: {}, cards: [] });
    vi.spyOn(klmApi, 'followUps').mockResolvedValue([]);
    vi.spyOn(klmApi, 'flows').mockResolvedValue(flows);
    vi.spyOn(klmApi, 'messages').mockResolvedValue([]);

    renderPage();
    expect((await screen.findAllByText('Contato sem card')).length).toBeGreaterThan(0);

    expect(screen.getByRole('button', { name: 'CRM' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Pedir docs' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Agendar' })).toBeDisabled();
    expect(screen.getByRole('button', { name: /^Proposta$/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Áudio' })).toBeEnabled();
    expect(screen.getAllByRole('button', { name: 'Assumir' })[0]).toBeEnabled();
    expect(screen.getByPlaceholderText('Escreva ou aprove a sugestão. Nada sai como humano sem você enviar.')).toBeEnabled();
    expect(screen.getByRole('button', { name: 'CRM' })).toHaveAttribute('title', 'Vincule esta conversa a um caso antes de usar a ferramenta.');
  }, 10_000);

  it('explains permission blocks without replacing persisted conversation data', async () => {
    permissionMock.mockImplementation((module: string, action: string) => {
      if (module === 'mensagens' && action === 'send_message') return false;
      if (module === 'automations' && action === 'manage') return false;
      if (module === 'kanban' && action === 'edit') return false;
      return true;
    });
    mockConnectedData();

    renderPage();
    expect((await screen.findAllByText('Ana Paula Ribeiro')).length).toBeGreaterThan(0);

    expect(screen.getByPlaceholderText('Escreva ou aprove a sugestão. Nada sai como humano sem você enviar.').closest('[aria-disabled="true"]')).not.toBeNull();
    expect(screen.getAllByRole('button', { name: 'Assumir' })[0]).toBeDisabled();
    expect(screen.getAllByRole('button', { name: 'Fluxo' })[0]).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Pedir docs' })).toBeDisabled();
    expect(screen.getByRole('button', { name: /^Proposta$/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Classificar potencial de Ana Paula Ribeiro com 3 estrelas' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Classificar potencial de Ana Paula Ribeiro com 3 estrelas' }).closest('[aria-disabled="true"]')).not.toBeNull();
    expect(screen.getByText('Somente leitura')).toBeInTheDocument();
  });

  it('activates any published workspace flow for the selected conversation', async () => {
    mockConnectedData();
    const run: KlmFlowRun = {
      id: 'run-recovery', flow_id: 'flow-recovery', matter_id: matterId, status: 'completed', current_node_id: 'message', context: { klmRuntime: 'local_simulation', externalActionsExecuted: 0, trace: [{ nodeId: 'start' }, { nodeId: 'review' }, { nodeId: 'message' }] }, pause_reason: null, started_at: '2026-07-13T12:05:00Z', completed_at: '2026-07-13T12:05:01Z', paused_at: null,
    };
    const runFlow = vi.spyOn(klmApi, 'runFlow').mockResolvedValue(run);
    const customerDelivery = vi.spyOn(klmApi, 'sendMessage');

    renderPage();
    expect((await screen.findAllByText('Ana Paula Ribeiro')).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: 'Fluxo' }));

    expect((await screen.findAllByText('Follow-up: documentos pendentes')).length).toBeGreaterThan(0);
    expect(screen.getByText('Recuperação: oportunidade sem resposta')).toBeInTheDocument();
    expect(screen.getByText('Fluxo personalizado do escritório')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('option', { name: /Recuperação: oportunidade sem resposta/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Ativar neste caso' }));

    await waitFor(() => expect(runFlow).toHaveBeenCalledWith('flow-recovery', matterId, {
      threadId,
      context: { source: 'conversation_flow_picker', flowName: 'Recuperação: oportunidade sem resposta' },
    }));
    expect(customerDelivery).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Ativar fluxo no atendimento' })).not.toBeInTheDocument());
    expect(await screen.findByText(/3 etapas e realizou 0 envios externos/)).toBeInTheDocument();
  });

  it('finds and controls a non-latest active run without duplicating the same flow', async () => {
    mockConnectedData();
    const completedLatest: KlmFlowRun = {
      id: 'run-custom-completed', flow_id: 'flow-custom', matter_id: matterId, status: 'completed', current_node_id: 'custom', context: { klmRuntime: 'local_simulation' }, pause_reason: null, started_at: '2026-07-13T12:10:00Z', completed_at: '2026-07-13T12:10:01Z', paused_at: null,
    };
    const activeRecovery: KlmFlowRun = {
      id: 'run-recovery-active', flow_id: 'flow-recovery', matter_id: matterId, status: 'waiting_human', current_node_id: 'review', context: { klmRuntime: 'local_simulation' }, pause_reason: 'Revisão', started_at: '2026-07-13T12:05:00Z', completed_at: null, paused_at: '2026-07-13T12:05:01Z',
    };
    const cancelledRecovery: KlmFlowRun = {
      ...activeRecovery,
      status: 'cancelled',
      completed_at: '2026-07-13T12:11:00Z',
    };
    vi.mocked(klmApi.flowRuns)
      .mockResolvedValueOnce([completedLatest, activeRecovery])
      .mockResolvedValue([completedLatest, cancelledRecovery]);
    const cancelFlowRun = vi.spyOn(klmApi, 'cancelFlowRun').mockResolvedValue(cancelledRecovery);
    const runFlow = vi.spyOn(klmApi, 'runFlow');

    renderPage();
    expect((await screen.findAllByText('Ana Paula Ribeiro')).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: 'Fluxo' }));
    fireEvent.click(screen.getByRole('option', { name: /Recuperação: oportunidade sem resposta/ }));

    expect(await screen.findByText('Execução atual: Aguardando humano.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Ativar neste caso' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Encerrar execução' }));

    await waitFor(() => expect(cancelFlowRun).toHaveBeenCalledWith('run-recovery-active'));
    expect(await screen.findByRole('button', { name: /Ativar neste caso|Executar novamente/ })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Encerrar execução' })).not.toBeInTheDocument();
    expect(runFlow).not.toHaveBeenCalled();
  });

  it('uses distinct message backgrounds for client, bot, lawyer, and internal notes', async () => {
    mockConnectedData([
      {
        id: 'message-client', threadId, matterId, channel: 'whatsapp', direction: 'inbound', authorType: 'client', authorLabel: 'Ana Paula', body: 'Mensagem do cliente', deliveryStatus: 'received', occurredAt: '2026-07-13T12:00:00Z', messageMetadata: {},
      },
      {
        id: 'message-bot', threadId, matterId, channel: 'whatsapp', direction: 'outbound', authorType: 'agent', authorLabel: 'SDR Saúde', body: 'Mensagem do bot', deliveryStatus: 'local_draft', occurredAt: '2026-07-13T12:01:00Z', messageMetadata: {},
      },
      {
        id: 'message-lawyer', threadId, matterId, channel: 'whatsapp', direction: 'outbound', authorType: 'human', authorLabel: 'Dr. Kleistr', body: 'Mensagem do advogado', deliveryStatus: 'local_draft', occurredAt: '2026-07-13T12:02:00Z', messageMetadata: {},
      },
      {
        id: 'message-internal', threadId, matterId, channel: 'internal', direction: 'internal', authorType: 'human', authorLabel: 'Dr. Kleistr', body: 'Nota privada do caso', deliveryStatus: 'received', occurredAt: '2026-07-13T12:03:00Z', messageMetadata: { visibility: 'internal_only' },
      },
      {
        id: 'message-agent-log', threadId, matterId, channel: 'internal', direction: 'internal', authorType: 'agent', authorLabel: 'CRM de precisão', body: 'Atualização técnica entre agentes', deliveryStatus: 'received', occurredAt: '2026-07-13T12:04:00Z', messageMetadata: { visibility: 'internal_only' },
      },
    ]);

    const { container } = renderPage();

    await screen.findByText('Mensagem do cliente');
    const clientProfile = container.querySelector('[data-message-profile="client"]');
    const botProfile = container.querySelector('[data-message-profile="bot"]');
    const lawyerProfile = container.querySelector('[data-message-profile="lawyer"]');
    const internalProfile = container.querySelector('[data-message-profile="internal"]');

    expect(clientProfile).toHaveAttribute('data-message-profile', 'client');
    expect(clientProfile?.firstElementChild).toHaveClass('bg-secondary');
    expect(botProfile).toHaveAttribute('data-message-profile', 'bot');
    expect(botProfile?.firstElementChild).toHaveClass('bg-[#eef0f2]');
    expect(lawyerProfile).toHaveAttribute('data-message-profile', 'lawyer');
    expect(lawyerProfile?.firstElementChild).toHaveClass('bg-[#d9fdd3]');
    expect(internalProfile).toHaveAttribute('data-message-profile', 'internal');
    expect(internalProfile?.firstElementChild).toHaveClass('bg-amber-50');
    expect(container.querySelectorAll('[data-message-profile="internal"]')).toHaveLength(1);
    expect(screen.getByText(/Visível somente para a equipe/)).toBeInTheDocument();
    fireEvent.click(screen.getByTitle('Abrir painel Caso 360'));
    expect(await screen.findByText('Atualização técnica entre agentes')).toBeInTheDocument();
  });

  it('shows channel projection state and safe attachment links inside the conversation', async () => {
    mockConnectedData([
      {
        id: 'message-attachment', threadId, matterId, channel: 'whatsapp', direction: 'inbound', authorType: 'client', authorLabel: 'Ana Paula', body: 'Segue a negativa.', deliveryStatus: 'received', occurredAt: '2026-07-13T12:00:00Z', messageMetadata: {
          source: 'chatwoot_webhook',
          chatwootMessageId: '901',
          attachments: [{ id: '31', name: 'negativa.pdf', fileType: 'file', localUrl: 'http://127.0.0.1:3100/rails/active_storage/blob/31' }],
        },
      },
      {
        id: 'message-projected', threadId, matterId, channel: 'whatsapp', direction: 'outbound', authorType: 'human', authorLabel: 'Dr. Kleistr', body: 'Documento recebido.', deliveryStatus: 'sent', occurredAt: '2026-07-13T12:01:00Z', messageMetadata: { chatwootMessageId: '902', chatwootProjection: 'local_only' },
      },
    ]);

    renderPage();

    expect(await screen.findByText('negativa.pdf')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Abrir' })).toHaveAttribute('href', 'http://127.0.0.1:3100/rails/active_storage/blob/31');
    fireEvent.click(screen.getByRole('button', { name: /Ver fonte no RAG/ }));
    expect(await screen.findByRole('heading', { name: 'Fonte no RAG' })).toBeInTheDocument();
    const ragSurface = screen.getByLabelText('Contexto: Fonte no RAG');
    expect(await within(ragSurface).findByText('Caso 360', { selector: 'h2' })).toBeInTheDocument();
    expect(ragSurface.querySelector('iframe')).not.toBeInTheDocument();
    expect(screen.getByTestId('conversation-route')).not.toHaveTextContent('/app/leads');
    expect(screen.getByText('recebida pelo canal conectado')).toBeInTheDocument();
    expect(screen.getByText('enviada pelo canal')).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/Chatwoot|Rocket\.Chat|ChatterMate|BottleCRM/);
  });

  it('saves notes through the internal channel without calling customer delivery', async () => {
    mockConnectedData();
    const internalMessage: KlmMessage = {
      id: 'note-saved', threadId, matterId, channel: 'internal', direction: 'internal', authorType: 'human', authorLabel: 'Dr. Kleistr', body: 'Revisar relatório antes da reunião.', deliveryStatus: 'received', occurredAt: '2026-07-13T12:03:00Z', messageMetadata: { visibility: 'internal_only' },
    };
    const sendInternal = vi.spyOn(klmApi, 'sendInternalMessage').mockResolvedValue(internalMessage);
    const sendCustomer = vi.spyOn(klmApi, 'sendMessage');

    renderPage();
    expect((await screen.findAllByText('Ana Paula Ribeiro')).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: 'Nota interna' }));
    expect(screen.getByText(/nunca é enviada ao cliente ou ao WhatsApp/i)).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('Escreva uma nota privada para agentes e equipe...'), { target: { value: 'Revisar relatório antes da reunião.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar nota interna' }));

    await waitFor(() => expect(sendInternal).toHaveBeenCalledWith(
      threadId,
      'Revisar relatório antes da reunião.',
      'Kleist Monteiro',
    ));
    expect(sendCustomer).not.toHaveBeenCalled();
  });

  it('turns an agent mention into an internal note and returns the conversation to that agent', async () => {
    mockConnectedData();
    vi.spyOn(klmApi, 'agents').mockResolvedValue({
      agents: [{
        key: 'orquestrador',
        name: 'Orquestrador',
        role: 'coordination',
        group: 'governanca',
        state: 'active',
        qualityScore: 100,
        queueSize: 0,
        description: 'Coordena a continuidade da conversa.',
        tools: [],
        gate: 'internal',
        permissions: { readMatter: true, writeCrm: true, createDraft: true, sendExternal: false, requestHuman: true },
        modelPolicy: { strategy: 'local', provider: 'local', model: 'runtime', taskClass: 'coordination', modelApiEnabled: false, localModelEnabled: true, simulatorAvailable: true, channelDeliveryEnabled: false, externalEnabled: false },
      }],
    } as Awaited<ReturnType<typeof klmApi.agents>>);
    const internalMessage: KlmMessage = {
      id: 'agent-note', threadId, matterId, channel: 'internal', direction: 'internal', authorType: 'human', authorLabel: 'Kleist Monteiro', body: '@orquestrador continue o atendimento conforme o playbook.', deliveryStatus: 'received', occurredAt: '2026-07-13T12:04:00Z', messageMetadata: { visibility: 'internal_only' },
    };
    const sendInternal = vi.spyOn(klmApi, 'sendInternalMessage').mockResolvedValue(internalMessage);
    const runAgent = vi.spyOn(klmApi, 'runAgent').mockResolvedValue({
      runs: [], messageIds: [], approvalId: null, deduplicated: false, externalActionsExecuted: 0, runtime: 'local', policyVersion: 'test',
    });
    const sendCustomer = vi.spyOn(klmApi, 'sendMessage');

    renderPage();
    const composer = await screen.findByTestId('conversation-composer');
    fireEvent.change(composer, { target: { value: '@orquestrador continue o atendimento conforme o playbook.' } });

    expect(await screen.findByText('Registro privado da equipe')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Salvar nota interna' }));

    await waitFor(() => expect(sendInternal).toHaveBeenCalledWith(
      threadId,
      '@orquestrador continue o atendimento conforme o playbook.',
      'Kleist Monteiro',
    ));
    await waitFor(() => expect(runAgent).toHaveBeenCalledWith(expect.objectContaining({
      agentKey: 'orquestrador',
      matterId,
      threadId,
      requestHuman: false,
    })));
    expect(sendCustomer).not.toHaveBeenCalled();
  });

  it('uses the canonical conversation route without exposing a technical selector', async () => {
    mockConnectedData();
    const sentMessage: KlmMessage = {
      id: 'hybrid-route-message', threadId, matterId, channel: 'whatsapp', direction: 'outbound', authorType: 'human', authorLabel: 'Kleist Monteiro', body: 'Continuar pelo aplicativo conectado.', deliveryStatus: 'local_draft', occurredAt: '2026-07-13T12:05:00Z', messageMetadata: {},
    };
    const sendCustomer = vi.spyOn(klmApi, 'sendMessage').mockResolvedValue(sentMessage);
    vi.spyOn(klmApi, 'updateThread').mockResolvedValue({ ...thread, status: 'human_active' });

    renderPage();
    expect((await screen.findAllByText('Ana Paula Ribeiro')).length).toBeGreaterThan(0);
    expect(screen.queryByRole('combobox', { name: 'Canal e rota do envio' })).not.toBeInTheDocument();
    const composer = screen.getByPlaceholderText('Escreva ou aprove a sugestão. Nada sai como humano sem você enviar.');
    fireEvent.change(composer, { target: { value: 'Continuar pelo aplicativo conectado.' } });
    expect(composer.parentElement).toContainElement(screen.getByTestId('conversation-composer-schedule'));
    expect(composer.parentElement).toContainElement(screen.getByTestId('conversation-composer-send'));
    fireEvent.keyDown(composer, { key: 'Enter', code: 'Enter' });

    await waitFor(() => expect(sendCustomer).toHaveBeenCalledWith(
      threadId,
      'Continuar pelo aplicativo conectado.',
      'whatsapp',
      'Kleist Monteiro',
      {
        endpointId: 'endpoint-cloud',
        bindingId: 'binding-cloud',
        connectionId: 'connection-cloud',
        connectionKey: 'whatsapp-cloud-comercial',
        transport: 'cloud_api',
      },
    ));
    expect(await screen.findByText(/Envio via WhatsApp · Cloud API registrado/)).toBeInTheDocument();
  });

  it('opens saved responses when the operator types a slash command', async () => {
    mockConnectedData();
    renderPage();

    expect((await screen.findAllByText('Ana Paula Ribeiro')).length).toBeGreaterThan(0);
    fireEvent.change(
      screen.getByPlaceholderText('Escreva ou aprove a sugestão. Nada sai como humano sem você enviar.'),
      { target: { value: '/negativa' } },
    );

    expect(await screen.findByRole('heading', { name: 'Operação do atendimento' })).toBeInTheDocument();
  });

  it('keeps response and internal-note drafts isolated when switching modes', async () => {
    mockConnectedData();
    renderPage();
    expect((await screen.findAllByText('Ana Paula Ribeiro')).length).toBeGreaterThan(0);

    const responseComposer = screen.getByPlaceholderText('Escreva ou aprove a sugestão. Nada sai como humano sem você enviar.');
    fireEvent.change(responseComposer, { target: { value: 'Resposta ainda em revisão.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Nota interna' }));
    fireEvent.change(screen.getByPlaceholderText('Escreva uma nota privada para agentes e equipe...'), { target: { value: 'Nota privada em elaboração.' } });

    fireEvent.click(screen.getByRole('button', { name: 'Resposta' }));
    expect(screen.getByPlaceholderText('Escreva ou aprove a sugestão. Nada sai como humano sem você enviar.')).toHaveValue('Resposta ainda em revisão.');

    fireEvent.click(screen.getByRole('button', { name: 'Nota interna' }));
    expect(screen.getByPlaceholderText('Escreva uma nota privada para agentes e equipe...')).toHaveValue('Nota privada em elaboração.');
  });

  it('removes a failed optimistic message and restores its outbound draft', async () => {
    mockConnectedData();
    vi.spyOn(klmApi, 'sendMessage').mockRejectedValue(new Error('database unavailable'));
    vi.spyOn(klmApi, 'updateThread').mockResolvedValue({ ...thread, status: 'human_active' });
    renderPage();
    expect((await screen.findAllByText('Ana Paula Ribeiro')).length).toBeGreaterThan(0);

    const composer = screen.getByPlaceholderText('Escreva ou aprove a sugestão. Nada sai como humano sem você enviar.');
    fireEvent.change(composer, { target: { value: 'Mensagem que precisa voltar ao rascunho.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Preparar envio' }));

    expect(await screen.findByText(/O rascunho foi restaurado/)).toBeInTheDocument();
    expect(composer).toHaveValue('Mensagem que precisa voltar ao rascunho.');
    expect(screen.queryByText('Mensagem que precisa voltar ao rascunho.', { selector: 'p' })).not.toBeInTheDocument();
  });

  it('refreshes the selected conversation when an omnichannel event arrives', async () => {
    const initialMessage: KlmMessage = {
      id: 'message-initial', threadId, matterId, channel: 'whatsapp', direction: 'inbound', authorType: 'client', authorLabel: 'Ana', body: 'Mensagem inicial', deliveryStatus: 'received', occurredAt: '2026-07-13T12:00:00Z', messageMetadata: {},
    };
    const realtimeMessage: KlmMessage = {
      ...initialMessage,
      id: 'message-realtime',
      body: 'Mensagem recebida em tempo real',
      occurredAt: '2026-07-13T12:01:00Z',
    };
    mockConnectedData();
    vi.mocked(klmApi.messages)
      .mockResolvedValueOnce([initialMessage])
      .mockResolvedValue([initialMessage, realtimeMessage]);
    renderPage(`/app/conversas?matter=${matterId}&thread=${threadId}`);

    expect(await screen.findByText('Mensagem inicial')).toBeInTheDocument();
    window.dispatchEvent(new CustomEvent('klm:realtime', {
      detail: {
        event: 'OMNICHANNEL_EVENT_INGESTED',
        data: { thread_id: threadId, message_id: realtimeMessage.id },
      },
    }));

    expect(await screen.findByText('Mensagem recebida em tempo real')).toBeInTheDocument();
    expect(klmApi.messages).toHaveBeenCalledTimes(2);
  });

  it('shows a provider-accepted outbound message as sent instead of locally pending', async () => {
    mockConnectedData([{
      id: 'message-provider-sent',
      threadId,
      matterId,
      channel: 'whatsapp',
      direction: 'outbound',
      authorType: 'human',
      authorLabel: 'Kleist',
      body: 'Mensagem aceita pela Meta',
      deliveryStatus: 'sent',
      occurredAt: '2026-07-13T12:01:00Z',
      messageMetadata: {
        lastDeliveryReceiptId: 'receipt-1',
        lastProviderStatus: 'sent',
      },
    }]);
    renderPage(`/app/conversas?matter=${matterId}&thread=${threadId}`);

    expect(await screen.findByText('Mensagem aceita pela Meta')).toBeInTheDocument();
    expect(screen.getByText('enviada pelo canal')).toBeInTheDocument();
    expect(screen.queryByText('pendente local')).not.toBeInTheDocument();
  });

  it('never renders messages from a previous thread after switching while its request is pending', async () => {
    const firstMessages = deferred<KlmMessage[]>();
    const secondMessages = deferred<KlmMessage[]>();
    mockTwoThreadData((requestedThreadId) => requestedThreadId === threadId ? firstMessages.promise : secondMessages.promise);
    renderPage();
    expect((await screen.findAllByText('Ana Paula Ribeiro')).length).toBeGreaterThan(0);

    selectContact('Bruno Cardoso');
    await act(async () => secondMessages.resolve([{
      id: 'message-second', threadId: secondThreadId, matterId: secondMatterId, channel: 'whatsapp', direction: 'inbound', authorType: 'client', authorLabel: 'Bruno', body: 'Mensagem exclusiva da conversa B', deliveryStatus: 'received', occurredAt: '2026-07-13T13:00:00Z', messageMetadata: {},
    }]));
    expect(await screen.findByText('Mensagem exclusiva da conversa B')).toBeInTheDocument();

    await act(async () => firstMessages.resolve([{
      id: 'message-first', threadId, matterId, channel: 'whatsapp', direction: 'inbound', authorType: 'client', authorLabel: 'Ana', body: 'Mensagem atrasada da conversa A', deliveryStatus: 'received', occurredAt: '2026-07-13T12:00:00Z', messageMetadata: {},
    }]));
    expect(screen.queryByText('Mensagem atrasada da conversa A')).not.toBeInTheDocument();
    expect(screen.getByText('Mensagem exclusiva da conversa B')).toBeInTheDocument();
  });

  it('keeps outbound and internal drafts isolated by conversation', async () => {
    mockTwoThreadData();
    renderPage();
    expect((await screen.findAllByText('Ana Paula Ribeiro')).length).toBeGreaterThan(0);

    fireEvent.change(screen.getByPlaceholderText('Escreva ou aprove a sugestão. Nada sai como humano sem você enviar.'), { target: { value: 'Rascunho da Ana' } });
    fireEvent.click(screen.getByRole('button', { name: 'Nota interna' }));
    fireEvent.change(screen.getByPlaceholderText('Escreva uma nota privada para agentes e equipe...'), { target: { value: 'Nota da Ana' } });

    selectContact('Bruno Cardoso');
    expect(screen.getByPlaceholderText('Escreva uma nota privada para agentes e equipe...')).toHaveValue('');
    fireEvent.change(screen.getByPlaceholderText('Escreva uma nota privada para agentes e equipe...'), { target: { value: 'Nota do Bruno' } });
    fireEvent.click(screen.getByRole('button', { name: 'Resposta' }));
    expect(screen.getByPlaceholderText('Escreva ou aprove a sugestão. Nada sai como humano sem você enviar.')).toHaveValue('');
    fireEvent.change(screen.getByPlaceholderText('Escreva ou aprove a sugestão. Nada sai como humano sem você enviar.'), { target: { value: 'Rascunho do Bruno' } });

    selectContact('Ana Paula Ribeiro');
    expect(screen.getByPlaceholderText('Escreva ou aprove a sugestão. Nada sai como humano sem você enviar.')).toHaveValue('Rascunho da Ana');
    fireEvent.click(screen.getByRole('button', { name: 'Nota interna' }));
    expect(screen.getByPlaceholderText('Escreva uma nota privada para agentes e equipe...')).toHaveValue('Nota da Ana');
  });

  it('writes a delayed meeting draft only to the originating conversation', async () => {
    mockTwoThreadData();
    const pendingMeeting = deferred<KlmMeeting>();
    vi.spyOn(klmApi, 'createInternalBooking').mockReturnValue(pendingMeeting.promise);
    renderPage();
    expect((await screen.findAllByText('Ana Paula Ribeiro')).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: 'Agendar' }));
    fireEvent.click(await screen.findByRole('button', { name: '11:00' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar horário' }));
    await act(async () => pendingMeeting.resolve({
      id: 'meeting-delayed', version: 1, matterId, title: 'Reunião online: Ana Paula Ribeiro', startsAt: '2026-07-13T17:00:00Z', endsAt: '2026-07-13T17:45:00Z', status: 'scheduled', location: 'Online', meetingUrl: null, notes: 'Local',
    }));

    selectContact('Bruno Cardoso');
    expect(screen.getByPlaceholderText('Escreva ou aprove a sugestão. Nada sai como humano sem você enviar.')).toHaveValue('');
    selectContact('Ana Paula Ribeiro');
    expect((screen.getByPlaceholderText('Escreva ou aprove a sugestão. Nada sai como humano sem você enviar.') as HTMLTextAreaElement).value).toContain('Ana Paula Ribeiro');
  });

  it('writes a delayed proposal link only to the originating conversation', async () => {
    mockTwoThreadData();
    const pendingMove = deferred<Awaited<ReturnType<typeof klmApi.moveFunnel>>>();
    vi.spyOn(klmApi, 'moveFunnel').mockReturnValue(pendingMove.promise);
    renderPage();
    expect((await screen.findAllByText('Ana Paula Ribeiro')).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: /^Proposta$/ }));
    const generateProposal = await screen.findByRole('button', { name: 'Gerar proposta e abrir prévia' });
    await waitFor(() => expect(generateProposal).toBeEnabled());
    fireEvent.click(generateProposal);
    selectContact('Bruno Cardoso');
    await act(async () => pendingMove.resolve({
      matterId, columnId: 'proposta', microStatus: 'proposta_em_revisao', cardOrder: 0, boardVersion: 1, operationId: '00000000-0000-4000-8000-000000000003', agentDispatched: false, deduplicated: false,
    }));

    expect(screen.getByPlaceholderText('Escreva ou aprove a sugestão. Nada sai como humano sem você enviar.')).toHaveValue('');
    selectContact('Ana Paula Ribeiro');
    expect((screen.getByPlaceholderText('Escreva ou aprove a sugestão. Nada sai como humano sem você enviar.') as HTMLTextAreaElement).value).toContain(`/proposta/${matterId}`);
  });

  it('restores a failed send only in the originating conversation', async () => {
    mockTwoThreadData();
    const pendingSend = deferred<KlmMessage>();
    vi.spyOn(klmApi, 'sendMessage').mockReturnValue(pendingSend.promise);
    vi.spyOn(klmApi, 'updateThread').mockResolvedValue({ ...thread, status: 'human_active' });
    renderPage();
    expect((await screen.findAllByText('Ana Paula Ribeiro')).length).toBeGreaterThan(0);

    fireEvent.change(screen.getByPlaceholderText('Escreva ou aprove a sugestão. Nada sai como humano sem você enviar.'), { target: { value: 'Resposta pendente da Ana' } });
    fireEvent.click(screen.getByRole('button', { name: 'Preparar envio' }));
    selectContact('Bruno Cardoso');
    expect(screen.queryByText('Resposta pendente da Ana', { selector: 'p' })).not.toBeInTheDocument();
    await act(async () => pendingSend.reject(new Error('database unavailable')));

    expect(screen.getByPlaceholderText('Escreva ou aprove a sugestão. Nada sai como humano sem você enviar.')).toHaveValue('');
    selectContact('Ana Paula Ribeiro');
    expect(screen.getByPlaceholderText('Escreva ou aprove a sugestão. Nada sai como humano sem você enviar.')).toHaveValue('Resposta pendente da Ana');
    expect(screen.queryByText('Resposta pendente da Ana', { selector: 'p' })).not.toBeInTheDocument();
  });
});
