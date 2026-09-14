/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { FinanceHarmonizationCard } from '@/klm/components/ReconciliationPanel';
import { klmApi, type KlmFinanceHarmonizationRunDetail } from '@/klm/api';

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

const RUN_FIXTURE: KlmFinanceHarmonizationRunDetail = {
  id: 'run-1',
  workspaceId: 'ws-1',
  sourceDocumentName: 'extrato-julho.csv',
  sourceType: 'csv',
  periodStart: '2026-07-01',
  periodEnd: '2026-07-31',
  competence: '2026-07',
  status: 'aguardando_validacao',
  totals: { batem: 1, naoBatem: 1, soNoDocumento: 1, soNoSistema: 1 },
  parserWarning: null,
  createdBy: 'user-1',
  decidedBy: null,
  decidedAt: null,
  createdAt: '2026-07-25T12:00:00Z',
  items: [
    {
      id: 'item-exato',
      runId: 'run-1',
      docDate: '2026-07-02',
      docValue: 250,
      docDescription: 'Aluguel escritorio',
      matchedSource: 'finance_entry',
      matchedEntryId: 'entry-1',
      matchKind: 'exato',
      deltaValue: 0,
      deltaDays: 0,
      decision: 'pendente',
      decidedBy: null,
      decidedAt: null,
      createdAt: '2026-07-25T12:00:00Z',
    },
    {
      id: 'item-aproximado',
      runId: 'run-1',
      docDate: '2026-07-04',
      docValue: 250,
      docDescription: 'Aluguel escritorio (2 dias de diferenca)',
      matchedSource: 'finance_entry',
      matchedEntryId: 'entry-3',
      matchKind: 'aproximado',
      deltaValue: 0,
      deltaDays: 2,
      decision: 'pendente',
      decidedBy: null,
      decidedAt: null,
      createdAt: '2026-07-25T12:00:00Z',
    },
    {
      id: 'item-so-doc',
      runId: 'run-1',
      docDate: '2026-07-10',
      docValue: 999,
      docDescription: 'Transacao desconhecida',
      matchedSource: null,
      matchedEntryId: null,
      matchKind: 'so_no_documento',
      deltaValue: null,
      deltaDays: null,
      decision: 'pendente',
      decidedBy: null,
      decidedAt: null,
      createdAt: '2026-07-25T12:00:00Z',
    },
    {
      id: 'item-so-sistema',
      runId: 'run-1',
      docDate: null,
      docValue: null,
      docDescription: null,
      matchedSource: 'finance_entry',
      matchedEntryId: 'entry-2',
      matchKind: 'so_no_sistema',
      deltaValue: null,
      deltaDays: null,
      decision: 'pendente',
      decidedBy: null,
      decidedAt: null,
      createdAt: '2026-07-25T12:00:00Z',
    },
  ],
};

describe('FinanceHarmonizationCard (F14)', () => {
  it('shows the quota counter and the four comparative groups', async () => {
    vi.spyOn(klmApi, 'financeHarmonizationQuota').mockResolvedValue({
      competence: '2026-07',
      used: 1,
      limit: 3,
      remaining: 2,
      renewsAt: '2026-08-01',
    });
    vi.spyOn(klmApi, 'financeHarmonizationRuns').mockResolvedValue([
      {
        id: RUN_FIXTURE.id,
        workspaceId: RUN_FIXTURE.workspaceId,
        sourceDocumentName: RUN_FIXTURE.sourceDocumentName,
        sourceType: RUN_FIXTURE.sourceType,
        periodStart: RUN_FIXTURE.periodStart,
        periodEnd: RUN_FIXTURE.periodEnd,
        competence: RUN_FIXTURE.competence,
        status: RUN_FIXTURE.status,
        totals: RUN_FIXTURE.totals,
        parserWarning: RUN_FIXTURE.parserWarning,
        createdBy: RUN_FIXTURE.createdBy,
        decidedBy: RUN_FIXTURE.decidedBy,
        decidedAt: RUN_FIXTURE.decidedAt,
        createdAt: RUN_FIXTURE.createdAt,
      },
    ]);
    vi.spyOn(klmApi, 'financeHarmonizationRun').mockResolvedValue(RUN_FIXTURE);

    renderWithClient(<FinanceHarmonizationCard canManage />);

    expect(await screen.findByText('Verificação completa do mês')).toBeInTheDocument();
    expect(await screen.findByText('1/3 usadas este mês')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('Aluguel escritorio')).toBeInTheDocument();
    });
    expect(screen.getByText('Bate')).toBeInTheDocument();
    expect(screen.getByText('Não bate exato')).toBeInTheDocument();
    expect(screen.getByText('Só no documento')).toBeInTheDocument();
    expect(screen.getByText('Só no sistema')).toBeInTheDocument();
    expect(screen.getByText('Transacao desconhecida')).toBeInTheDocument();
  });

  it('disables the upload button once the monthly quota is exhausted', async () => {
    vi.spyOn(klmApi, 'financeHarmonizationQuota').mockResolvedValue({
      competence: '2026-07',
      used: 3,
      limit: 3,
      remaining: 0,
      renewsAt: '2026-08-01',
    });
    vi.spyOn(klmApi, 'financeHarmonizationRuns').mockResolvedValue([]);

    renderWithClient(<FinanceHarmonizationCard canManage />);

    expect(await screen.findByText('3/3 usadas este mês')).toBeInTheDocument();
    const uploadButton = await screen.findByRole('button', { name: /enviar documento/i });
    expect(uploadButton).toBeDisabled();
  });

  it('confirming a comparative line calls the decision endpoint exactly once', async () => {
    vi.spyOn(klmApi, 'financeHarmonizationQuota').mockResolvedValue({
      competence: '2026-07',
      used: 1,
      limit: 3,
      remaining: 2,
      renewsAt: '2026-08-01',
    });
    vi.spyOn(klmApi, 'financeHarmonizationRuns').mockResolvedValue([
      {
        id: RUN_FIXTURE.id,
        workspaceId: RUN_FIXTURE.workspaceId,
        sourceDocumentName: RUN_FIXTURE.sourceDocumentName,
        sourceType: RUN_FIXTURE.sourceType,
        periodStart: RUN_FIXTURE.periodStart,
        periodEnd: RUN_FIXTURE.periodEnd,
        competence: RUN_FIXTURE.competence,
        status: RUN_FIXTURE.status,
        totals: RUN_FIXTURE.totals,
        parserWarning: RUN_FIXTURE.parserWarning,
        createdBy: RUN_FIXTURE.createdBy,
        decidedBy: RUN_FIXTURE.decidedBy,
        decidedAt: RUN_FIXTURE.decidedAt,
        createdAt: RUN_FIXTURE.createdAt,
      },
    ]);
    vi.spyOn(klmApi, 'financeHarmonizationRun').mockResolvedValue(RUN_FIXTURE);
    const decide = vi.spyOn(klmApi, 'financeHarmonizationDecideItem').mockResolvedValue({
      ...RUN_FIXTURE.items[0],
      decision: 'confirmado',
    });

    renderWithClient(<FinanceHarmonizationCard canManage />);

    await waitFor(() => {
      expect(screen.getByText('Aluguel escritorio')).toBeInTheDocument();
    });
    const row = screen.getByText('Aluguel escritorio').closest('tr');
    expect(row).not.toBeNull();
    const confirmButton = within(row as HTMLElement).getByRole('button', { name: /confirmar/i });
    confirmButton.click();

    await waitFor(() => {
      expect(decide).toHaveBeenCalledTimes(1);
      expect(decide).toHaveBeenCalledWith('run-1', 'item-exato', 'confirmado');
    });
  });
});
