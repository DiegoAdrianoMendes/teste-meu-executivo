/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { FeeArrangementsPanel } from '@/klm/components/FeeArrangementsPanel';
import { klmApi, type KlmFeeArrangement, type KlmFinanceCharge, type KlmFunnel } from '@/klm/api';

vi.mock('@/services/admin', () => ({
  fetchTeamMembers: vi.fn().mockResolvedValue([]),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
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
      name: 'Caso Teste Honorários',
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

const ARRANGEMENT_FIXTURE: KlmFeeArrangement = {
  id: 'arrangement-1',
  workspaceId: 'ws-1',
  matterId: 'matter-1',
  kind: 'fixo',
  fixedValue: 5000,
  percent: null,
  baseCalculoExito: null,
  hourlyRate: null,
  status: 'ativo',
  notes: null,
  createdAt: '2026-07-01T00:00:00Z',
  updatedAt: '2026-07-01T00:00:00Z',
};

const CHARGE_FIXTURE: KlmFinanceCharge = {
  id: 'charge-1',
  workspaceId: 'ws-1',
  entryId: 'entry-1',
  gateway: 'local',
  externalId: null,
  billingType: null,
  chargeKind: 'avulsa',
  status: 'rascunho',
  value: 5000,
  netValue: null,
  feeValue: null,
  dueDate: null,
  paidAt: null,
  invoiceUrl: null,
  bankSlipUrl: null,
  pixPayload: null,
  externalReference: null,
  installmentRef: null,
  subscriptionRef: null,
  createdAt: '2026-07-01T00:00:00Z',
  updatedAt: '2026-07-01T00:00:00Z',
  draftNotice: 'Gateway desligado.',
};

describe('FeeArrangementsPanel (F9)', () => {
  it('renders arrangements grouped with case name, kind and status', async () => {
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(FUNNEL_FIXTURE);
    vi.spyOn(klmApi, 'feeArrangements').mockResolvedValue([ARRANGEMENT_FIXTURE]);
    vi.spyOn(klmApi, 'financeEntries').mockResolvedValue([]);
    vi.spyOn(klmApi, 'approvals').mockResolvedValue([]);

    renderWithClient(<FeeArrangementsPanel canManage />);

    expect(await screen.findByText('Caso Teste Honorários')).toBeInTheDocument();
    expect(screen.getByText('Valor fixo')).toBeInTheDocument();
    expect(screen.getByText('Ativo')).toBeInTheDocument();
    expect(screen.getByText('R$ 5.000,00')).toBeInTheDocument();
  });

  it('generating a charge for a fixed arrangement calls the endpoint exactly once', async () => {
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(FUNNEL_FIXTURE);
    vi.spyOn(klmApi, 'feeArrangements').mockResolvedValue([ARRANGEMENT_FIXTURE]);
    vi.spyOn(klmApi, 'financeEntries').mockResolvedValue([]);
    vi.spyOn(klmApi, 'approvals').mockResolvedValue([]);
    const generate = vi.spyOn(klmApi, 'generateFeeArrangementCharge').mockResolvedValue({
      kind: 'fixo',
      entry: { id: 'entry-1', title: 'Honorário fixo', amount: 5000, dueDate: null, status: 'pending', externalRef: null },
      charge: CHARGE_FIXTURE,
    });

    renderWithClient(<FeeArrangementsPanel canManage />);

    const generateRowButton = await screen.findByRole('button', { name: /gerar cobrança/i });
    fireEvent.click(generateRowButton);

    const dialog = await screen.findByRole('dialog');
    const confirmButton = within(dialog).getByRole('button', { name: /gerar cobrança/i });
    fireEvent.click(confirmButton);

    await waitFor(() => {
      expect(generate).toHaveBeenCalledTimes(1);
      expect(generate).toHaveBeenCalledWith('arrangement-1', expect.objectContaining({ billingType: 'UNDEFINED' }));
    });
  });
});
