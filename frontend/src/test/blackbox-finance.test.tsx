/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import BlackboxFinance from '@/pages/BlackboxFinance';
import * as platform from '@/services/platform';

const manifestState: { value: Record<string, unknown> } = { value: { isPlatformRoot: true, permissions: [], availableModules: ['finance'], supportSession: { type: 'root_direct', workspaceId: 'workspace-finance' } } };
vi.mock('@/blackbox/session-manifest', () => ({
  useBlackboxManifest: () => ({ manifest: manifestState.value }),
}));

vi.mock('@/components/platform/PlatformEntityCombobox', () => ({
  PlatformEntityCombobox: ({ onChange }: { onChange: (value: string) => void }) => (
    <button type="button" onClick={() => onChange('workspace-finance')}>Selecionar Conta Alfa</button>
  ),
}));

function renderPage() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter><BlackboxFinance /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.spyOn(platform, 'fetchPlatformWorkspaceFinanceExecutive').mockResolvedValue({
    arr: { value: 120000, unit: 'BRL', quality: 'updated', source: 'contratos', definition: 'Receita recorrente anualizada.' },
    cac: { value: null, unit: 'BRL', quality: 'insufficient_data', source: 'aquisição', definition: 'Exige fonte de aquisição.' },
  });
  vi.spyOn(platform, 'fetchPlatformWorkspaceFinanceDrilldown').mockResolvedValue({
    total: '1000', groupDimension: 'cost_center', totalCount: 1, hasMore: false,
    coverage: { allocatedAmount: '900', unallocatedAmount: '100', reconciled: true },
    groups: [{ dimension: 'cost_center', id: 'center-1', label: 'ADM · Administrativo', amount: '900' }],
    items: [{ entryId: 'entry-1', entryLabel: 'Honorários', costCenterLabel: 'ADM · Administrativo', clientLabel: 'Cliente Alfa', contractLabel: 'Contrato mensal', matterLabel: 'Caso 123', amount: '900', source: 'manual' }],
  });
  vi.spyOn(platform, 'listPlatformWorkspaceFinanceExports').mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0, hasMore: false });
  vi.spyOn(platform, 'getPlatformWorkspaceFinanceExport').mockResolvedValue({ id: 'export-1', format: 'pdf', status: 'running', progress: 60 });
  vi.spyOn(platform, 'createPlatformWorkspaceFinanceExport').mockResolvedValue({ id: 'export-1', status: 'queued' });
  vi.spyOn(platform, 'cancelPlatformWorkspaceFinanceExport').mockResolvedValue({ id: 'export-1', status: 'cancelled' });
  vi.spyOn(platform, 'downloadPlatformWorkspaceFinanceExport').mockResolvedValue();
});

afterEach(() => {
  cleanup();
  manifestState.value = { isPlatformRoot: true, permissions: [], availableModules: ['finance'], supportSession: { type: 'root_direct', workspaceId: 'workspace-finance' } };
  vi.restoreAllMocks();
});

describe('BlackboxFinance', () => {
  it('uses an explicit account and drills down with human labels instead of raw JSON', async () => {
    const drilldown = vi.spyOn(platform, 'fetchPlatformWorkspaceFinanceDrilldown');
    renderPage();
    expect(screen.getByText(/Selecione uma conta/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Selecionar Conta Alfa' }));

    expect(await screen.findByText('ARR')).toBeInTheDocument();
    expect(screen.getByText(/120\.000,00/)).toBeInTheDocument();
    expect(screen.getByText('Dados insuficientes')).toBeInTheDocument();
    expect((await screen.findAllByText('Cliente Alfa')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Contrato mensal').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Caso 123').length).toBeGreaterThan(0);
    expect(document.querySelector('pre')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /ADM · Administrativo/ }));
    await waitFor(() => expect(drilldown).toHaveBeenLastCalledWith(
      'workspace-finance',
      expect.objectContaining({ cost_center_id: 'center-1' }),
    ));
    expect(screen.getByRole('link', { name: 'Abrir Conta 360' })).toHaveAttribute(
      'href',
      '/blackbox/workspaces?workspaceId=workspace-finance',
    );
  });

  it('creates the selected export format with the active drill context', async () => {
    const createExport = vi.spyOn(platform, 'createPlatformWorkspaceFinanceExport');
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'Selecionar Conta Alfa' }));
    await screen.findByText('ARR');
    fireEvent.click(screen.getByRole('button', { name: /ADM · Administrativo/ }));
    await waitFor(() => expect(screen.getByLabelText('Filtros ativos')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Formato'), { target: { value: 'xlsx' } });
    fireEvent.click(screen.getByRole('button', { name: 'Exportar contexto atual' }));
    await waitFor(() => expect(createExport).toHaveBeenCalledWith(
      'workspace-finance',
      expect.objectContaining({
        format: 'xlsx',
        drill_context: expect.objectContaining({ costCenterId: 'center-1' }),
      }),
    ));
    expect(createExport.mock.calls[0]?.[1]?.drill_context).not.toHaveProperty('cost_center_id');
  });

  it('paginates drilldown on the server and opens export detail through an explicit control', async () => {
    const drilldown = vi.spyOn(platform, 'fetchPlatformWorkspaceFinanceDrilldown').mockImplementation((_workspaceId, payload) => Promise.resolve({
      total: '1000', groupDimension: 'cost_center', totalCount: 51, hasMore: (payload.offset ?? 0) === 0,
      coverage: { allocatedAmount: '900', unallocatedAmount: '100', reconciled: true },
      groups: [],
      items: [{ entryId: `entry-${payload.offset ?? 0}`, entryLabel: 'Honorários', costCenterLabel: 'ADM', clientLabel: 'Cliente Alfa', contractLabel: 'Contrato mensal', matterLabel: 'Caso 123', amount: '900', source: 'manual' }],
    }));
    vi.spyOn(platform, 'listPlatformWorkspaceFinanceExports').mockResolvedValue({ items: [{ id: 'export-1', format: 'pdf', status: 'running', progress: 20 }], total: 1, limit: 50, offset: 0, hasMore: false });
    const detail = vi.spyOn(platform, 'getPlatformWorkspaceFinanceExport');

    renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'Selecionar Conta Alfa' }));
    await screen.findByText(/51 lançamento/);
    fireEvent.click(screen.getByRole('button', { name: 'Próxima' }));
    await waitFor(() => expect(drilldown).toHaveBeenLastCalledWith('workspace-finance', expect.objectContaining({ limit: 50, offset: 50 })));

    fireEvent.click(await screen.findByRole('button', { name: 'Ver status' }));
    await waitFor(() => expect(detail).toHaveBeenCalledWith('workspace-finance', 'export-1'));
    const selectedLabel = await screen.findByText(/Exportação selecionada:/);
    expect(selectedLabel.parentElement).toHaveTextContent('PDF · running · 60%');
  });

  it('does not read, export or mutate tenant finance without a matching administrative session', async () => {
    const executive = vi.spyOn(platform, 'fetchPlatformWorkspaceFinanceExecutive');
    const drilldown = vi.spyOn(platform, 'fetchPlatformWorkspaceFinanceDrilldown');
    const exports = vi.spyOn(platform, 'listPlatformWorkspaceFinanceExports');
    manifestState.value = { isPlatformRoot: true, permissions: [], availableModules: ['finance'], supportSession: null };
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'Selecionar Conta Alfa' }));
    expect(await screen.findByText('Sessão administrativa necessária')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: 'Abrir Conta 360' })[0]).toHaveAttribute('href', '/blackbox/workspaces?workspaceId=workspace-finance');
    expect(screen.queryByRole('tablist', { name: 'Análises financeiras avançadas' })).toBeNull();
    expect(screen.queryByRole('region', { name: 'Drill-down financeiro' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Adicionar linha' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Exportar contexto atual' })).toBeNull();
    await waitFor(() => {
      expect(executive).not.toHaveBeenCalled();
      expect(drilldown).not.toHaveBeenCalled();
      expect(exports).not.toHaveBeenCalled();
    });
  });

  it('does not reuse a root session opened for another account', async () => {
    const executive = vi.spyOn(platform, 'fetchPlatformWorkspaceFinanceExecutive');
    manifestState.value = { isPlatformRoot: true, permissions: [], availableModules: ['finance'], supportSession: { type: 'root_direct', workspaceId: 'workspace-other' } };
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'Selecionar Conta Alfa' }));
    expect(await screen.findByText('Sessão administrativa necessária')).toBeInTheDocument();
    expect(executive).not.toHaveBeenCalled();
  });

  it('renders DRE, forecast, valuation and tax reform as operational information without raw JSON', async () => {
    vi.spyOn(platform, 'fetchPlatformWorkspaceFinanceDre').mockResolvedValue({
      mode: 'accrual', periodStart: '2026-08-01', periodEnd: '2026-08-31', result: '700',
      lines: [{ key: 'net_revenue', label: 'Receita líquida', amount: '1000' }, { key: 'expenses', label: 'Despesas', amount: '-300' }],
      comparison: { currentResult: '700', previousResult: '600', variance: '100', variancePercent: '16.67' },
      budget: { revenue: '950', expenses: '280', result: '670', variance: '30' }, budgetQuality: 'configured',
      series: [{ periodStart: '2026-08-01', periodEnd: '2026-08-31', result: '700', accumulatedResult: '700' }],
      source: 'FinanceEntry + FinanceAllocation', generatedAt: '2026-08-31T12:00:00Z',
    });
    vi.spyOn(platform, 'generatePlatformWorkspaceFinanceForecast').mockResolvedValue([
      { id: 'forecast-base', scenario: 'base', horizon: '90d', periodStart: '2026-08-05', periodEnd: '2026-11-03', revenue: '5000', expense: '3000', assumptions: { factor: '1', source: 'histórico financeiro' } },
    ]);
    vi.spyOn(platform, 'calculatePlatformWorkspaceFinanceValuation').mockResolvedValue({
      id: 'valuation-1', method: 'revenue_multiple', valuationDate: '2026-08-04', enterpriseValue: '300000', premiseQuality: 'partial',
      assumptions: { multiple: 3, range: { low: '240000', base: '300000', high: '360000' }, decomposition: { netRevenue: '100000', ebitdaProxy: '35000', method: 'revenue_multiple' }, supportingContracts: [{ contractId: 'contract-1', label: 'Contrato Alfa', allocatedRevenue: '80000' }] },
    });
    vi.spyOn(platform, 'getPlatformWorkspaceFinanceTaxProfile').mockResolvedValue([]);
    vi.spyOn(platform, 'simulatePlatformWorkspaceFinanceTaxReform').mockResolvedValue({
      regime: 'lucro_presumido', quality: 'simulation_requires_accountant_validation',
      transition: [{ year: 2027, rate: '15', estimatedTax: '15000', taxDelta: '1000', estimatedMargin: '64000', estimatedCashImpact: '-1000' }],
      impact: { priceImpact: '500', marginImpact: '64000', cashImpact: '-1000' }, ownerAlerts: ['D-180', 'D-90'], sources: [{ label: 'Receita Federal' }],
    });

    renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'Selecionar Conta Alfa' }));
    await screen.findByText('ARR');

    fireEvent.click(screen.getByRole('tab', { name: 'DRE' }));
    expect((await screen.findAllByText('Receita líquida')).length).toBeGreaterThan(0);
    expect(screen.getByText('Comparação com período anterior')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: 'Projeções' }));
    fireEvent.click(screen.getByRole('button', { name: 'Gerar cenários 90 dias' }));
    expect(await screen.findByText('Saldo projetado:', { exact: false })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: 'Valuation' }));
    fireEvent.click(screen.getByRole('button', { name: 'Calcular múltiplo de receita' }));
    expect(await screen.findByText('Contratos que sustentam a estimativa')).toBeInTheDocument();
    expect(screen.getByText('Contrato Alfa')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: 'Tributação e reforma' }));
    fireEvent.click(screen.getByRole('button', { name: 'Simular transição tributária' }));
    expect(await screen.findByText('Revisão humana obrigatória')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Transição tributária' }).querySelector('table')).toBeInTheDocument();
    expect(document.querySelector('pre')).toBeNull();
  }, 15_000);
});
