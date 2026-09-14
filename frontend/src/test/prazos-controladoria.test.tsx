/** @vitest-environment jsdom */
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { klmApi, type KlmControladoriaPainel, type KlmDistributionRuleListResult, type KlmProcessPublication } from '@/klm/api';
import PrazosPage from '@/klm/pages/PrazosPage';
import * as appStore from '@/store/app-context';

/**
 * Publicações e Controladoria — a mesma tela agora funde o painel diário
 * (11 categorias de `GET /legal/controladoria/painel`) com a agenda de
 * prazos do módulo processual (Sprint B UI, decisão do dono: Publicações
 * e Controladoria são a mesma coisa). Ver `PrazosPage`.
 */

const painel: KlmControladoriaPainel = {
  generatedAt: '2026-07-26T08:00:00Z',
  publicacoesNaoTratadas: 4,
  prazosSugeridos: 2,
  prazosFatais: 1,
  prazosPorJanela: { d7: 3, d3: 2, d1: 1, d0: 0, atrasados: 0 },
  tarefasSemResponsavel: 5,
  processosSemMovimentacao: 2,
  audienciasProximas: 1,
  documentosPendentes: 6,
  minutasBloqueadas: 0,
  excecoesDeAgentes: 1,
  clientesSemAtualizacao: 3,
};

const suggestedPublication: KlmProcessPublication = {
  id: 'publication-1',
  workspaceId: 'workspace-1',
  monitoredProcessId: 'process-1',
  fonte: 'djen',
  externalId: 'ext-1',
  numeroCnj: '0700001-11.2026.8.07.0001',
  conteudo: 'Intimação para manifestação em 15 dias.',
  publishedAt: '2026-07-20T12:00:00Z',
  tipo: 'intimação',
  urgencia: 'alta',
  dias: 15,
  tipoContagem: 'dias_uteis',
  dataFatal: '2026-08-10',
  memoriaCalculo: [],
  prazoStatus: 'prazo_sugerido',
  confirmedByUserId: null,
  confirmedAt: null,
  createdAt: '2026-07-20T12:05:00Z',
};

const distributionRules: KlmDistributionRuleListResult = {
  items: [{
    id: 'rule-1',
    workspaceId: 'workspace-1',
    name: 'Trabalhista — rodízio padrão',
    description: null,
    active: true,
    priority: 1,
    matchNatures: ['judicial'],
    matchAreas: ['trabalhista'],
    matchClientIds: [],
    matchRisks: [],
    matchUnits: [],
    matchTeams: [],
    strategy: 'round_robin',
    candidateUserIds: ['user-1', 'user-2'],
    maxActivePerCandidate: null,
    respectAvailability: true,
    createdByUserId: 'user-1',
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
  }],
  total: 1,
};

function renderPrazosPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <PrazosPage />
    </QueryClientProvider>,
  );
}

describe('Publicações e Controladoria (PrazosPage fundida)', () => {
  beforeEach(() => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({
      can: () => true,
    } as unknown as ReturnType<typeof appStore.useAppStore>);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders the daily controladoria panel (11 categories) together with the deadline agenda and distribution summary', async () => {
    vi.spyOn(klmApi, 'controladoriaPainel').mockResolvedValue(painel);
    vi.spyOn(klmApi, 'listProcessDeadlines').mockImplementation(async (params) => (
      params?.status === 'prazo_sugerido' ? [suggestedPublication] : []
    ));
    vi.spyOn(klmApi, 'listDistributionRules').mockResolvedValue(distributionRules);

    renderPrazosPage();

    expect(await screen.findByRole('heading', { name: 'Publicações e Controladoria' })).toBeInTheDocument();

    // Painel diário — categorias reais vindas de GET /legal/controladoria/painel.
    expect(await screen.findByText('Painel diário da controladoria')).toBeInTheDocument();
    expect(screen.getByText('Publicações não tratadas')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText(String(painel.publicacoesNaoTratadas))).toBeInTheDocument();
    });
    expect(screen.getByText('Prazos fatais')).toBeInTheDocument();
    expect(screen.getByText('Tarefas sem responsável')).toBeInTheDocument();
    expect(screen.getByText('SLA — prazos por janela')).toBeInTheDocument();
    // A publication can independently carry the same D-7 proximity badge;
    // prove that the control window is present without assuming global text
    // uniqueness across the complete page.
    expect(screen.getAllByText('D-7').length).toBeGreaterThan(0);
    expect(screen.getByText('Atrasados')).toBeInTheDocument();

    // Agenda de prazos / publicações — comportamento existente preservado.
    expect(screen.getByText('Publicações e intimações — aguardando confirmação')).toBeInTheDocument();
    expect(await screen.findByText(suggestedPublication.numeroCnj)).toBeInTheDocument();

    // Distribuição — resumo de regras do workspace.
    expect(await screen.findByText('Distribuição de trabalho jurídico')).toBeInTheDocument();
    expect(screen.getByText('Trabalhista — rodízio padrão')).toBeInTheDocument();
    expect(screen.getByText('1 ativa(s)')).toBeInTheDocument();
  });

  it('keeps the deadline agenda usable when the controladoria panel endpoint is denied for the role', async () => {
    const { ApiError } = await import('@/services/http');
    vi.spyOn(klmApi, 'controladoriaPainel').mockRejectedValue(new ApiError(403, 'Acesso negado', { code: 'LEGAL-403' }));
    vi.spyOn(klmApi, 'listDistributionRules').mockRejectedValue(new ApiError(403, 'Acesso negado', { code: 'LEGAL-403' }));
    vi.spyOn(klmApi, 'listProcessDeadlines').mockResolvedValue([]);

    renderPrazosPage();

    expect(await screen.findByRole('heading', { name: 'Publicações e Controladoria' })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('Painel diário da controladoria')).not.toBeInTheDocument());
    expect(await screen.findByText('Publicações e intimações — aguardando confirmação')).toBeInTheDocument();
  });
});
