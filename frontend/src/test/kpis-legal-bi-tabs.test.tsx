/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  klmApi,
  type KlmAiBi,
  type KlmCapacityReport,
  type KlmLegalBiRead,
  type KlmOperationBi,
} from '@/klm/api';
import KpisPage from '@/klm/pages/KpisPage';
import * as appStore from '@/store/app-context';
import { ApiError } from '@/services/http';

/**
 * BI jurídico + capacidade como abas da página de Indicadores existente
 * (Onda 8, Sprint C UI, decisão do dono: não item de menu novo). Cobre:
 * as 4 abas novas renderizam com mock, o metadado de honestidade
 * (§15) aparece em cada uma, e indicador `null` do backend (ex.: "tempo
 * economizado" em IA) aparece como "Indisponível" — nunca um zero
 * fabricado. Ver `KpisPage`.
 */

function installPointerCaptureForRadix() {
  Object.defineProperty(HTMLElement.prototype, 'hasPointerCapture', { configurable: true, value: () => false });
  Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', { configurable: true, value: () => undefined });
  Object.defineProperty(HTMLElement.prototype, 'releasePointerCapture', { configurable: true, value: () => undefined });
}

async function activateTab(name: string) {
  const trigger = await screen.findByRole('tab', { name });
  fireEvent.mouseDown(trigger, { button: 0 });
  fireEvent.click(trigger);
}

function mockOverviewQueries() {
  vi.spyOn(klmApi, 'dashboard').mockResolvedValue({
    metrics: { activeThreads: 1, dueFollowUps: 0, pendingApprovals: 0, externalActions: 0 },
    funnel: [],
    decisions: [],
    runtime: {
      mode: 'local', localOnly: true, localInfrastructure: true, externalActionsEnabled: false,
      awsEnabled: false, modelApisEnabled: false, onlineApproved: false, localBootstrapEnabled: true,
      providers: {},
    },
  });
  vi.spyOn(klmApi, 'kpis').mockResolvedValue({
    topMetrics: [], families: [], channels: [], periodDays: 30, subtheme: null,
    generatedAt: '2026-07-26T12:00:00Z', source: 'local_database',
    teamPerformance: { audience: 'workspace_team', periodDays: 30, generatedAt: '2026-07-26T12:00:00Z', scoreFormula: 'fórmula', rows: [] },
  });
  vi.spyOn(klmApi, 'financeOverview').mockResolvedValue({
    totalOpenReceivables: 0, overdueAmount: 0, scheduledAmount: 0, paidAmount: 0,
    pendingReminders: 0, entriesDueSoon: 0, upcomingEntries: [], highlights: [],
  });
  vi.spyOn(klmApi, 'quality').mockResolvedValue({ score: 90, metrics: {}, dimensions: [], reviewQueue: [], generatedAt: '2026-07-26T12:00:00Z' });
  vi.spyOn(klmApi, 'tracking').mockResolvedValue({
    periodDays: 30, periodStart: '2026-06-26T12:00:00Z', totalEvents: 0, identifiedJourneys: 0,
    leads: 0, qualifiedLeads: 0, meetings: 0, clients: 0, adSpend: 0, attributableRevenue: 0,
    ignoredValueEvents: 0, conversionRate: 0, cac: null, roas: null, byChannel: [], eventCoverage: [],
    timeTrend: [], journeys: [], remarketing: [],
    attributionCoverage: { conversations: 0, trackedConversations: 0, untrackedConversations: 0, trackingRate: 0, conversionEvents: 0, attributedConversions: 0, unattributedConversions: 0, attributedConversionRate: 0, feedbackEligibleConversions: 0, feedbackBlockedNoEvidence: 0, fullHierarchyTouches: 0, partialHierarchyTouches: 0 },
    attributionRows: [], funnelSteps: [], insights: [],
    serviceQuality: { findings: 0, activeFindings: 0, criticalFindings: 0, unansweredLeads: 0, deliveryMismatches: 0, duplicateOutbound: 0, resolvedFindings: 0, auditedThreads: 0, qualityScore: 100 },
    conversionFeedback: { prepared: 0, blockedExternal: 0, awaitingApproval: 0, approvedLocal: 0, rejected: 0, duplicatePrevented: 0, payloadReady: 0, configurationBlocked: 0, preparedProofs: 0, providerReceipts: 0, failedReceipts: 0, providers: {} },
    feedbackEvents: [], dailyChannels: [], containsLocalFixtureData: false, generatedAt: '2026-07-26T12:00:00Z',
  });
}

const operationBi: KlmOperationBi = {
  honesty: {
    generatedAt: '2026-07-26T12:00:00Z',
    periodFrom: '2026-06-26T12:00:00Z',
    periodTo: '2026-07-26T12:00:00Z',
    sampleSize: 42,
    origin: 'process_publications+klm_audit_events, tasks+matters, legal_demands',
    missingFields: ['tarefas_sem_data_classificavel — Task.due_date é texto livre não validado'],
  },
  tempoPorMarco: [{ toState: 'triagem_concluida', sampleSize: 5, avgHours: 12.5, medianHours: 10 }],
  tarefasD0: 3,
  tarefasD1: 2,
  tarefasAtrasadas: 1,
  tarefasSemDataClassificavel: 0,
  tarefasSemResponsavel: 4,
  retrabalhoCorrecoesPendentes: 2,
  minutasComMultiplasVersoes: 1,
  processosSemMovimentacao: 0,
  backlogPorResponsavel: [{ key: 'user-1', count: 6 }],
  slaPorCliente: [{ clientId: 'client-1', clientName: 'Cliente Exemplo', noPrazo: 3, atrasado: 1, semSlaDefinido: 0 }],
  excecoesPorOrigem: [{ key: 'triagem_ia', count: 2 }],
};

const legalBi: KlmLegalBiRead = {
  honesty: {
    generatedAt: '2026-07-26T12:00:00Z',
    periodFrom: '2026-06-26T12:00:00Z',
    periodTo: '2026-07-26T12:00:00Z',
    sampleSize: 18,
    origin: 'legal_demands (+ matters.column_id quando vinculado, klm_audit_events)',
    missingFields: ['duracao_media_dias_por_natureza é aproximada por LegalDemand.updated_at'],
  },
  estoquePorFase: [{ key: 'triagem', count: 5 }],
  entradasNoPeriodo: 4,
  saidasNoPeriodo: 2,
  duracaoMediaDiasPorNatureza: [{ nature: 'judicial', sampleSize: 3, avgDays: 15.2 }],
  valoresPorFase: [{ key: 'triagem', totalValue: 25000, count: 2 }],
  contingenciaPorRisco: [{ key: 'alto', totalValue: 15000, count: 1 }],
  acordosNoPeriodo: 1,
  demandasComEstrategiaRegistrada: 3,
  resultadoPorColunaKanban: [{ key: 'ganho', count: 2 }],
  resultadoSemMatterVinculado: 1,
  motivoEncerramentoRegistrado: 1,
};

const aiBi: KlmAiBi = {
  honesty: {
    generatedAt: '2026-07-26T12:00:00Z',
    periodFrom: '2026-06-26T12:00:00Z',
    periodTo: '2026-07-26T12:00:00Z',
    sampleSize: 30,
    origin: 'agent_runs + human_overrides',
    missingFields: [
      'tempo_economizado — nenhum modelo grava a linha de base manual para comparar; não é calculado para não fabricar o número',
      'rollback — não há um estado/evento dedicado de reversão de saída de agente hoje',
    ],
  },
  passosAutomatizadosPorAgente: [{ key: 'agente-triagem', count: 12 }],
  execucoesTotais: 30,
  execucoesFalhas: 2,
  execucoesAguardandoHumano: 5,
  intervencoesHumanasTotal: 8,
  taxaIntervencaoHumana: 0.2667,
  decisoesAceitas: 5,
  decisoesCorrigidas: 2,
  decisoesRejeitadas: 1,
  custoUsdPorAgente: [{ key: 'agente-triagem', totalValue: 3.42, count: 12 }],
  tokensInputTotais: 120000,
  tokensOutputTotais: 45000,
};

const capacity: KlmCapacityReport = {
  honesty: {
    generatedAt: '2026-07-26T12:00:00Z',
    sampleSize: 2,
    origin: 'workspace_memberships (ativos) + legal_demands (carga) + tasks/matters (carga) + staff_availability_profiles',
    missingFields: ['não há % de utilização: carga e disponibilidade são unidades diferentes'],
  },
  porResponsavel: [
    { responsibleUserId: 'user-1', responsibleName: 'Advogada Exemplo', cargaDemandasAtivas: 5, cargaTarefasAbertas: 3, disponibilidadeConfigurada: true, horasSemanaConfiguradas: 40, disponivelAgora: true, motivoDisponibilidade: 'recurring_schedule' },
    { responsibleUserId: 'user-2', responsibleName: 'Advogado Sem Agenda', cargaDemandasAtivas: 2, cargaTarefasAbertas: 1, disponibilidadeConfigurada: false, horasSemanaConfiguradas: null, disponivelAgora: true, motivoDisponibilidade: 'schedule_not_configured' },
  ],
  porUnidade: [{ key: 'trabalhista', cargaDemandasAtivas: 4 }],
  porEquipe: [{ key: 'equipe_1', cargaDemandasAtivas: 7 }],
};

function renderKpisPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/app/kpis']}>
        <KpisPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('KpisPage — abas de BI jurídico e capacidade (Sprint C UI)', () => {
  beforeEach(() => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => true } as unknown as ReturnType<typeof appStore.useAppStore>);
    installPointerCaptureForRadix();
    mockOverviewQueries();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('keeps the existing "Visão geral" tab and adds the 4 new tabs without removing anything', async () => {
    vi.spyOn(klmApi, 'getLegalBiOperacao').mockResolvedValue(operationBi);
    vi.spyOn(klmApi, 'getLegalBiJuridico').mockResolvedValue(legalBi);
    vi.spyOn(klmApi, 'getLegalBiIa').mockResolvedValue(aiBi);
    vi.spyOn(klmApi, 'getLegalCapacity').mockResolvedValue(capacity);

    renderKpisPage();

    expect(await screen.findByText('KPIs completos')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Visão geral' })).toHaveAttribute('data-state', 'active');
    expect(screen.getByRole('tab', { name: 'Operação jurídica' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Jurídico' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'IA' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Capacidade' })).toBeInTheDocument();
    // Tela original preservada — trecho que já existia antes desta sprint.
    expect(await screen.findByText('Matriz completa de KPIs')).toBeInTheDocument();
  });

  it('renders "Operação jurídica" with the honesty metadata and the real indicators', async () => {
    vi.spyOn(klmApi, 'getLegalBiOperacao').mockResolvedValue(operationBi);
    vi.spyOn(klmApi, 'getLegalBiJuridico').mockResolvedValue(legalBi);
    vi.spyOn(klmApi, 'getLegalBiIa').mockResolvedValue(aiBi);
    vi.spyOn(klmApi, 'getLegalCapacity').mockResolvedValue(capacity);

    renderKpisPage();
    await activateTab('Operação jurídica');

    const tab = await screen.findByTestId('bi-tab-operacao');
    expect(await screen.findByTestId('bi-honesty-banner')).toBeInTheDocument();
    expect(screen.getByText(/Corte temporal/)).toBeInTheDocument();
    expect(screen.getByText('42 registros')).toBeInTheDocument();
    expect(screen.getByText(operationBi.honesty.origin)).toBeInTheDocument();
    expect(screen.getByText(/Task.due_date é texto livre/)).toBeInTheDocument();
    expect(tab.querySelector('article')).toBeTruthy();
    expect(screen.getByText('Tarefas D0')).toBeInTheDocument();
    expect(screen.getByText('Cliente Exemplo')).toBeInTheDocument();
  });

  it('renders "Jurídico" with the honesty metadata and BRL amounts', async () => {
    vi.spyOn(klmApi, 'getLegalBiOperacao').mockResolvedValue(operationBi);
    vi.spyOn(klmApi, 'getLegalBiJuridico').mockResolvedValue(legalBi);
    vi.spyOn(klmApi, 'getLegalBiIa').mockResolvedValue(aiBi);
    vi.spyOn(klmApi, 'getLegalCapacity').mockResolvedValue(capacity);

    renderKpisPage();
    await activateTab('Jurídico');

    expect(await screen.findByTestId('bi-tab-juridico')).toBeInTheDocument();
    expect(await screen.findByTestId('bi-honesty-banner')).toBeInTheDocument();
    expect(screen.getByText('18 registros')).toBeInTheDocument();
    expect(screen.getByText('Entradas no período')).toBeInTheDocument();
    expect(screen.getByText(/R\$\s*25\.000,00/)).toBeInTheDocument();
  });

  it('renders "IA" showing missing fields (ex.: tempo economizado) as unavailable, never as 0', async () => {
    vi.spyOn(klmApi, 'getLegalBiOperacao').mockResolvedValue(operationBi);
    vi.spyOn(klmApi, 'getLegalBiJuridico').mockResolvedValue(legalBi);
    vi.spyOn(klmApi, 'getLegalBiIa').mockResolvedValue(aiBi);
    vi.spyOn(klmApi, 'getLegalCapacity').mockResolvedValue(capacity);

    renderKpisPage();
    await activateTab('IA');

    expect(await screen.findByTestId('bi-tab-ia')).toBeInTheDocument();
    expect(screen.getByText(/tempo_economizado/)).toBeInTheDocument();
    expect(screen.getByText(/rollback/)).toBeInTheDocument();
    expect(screen.getByText('Execuções totais')).toBeInTheDocument();
    expect(screen.getByText('Taxa de intervenção humana')).toBeInTheDocument();
  });

  it('shows "Indisponível" (not a fabricated 0) when an AI indicator is null', async () => {
    vi.spyOn(klmApi, 'getLegalBiOperacao').mockResolvedValue(operationBi);
    vi.spyOn(klmApi, 'getLegalBiJuridico').mockResolvedValue(legalBi);
    vi.spyOn(klmApi, 'getLegalBiIa').mockResolvedValue({ ...aiBi, execucoesTotais: 0, taxaIntervencaoHumana: null });
    vi.spyOn(klmApi, 'getLegalCapacity').mockResolvedValue(capacity);

    renderKpisPage();
    await activateTab('IA');

    expect(await screen.findByTestId('bi-tab-ia')).toBeInTheDocument();
    expect(screen.getByText('Indisponível')).toBeInTheDocument();
    expect(screen.queryByText('0%')).not.toBeInTheDocument();
  });

  it('renders "Capacidade" with per-responsible load vs. availability, including "no schedule configured"', async () => {
    vi.spyOn(klmApi, 'getLegalBiOperacao').mockResolvedValue(operationBi);
    vi.spyOn(klmApi, 'getLegalBiJuridico').mockResolvedValue(legalBi);
    vi.spyOn(klmApi, 'getLegalBiIa').mockResolvedValue(aiBi);
    vi.spyOn(klmApi, 'getLegalCapacity').mockResolvedValue(capacity);

    renderKpisPage();
    await activateTab('Capacidade');

    expect(await screen.findByTestId('bi-tab-capacidade')).toBeInTheDocument();
    expect(await screen.findByTestId('bi-honesty-banner')).toBeInTheDocument();
    expect(screen.getByText('Advogada Exemplo')).toBeInTheDocument();
    expect(screen.getByText('Advogado Sem Agenda')).toBeInTheDocument();
    expect(screen.getByText('Indisponível')).toBeInTheDocument();
    expect(screen.getByText('trabalhista')).toBeInTheDocument();
  });

  it('keeps each tab usable when its endpoint is denied for the role (403)', async () => {
    vi.spyOn(klmApi, 'getLegalBiOperacao').mockRejectedValue(new ApiError(403, 'Acesso negado', { code: 'LEGAL-403' }));
    vi.spyOn(klmApi, 'getLegalBiJuridico').mockResolvedValue(legalBi);
    vi.spyOn(klmApi, 'getLegalBiIa').mockResolvedValue(aiBi);
    vi.spyOn(klmApi, 'getLegalCapacity').mockResolvedValue(capacity);

    renderKpisPage();
    await activateTab('Operação jurídica');

    expect(await screen.findByTestId('bi-tab-operacao')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/não tem acesso a os indicadores de operação/)).toBeInTheDocument());
    expect(screen.queryByTestId('bi-honesty-banner')).not.toBeInTheDocument();
  });
});
