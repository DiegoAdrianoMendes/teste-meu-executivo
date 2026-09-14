import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { PlatformAgentBillingReconciliationPanel } from '@/components/platform/PlatformAgentBillingReconciliationPanel';
import {
  fetchPlatformPendingBillingReconciliations,
  reconcilePlatformAgentRunBilling,
} from '@/services/platform';
import type {
  PlatformAgentBillingReconciliationPage,
  PlatformAgentBillingReconciliationRun,
} from '@/types';

vi.mock('@/services/platform', () => ({
  fetchPlatformPendingBillingReconciliations: vi.fn(),
  reconcilePlatformAgentRunBilling: vi.fn(),
}));
vi.mock('@/services/platform-search', () => ({
  lookupPlatformEntity: vi.fn(),
  resolvePlatformEntity: vi.fn().mockResolvedValue({
    entityType: 'accounts', state: 'ready', nextCursor: null, errorCode: null,
    items: [{ id: 'workspace-1', entityType: 'accounts', label: 'Escritório Alfa' }],
  }),
}));

const pendingRun: PlatformAgentBillingReconciliationRun = {
  id: 'run-1',
  workspaceId: 'workspace-1',
  matterId: 'matter-1',
  agentName: 'hermes-triagem',
  triggerSource: 'inbox',
  status: 'failed',
  errorMessage: 'timeout sanitizado',
  aiProvider: 'openai',
  modelUsed: 'model-test',
  inputTokens: 180,
  outputTokens: 90,
  outputPayload: {
    billingReconciliation: {
      status: 'reconciliation_required',
      reconciliationRequired: true,
      estimatedCostUsd: '0.0075',
      provider: 'openai',
      model: 'model-test',
      providerRequestId: 'provider-reconcile-42',
    },
  },
  createdAt: '2026-07-16T12:00:00Z',
  updatedAt: '2026-07-16T12:00:00Z',
};

function page(items: PlatformAgentBillingReconciliationRun[]): PlatformAgentBillingReconciliationPage {
  return { total: items.length, limit: 50, offset: 0, items };
}

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <PlatformAgentBillingReconciliationPanel
        defaultWorkspaceId="workspace-1"
        enabled
        canDecide
      />
    </QueryClientProvider>,
  );
}

describe('PlatformAgentBillingReconciliationPanel', () => {
  beforeEach(() => {
    vi.mocked(fetchPlatformPendingBillingReconciliations).mockReset();
    vi.mocked(reconcilePlatformAgentRunBilling).mockReset();
  });

  it('lista as execuções com cobrança incerta do workspace selecionado', async () => {
    vi.mocked(fetchPlatformPendingBillingReconciliations).mockResolvedValue(page([pendingRun]));

    renderPanel();

    expect(await screen.findByText('hermes-triagem')).toBeInTheDocument();
    expect(screen.getByText(/provider-reconcile-42/)).toBeInTheDocument();
    expect(screen.getByText(/Estimado: 0.0075/)).toBeInTheDocument();
    expect(vi.mocked(fetchPlatformPendingBillingReconciliations)).toHaveBeenCalledWith('workspace-1');
  });

  it('mostra estado vazio explícito quando não há cobrança pendente', async () => {
    vi.mocked(fetchPlatformPendingBillingReconciliations).mockResolvedValue(page([]));

    renderPanel();

    expect(await screen.findByTestId('billing-reconciliation-empty')).toHaveTextContent(
      'Nenhuma execução com cobrança incerta pendente neste workspace.',
    );
    expect(screen.queryByTestId('billing-reconciliation-row')).not.toBeInTheDocument();
  });

  it('registra a decisão de conciliação pelo serviço da plataforma', async () => {
    vi.mocked(fetchPlatformPendingBillingReconciliations).mockResolvedValue(page([pendingRun]));
    vi.mocked(reconcilePlatformAgentRunBilling).mockResolvedValue(pendingRun);

    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: 'Conciliar' }));

    fireEvent.change(screen.getByLabelText('Referência da evidência'), {
      target: { value: 'invoice-provider-2026-07' },
    });
    fireEvent.change(screen.getByLabelText('Justificativa'), {
      target: { value: 'Cobrança confirmada no extrato do provider.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Registrar decisão' }));

    await waitFor(() => {
      expect(vi.mocked(reconcilePlatformAgentRunBilling)).toHaveBeenCalledWith(
        'workspace-1',
        'run-1',
        {
          decision: 'charged',
          actualCostUsd: '0.0075',
          evidenceReference: 'invoice-provider-2026-07',
          reason: 'Cobrança confirmada no extrato do provider.',
          providerRequestId: 'provider-reconcile-42',
        },
      );
    });
  });

  it('não dispara a decisão sem evidência e justificativa', async () => {
    vi.mocked(fetchPlatformPendingBillingReconciliations).mockResolvedValue(page([pendingRun]));

    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: 'Conciliar' }));
    const submit = screen.getByRole('button', { name: 'Registrar decisão' });

    expect(submit).toBeDisabled();
    fireEvent.click(submit);
    expect(vi.mocked(reconcilePlatformAgentRunBilling)).not.toHaveBeenCalled();
  });
});
