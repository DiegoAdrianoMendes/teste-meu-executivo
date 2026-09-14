/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { TooltipProvider } from '@/components/ui/tooltip';
import TrackingPage from '@/klm/pages/TrackingPage';
import { klmApi, type KlmTrackingSummary } from '@/klm/api';

vi.mock('@/store/app-context', () => ({
  useOptionalAppStore: () => ({ can: () => true }),
  useAppStore: () => ({ can: () => true }),
}));

vi.mock('@/services/admin', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/admin')>();
  return { ...actual, fetchMyAccessPackage: async () => null };
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const baseSummary: KlmTrackingSummary = {
  periodDays: 30,
  periodStart: '2026-06-23T12:00:00Z',
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
    businessDuplicatesSuppressed: 0,
    fullHierarchyTouches: 0,
    partialHierarchyTouches: 0,
  },
  attributionRows: [],
  funnelSteps: [],
  insights: [],
  serviceQuality: {
    findings: 0, activeFindings: 0, criticalFindings: 0, unansweredLeads: 0, deliveryMismatches: 0,
    duplicateOutbound: 0, resolvedFindings: 0, auditedThreads: 0, qualityScore: 100,
  },
  conversionFeedback: {
    prepared: 0, blockedExternal: 0, awaitingApproval: 0, approvedLocal: 0, rejected: 0, duplicatePrevented: 0,
    payloadReady: 0, configurationBlocked: 0, preparedProofs: 0, providerReceipts: 0, failedReceipts: 0, providers: {},
  },
  feedbackEvents: [],
  dailyChannels: [],
  containsLocalFixtureData: false,
  generatedAt: '2026-07-23T12:00:00Z',
};

function renderPage(entry = '/app/rastreamento') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <MemoryRouter initialEntries={[entry]}>
          <Routes><Route path="/app/rastreamento" element={<TrackingPage />} /></Routes>
        </MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe('TrackingPage local fixture banner', () => {
  it('shows no demonstration banner when the summary has only real data', async () => {
    vi.spyOn(klmApi, 'tracking').mockResolvedValue({ ...baseSummary, containsLocalFixtureData: false });
    renderPage();

    expect(await screen.findByRole('heading', { name: 'Rastreamento e aquisição' })).toBeInTheDocument();
    expect(screen.queryByText(/Inclui dados de demonstração/)).not.toBeInTheDocument();
  });

  it('shows the demonstration banner when the summary includes local fixture imports', async () => {
    vi.spyOn(klmApi, 'tracking').mockResolvedValue({ ...baseSummary, containsLocalFixtureData: true });
    renderPage();

    expect(await screen.findByText(/Inclui dados de demonstração \(cenário local\)/)).toBeInTheDocument();
  });
});

describe('TrackingPage CTWA backfill dialog', () => {
  it('runs a dry-run automatically on open, then confirms and executes', async () => {
    vi.spyOn(klmApi, 'tracking').mockResolvedValue({ ...baseSummary });
    const backfillSpy = vi.spyOn(klmApi, 'backfillCtwa')
      .mockResolvedValueOnce({
        scanned: 12, withReferral: 3, eventsCreated: 3, skippedExisting: 0, unresolved: 0,
        dryRun: true, sampleDedupeKeys: ['ctwa:wamid.1', 'ctwa:wamid.2', 'ctwa:wamid.3'],
      })
      .mockResolvedValueOnce({
        scanned: 12, withReferral: 3, eventsCreated: 3, skippedExisting: 0, unresolved: 0,
        dryRun: false, sampleDedupeKeys: ['ctwa:wamid.1', 'ctwa:wamid.2', 'ctwa:wamid.3'],
      });
    renderPage('/app/rastreamento?tab=eventos');

    fireEvent.click(await screen.findByRole('button', { name: /Recuperar histórico de anúncios \(CTWA\)/ }));

    expect(await screen.findByText('Confirme para gravar 3 evento(s) de aquisição. Rodar de novo depois não duplica nada.')).toBeInTheDocument();
    expect(backfillSpy).toHaveBeenNthCalledWith(1, { dryRun: true });

    fireEvent.click(await screen.findByRole('button', { name: /Confirmar e recuperar 3 evento\(s\)/ }));

    const hasResultText = (node: Element | null) =>
      Boolean(node?.textContent?.includes('3 evento(s) de aquisição recuperado(s) e vinculado(s) às conversas.'));
    expect(await screen.findByText((_, element) =>
      hasResultText(element) && Array.from(element?.children ?? []).every((child) => !hasResultText(child)),
    )).toBeInTheDocument();
    expect(backfillSpy).toHaveBeenNthCalledWith(2, { dryRun: false });
  });

  it('shows a retry affordance when the dry-run fails', async () => {
    vi.spyOn(klmApi, 'tracking').mockResolvedValue({ ...baseSummary });
    const backfillSpy = vi.spyOn(klmApi, 'backfillCtwa').mockRejectedValue(new Error('falha de rede'));
    renderPage('/app/rastreamento?tab=eventos');

    fireEvent.click(await screen.findByRole('button', { name: /Recuperar histórico de anúncios \(CTWA\)/ }));

    expect(await screen.findByText('Não foi possível analisar o histórico.')).toBeInTheDocument();
    expect(screen.getByText('falha de rede')).toBeInTheDocument();
    expect(backfillSpy).toHaveBeenCalledTimes(1);
  });

  it('reports nothing to recover without showing a confirm button', async () => {
    vi.spyOn(klmApi, 'tracking').mockResolvedValue({ ...baseSummary });
    vi.spyOn(klmApi, 'backfillCtwa').mockResolvedValue({
      scanned: 5, withReferral: 0, eventsCreated: 0, skippedExisting: 0, unresolved: 0,
      dryRun: true, sampleDedupeKeys: [],
    });
    renderPage('/app/rastreamento?tab=eventos');

    fireEvent.click(await screen.findByRole('button', { name: /Recuperar histórico de anúncios \(CTWA\)/ }));

    expect(await screen.findByText(/Nada para recuperar/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Confirmar e recuperar/ })).not.toBeInTheDocument();
  });
});
