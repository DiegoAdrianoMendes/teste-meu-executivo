/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactElement } from 'react';

import FunnelPage from '@/klm/pages/FunnelPage';
import { klmApi } from '@/klm/api';
import * as appStore from '@/store/app-context';

const renderKlm = (ui: ReactElement, initialEntries = ['/app/funil']) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
};

function mockBottleFunnelContext() {
  vi.spyOn(klmApi, 'nativeCRMFunnelContext').mockResolvedValue({
    projection: { linked: false, totalRecords: 0, availableRecords: 0, failedRecords: 0, staleRecords: 0, byEntityType: { contacts: 0, leads: 0, opportunities: 0, cases: 0, tasks: 0, financeEntries: 0 }, updatedAt: null, externalExecutionEnabled: false },
    counts: { accounts: 0, contacts: 0, leads: 0, opportunities: 0 }, urgentCounts: { overdueTasks: 0, followupsToday: 0, hotLeads: 0 }, pipeline: [], revenue: { totalPipeline: 0, weightedPipeline: 0, conversionRate: 0, currency: 'BRL' }, opportunityColumns: [], leadColumns: [], caseColumns: [], goals: [], agingConfig: [], notificationsUnread: 0, sensitiveDataHidden: false,
  });
}

function installPointerCaptureForRadix() {
  Object.defineProperty(HTMLElement.prototype, 'hasPointerCapture', { configurable: true, value: () => false });
  Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', { configurable: true, value: () => undefined });
  Object.defineProperty(HTMLElement.prototype, 'releasePointerCapture', { configurable: true, value: () => undefined });
}

const stages = [
  { id: 'novo_lead', label: 'Novo lead' },
  { id: 'triagem_sdr', label: 'Triagem' },
  { id: 'ganho', label: 'Fechado' },
  { id: 'perdido', label: 'Perdido' },
];

function funnelFixture(card: Record<string, unknown>) {
  return {
    boardVersion: 1,
    stages: stages.map((stage) => ({ ...stage, count: card.stage === stage.id ? 1 : 0 })),
    metrics: {},
    cards: [card],
  };
}

function mockFunnelQueries(card: Record<string, unknown>) {
  vi.spyOn(klmApi, 'dashboard').mockResolvedValue({
    metrics: { leads: 1, qualified: 0, activeThreads: 0, pendingApprovals: 0, dueFollowUps: 0, externalActions: 0 },
    funnel: [], decisions: [],
    runtime: { mode: 'local_simulation', localOnly: true, localInfrastructure: true, externalActionsEnabled: false, awsEnabled: false, modelApisEnabled: false, onlineApproved: false, localBootstrapEnabled: true, providers: {} },
  });
  vi.spyOn(klmApi, 'kpis').mockResolvedValue({ topMetrics: [], families: [], channels: [], periodDays: null, subtheme: null, generatedAt: '2026-07-23T12:00:00Z', source: 'local_database' });
  vi.spyOn(klmApi, 'funnel').mockResolvedValue(funnelFixture(card) as Awaited<ReturnType<typeof klmApi.funnel>>);
  vi.spyOn(klmApi, 'financeOverview').mockResolvedValue({ totalOpenReceivables: 0, overdueAmount: 0, scheduledAmount: 0, paidAmount: 0, pendingReminders: 0, entriesDueSoon: 0, upcomingEntries: [], highlights: [] });
  vi.spyOn(klmApi, 'financeEntries').mockResolvedValue([]);
  vi.spyOn(klmApi, 'listFinanceCharges').mockResolvedValue([]);
  vi.spyOn(klmApi, 'feeArrangements').mockResolvedValue([]);
  vi.spyOn(klmApi, 'approvals').mockResolvedValue([]);
  vi.spyOn(klmApi, 'quality').mockResolvedValue({ score: 90, metrics: {}, dimensions: [], reviewQueue: [], generatedAt: '2026-07-23T12:00:00Z' });
  vi.spyOn(klmApi, 'tracking').mockResolvedValue({
    leads: 1,
    qualifiedLeads: 0,
    meetings: 0,
    clients: 0,
    adSpend: 0,
    attributableRevenue: 0,
    ignoredValueEvents: 0,
    conversionRate: null,
    cac: null,
    roas: null,
    periodDays: null,
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
  });
}

function matterCard(overrides: Record<string, unknown> = {}) {
  return {
    id: 'matter-win-1',
    clientId: 'client-win-1',
    name: 'Caso Rosimeire',
    subject: 'Plano de saúde',
    object: 'Cumprimento de sentença',
    source: 'google_ads',
    owner: 'Advogado A',
    probability: 80,
    initialFee: 5000,
    projectedRevenue: 5000,
    slaDueAt: null,
    documents: 1,
    documentScore: 60,
    stage: 'novo_lead',
    microStatus: 'lead_novo',
    cardOrder: 0,
    ...overrides,
  };
}

beforeEach(() => {
  window.localStorage.clear();
  vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => true } as unknown as ReturnType<typeof appStore.useAppStore>);
  installPointerCaptureForRadix();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('Celebração de caso ganho no funil', () => {
  it('dispara a celebração ao mover um card para a etapa de ganho', async () => {
    const card = matterCard();
    mockFunnelQueries(card);
    mockBottleFunnelContext();
    vi.spyOn(klmApi, 'moveFunnel').mockResolvedValue({ matterId: card.id, columnId: 'ganho', microStatus: 'fechado', cardOrder: 0, boardVersion: 2, operationId: 'op-1', agentDispatched: false, deduplicated: false });

    renderKlm(<FunnelPage />);
    expect(await screen.findByTestId(`funnel-card-${card.id}`)).toBeInTheDocument();
    expect(screen.queryByTestId('win-celebration')).not.toBeInTheDocument();

    const stageSelect = screen.getByRole('combobox', { name: 'Mover lead para etapa' });
    stageSelect.focus();
    fireEvent.keyDown(stageSelect, { key: 'ArrowDown', code: 'ArrowDown' });
    fireEvent.click(await screen.findByRole('option', { name: 'Fechado' }));

    await waitFor(() => expect(screen.getByTestId('win-celebration')).toBeInTheDocument());
    expect(within(screen.getByTestId('win-celebration')).getByText('Caso Rosimeire')).toBeInTheDocument();
    expect(window.localStorage.getItem('klm-funnel-won-celebrated')).toContain(card.id);
  });

  it('não repete a celebração para um caso já celebrado anteriormente', async () => {
    const card = matterCard({ stage: 'triagem_sdr' });
    window.localStorage.setItem('klm-funnel-won-celebrated', JSON.stringify([card.id]));
    mockFunnelQueries(card);
    mockBottleFunnelContext();
    vi.spyOn(klmApi, 'moveFunnel').mockResolvedValue({ matterId: card.id, columnId: 'ganho', microStatus: 'fechado', cardOrder: 0, boardVersion: 2, operationId: 'op-2', agentDispatched: false, deduplicated: false });

    renderKlm(<FunnelPage />);
    expect(await screen.findByTestId(`funnel-card-${card.id}`)).toBeInTheDocument();

    const stageSelect = screen.getByRole('combobox', { name: 'Mover lead para etapa' });
    stageSelect.focus();
    fireEvent.keyDown(stageSelect, { key: 'ArrowDown', code: 'ArrowDown' });
    fireEvent.click(await screen.findByRole('option', { name: 'Fechado' }));

    await waitFor(() => expect(vi.mocked(klmApi.moveFunnel)).toHaveBeenCalledTimes(1));
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByTestId('win-celebration')).not.toBeInTheDocument();
  });
});
