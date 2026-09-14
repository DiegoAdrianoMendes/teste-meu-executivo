/** @vitest-environment jsdom */
import type { ReactElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { TooltipProvider } from '@/components/ui/tooltip';
import HomePage from '@/klm/pages/HomePage';
import TrackingPage from '@/klm/pages/TrackingPage';
import QualityPage from '@/klm/pages/QualityPage';
import FunnelPage from '@/klm/pages/FunnelPage';
import { NativeContextTools } from '@/klm/components/NativeContextTools';
import {
  klmApi,
  type KlmFunnel,
  type KlmNativeCapability,
  type KlmNativeCapabilityRegistry,
  type KlmTrackingSummary,
} from '@/klm/api';
import * as appStore from '@/store/app-context';

vi.mock('@/klm/components/NativeModuleOverview', () => ({ NativeModuleOverview: () => null }));
vi.mock('@/klm/components/NativeModuleBar', () => ({ NativeModuleBar: () => null }));
vi.mock('@/klm/components/NativeCRMOperationsPanel', () => ({ NativeCRMOperationsPanel: () => null }));
vi.mock('@/klm/components/NativeAgentOperationsPanel', () => ({ NativeAgentOperationsPanel: () => null }));

beforeEach(() => {
  // Default liberado — `PermissionGate` (usado internamente por Tracking/Funnel) chama
  // `useAppStore()` incondicionalmente, então cada teste precisa de um spy ativo,
  // mesmo os que não exercitam permissões diretamente.
  vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => true } as unknown as ReturnType<typeof appStore.useAppStore>);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

function renderKlm(ui: ReactElement, initialEntry: string) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <MemoryRouter initialEntries={[initialEntry]}>{ui}</MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location-probe">{location.pathname}{location.search}</output>;
}

function mockPermissions(allowed = false) {
  vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => allowed } as unknown as ReturnType<typeof appStore.useAppStore>);
}

const runtime = {
  mode: 'local_simulation',
  localOnly: true,
  localInfrastructure: true,
  externalActionsEnabled: false,
  awsEnabled: false,
  modelApisEnabled: false,
  onlineApproved: false,
  localBootstrapEnabled: true,
  providers: {},
};

const trackingSummary: KlmTrackingSummary = {
  periodDays: 7,
  periodStart: '2026-07-11T00:00:00Z',
  totalEvents: 8,
  identifiedJourneys: 2,
  leads: 3,
  qualifiedLeads: 2,
  meetings: 1,
  clients: 1,
  adSpend: 1000,
  attributableRevenue: 4000,
  ignoredValueEvents: 0,
  conversionRate: 33.33,
  cac: 1000,
  roas: 4,
  byChannel: [
    { channel: 'google_ads', value: 4000, events: 5, leads: 2, qualifiedLeads: 2, meetings: 1, clients: 1, adSpend: 800, attributableRevenue: 4000, ignoredValueEvents: 0, conversionRate: 50, cac: 800, roas: 5 },
    { channel: 'tiktok_ads', value: 0, events: 3, leads: 1, qualifiedLeads: 0, meetings: 0, clients: 0, adSpend: 200, attributableRevenue: 0, ignoredValueEvents: 0, conversionRate: 0, cac: null, roas: 0 },
  ],
  eventCoverage: [{ event: 'lead_created', count: 3, covered: true }],
  timeTrend: [],
  journeys: [],
  remarketing: [],
  attributionCoverage: { conversations: 2, trackedConversations: 2, untrackedConversations: 0, trackingRate: 100, conversionEvents: 1, attributedConversions: 1, unattributedConversions: 0, attributedConversionRate: 100, feedbackEligibleConversions: 1, feedbackBlockedNoEvidence: 0, fullHierarchyTouches: 1, partialHierarchyTouches: 0 },
  attributionRows: [],
  funnelSteps: [],
  insights: [],
  serviceQuality: { findings: 0, activeFindings: 0, criticalFindings: 0, unansweredLeads: 0, deliveryMismatches: 0, duplicateOutbound: 0, resolvedFindings: 0, auditedThreads: 2, qualityScore: 100 },
  conversionFeedback: { prepared: 0, blockedExternal: 0, awaitingApproval: 0, approvedLocal: 0, rejected: 0, duplicatePrevented: 0, payloadReady: 0, configurationBlocked: 0, preparedProofs: 0, providerReceipts: 0, failedReceipts: 0, providers: {} },
  feedbackEvents: [],
  dailyChannels: [],
  containsLocalFixtureData: false,
  generatedAt: '2026-07-18T12:00:00Z',
};

const homeFunnel: KlmFunnel = {
  boardVersion: 1,
  stages: [
    { id: 'entrada_api', label: 'Entrada API', count: 2 },
    { id: 'analise_api', label: 'Análise API', count: 1 },
  ],
  cards: [],
  metrics: {},
};

function mockHomeQueries() {
  vi.spyOn(klmApi, 'dashboard').mockResolvedValue({ metrics: { activeThreads: 0 }, funnel: [], decisions: [], runtime });
  vi.spyOn(klmApi, 'kpis').mockResolvedValue({
    topMetrics: [
      { key: 'contracts', label: 'Contratos', value: 0, unit: 'count' },
      { key: 'pipeline', label: 'Pipeline', value: 0, unit: 'BRL' },
    ],
    families: [],
    channels: [],
    periodDays: 1,
    subtheme: null,
    generatedAt: '2026-07-18T12:00:00Z',
    source: 'local_database',
  });
  vi.spyOn(klmApi, 'funnel').mockResolvedValue(homeFunnel);
  vi.spyOn(klmApi, 'financeOverview').mockResolvedValue({ totalOpenReceivables: 0, overdueAmount: 0, scheduledAmount: 0, paidAmount: 0, pendingReminders: 0, entriesDueSoon: 0, upcomingEntries: [], highlights: [] });
  vi.spyOn(klmApi, 'quality').mockResolvedValue({ score: 100, metrics: {}, dimensions: [], reviewQueue: [], generatedAt: '2026-07-18T12:00:00Z' });
  vi.spyOn(klmApi, 'tracking').mockResolvedValue({ ...trackingSummary, periodDays: 1 });
}

describe('contextual capability navigation contracts', () => {
  it('sends real stage and channel filters from Home to their destinations', async () => {
    mockPermissions(false);
    mockHomeQueries();
    renderKlm(
      <Routes>
        <Route path="/app" element={<HomePage />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>,
      '/app',
    );

    fireEvent.click(await screen.findByRole('button', { name: /Análise API/ }));
    expect(await screen.findByTestId('location-probe')).toHaveTextContent('/app/funil?stage=analise_api');

    cleanup();
    renderKlm(
      <Routes>
        <Route path="/app" element={<HomePage />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>,
      '/app',
    );

    expect(await screen.findByText(/Etapas e canais abrem as respectivas visões com os filtros exibidos/)).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: /Canal com mais resultado/ }));
    expect(await screen.findByTestId('location-probe')).toHaveTextContent('/app/rastreamento?channel=google_ads&period=1&tab=canais');
  });

  it('consumes period, tab and channel from Tracking query and names a GET as a reading update', async () => {
    const tracking = vi.spyOn(klmApi, 'tracking').mockResolvedValue(trackingSummary);
    renderKlm(
      <Routes>
        <Route path="/app/rastreamento" element={<><TrackingPage /><LocationProbe /></>} />
      </Routes>,
      '/app/rastreamento?period=7&tab=eventos&channel=google_ads',
    );

    expect(await screen.findByRole('heading', { name: 'Saúde dos eventos' })).toBeInTheDocument();
    expect(tracking).toHaveBeenCalledWith(7);
    expect(screen.getByRole('button', { name: 'Atualizar leitura' })).toBeInTheDocument();
    expect(screen.queryByText(/Auditar eventos|Auditoria concluída/)).not.toBeInTheDocument();

    const channelsTab = screen.getByRole('tab', { name: 'Canais' });
    fireEvent.mouseDown(channelsTab, { button: 0, ctrlKey: false });
    await waitFor(() => expect(channelsTab).toHaveAttribute('data-state', 'active'));
    expect(await screen.findByRole('button', { name: /Google Ads/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Tiktok Ads/ })).not.toBeInTheDocument();
    expect(screen.getByDisplayValue('Google Ads')).toBeInTheDocument();
    expect(screen.getByTestId('location-probe')).toHaveTextContent('period=7&tab=canais&channel=google_ads');

    fireEvent.click(screen.getByRole('button', { name: 'Atualizar leitura' }));
    await waitFor(() => expect(tracking.mock.calls.length).toBeGreaterThan(1));
    expect(await screen.findByText(/Leitura atualizada: 8 eventos retornados/)).toBeInTheDocument();
  });

  it('shows an explicit failure state instead of zeroed tracking KPIs', async () => {
    vi.spyOn(klmApi, 'tracking').mockRejectedValue(new Error('tracking unavailable'));
    renderKlm(
      <Routes>
        <Route path="/app/rastreamento" element={<TrackingPage />} />
      </Routes>,
      '/app/rastreamento?period=7',
    );

    expect(await screen.findByRole('heading', { name: 'A leitura de tracking falhou' })).toBeInTheDocument();
    expect(screen.getByText(/indicadores foram ocultados/)).toBeInTheDocument();
    expect(screen.queryByText('R$ 0,00')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Tentar novamente' })).toBeInTheDocument();
  });

  it('separates local preparation, immutable proof and official provider receipt', async () => {
    vi.spyOn(klmApi, 'tracking').mockResolvedValue({
      ...trackingSummary,
      conversionFeedback: {
        prepared: 1,
        blockedExternal: 1,
        awaitingApproval: 0,
        approvedLocal: 0,
        rejected: 0,
        duplicatePrevented: 2,
        payloadReady: 1,
        configurationBlocked: 0,
        preparedProofs: 1,
        providerReceipts: 0,
        failedReceipts: 0,
        providers: { meta_ads: 1 },
      },
      feedbackEvents: [{
        intentId: 'intent-meta-1',
        provider: 'meta_ads',
        sourceEventId: 'event-sale-1',
        sourceTouchEventId: 'touch-meta-1',
        conversionEvent: 'client',
        happenedAt: '2026-07-18T12:00:00Z',
        value: 4_000,
        currency: 'BRL',
        status: 'blocked_external',
        providerEventId: 'klm-meta-event-1',
        matchKeyNames: ['fbc', 'email_sha256'],
        payloadSchemaVersion: 'klm.ads.meta-capi.v1',
        matchingMode: 'click_id_and_hashed_identity',
        readyForDispatch: true,
        missingConfiguration: [],
        privacyGuard: {
          rawContactDataIncluded: false,
          sensitiveMatterDataIncluded: false,
          allowedDataClasses: ['click_ids', 'hashed_contact_identifiers'],
          excludedDataClasses: ['cpf', 'health_data', 'conversation'],
        },
        payloadSha256: 'a'.repeat(64),
        proofState: 'prepared_local',
        receiptState: null,
        receiptCount: 0,
        dedupeKey: 'ads-conversion-feedback:meta_ads:event-sale-1',
        externalActionsExecuted: 0,
      }],
    });

    renderKlm(
      <Routes><Route path="/app/rastreamento" element={<TrackingPage />} /></Routes>,
      '/app/rastreamento?period=7&tab=feedback',
    );

    expect(await screen.findByRole('heading', { name: 'Fila de feedback aos provedores' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('Payloads prontos').parentElement).toHaveTextContent('1'));
    expect(screen.getByText('Provas imutáveis').parentElement).toHaveTextContent('1');
    expect(screen.getByText('Repetições bloqueadas').parentElement).toHaveTextContent('2');
    expect(screen.getByText('Recibos oficiais').parentElement).toHaveTextContent('0');
    expect(screen.getByText(/CPF, caso, saúde, documentos, conversa e notas internas não são mapeados/)).toBeInTheDocument();
    expect(screen.getByText('sem recibo oficial')).toBeInTheDocument();
    expect(screen.getByText('klm.ads.meta-capi.v1')).toBeInTheDocument();
    expect(screen.getByText('0 chamada(s)')).toBeInTheDocument();
  });

  it('replaces the static connector catalog with the native acquisition center', async () => {
    vi.spyOn(klmApi, 'tracking').mockResolvedValue(trackingSummary);
    vi.spyOn(klmApi, 'omnichannelConnections').mockResolvedValue([]);
    vi.spyOn(klmApi, 'leadCaptures').mockResolvedValue({
      items: [],
      total: 0,
      hasMore: false,
      countsByProvider: {},
      countsByState: {},
      generatedAt: '2026-07-20T12:00:00Z',
    });
    renderKlm(
      <Routes>
        <Route path="/app/rastreamento" element={<TrackingPage />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>,
      '/app/rastreamento?tab=eventos',
    );

    expect(await screen.findByRole('heading', { name: 'Conexões de aquisição' })).toBeInTheDocument();
    expect(screen.queryByText('Catálogo esperado de conectores')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Abrir central de captação' }));
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Captação e anúncios' })).toHaveAttribute('data-state', 'active'));
    expect(await screen.findByRole('heading', { name: 'Operação de anúncios' })).toBeInTheDocument();
  });

  it('records Quality decisions as internal notes and publication work as a local intent', async () => {
    mockPermissions(true);
    vi.spyOn(klmApi, 'quality').mockResolvedValue({
      score: 70,
      metrics: {},
      dimensions: [{ key: 'guardrail', label: 'Gate externo', score: 70 }],
      reviewQueue: [{ threadId: 'thread-quality', matterId: 'matter-quality', reason: 'score_baixo', score: 45 }],
      generatedAt: '2026-07-18T12:00:00Z',
    });
    vi.spyOn(klmApi, 'messages').mockResolvedValue([]);
    const sendInternalMessage = vi.spyOn(klmApi, 'sendInternalMessage').mockResolvedValue({
      id: 'message-note', threadId: 'thread-quality', matterId: 'matter-quality', channel: 'internal', direction: 'internal', authorType: 'human', authorLabel: 'Revisão de qualidade', body: 'nota', deliveryStatus: 'stored', occurredAt: '2026-07-18T12:00:00Z', messageMetadata: {},
    });
    const createIntent = vi.spyOn(klmApi, 'createDomainExternalIntent').mockResolvedValue({
      id: 'quality-intent', workspaceId: 'workspace', requestedByUserId: 'user', matterId: 'matter-quality', actionType: 'agent_quality_version_publish', provider: 'chattermate', idempotencyKey: 'quality', status: 'awaiting_approval', payload: {}, reason: 'Aguardando aprovação local.', createdAt: '2026-07-18T12:00:00Z', updatedAt: '2026-07-18T12:00:00Z',
    });
    vi.spyOn(klmApi, 'approveExternalIntentLocally').mockResolvedValue({
      id: 'quality-intent', workspaceId: 'workspace', requestedByUserId: 'user', matterId: 'matter-quality', actionType: 'agent_quality_version_publish', provider: 'chattermate', idempotencyKey: 'quality', status: 'approved_local', payload: {}, reason: 'Aprovada localmente.', createdAt: '2026-07-18T12:00:00Z', updatedAt: '2026-07-18T12:00:01Z',
    });
    renderKlm(<QualityPage />, '/app/qualidade');

    const suggestion = await screen.findByRole('button', { name: 'Registrar sugestão de ajuste' });
    expect(screen.queryByRole('button', { name: 'Aprovar ajuste' })).not.toBeInTheDocument();
    expect(screen.queryByText('Publicação supervisionada')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Intenção de versão' })).toBeInTheDocument();
    fireEvent.click(suggestion);
    await waitFor(() => expect(sendInternalMessage).toHaveBeenCalledTimes(1));
    expect(sendInternalMessage.mock.calls[0][1]).toContain('sugestão de ajuste registrada');
    expect(await screen.findByText(/Sugestão de ajuste registrada como nota interna/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Registrar intenção de versão' }));
    await waitFor(() => expect(createIntent).toHaveBeenCalledTimes(1));
    expect(createIntent).toHaveBeenCalledWith(expect.objectContaining({ domain: 'automation' }));
    expect(await screen.findByText(/Intenção de versão registrada localmente/)).toBeInTheDocument();
  });

  it('derives Funnel stages from the API, applies stage query and shows document count without a fake denominator', async () => {
    mockPermissions(false);
    vi.spyOn(klmApi, 'funnel').mockResolvedValue({
      boardVersion: 4,
      stages: [
        { id: 'captacao_api', label: 'Captação da API', count: 1 },
        { id: 'analise_api', label: 'Análise da API', count: 1 },
      ],
      cards: [
        { id: 'matter-captacao', clientId: 'client-1', name: 'Cliente captação', subject: 'tema_a', object: 'Objeto A', source: 'origem_a', owner: 'Equipe A', probability: 30, initialFee: 1000, projectedRevenue: 2000, slaDueAt: null, documents: 1, documentScore: 20, stage: 'captacao_api', microStatus: 'novo', cardOrder: 0 },
        { id: 'matter-analise', clientId: 'client-2', name: 'Cliente análise', subject: 'tema_b', object: 'Objeto B', source: 'origem_b', owner: 'Equipe B', probability: 70, initialFee: 2000, projectedRevenue: 4000, slaDueAt: null, documents: 2, documentScore: 60, stage: 'analise_api', microStatus: 'analise', cardOrder: 0 },
      ],
      metrics: { conversations: 2, qualified: 1, meetings: 0, paid: 0 },
    });
    renderKlm(
      <Routes>
        <Route path="/app/funil" element={<><FunnelPage /><LocationProbe /></>} />
      </Routes>,
      '/app/funil?stage=analise_api',
    );

    expect(await screen.findByTestId('funnel-column-analise_api')).toBeInTheDocument();
    expect(screen.queryByTestId('funnel-column-captacao_api')).not.toBeInTheDocument();
    expect(screen.getAllByText('Cliente análise')).toHaveLength(2);
    expect(screen.queryByText('Cliente captação')).not.toBeInTheDocument();
    expect(screen.getByText('2 documentos')).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent('2/5');
    expect(screen.queryByText('Alta probabilidade')).not.toBeInTheDocument();
    expect(screen.queryByText('Documentos pendentes')).not.toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Filtrar por etapa' })).toHaveTextContent('Análise da API');
    await waitFor(() => expect(screen.getByTestId('location-probe')).toHaveTextContent('stage=analise_api&matter=matter-analise'));
  });

  it('preserves matter, thread, flow and contextual query when NativeContextTools opens a native route', async () => {
    const capability: KlmNativeCapability = {
      id: 'communications.conversation-composer',
      domain: 'communications',
      key: 'conversation-composer',
      label: 'Compositor de conversa',
      category: 'Atendimento',
      description: 'Composição local.',
      disposition: 'implemented',
      exposure: 'native',
      mode: 'local_canonical',
      surface: 'Conversas',
      relatedRoutes: ['/app/conversas'],
      nativeRoute: '/app/capacidades/communications/conversation-composer?mode=review',
      verified: true,
      externalEffectVerified: false,
      reason: 'Disponível localmente.',
    };
    vi.spyOn(klmApi, 'capabilities').mockResolvedValue(capabilityRegistry(capability));
    renderKlm(
      <Routes>
        <Route path="/app/conversas" element={<NativeContextTools />} />
        <Route path="/app/capacidades/:domain/:capability" element={<LocationProbe />} />
      </Routes>,
      '/app/conversas?matter=matter-1&thread=thread-1&flow=flow-1&channel=whatsapp&period=7',
    );

    fireEvent.click(screen.getByRole('button', { name: 'Recursos conectados' }));
    expect(await screen.findByText('Compositor de conversa')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Abrir no Meu Executivo/ }));
    const destination = await screen.findByTestId('location-probe');
    const url = new URL(destination.textContent ?? '', 'http://klm.local');
    expect(url.pathname).toBe('/app/capacidades/communications/conversation-composer');
    expect(Object.fromEntries(url.searchParams)).toMatchObject({
      mode: 'review', matter: 'matter-1', thread: 'thread-1', flow: 'flow-1', channel: 'whatsapp', period: '7',
    });
    expect(document.body).not.toHaveTextContent(/Abrir motor/);
  });
});

function capabilityRegistry(capability: KlmNativeCapability): KlmNativeCapabilityRegistry {
  return {
    schemaVersion: '1.0', scope: 'klm-native-capabilities', capabilities: [capability],
    summary: {
      total: 1, available: 1, verified: 1, externalEffectVerified: 0,
      byDomain: { communications: 1, collaboration: 0, automation: 0, crm: 0 },
      byDisposition: { implemented: 1 },
    },
    verification: { state: 'verified', reason: 'Teste.', checks: 1 },
  };
}
