/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactElement } from 'react';

import FunnelPage from '@/klm/pages/FunnelPage';
import { klmApi, type KlmApproval, type KlmFinanceEntry } from '@/klm/api';
import * as appStore from '@/store/app-context';

const renderKlm = (ui: ReactElement, initialEntries = ['/app/funil']) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
};

function installPointerCaptureForRadix() {
  Object.defineProperty(HTMLElement.prototype, 'hasPointerCapture', { configurable: true, value: () => false });
  Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', { configurable: true, value: () => undefined });
  Object.defineProperty(HTMLElement.prototype, 'releasePointerCapture', { configurable: true, value: () => undefined });
}

function mockBottleFunnelContext() {
  vi.spyOn(klmApi, 'nativeCRMFunnelContext').mockResolvedValue({
    projection: { linked: false, totalRecords: 0, availableRecords: 0, failedRecords: 0, staleRecords: 0, byEntityType: { contacts: 0, leads: 0, opportunities: 0, cases: 0, tasks: 0, financeEntries: 0 }, updatedAt: null, externalExecutionEnabled: false },
    counts: { accounts: 0, contacts: 0, leads: 0, opportunities: 0 }, urgentCounts: { overdueTasks: 0, followupsToday: 0, hotLeads: 0 }, pipeline: [], revenue: { totalPipeline: 0, weightedPipeline: 0, conversionRate: 0, currency: 'BRL' }, opportunityColumns: [], leadColumns: [], caseColumns: [], goals: [], agingConfig: [], notificationsUnread: 0, sensitiveDataHidden: false,
  });
}

const stages = [
  { id: 'novo_lead', label: 'Novo lead' },
  { id: 'contrato', label: 'Contrato' },
  { id: 'pagamento', label: 'Pagamento' },
  { id: 'ganho', label: 'Fechado' },
];

function matterCard(overrides: Record<string, unknown> = {}) {
  return {
    id: 'matter-billing-1',
    clientId: 'client-billing-1',
    name: 'Caso Financeiro Kanban',
    subject: 'Plano de saúde',
    object: 'Cobertura de medicamento',
    source: 'google_ads',
    owner: 'Advogado A',
    probability: 80,
    initialFee: 5000,
    projectedRevenue: 5000,
    slaDueAt: null,
    documents: 1,
    documentScore: 60,
    stage: 'pagamento',
    microStatus: 'pagamento_pendente',
    cardOrder: 0,
    ...overrides,
  };
}

function funnelFixture(card: Record<string, unknown>) {
  return {
    boardVersion: 1,
    stages: stages.map((stage) => ({ ...stage, count: card.stage === stage.id ? 1 : 0 })),
    metrics: {},
    cards: [card],
  };
}

function mockFunnelQueries(card: Record<string, unknown>, options: { entries?: KlmFinanceEntry[]; approvals?: KlmApproval[] } = {}) {
  vi.spyOn(klmApi, 'funnel').mockResolvedValue(funnelFixture(card) as Awaited<ReturnType<typeof klmApi.funnel>>);
  vi.spyOn(klmApi, 'financeEntries').mockResolvedValue(options.entries ?? []);
  vi.spyOn(klmApi, 'listFinanceCharges').mockResolvedValue([]);
  vi.spyOn(klmApi, 'feeArrangements').mockResolvedValue([]);
  vi.spyOn(klmApi, 'approvals').mockResolvedValue(options.approvals ?? []);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('Reação financeira do Kanban (Frente C)', () => {
  it('normalizes the quick-lead phone before persisting it in a campaign funnel', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => true } as unknown as ReturnType<typeof appStore.useAppStore>);
    installPointerCaptureForRadix();
    const card = matterCard();
    mockFunnelQueries(card);
    mockBottleFunnelContext();
    const createClient = vi.spyOn(klmApi, 'createClient').mockResolvedValue({ id: 'client-new' } as Awaited<ReturnType<typeof klmApi.createClient>>);
    vi.spyOn(klmApi, 'createMatter').mockResolvedValue({ id: 'matter-new' } as Awaited<ReturnType<typeof klmApi.createMatter>>);

    renderKlm(<FunnelPage />);

    fireEvent.click((await screen.findAllByRole('button', { name: 'Adicionar lead' }))[0]);
    const dialog = await screen.findByRole('dialog', { name: 'Novo lead' });
    fireEvent.change(within(dialog).getByLabelText('Nome'), { target: { value: 'Maria' } });
    fireEvent.change(within(dialog).getByLabelText('Telefone'), { target: { value: '(61) 99999-9999' } });
    fireEvent.change(within(dialog).getByLabelText('Assunto inicial'), { target: { value: 'Atendimento' } });
    fireEvent.blur(within(dialog).getByLabelText('Telefone'));
    expect(within(dialog).getByLabelText('Telefone')).toHaveValue('5561999999999');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Criar lead' }));

    await waitFor(() => expect(createClient).toHaveBeenCalledWith({
      name: 'Maria',
      phone: '5561999999999',
    }));
  });

  it('shows the "Criar cobrança/arranjo" CTA on a payment-stage card with no financial structure', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => true } as unknown as ReturnType<typeof appStore.useAppStore>);
    installPointerCaptureForRadix();
    const card = matterCard();
    mockFunnelQueries(card);
    mockBottleFunnelContext();

    renderKlm(<FunnelPage />);

    const reaction = await screen.findByTestId(`funnel-finance-reaction-${card.id}`);
    expect(reaction).toHaveTextContent('Criar cobrança/arranjo');
    expect(reaction).not.toHaveTextContent('aguardando aprovação do financeiro');

    fireEvent.click(screen.getByRole('button', { name: /Criar cobrança\/arranjo/i }));
    const dialog = await screen.findByRole('dialog', { name: /Criar cobrança \/ arranjo/i });
    expect(within(dialog).getByText(new RegExp(card.name as string))).toBeInTheDocument();
  });

  it('shows the pending-approval notice instead of the CTA when a contract billing proposal is pending', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => true } as unknown as ReturnType<typeof appStore.useAppStore>);
    installPointerCaptureForRadix();
    const card = matterCard();
    const pendingApproval: KlmApproval = {
      id: 'approval-billing-1',
      cardId: card.id,
      clientName: card.name,
      type: 'contract_billing_proposal',
      title: 'Plano de cobrança extraído do contrato',
      description: null,
      priority: 'media',
      status: 'pending',
      requestedAt: '2026-07-24T12:00:00Z',
      resolutionReason: null,
    };
    mockFunnelQueries(card, { approvals: [pendingApproval] });
    mockBottleFunnelContext();

    renderKlm(<FunnelPage />);

    const reaction = await screen.findByTestId(`funnel-finance-reaction-${card.id}`);
    expect(reaction).toHaveTextContent('proposta aguardando aprovação do financeiro');
    expect(screen.queryByRole('button', { name: /Criar cobrança\/arranjo/i })).not.toBeInTheDocument();
  });

  it('does not render the financial reaction outside contrato/pagamento stages', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => true } as unknown as ReturnType<typeof appStore.useAppStore>);
    installPointerCaptureForRadix();
    const card = matterCard({ stage: 'novo_lead', microStatus: 'lead_novo' });
    mockFunnelQueries(card);
    mockBottleFunnelContext();

    renderKlm(<FunnelPage />);

    await screen.findByTestId(`funnel-card-${card.id}`);
    expect(screen.queryByTestId(`funnel-finance-reaction-${card.id}`)).not.toBeInTheDocument();
  });

  it('shows the discreet "Vencido" finance badge for an overdue entry in the payment stage', async () => {
    vi.spyOn(appStore, 'useAppStore').mockReturnValue({ can: () => true } as unknown as ReturnType<typeof appStore.useAppStore>);
    installPointerCaptureForRadix();
    const card = matterCard();
    const overdueEntry: KlmFinanceEntry = {
      id: 'entry-overdue-1',
      matterId: card.id,
      title: 'Entrada honorários',
      description: null,
      status: 'overdue',
      amount: 3000,
      currency: 'BRL',
      dueDate: '2026-06-01',
      paidAt: null,
      provider: null,
    };
    mockFunnelQueries(card, { entries: [overdueEntry] });
    mockBottleFunnelContext();

    renderKlm(<FunnelPage />);

    const reaction = await screen.findByTestId(`funnel-finance-reaction-${card.id}`);
    expect(reaction).toHaveTextContent('Vencido');
    expect(reaction).not.toHaveTextContent('Criar cobrança/arranjo');
  });
});
