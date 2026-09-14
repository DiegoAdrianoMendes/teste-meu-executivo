/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

import StatusPage from '@/pages/Status';
import * as http from '@/services/http';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

const systemStatus = {
  status: 'operational',
  timestamp: '2026-07-27T12:00:00Z',
  version: '1.0.0',
  components: {
    workers: { status: 'operational', workers: 3 },
  },
  message: 'Todos os componentes verificados estão operando normalmente.',
  historyRecorded: true,
};

const history = {
  state: 'ready',
  periodDays: 30,
  observations: 10,
  uptimePercent: 99.9,
  firstObservedAt: '2026-07-01T12:00:00Z',
  lastObservedAt: '2026-07-27T12:00:00Z',
  daysObserved: 26,
  latestStatus: 'operational',
  daily: [],
  incidents: [],
};

const taskOverview = {
  generatedAt: '2026-07-27T12:00:00Z',
  graceSeconds: 120,
  summary: { total: 3, failing: 1, late: 1, neverRun: 1, healthy: 1 },
  tasks: [
    {
      scheduleName: 'finance-dunning-tick-daily',
      taskName: 'finance_tasks.dunning_tick',
      scheduleDescription: '<crontab: 0 8 * * * (m/h/dM/MY/d)>',
      status: 'error',
      lastStartedAt: '2026-07-25T08:00:00Z',
      lastFinishedAt: '2026-07-25T08:00:03Z',
      lastSuccessAt: null,
      lastErrorAt: '2026-07-25T08:00:03Z',
      lastDurationMs: 3000,
      errorType: 'RuntimeError',
      successCount: 0,
      errorCount: 4,
      isLate: true,
      expectedNextRunAt: '2026-07-26T08:00:00Z',
    },
    {
      scheduleName: 'recover-agent-runs-every-minute',
      taskName: 'app.workers.recover_agent_runs',
      scheduleDescription: '<crontab: * * * * * (m/h/dM/MY/d)>',
      status: 'success',
      lastStartedAt: '2026-07-27T11:59:00Z',
      lastFinishedAt: '2026-07-27T11:59:01Z',
      lastSuccessAt: '2026-07-27T11:59:01Z',
      lastErrorAt: null,
      lastDurationMs: 1000,
      errorType: null,
      successCount: 120,
      errorCount: 0,
      isLate: false,
      expectedNextRunAt: '2026-07-27T12:00:00Z',
    },
    {
      scheduleName: 'send-daily-digest-at-7am',
      taskName: 'notification_tasks.send_daily_digest',
      scheduleDescription: '<crontab: 0 7 * * * (m/h/dM/MY/d)>',
      status: 'never_run',
      lastStartedAt: null,
      lastFinishedAt: null,
      lastSuccessAt: null,
      lastErrorAt: null,
      lastDurationMs: null,
      errorType: null,
      successCount: 0,
      errorCount: 0,
      isLate: null,
      expectedNextRunAt: null,
    },
  ],
};

function mockApi() {
  return vi.spyOn(http, 'apiFetch').mockImplementation(async (path: string) => {
    if (path === '/api/v1/status') return systemStatus as never;
    if (path === '/api/v1/status/history') return history as never;
    if (path === '/api/v1/status/tasks') return taskOverview as never;
    throw new Error(`unexpected path ${path}`);
  });
}

describe('Status — aba Workers (dormente #9)', () => {
  it('não apresenta infraestrutura Meta preparada como WhatsApp operacional', async () => {
    vi.spyOn(http, 'apiFetch').mockImplementation(async (path: string) => {
      if (path === '/api/v1/status') {
        return {
          ...systemStatus,
          status: 'partial',
          components: {
            whatsapp_api: {
              status: 'prepared',
              note: 'Plataforma preparada; nenhum número operacional conectado',
            },
          },
        } as never;
      }
      if (path === '/api/v1/status/history') return history as never;
      if (path === '/api/v1/status/tasks') return taskOverview as never;
      throw new Error(`unexpected path ${path}`);
    });

    render(<StatusPage />);

    expect(await screen.findByText('Preparado, sem conexão')).toBeInTheDocument();
    expect(screen.getByText('Plataforma preparada; nenhum número operacional conectado')).toBeInTheDocument();
  });

  it('mantém a contagem de workers já existente na visão geral (não regressão)', async () => {
    mockApi();
    render(<StatusPage />);

    expect(await screen.findByText('3 worker(s) respondendo')).toBeInTheDocument();
    expect(screen.getByText('Componentes verificados')).toBeInTheDocument();
    expect(screen.getByText('Disponibilidade observada')).toBeInTheDocument();
  });

  it('renderiza a tabela por task e destaca erro e atraso', async () => {
    mockApi();
    render(<StatusPage />);

    await screen.findByText('3 worker(s) respondendo');
    fireEvent.click(screen.getByRole('tab', { name: /Workers/ }));

    const failing = await screen.findByTestId('task-row-finance-dunning-tick-daily');
    expect(failing).toHaveAttribute('data-attention', 'true');
    expect(failing).toHaveTextContent('Erro');
    expect(failing).toHaveTextContent('Atrasada');
    expect(failing).toHaveTextContent('RuntimeError');

    const healthy = screen.getByTestId('task-row-recover-agent-runs-every-minute');
    expect(healthy).toHaveAttribute('data-attention', 'false');
    expect(healthy).toHaveTextContent('Sucesso');
  });

  it('mostra task que nunca rodou como "nunca executada", não como sucesso', async () => {
    mockApi();
    render(<StatusPage />);

    await screen.findByText('3 worker(s) respondendo');
    fireEvent.click(screen.getByRole('tab', { name: /Workers/ }));

    const neverRun = await screen.findByTestId('task-row-send-daily-digest-at-7am');
    expect(neverRun).toHaveTextContent('Nunca executada');
    expect(neverRun).not.toHaveTextContent('Sucesso');
    expect(neverRun).toHaveAttribute('data-attention', 'false');
  });

  it('consulta o estado por task via serviço http, sem fetch cru', async () => {
    const spy = mockApi();
    render(<StatusPage />);

    await waitFor(() => expect(spy).toHaveBeenCalledWith('/api/v1/status/tasks'));
  });
});
