import { beforeEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import { fetchWorkspaceAiCosts, saveWorkspaceAiCostPolicy } from '@/services/admin';
import {
  decidePlatformWorkspaceAiTokenPackageRequest,
  fetchAiOperationsEvents,
  fetchPlatformAiCosts,
  fetchPlatformWorkspaceAiTokenPackageRequests,
  revokePlatformWorkspaceAiTokenPackageRequest,
  savePlatformWorkspaceAiCostPolicy,
} from '@/services/platform';

describe('AI cost service contracts', () => {
  beforeEach(() => {
    http.apiFetch.mockReset();
    http.apiFetch.mockResolvedValue({});
  });

  it('usa o escopo do workspace ativo para leitura e política', async () => {
    await fetchWorkspaceAiCosts();
    await saveWorkspaceAiCostPolicy({ monthlyLimit: 500_000, alertAtPct: 75, hardLimit: true });

    expect(http.apiFetch).toHaveBeenNthCalledWith(1, '/api/v1/workspaces/ai-costs');
    expect(http.apiFetch).toHaveBeenNthCalledWith(2, '/api/v1/workspaces/ai-costs', {
      method: 'PATCH',
      body: JSON.stringify({ monthlyLimit: 500_000, alertAtPct: 75, hardLimit: true }),
    });
  });

  it('permite ao painel SaaS consultar o agregado e administrar uma conta explícita', async () => {
    await fetchPlatformAiCosts(30);
    await fetchPlatformAiCosts(90, 'workspace/1');
    await savePlatformWorkspaceAiCostPolicy('workspace/1', { monthlyLimit: null, alertAtPct: 80, hardLimit: false });

    expect(http.apiFetch).toHaveBeenNthCalledWith(1, '/api/v1/platform/ai-costs?days=30');
    expect(http.apiFetch).toHaveBeenNthCalledWith(2, '/api/v1/platform/ai-costs?days=90&workspaceId=workspace%2F1');
    expect(http.apiFetch).toHaveBeenNthCalledWith(3, '/api/v1/platform/ai-costs/workspace%2F1', {
      method: 'PATCH',
      body: JSON.stringify({ monthlyLimit: null, alertAtPct: 80, hardLimit: false }),
    });
  });

  it('pagina solicitações de pacotes e envia motivo nas decisões administrativas', async () => {
    await fetchPlatformWorkspaceAiTokenPackageRequests('workspace/1', {
      limit: 10,
      offset: 20,
      status: 'requested',
    });
    await decidePlatformWorkspaceAiTokenPackageRequest(
      'workspace/1',
      'request/1',
      'approved',
      'Crédito validado pelo suporte.',
    );
    await revokePlatformWorkspaceAiTokenPackageRequest(
      'workspace/1',
      'request/1',
      'Revogação solicitada pelo responsável.',
    );

    expect(http.apiFetch).toHaveBeenNthCalledWith(
      1,
      '/api/v1/platform/workspaces/workspace%2F1/ai-token-package-requests/page?limit=10&offset=20&status=requested',
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      2,
      '/api/v1/platform/workspaces/workspace%2F1/ai-token-package-requests/request%2F1/decision',
      {
        method: 'POST',
        body: JSON.stringify({
          decision: 'approved',
          decisionNote: 'Crédito validado pelo suporte.',
        }),
      },
    );
    expect(http.apiFetch).toHaveBeenNthCalledWith(
      3,
      '/api/v1/platform/workspaces/workspace%2F1/ai-token-package-requests/request%2F1/revoke',
      {
        method: 'POST',
        body: JSON.stringify({
          decisionNote: 'Revogação solicitada pelo responsável.',
        }),
      },
    );
  });

  it('transporta offset e filtro na paginação de eventos de IA', async () => {
    await fetchAiOperationsEvents({ days: 30, limit: 12, offset: 24, onlyFallbacks: true });

    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/platform/ai-operations/events?days=30&limit=12&offset=24&only_fallbacks=true',
    );
  });
});
