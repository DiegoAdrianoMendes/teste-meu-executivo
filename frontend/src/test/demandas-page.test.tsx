/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import DemandasPage from '@/klm/pages/DemandasPage';
import DemandaDetailPage from '@/klm/pages/DemandaDetailPage';
import { klmApi, type KlmCase360, type KlmCaseStrategyMatrix, type KlmLegalDemand } from '@/klm/api';
import { ApiError } from '@/services/http';

const permissionMock = vi.hoisted(() => vi.fn((_module: string, _action: string) => true));
const toastMock = vi.hoisted(() => vi.fn());

vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({
    can: permissionMock,
    state: { currentUser: { id: 'lawyer-1', name: 'Ana Advogada', email: 'ana@klmadvogados.com.br' } },
  }),
  useOptionalAppStore: () => ({ can: permissionMock, state: {} }),
}));

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }));

function emptyFunnel() {
  return { boardVersion: 1, stages: [], cards: [], metrics: {} };
}

function makeDemand(overrides: Partial<KlmLegalDemand> = {}): KlmLegalDemand {
  return {
    id: 'demand-1',
    workspaceId: 'workspace-local',
    externalRef: null,
    nature: 'judicial',
    area: 'Cível',
    subarea: null,
    clientId: 'client-1',
    parties: [],
    unit: null,
    team: null,
    responsibleUserId: null,
    strategy: null,
    risk: 'medio',
    value: null,
    phase: 'em_andamento',
    sla: {},
    playbookRef: null,
    documentsRequired: [],
    matterId: null,
    contractRef: null,
    financeRef: null,
    createdAt: '2026-07-20T10:00:00Z',
    updatedAt: '2026-07-25T10:00:00Z',
    ...overrides,
  };
}

function makeCase360(demand: KlmLegalDemand): KlmCase360 {
  return {
    generatedAt: '2026-07-26T10:00:00Z',
    demandId: demand.id,
    workspaceId: demand.workspaceId,
    demandaContratada: demand,
    oportunidade: null,
    cliente: {
      id: demand.clientId,
      name: 'Maria Cliente',
      cpf: null,
      email: 'maria@example.com',
      phone: '61999990000',
      employer: null,
      position: null,
      sensitiveDataHidden: false,
      maskedLabel: null,
    },
    responsavel: null,
    partes: [],
    processos: [],
    prazos: {
      legalDemandId: demand.id,
      prazoLegalAt: null,
      prazoFatalAt: null,
      prazoInternoAt: null,
      slaClienteAt: null,
      prazoPlaybookAt: null,
      margemSegurancaDias: null,
      slaEffectiveAt: null,
      slaEffectiveLayer: null,
      slaEscalationLevel: null,
    },
    trabalhoDistribuido: { responsibleUser: null, lastDecision: null, openEscalations: [] },
    documentos: [],
    financeiro: {
      entries: [],
      totals: { totalReceivable: 0, totalPaid: 0, totalPending: 0, totalOverdue: 0 },
      feeArrangements: [],
    },
    comunicacoes: { totalCount: 0, recent: [] },
    estrategia: null,
    provas: { legalDemandId: demand.id, facts: [], proofLinks: [], pleadings: [], gaps: [] },
    excecoesAbertas: [],
    timeline: [],
  };
}

function emptyStrategyMatrix(demandId: string): KlmCaseStrategyMatrix {
  return { legalDemandId: demandId, facts: [], proofLinks: [], pleadings: [], gaps: [] };
}

function renderDemandas() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <DemandasPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderDemandaDetail(demandId: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/app/demandas/${demandId}`]}>
        <Routes>
          <Route path="/app/demandas/:demandId" element={<DemandaDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  permissionMock.mockReset();
  permissionMock.mockReturnValue(true);
  toastMock.mockReset();
});

describe('DemandasPage', () => {
  it('lists real demands with nature and phase badge', async () => {
    const demand = makeDemand();
    vi.spyOn(klmApi, 'listLegalDemands').mockResolvedValue({ items: [demand], total: 1 });
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(emptyFunnel());

    renderDemandas();

    expect(await screen.findByTestId(`demanda-row-${demand.id}`)).toBeInTheDocument();
    expect(screen.getByText('Judicial')).toBeInTheDocument();
    expect(screen.getByText('Em andamento')).toBeInTheDocument();
  });

  it('shows the honest access-denied state when the legal module returns 403', async () => {
    vi.spyOn(klmApi, 'listLegalDemands').mockRejectedValue(
      new ApiError(403, 'Permissão insuficiente para esta ação.', { code: 'AUTH-012' }),
    );
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(emptyFunnel());

    renderDemandas();

    expect(await screen.findByTestId('demandas-access-denied')).toHaveTextContent('Acesso ao módulo jurídico restrito');
  });

  it('disables "Nova demanda" when the user lacks legal.manage', async () => {
    permissionMock.mockImplementation((module: string, action?: string) => !(module === 'legal' && action === 'manage'));
    vi.spyOn(klmApi, 'listLegalDemands').mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(emptyFunnel());

    renderDemandas();

    const button = await screen.findByRole('button', { name: /Nova demanda/i });
    expect(button).toBeDisabled();
  });
});

describe('DemandaDetailPage', () => {
  it('renders the tab container with Visão 360, Partes and Estratégia populated from mocked data', async () => {
    const demand = makeDemand({ id: 'demand-2', nature: 'consultivo', area: 'Societário' });
    vi.spyOn(klmApi, 'getLegalDemand').mockResolvedValue(demand);
    vi.spyOn(klmApi, 'getLegalDemandCase360').mockResolvedValue(makeCase360(demand));
    vi.spyOn(klmApi, 'listCaseParties').mockResolvedValue({ items: [] });
    vi.spyOn(klmApi, 'getCaseStrategyMatrix').mockResolvedValue(emptyStrategyMatrix(demand.id));

    renderDemandaDetail(demand.id);

    expect(await screen.findByText('Consultivo · Societário')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Visão 360' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Partes' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Estratégia' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Peças' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Documentos' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Protocolo' })).toBeInTheDocument();

    expect(await screen.findByText('Maria Cliente')).toBeInTheDocument();

    // Radix Tabs seleciona no `mousedown`, não no `click` — `fireEvent.click`
    // sozinho não troca a aba em jsdom (ver @radix-ui/react-tabs Trigger).
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Partes' }));
    await waitFor(() => expect(screen.getByText('Nenhuma parte cadastrada ainda.')).toBeInTheDocument());

    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Peças' }));
    // Pós-wiring, a aba Peças monta o componente real (LegalArtifactsTab); basta provar que a troca de aba ativa o painel correto.
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Peças' })).toHaveAttribute('data-state', 'active'));
  });

  it('shows the honest access-denied state when the demand fetch returns 403', async () => {
    vi.spyOn(klmApi, 'getLegalDemand').mockRejectedValue(
      new ApiError(403, 'Permissão insuficiente para esta ação.', { code: 'AUTH-012' }),
    );

    renderDemandaDetail('demand-3');

    expect(await screen.findByText('Acesso ao módulo jurídico restrito')).toBeInTheDocument();
  });
});
