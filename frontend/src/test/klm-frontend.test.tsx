/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';
import type { ReactElement } from 'react';
import { BRAND, BRAND_INITIALS } from '@/lib/brand';
import { defaultWorkspaceMode, getNavigationItem, klmNavigation, mobileNavigation, navigationForWorkspaceMode } from '@/klm/navigation';
import { assertExternalActionsDisabled, isLocalSimulation, klmRuntime, resolveKlmRuntimeMode } from '@/klm/runtime';
import { KlmMetricCard, KlmPanel, KlmPill, KlmProgress } from '@/klm/components/Primitives';
import { NativeModuleBar } from '@/klm/components/NativeModuleBar';
import { NativeInboxRoutingPanel } from '@/klm/components/NativeInboxRoutingPanel';
import { NativeCRMOperationsPanel } from '@/klm/components/NativeCRMOperationsPanel';
import { NativeAgentOperationsPanel } from '@/klm/components/NativeAgentOperationsPanel';
import { NativeFlowRuntimePanel } from '@/klm/components/NativeFlowRuntimePanel';
import { NativeCollaborationPanel } from '@/klm/components/NativeCollaborationPanel';
import { initializeKlmTheme } from '@/klm/theme';
import HomePage from '@/klm/pages/HomePage';
import AreasPage from '@/klm/pages/AreasPage';
import ToolsPage from '@/klm/pages/ToolsPage';
import TiAgentPage from '@/klm/pages/TiAgentPage';
import AudioPage from '@/klm/pages/AudioPage';
import InternalPage from '@/klm/pages/InternalPage';
import KpisPage from '@/klm/pages/KpisPage';
import FunnelPage from '@/klm/pages/FunnelPage';
import AgendaPage from '@/klm/pages/AgendaPage';
import {
  FOLLOW_UP_RECONCILIATION_MS,
  meetingReconciliationMs,
} from '@/klm/agenda-reconciliation';
import { trustedAudioSource } from '@/klm/audio-media';
import OperationalModulePage from '@/klm/pages/OperationalModulePage';
import { buildTimelineRows } from '@/klm/timeline';
import { klmApi } from '@/klm/api';
import * as adminService from '@/services/admin';
import * as appStore from '@/store/app-context';
import * as whatsappService from '@/services/whatsapp';
import { mockEmptyCapabilityQueries } from '@/test/capability-fixtures';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const renderKlm = (ui: ReactElement, initialEntries = ['/app']) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
};

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location-probe">{location.pathname}{location.search}</output>;
}

const localRuntimeFixture = {
  mode: 'local_simulation',
  localOnly: true,
  localInfrastructure: true,
  externalActionsEnabled: false,
  awsEnabled: false,
  modelApisEnabled: false,
  onlineApproved: false,
  localBootstrapEnabled: true,
  providers: {},
} as const;

function mockAnalyticsData(cards: Awaited<ReturnType<typeof klmApi.funnel>>['cards'] = []) {
  vi.spyOn(klmApi, 'dashboard').mockResolvedValue({
    metrics: { leads: cards.length, qualified: cards.filter((card) => card.probability >= 60).length, activeThreads: 2, pendingApprovals: 1, dueFollowUps: 0, externalActions: 0 },
    funnel: [],
    decisions: [{ type: 'approval', count: 1, priority: 'high' }],
    runtime: localRuntimeFixture,
  });
  const kpisApi = vi.spyOn(klmApi, 'kpis').mockImplementation(async (periodDays, healthSubtheme) => ({
    topMetrics: [
      { key: 'new_leads', label: 'Leads novos', value: cards.length || 2, unit: 'count' },
      { key: 'qualified', label: 'Leads qualificados', value: 1, unit: 'count' },
      { key: 'cac', label: 'CAC cliente', value: 500, unit: 'BRL' },
      { key: 'lead_to_meeting', label: 'Lead para reunião', value: 50, unit: 'percent' },
      { key: 'contracts', label: 'Contratos', value: 1, unit: 'count' },
      { key: 'paid', label: 'Pagos', value: 0, unit: 'count' },
      { key: 'docs_complete', label: 'Docs completos', value: 50, unit: 'percent' },
      { key: 'pipeline', label: 'Pipeline projetado', value: 12000, unit: 'BRL' },
    ],
    families: [
      { key: 'acquisition', label: 'Aquisição', coverage: 4 },
      { key: 'documents', label: 'Docs e RAG', coverage: 1 },
      { key: 'finance', label: 'Financeiro', coverage: 2 },
    ],
    channels: [],
    periodDays: periodDays ?? null,
    subtheme: healthSubtheme ?? null,
    generatedAt: '2026-07-18T12:00:00Z',
    source: 'local_database',
    teamPerformance: {
      audience: 'workspace_team',
      periodDays: periodDays ?? 30,
      generatedAt: '2026-07-18T12:00:00Z',
      scoreFormula: '10 pontos por tarefa concluída e 2 por caso atribuído no período.',
      rows: [
        {
          id: 'workspace-1:user-1',
          name: 'Vendedora Exemplo',
          scopeName: 'Escritório Exemplo',
          role: 'member',
          primaryCount: 4,
          completedCount: 3,
          pendingCount: 1,
          qualityValue: 88,
          score: 38,
        },
      ],
    },
  }));
  const advancedKpisApi = vi.spyOn(klmApi, 'advancedKpis').mockImplementation(async (filters = {}) => ({
    generatedAt: '2026-07-18T12:00:00Z',
    periodDays: filters.periodDays ?? 30,
    previousPeriodDays: filters.periodDays ?? 30,
    catalogTotal: 430,
    totalRows: 2,
    rowsWithData: 2,
    coverageTotal: 2,
    coverageWithData: 2,
    truncated: false,
    appliedFilters: {
      legalAreaKey: filters.legalAreaKey ?? null,
      legalProductKey: filters.legalProductKey ?? null,
      category: filters.category ?? null,
    },
    categories: [
      { key: 'service', label: 'Conversas e atendimento', metricCount: 42, metricsWithData: 36 },
      { key: 'legal_areas', label: 'Áreas jurídicas', metricCount: 65, metricsWithData: 65 },
      { key: 'legal_products', label: 'Produtos jurídicos', metricCount: 300, metricsWithData: 300 },
    ],
    options: {
      legalAreas: [
        { value: 'saude_suplementar', label: 'Saúde suplementar' },
        { value: 'empresarial', label: 'Direito empresarial' },
      ],
      legalProducts: [
        { value: 'negativa_cobertura', label: 'Negativa de cobertura', parentValue: 'saude_suplementar' },
        { value: 'contratos_empresariais', label: 'Contratos empresariais', parentValue: 'empresarial' },
      ],
      channels: [{ value: 'whatsapp', label: 'WhatsApp' }],
      responsibleUsers: [{ value: 'user-1', label: 'Vendedora Exemplo' }],
      stages: [{ value: 'novo_lead', label: 'Novo Lead' }],
    },
    rows: [
      {
        key: 'service.response.median',
        label: 'Mediana de primeira resposta',
        category: 'service',
        categoryLabel: 'Conversas e atendimento',
        value: 180,
        previousValue: 240,
        deltaPct: -25,
        polarity: 'negative',
        unit: 'seconds',
        hasData: true,
        description: 'Tempo mediano entre a mensagem recebida e a primeira resposta.',
        source: 'omnichannel_messages',
        dimensions: {},
        note: null,
      },
      {
        key: 'legal_areas.empresarial.leads',
        label: 'Leads em Direito empresarial',
        category: 'legal_areas',
        categoryLabel: 'Áreas jurídicas',
        value: 4,
        previousValue: 2,
        deltaPct: 100,
        polarity: 'positive',
        unit: 'count',
        hasData: true,
        description: 'Leads criados na área jurídica selecionada.',
        source: 'matters.practice_area',
        dimensions: { legalAreaKey: 'empresarial' },
        note: null,
      },
    ],
  }));
  vi.spyOn(klmApi, 'funnel').mockResolvedValue({
    boardVersion: 3,
    stages: [
      { id: 'novo_lead', label: 'Novo lead', count: cards.filter((card) => card.stage === 'novo_lead').length },
      { id: 'triagem_sdr', label: 'Triagem', count: cards.filter((card) => card.stage === 'triagem_sdr').length },
      { id: 'documentos', label: 'Documentos', count: cards.filter((card) => card.stage === 'documentos').length },
      { id: 'reuniao', label: 'Reunião', count: cards.filter((card) => card.stage === 'reuniao').length },
      { id: 'proposta', label: 'Proposta', count: cards.filter((card) => card.stage === 'proposta').length },
      { id: 'contrato', label: 'Contrato', count: cards.filter((card) => card.stage === 'contrato').length },
      { id: 'pagamento', label: 'Pagamento', count: cards.filter((card) => card.stage === 'pagamento').length },
      { id: 'ganho', label: 'Fechado', count: cards.filter((card) => card.stage === 'ganho').length },
      { id: 'perdido', label: 'Perdido', count: cards.filter((card) => card.stage === 'perdido').length },
    ],
    metrics: { conversations: cards.length, qualified: cards.filter((card) => card.probability >= 60).length, meetings: cards.filter((card) => card.stage === 'reuniao').length, paid: cards.filter((card) => card.stage === 'ganho').length },
    cards,
  });
  vi.spyOn(klmApi, 'financeOverview').mockResolvedValue({ totalOpenReceivables: 6000, overdueAmount: 0, scheduledAmount: 6000, paidAmount: 4000, pendingReminders: 0, entriesDueSoon: 0, upcomingEntries: [], highlights: [] });
  vi.spyOn(klmApi, 'financeEntries').mockResolvedValue([]);
  vi.spyOn(klmApi, 'listFinanceCharges').mockResolvedValue([]);
  vi.spyOn(klmApi, 'feeArrangements').mockResolvedValue([]);
  vi.spyOn(klmApi, 'approvals').mockResolvedValue([]);
  vi.spyOn(klmApi, 'quality').mockResolvedValue({ score: 91, metrics: { externalMessagesSent: 0 }, dimensions: [{ key: 'guardrail', label: 'Guardrails', score: 100 }], reviewQueue: [], generatedAt: '2026-07-18T12:00:00Z' });
  vi.spyOn(klmApi, 'tracking').mockImplementation(async (periodDays) => ({
    periodDays: periodDays ?? null,
    periodStart: '2026-07-17T12:00:00Z',
    totalEvents: 8,
    identifiedJourneys: 2,
    leads: 2,
    qualifiedLeads: 1,
    meetings: 1,
    clients: 1,
    adSpend: 1000,
    attributableRevenue: 4000,
    ignoredValueEvents: 0,
    conversionRate: 50,
    cac: 1000,
    roas: 4,
    byChannel: [
      { channel: 'google_ads', value: 4000, events: 5, leads: 1, qualifiedLeads: 1, meetings: 1, clients: 1, adSpend: 800, attributableRevenue: 4000, ignoredValueEvents: 0, conversionRate: 100, cac: 800, roas: 5 },
      { channel: 'tiktok_ads', value: 0, events: 3, leads: 1, qualifiedLeads: 0, meetings: 0, clients: 0, adSpend: 200, attributableRevenue: 0, ignoredValueEvents: 0, conversionRate: 0, cac: null, roas: 0 },
    ],
    eventCoverage: [{ event: 'lead', count: 2, covered: true }],
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
  }));
  return { kpisApi, advancedKpisApi };
}

function mockBottleFunnelContext() {
  vi.spyOn(klmApi, 'nativeCRMFunnelContext').mockResolvedValue({
    projection: { linked: false, totalRecords: 0, availableRecords: 0, failedRecords: 0, staleRecords: 0, byEntityType: { contacts: 0, leads: 0, opportunities: 0, cases: 0, tasks: 0, financeEntries: 0 }, updatedAt: null, externalExecutionEnabled: false },
    counts: { accounts: 0, contacts: 0, leads: 0, opportunities: 0 }, urgentCounts: { overdueTasks: 0, followupsToday: 0, hotLeads: 0 }, pipeline: [], revenue: { totalPipeline: 0, weightedPipeline: 0, conversionRate: 0, currency: 'BRL' }, opportunityColumns: [], leadColumns: [], caseColumns: [], goals: [], agingConfig: [], notificationsUnread: 0, sensitiveDataHidden: false,
  });
}

function mockBrowserDownload() {
  const createObjectURL = vi.fn(() => 'blob:klm-local');
  const revokeObjectURL = vi.fn();
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL });
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
  return { createObjectURL, click };
}

function installPointerCaptureForRadix() {
  Object.defineProperty(HTMLElement.prototype, 'hasPointerCapture', { configurable: true, value: () => false });
  Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', { configurable: true, value: () => undefined });
  Object.defineProperty(HTMLElement.prototype, 'releasePointerCapture', { configurable: true, value: () => undefined });
}

const routeCases = [
  ['/app', 'Início'],
  ['/app/conversas', 'Conversas'],
  ['/app/leads', 'CRM'],
  ['/app/funil', 'CRM'],
  ['/app/kpis', 'KPIs e métricas'],
  ['/app/rastreamento', 'Rastreamento'],
  ['/app/qualidade', 'Agentes'],
  ['/app/ferramentas', 'Configurações'],
  ['/app/flows', 'Flows'],
  ['/app/equipe', 'Equipe interna'],
  ['/app/comentarios', 'Automações'],
  ['/app/aquisicao-paga', 'Tráfego pago'],
  ['/app/conexoes', 'Integrações e webhooks'],
  ['/app/agenda', 'Agenda'],
  ['/app/contratos', 'Contratos'],
  ['/app/financeiro', 'Financeiro'],
  ['/app/peticoes', 'Petições'],
  ['/app/audio', 'Configurações'],
  ['/app/agentes', 'Agentes'],
  ['/app/areas', 'Áreas e times'],
  ['/app/ti', 'TI Agent'],
  ['/app/configuracoes', 'Configurações'],
  ['/app/playbooks', 'Configurações'],
  ['/app/atividade', 'Atividade'],
] as const;

describe('KLM navigation contract', () => {
  it.each(routeCases)('resolves %s to %s', (path, label) => {
    expect(getNavigationItem(path)?.label).toBe(label);
  });

  it('keeps every primary route unique', () => {
    expect(new Set(klmNavigation.map((item) => item.path)).size).toBe(klmNavigation.length);
    expect(klmNavigation.map((item) => item.path)).not.toContain('/app/qualidade');
    expect(klmNavigation.map((item) => item.path)).not.toContain('/app/playbooks');
    expect(klmNavigation.map((item) => item.path)).not.toContain('/app/ferramentas');
    expect(klmNavigation.map((item) => item.path)).not.toContain('/app/conexoes');
    expect(klmNavigation.map((item) => item.path)).not.toContain('/app/audio');
    expect(klmNavigation.find((item) => item.path === '/app/equipe')?.group).toBe('gestao');
    expect(klmNavigation.find((item) => item.path === '/app/agenda')?.group).toBe('operacao');
    expect(klmNavigation.find((item) => item.path === '/app/financeiro')?.group).toBe('gestao');
    expect(klmNavigation.find((item) => item.path === '/app/contratos')?.group).toBe('clientes');
    expect(klmNavigation.find((item) => item.path === '/app/aprovacoes')?.group).toBe('operacao');
  });

  it('keeps the approved mobile order', () => {
    expect(mobileNavigation.map((item) => item.shortLabel)).toEqual(['Início', 'CRM', 'Chat', 'Suporte', 'Áreas']);
  });

  it('keeps the full menu exclusive to the workspace owner', () => {
    expect(navigationForWorkspaceMode(klmNavigation, 'general', true)).toEqual(klmNavigation);
    expect(navigationForWorkspaceMode(klmNavigation, 'general', false)).toEqual([]);
  });

  it('keeps client communication and contracted Case 360 in the legal panel', () => {
    const legal = navigationForWorkspaceMode(klmNavigation, 'legal', false);
    expect(legal.map((item) => item.label)).toEqual(expect.arrayContaining(['Atendimentos e conversas', 'Grupos', 'Caso 360', 'Processos', 'Peças e petições']));
    expect(legal.find((item) => item.label === 'Caso 360')?.path).toBe('/app/leads');
    expect(legal.map((item) => item.label)).not.toEqual(expect.arrayContaining(['Tráfego pago', 'Rastreamento']));
  });

  it('defaults owners to the complete panel and legal roles to the legal panel', () => {
    expect(defaultWorkspaceMode('workspace_owner')).toBe('general');
    expect(defaultWorkspaceMode('legal_manager')).toBe('legal');
    expect(defaultWorkspaceMode('workspace_member')).toBe('commercial');
  });

  it('uses the longest matching route before the app root', () => {
    expect(getNavigationItem('/app/conversas/ana')?.path).toBe('/app/conversas');
  });

  it('treats the legacy omnichannel route as the unified conversation inbox', () => {
    expect(getNavigationItem('/app/omnichannel')?.path).toBe('/app/conversas');
  });
});

describe('KLM brand and offline gates', () => {
  it('exposes the Meu Executivo product identity', () => {
    expect(BRAND.name).toBe('Meu Executivo');
    expect(BRAND.company).toBe('Meu Executivo');
    expect(BRAND_INITIALS).toBe('ME');
  });

  it('starts in local simulation', () => {
    expect(isLocalSimulation).toBe(true);
    expect(klmRuntime.mode).toBe('local_simulation');
  });

  it('fails closed to authenticated online mode when a production build omits the runtime mode', () => {
    expect(resolveKlmRuntimeMode(undefined, false)).toBe('online');
    expect(resolveKlmRuntimeMode(undefined, true)).toBe('local_simulation');
    expect(resolveKlmRuntimeMode('connected_local', false)).toBe('connected_local');
  });

  it('keeps external actions and AWS disabled', () => {
    expect(klmRuntime.externalActionsEnabled).toBe(false);
    expect(klmRuntime.awsEnabled).toBe(false);
    expect(() => assertExternalActionsDisabled()).not.toThrow();
  });

  it('restores the stored dark theme when the SPA reloads', () => {
    const root = document.createElement('div');
    expect(initializeKlmTheme(root, { getItem: () => 'dark' })).toBe(true);
    expect(root.classList.contains('dark')).toBe(true);
    expect(initializeKlmTheme(root, { getItem: () => 'light' })).toBe(false);
  });
});

describe('KLM primitives', () => {
  it('renders a stable metric card', () => {
    render(<KlmMetricCard label="CAC cliente" value="R$ 672" delta="-11%" trend="up" />);
    expect(screen.getByText('CAC cliente')).toBeInTheDocument();
    expect(screen.getByText('R$ 672')).toBeInTheDocument();
  });

  it('clamps progress above one hundred', () => {
    const { container } = render(<KlmProgress value={180} />);
    expect(container.querySelector('[style="width: 100%;"]')).toBeInTheDocument();
  });

  it('clamps progress below zero', () => {
    const { container } = render(<KlmProgress value={-15} />);
    expect(container.querySelector('[style="width: 0%;"]')).toBeInTheDocument();
  });

  it('supports all operational panel and pill tones', () => {
    render(<KlmPanel tone="black"><KlmPill tone="green">Local</KlmPill></KlmPanel>);
    expect(screen.getByText('Local')).toBeInTheDocument();
  });
});

describe('KLM timeline identity', () => {
  it('keeps events unique when they share the same displayed minute', () => {
    const rows = buildTimelineRows([
      { id: 'message-a', occurredAt: '2026-07-12T14:05:01Z', body: 'Primeiro evento', authorLabel: 'SDR' },
      { id: 'message-b', occurredAt: '2026-07-12T14:05:49Z', body: 'Segundo evento', authorLabel: 'Jurídico' },
    ]);

    expect(rows.map((row) => row.key)).toEqual(['message-a', 'message-b']);
    expect(new Set(rows.map((row) => row.key)).size).toBe(rows.length);
  });
});

describe('KLM page interactions', () => {
  it('keeps durable agenda reconciliation active even with WebSocket connected', () => {
    expect(meetingReconciliationMs('connected')).toBeGreaterThan(0);
    expect(meetingReconciliationMs('offline')).toBeLessThan(
      meetingReconciliationMs('connected'),
    );
    expect(FOLLOW_UP_RECONCILIATION_MS).toBeGreaterThan(0);
  });

  it('accepts only private KLM tickets as persisted audio sources', () => {
    const apiBase = 'http://127.0.0.1:8010';
    expect(trustedAudioSource(
      'http://127.0.0.1:8010/api/v1/local-storage/download?ticket=local-ticket',
      apiBase,
    )).toBe('http://127.0.0.1:8010/api/v1/local-storage/download?ticket=local-ticket');
    expect(trustedAudioSource('https://attacker.example/audio.mp3', apiBase)).toBeNull();
    expect(trustedAudioSource('http://127.0.0.1:8010/not-private/audio.mp3', apiBase)).toBeNull();
    expect(trustedAudioSource('data:audio/mpeg;base64,AAAA', apiBase)).toBeNull();
  });

  const mockAgenda = () => {
    mockEmptyCapabilityQueries();
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({
      can: () => true,
    } as unknown as ReturnType<typeof appStore.useAppStore>);
    const startsAt = new Date();
    startsAt.setHours(14, 0, 0, 0);
    const endsAt = new Date(startsAt.getTime() + 60 * 60 * 1000);
    const meetingsApi = vi.spyOn(klmApi, 'meetings').mockResolvedValue([{
      id: 'meeting-1',
      version: 1,
      matterId: 'matter-1',
      responsibleUserId: 'user-1',
      title: 'Reunião online: Ana Paula',
      startsAt: startsAt.toISOString(),
      endsAt: endsAt.toISOString(),
      status: 'scheduled',
      location: 'Google Meet',
      meetingUrl: null,
      notes: 'Revisar documentos médicos.',
      googleCalendarEventId: null,
      createdAt: new Date(startsAt.getTime() - 30 * 60 * 1000).toISOString(),
      updatedAt: new Date(startsAt.getTime() - 30 * 60 * 1000).toISOString(),
    }]);
    vi.spyOn(klmApi, 'followUps').mockResolvedValue([]);
    vi.spyOn(klmApi, 'funnel').mockResolvedValue({
      boardVersion: 1,
      stages: [],
      metrics: {},
      cards: [{
        id: 'matter-1', clientId: 'client-1', name: 'Ana Paula', subject: 'Medicamento de alto custo', object: 'Zolgensma', source: 'google_ads', owner: 'Kleist', probability: 80, initialFee: 5000, projectedRevenue: 12000, slaDueAt: null, documents: 3, documentScore: 90, stage: 'reuniao', microStatus: 'agendada',
      }],
    });
    vi.spyOn(klmApi, 'runtime').mockResolvedValue({
      mode: 'local_simulation', localOnly: true, localInfrastructure: true, externalActionsEnabled: false, awsEnabled: false, modelApisEnabled: false, onlineApproved: false, localBootstrapEnabled: true, providers: { googleWorkspace: 'offline' },
    });
    vi.spyOn(klmApi, 'dailyBriefing').mockResolvedValue({
      day: startsAt.toISOString().slice(0, 10), timezone: 'America/Sao_Paulo', total: 1, linkedCount: 1, orphanCount: 0, missingRoomCount: 1, summary: '1 reunião no dia.', priorities: ['Inserir o link do Meet ou Teams.'], generationMode: 'local_context', generatedBy: 'Hermes Agenda', generatedAt: new Date().toISOString(),
      items: [{ meetingId: 'meeting-1', startsAt: startsAt.toISOString(), title: 'Reunião online: Ana Paula', clientName: 'Ana Paula', matterId: 'matter-1', demandType: 'Medicamento de alto custo', noShowRisk: 'low', readiness: 'adicionar sala', meetingUrl: null, warnings: ['Inserir o link do Meet ou Teams.'] }],
    });
    vi.spyOn(klmApi, 'meetingBriefing').mockResolvedValue({
      meetingId: 'meeting-1', matterId: 'matter-1', linkedToMatter: true, clientName: 'Ana Paula', contact: null, demandType: 'Medicamento de alto custo', healthSubtheme: 'medicamento_alto_custo', healthObjectName: 'Zolgensma', healthInsurer: 'Plano local', medicalUrgencyHours: 24, leadScore: 88, documentCount: 5, readyDocumentCount: 3, noShowRisk: 'low', summary: 'Paciente precisa validar a negativa e os documentos médicos.', insights: ['Revisar a prova clínica.'], suggestedQuestions: ['Qual a urgência atual?'], warnings: ['Inserir o link do Meet ou Teams.'], nextAction: 'Confirmar documentos.', meetingUrl: null, roomProvider: 'Google Meet', generationMode: 'local_context', generatedBy: 'Hermes Agenda', generatedAt: new Date().toISOString(),
    });
    vi.spyOn(klmApi, 'unifiedAgenda').mockResolvedValue([]);
    vi.spyOn(adminService, 'fetchTeamMembers').mockResolvedValue([]);
    return { startsAt, endsAt, meetingsApi };
  };

  it('renders a real calendar with Google sync truthfully disconnected', async () => {
    mockAgenda();
    renderKlm(<AgendaPage />, ['/app/agenda']);
    expect(await screen.findByRole('heading', { name: 'Agenda' })).toBeInTheDocument();
    expect(screen.getAllByText('Google desconectado').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Mês' })).toHaveAttribute('aria-pressed', 'true');
    expect(await screen.findAllByText('Ana Paula')).not.toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Semana' }));
    expect(screen.getByRole('button', { name: 'Semana' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Dia' }));
    expect(screen.getByRole('button', { name: 'Dia' })).toHaveAttribute('aria-pressed', 'true');
  }, 10_000);

  it('creates a meeting from the agenda dialog', async () => {
    const { startsAt, endsAt } = mockAgenda();
    const create = vi.spyOn(klmApi, 'createMeeting').mockResolvedValue({
      id: 'meeting-2', version: 1, matterId: null, title: 'Reunião de retorno', startsAt: startsAt.toISOString(), endsAt: endsAt.toISOString(), status: 'scheduled', location: 'Google Meet', meetingUrl: null, notes: null,
    });
    renderKlm(<AgendaPage />, ['/app/agenda']);
    await screen.findByRole('heading', { name: 'Agenda' });
    fireEvent.click(screen.getByRole('button', { name: 'Nova reunião' }));
    fireEvent.change(screen.getByLabelText('Título'), { target: { value: 'Reunião de retorno' } });
    fireEvent.click(screen.getByRole('button', { name: 'Criar reunião' }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0][0]).toMatchObject({ title: 'Reunião de retorno', location: 'Google Meet' });
  });

  it('opens a new meeting already linked to the matter carried by Omnichannel', async () => {
    mockAgenda();
    renderKlm(<AgendaPage />, ['/app/agenda?matter=matter-1&action=new']);

    expect(await screen.findByRole('dialog', { name: 'Nova reunião' })).toBeInTheDocument();
    expect(screen.getByLabelText('Título')).toHaveValue('Reunião online: Ana Paula');
    expect(screen.getByRole('combobox', { name: 'Caso vinculado' })).toHaveTextContent('Ana Paula');
    expect(screen.getByText('Nova reunião vinculada ao atendimento')).toBeInTheDocument();
  });

  it('shows comparative indicators, the complete list and reacts to booking events in real time', async () => {
    const { meetingsApi } = mockAgenda();
    renderKlm(<AgendaPage />, ['/app/agenda']);
    await screen.findByRole('heading', { name: 'Agenda' });

    const metrics = screen.getByLabelText('Indicadores comparativos da agenda');
    expect(within(metrics).getByText('Novos agendamentos')).toBeInTheDocument();
    expect(within(metrics).getAllByText(/Anterior:/)).toHaveLength(6);
    expect(within(metrics).getByText('No-show em 30 dias')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Lista completa' })).toBeInTheDocument();
    expect(screen.getByText('Booking em tempo real')).toBeInTheDocument();

    act(() => {
      window.dispatchEvent(new CustomEvent('klm:realtime-status', { detail: { status: 'connected' } }));
      window.dispatchEvent(new CustomEvent('klm:realtime', { detail: { event: 'MEETING_CREATED', data: { meeting_id: 'meeting-1' } } }));
    });

    expect(await screen.findByText('Novo agendamento')).toBeInTheDocument();
    expect(screen.getByText('Ao vivo')).toBeInTheDocument();
    await waitFor(() => expect(meetingsApi.mock.calls.length).toBeGreaterThan(1));
  });

  it('downloads the Home executive report as a real local JSON file', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => false } as unknown as ReturnType<typeof appStore.useAppStore>);
    mockAnalyticsData();
    const { createObjectURL, click } = mockBrowserDownload();
    renderKlm(<HomePage />);
    fireEvent.click(screen.getByRole('button', { name: 'Gerar relatório' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('Relatório executivo');
    expect(screen.getByRole('dialog')).toHaveTextContent('Bloqueado');
    const download = screen.getByRole('button', { name: 'Baixar JSON' });
    await waitFor(() => expect(download).toBeEnabled());
    fireEvent.click(download);
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(click).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/Arquivo JSON gerado com os dados atuais/)).toBeInTheDocument();
  });

  it('persists and locally approves channel recommendations without changing campaigns', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => true } as unknown as ReturnType<typeof appStore.useAppStore>);
    mockAnalyticsData();
    const create = vi.spyOn(klmApi, 'createExternalIntent').mockResolvedValue({
      id: 'intent-search', workspaceId: 'workspace', requestedByUserId: 'user', matterId: null, actionType: 'marketing_channel_budget_increase', provider: 'google_ads', idempotencyKey: 'home:search', status: 'awaiting_approval', payload: {}, reason: 'Aguardando aprovação local.', createdAt: '2026-07-18T12:00:00Z', updatedAt: '2026-07-18T12:00:00Z',
    });
    const approve = vi.spyOn(klmApi, 'approveExternalIntentLocally').mockResolvedValue({
      id: 'intent-search', workspaceId: 'workspace', requestedByUserId: 'user', matterId: null, actionType: 'marketing_channel_budget_increase', provider: 'google_ads', idempotencyKey: 'home:search', status: 'approved_local', payload: {}, reason: 'Aprovado localmente.', createdAt: '2026-07-18T12:00:00Z', updatedAt: '2026-07-18T12:00:01Z',
    });
    renderKlm(<HomePage />);
    const recommendation = screen.getByRole('button', { name: 'Sugerir aumento em Search' });
    await waitFor(() => expect(recommendation).toBeEnabled());
    fireEvent.click(recommendation);
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0][0]).toMatchObject({ actionType: 'marketing_channel_budget_increase', provider: 'google_ads', payload: { externalActionsExecuted: 0 } });
    expect(approve).toHaveBeenCalledWith('intent-search');
    expect(await screen.findByText(/Nenhuma campanha externa foi alterada/)).toBeInTheDocument();
  });

  it('starts the funnel empty and never renders demonstration leads', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => true } as unknown as ReturnType<typeof appStore.useAppStore>);
    mockEmptyCapabilityQueries();
    mockAnalyticsData();
    mockBottleFunnelContext();
    renderKlm(<FunnelPage />, ['/app/funil']);
    expect(await screen.findByText('Nenhum lead neste funil ainda.')).toBeInTheDocument();
    expect(screen.queryByText('Pedro H.')).not.toBeInTheDocument();
    expect(screen.queryByText('Ana Paula R.')).not.toBeInTheDocument();
    expect(screen.getByText('CRM: conectado')).toBeInTheDocument();
  });

  it('persists a funnel stage change through the versioned backend command', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => true } as unknown as ReturnType<typeof appStore.useAppStore>);
    mockEmptyCapabilityQueries();
    const card = {
      id: 'matter-real', clientId: 'client-real', name: 'Cliente real', subject: 'Medicamento de alto custo', object: 'Terapia prescrita', source: 'google_ads', owner: 'SDR Saúde', probability: 72, initialFee: 4000, projectedRevenue: 18000, slaDueAt: null, documents: 2, documentScore: 40, stage: 'novo_lead', microStatus: 'lead_novo', cardOrder: 0,
    };
    mockAnalyticsData([card]);
    mockBottleFunnelContext();
    const move = vi.spyOn(klmApi, 'moveFunnel').mockResolvedValue({ matterId: card.id, columnId: 'triagem_sdr', microStatus: 'triagem_incompleta', cardOrder: 0, boardVersion: 4, operationId: 'operation-real', agentDispatched: true, deduplicated: false, automationStatus: 'completed', agentsTriggered: ['crm', 'orquestrador', 'sdr'], agentRunIds: ['run-crm', 'run-orquestrador', 'run-sdr'], approvalId: null, automationMessage: 'CRM e Orquestrador concluíram a leitura da nova etapa.' });
    installPointerCaptureForRadix();
    renderKlm(<FunnelPage />, ['/app/funil']);
    expect(await screen.findByTestId('funnel-card-matter-real')).toBeInTheDocument();
    const stageSelect = screen.getByRole('combobox', { name: 'Mover lead para etapa' });
    stageSelect.focus();
    fireEvent.keyDown(stageSelect, { key: 'ArrowDown', code: 'ArrowDown' });
    fireEvent.click(await screen.findByRole('option', { name: 'Triagem' }));
    await waitFor(() => expect(move).toHaveBeenCalledTimes(1));
    expect(move.mock.calls[0][0]).toBe(card.id);
    expect(move.mock.calls[0][1]).toBe('triagem_sdr');
    expect(move.mock.calls[0][2]).toMatchObject({ expectedBoardVersion: 3, sourceColumnId: 'novo_lead', position: 0 });
    expect(await screen.findByText('CRM e Orquestrador concluíram a leitura da nova etapa.')).toBeInTheDocument();
    expect(screen.getByText('Orquestração concluída')).toBeInTheDocument();
    expect(screen.getByText('Agentes: crm → orquestrador → sdr')).toBeInTheDocument();
  });

  it('creates a new area as a local draft', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({
      can: () => true,
      state: { session: { activeRole: 'workspace_owner' } },
    } as unknown as ReturnType<typeof appStore.useAppStore>);
    const createdArea = {
      id: 'area-bancario',
      areaKey: 'direito_bancario',
      name: 'Direito bancário',
      description: null,
      status: 'draft' as const,
      version: 'v0.1.0-local',
      readiness: 0,
      isActive: false,
      blueprint: {},
      validatedAt: null,
    };
    vi.spyOn(klmApi, 'areas').mockResolvedValue([]);
    vi.spyOn(klmApi, 'createArea').mockResolvedValue(createdArea);
    vi.spyOn(klmApi, 'updateArea').mockResolvedValue(createdArea);
    renderKlm(<AreasPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Criar área' }));
    fireEvent.change(screen.getByLabelText('Nome da área'), { target: { value: 'Direito bancário' } });
    fireEvent.click(screen.getByRole('button', { name: 'Criar estrutura' }));
    expect(await screen.findAllByText('Direito bancário')).toHaveLength(2);
    expect(screen.getAllByText('Congelada').length).toBeGreaterThan(0);
  });

  it('routes chat tools to their real KLM surface and never claims a fake simulation', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => true } as unknown as ReturnType<typeof appStore.useAppStore>);
    renderKlm(<><ToolsPage /><LocationProbe /></>, ['/app/ferramentas']);
    const toolbarTab = screen.getByRole('tab', { name: 'Barra do atendimento' });
    fireEvent.mouseDown(toolbarTab, { button: 0, ctrlKey: false });
    fireEvent.click(toolbarTab);
    await waitFor(() => expect(toolbarTab).toHaveAttribute('data-state', 'active'));
    fireEvent.click(screen.getByRole('button', { name: /Etiqueta/ }));
    expect(screen.getByTestId('location-probe')).toHaveTextContent('/app/leads?source=tools&tool=Etiqueta');
    expect(screen.queryByText('Concluir simulação')).not.toBeInTheDocument();
  });

  it('shows the native domain incorporation matrix without operational product names', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => true } as unknown as ReturnType<typeof appStore.useAppStore>);
    vi.spyOn(klmApi, 'tools').mockResolvedValue([]);
    vi.spyOn(klmApi, 'capabilities').mockResolvedValue({
      schemaVersion: '1.0',
      scope: 'pinned-community-feature-families',
      summary: {
        total: 2,
        available: 2,
        verified: 2,
        byDomain: { communications: 2, collaboration: 0, automation: 0, crm: 0 },
        byDisposition: { implemented: 1, approved_native: 1 },
        byExposure: { none: 0, bridge: 2, native: 0, microfrontend: 0, full_app: 0 },
        byIntegrationLevel: { blocked: 0, deep_link: 0, read_projection: 1, write_bridge: 1, native: 0, microfrontend: 0, unexposed: 0 },
        unclassified: 0,
        nativeExperience: 2,
        nativeExperienceVerified: 2,
        nativeRoute: 2,
        primaryDeepLink: 0,
        contractEligible: 2,
        contractVerified: 2,
        externalEffectVerified: 0,
        gatedConfiguration: 0,
      },
      verification: {
        state: 'verified',
        reason: 'Artefatos íntegros cobrem todos os IDs.',
        checks: 5,
        manifestPath: 'app/data/oss_native_verification.json',
        generatedAt: '2026-07-18T12:00:00Z',
        nativeIdsVerified: true,
        contractIdsVerified: true,
        verifiedNativeCapabilityIds: ['communications.api-webhooks-realtime', 'communications.conversation-composer'],
        verifiedContractCapabilityIds: ['communications.api-webhooks-realtime', 'communications.conversation-composer'],
        actionOccurrencesContractValidated: 511,
        uniqueCommandsContractValidated: 360,
        externalEffectsExecuted: 0,
        contractDigest: 'contract',
        nativeIdsDigest: 'native',
        nativeCount: 2,
        contractIdsDigest: 'contracts',
        contractCount: 2,
        verificationFileCount: 811,
        verificationFilesDigest: 'abcdef1234567890',
        proofRuns: ['backend', 'frontend-unit', 'frontend-e2e', 'local-runtime', 'upstream-locks'].map((id) => ({
          id,
          status: 'passed' as const,
          command: ['verify', id],
          generatedAt: '2026-07-18T12:00:00Z',
          outputSha256: `${id}-output`,
          artifactSha256: `${id}-artifact`,
        })),
      },
      capabilities: [
        {
          id: 'communications.api-webhooks-realtime',
          domain: 'communications',
          key: 'api-webhooks-realtime',
          label: 'API, webhooks e tempo real',
          category: 'plataforma',
          description: 'Transporta eventos idempotentes.',
          disposition: 'implemented',
          exposure: 'native',
          mode: 'local_canonical',
          relatedRoutes: ['/app/ferramentas'],
          verified: true,
          sourceDisposition: 'implemented',
          currentExposure: 'native',
          sourceExposure: 'bridge',
          surface: 'Infraestrutura',
          route: '/app/ferramentas',
          targetRoute: '/app/capacidades/communications/api-webhooks-realtime',
          sourceRoute: '/app/ferramentas',
          sourceTargetRoute: '/app/ferramentas',
          nativeExperience: true,
          nativeExperienceVerified: true,
          nativeRoute: '/app/capacidades/communications/api-webhooks-realtime',
          nativeMode: 'sidecar_round_trip',
          primaryExperience: 'native',
          primaryDeepLink: false,
          externalExecutionState: 'local_only',
          roundTrip: true,
          integrationLevel: 'native',
          contractEligible: true,
          contractVerified: true,
          externalEffectVerified: false,
          verificationLevel: 'contract_and_native_read',
          reason: 'Bridge e testes locais comprovados.',
          sourceReason: 'Bridge e testes locais comprovados.',
          upstreamEvidence: ['chatwoot/api'],
          klmEvidence: ['app/services/chatwoot_bridge_service.py'],
          testEvidence: ['tests/services/test_chatwoot_bridge_service.py'],
        },
        {
          id: 'communications.conversation-composer',
          domain: 'communications',
          key: 'conversation-composer',
          label: 'Conversas, timeline e composer',
          category: 'atendimento',
          description: 'Opera conversas no contexto do caso.',
          disposition: 'approved_native',
          exposure: 'native',
          mode: 'local_canonical',
          relatedRoutes: ['/app/conversas'],
          verified: true,
          sourceDisposition: 'approved_native',
          currentExposure: 'native',
          sourceExposure: 'bridge',
          surface: 'Conversas',
          route: '/app/conversas',
          targetRoute: '/app/capacidades/communications/conversation-composer',
          sourceRoute: '/app/conversas',
          sourceTargetRoute: '/app/conversas',
          nativeExperience: true,
          nativeExperienceVerified: true,
          nativeRoute: '/app/capacidades/communications/conversation-composer',
          nativeMode: 'sidecar_round_trip',
          primaryExperience: 'native',
          primaryDeepLink: false,
          externalExecutionState: 'local_only',
          roundTrip: true,
          integrationLevel: 'native',
          contractEligible: true,
          contractVerified: true,
          externalEffectVerified: false,
          verificationLevel: 'contract_and_native_read',
          reason: 'A timeline existe, mas ainda precisa operar todo o motor.',
          sourceReason: 'A timeline existe, mas ainda precisa operar todo o motor.',
          upstreamEvidence: ['chatwoot/conversation'],
          klmEvidence: ['frontend/src/klm/pages/ConversationsPage.tsx'],
          testEvidence: [],
        },
      ],
    } as unknown as Awaited<ReturnType<typeof klmApi.capabilities>>);

    renderKlm(<ToolsPage />, ['/app/ferramentas']);
    const coverageTab = screen.getByRole('tab', { name: 'Cobertura nativa' });
    fireEvent.mouseDown(coverageTab, { button: 0, ctrlKey: false });
    fireEvent.click(coverageTab);

    expect(await screen.findByText('API, webhooks e tempo real')).toBeInTheDocument();
    expect(screen.getByText('Conversas, timeline e composer')).toBeInTheDocument();
    expect(screen.getByText('2/2 disponíveis · 2 verificadas')).toBeInTheDocument();
    expect(screen.getByTestId('native-qa-verification')).toHaveTextContent('5 verificações');
    expect(document.body).not.toHaveTextContent(/Abrir motor/);
    expect(screen.getByText('Implementada')).toBeInTheDocument();
    expect(screen.getByText('Aprovada nativa')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Implementadas' }));
    expect(screen.getByText('API, webhooks e tempo real')).toBeInTheDocument();
    expect(screen.queryByText('Conversas, timeline e composer')).not.toBeInTheDocument();
  });

  it('opens only the canonical KLM capability route from a domain module', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({
      can: () => true,
      state: { session: { activeRole: 'workspace_owner' } },
    } as unknown as ReturnType<typeof appStore.useAppStore>);
    vi.spyOn(klmApi, 'capabilities').mockResolvedValue({
      schemaVersion: '1.0',
      scope: 'pinned-community-feature-families',
      summary: {
        total: 1,
        available: 1,
        verified: 1,
        byDomain: { communications: 1, collaboration: 0, automation: 0, crm: 0 },
        byDisposition: { approved_native: 1 },
        byExposure: { none: 0, bridge: 1, native: 0, microfrontend: 0, full_app: 0 },
        byIntegrationLevel: { blocked: 0, deep_link: 0, read_projection: 1, write_bridge: 0, native: 0, microfrontend: 0, unexposed: 0 },
        unclassified: 0,
        nativeExperience: 1,
        nativeExperienceVerified: 1,
        nativeRoute: 1,
        primaryDeepLink: 0,
        contractEligible: 1,
        contractVerified: 1,
        externalEffectVerified: 0,
        gatedConfiguration: 0,
      },
      verification: {
        state: 'verified',
        reason: 'Artefatos íntegros cobrem todos os IDs.',
        checks: 5,
        manifestPath: 'app/data/oss_native_verification.json',
        generatedAt: '2026-07-18T12:00:00Z',
        nativeIdsVerified: true,
        contractIdsVerified: true,
        verifiedNativeCapabilityIds: ['communications.conversation-composer'],
        verifiedContractCapabilityIds: ['communications.conversation-composer'],
        actionOccurrencesContractValidated: 511,
        uniqueCommandsContractValidated: 360,
        externalEffectsExecuted: 0,
        contractDigest: 'contract',
        nativeIdsDigest: 'native',
        nativeCount: 1,
        contractIdsDigest: 'contracts',
        contractCount: 1,
        verificationFileCount: 811,
        verificationFilesDigest: 'abcdef1234567890',
        proofRuns: ['backend', 'frontend-unit', 'frontend-e2e', 'local-runtime', 'upstream-locks'].map((id) => ({
          id,
          status: 'passed' as const,
          command: ['verify', id],
          generatedAt: '2026-07-18T12:00:00Z',
          outputSha256: `${id}-output`,
          artifactSha256: `${id}-artifact`,
        })),
      },
      capabilities: [{
        id: 'communications.conversation-composer',
        domain: 'communications',
        key: 'conversation-composer',
        label: 'Conversas e texto',
        category: 'atendimento',
        description: 'Opera mensagens no caso.',
        disposition: 'approved_native',
        exposure: 'native',
        mode: 'local_canonical',
        relatedRoutes: ['/app/conversas'],
        verified: true,
        sourceDisposition: 'approved_native',
        currentExposure: 'native',
        sourceExposure: 'bridge',
        surface: 'Conversas',
        route: '/app/conversas',
        targetRoute: '/app/capacidades/communications/conversation-composer',
        sourceRoute: '/app/conversas',
        sourceTargetRoute: '/app/conversas',
        nativeExperience: true,
        nativeExperienceVerified: true,
        nativeRoute: '/app/capacidades/communications/conversation-composer',
        nativeMode: 'sidecar_round_trip',
        primaryExperience: 'native',
        primaryDeepLink: false,
        externalExecutionState: 'local_only',
        roundTrip: true,
        integrationLevel: 'native',
        contractEligible: true,
        contractVerified: true,
        externalEffectVerified: false,
        verificationLevel: 'contract_and_native_read',
        reason: 'Ponte de texto comprovada.',
        sourceReason: 'Ponte de texto comprovada.',
        upstreamEvidence: ['upstream'],
        klmEvidence: ['klm'],
        testEvidence: ['test'],
      }],
    } as unknown as Awaited<ReturnType<typeof klmApi.capabilities>>);
    renderKlm(
      <>
        <NativeModuleBar
          domain="communications"
          surface="Conversa 360"
          returnTo="/app/conversas"
          matterId="11111111-1111-4111-8111-111111111111"
          threadId="22222222-2222-4222-8222-222222222222"
          capabilityIds={['conversation-composer']}
        />
        <LocationProbe />
      </>,
      ['/app/conversas'],
    );

    const resourceSelect = await screen.findByRole('combobox', { name: 'Recurso de Comunicações' });
    await waitFor(() => expect(resourceSelect).toHaveTextContent('Conversas e texto'));
    const openResourceButton = await screen.findByRole('button', { name: 'Abrir recurso' });
    await waitFor(() => expect(openResourceButton).toBeEnabled());
    fireEvent.click(openResourceButton);
    expect(screen.getByTestId('location-probe')).toHaveTextContent('/app/capacidades/communications/conversation-composer');
    expect(document.body).not.toHaveTextContent(/Abrir motor/);
  });

  it('separates native availability from verification in the module summary', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({
      can: () => true,
    } as unknown as ReturnType<typeof appStore.useAppStore>);
    vi.spyOn(klmApi, 'capabilities').mockResolvedValue({
      capabilities: [{
        id: 'communications.unverified-native',
        domain: 'communications',
        key: 'unverified-native',
        label: 'Recurso nativo pendente de verificação',
        disposition: 'approved_native',
        exposure: 'native',
        verified: false,
        nativeRoute: '/app/capacidades/communications/unverified-native',
        reason: 'A operação está disponível, mas a verificação permanece pendente.',
      }],
    } as unknown as Awaited<ReturnType<typeof klmApi.capabilities>>);

    renderKlm(
      <NativeModuleBar
        domain="communications"
        surface="Conversa 360"
        capabilityIds={['unverified-native']}
      />,
    );

    const moduleBar = await screen.findByTestId('native-domain-bar-communications');
    await waitFor(() => expect(within(moduleBar).getByText('1 disponíveis')).toBeInTheDocument());
    expect(within(moduleBar).getByText('1 disponíveis')).toBeInTheDocument();
    expect(within(moduleBar).getByText('0 verificadas')).toBeInTheDocument();
  });

  it('operates communications routing and inserts a real canned response in the composer', async () => {
    const threadId = '22222222-2222-4222-8222-222222222222';
    const thread = {
      id: threadId, workspaceId: 'workspace-1', matterId: 'matter-1', clientId: 'client-1', assignedHumanId: null,
      identityId: 'identity-1', assignedTeamId: null, primaryChannel: 'whatsapp' as const, status: 'open',
      subject: 'medicamento_alto_custo', objectName: 'Zolgensma', ownerAgentKey: 'sdr', queue: 'atendimento',
      priority: 90, isFavorite: false, preferredChannel: 'whatsapp', version: 3, score: 90, unreadCount: 1,
      lastMessagePreview: 'Preciso do medicamento.', lastMessageAt: '2026-07-18T12:00:00Z', slaDueAt: null, threadMetadata: {},
    };
    const context: Awaited<ReturnType<typeof klmApi.omnichannelContext>> = {
      thread,
      identity: null,
      channels: [{
        endpointId: 'endpoint-cloud', bindingId: 'binding-cloud', channel: 'whatsapp', provider: 'meta_cloud',
        connectionId: 'connection-cloud', connectionKey: 'whatsapp-cloud-principal', connectionName: 'WhatsApp principal',
        connectionStatus: 'configured_local', transportMode: 'cloud_api', externalAccountId: null, address: '+5561999999999',
        externalUserId: 'wa-user-1', consentState: 'granted', blockedAt: null, lastSeenAt: '2026-07-18T12:00:00Z',
        bindingCount: 1, capabilities: { outbound_messages: { supported: true } },
      }],
      bindings: [], actionIntents: [], scheduledMessages: [], calls: [], automationRuns: [], timeline: [],
      externalGateOpen: false, generatedAt: '2026-07-18T12:00:00Z',
    };
    vi.spyOn(klmApi, 'omnichannelContext').mockResolvedValue(context);
    vi.spyOn(klmApi, 'omnichannelTeams').mockResolvedValue([]);
    vi.spyOn(whatsappService, 'fetchMessageTemplates').mockResolvedValue([{
      id: 'template-docs', source: 'workspace', kind: 'message', category: 'triagem', label: '/docs',
      body: 'Envie os PDFs da negativa.', format: 'text', favorite: true,
    }]);
    const transfer = vi.spyOn(klmApi, 'transferOmnichannelThread').mockResolvedValue({
      thread: { ...thread, version: 4 },
      event: {
        id: 'routing-event-1', fromUserId: null, toUserId: null, fromTeamId: null, toTeamId: null,
        action: 'transfer', reason: 'Transferência manual pela Central de atendimento', source: 'human',
        expectedThreadVersion: 3, resultThreadVersion: 4, occurredAt: '2026-07-18T12:01:00Z',
      },
      deduplicated: false,
    });
    const useResponse = vi.fn();

    renderKlm(<NativeInboxRoutingPanel threadId={threadId} canEdit onUseResponse={useResponse} />);
    expect(await screen.findByText(/1 canal\(is\) · 1 histórico · versão 3/)).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Rota oficial deste atendimento' })).toHaveTextContent(/Cloud API/);
    fireEvent.click(screen.getByRole('button', { name: /docs/i }));
    expect(useResponse).toHaveBeenCalledWith('Envie os PDFs da negativa.');
    fireEvent.click(screen.getByRole('button', { name: 'Transferir' }));
    await waitFor(() => expect(transfer).toHaveBeenCalledWith(threadId, expect.objectContaining({
      expectedVersion: 3,
      toTeamId: null,
      toUserId: null,
      queue: 'atendimento',
    })));
    expect(await screen.findByText(/Atendimento transferido e auditado/)).toBeInTheDocument();
  });

  it('projects collaboration room context and refreshes only the selected case', async () => {
    const threadId = '22222222-2222-4222-8222-222222222222';
    vi.spyOn(klmApi, 'nativeCollaborationThreadContext').mockResolvedValue({
      linked: true,
      threadId,
      roomId: 'room-case',
      roomType: 'p',
      room: { id: 'room-case', name: 'caso-saude', displayName: 'Caso Saúde' },
      members: [{ id: 'u1', name: 'Advogada', username: 'advogada', status: 'online', role: 'member' }],
      messages: [{
        id: 'm1',
        body: 'Estratégia aprovada.',
        author: { id: 'u1', name: 'Advogada', username: 'advogada' },
        occurredAt: '2026-07-18T12:00:00Z',
        attachments: [{ name: 'parecer.pdf', mediaType: 'application/pdf' }],
        reactions: [{ emoji: ':white_check_mark:', count: 1 }],
        replyCount: 0,
      }],
      discussions: [{ id: 'd1', name: 'Tese liminar', messageCount: 3, createdAt: null, updatedAt: null }],
      sensitiveDataHidden: false,
    });
    const sync = vi.spyOn(klmApi, 'reconcileNativeCollaborationThread').mockResolvedValue({
      status: 'synced',
      threadId,
      roomId: 'room-case',
      messagesSeen: 2,
      messagesProjected: 1,
      externalNetworkExecuted: false,
    });

    renderKlm(<NativeCollaborationPanel threadId={threadId} canSync />);
    expect(await screen.findByText('Caso Saúde')).toBeInTheDocument();
    expect(screen.getByText('Estratégia aprovada.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: /Arquivos 1/ }));
    expect(screen.getByText('parecer.pdf')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: /Discussões 1/ }));
    expect(screen.getByText('Tese liminar')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Reconciliar sala' }));
    await waitFor(() => expect(sync).toHaveBeenCalledWith(threadId));
    expect(await screen.findByText(/2 mensagem\(ns\) verificadas/)).toBeInTheDocument();
  });

  it('exposes automation agents and reconciles them from the KLM source of truth', async () => {
    vi.spyOn(klmApi, 'nativeAgentContext').mockResolvedValue({
      linked: true,
      agents: [{ id: 'a1', name: 'KLM · Hermes', displayName: 'Hermes', description: 'Ponte executiva com o dono', isActive: true, state: 'ready', qualityScore: 100, queueSize: 0, transferToHuman: true }],
      workflows: [],
      analytics: { runs: 0, byStatus: {}, externalActionsExecuted: 0 },
      performance: [],
      people: { items: [], total: 0 },
      peopleStats: { totalPeople: 0, activeMatters: 0, newLeads7D: 0 },
      tools: [],
      groups: [],
      notifications: [],
      knowledge: { items: [], total: 0, queueSize: 0 },
      timeRange: '7d',
      sensitiveDataHidden: false,
      externalExecutionEnabled: false,
    });
    const sync = vi.spyOn(klmApi, 'reconcileNativeAgents').mockResolvedValue({
      status: 'synced', created: 0, updated: 1, adopted: 0, conflicts: 0, seen: 13,
    });

    renderKlm(<NativeAgentOperationsPanel view="agents" canManage />);
    expect(await screen.findByText('Agentes nativos')).toBeInTheDocument();
    expect(screen.getByText('Hermes')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Reconciliar' }));
    await waitFor(() => expect(sync).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/13 verificados/)).toBeInTheDocument();
  });

  it('mirrors one saved KLM flow and opens its exact automation editor', async () => {
    const flowId = '8df7bffe-776d-4d33-8810-1aa9eff9fe8a';
    vi.spyOn(klmApi, 'nativeFlowContext').mockResolvedValue({
      linked: true,
      flowId,
      revision: 4,
      workflowStatus: 'draft',
      nodeCount: 4,
      connectionCount: 3,
      digestMatches: true,
      graphMatches: true,
      staleLink: false,
      externalExecutionEnabled: false,
    });
    const sync = vi.spyOn(klmApi, 'reconcileNativeFlow').mockResolvedValue({
      status: 'synced',
      flowId,
      graphAction: 'unchanged',
      nodeCount: 4,
      connectionCount: 3,
      revision: 4,
      externalExecutionEnabled: false,
    });
    renderKlm(
      <>
        <NativeFlowRuntimePanel
          flowId={flowId}
          revision={4}
          isDirty={false}
          flowReady
          canManage
          canOpenEditor
        />
        <LocationProbe />
      </>,
      ['/app/agentes'],
    );

    expect(await screen.findByText('íntegro')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Validar revisão' }));
    await waitFor(() => expect(sync).toHaveBeenCalledWith(flowId));
    expect(await screen.findByText(/está íntegra no editor nativo/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Abrir editor' }));
    expect(screen.getByTestId('location-probe')).toHaveTextContent(`/app/flows?flow=${flowId}`);
  });

  it('renders CRM pipeline intelligence from the linked local projection', async () => {
    vi.spyOn(klmApi, 'nativeCRMFunnelContext').mockResolvedValue({
      projection: {
        linked: true,
        totalRecords: 24,
        availableRecords: 24,
        failedRecords: 0,
        staleRecords: 0,
        byEntityType: { contacts: 0, leads: 8, opportunities: 8, cases: 8, tasks: 0, financeEntries: 0 },
        updatedAt: '2026-07-17T15:00:00Z',
        externalExecutionEnabled: false,
      },
      counts: { accounts: 1, contacts: 8, leads: 8, opportunities: 8 },
      urgentCounts: { overdueTasks: 2, followupsToday: 1, hotLeads: 3 },
      pipeline: [{ id: 'PROPOSAL', label: 'Proposta', count: 3, value: 24000 }],
      revenue: { totalPipeline: 24000, weightedPipeline: 16800, conversionRate: 37.5, currency: 'BRL' },
      opportunityColumns: [{
        id: 'proposal', name: 'Proposta', order: 0, color: '#B58A2A', stageType: 'PROPOSAL', wipLimit: null, itemCount: 1,
        items: [
          { id: 'opp-1', entityType: 'opportunity', name: 'Ana Paula', stage: 'PROPOSAL', status: null, priority: null, amount: 24000, currency: 'BRL', probability: 70, daysInStage: 9, agingStatus: 'warning', dueDate: null, nextFollowUp: null, overdue: false, slaBreached: false, escalationCount: 0 },
          { id: 'opp-2', entityType: 'opportunity', name: 'Lead saudável', stage: 'PROPOSAL', status: null, priority: null, amount: 8000, currency: 'BRL', probability: 40, daysInStage: 1, agingStatus: 'green', dueDate: null, nextFollowUp: null, overdue: false, slaBreached: false, escalationCount: 0 },
        ],
      }],
      leadColumns: [],
      caseColumns: [],
      goals: [],
      agingConfig: [{ stage: 'PROPOSAL', expectedDays: 7, warningDays: 5 }],
      notificationsUnread: 2,
      sensitiveDataHidden: false,
    });

    renderKlm(<NativeCRMOperationsPanel view="funnel" />);
    expect(await screen.findByText('Inteligência comercial nativa')).toBeInTheDocument();
    expect(screen.getByText('Pipeline canônico')).toBeInTheDocument();
    expect(screen.getByText('R$ 16.800,00')).toBeInTheDocument();
    expect(screen.getByText('Ana Paula')).toBeInTheDocument();
    expect(screen.getByText('Atenção')).toBeInTheDocument();
    expect(screen.queryByText('Lead saudável')).not.toBeInTheDocument();
    expect(screen.getByText('24 disponíveis')).toBeInTheDocument();
  });

  it('keeps CRM matter identities masked when the KLM permission hides lead data', async () => {
    vi.spyOn(klmApi, 'nativeCRMMatterContext').mockResolvedValue({
      projection: {
        linked: true,
        totalRecords: 1,
        availableRecords: 1,
        failedRecords: 0,
        staleRecords: 0,
        byEntityType: { contacts: 0, leads: 1, opportunities: 0, cases: 0, tasks: 0, financeEntries: 0 },
        updatedAt: null,
        externalExecutionEnabled: false,
      },
      matterId: 'matter-1',
      records: [{ id: 'lead-1', entityType: 'lead', name: 'Lead restrito', stage: null, status: 'assigned', priority: null, amount: null, currency: 'BRL', probability: null, daysInStage: null, agingStatus: null, dueDate: null, nextFollowUp: null, overdue: false, slaBreached: false, escalationCount: 0, syncState: 'synced', lastSyncedAt: null }],
      sensitiveDataHidden: true,
    });

    renderKlm(<NativeCRMOperationsPanel view="matter" matterId="matter-1" />);
    expect(await screen.findByText('CRM completo deste caso')).toBeInTheDocument();
    expect(screen.queryByText('Lead restrito')).not.toBeInTheDocument();
    expect(screen.getByText('Registro restrito')).toBeInTheDocument();
    expect(screen.getByText('dados ocultos')).toBeInTheDocument();
  });

  it('creates a technical ticket in the canonical support center', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({
      can: () => true,
      state: {
        currentUser: { id: 'user-1', name: 'Kleist Monteiro', email: 'local@klmadvogados.com.br' },
        session: { activeWorkspace: { id: 'workspace-1' } },
      },
    } as unknown as ReturnType<typeof appStore.useAppStore>);
    vi.spyOn(klmApi, 'supportTickets').mockResolvedValue({ items: [], nextCursor: null });
    const createdTicket = {
      id: 'support-ticket-1',
      publicNumber: 'SAC-2026-0001',
      workspaceId: 'workspace-1',
      reporterUserId: 'user-1',
      assignedToUserId: null,
      ticketType: 'problem',
      priority: 'normal',
      severity: 'medium',
      queue: 'technical',
      status: 'new',
      subject: 'Corrigir alinhamento mobile',
      description: 'O botão deve permanecer dentro do painel em 393px.',
      route: '/app',
      technicalContext: {},
      errorFingerprint: null,
      linkedErrorLogId: null,
      linkedIncidentId: null,
      linkedReleaseId: null,
      supportAccessRequestId: null,
      duplicateOfTicketId: null,
      version: 1,
      firstResponseDueAt: '2026-07-12T13:00:00Z',
      resolutionDueAt: '2026-07-13T12:00:00Z',
      firstRespondedAt: null,
      resolvedAt: null,
      closedAt: null,
      reopenedAt: null,
      satisfactionScore: null,
      satisfactionComment: null,
      createdAt: '2026-07-12T12:00:00Z',
      updatedAt: '2026-07-12T12:00:00Z',
      reporter: null,
      assignee: null,
    } as const;
    const createSupportTicket = vi.spyOn(klmApi, 'createSupportTicket').mockResolvedValue(createdTicket);
    renderKlm(<TiAgentPage />);
    fireEvent.change(screen.getByLabelText('Título'), { target: { value: 'Corrigir alinhamento mobile' } });
    fireEvent.change(screen.getByLabelText('Resultado esperado e contexto'), { target: { value: 'O botão deve permanecer dentro do painel em 393px.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Criar solicitação' }));
    await waitFor(() => expect(createSupportTicket).toHaveBeenCalledTimes(1));
    expect(createSupportTicket.mock.calls[0][0]).toMatchObject({
      ticketType: 'problem',
      subject: 'Corrigir alinhamento mobile',
      description: 'O botão deve permanecer dentro do painel em 393px.',
      severity: 'medium',
      technicalContext: expect.objectContaining({
        page: '/app',
        action: 'support_center_submission',
      }),
    });
    expect(createSupportTicket.mock.calls[0][1]).toMatch(/^support-ticket-create:/);
    expect(await screen.findByText(/SAC-2026-0001 confirmado/)).toBeInTheDocument();
  });

  it('uses the canonical public conversation in the technical queue', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({
      can: () => true,
      state: {
        currentUser: { id: 'user-1', name: 'Kleist Monteiro', email: 'local@klmadvogados.com.br' },
        session: { activeWorkspace: { id: 'workspace-1' } },
      },
    } as unknown as ReturnType<typeof appStore.useAppStore>);
    const ticket = {
      id: 'support-ticket-1',
      publicNumber: 'SAC-2026-0002',
      workspaceId: 'workspace-1',
      reporterUserId: 'user-1',
      assignedToUserId: null,
      ticketType: 'problem' as const,
      priority: 'high' as const,
      severity: 'high' as const,
      queue: 'technical' as const,
      status: 'in_progress' as const,
      subject: 'Corrigir agenda mobile',
      description: 'O calendário ultrapassa o viewport.',
      route: '/app/agenda',
      technicalContext: {},
      errorFingerprint: null,
      linkedErrorLogId: null,
      linkedIncidentId: null,
      linkedReleaseId: null,
      supportAccessRequestId: null,
      duplicateOfTicketId: null,
      version: 2,
      firstResponseDueAt: '2026-07-18T11:00:00Z',
      resolutionDueAt: '2026-07-19T10:00:00Z',
      firstRespondedAt: '2026-07-18T10:10:00Z',
      resolvedAt: null,
      closedAt: null,
      reopenedAt: null,
      satisfactionScore: null,
      satisfactionComment: null,
      createdAt: '2026-07-18T10:00:00Z',
      updatedAt: '2026-07-18T10:00:00Z',
      reporter: null,
      assignee: null,
    };
    vi.spyOn(klmApi, 'supportTickets').mockResolvedValue({ items: [ticket], nextCursor: null });
    vi.spyOn(klmApi, 'supportTicket').mockResolvedValue({
      ...ticket,
      messages: [{
        id: 'message-1',
        authorUserId: null,
        authorLabel: 'Equipe de TI',
        visibility: 'public',
        body: 'Conseguimos reproduzir o problema.',
        createdAt: '2026-07-18T10:10:00Z',
      }],
      attachments: [],
      events: [],
      collaboratorIds: [],
    });
    const addSupportMessage = vi.spyOn(klmApi, 'addSupportMessage').mockResolvedValue({
      id: 'message-2',
      authorUserId: 'user-1',
      authorLabel: 'Kleist Monteiro',
      visibility: 'public',
      body: 'Breakpoint ajustado e validado localmente.',
      createdAt: '2026-07-18T11:00:00Z',
    });
    renderKlm(<TiAgentPage />);
    const queueTab = screen.getByRole('tab', { name: 'Fila técnica' });
    fireEvent.mouseDown(queueTab, { button: 0, ctrlKey: false });
    fireEvent.click(queueTab);
    fireEvent.click(await screen.findByRole('button', { name: /Corrigir agenda mobile/ }));
    expect(await screen.findByText('Conseguimos reproduzir o problema.')).toBeInTheDocument();
    expect(screen.queryByLabelText('Estado da solicitação técnica')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Mensagem para Suporte e TI'), { target: { value: 'Breakpoint ajustado e validado localmente.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enviar mensagem' }));
    await waitFor(() => expect(addSupportMessage).toHaveBeenCalledWith(
      'support-ticket-1',
      'Breakpoint ajustado e validado localmente.',
      expect.stringMatching(/^support-message-create:/),
    ));
  });

  it('shows only persisted audio gate facts and never restores fabricated session data', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({
      can: () => true,
      state: {
        currentUser: { name: 'Kleist Monteiro', email: 'local@klmadvogados.com.br' },
      },
    } as unknown as ReturnType<typeof appStore.useAppStore>);
    vi.spyOn(klmApi, 'threads').mockResolvedValue([{
      id: 'audio-thread-1', workspaceId: 'workspace-1', matterId: null, clientId: null, assignedHumanId: null,
      primaryChannel: 'audio', status: 'open', subject: 'Cirurgia urgente', objectName: null, ownerAgentKey: null,
      score: 82, unreadCount: 0, lastMessagePreview: null, lastMessageAt: null, slaDueAt: null,
      threadMetadata: { contactName: 'Cliente persistido' },
    }]);
    vi.spyOn(klmApi, 'funnel').mockResolvedValue({ boardVersion: 1, stages: [], metrics: {}, cards: [] });
    vi.spyOn(klmApi, 'messages').mockResolvedValue([
      {
        id: 'audio-message-1', threadId: 'audio-thread-1', matterId: null, channel: 'audio', direction: 'internal',
        authorType: 'human', authorLabel: 'Kleist Monteiro', body: 'Prefiro explicar por áudio.', deliveryStatus: 'stored_local',
        occurredAt: '2026-07-18T10:00:00Z',
        messageMetadata: { source: 'operator_audio_gate', requestCount: 1, voiceGatePassed: false, transcriptionEnabled: true, durationSeconds: 73 },
      },
      {
        id: 'audio-message-untrusted', threadId: 'audio-thread-1', matterId: null, channel: 'audio', direction: 'inbound',
        authorType: 'client', authorLabel: 'Cliente', body: 'Metadado não confiável.', deliveryStatus: 'stored_local',
        occurredAt: '2026-07-18T10:01:00Z',
        messageMetadata: { requestCount: 20, voiceGatePassed: true },
      },
    ]);
    vi.spyOn(klmApi, 'externalIntents').mockResolvedValue([]);
    renderKlm(<AudioPage />, ['/app/audio?thread=audio-thread-1']);

    expect(await screen.findByText('aguardando insistência: 1 pedido')).toBeInTheDocument();
    expect(screen.getByText(/Cliente persistido · Cirurgia urgente/)).toBeInTheDocument();
    expect(screen.getByText(/01:13 · responsável não atribuído/)).toBeInTheDocument();
    expect(screen.queryByText(/Eduardo B\./)).not.toBeInTheDocument();
    expect(screen.queryByText(/Tratamento ABA/)).not.toBeInTheDocument();

    expect(screen.queryByRole('button', { name: '2º pedido confirmado' })).not.toBeInTheDocument();
    expect(screen.getByText(/reiteração 2/)).toBeInTheDocument();
    expect(screen.getByText('aguardando insistência: 1 pedido')).toBeInTheDocument();
  });

  it('persists the selected internal room instead of inferring it from note text', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({
      can: () => true,
      state: {
        currentUser: { name: 'Kleist Monteiro', email: 'local@klmadvogados.com.br' },
      },
    } as unknown as ReturnType<typeof appStore.useAppStore>);
    mockEmptyCapabilityQueries();
    const thread = {
      id: 'internal-thread-1', workspaceId: 'workspace-1', matterId: 'matter-1', clientId: 'client-1', assignedHumanId: null,
      primaryChannel: 'whatsapp' as const, status: 'open', subject: 'cirurgia', objectName: 'Cirurgia urgente', ownerAgentKey: 'juridico_saude',
      score: 90, unreadCount: 0, lastMessagePreview: null, lastMessageAt: null, slaDueAt: null, threadMetadata: {},
    };
    vi.spyOn(klmApi, 'threads').mockResolvedValue([thread]);
    vi.spyOn(klmApi, 'agents').mockResolvedValue({
      policyVersion: 'local', runtime: 'local_simulation', agents: [],
      metrics: { modeled: 0, active: 0, paused: 0, qualityAverage: 0, runs: 0, waitingHuman: 0, externalActionsExecuted: 0, modelStrategy: 'api', modelApisEnabled: false, localModelsEnabled: false, executionMode: 'local_simulation', modelTokens: 0 },
    });
    vi.spyOn(klmApi, 'internalMessageFeed').mockResolvedValue([]);
    vi.spyOn(klmApi, 'omnichannelTeams').mockResolvedValue([]);
    vi.spyOn(klmApi, 'matterTasks').mockResolvedValue([]);
    vi.spyOn(klmApi, 'nativeCollaborationThreadContext').mockResolvedValue({ linked: false, threadId: thread.id, roomId: null, roomType: null, room: null, members: [], messages: [], discussions: [], sensitiveDataHidden: false });
    const sendInternalMessage = vi.spyOn(klmApi, 'sendInternalMessage').mockResolvedValue({
      id: 'internal-message-1', threadId: thread.id, matterId: thread.matterId, channel: 'internal', direction: 'internal', authorType: 'human', authorLabel: 'Kleist Monteiro', body: 'Revisar a documentação recebida.', deliveryStatus: 'received', occurredAt: '2026-07-18T12:00:00Z', messageMetadata: { roomKey: 'sdr' },
    });
    vi.spyOn(klmApi, 'reconcileNativeCollaborationThread').mockResolvedValue({ status: 'synced', threadId: thread.id, roomId: 'room-1', messagesSeen: 1, messagesProjected: 1, externalNetworkExecuted: false });
    vi.spyOn(klmApi, 'kpis').mockResolvedValue({
      topMetrics: [],
      families: [],
      channels: [],
      periodDays: 30,
      subtheme: null,
      generatedAt: '2026-07-18T12:00:00Z',
      source: 'local_database',
      teamPerformance: {
        audience: 'workspace_team',
        periodDays: 30,
        generatedAt: '2026-07-18T12:00:00Z',
        scoreFormula: '10 pontos por tarefa concluída e 2 por caso atribuído no período.',
        rows: [{
          id: 'workspace-1:user-1',
          name: 'Vendedora Exemplo',
          scopeName: 'Escritório Exemplo',
          role: 'member',
          primaryCount: 4,
          completedCount: 3,
          pendingCount: 1,
          qualityValue: 88,
          score: 38,
        }],
      },
    });

    renderKlm(<InternalPage />, ['/app/equipe']);
    expect(await screen.findByText('Gamificação operacional')).toBeInTheDocument();
    expect(await screen.findByText('Vendedora Exemplo')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: /triagem-sdr/ }));
    fireEvent.change(screen.getByLabelText('Caso da nota interna'), { target: { value: thread.id } });
    fireEvent.change(screen.getByPlaceholderText(/Mensagem interna/), { target: { value: 'Revisar a documentação recebida.' } });
    fireEvent.click(screen.getByTitle('Enviar mensagem interna'));

    await waitFor(() => expect(sendInternalMessage).toHaveBeenCalledWith(
      thread.id,
      'Revisar a documentação recebida.',
      'Kleist Monteiro',
      {
        roomKey: 'sdr',
        roomLabel: 'triagem-sdr',
        roomType: 'fixed',
        teamId: null,
        mentions: [],
        references: {
          threadId: thread.id,
          matterId: thread.matterId,
          clientId: thread.clientId,
          taskId: null,
        },
      },
    ));
  });

  it('requests filtered KPIs and exports a real local CSV file', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => true } as unknown as ReturnType<typeof appStore.useAppStore>);
    mockEmptyCapabilityQueries();
    const { kpisApi, advancedKpisApi } = mockAnalyticsData();
    mockBottleFunnelContext();
    const { createObjectURL, click } = mockBrowserDownload();
    installPointerCaptureForRadix();
    renderKlm(<KpisPage />);
    await waitFor(() => expect(kpisApi).toHaveBeenCalledWith(30, null));
    expect(await screen.findByText('Desempenho e gamificação da equipe')).toBeInTheDocument();
    expect(screen.getByText('Vendedora Exemplo')).toBeInTheDocument();
    expect(await screen.findByText('Relatórios avançados')).toBeInTheDocument();
    expect(screen.getByText('430 indicadores')).toBeInTheDocument();
    expect(screen.getByText('Mediana de primeira resposta')).toBeInTheDocument();
    expect(advancedKpisApi).toHaveBeenCalledWith(expect.objectContaining({
      periodDays: 30,
      limit: 600,
    }));
    expect(screen.getByRole('combobox', { name: 'Grupo de KPIs' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Financeiro/ }));
    expect(await screen.findByText('Gráfico de Financeiro')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Financeiro/ })).toHaveAttribute('aria-pressed', 'true');
    const subthemeSelect = screen.getByRole('combobox', { name: 'Subtema dos indicadores' });
    subthemeSelect.focus();
    fireEvent.keyDown(subthemeSelect, { key: 'ArrowDown', code: 'ArrowDown' });
    fireEvent.click(await screen.findByRole('option', { name: 'Medicamento alto custo' }));
    await waitFor(() => expect(kpisApi).toHaveBeenLastCalledWith(30, 'medicamento_alto_custo'));
    fireEvent.click(screen.getByRole('button', { name: 'Exportar local' }));
    const download = screen.getByRole('button', { name: 'Baixar CSV' });
    await waitFor(() => expect(download).toBeEnabled());
    fireEvent.click(download);
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(click).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/Arquivo CSV gerado com os dados atuais/)).toBeInTheDocument();
  });

  it('opens only a persisted approval and records the local decision through the API', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => true } as unknown as ReturnType<typeof appStore.useAppStore>);
    mockEmptyCapabilityQueries();
    const approval = {
      id: 'approval-real-1',
      cardId: '',
      clientName: 'Cliente persistido',
      type: 'petition_review',
      title: 'Revisar petição persistida',
      description: 'Documento retornado pela fila local.',
      priority: 'alta' as const,
      status: 'pending' as const,
      requestedAt: '2026-07-18T12:00:00Z',
      resolutionReason: null,
    };
    vi.spyOn(klmApi, 'approvals').mockResolvedValue([approval]);
    const resolveApproval = vi.spyOn(klmApi, 'resolveApproval').mockResolvedValue({
      ...approval,
      status: 'approved',
      resolutionReason: 'Aprovado pelo usuário no KLM Hub local.',
    });
    renderKlm(<OperationalModulePage />, ['/app/aprovacoes']);

    fireEvent.click(await screen.findByRole('button', { name: /Revisar petição persistida/ }));
    expect(screen.getByRole('dialog')).toHaveTextContent('Fila de aprovações');
    expect(screen.getByRole('dialog')).not.toHaveTextContent('Fixtures');
    expect(screen.queryByText(/Aprovar rascunho de petição/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Aprovar localmente' }));
    await waitFor(() => expect(resolveApproval).toHaveBeenCalledWith(
      'approval-real-1',
      'approved',
      'Aprovado pelo usuário no Meu Executivo local.',
    ));
    expect(await screen.findByText(/aprovada/)).toBeInTheDocument();
  });

  it('renders /app/equipe as human team management with individual KPIs', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => true, state: { currentUser: null, session: { activeRole: 'workspace_owner' } } } as unknown as ReturnType<typeof appStore.useAppStore>);
    vi.spyOn(adminService, 'fetchTeamMembers').mockResolvedValue([{
      id: 'user-1', membershipId: 'membership-1', name: 'Ana Gestora', initials: 'AG', email: 'ana@example.com', phone: null,
      type: 'human', role: 'workspace_owner', color: '#123456', isActive: true, lastLogin: '-', roles: ['workspace_owner'],
      accessPackage: null, directPermissions: [], deniedPermissions: [], rolePermissions: [], effectivePermissions: [],
      canViewSensitiveLeadData: true, canEditSensitiveLeadData: true, membershipStatus: 'active',
    }]);
    vi.spyOn(adminService, 'fetchRoleCatalog').mockResolvedValue([]);
    vi.spyOn(adminService, 'fetchWorkspaceAccessPackages').mockResolvedValue([]);
    vi.spyOn(klmApi, 'kpis').mockResolvedValue({
      topMetrics: [], families: [], channels: [], periodDays: 30, subtheme: null,
      generatedAt: '2026-07-26T12:00:00Z', source: 'local_database',
      teamPerformance: {
        audience: 'workspace_team', periodDays: 30, generatedAt: '2026-07-26T12:00:00Z', scoreFormula: 'persisted',
        rows: [{ id: 'user-1', name: 'Ana Gestora', scopeName: 'Workspace', role: 'manager', primaryCount: 8, completedCount: 6, pendingCount: 2, qualityValue: 92, score: 76 }],
      },
    });

    renderKlm(<OperationalModulePage />, ['/app/equipe']);

    expect(await screen.findByRole('heading', { name: 'Gestão da equipe' })).toBeInTheDocument();
    expect(await screen.findByText('Produtividade individual')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Abrir indicadores de Ana Gestora' })).toBeInTheDocument();
    expect(within(screen.getByTestId('team-individual-performance')).getByText('6')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Abrir indicadores de Ana Gestora' }));
    expect(await screen.findByRole('dialog', { name: 'Indicadores de Ana Gestora' })).toHaveTextContent('6');
    expect(screen.getByRole('button', { name: 'Horários e disponibilidade' })).toBeInTheDocument();
    expect(screen.queryByText('Comunicação interna')).not.toBeInTheDocument();
    expect(screen.queryByText(/agentes, decisões e tarefas/i)).not.toBeInTheDocument();
  });

  it('shows an honest empty state instead of contract rows and metrics from examples', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => true } as unknown as ReturnType<typeof appStore.useAppStore>);
    mockEmptyCapabilityQueries();
    vi.spyOn(klmApi, 'funnel').mockResolvedValue({ boardVersion: 1, stages: [], cards: [], metrics: {} });

    renderKlm(<OperationalModulePage />, ['/app/contratos']);

    expect(await screen.findByText('Nenhum registro persistido nesta seção')).toBeInTheDocument();
    expect(screen.getByText(/consulta foi concluída com sucesso/)).toBeInTheDocument();
    expect(screen.queryByText(/Ana Paula R/)).not.toBeInTheDocument();
    expect(screen.queryByText('Propostas')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Filtrar fila' })).toBeDisabled();
  });

  it('maps contract and petition rows only from persisted funnel fields', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => true } as unknown as ReturnType<typeof appStore.useAppStore>);
    mockEmptyCapabilityQueries();
    vi.spyOn(klmApi, 'funnel').mockResolvedValue({
      boardVersion: 1,
      stages: [],
      metrics: {},
      cards: [{
        id: 'matter-persisted-1',
        clientId: 'client-persisted-1',
        name: 'Cliente persistido',
        subject: 'medicamento_alto_custo',
        object: 'Medicamento registrado',
        source: 'google_ads',
        owner: 'Equipe jurídica',
        probability: 65,
        initialFee: 4000,
        projectedRevenue: 15000,
        slaDueAt: null,
        documents: 4,
        documentScore: 73,
        stage: 'contrato',
        microStatus: 'revisao_documental',
      }],
    });

    const contractView = renderKlm(<OperationalModulePage />, ['/app/contratos']);
    const contractRow = await screen.findByRole('button', { name: /Cliente persistido · Contrato/ });
    expect(contractRow).toHaveTextContent('Revisao Documental');
    expect(within(contractRow).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '73');
    contractView.unmount();

    renderKlm(<OperationalModulePage />, ['/app/peticoes']);
    const petitionRow = await screen.findByRole('button', { name: /Cliente persistido · Medicamento registrado/ });
    expect(petitionRow).toHaveTextContent('etapa Contrato');
    expect(within(petitionRow).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '73');
    expect(screen.queryByText(/Obrigação de fazer/)).not.toBeInTheDocument();
  });

  it('renders only persisted finance values and does not invent row progress', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => true } as unknown as ReturnType<typeof appStore.useAppStore>);
    mockEmptyCapabilityQueries();
    vi.spyOn(klmApi, 'nativeCRMFinanceContext').mockRejectedValue(new Error('Sidecar indisponível no teste.'));
    vi.spyOn(klmApi, 'financeOverview').mockResolvedValue({
      totalOpenReceivables: 5000,
      overdueAmount: 1250,
      scheduledAmount: 3750,
      paidAmount: 4200,
      pendingReminders: 2,
      entriesDueSoon: 1,
      upcomingEntries: [{
        id: 'finance-real-1',
        matterId: 'matter-real-1',
        title: 'Entrada contratual persistida',
        description: null,
        status: 'overdue',
        amount: 1250,
        currency: 'BRL',
        dueDate: '2026-07-17T12:00:00Z',
        paidAt: null,
        provider: 'asaas_local',
      }],
      highlights: ['Um lançamento vencido retornado pelo financeiro local.'],
    });

    renderKlm(<OperationalModulePage />, ['/app/financeiro']);

    expect(await screen.findByText('Entrada contratual persistida')).toBeInTheDocument();
    expect(screen.getByText('R$ 4.200,00')).toBeInTheDocument();
    expect(screen.getAllByText('R$ 1.250,00').length).toBeGreaterThan(0);
    const financeRow = screen.getByRole('button', { name: /Entrada contratual persistida/ });
    expect(within(financeRow).queryByRole('progressbar')).not.toBeInTheDocument();
    expect(screen.queryByText(/Cláudia S/)).not.toBeInTheDocument();
  });

  it('keeps activity metrics and rows absent when the persisted query fails', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => true } as unknown as ReturnType<typeof appStore.useAppStore>);
    mockEmptyCapabilityQueries();
    vi.spyOn(klmApi, 'nativeCRMFunnelContext').mockRejectedValue(new Error('Sidecar indisponível no teste.'));
    vi.spyOn(klmApi, 'activity').mockRejectedValue(new Error('Auditoria local indisponível.'));

    renderKlm(<OperationalModulePage />, ['/app/atividade']);

    expect(await screen.findByText('Não foi possível carregar atividade e trilha de auditoria')).toBeInTheDocument();
    expect(screen.getByText(/Nenhuma fila ou métrica substituta/)).toBeInTheDocument();
    expect(screen.queryByText('Eventos')).not.toBeInTheDocument();
    expect(screen.queryByText(/Dr. Kleistr assumiu atendimento/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Filtrar fila' })).toBeDisabled();
  });

  it('derives configuration metrics from runtime, areas and tools and preserves user management', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => true } as unknown as ReturnType<typeof appStore.useAppStore>);
    mockEmptyCapabilityQueries();
    vi.spyOn(klmApi, 'nativeCRMFunnelContext').mockRejectedValue(new Error('Sidecar indisponível no teste.'));
    vi.spyOn(klmApi, 'messagingCadenceSettings').mockRejectedValue(new Error('Cadência indisponível no teste.'));
    vi.spyOn(klmApi, 'legalPricingPolicy').mockRejectedValue(new Error('Política indisponível no teste.'));
    vi.spyOn(klmApi, 'runtime').mockResolvedValue({
      mode: 'connected_local',
      localOnly: true,
      localInfrastructure: true,
      externalActionsEnabled: false,
      awsEnabled: false,
      modelApisEnabled: false,
      onlineApproved: false,
      localBootstrapEnabled: true,
      providers: { bottlecrm: 'online', chatwoot: 'offline' },
    });
    vi.spyOn(klmApi, 'areas').mockResolvedValue([
      { id: 'area-1', areaKey: 'saude', name: 'Saúde', description: null, status: 'active', version: '1', readiness: 100, isActive: true, blueprint: {}, validatedAt: '2026-07-18T10:00:00Z' },
      { id: 'area-2', areaKey: 'bancario', name: 'Bancário', description: null, status: 'draft', version: '1', readiness: 0, isActive: false, blueprint: {}, validatedAt: null },
    ]);
    vi.spyOn(klmApi, 'tools').mockResolvedValue([
      { key: 'tool-1', name: 'Ferramenta 1', category: 'crm', state: 'ready', external: false, humanGate: true, capabilities: [] },
      { key: 'tool-2', name: 'Ferramenta 2', category: 'audit', state: 'ready', external: false, humanGate: false, capabilities: [] },
    ]);
    vi.spyOn(adminService, 'fetchTeamMembers').mockResolvedValue([]);
    vi.spyOn(adminService, 'fetchRoleCatalog').mockResolvedValue([]);
    vi.spyOn(adminService, 'fetchWorkspaceAccessPackages').mockResolvedValue([]);

    renderKlm(<><OperationalModulePage /><LocationProbe /></>, ['/app/configuracoes']);

    expect(await screen.findByText('Áreas ativas')).toBeInTheDocument();
    expect(screen.getByText('2 registrada(s)')).toBeInTheDocument();
    expect(screen.getByText('1 com gate humano')).toBeInTheDocument();
    expect(screen.getByText('2 registrado(s)')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Usuários e permissões/ }));
    expect(await screen.findByText('Nenhum usuário retornado')).toBeInTheDocument();
    expect(screen.getByText(/Use a gestão abaixo para criar o primeiro acesso/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Voltar às configurações/ }));
    fireEvent.click(screen.getByRole('button', { name: /Identidade e marca/ }));
    expect(screen.getByTestId('location-probe')).toHaveTextContent('/app/configuracoes/avancadas?tab=personalizacao');
  });
});
