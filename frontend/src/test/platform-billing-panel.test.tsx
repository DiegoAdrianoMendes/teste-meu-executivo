/** @vitest-environment jsdom */
import { cleanup, render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { PlatformBillingPanel } from '@/components/platform/PlatformBillingPanel';

vi.mock('recharts', () => ({
  Line: () => null,
  LineChart: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  Tooltip: () => null,
  XAxis: () => null,
  YAxis: () => null,
}));

const platformApi = vi.hoisted(() => ({
  overview: vi.fn(),
  subscriptions: vi.fn(),
  workspace360: vi.fn(),
  subscriptionEvents: vi.fn(),
  people: vi.fn(),
  changePlan: vi.fn(),
}));

vi.mock('@/services/platform', async () => {
  const actual = await vi.importActual<typeof import('@/services/platform')>('@/services/platform');
  return {
    ...actual,
    fetchPlatformBillingOverview: platformApi.overview,
    fetchPlatformSubscriptions: platformApi.subscriptions,
    fetchWorkspace360: platformApi.workspace360,
    fetchPlatformSubscriptionEventsPage: platformApi.subscriptionEvents,
    fetchPlatformPeople: platformApi.people,
    changeSubscriptionPlan: platformApi.changePlan,
  };
});

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <PlatformBillingPanel enabled />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('PlatformBillingPanel', () => {
  it('renders MRR/ARR/churn/LTV cards and the subscriptions table with mock data', async () => {
    platformApi.overview.mockResolvedValue({
      mrr_cents: 49700,
      mrr_brl: 497,
      arr_brl: 5964,
      churn_rate_pct: 2.5,
      ltv_estimate_brl: 19880,
      active_count: 3,
      trial_count: 1,
      at_risk_count: 0,
      by_plan: [{ plano: 'escritorio', workspace_count: 1, mrr_brl: 497 }],
      series: [{ month: '2026-07', mrr_brl: 497 }],
    });
    platformApi.subscriptions.mockResolvedValue({
      items: [
        {
          id: 'sub-1',
          workspace_id: 'ws-1',
          workspace_name: 'Escritório Teste',
          plano: 'escritorio',
          status: 'ativa',
          mrr_cents: 49700,
          desconto_pct: 0,
          cortesia: false,
          trial_ends_at: null,
        },
      ],
      count: 1,
      limit: 100,
      offset: 0,
    });

    renderPanel();

    await waitFor(() => expect(screen.getByText('Escritório Teste')).toBeInTheDocument());
    expect(screen.getAllByText('MRR').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Churn (30d)')).toBeInTheDocument();
    expect(screen.getByText('LTV estimado')).toBeInTheDocument();
    expect(screen.getByText('Contas em risco')).toBeInTheDocument();
  });

  it('opens the change-plan dialog and requires a motivo before confirming', async () => {
    platformApi.overview.mockResolvedValue({
      mrr_cents: 0, mrr_brl: 0, arr_brl: 0, churn_rate_pct: 0, ltv_estimate_brl: 0,
      active_count: 0, trial_count: 0, at_risk_count: 0, by_plan: [], series: [],
    });
    platformApi.subscriptions.mockResolvedValue({
      items: [
        {
          id: 'sub-2',
          workspace_id: 'ws-2',
          workspace_name: 'Escritório Ação',
          plano: 'essencial',
          status: 'trial',
          mrr_cents: 19700,
          desconto_pct: 0,
          cortesia: false,
          trial_ends_at: '2026-08-01T00:00:00Z',
        },
      ],
      count: 1,
      limit: 100,
      offset: 0,
    });

    renderPanel();

    await waitFor(() => expect(screen.getByText('Escritório Ação')).toBeInTheDocument());
    fireEvent.click(screen.getAllByText('Mudar plano')[0]);

    await waitFor(() => expect(screen.getByText('Confirmar')).toBeInTheDocument());
    const confirmButton = screen.getByText('Confirmar') as HTMLButtonElement;
    expect(confirmButton.disabled).toBe(true);
  });

  it('shows an empty state when no subscriptions match the filters', async () => {
    platformApi.overview.mockResolvedValue({
      mrr_cents: 0, mrr_brl: 0, arr_brl: 0, churn_rate_pct: 0, ltv_estimate_brl: 0,
      active_count: 0, trial_count: 0, at_risk_count: 0, by_plan: [], series: [],
    });
    platformApi.subscriptions.mockResolvedValue({ items: [], count: 0, limit: 100, offset: 0 });

    renderPanel();

    await waitFor(() =>
      expect(screen.getByText('Nenhuma assinatura encontrada com esses filtros.')).toBeInTheDocument(),
    );
  });

  it('navigates subscriptions with server-side totals', async () => {
    platformApi.overview.mockResolvedValue({
      mrr_cents: 0, mrr_brl: 0, arr_brl: 0, churn_rate_pct: 0, ltv_estimate_brl: 0,
      active_count: 0, trial_count: 0, at_risk_count: 0, by_plan: [], series: [],
    });
    platformApi.subscriptions.mockImplementation(({ offset = 0 }: { offset?: number }) => Promise.resolve({
      items: [{
        id: `sub-${offset}`, workspace_id: `ws-${offset}`, workspace_name: `Conta ${offset + 1}`,
        plano: 'essencial', status: 'ativa', mrr_cents: 19700, desconto_pct: 0,
        cortesia: false, trial_ends_at: null,
      }],
      count: 55,
      total: 55,
      limit: 50,
      offset,
      hasMore: offset === 0,
    }));

    renderPanel();
    await screen.findByText('Conta 1');
    expect(screen.getByText('1–1 de 55')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Próxima' }));

    await waitFor(() => expect(platformApi.subscriptions).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 50, offset: 50 }),
    ));
    await screen.findByText('Conta 51');
    expect(screen.getByRole('button', { name: 'Próxima' })).toBeDisabled();
  });

  it('loads and navigates the complete subscription history from the paginated endpoint', async () => {
    platformApi.overview.mockResolvedValue({
      mrr_cents: 0, mrr_brl: 0, arr_brl: 0, churn_rate_pct: 0, ltv_estimate_brl: 0,
      active_count: 1, trial_count: 0, at_risk_count: 0, by_plan: [], series: [],
    });
    platformApi.subscriptions.mockResolvedValue({
      items: [{
        id: 'sub-history', workspace_id: 'ws-history', workspace_name: 'Conta Histórico',
        plano: 'essencial', status: 'ativa', mrr_cents: 19700, desconto_pct: 0,
        cortesia: false, trial_ends_at: null,
      }],
      count: 1, total: 1, limit: 50, offset: 0, hasMore: false,
    });
    platformApi.workspace360.mockResolvedValue({
      workspace_name: 'Conta Histórico',
      subscription: { plano: 'essencial', status: 'ativa' },
      pnl: { receita_mensal_brl: 197, margem_brl: 120, margem_pct: 60 },
      people: { items: [] },
      recent_events: [{ id: 'legacy-event', event_type: 'nao_deve_aparecer' }],
    });
    platformApi.subscriptionEvents.mockImplementation((
      _workspaceId: string,
      limit: number,
      offset: number,
    ) => Promise.resolve({
      items: [{
        id: `event-${offset}`,
        eventType: offset === 0 ? 'plano_alterado' : 'trial_estendido',
        actorLabel: 'Super Admin',
        motivo: 'Motivo administrativo completo.',
        occurredAt: '2026-07-29T12:00:00Z',
        before: { plan: 'essencial' },
        after: { plan: offset === 0 ? 'escritorio' : 'performance' },
      }],
      total: 11,
      limit,
      offset,
      hasMore: offset === 0,
    }));
    platformApi.people.mockImplementation(({ offset = 0 }: { offset?: number }) => Promise.resolve({
      items: [{
        user_id: `user-${offset}`,
        name: offset === 0 ? 'Pessoa da primeira página' : 'Pessoa da segunda página',
        email: null,
        is_active: true,
        workspace_id: 'ws-history',
        workspace_name: 'Conta Histórico',
        role: 'workspace_member',
        access_package: null,
        membership_status: 'active',
        last_seen_at: null,
        active_session_count: 0,
        invitation_id: null,
        invitation_status: null,
        invitation_expires_at: null,
      }],
      count: 9,
      total: 9,
      limit: 8,
      offset,
      hasMore: offset === 0,
    }));

    renderPanel();
    fireEvent.click(await screen.findByText('Conta Histórico'));

    await screen.findByText('plano alterado');
    expect(screen.getByText('1–1 de 11')).toBeInTheDocument();
    expect(screen.queryByText('nao_deve_aparecer')).not.toBeInTheDocument();
    const eventsSection = screen.getByText('Histórico da assinatura').parentElement as HTMLElement;
    fireEvent.click(within(eventsSection).getByRole('button', { name: 'Próxima' }));

    await waitFor(() => expect(platformApi.subscriptionEvents).toHaveBeenLastCalledWith(
      'ws-history',
      10,
      10,
    ));
    await screen.findByText('trial estendido');
    expect(screen.getByText('11–11 de 11')).toBeInTheDocument();

    const peopleSection = screen.getByText('Usuários da conta').parentElement as HTMLElement;
    expect(within(peopleSection).getByText(/Pessoa da primeira página/)).toBeInTheDocument();
    expect(within(peopleSection).getByText('1–1 de 9')).toBeInTheDocument();
    fireEvent.click(within(peopleSection).getByRole('button', { name: 'Próxima' }));
    await waitFor(() => expect(platformApi.people).toHaveBeenLastCalledWith({
      workspaceId: 'ws-history',
      limit: 8,
      offset: 8,
    }));
    await within(peopleSection).findByText(/Pessoa da segunda página/);
    expect(within(peopleSection).getByText('9–9 de 9')).toBeInTheDocument();
  });
});
