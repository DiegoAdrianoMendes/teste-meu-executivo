/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { ReactElement } from 'react';
import { klmApi, type KlmFunnel, type KlmLegalArtifactVersion, type KlmLegalPricingPolicy, type KlmMatterDetail } from '@/klm/api';
import LeadPage from '@/klm/pages/LeadPage';
import ProposalPreviewPage from '@/klm/pages/ProposalPreviewPage';
import { buildTimelineRows } from '@/klm/timeline';

vi.mock('@/klm/components/BookingLinkDialog', () => ({
  BookingLinkDialog: ({ open, lockedMatterId }: { open: boolean; lockedMatterId?: string }) => open ? <div data-testid="booking-link-dialog">{lockedMatterId}</div> : null,
}));
vi.mock('@/klm/components/NativeCRMOperationsPanel', () => ({ NativeCRMOperationsPanel: () => null }));
vi.mock('@/klm/components/NativeModuleBar', () => ({ NativeModuleBar: () => null }));
vi.mock('@/klm/components/TreatmentCostPanel', () => ({ TreatmentCostPanel: () => null }));
vi.mock('@/klm/components/TaskCollaborationDrawer', () => ({ TaskCollaborationDrawer: () => null }));

const matterId = '428c5996-f1ab-420f-ba3f-24d20280d452';

const emptyMatter: KlmMatterDetail = {
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
  client: { id: 'client-1', name: 'Cliente sem condições', email: null, phone: null },
  documents: [],
  timeline: [],
  knowledgeChunks: [],
  revenueProjection: null,
  honorariumEstimate: null,
  campaignAttribution: null,
};

const funnel: KlmFunnel = {
  boardVersion: 1,
  stages: [{ id: 'triagem_sdr', label: 'Triagem', count: 1 }],
  metrics: {},
  cards: [{
    id: matterId,
    clientId: 'client-1',
    name: emptyMatter.client.name,
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

const pricingPolicy: KlmLegalPricingPolicy = {
  minimumInitialFee: 4000,
  maximumInitialFee: null,
  allowSuccessOnly: true,
  successOnlyMoralDamagesPercentage: 100,
  allowInjunctionMilestoneFee: true,
  oncologyObligationFeeMinimum: 100000,
  oncologyObligationFeeMaximum: 300000,
  requireTreatmentCycleForMedicationValue: true,
  requireHumanReview: true,
  currency: 'BRL',
  policyNote: 'Política persistida para o teste.',
};

function proposalArtifact(
  content: Record<string, unknown>,
  status: KlmLegalArtifactVersion['status'] = 'draft',
): KlmLegalArtifactVersion {
  return {
    id: 'artifact-1',
    workspaceId: 'workspace-1',
    matterId,
    artifactKey: 'artifact-key-1',
    version: 1,
    kind: 'proposal',
    title: 'Proposta persistida',
    status,
    content,
    sourceSnapshot: { matterId, medicalUrgencyHours: null },
    createdByUserId: 'user-1',
    approvalItemId: null,
    createdAt: '2026-07-18T12:00:00Z',
    updatedAt: '2026-07-18T12:00:00Z',
  };
}

function renderWithQuery(ui: ReactElement, route: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('timeline persistida', () => {
  it('não cria eventos quando mensagens e histórico estão vazios', () => {
    expect(buildTimelineRows()).toEqual([]);
    expect(buildTimelineRows([], [])).toEqual([]);
  });

  it('combina mensagens e eventos reais sem perder a identidade', () => {
    const rows = buildTimelineRows(
      [{ id: 'message-1', occurredAt: '2026-07-18T12:00:00Z', body: 'Mensagem persistida', authorLabel: 'Cliente' }],
      [{ id: 'event-1', type: 'task_created', title: 'Tarefa persistida', description: 'Descrição persistida', timestamp: '2026-07-18T12:01:00Z' }],
    );

    expect(rows.map((row) => row.key)).toEqual(['message-1', 'matter-event:event-1']);
    expect(rows[1]).toMatchObject({ action: 'Tarefa persistida', detail: 'Descrição persistida', agent: 'task created' });
  });
});

describe('Caso 360 sem fallback operacional', () => {
  it('mostra ausências honestas sem fabricar RAG, timeline ou valores', async () => {
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(funnel);
    vi.spyOn(klmApi, 'threads').mockResolvedValue([]);
    vi.spyOn(klmApi, 'matter').mockResolvedValue(emptyMatter);
    vi.spyOn(klmApi, 'legalPricingPolicy').mockResolvedValue(pricingPolicy);

    renderWithQuery(<LeadPage />, `/app/leads?matter=${matterId}`);

    expect(await screen.findByRole('heading', { name: 'Caso 360' })).toBeInTheDocument();
    expect(screen.getByText('Ainda não há eventos persistidos neste caso.')).toBeInTheDocument();
    expect(screen.getByText('Nenhuma fonte foi indexada para este caso.')).toBeInTheDocument();
    expect(screen.getAllByText('Não definido').length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText('Cliente enviou negativa.pdf')).not.toBeInTheDocument();
    expect(screen.queryByText('RAG indexou 17 trechos')).not.toBeInTheDocument();
    expect(screen.queryByText('R$ 5.500,00')).not.toBeInTheDocument();
    expect(screen.queryByText('R$ 2.100.000,00')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Abrir fontes' }));
    expect(await screen.findByRole('dialog', { name: 'Fontes locais do RAG' })).toBeInTheDocument();
    expect(screen.getAllByText('Nenhuma fonte foi indexada para este caso.').length).toBeGreaterThan(0);
    expect(screen.queryByText(/Trechos simulados/)).not.toBeInTheDocument();
  });

  it('mantém agenda, funil e solicitação documental ligados às APIs nativas', async () => {
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(funnel);
    vi.spyOn(klmApi, 'threads').mockResolvedValue([]);
    vi.spyOn(klmApi, 'matter').mockResolvedValue(emptyMatter);
    vi.spyOn(klmApi, 'legalPricingPolicy').mockResolvedValue(pricingPolicy);
    const moveFunnel = vi.spyOn(klmApi, 'moveFunnel').mockResolvedValue({
      matterId,
      columnId: 'proposta',
      microStatus: 'proposta_em_revisao',
      cardOrder: 0,
      boardVersion: 2,
      operationId: 'operation-1',
      agentDispatched: false,
      deduplicated: false,
    });
    const createMatterTask = vi.spyOn(klmApi, 'createMatterTask').mockResolvedValue({
      id: 'task-1',
      version: 1,
      matterId,
      title: 'Solicitar Negativa do plano',
      description: 'Solicitação persistida.',
      status: 'pending',
      priority: 'alta',
      assignedTo: null,
      dueDate: null,
      isAiGenerated: false,
      createdAt: '2026-07-18T12:00:00Z',
    });

    renderWithQuery(<LeadPage />, `/app/leads?matter=${matterId}`);
    expect(await screen.findByRole('heading', { name: 'Caso 360' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Enviar agenda' }));
    expect(screen.getByTestId('booking-link-dialog')).toHaveTextContent(matterId);

    fireEvent.click(screen.getAllByRole('button', { name: 'Mover para Closer' })[0]);
    await waitFor(() => expect(moveFunnel).toHaveBeenCalledWith(matterId, 'proposta', expect.objectContaining({ expectedBoardVersion: 1, sourceColumnId: 'triagem_sdr' })));

    const requestButton = screen.getAllByText('Pedir')[0].closest('button');
    expect(requestButton).not.toBeNull();
    fireEvent.click(requestButton!);
    await waitFor(() => expect(createMatterTask).toHaveBeenCalledWith(matterId, expect.objectContaining({ title: 'Solicitar Negativa do plano', priority: 'alta' })));
  });
});

describe('prévia de proposta persistida', () => {
  it('não injeta honorários nem escopo padrão quando o dossiê está incompleto', async () => {
    vi.spyOn(klmApi, 'matter').mockResolvedValue(emptyMatter);
    vi.spyOn(klmApi, 'legalArtifacts').mockResolvedValue([proposalArtifact({
      clientName: emptyMatter.client.name,
      demandLabel: emptyMatter.demandType,
      objectName: null,
      denialReason: null,
      executiveSummary: null,
      strategyNotes: null,
      nextAction: null,
      financialConditions: {},
      institutionalEvidence: {},
    })]);

    renderWithQuery(
      <Routes><Route path="/proposta/:matterId" element={<ProposalPreviewPage />} /></Routes>,
      `/proposta/${matterId}`,
    );

    expect(await screen.findByTestId('proposal-preview')).toBeInTheDocument();
    expect(screen.getByText('v1 · draft')).toBeInTheDocument();
    expect(screen.getByText('Honorários ainda não definidos no dossiê. Nenhum valor padrão foi aplicado.')).toBeInTheDocument();
    expect(screen.queryByText('R$ 5.500,00')).not.toBeInTheDocument();
    expect(screen.queryByText(/20% sobre/)).not.toBeInTheDocument();
    expect(screen.queryByText('Revisão da negativa e dos documentos médicos')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Revisar dossiê' })).toBeEnabled();
  });

  it('exibe exatamente as condições e o conteúdo recebidos do dossiê', async () => {
    vi.spyOn(klmApi, 'matter').mockResolvedValue({
      ...emptyMatter,
      executiveSummary: 'Resumo efetivamente salvo no caso.',
      strategyNotes: 'Estratégia efetivamente salva no caso.',
      nextAction: 'Próximo passo efetivamente salvo no caso.',
      honorariumEstimate: {
        contractType: 'initial_plus_success',
        fixedFee: 7200,
        quotaLitisPercentage: 18,
        expectedExpenses: null,
        totalEstimatedValue: 25200,
        notes: 'Condições revisadas por humano.',
      },
      revenueProjection: {
        estimatedClaimValue: 100000,
        projectedRevenue: 25200,
        successProbability: 0.8,
        confidenceLevel: 0.7,
        notes: 'Projeção persistida.',
      },
    });
    vi.spyOn(klmApi, 'legalArtifacts').mockResolvedValue([proposalArtifact({
      clientName: emptyMatter.client.name,
      demandLabel: emptyMatter.demandType,
      objectName: 'tratamento indicado',
      denialReason: 'Negativa persistida.',
      executiveSummary: 'Resumo efetivamente salvo no caso.',
      strategyNotes: 'Estratégia efetivamente salva no caso.',
      nextAction: 'Próximo passo efetivamente salvo no caso.',
      financialConditions: {
        fixedFee: 7200,
        successPercentage: 18,
        projectedRevenue: 25200,
        reviewStatus: 'aprovado',
      },
      institutionalEvidence: {},
    }, 'approved')]);

    renderWithQuery(
      <Routes><Route path="/proposta/:matterId" element={<ProposalPreviewPage />} /></Routes>,
      `/proposta/${matterId}`,
    );

    expect(await screen.findByText('R$ 7.200,00')).toBeInTheDocument();
    expect(screen.getByText('18% sobre a base contratual revisada')).toBeInTheDocument();
    expect(screen.getByText('Resumo efetivamente salvo no caso.')).toBeInTheDocument();
    expect(screen.getByText('Estratégia efetivamente salva no caso.')).toBeInTheDocument();
    expect(screen.getByText('R$ 25.200,00')).toBeInTheDocument();
  });
});
