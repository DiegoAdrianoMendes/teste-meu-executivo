/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { ContractBillingProposalsPanel } from '@/klm/components/ContractBillingProposalsPanel';
import { klmApi, type KlmContractBillingProposal, type KlmFunnel } from '@/klm/api';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const renderWithClient = (ui: ReactElement) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
};

const FUNNEL_FIXTURE: KlmFunnel = {
  boardVersion: 1,
  stages: [],
  metrics: {},
  cards: [
    {
      id: 'matter-1',
      clientId: 'client-1',
      name: 'Caso Contrato F11',
      subject: 'Cobrança',
      object: 'Ação de cobrança',
      source: 'site',
      owner: 'user-1',
      probability: 60,
      initialFee: 0,
      projectedRevenue: 0,
      slaDueAt: null,
      documents: 0,
      documentScore: 0,
      stage: 'negociacao',
      microStatus: 'ativo',
    },
  ],
};

const DRAFT_PROPOSAL_FIXTURE: KlmContractBillingProposal = {
  id: 'proposal-1',
  workspaceId: 'ws-1',
  matterId: 'matter-1',
  sourceDocumentName: 'contrato.pdf',
  status: 'draft',
  extractionModel: 'fake-extractor-v1',
  approvalId: null,
  decidedAt: null,
  decidedBy: null,
  createdAt: '2026-07-26T00:00:00Z',
  updatedAt: '2026-07-26T00:00:00Z',
  items: [
    {
      kind: 'fixo',
      valor: 4000,
      percentual: null,
      vencimento: '2026-08-01',
      ciclo: null,
      descricao: null,
      confidence: 0.95,
      needsReview: false,
    },
    {
      kind: 'exito',
      valor: null,
      percentual: 20,
      vencimento: null,
      ciclo: null,
      descricao: 'Percentual não confirmado no texto',
      confidence: 0.4,
      needsReview: true,
    },
  ],
};

describe('ContractBillingProposalsPanel (F11)', () => {
  it('renders the extraction form', async () => {
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(FUNNEL_FIXTURE);

    renderWithClient(<ContractBillingProposalsPanel canManage />);

    expect(await screen.findByText('Propostas de contrato')).toBeInTheDocument();
    expect(screen.getByLabelText(/texto do contrato/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /extrair plano de cobrança/i })).toBeDisabled();
  });

  it('extracting a plan calls the endpoint and shows the draft proposal with needs-review items', async () => {
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(FUNNEL_FIXTURE);
    const extract = vi.spyOn(klmApi, 'extractContractBillingPlan').mockResolvedValue(DRAFT_PROPOSAL_FIXTURE);

    renderWithClient(<ContractBillingProposalsPanel canManage />);

    const matterTrigger = await screen.findByRole('combobox');
    fireEvent.click(matterTrigger);
    const option = await screen.findByRole('option', { name: /Caso Contrato F11/i }, { timeout: 3000 });
    fireEvent.click(option);

    const textarea = screen.getByLabelText(/texto do contrato/i);
    fireEvent.change(textarea, { target: { value: 'Cláusula de honorários: R$4.000,00 fixo mais 20% de êxito.' } });

    const submitButton = screen.getByRole('button', { name: /extrair plano de cobrança/i });
    await waitFor(() => expect(submitButton).not.toBeDisabled());
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(extract).toHaveBeenCalledTimes(1);
      expect(extract).toHaveBeenCalledWith(expect.objectContaining({ matterId: 'matter-1' }));
    });

    expect(await screen.findByText('Rascunho')).toBeInTheDocument();
    expect(screen.getByText('Revisar')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /enviar para aprovação/i })).toBeInTheDocument();
  });
});
