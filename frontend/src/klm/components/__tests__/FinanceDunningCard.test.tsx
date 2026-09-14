/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { FinanceDunningCard } from '@/klm/components/FinanceDunningCard';
import { klmApi, type KlmFinanceDunningLog, type KlmFinanceDunningSettings } from '@/klm/api';

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

const CONFIG_FIXTURE: KlmFinanceDunningSettings = {
  steps: {
    d1_lembrete: { enabled: true, offsetDays: 1, requiresApproval: false, messageTemplate: 'Lembrete {clientName}' },
    d7_segunda_via: { enabled: true, offsetDays: 7, requiresApproval: false, messageTemplate: 'Segunda via {clientName}' },
    d15_formal: { enabled: true, offsetDays: 15, requiresApproval: true, messageTemplate: 'Formal {clientName}' },
  },
};

const PENDING_LOG_FIXTURE: KlmFinanceDunningLog = {
  id: 'log-pending-1',
  workspaceId: 'ws-1',
  chargeId: 'charge-1',
  step: 'd15_formal',
  status: 'pending_approval',
  reasonCode: null,
  messagePreview: 'Formal Fulano de Tal',
  viewedBeforeDispatch: false,
  requiresApproval: true,
  approvedByUserId: null,
  approvedAt: null,
  sentAt: null,
  ownerNotifiedAt: null,
  createdAt: '2026-07-20T12:00:00Z',
  updatedAt: '2026-07-20T12:00:00Z',
  entryTitle: 'Honorários — Caso Fulano',
  amount: 1500,
  dueDate: '2026-07-05T00:00:00Z',
};

const SENT_LOG_FIXTURE: KlmFinanceDunningLog = {
  ...PENDING_LOG_FIXTURE,
  id: 'log-sent-1',
  step: 'd1_lembrete',
  status: 'sent',
  requiresApproval: false,
  messagePreview: 'Lembrete Fulano de Tal',
};

function mockLogEndpoint() {
  vi.spyOn(klmApi, 'financeDunningLog').mockImplementation(async (params = {}) => {
    if (params.status === 'pending_approval') return [PENDING_LOG_FIXTURE];
    return [SENT_LOG_FIXTURE, PENDING_LOG_FIXTURE];
  });
}

describe('FinanceDunningCard (F4)', () => {
  it('renders the three steps and the recent dispatch history', async () => {
    vi.spyOn(klmApi, 'financeDunningConfig').mockResolvedValue(CONFIG_FIXTURE);
    mockLogEndpoint();

    renderWithClient(<FinanceDunningCard canManage />);

    expect(await screen.findByText('Régua de cobrança')).toBeInTheDocument();
    expect(screen.getAllByText('D+1 · Lembrete').length).toBeGreaterThan(0);
    expect(screen.getByText('D+7 · Segunda via')).toBeInTheDocument();
    expect(screen.getAllByText('D+15 · Formal').length).toBeGreaterThan(0);

    await waitFor(() => {
      expect(screen.getAllByText('Honorários — Caso Fulano').length).toBeGreaterThan(0);
    });
  });

  it('saves the dunning config with the d15 approval flag always forced true', async () => {
    vi.spyOn(klmApi, 'financeDunningConfig').mockResolvedValue(CONFIG_FIXTURE);
    mockLogEndpoint();
    const update = vi.spyOn(klmApi, 'updateFinanceDunningConfig').mockResolvedValue(CONFIG_FIXTURE);

    renderWithClient(<FinanceDunningCard canManage />);

    const saveButton = await screen.findByRole('button', { name: /salvar régua/i });
    saveButton.click();

    await waitFor(() => {
      expect(update).toHaveBeenCalledTimes(1);
    });
    const [steps] = update.mock.calls[0];
    expect(steps.d15_formal.requiresApproval).toBe(true);
  });

  it('approves a pending step exactly once and refreshes the queue', async () => {
    vi.spyOn(klmApi, 'financeDunningConfig').mockResolvedValue(CONFIG_FIXTURE);
    mockLogEndpoint();
    const approve = vi.spyOn(klmApi, 'approveFinanceDunningLog').mockResolvedValue({
      ...PENDING_LOG_FIXTURE,
      status: 'sent',
    });

    renderWithClient(<FinanceDunningCard canManage />);

    await waitFor(() => {
      expect(screen.getByText('1 pendente')).toBeInTheDocument();
    });
    const approveButton = await screen.findByRole('button', { name: /aprovar/i });
    approveButton.click();

    await waitFor(() => {
      expect(approve).toHaveBeenCalledTimes(1);
      expect(approve).toHaveBeenCalledWith('log-pending-1');
    });
  });

  it('hides the approve action and the save button when the viewer cannot manage finance', async () => {
    vi.spyOn(klmApi, 'financeDunningConfig').mockResolvedValue(CONFIG_FIXTURE);
    mockLogEndpoint();

    renderWithClient(<FinanceDunningCard canManage={false} />);

    await waitFor(() => {
      expect(screen.getByText('1 pendente')).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: /salvar régua/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /aprovar/i })).not.toBeInTheDocument();
  });
});
