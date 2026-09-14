/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import BlackboxAdvancedReports from '@/pages/BlackboxAdvancedReports';

vi.mock('recharts', () => ({
  Bar: () => null,
  BarChart: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  CartesianGrid: () => null,
  Legend: () => null,
  Line: () => null,
  LineChart: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  Tooltip: () => null,
  XAxis: () => null,
  YAxis: () => null,
}));

const studiosApi = vi.hoisted(() => ({
  advancedReport: vi.fn(),
}));

vi.mock('@/services/blackbox-studios', async () => {
  const actual = await vi.importActual<typeof import('@/services/blackbox-studios')>(
    '@/services/blackbox-studios',
  );
  return { ...actual, fetchAdvancedReport: studiosApi.advancedReport };
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <BlackboxAdvancedReports />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('BlackboxAdvancedReports', () => {
  it('navigates the analytic table with server-side offset and total', async () => {
    studiosApi.advancedReport.mockImplementation(({ limit, offset }: {
      limit: number;
      offset: number;
    }) => Promise.resolve({
      generatedAt: '2026-07-29T12:00:00Z',
      periodDays: 30,
      availableCategories: [{ key: 'receita', label: 'Receita' }],
      appliedFilters: { limit, offset },
      totalRows: 26,
      rowsWithData: 26,
      limit,
      offset,
      hasMore: offset === 0,
      rows: [{
        key: offset === 0 ? 'receita.mrr' : 'receita.arr',
        label: offset === 0 ? 'MRR da plataforma' : 'ARR da plataforma',
        category: 'receita',
        categoryLabel: 'Receita',
        value: offset === 0 ? 100 : 1200,
        unit: 'brl',
        deltaPct: null,
        hasData: true,
        note: null,
        series: [],
      }],
      chartRows: [],
      trendRows: [],
    }));

    renderPage();

    await screen.findByText('MRR da plataforma');
    expect(screen.getByText('1–1 de 26')).toBeInTheDocument();
    expect(studiosApi.advancedReport).toHaveBeenLastCalledWith(expect.objectContaining({
      limit: 25,
      offset: 0,
    }));

    fireEvent.click(screen.getByRole('button', { name: 'Próxima' }));

    await waitFor(() => expect(studiosApi.advancedReport).toHaveBeenLastCalledWith(
      expect.objectContaining({ limit: 25, offset: 25 }),
    ));
    await screen.findByText('ARR da plataforma');
    expect(screen.getByText('26–26 de 26')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Próxima' })).toBeDisabled();
  });

  it('does not represent an unavailable report as zero', async () => {
    studiosApi.advancedReport.mockRejectedValue(new Error('indisponível'));

    renderPage();

    await screen.findByText('Não foi possível carregar os relatórios.');
    expect(screen.getAllByText('—')).toHaveLength(2);
  });
});
