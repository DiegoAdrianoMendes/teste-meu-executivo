/** @vitest-environment jsdom */
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  klmApi,
  type KlmChannelConnection,
  type KlmDashboard,
  type KlmFinanceOverview,
  type KlmFunnel,
  type KlmKpis,
  type KlmMeeting,
  type KlmQuality,
  type KlmTrackingSummary,
} from '@/klm/api';
import HomePage from '@/klm/pages/HomePage';

vi.mock('@/klm/components/NativeModuleOverview', () => ({ NativeModuleOverview: () => null }));
vi.mock('@/components/common/CustomPeriodButton', () => ({ CustomPeriodButton: () => null }));

const appState = vi.hoisted(() => ({
  current: { activeRole: null as string | null, workspaceId: 'workspace-1', userName: 'Ana Souza' },
}));

vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({
    can: () => true,
    state: {
      session: { activeRole: appState.current.activeRole },
      currentUser: { role: null, name: appState.current.userName },
      activeWorkspace: { id: appState.current.workspaceId },
    },
  }),
}));

const dashboard: KlmDashboard = {
  metrics: { leads: 4, qualified: 2, activeThreads: 3, pendingApprovals: 1, dueFollowUps: 2, externalActions: 0 },
  funnel: [],
  decisions: [
    { type: 'approval', count: 1, priority: 'high' },
    { type: 'follow_up', count: 2, priority: 'high' },
  ],
  runtime: {
    mode: 'local_simulation',
    localOnly: true,
    localInfrastructure: true,
    externalActionsEnabled: false,
    awsEnabled: false,
    modelApisEnabled: false,
    onlineApproved: false,
    localBootstrapEnabled: true,
    providers: {},
  },
};

const kpis: KlmKpis = {
  topMetrics: [
    { key: 'contracts', label: 'Contratos', value: 3, unit: 'count' },
    { key: 'pipeline', label: 'Pipeline projetado', value: 12000, unit: 'BRL' },
  ],
  families: [],
  channels: [],
  periodDays: 1,
  subtheme: null,
  generatedAt: '2026-07-23T12:00:00Z',
  source: 'local_database',
  trend: {
    leads: Array.from({ length: 14 }, (_, index) => ({ date: `2026-07-${String(index + 1).padStart(2, '0')}`, value: index })),
    revenue: Array.from({ length: 14 }, (_, index) => ({ date: `2026-07-${String(index + 1).padStart(2, '0')}`, value: index * 100 })),
  },
};

const funnel: KlmFunnel = { boardVersion: 1, stages: [], cards: [], metrics: {} };

function meetingToday(): KlmMeeting {
  const startsAt = new Date();
  startsAt.setHours(14, 30, 0, 0);
  const endsAt = new Date(startsAt.getTime() + 60 * 60 * 1000);
  return {
    id: 'meeting-1',
    version: 1,
    workspaceId: 'workspace-1',
    matterId: null,
    responsibleUserId: null,
    title: 'Reunião de alinhamento',
    startsAt: startsAt.toISOString(),
    endsAt: endsAt.toISOString(),
    status: 'scheduled',
    location: null,
    meetingUrl: 'https://meet.google.com/teste-local',
    notes: null,
    googleCalendarEventId: null,
    createdAt: startsAt.toISOString(),
    updatedAt: startsAt.toISOString(),
  };
}

const finance: KlmFinanceOverview = {
  totalOpenReceivables: 0,
  overdueAmount: 0,
  scheduledAmount: 0,
  paidAmount: 0,
  pendingReminders: 0,
  entriesDueSoon: 0,
  upcomingEntries: [],
  highlights: [],
};

const quality: KlmQuality = { score: 80, metrics: {}, dimensions: [], reviewQueue: [], generatedAt: '2026-07-23T12:00:00Z' };

const tracking: KlmTrackingSummary = {
  leads: 4,
  qualifiedLeads: 2,
  meetings: 1,
  clients: 0,
  adSpend: 100,
  attributableRevenue: 0,
  ignoredValueEvents: 0,
  conversionRate: null,
  cac: null,
  roas: null,
  periodDays: 1,
  periodStart: null,
  totalEvents: 0,
  identifiedJourneys: 0,
  byChannel: [],
  eventCoverage: [],
  timeTrend: [],
  journeys: [],
  remarketing: [],
  attributionCoverage: {
    conversations: 0,
    trackedConversations: 0,
    untrackedConversations: 0,
    trackingRate: null,
    conversionEvents: 0,
    attributedConversions: 0,
    unattributedConversions: 0,
    attributedConversionRate: null,
    feedbackEligibleConversions: 0,
    feedbackBlockedNoEvidence: 0,
    fullHierarchyTouches: 0,
    partialHierarchyTouches: 0,
  },
  attributionRows: [],
  funnelSteps: [],
  insights: [],
  serviceQuality: {
    findings: 0,
    activeFindings: 0,
    criticalFindings: 0,
    unansweredLeads: 0,
    deliveryMismatches: 0,
    duplicateOutbound: 0,
    resolvedFindings: 0,
    auditedThreads: 0,
    qualityScore: 100,
  },
  conversionFeedback: {
    prepared: 0,
    blockedExternal: 0,
    awaitingApproval: 0,
    approvedLocal: 0,
    rejected: 0,
    duplicatePrevented: 0,
    payloadReady: 0,
    configurationBlocked: 0,
    preparedProofs: 0,
    providerReceipts: 0,
    failedReceipts: 0,
    providers: {},
  },
  feedbackEvents: [],
  dailyChannels: [],
  containsLocalFixtureData: false,
  generatedAt: '2026-07-23T12:00:00Z',
};

function connection(status: string): KlmChannelConnection {
  return {
    id: 'conn-1',
    workspaceId: 'workspace-1',
    channel: 'whatsapp',
    provider: 'meta',
    connectionKey: 'default',
    displayName: 'WhatsApp',
    externalAccountId: null,
    transportMode: 'cloud_api',
    status,
    isDefault: true,
    capabilities: {},
    limits: {},
    routingPolicy: {},
    health: {},
    configuration: {},
    credentialReference: null,
    webhookState: 'pending',
    version: 1,
    verifiedAt: null,
    connectedAt: null,
    lastSyncedAt: null,
    createdAt: '2026-07-23T12:00:00Z',
    updatedAt: '2026-07-23T12:00:00Z',
  };
}

function renderHome() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/app']}>
        <HomePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function flushEffects(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  window.localStorage.clear();
  appState.current = { activeRole: null, workspaceId: 'workspace-1', userName: 'Ana Souza' };
  vi.spyOn(klmApi, 'dashboard').mockResolvedValue(dashboard);
  vi.spyOn(klmApi, 'kpis').mockResolvedValue(kpis);
  vi.spyOn(klmApi, 'funnel').mockResolvedValue(funnel);
  vi.spyOn(klmApi, 'financeOverview').mockResolvedValue(finance);
  vi.spyOn(klmApi, 'quality').mockResolvedValue(quality);
  vi.spyOn(klmApi, 'tracking').mockResolvedValue(tracking);
  vi.spyOn(klmApi, 'meetings').mockResolvedValue([meetingToday()]);
  vi.spyOn(klmApi, 'omnichannelConnections').mockResolvedValue([connection('offline')]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('HomePage', () => {
  it('renders the contextual greeting with the user name and daily summary', async () => {
    renderHome();
    await flushEffects();
    expect(await screen.findByText(/Ana/)).toBeInTheDocument();
    expect((await screen.findAllByText(/follow-up.*vencido/i)).length).toBeGreaterThan(0);
  });

  it('shows the real agenda overview for today and the current week', async () => {
    renderHome();
    await flushEffects();
    expect(await screen.findByText('Agenda executiva')).toBeInTheDocument();
    expect(await screen.findByText('Reunião de alinhamento')).toBeInTheDocument();
    expect(screen.getByText('14:30')).toBeInTheDocument();
    expect(screen.getByText(/Sem caso vinculado/)).toBeInTheDocument();
  });

  it('shows the "Comece por aqui" aha card when there is no active WhatsApp connection', async () => {
    renderHome();
    await flushEffects();
    expect(await screen.findByTestId('aha-onboarding-card')).toBeInTheDocument();
    expect(screen.getByText('Comece por aqui')).toBeInTheDocument();
  });

  it('hides the aha card when the workspace has an active WhatsApp connection', async () => {
    vi.spyOn(klmApi, 'omnichannelConnections').mockResolvedValue([connection('connected')]);
    renderHome();
    await flushEffects();
    await screen.findByText('Leads novos');
    expect(screen.queryByTestId('aha-onboarding-card')).not.toBeInTheDocument();
  });

  it('dismisses the aha card and persists the dismissal in localStorage', async () => {
    renderHome();
    await flushEffects();
    expect(await screen.findByTestId('aha-onboarding-card')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Dispensar'));
    await flushEffects();
    expect(screen.queryByTestId('aha-onboarding-card')).not.toBeInTheDocument();
    expect(window.localStorage.getItem('klm-aha-onboarding:workspace-1')).toContain('"dismissed":true');
  });

  it('reorders home blocks by the active role package (office_owner sees decisions first)', async () => {
    appState.current.activeRole = 'office_owner';
    renderHome();
    await flushEffects();
    await screen.findByText('Funil de conversão');
    const titles = screen.getAllByRole('heading', { level: 2 }).map((node) => node.textContent);
    const decisionsIndex = titles.findIndex((title) => title?.includes('Fila de decisões'));
    const pipelineIndex = titles.findIndex((title) => title?.includes('Funil de conversão'));
    expect(decisionsIndex).toBeGreaterThanOrEqual(0);
    expect(decisionsIndex).toBeLessThan(pipelineIndex);
  });

  it('generates a branded executive report HTML and triggers window.print() without dropping CSV/JSON', async () => {
    const fakeWindow = {
      document: { open: () => {}, write: () => {}, close: () => {} },
      focus: () => {},
      print: () => {},
    } as unknown as Window;
    const printSpy = vi.spyOn(fakeWindow, 'print');
    const writeSpy = vi.spyOn(fakeWindow.document, 'write');
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(fakeWindow);

    renderHome();
    await flushEffects();
    fireEvent.click(screen.getByText('Gerar relatório'));
    await flushEffects();

    expect(await screen.findByRole('button', { name: /Baixar JSON/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Baixar CSV/i })).toBeInTheDocument();

    const executiveButton = screen.getByRole('button', { name: /Relatório executivo \(PDF\)/i });
    fireEvent.click(executiveButton);
    await flushEffects();

    expect(openSpy).toHaveBeenCalledWith('', '_blank');
    expect(writeSpy).toHaveBeenCalledTimes(1);
    const writtenHtml = writeSpy.mock.calls[0][0] as string;
    expect(writtenHtml).toContain('Contratos');
    expect(writtenHtml).toContain('Gerado pelo');
    expect(printSpy).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/Salvar como PDF/i)).toBeInTheDocument();

    openSpy.mockRestore();
  });

  it('keeps the default block order for an unknown role', async () => {
    appState.current.activeRole = 'some_unmapped_role';
    renderHome();
    await flushEffects();
    await screen.findByText('Funil de conversão');
    const titles = screen.getAllByRole('heading', { level: 2 }).map((node) => node.textContent);
    const pipelineIndex = titles.findIndex((title) => title?.includes('Funil de conversão'));
    const decisionsIndex = titles.findIndex((title) => title?.includes('Fila de decisões'));
    expect(pipelineIndex).toBeGreaterThanOrEqual(0);
    expect(pipelineIndex).toBeLessThan(decisionsIndex);
  });
});
