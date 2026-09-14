/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { PlatformAiOperationsPanel } from '@/components/platform/PlatformAiOperationsPanel';

const api = vi.hoisted(() => ({
  summary: vi.fn(),
  events: vi.fn(),
}));

vi.mock('@/services/platform', async () => {
  const actual = await vi.importActual<typeof import('@/services/platform')>('@/services/platform');
  return {
    ...actual,
    fetchAiOperationsSummary: api.summary,
    fetchAiOperationsEvents: api.events,
  };
});

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <PlatformAiOperationsPanel enabled />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('PlatformAiOperationsPanel', () => {
  it('renders KPIs, route alert and fallback event feed', async () => {
    api.summary.mockResolvedValue({
      generated_at: '2026-07-24T12:00:00Z',
      window_days: 7,
      executions: 3,
      cost_usd: '0.0305',
      fallback_count: 1,
      fallback_rate: 0.3333,
      billing_unknown: 1,
      by_class_provider: [
        {
          task_class: 'premium',
          provider: 'openai',
          executions: 1,
          fallback_count: 1,
          cost_usd: '0.03',
          billing_unknown: 0,
        },
      ],
      by_agent: [{ agent_name: 'klm_drafter', executions: 2, cost_usd: '0.03' }],
      by_day: [],
      by_workspace: [{ workspace_id: 'w1', executions: 3, cost_usd: '0.03', fallback_count: 1 }],
      route_alerts: [
        {
          route_key: 'anthropic:claude-opus-4-8',
          provider: 'anthropic',
          model: 'claude-opus-4-8',
          failures: 1,
          last_failure_at: '2026-07-24T11:00:00Z',
        },
      ],
    });
    api.events.mockResolvedValue({
      total: 1,
      limit: 12,
      offset: 0,
      hasMore: false,
      items: [
        {
          id: 'e1',
          workspace_id: 'w1',
          created_at: '2026-07-24T11:00:00Z',
          agent_name: 'klm_drafter',
          task_class: 'premium',
          provider: 'openai',
          model: 'gpt-5.6-sol',
          outcome: 'success',
          route_key: 'openai:gpt-5.6-sol',
          fallback_used: true,
          cache_hit: false,
          billing_unknown: false,
          latency_ms: 900,
          input_tokens: 2000,
          output_tokens: 800,
          cost_usd: '0.03',
          estimated_cost_usd: null,
          operation: 'complete',
        },
      ],
    });

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText('Custo (7d)')).toBeTruthy();
    });
    // Taxa de fallback 33%
    expect(screen.getByText('33%')).toBeTruthy();
    // Alerta de rota com falha
    expect(screen.getByText('anthropic:claude-opus-4-8')).toBeTruthy();
    // Feed de evento de fallback
    expect(await screen.findByText('openai:gpt-5.6-sol')).toBeTruthy();
  });

  it('toggles the fallback-only filter', async () => {
    api.summary.mockResolvedValue({
      generated_at: '2026-07-24T12:00:00Z',
      window_days: 7,
      executions: 0,
      cost_usd: '0',
      fallback_count: 0,
      fallback_rate: 0,
      billing_unknown: 0,
      by_class_provider: [],
      by_agent: [],
      by_day: [],
      by_workspace: [],
      route_alerts: [],
    });
    api.events.mockResolvedValue({ total: 0, limit: 12, offset: 0, hasMore: false, items: [] });

    renderPanel();
    const toggle = await screen.findByText('Só fallbacks');
    fireEvent.click(toggle);
    await waitFor(() => {
      expect(screen.getByText('Todos')).toBeTruthy();
    });
    expect(api.events).toHaveBeenLastCalledWith({
      days: 7,
      limit: 12,
      offset: 0,
      onlyFallbacks: false,
    });
  });
});
