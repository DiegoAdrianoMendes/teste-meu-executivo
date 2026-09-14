/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { klmApi, type KlmMatterFlowJourneyOverview } from '@/klm/api';
import { MatterFlowJourney } from '@/klm/components/MatterFlowJourney';
import { refreshIntervalFor } from '@/klm/lib/flowJourneyPolling';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

const overview: KlmMatterFlowJourneyOverview = {
  matter_id: 'matter-1',
  generated_at: '2026-07-20T18:00:00Z',
  kpis: {
    total_runs: 1,
    active_runs: 1,
    waiting_human_runs: 0,
    completed_runs: 0,
    failed_runs: 0,
    completion_rate: 0,
    overall_progress_percent: 50,
    average_duration_seconds: null,
    completed_steps: 1,
    total_steps: 2,
  },
  journeys: [{
    run_id: 'run-1',
    flow_id: 'flow-1',
    flow_name: 'Triagem jurídica',
    revision: 3,
    status: 'active',
    progress_percent: 50,
    completed_steps: 1,
    total_steps: 2,
    current_node_id: 'review',
    current_step_label: 'Revisar documentos',
    pause_reason: null,
    error_message: null,
    started_at: '2026-07-20T17:55:00Z',
    completed_at: null,
    paused_at: null,
    elapsed_seconds: 300,
    timeline: [
      {
        node_id: 'start',
        node_type: 'trigger',
        label: 'Lead recebido',
        state: 'completed',
        step: 1,
        occurred_at: '2026-07-20T17:55:00Z',
        decision: null,
      },
      {
        node_id: 'review',
        node_type: 'human_pause',
        label: 'Revisar documentos',
        state: 'current',
        step: 2,
        occurred_at: null,
        decision: null,
      },
    ],
  }],
};

describe('MatterFlowJourney', () => {
  it('keeps progress and operational KPIs visible with the live timeline', async () => {
    vi.spyOn(klmApi, 'matterFlowJourneys').mockResolvedValue(overview);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={queryClient}>
        <MatterFlowJourney matterId="matter-1" />
      </QueryClientProvider>,
    );

    expect(await screen.findByText('Triagem jurídica')).toBeInTheDocument();
    expect(screen.getAllByText('50%').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('Ao vivo · 2,5s')).toBeInTheDocument();
    expect(screen.getByText('1/2')).toBeInTheDocument();
    expect(screen.getAllByText('Revisar documentos').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('Lead recebido')).toBeInTheDocument();
  });

  it('picks the polling interval from the live state of the runs (V26)', () => {
    expect(refreshIntervalFor(undefined)).toBe(60_000);
    expect(refreshIntervalFor({ ...overview, kpis: { ...overview.kpis, active_runs: 0, waiting_human_runs: 0 } })).toBe(60_000);
    expect(refreshIntervalFor({ ...overview, kpis: { ...overview.kpis, active_runs: 0, waiting_human_runs: 1 } })).toBe(15_000);
    expect(refreshIntervalFor(overview)).toBe(2_500);
  });

  it('polls slowly without live runs and speeds up when a run becomes active (V26)', async () => {
    vi.useFakeTimers();
    const idle: KlmMatterFlowJourneyOverview = {
      ...overview,
      kpis: { ...overview.kpis, active_runs: 0, waiting_human_runs: 0, total_runs: 0, overall_progress_percent: 0 },
      journeys: [],
    };
    const spy = vi.spyOn(klmApi, 'matterFlowJourneys').mockResolvedValue(idle);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={queryClient}>
        <MatterFlowJourney matterId="matter-1" />
      </QueryClientProvider>,
    );

    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(spy).toHaveBeenCalledTimes(1);
    expect(screen.getByText('Monitorado')).toBeInTheDocument();

    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(spy).toHaveBeenCalledTimes(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(31_000); });
    expect(spy).toHaveBeenCalledTimes(2);

    spy.mockResolvedValue(overview);
    await act(async () => { await vi.advanceTimersByTimeAsync(61_000); });
    expect(screen.getByText('Ao vivo · 2,5s')).toBeInTheDocument();
    const callsWhenLive = spy.mock.calls.length;

    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(spy.mock.calls.length).toBeGreaterThan(callsWhenLive);
  });
});
