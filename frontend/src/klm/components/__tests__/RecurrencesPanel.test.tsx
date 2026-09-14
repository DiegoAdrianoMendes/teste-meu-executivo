/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { RecurrencesPanel } from '@/klm/components/RecurrencesPanel';
import { klmApi, type KlmFinanceRecurrence, type KlmFunnel } from '@/klm/api';

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
      name: 'Caso Teste Recorrência',
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

const ACTIVE_RECURRENCE: KlmFinanceRecurrence = {
  id: 'rec-1',
  workspaceId: 'ws-1',
  clientId: 'client-1',
  matterId: 'matter-1',
  entryTitle: 'Honorários mensais — retainer',
  entryDescription: null,
  entryValue: 1500,
  entryType: 'receivable',
  kind: 'geracao_programada',
  cycle: 'mensal',
  billingType: 'UNDEFINED',
  nextRun: '2026-08-01',
  endDate: null,
  maxCount: null,
  generatedCount: 3,
  status: 'ativa',
  gatewaySubscriptionId: null,
  createdAt: '2026-07-01T00:00:00Z',
  updatedAt: '2026-07-01T00:00:00Z',
};

const PAUSED_RECURRENCE: KlmFinanceRecurrence = {
  ...ACTIVE_RECURRENCE,
  id: 'rec-2',
  entryTitle: 'Assinatura gateway pausada',
  status: 'pausada',
  kind: 'assinatura_gateway',
};

describe('RecurrencesPanel (F3)', () => {
  it('lists recurrences with client name, cycle and status', async () => {
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(FUNNEL_FIXTURE);
    vi.spyOn(klmApi, 'financeRecurrences').mockResolvedValue([ACTIVE_RECURRENCE, PAUSED_RECURRENCE]);

    renderWithClient(<RecurrencesPanel canManage />);

    expect(await screen.findByText('Honorários mensais — retainer')).toBeInTheDocument();
    expect(screen.getByText('Assinatura gateway pausada')).toBeInTheDocument();
    expect(screen.getAllByText(/Caso Teste Recorrência/).length).toBeGreaterThan(0);
    expect(screen.getByText('Ativa')).toBeInTheDocument();
    expect(screen.getByText('Pausada')).toBeInTheDocument();
  });

  it('pauses an active recurrence', async () => {
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(FUNNEL_FIXTURE);
    vi.spyOn(klmApi, 'financeRecurrences').mockResolvedValue([ACTIVE_RECURRENCE]);
    const pause = vi.spyOn(klmApi, 'financeRecurrencePause').mockResolvedValue({ ...ACTIVE_RECURRENCE, status: 'pausada' });

    renderWithClient(<RecurrencesPanel canManage />);

    const row = (await screen.findByText('Honorários mensais — retainer')).closest('tr');
    expect(row).not.toBeNull();
    const pauseButton = within(row as HTMLElement).getByRole('button', { name: /pausar/i });
    pauseButton.click();

    await waitFor(() => {
      expect(pause).toHaveBeenCalledTimes(1);
      expect(pause).toHaveBeenCalledWith('rec-1');
    });
  });

  it('resumes a paused recurrence', async () => {
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(FUNNEL_FIXTURE);
    vi.spyOn(klmApi, 'financeRecurrences').mockResolvedValue([PAUSED_RECURRENCE]);
    const resume = vi.spyOn(klmApi, 'financeRecurrenceResume').mockResolvedValue({ ...PAUSED_RECURRENCE, status: 'ativa' });

    renderWithClient(<RecurrencesPanel canManage />);

    const row = (await screen.findByText('Assinatura gateway pausada')).closest('tr');
    expect(row).not.toBeNull();
    const resumeButton = within(row as HTMLElement).getByRole('button', { name: /retomar/i });
    resumeButton.click();

    await waitFor(() => {
      expect(resume).toHaveBeenCalledTimes(1);
      expect(resume).toHaveBeenCalledWith('rec-2');
    });
  });

  it('ends a recurrence', async () => {
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(FUNNEL_FIXTURE);
    vi.spyOn(klmApi, 'financeRecurrences').mockResolvedValue([ACTIVE_RECURRENCE]);
    const end = vi.spyOn(klmApi, 'financeRecurrenceEnd').mockResolvedValue({ ...ACTIVE_RECURRENCE, status: 'encerrada' });

    renderWithClient(<RecurrencesPanel canManage />);

    const row = (await screen.findByText('Honorários mensais — retainer')).closest('tr');
    expect(row).not.toBeNull();
    const endButton = within(row as HTMLElement).getByRole('button', { name: /encerrar/i });
    endButton.click();

    await waitFor(() => {
      expect(end).toHaveBeenCalledTimes(1);
      expect(end).toHaveBeenCalledWith('rec-1');
    });
  });

  it('disables actions when the user cannot manage finance', async () => {
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(FUNNEL_FIXTURE);
    vi.spyOn(klmApi, 'financeRecurrences').mockResolvedValue([ACTIVE_RECURRENCE]);

    renderWithClient(<RecurrencesPanel canManage={false} />);

    const row = (await screen.findByText('Honorários mensais — retainer')).closest('tr');
    expect(row).not.toBeNull();
    const pauseButton = within(row as HTMLElement).getByRole('button', { name: /pausar/i });
    expect(pauseButton).toBeDisabled();
  });
});
