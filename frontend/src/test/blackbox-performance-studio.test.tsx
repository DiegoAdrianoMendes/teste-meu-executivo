/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import BlackboxPerformanceStudio from '@/pages/BlackboxPerformanceStudio';

vi.mock('recharts', () => ({
  Bar: () => null,
  BarChart: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  CartesianGrid: () => null,
  ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  Tooltip: () => null,
  XAxis: () => null,
  YAxis: () => null,
}));

vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({ state: { currentUser: { id: 'root', isPlatformRoot: true } } }),
}));

vi.mock('@/components/platform/PlatformEntityCombobox', () => ({
  PlatformEntityCombobox: () => <div>Filtro de escritório</div>,
}));

const studiosApi = vi.hoisted(() => ({
  office: vi.fn(),
  saas: vi.fn(),
  benchmark: vi.fn(),
}));

vi.mock('@/services/blackbox-studios', async () => {
  const actual = await vi.importActual<typeof import('@/services/blackbox-studios')>(
    '@/services/blackbox-studios',
  );
  return {
    ...actual,
    fetchWorkspaceTeamPerformance: studiosApi.office,
    fetchSaasTeamPerformance: studiosApi.saas,
    fetchWorkspaceBenchmark: studiosApi.benchmark,
  };
});

function board(offset: number) {
  return {
    audience: 'workspace_team' as const,
    periodDays: 30,
    generatedAt: '2026-07-29T12:00:00Z',
    scoreFormula: 'Pontuação persistida.',
    total: 26,
    limit: 25,
    offset,
    hasMore: offset === 0,
    rows: [{
      id: `employee-${offset}`,
      name: offset === 0 ? 'Primeira pessoa' : 'Última pessoa',
      scopeName: 'Conta real',
      role: 'support',
      primaryCount: 1,
      completedCount: 1,
      pendingCount: 0,
      qualityValue: null,
      score: 10,
    }],
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <BlackboxPerformanceStudio />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('BlackboxPerformanceStudio', () => {
  it('navigates each board through the authoritative server page', async () => {
    studiosApi.office.mockImplementation((_days, _workspace, _limit, offset) => (
      Promise.resolve(board(offset))
    ));
    studiosApi.saas.mockResolvedValue({ ...board(0), audience: 'saas_team' });
    studiosApi.benchmark.mockResolvedValue({ ...board(0), audience: 'workspace_benchmark' });

    renderPage();

    await screen.findByText('Primeira pessoa');
    expect(screen.getByText('1–1 de 26')).toBeInTheDocument();
    expect(studiosApi.office).toHaveBeenLastCalledWith(30, undefined, 25, 0);

    fireEvent.click(screen.getByRole('button', { name: 'Próxima' }));

    await waitFor(() => expect(studiosApi.office).toHaveBeenLastCalledWith(30, undefined, 25, 25));
    await screen.findByText('Última pessoa');
    expect(screen.getByText('26–26 de 26')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Próxima' })).toBeDisabled();
  });
});
