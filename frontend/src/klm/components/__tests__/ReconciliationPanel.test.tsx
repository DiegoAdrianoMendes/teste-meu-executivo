/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { ReconciliationPanel } from '@/klm/components/ReconciliationPanel';
import { klmApi, type KlmPendingReconciliation } from '@/klm/api';

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

const DETERMINISTIC_PENDING: KlmPendingReconciliation = {
  transaction: {
    id: 'tx-1',
    workspaceId: 'ws-1',
    source: 'ofx',
    externalFingerprint: 'fp-1',
    date: '2026-07-10T00:00:00Z',
    value: 500,
    description: 'Pagamento cliente A',
    createdAt: '2026-07-10T00:00:00Z',
  },
  match: {
    id: 'match-1',
    workspaceId: 'ws-1',
    bankTransactionId: 'tx-1',
    targetKind: 'entry',
    targetId: 'entry-1',
    method: 'deterministico',
    confidence: 0.95,
    suggestedCategory: 'honorarios',
    suggestedTags: ['honorarios'],
    status: 'sugerido',
    confirmedBy: null,
    confirmedAt: null,
    createdAt: '2026-07-10T00:00:00Z',
  },
};

const IA_PENDING: KlmPendingReconciliation = {
  transaction: {
    id: 'tx-2',
    workspaceId: 'ws-1',
    source: 'csv',
    externalFingerprint: 'fp-2',
    date: '2026-07-11T00:00:00Z',
    value: 320,
    description: 'Pagamento cliente B',
    createdAt: '2026-07-11T00:00:00Z',
  },
  match: {
    id: 'match-2',
    workspaceId: 'ws-1',
    bankTransactionId: 'tx-2',
    targetKind: null,
    targetId: null,
    method: 'ia',
    confidence: 0.4,
    suggestedCategory: 'despesas',
    suggestedTags: [],
    status: 'sugerido',
    confirmedBy: null,
    confirmedAt: null,
    createdAt: '2026-07-11T00:00:00Z',
  },
};

describe('ReconciliationPanel (F7)', () => {
  it('imports an Asaas statement for the selected period', async () => {
    vi.spyOn(klmApi, 'reconciliationPending').mockResolvedValue([]);
    const importAsaas = vi.spyOn(klmApi, 'reconciliationImportAsaas').mockResolvedValue({
      imported: 4,
      duplicates: 1,
      matchedDeterministic: 2,
      suggested: 2,
    });

    renderWithClient(<ReconciliationPanel canManage />);

    const trigger = await screen.findByRole('button', { name: /importar extrato asaas/i });
    fireEvent.click(trigger);

    const confirm = await screen.findByRole('button', { name: /^importar$/i });
    fireEvent.click(confirm);

    await waitFor(() => {
      expect(importAsaas).toHaveBeenCalledTimes(1);
    });
    expect(importAsaas.mock.calls[0][0]).toEqual(
      expect.objectContaining({ start: expect.any(String), finish: expect.any(String) }),
    );
  });

  it('confirms selected deterministic matches in batch', async () => {
    vi.spyOn(klmApi, 'reconciliationPending').mockResolvedValue([DETERMINISTIC_PENDING]);
    const confirmBatch = vi.spyOn(klmApi, 'reconciliationConfirmMatchesBatch').mockResolvedValue([
      { ...DETERMINISTIC_PENDING.match, status: 'confirmado' },
    ]);

    renderWithClient(<ReconciliationPanel canManage />);

    const row = (await screen.findByText('Pagamento cliente A')).closest('tr');
    expect(row).not.toBeNull();
    const checkbox = within(row as HTMLElement).getByRole('checkbox');
    fireEvent.click(checkbox);

    const confirmSelected = await screen.findByRole('button', { name: /confirmar selecionados/i });
    expect(confirmSelected).toBeEnabled();
    fireEvent.click(confirmSelected);

    await waitFor(() => {
      expect(confirmBatch).toHaveBeenCalledTimes(1);
      expect(confirmBatch).toHaveBeenCalledWith([
        { matchId: 'match-1', targetKind: 'entry', targetId: 'entry-1', category: 'honorarios', tags: ['honorarios'] },
      ]);
    });
  });

  it('disables batch confirm when a selected match has no automatic target, but batch reject still works', async () => {
    vi.spyOn(klmApi, 'reconciliationPending').mockResolvedValue([IA_PENDING]);
    const rejectBatch = vi.spyOn(klmApi, 'reconciliationRejectMatchesBatch').mockResolvedValue([
      { ...IA_PENDING.match, status: 'rejeitado' },
    ]);

    renderWithClient(<ReconciliationPanel canManage />);

    const row = (await screen.findByText('Pagamento cliente B')).closest('tr');
    expect(row).not.toBeNull();
    const checkbox = within(row as HTMLElement).getByRole('checkbox');
    fireEvent.click(checkbox);

    const confirmSelected = await screen.findByRole('button', { name: /confirmar selecionados/i });
    expect(confirmSelected).toBeDisabled();

    const rejectSelected = await screen.findByRole('button', { name: /rejeitar selecionados/i });
    fireEvent.click(rejectSelected);

    await waitFor(() => {
      expect(rejectBatch).toHaveBeenCalledTimes(1);
      expect(rejectBatch).toHaveBeenCalledWith(['match-2']);
    });
  });
});
