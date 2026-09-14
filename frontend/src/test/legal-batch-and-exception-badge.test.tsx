/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { ReactElement } from 'react';

import DemandaDetailPage from '@/klm/pages/DemandaDetailPage';
import LeadPage from '@/klm/pages/LeadPage';
import { FunnelCardWorkspaceSheet } from '@/klm/components/FunnelCardWorkspaceSheet';
import {
  klmApi,
  type KlmCase360,
  type KlmFunnel,
  type KlmLegalBatch,
  type KlmLegalBatchItem,
  type KlmLegalBatchPanel,
  type KlmLegalDemand,
  type KlmMatterDetail,
} from '@/klm/api';

/**
 * Sprint B UI — "lotes por cliente/demanda" + selo "gerado por falha" no
 * to-do (Onda 8 / Central de Exceções). Consome o contrato real de
 * `app/api/v1/endpoints/legal_batches.py` via `klmApi` mockado (nunca
 * `fetch` cru) — ver `LegalBatchPanel` e `DetailDrawer`/
 * `FunnelCardWorkspaceSheet`/`InternalPage` para o selo.
 */

const permissionMock = vi.hoisted(() => vi.fn((_module: string, _action: string) => true));

vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({
    can: permissionMock,
    state: { currentUser: { id: 'lawyer-1', name: 'Ana Advogada', email: 'ana@klmadvogados.com.br' } },
  }),
  useOptionalAppStore: () => ({ can: permissionMock, canUseCapability: () => true, state: {} }),
}));

vi.mock('@/klm/components/BookingLinkDialog', () => ({ BookingLinkDialog: () => null }));
vi.mock('@/klm/components/TaskCollaborationDrawer', () => ({ TaskCollaborationDrawer: () => null }));
vi.mock('@/klm/components/NativeCRMOperationsPanel', () => ({ NativeCRMOperationsPanel: () => null }));
vi.mock('@/klm/components/NativeModuleBar', () => ({ NativeModuleBar: () => null }));
vi.mock('@/klm/components/TreatmentCostPanel', () => ({ TreatmentCostPanel: () => null }));

function renderWithProviders(ui: ReactElement, route: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function makeBatch(overrides: Partial<KlmLegalBatch> = {}): KlmLegalBatch {
  return {
    id: 'batch-1',
    workspaceId: 'workspace-local',
    name: 'Lote de teste',
    externalRef: null,
    criterionType: 'cliente',
    criterionValue: {},
    playbookRef: null,
    defaultAction: 'classificar',
    status: 'aberto',
    createdByUserId: 'lawyer-1',
    createdAt: '2026-07-26T10:00:00Z',
    updatedAt: '2026-07-26T10:00:00Z',
    ...overrides,
  };
}

function makePanel(overrides: Partial<KlmLegalBatchPanel> = {}): KlmLegalBatchPanel {
  return {
    batchId: 'batch-1',
    status: 'aberto',
    totalItems: 3,
    pendente: 1,
    classificado: 0,
    vinculado: 0,
    rascunho: 0,
    revisado: 0,
    bloqueado: 0,
    baixaConfianca: 0,
    divergente: 0,
    aguardandoHumano: 2,
    concluido: 0,
    estimatedCostTotal: 12.5,
    estimatedTimeSavedMinutesTotal: 40,
    ...overrides,
  };
}

function makeItem(overrides: Partial<KlmLegalBatchItem> = {}): KlmLegalBatchItem {
  return {
    id: 'item-1',
    workspaceId: 'workspace-local',
    batchId: 'batch-1',
    itemRef: 'demand-1',
    legalDemandId: 'demand-1',
    action: 'classificar',
    decisionKind: 'classificacao',
    state: 'aguardando_humano',
    stateHistory: [],
    inputPayload: {},
    resultPayload: {},
    confidence: null,
    errorReason: null,
    autonomyLevelApplied: null,
    autonomyPolicyId: null,
    requiresHumanApproval: true,
    approvedByUserId: null,
    approvedAt: null,
    estimatedCost: null,
    estimatedTimeSavedMinutes: null,
    responsibleUserId: null,
    createdAt: '2026-07-26T10:00:00Z',
    updatedAt: '2026-07-26T10:00:00Z',
    ...overrides,
  };
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

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  permissionMock.mockReset();
  permissionMock.mockReturnValue(true);
});

describe('Processar em lote — Demanda', () => {
  it('cria o lote recortado pela demanda e mostra o painel real', async () => {
    const demand = makeDemand();
    vi.spyOn(klmApi, 'getLegalDemand').mockResolvedValue(demand);
    vi.spyOn(klmApi, 'getLegalDemandCase360').mockResolvedValue(makeCase360(demand));
    vi.spyOn(klmApi, 'listCaseParties').mockResolvedValue({ items: [] });
    vi.spyOn(klmApi, 'getCaseStrategyMatrix').mockResolvedValue({ legalDemandId: demand.id, facts: [], proofLinks: [], pleadings: [], gaps: [] });
    const createBatch = vi.spyOn(klmApi, 'createLegalBatchFromContext').mockResolvedValue(makeBatch());
    vi.spyOn(klmApi, 'getLegalBatchPanel').mockResolvedValue(makePanel());
    vi.spyOn(klmApi, 'listLegalBatchItems').mockResolvedValue([makeItem()]);

    renderWithProviders(
      <Routes><Route path="/app/demandas/:demandId" element={<DemandaDetailPage />} /></Routes>,
      `/app/demandas/${demand.id}`,
    );

    const button = await screen.findByRole('button', { name: /Processar em lote/i });
    fireEvent.click(button);

    await waitFor(() => expect(createBatch).toHaveBeenCalledWith(
      expect.objectContaining({ legalDemandId: demand.id, externalRef: `legal-batch:demanda:${demand.id}` }),
    ));

    expect(await screen.findByTestId('legal-batch-panel')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument(); // total de itens
    expect(screen.getByText('2')).toBeInTheDocument(); // aguardando humano
  });
});

describe('Processar em lote — Cliente', () => {
  const matterId = '428c5996-f1ab-420f-ba3f-24d20280d452';

  function matterFixture(): KlmMatterDetail {
    return {
      id: matterId,
      columnId: 'triagem_sdr',
      microStatus: 'qualificado',
      demandType: 'Negativa de medicamento',
      leadSource: null,
      tags: [],
      executiveSummary: null,
      healthSubtheme: null,
      healthSubthemeCustom: null,
      healthObjectName: null,
      healthInsurer: null,
      healthDenialReason: null,
      medicalUrgencyHours: null,
      economicValueAnnual: null,
      leadScore: 0,
      manualContextRevision: 0,
      manualContextUpdatedAt: null,
      nextAction: null,
      docScore: 0,
      productionScore: 0,
      claimValue: null,
      client: { id: 'client-42', name: 'Cliente Teste', email: null, phone: null },
      documents: [],
      timeline: [],
      knowledgeChunks: [],
      revenueProjection: null,
      honorariumEstimate: null,
      campaignAttribution: null,
    } as KlmMatterDetail;
  }

  function funnelFixture(): KlmFunnel {
    return {
      boardVersion: 1,
      stages: [{ id: 'triagem_sdr', label: 'Triagem', count: 1 }],
      metrics: {},
      cards: [{
        id: matterId,
        clientId: 'client-42',
        name: 'Cliente Teste',
        subject: 'não informado',
        object: 'não informado',
        source: 'não atribuída',
        owner: 'não atribuído',
        probability: 0,
        initialFee: 0,
        projectedRevenue: 0,
        slaDueAt: null,
        documents: 0,
        documentScore: 0,
        stage: 'triagem_sdr',
        microStatus: 'qualificado',
      }],
    };
  }

  it('cria o lote recortado pelo cliente do card e mostra o painel real', async () => {
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(funnelFixture());
    vi.spyOn(klmApi, 'threads').mockResolvedValue([]);
    vi.spyOn(klmApi, 'matter').mockResolvedValue(matterFixture());
    vi.spyOn(klmApi, 'legalPricingPolicy').mockResolvedValue({
      minimumInitialFee: 0, maximumInitialFee: null, allowSuccessOnly: true, successOnlyMoralDamagesPercentage: 0,
      allowInjunctionMilestoneFee: true, oncologyObligationFeeMinimum: 0, oncologyObligationFeeMaximum: 0,
      requireTreatmentCycleForMedicationValue: false, requireHumanReview: false, currency: 'BRL', policyNote: '',
    });
    const createBatch = vi.spyOn(klmApi, 'createLegalBatchFromContext').mockResolvedValue(makeBatch({ criterionType: 'cliente' }));
    vi.spyOn(klmApi, 'getLegalBatchPanel').mockResolvedValue(makePanel({ pendente: 0, concluido: 3, aguardandoHumano: 0, totalItems: 3 }));
    vi.spyOn(klmApi, 'listLegalBatchItems').mockResolvedValue([]);

    renderWithProviders(<LeadPage />, `/app/leads?matter=${matterId}`);

    const button = await screen.findByRole('button', { name: /Processar em lote/i });
    fireEvent.click(button);

    await waitFor(() => expect(createBatch).toHaveBeenCalledWith(
      expect.objectContaining({ clientId: 'client-42', externalRef: 'legal-batch:cliente:client-42' }),
    ));

    expect(await screen.findByTestId('legal-batch-panel')).toBeInTheDocument();
    expect(screen.getByText('Lote deste cliente')).toBeInTheDocument();
  });
});

describe('Tarefa gerada por exceção — selo no to-do', () => {
  it('mostra o selo "gerado por falha" com link para o caso quando a task tem sourceExceptionId', async () => {
    const card = {
      id: 'matter-badge-1',
      name: 'Caso com exceção',
      owner: 'Advogado A',
      priority: 'normal',
      urgency: 'normal',
      tags: [],
      nextAction: null,
      nextDeadline: null,
      docs: 0,
      documentScore: 0,
      openTasks: 1,
      stage: 'triagem_sdr',
    };
    vi.spyOn(klmApi, 'matter').mockResolvedValue({
      id: card.id,
      columnId: 'triagem_sdr',
      microStatus: 'qualificado',
      demandType: 'Negativa de medicamento',
      leadSource: null,
      tags: [],
      executiveSummary: null,
      healthSubtheme: null,
      healthSubthemeCustom: null,
      healthObjectName: null,
      healthInsurer: null,
      healthDenialReason: null,
      medicalUrgencyHours: null,
      economicValueAnnual: null,
      leadScore: 0,
      manualContextRevision: 0,
      manualContextUpdatedAt: null,
      nextAction: null,
      docScore: 0,
      productionScore: 0,
      claimValue: null,
      client: { id: 'client-badge-1', name: 'Cliente com exceção', email: null, phone: null },
      documents: [],
      timeline: [],
      knowledgeChunks: [],
      revenueProjection: null,
      honorariumEstimate: null,
      campaignAttribution: null,
    } as unknown as KlmMatterDetail);
    vi.spyOn(klmApi, 'matterTasks').mockResolvedValue([
      {
        id: 'task-exception-1',
        version: 1,
        matterId: card.id,
        title: 'Revisar publicação sem vínculo confiável',
        description: null,
        status: 'pending',
        priority: 'alta',
        assignedTo: null,
        dueDate: null,
        isAiGenerated: true,
        createdAt: '2026-07-26T10:00:00Z',
        sourceExceptionId: 'exception-1',
      },
      {
        id: 'task-normal-1',
        version: 1,
        matterId: card.id,
        title: 'Tarefa comum sem origem de falha',
        description: null,
        status: 'pending',
        priority: 'media',
        assignedTo: null,
        dueDate: null,
        isAiGenerated: false,
        createdAt: '2026-07-26T10:00:00Z',
        sourceExceptionId: null,
      },
    ]);

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/app/funil']}>
          <FunnelCardWorkspaceSheet
            card={card}
            open
            onOpenChange={() => {}}
            stageLabel="Triagem"
            stageOptions={[{ id: card.stage, label: 'Triagem' }]}
            onStageChange={() => {}}
            movePending={false}
            canEdit
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Checklist' }));

    const badgeLink = await screen.findByRole('link', { name: /gerado por falha/i });
    expect(badgeLink).toHaveAttribute('href', `/app/leads?matter=${card.id}`);

    expect(screen.getByText('Revisar publicação sem vínculo confiável')).toBeInTheDocument();
    // A tarefa sem sourceExceptionId nunca ganha o selo.
    const normalRow = screen.getByText('Tarefa comum sem origem de falha').closest('button');
    expect(normalRow).not.toBeNull();
    expect(normalRow?.querySelector('a')).toBeNull();
  });
});
