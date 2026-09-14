/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import {
  ConversationResponseMetricsSheet,
  ConversationResponseWaitClock,
} from '@/klm/components/ConversationResponseMetricsSheet';
import {
  klmApi,
  type KlmConversationResponseMetric,
  type KlmConversationResponseMetrics,
} from '@/klm/api';

const waitingMetric: KlmConversationResponseMetric = {
  threadId: '11111111-1111-4111-8111-111111111111',
  threadStatus: 'open',
  primaryChannel: 'whatsapp',
  state: 'cliente_aguardando_escritorio',
  stateStartedAt: '2026-07-22T15:00:00.000Z',
  durationSeconds: 120,
  lastClientFacingActivityAt: '2026-07-22T15:00:00.000Z',
  assignedTeamId: '22222222-2222-4222-8222-222222222222',
  assignedTeamName: 'Saúde',
  assignedAttendantId: '33333333-3333-4333-8333-333333333333',
  assignedAttendantName: 'Marina Gestora',
  slaDueAt: '2026-07-22T15:01:00.000Z',
  slaStatus: 'overdue',
  isOverdue: true,
  overdueSeconds: 60,
  firstClientMessageAt: '2026-07-22T15:00:00.000Z',
  firstResponseAt: null,
  firstResponseSeconds: null,
  firstResponseWithinSla: null,
};

const waitingForClientMetric: KlmConversationResponseMetric = {
  ...waitingMetric,
  state: 'escritorio_aguardando_cliente',
  slaDueAt: null,
  slaStatus: 'not_applicable',
  isOverdue: false,
  overdueSeconds: 0,
};

function metricsFixture(): KlmConversationResponseMetrics {
  const metrics = {
    conversations: 4,
    pending: 2,
    averageWaitSeconds: 150,
    medianWaitSeconds: 150,
    p90WaitSeconds: 180,
    longestWaitSeconds: 180,
    averageFirstResponseSeconds: 45,
    withinSlaRate: 75,
    slaEvaluated: 4,
    withinSla: 3,
  };
  return {
    generatedAt: '2026-07-22T15:02:00.000Z',
    period: {
      preset: 'last_7_days',
      startedAt: '2026-07-15T15:02:00.000Z',
      endedAt: '2026-07-22T15:02:00.000Z',
      asOf: '2026-07-22T15:02:00.000Z',
    },
    filters: {
      preset: 'all',
      attendantIds: [],
      teamIds: [],
      statuses: [],
      slaStatuses: [],
      sortBy: 'duration',
      sortDirection: 'desc',
    },
    total: 1,
    offset: 0,
    limit: 500,
    hasMore: false,
    items: [waitingMetric],
    account: { groupId: 'workspace-1', groupName: 'KLM Advogados', metrics },
    byTeam: [{ groupId: waitingMetric.assignedTeamId, groupName: 'Saúde', metrics }],
    byAttendant: [{ groupId: waitingMetric.assignedAttendantId, groupName: 'Marina Gestora', metrics }],
  };
}

function renderSheet() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ConversationResponseMetricsSheet open onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe('conversation response metrics', () => {
  it('serializes the typed API contract with repeated tenant filters', async () => {
    const fixture = metricsFixture();
    http.apiFetch.mockResolvedValue(fixture);

    const result = await klmApi.conversationResponseMetrics({
      period: 'custom',
      dateFrom: '2026-07-01T00:00:00.000Z',
      dateTo: '2026-07-22T23:59:59.000Z',
      preset: 'pending',
      attendantIds: ['attendant-1', 'attendant-2'],
      teamIds: ['team-1'],
      statuses: ['cliente_aguardando_escritorio'],
      slaStatuses: ['overdue'],
      sortBy: 'duration',
      sortDirection: 'desc',
      limit: 500,
    });

    expect(result).toBe(fixture);
    const requestedPath = String(http.apiFetch.mock.calls[0][0]);
    const requestedUrl = new URL(requestedPath, 'http://klm.local');
    expect(requestedUrl.pathname).toBe('/api/v1/klm/omnichannel/response-metrics');
    expect(requestedUrl.searchParams.get('period')).toBe('custom');
    expect(requestedUrl.searchParams.getAll('attendant_id')).toEqual(['attendant-1', 'attendant-2']);
    expect(requestedUrl.searchParams.getAll('team_id')).toEqual(['team-1']);
    expect(requestedUrl.searchParams.getAll('status')).toEqual(['cliente_aguardando_escritorio']);
    expect(requestedUrl.searchParams.getAll('sla')).toEqual(['overdue']);
    expect(requestedUrl.searchParams.get('sort_direction')).toBe('desc');
  });

  it('keeps the waiting clock alive and marks an overdue office response', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-22T15:02:00.000Z'));
    render(<ConversationResponseWaitClock metric={waitingMetric} />);

    expect(screen.getByLabelText('Cliente aguarda o escritório: 2m 00s')).toBeInTheDocument();
    expect(screen.getByText('Resposta atrasada', { selector: '.sr-only' })).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(1_000));
    expect(screen.getByLabelText('Cliente aguarda o escritório: 2m 01s')).toBeInTheDocument();
  });

  it('classifies the time waiting for the client at the exact green, yellow and red boundaries', () => {
    const startedAt = new Date(waitingForClientMetric.stateStartedAt!).getTime();
    const { rerender } = render(
      <ConversationResponseWaitClock metric={waitingForClientMetric} nowMs={startedAt + 300_000} />,
    );

    expect(screen.getByText('Faixa verde')).toBeInTheDocument();
    expect(screen.getByText('Faixa verde').closest('[data-response-wait-band]')).toHaveAttribute('data-response-wait-band', 'green');

    rerender(<ConversationResponseWaitClock metric={waitingForClientMetric} nowMs={startedAt + 301_000} />);
    expect(screen.getByText('Faixa amarela').closest('[data-response-wait-band]')).toHaveAttribute('data-response-wait-band', 'yellow');

    rerender(<ConversationResponseWaitClock metric={waitingForClientMetric} nowMs={startedAt + 600_000} />);
    expect(screen.getByText('Faixa amarela').closest('[data-response-wait-band]')).toHaveAttribute('data-response-wait-band', 'yellow');

    rerender(<ConversationResponseWaitClock metric={waitingForClientMetric} nowMs={startedAt + 601_000} />);
    expect(screen.getByText('Faixa vermelha').closest('[data-response-wait-band]')).toHaveAttribute('data-response-wait-band', 'red');
  });

  it('uses the card layout without cutting the state or duration', () => {
    const startedAt = new Date(waitingForClientMetric.stateStartedAt!).getTime();
    const { container } = render(
      <ConversationResponseWaitClock
        metric={waitingForClientMetric}
        nowMs={startedAt + 601_000}
        layout="card"
      />,
    );

    expect(screen.getByText('Aguardando cliente')).toBeInTheDocument();
    expect(screen.getByText('10m 01s')).toBeInTheDocument();
    const clock = container.querySelector('[data-response-wait-band="red"]');
    expect(clock).toHaveClass('grid', 'w-full', 'min-w-0', 'whitespace-normal');
    expect(clock).not.toHaveClass('truncate');
  });

  it('shows real KPIs, current-assignment caveat and the minimum-wait filter without navigation', async () => {
    http.apiFetch.mockResolvedValue(metricsFixture());
    renderSheet();

    const dialog = await screen.findByRole('dialog', { name: 'Tempo sem resposta' });
    await waitFor(() => expect(within(dialog).getAllByText('KLM Advogados')).toHaveLength(2));
    expect(within(dialog).getByText('Cliente aguardando').closest('article')).toHaveTextContent('2');
    expect(within(dialog).getByText('Espera média').closest('article')).toHaveTextContent('2m 30s');
    expect(within(dialog).getByText('Dentro do prazo').closest('article')).toHaveTextContent('75%');
    expect(within(dialog).getByText(/os agrupamentos por equipe e atendente usam o responsável atual/i)).toBeInTheDocument();
    expect(within(dialog).getByRole('columnheader', { name: 'Maior espera' })).toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText('Espera mínima (segundos)'), { target: { value: '181' } });
    expect(within(dialog).getByText(/filtra apenas a lista detalhada carregada/i)).toBeInTheDocument();
    expect(within(dialog).getByText('Nenhuma conversa corresponde à espera mínima e aos filtros.')).toBeInTheDocument();
    expect(http.apiFetch).toHaveBeenCalledWith(expect.stringContaining('/api/v1/klm/omnichannel/response-metrics?'));
  });

  it('fails clearly without fabricating fallback KPIs', async () => {
    http.apiFetch.mockRejectedValue(new Error('Banco local indisponível'));
    renderSheet();

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('KPIs de resposta indisponíveis');
    expect(alert).toHaveTextContent('Banco local indisponível');
    expect(alert).toHaveTextContent('Nenhum número estimado ou dado de demonstração será exibido.');
    expect(screen.queryByText('KLM Advogados')).not.toBeInTheDocument();
  });
});
