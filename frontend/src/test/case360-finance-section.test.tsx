/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { CaseFinanceSection } from '@/klm/components/CaseFinanceSection';
import { klmApi, type KlmFeeArrangement, type KlmFinanceCharge, type KlmFinanceEntry, type KlmMatterDetail } from '@/klm/api';

const toastMock = vi.hoisted(() => vi.fn());
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }));

const matterId = '6f2a5c3e-1111-4a2b-9c3d-000000000001';

function matterDetail(): KlmMatterDetail {
  return {
    id: matterId,
    columnId: 'contrato',
    microStatus: 'contrato_enviado',
    demandType: 'negativa_plano_saude',
    leadSource: 'google_ads',
    tags: [],
    executiveSummary: null,
    healthSubtheme: null,
    healthSubthemeCustom: null,
    healthObjectName: null,
    healthInsurer: null,
    healthDenialReason: null,
    medicalUrgencyHours: null,
    economicValueAnnual: null,
    leadScore: 80,
    manualContextRevision: 0,
    manualContextUpdatedAt: null,
    nextAction: null,
    docScore: 0,
    productionScore: 0,
    claimValue: null,
    client: { id: 'client-1', name: 'Caso Financeiro', email: null, phone: null },
    documents: [],
    timeline: [],
    knowledgeChunks: [],
    revenueProjection: null,
    honorariumEstimate: null,
    campaignAttribution: null,
  };
}

function arrangement(overrides: Partial<KlmFeeArrangement> = {}): KlmFeeArrangement {
  return {
    id: 'arrangement-1',
    workspaceId: 'workspace-1',
    matterId,
    kind: 'exito',
    fixedValue: null,
    percent: 20,
    baseCalculoExito: null,
    hourlyRate: null,
    status: 'ativo',
    notes: null,
    createdAt: '2026-07-01T00:00:00Z',
    updatedAt: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

function financeEntry(overrides: Partial<KlmFinanceEntry> = {}): KlmFinanceEntry {
  return {
    id: 'entry-1',
    matterId,
    title: 'Entrada honorários',
    description: null,
    status: 'pending',
    amount: 5000,
    currency: 'BRL',
    dueDate: '2026-08-01',
    paidAt: null,
    provider: null,
    ...overrides,
  };
}

function renderSection(props: { canViewFinance?: boolean; canManageFinance?: boolean } = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <CaseFinanceSection
        matter={matterDetail()}
        canViewFinance={props.canViewFinance ?? true}
        canManageFinance={props.canManageFinance ?? true}
      />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  toastMock.mockReset();
});

describe('Case 360 — seção Financeiro do caso', () => {
  it('renders the active fee arrangement badge, the open charge and the Cobrar button', async () => {
    vi.spyOn(klmApi, 'feeArrangements').mockResolvedValue([arrangement()]);
    vi.spyOn(klmApi, 'financeEntries').mockResolvedValue([financeEntry()]);
    vi.spyOn(klmApi, 'listFinanceCharges').mockResolvedValue([]);

    renderSection();

    expect(await screen.findByTestId('case360-finance-section')).toBeInTheDocument();
    expect(await screen.findByText('Êxito')).toBeInTheDocument();
    expect(screen.getByText('Ativo')).toBeInTheDocument();
    expect(screen.getByText('Entrada honorários')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Cobrar/i })).toBeEnabled();
    expect(screen.getByRole('button', { name: /Registrar êxito/i })).toBeEnabled();
  });

  it('shows the charge status badge instead of the Cobrar button when a charge already exists', async () => {
    const charge: KlmFinanceCharge = {
      id: 'charge-1',
      workspaceId: 'workspace-1',
      entryId: 'entry-1',
      gateway: 'asaas',
      externalId: null,
      billingType: 'PIX',
      chargeKind: 'avulsa',
      status: 'criada',
      value: 5000,
      netValue: null,
      feeValue: null,
      dueDate: '2026-08-01',
      paidAt: null,
      invoiceUrl: 'https://pay.example/1',
      bankSlipUrl: null,
      pixPayload: null,
      externalReference: null,
      installmentRef: null,
      subscriptionRef: null,
      createdAt: '2026-07-01T00:00:00Z',
      updatedAt: '2026-07-01T00:00:00Z',
      draftNotice: null,
    };
    vi.spyOn(klmApi, 'feeArrangements').mockResolvedValue([]);
    vi.spyOn(klmApi, 'financeEntries').mockResolvedValue([financeEntry()]);
    vi.spyOn(klmApi, 'listFinanceCharges').mockResolvedValue([charge]);

    renderSection();

    expect(await screen.findByText(/Cobrança: Criada/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Cobrar$/i })).not.toBeInTheDocument();
  });

  it('does not query the finance endpoints and shows the access notice when the user lacks finance.view', () => {
    const feeArrangementsSpy = vi.spyOn(klmApi, 'feeArrangements').mockResolvedValue([]);
    const financeEntriesSpy = vi.spyOn(klmApi, 'financeEntries').mockResolvedValue([]);

    renderSection({ canViewFinance: false });

    expect(screen.getByText('Seu perfil não possui acesso ao financeiro.')).toBeInTheDocument();
    expect(feeArrangementsSpy).not.toHaveBeenCalled();
    expect(financeEntriesSpy).not.toHaveBeenCalled();
  });
});
