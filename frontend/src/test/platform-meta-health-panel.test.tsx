/** @vitest-environment jsdom */
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { PlatformMetaHealthPanel } from '@/components/platform/PlatformMetaHealthPanel';

const platformApi = vi.hoisted(() => ({
  mrr: vi.fn(),
  tokenUsage: vi.fn(),
  awsCosts: vi.fn(),
  metaHealth: vi.fn(),
}));

vi.mock('@/services/platform', async () => {
  const actual = await vi.importActual<typeof import('@/services/platform')>('@/services/platform');
  return {
    ...actual,
    fetchPlatformMrr: platformApi.mrr,
    fetchPlatformTokenUsage: platformApi.tokenUsage,
    fetchPlatformAwsCosts: platformApi.awsCosts,
    fetchPlatformMetaHealth: platformApi.metaHealth,
  };
});

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <PlatformMetaHealthPanel enabled />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('PlatformMetaHealthPanel', () => {
  it('renders KPI cards and meta-health tables with seeded data', async () => {
    platformApi.mrr.mockResolvedValue({ total_mrr_cents: 500_000, total_arr_cents: 6_000_000 });
    platformApi.tokenUsage.mockResolvedValue({
      total_cost_usd: 42.5,
      total_runs: 120,
      total_tokens: 987_654,
    });
    platformApi.awsCosts.mockResolvedValue({ source: 'estimate', total_usd: null });
    platformApi.metaHealth.mockResolvedValue({
      paused_campaigns: [
        {
          campaign_id: 'c1',
          workspace_id: 'w1',
          title: 'Campanha pausada por política',
          paused_at: '2026-07-20T10:00:00Z',
          pause_reason: 'Kill switch automático: taxa de falha de 100% excedeu o limite.',
          provider_code: 368,
          paused_by_policy: true,
        },
      ],
      top_meta_errors: [
        { provider_code: '368', count: 2, sample_message: 'Conta com restrição de envio.' },
      ],
      funnel_last_24h: [{ event_name: 'LeadSubmitted', count: 1 }],
      funnel_last_7d: [
        { event_name: 'LeadSubmitted', count: 1 },
        { event_name: 'QualifiedLead', count: 1 },
      ],
      suppressions_by_reason: [{ reason: 'opt_out', count: 1 }],
      data_notes: ['pause_reason/paused_by_policy existem no schema mas ainda não são gravados pelo motor.'],
    });

    renderPanel();

    await waitFor(() => expect(screen.getByText('Campanha pausada por política')).toBeInTheDocument());

    expect(screen.getByText(/Kill switch automático: taxa de falha/)).toBeInTheDocument();
    expect(screen.getAllByText('368').length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('LeadSubmitted').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('QualifiedLead')).toBeInTheDocument();
    expect(screen.getByText('opt_out')).toBeInTheDocument();
    expect(screen.getByText(/ainda não são gravados pelo motor/)).toBeInTheDocument();
  });

  it('shows friendly empty states when nothing is pending', async () => {
    platformApi.mrr.mockResolvedValue({ total_mrr_cents: 0 });
    platformApi.tokenUsage.mockResolvedValue({ total_cost_usd: 0, total_runs: 0, total_tokens: 0 });
    platformApi.awsCosts.mockResolvedValue({ source: 'estimate', total_usd: null });
    platformApi.metaHealth.mockResolvedValue({
      paused_campaigns: [],
      top_meta_errors: [],
      funnel_last_24h: [],
      funnel_last_7d: [],
      suppressions_by_reason: [],
      data_notes: [],
    });

    renderPanel();

    await waitFor(() => expect(screen.getByText('Nenhuma campanha pausada')).toBeInTheDocument());
    expect(screen.getByText('Nenhum erro Meta recente')).toBeInTheDocument();
    expect(screen.getByText('Sem eventos nas últimas 24h')).toBeInTheDocument();
    expect(screen.getByText('Sem eventos nos últimos 7 dias')).toBeInTheDocument();
    expect(screen.getByText('Nenhuma supressão ativa')).toBeInTheDocument();
  });

  it('handles network errors gracefully without crashing', async () => {
    platformApi.mrr.mockRejectedValue(new Error('network down'));
    platformApi.tokenUsage.mockRejectedValue(new Error('network down'));
    platformApi.awsCosts.mockRejectedValue(new Error('network down'));
    platformApi.metaHealth.mockRejectedValue(new Error('network down'));

    renderPanel();

    await waitFor(() => expect(screen.getAllByText('Métrica indisponível.').length).toBeGreaterThan(0));
    await waitFor(() =>
      expect(
        screen.getByText('Não foi possível carregar a saúde Meta agora. Tente novamente em instantes.'),
      ).toBeInTheDocument(),
    );
  });
});
