/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FinanceOverviewPanel } from '@/klm/components/FinanceOverviewPanel';
import { klmApi } from '@/klm/api';

vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({
    can: (module: string, action: string) =>
      module === 'finance' && ['manage', 'export'].includes(action),
  }),
}));
vi.mock('@/klm/api/useFinanceReports', () => ({ useFinanceReports: () => ({ data: { recebido: 100, aReceber: 50, vencido: { total: 0, dias1A15: 0, dias16A30: 0, dias31A60: 0, dias61Mais: 0 }, inadimplenciaPct: 0, mrr: 0, despesas: 20, resultado: 80 }, isPending: false }) }));
vi.mock('@/klm/api/useFinanceDre', () => ({ useFinanceDre: () => ({ data: { receitaTotal: 100, despesaTotal: 20, resultado: 80, receitasPorCategoria: [], mensal: [] }, isPending: false }) }));
vi.mock('@/klm/api/useFinanceCashFlow', () => ({ useFinanceCashFlow: () => ({ data: { asOf: new Date().toISOString(), horizonteDias: 90, dias: [] }, isPending: false }) }));

const renderPanel = () => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><FinanceOverviewPanel /></QueryClientProvider>);
const searchFinanceDimension = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

beforeEach(() => {
  window.history.replaceState({}, '', '/app/financeiro');
  vi.spyOn(klmApi, 'listFinanceExports').mockResolvedValue([]);
  vi.spyOn(klmApi, 'listFinanceCostCenters').mockResolvedValue([
    { id: 'center-1', workspaceId: 'w', code: 'ADM', name: 'Administrativo', active: true, version: 1, type: 'operational', status: 'active' },
  ]);
  vi.spyOn(klmApi, 'financeLookup').mockImplementation(async (type) => ({
    entityType: type, search: '', limit: 50, nextCursor:null,
    items: type === 'entry' ? [{ id: 'entry-1', label: 'Honorários março', matterId: 'matter-1' }]
      : type === 'client' ? [{ id: 'client-1', label: 'Cliente Alfa' }]
      : type === 'matter' ? [{ id: 'matter-1', label: 'Caso Alfa', clientId: 'client-1' }]
      : type === 'contract' ? [{ id: 'contract-1', label: 'Contrato Alfa', matterId: 'matter-1' }]
      : type === 'team' ? [{ id: 'team-1', label: 'Equipe Financeira' }]
      : [{ id: 'user-1', label: 'Responsável Financeiro' }],
  }));
  vi.spyOn(klmApi,'resolveFinanceLookups').mockImplementation(async (type,ids)=>({entityType:type,items:ids.map((id)=>({id,label:`${type} salvo`}))}));
  vi.spyOn(klmApi, 'financeAnalytics').mockResolvedValue({ workspaceId:'w', filters:{}, items: [], groups:[], groupDimension:'cost_center', total: '0', totalCount: 0, limit:25, offset:0, hasMore: false, generatedAt:'2026-08-02T12:00:00Z' });
  vi.spyOn(klmApi, 'financeAnalyticsDrilldown').mockResolvedValue({ workspaceId:'w', filters:{}, items: [], groups:[], groupDimension:'client', total: '0', totalCount: 0, limit:25, offset:0, hasMore: false, generatedAt:'2026-08-02T12:00:00Z' });
  vi.spyOn(klmApi, 'financeAllocationClosure').mockResolvedValue({ financeEntryId:'entry-1', allocatedPercentage:'100', unallocatedPercentage:'0', allocatedAmount:'100', unallocatedAmount:'0', closed:true });
  vi.spyOn(klmApi, 'financeDimensionalDre').mockResolvedValue({ mode:'cash', periodStart:'2026-01-01', periodEnd:'2026-08-02', lines:[{key:'result',label:'Resultado gerencial',amount:'80'}], result:'80', budget:null, budgetQuality:'not_configured', source:'teste', generatedAt:'2026-08-02T12:00:00Z' });
  vi.spyOn(klmApi, 'listFinanceForecasts').mockResolvedValue([]);
  vi.spyOn(klmApi, 'listFinanceValuations').mockResolvedValue([]);
  vi.spyOn(klmApi, 'listFinanceBudgetLines').mockResolvedValue([]);
  vi.spyOn(klmApi, 'financeCostCenterImpact').mockResolvedValue({costCenterId:'center-1',version:1,childCount:0,allocationCount:0,allocatedAmount:'0',requiresConfirmation:false});
  vi.spyOn(klmApi, 'previewFinanceCostCenterImpact').mockResolvedValue({costCenterId:'center-1',version:1,childCount:0,allocationCount:0,allocatedAmount:'0',requiresConfirmation:false,proposed:{},expectedVersion:1,eligible:true});
  vi.spyOn(klmApi, 'financeExecutiveAnalytics').mockResolvedValue({ generatedAt: '2026-08-02T12:00:00Z', periodStart: '2026-08-01', periodEnd: '2026-08-02', ...Object.fromEntries(['arr','contractedRevenue','grossMargin','contributionMargin','ebitda','burn','runway','breakEven','concentration','dimensionalProfitability','cac','ltv','payback'].map((key) => [key, { value: key === 'arr' ? 1200 : null, quality: key === 'arr' ? 'derived' : 'insufficient_data', source: 'teste', definition: key, unit: key === 'concentration' ? 'percent' : key === 'payback' ? 'months' : 'BRL' }])) } as Awaited<ReturnType<typeof klmApi.financeExecutiveAnalytics>>);
  vi.spyOn(klmApi, 'financeTaxProfile').mockResolvedValue(null);
  vi.spyOn(klmApi, 'financeTaxRules').mockResolvedValue({ schemaVersion: 1, verifiedAt: '2026-08-02', asOf: '2026-08-02', reviewRequired: false, mandatoryHumanGate: 'contador', disclaimer: 'Simulação gerencial.', timeline: [{ year: 2026, phase: 'test', summary: 'Ano-teste.', sourceUrl: 'https://www.gov.br/' }], rules: [], alerts: [{ severity: 'high', code: 'tax_profile_missing', message: 'Perfil tributário não configurado.' }] });
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('FinanceOverviewPanel export lifecycle', () => {
  it('does not fan out empty high-cardinality lookups during the initial overview load', async () => {
    renderPanel();
    await screen.findByText('DRE gerencial dimensional');
    expect(klmApi.financeLookup).not.toHaveBeenCalled();
    searchFinanceDimension('Buscar cliente', 'Cliente');
    await waitFor(() => expect(klmApi.financeLookup).toHaveBeenCalledTimes(1));
    expect(klmApi.financeLookup).toHaveBeenCalledWith('client', expect.objectContaining({ search: 'Cliente' }));
  });

  it.each(['pdf', 'xlsx', 'docx'] as const)('creates a %s job and downloads when completed', async (format) => {
    vi.spyOn(klmApi, 'createFinanceExport').mockResolvedValue({ id: `${format}-1`, format, status: 'queued', drillContext: {}, workspaceId: 'w', actorUserId: 'u' });
    vi.spyOn(klmApi, 'getFinanceExport').mockResolvedValue({ id: `${format}-1`, format, status: 'completed', drillContext: {}, workspaceId: 'w', actorUserId: 'u', expiresAt: new Date(Date.now() + 60_000).toISOString() });
    vi.spyOn(klmApi, 'downloadFinanceExport').mockResolvedValue({ blob: new Blob(['ok']), filename: `finance.${format}`, truncated: false });
    renderPanel();
    fireEvent.change(screen.getByRole('combobox', { name: 'Formato da exportação financeira' }), { target: { value: format } });
    fireEvent.click(screen.getByRole('button', { name: 'Exportar relatório' }));
    await waitFor(() => expect(klmApi.createFinanceExport).toHaveBeenCalledWith(expect.objectContaining({ format })));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Baixar' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Baixar' }));
    await waitFor(() => expect(klmApi.downloadFinanceExport).toHaveBeenCalledWith(`${format}-1`));
  });

  it('cancels queued jobs and exposes failed/expired states', async () => {
    vi.spyOn(klmApi, 'createFinanceExport').mockResolvedValue({ id: 'job-1', format: 'pdf', status: 'queued', drillContext: {}, workspaceId: 'w', actorUserId: 'u' });
    vi.spyOn(klmApi, 'getFinanceExport').mockResolvedValue({ id: 'job-1', format: 'pdf', status: 'queued', drillContext: {}, workspaceId: 'w', actorUserId: 'u' });
    vi.spyOn(klmApi, 'cancelFinanceExport').mockResolvedValue({ id: 'job-1', format: 'pdf', status: 'cancelled', drillContext: {}, workspaceId: 'w', actorUserId: 'u' });
    renderPanel(); fireEvent.click(screen.getByRole('button', { name: 'Exportar relatório' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Cancelar' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }));
    await waitFor(() => expect(klmApi.cancelFinanceExport).toHaveBeenCalledWith('job-1'));
  });

  it('creates a human-labelled child cost center', async () => {
    const create = vi.spyOn(klmApi, 'createFinanceCostCenter').mockResolvedValue({ id: 'center-2', workspaceId: 'w', code: 'JUR', name: 'Jurídico', active: true, version: 1, parentId: 'center-1', type: 'operational', status: 'active' });
    renderPanel();
    await screen.findAllByRole('option', { name: 'ADM · Administrativo' });
    searchFinanceDimension('Buscar responsável humano', 'Responsável');
    await screen.findAllByRole('option', { name: 'Responsável Financeiro' });
    fireEvent.change(screen.getByLabelText('Código do novo centro'), { target: { value: 'JUR' } });
    fireEvent.change(screen.getByLabelText('Nome do novo centro'), { target: { value: 'Jurídico' } });
    fireEvent.change(screen.getByLabelText('Centro pai'), { target: { value: 'center-1' } });
    fireEvent.change(screen.getByLabelText('Responsável pelo centro'), { target: { value: 'user-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Criar centro de custo' }));
    await waitFor(() => expect(create).toHaveBeenCalledWith(expect.objectContaining({ code: 'JUR', name: 'Jurídico', parentId: 'center-1', ownerUserId: 'user-1' })));
  });

  it('allocates through client, case, contract, team and responsible selectors', async () => {
    const allocate = vi.spyOn(klmApi, 'createFinanceAllocation').mockResolvedValue({ id: 'allocation-1', workspaceId: 'w', financeEntryId: 'entry-1', costCenterId: 'center-1', percentage: 100, amount: 100, version: 1, auditMetadata: {}, clientId: 'client-1', matterId: 'matter-1', contractId: 'contract-1', source: 'manual' });
    renderPanel();
    searchFinanceDimension('Buscar cliente', 'Cliente');
    searchFinanceDimension('Buscar caso', 'Caso');
    searchFinanceDimension('Buscar contrato', 'Contrato');
    searchFinanceDimension('Buscar equipe', 'Equipe');
    searchFinanceDimension('Buscar responsável humano', 'Responsável');
    searchFinanceDimension('Buscar lançamento', 'Honorários');
    await screen.findByRole('option', { name: 'Cliente Alfa' });
    fireEvent.change(screen.getByLabelText('Centro de custo'), { target: { value: 'center-1' } });
    fireEvent.change(screen.getByLabelText('Cliente'), { target: { value: 'client-1' } });
    fireEvent.change(screen.getByLabelText('Caso'), { target: { value: 'matter-1' } });
    fireEvent.change(screen.getByLabelText('Contrato'), { target: { value: 'contract-1' } });
    fireEvent.change(screen.getByLabelText('Equipe'), { target: { value: 'team-1' } });
    fireEvent.change(screen.getByLabelText('Responsável'), { target: { value: 'user-1' } });
    fireEvent.change(screen.getByLabelText('Lançamento'), { target: { value: 'entry-1' } });
    expect(await screen.findByText(/Alocação fechada/)).toBeInTheDocument();
    expect(klmApi.financeAllocationClosure).toHaveBeenCalledWith('entry-1');
    fireEvent.change(screen.getByLabelText('Valor alocado'), { target: { value: '100' } });
    fireEvent.click(screen.getByRole('button', { name: 'Registrar alocação' }));
    await waitFor(() => expect(allocate).toHaveBeenCalledWith(expect.objectContaining({ clientId: 'client-1', matterId: 'matter-1', contractId: 'contract-1', teamId: 'team-1', userId: 'user-1', financeEntryId: 'entry-1', costCenterId: 'center-1' })));
  });

  it('persists a real tax regime and exports the active drill context', async () => {
    const saveTax = vi.spyOn(klmApi, 'upsertFinanceTaxProfile').mockResolvedValue({ id: 'tax-1' });
    const createExport = vi.spyOn(klmApi, 'createFinanceExport').mockResolvedValue({ id: 'context-job', format: 'pdf', status: 'queued', drillContext: {}, workspaceId: 'w', actorUserId: 'u' });
    vi.spyOn(klmApi, 'getFinanceExport').mockResolvedValue({ id: 'context-job', format: 'pdf', status: 'queued', drillContext: {}, workspaceId: 'w', actorUserId: 'u' });
    renderPanel();
    searchFinanceDimension('Buscar cliente', 'Cliente');
    searchFinanceDimension('Buscar caso', 'Caso');
    searchFinanceDimension('Buscar contrato', 'Contrato');
    await screen.findByRole('option', { name: 'Cliente Alfa' });
    fireEvent.change(screen.getByLabelText('Centro de custo'), { target: { value: 'center-1' } });
    fireEvent.change(screen.getByLabelText('Cliente'), { target: { value: 'client-1' } });
    fireEvent.change(screen.getByLabelText('Caso'), { target: { value: 'matter-1' } });
    fireEvent.change(screen.getByLabelText('Contrato'), { target: { value: 'contract-1' } });
    fireEvent.change(screen.getByLabelText('Regime tributário'), { target: { value: 'lucro_presumido' } });
    fireEvent.change(screen.getByLabelText('Alíquota tributária'), { target: { value: '14.25' } });
    fireEvent.change(screen.getByLabelText('Município tributário'), { target: { value: 'Brasília' } });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar perfil para validação do contador' }));
    await waitFor(() => expect(saveTax).toHaveBeenCalledWith(expect.objectContaining({ regime: 'lucro_presumido', taxRate: 14.25, municipality: 'Brasília' })));
    await waitFor(() => expect(window.location.search).toContain('financeContract=contract-1'));
    fireEvent.click(screen.getByRole('button', { name: 'Exportar relatório' }));
    await waitFor(() => expect(createExport).toHaveBeenCalledWith(expect.objectContaining({ drillContext: expect.objectContaining({ costCenterId: 'center-1', clientId: 'client-1', matterId: 'matter-1', contractId: 'contract-1' }) })));
  });

  it('switches DRE between cash and accrual using the active dimensional filters', async () => {
    const dre = vi.spyOn(klmApi,'financeDimensionalDre');
    renderPanel();
    fireEvent.change(screen.getByLabelText('Regime da DRE'),{target:{value:'accrual'}});
    await waitFor(()=>expect(dre).toHaveBeenLastCalledWith(expect.objectContaining({mode:'accrual'})));
    expect(await screen.findByText('Resultado gerencial')).toBeInTheDocument();
  });

  it('renders the structured budget variance and an honest not-comparable state', async () => {
    vi.mocked(klmApi.financeDimensionalDre).mockImplementation(async ({mode}) => mode === 'cash'
      ? {mode,periodStart:'2026-01-01',periodEnd:'2026-08-02',lines:[],result:'100',budget:{revenue:'200',expenses:'80',result:'120',variance:'-20'},budgetQuality:'configured',source:'teste',generatedAt:'2026-08-02T12:00:00Z'}
      : {mode,periodStart:'2026-01-01',periodEnd:'2026-08-02',lines:[],result:'100',budget:null,budgetQuality:'not_comparable',source:'teste',generatedAt:'2026-08-02T12:00:00Z'});
    renderPanel();
    const varianceLabel=await screen.findByText('Variação realizado × orçamento');
    expect(varianceLabel.closest('article')).toHaveTextContent(/20,00/);
    fireEvent.change(screen.getByLabelText('Regime da DRE'),{target:{value:'accrual'}});
    expect(await screen.findByText(/não comparável com este período ou dimensão/i)).toBeInTheDocument();
  }, 15_000);

  it('renders monthly or annual DRE series, accumulated result and previous-period comparison', async () => {
    vi.mocked(klmApi.financeDimensionalDre).mockResolvedValue({
      mode:'cash',periodStart:'2026-07-01',periodEnd:'2026-08-31',lines:[{key:'result',label:'Resultado gerencial',amount:'1600'}],result:'1600',budget:null,budgetQuality:'not_configured',source:'teste',generatedAt:'2026-08-31T12:00:00Z',granularity:'monthly',accumulated:true,
      series:[{periodStart:'2026-07-01',periodEnd:'2026-07-31',result:'600',accumulatedResult:'600'},{periodStart:'2026-08-01',periodEnd:'2026-08-31',result:'1000',accumulatedResult:'1600'}],
      comparison:{periodStart:'2026-05-01',periodEnd:'2026-06-30',previousResult:'400',currentResult:'1600',variance:'1200',variancePercent:'300'},
    });
    const dre=vi.mocked(klmApi.financeDimensionalDre);
    renderPanel();
    expect(await screen.findByRole('table',{name:'Série temporal da DRE'})).toHaveTextContent(/1\.600,00/);
    expect(screen.getByLabelText('Comparativo com período anterior')).toHaveTextContent(/300\.0%/);
    fireEvent.change(screen.getByLabelText('Periodicidade da DRE'),{target:{value:'annual'}});
    fireEvent.click(screen.getByLabelText('Exibir DRE acumulada'));
    fireEvent.click(screen.getByLabelText('Comparar DRE ao período anterior'));
    await waitFor(()=>expect(dre).toHaveBeenLastCalledWith(expect.objectContaining({granularity:'annual',accumulated:false,comparePrevious:false})));
  });

  it('explains valuation ranges and drills into the supporting contract without exposing an id field', async () => {
    vi.mocked(klmApi.listFinanceValuations).mockResolvedValue([{id:'valuation-1',workspaceId:'w',method:'revenue_multiple',valuationDate:'2026-08-02',enterpriseValue:'3000',premiseQuality:'partial',version:1,assumptions:{range:{low:'2400',base:'3000',high:'3600'},decomposition:{netRevenue:'1000',ebitdaProxy:'350',method:'revenue_multiple',sourcePeriodStart:'2025-08-03',sourcePeriodEnd:'2026-08-02'},supportingContracts:[{contractId:'contract-1',matterId:'matter-1',label:'Contrato Alfa',allocatedRevenue:'1000'}]}}]);
    renderPanel();
    expect(await screen.findByLabelText('Faixa do valuation')).toHaveTextContent(/2\.400,00/);
    expect(screen.getByText(/EBITDA proxy:/)).toHaveTextContent(/350,00/);
    fireEvent.click(screen.getByRole('button',{name:'Explorar contrato Contrato Alfa'}));
    await waitFor(()=>expect(window.location.search).toContain('financeContract=contract-1'));
    expect(window.location.search).toContain('financeMatter=matter-1');
    expect(screen.queryByText('contract-1')).not.toBeInTheDocument();
  });

  it('restores and persists finance periods, drill pagination and budget filters in the URL', async () => {
    window.history.replaceState({},'', '/app/financeiro?financeForecastDays=180&financeForecastAsOf=2026-07-31&financeOffset=50&financeDreMode=accrual&financeDreStart=2026-02-01&financeDreEnd=2026-07-31&financeBudgetStart=2026-07-01&financeBudgetEnd=2026-07-31&financeBudgetCenter=center-1&financeBudgetSearch=Marketing&financeTaxRegime=lucro_presumido&financeTaxRate=14.25&financeTaxMunicipality=Brasilia&financeTaxEffectiveFrom=2026-01-01&financeTaxAnnualRevenue=1000000&financeTaxAnnualExpense=250000&financeTaxPassThrough=30&financeValuationMethod=revenue_multiple&financeValuationMultiple=2.5&financeValuationDiscountRate=18&financeValuationGrowthRate=4&financeValuationYears=5');
    renderPanel();
    expect(screen.getByLabelText('Dias do forecast')).toHaveValue(180);
    expect(screen.getByLabelText('Data-base do forecast')).toHaveValue('2026-07-31');
    expect(screen.getByLabelText('Regime da DRE')).toHaveValue('accrual');
    expect(screen.getByLabelText('Início da DRE')).toHaveValue('2026-02-01');
    expect(screen.getByLabelText('Fim da DRE')).toHaveValue('2026-07-31');
    expect(screen.getByLabelText('Início do orçamento')).toHaveValue('2026-07-01');
    expect(screen.getByLabelText('Fim do orçamento')).toHaveValue('2026-07-31');
    expect(screen.getByLabelText('Buscar linha orçamentária')).toHaveValue('Marketing');
    expect(screen.getByLabelText('Regime tributário')).toHaveValue('lucro_presumido');
    expect(screen.getByLabelText('Alíquota tributária')).toHaveValue('14.25');
    expect(screen.getByLabelText('Município tributário')).toHaveValue('Brasilia');
    expect(screen.getByLabelText('Receita anual da simulação')).toHaveValue('1000000');
    expect(screen.getByLabelText('Repasse de preço da simulação')).toHaveValue('30');
    expect(screen.getByLabelText('Método de valuation')).toHaveValue('revenue_multiple');
    expect(screen.getByLabelText('Múltiplo do valuation')).toHaveValue('2.5');
    await waitFor(()=>expect(klmApi.financeAnalyticsDrilldown).toHaveBeenCalledWith(expect.objectContaining({offset:50})));
    fireEvent.change(screen.getByLabelText('Dias do forecast'),{target:{value:'365'}});
    await waitFor(()=>expect(window.location.search).toContain('financeForecastDays=365'));
  });

  it('restores finance assumptions and hierarchy when browser navigation changes the URL', async () => {
    renderPanel();
    act(() => {
      window.history.pushState({},'', '/app/financeiro?financeCenter=center-1&financeClient=client-1&financeForecastDays=365&financeTaxRegime=lucro_real&financeTaxRate=22&financeValuationMethod=ebitda_multiple&financeValuationMultiple=4');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    await waitFor(() => {
      expect(screen.getByLabelText('Dias do forecast')).toHaveValue(365);
      expect(screen.getByLabelText('Regime tributário')).toHaveValue('lucro_real');
      expect(screen.getByLabelText('Alíquota tributária')).toHaveValue('22');
      expect(screen.getByLabelText('Método de valuation')).toHaveValue('ebitda_multiple');
      expect(screen.getByLabelText('Múltiplo do valuation')).toHaveValue('4');
      expect(screen.getByLabelText('Centro de custo')).toHaveValue('center-1');
      expect(screen.getByLabelText('Cliente')).toHaveValue('client-1');
    });
  });

  it('creates a browser history entry when drilling into a financial group', async () => {
    vi.mocked(klmApi.financeAnalytics).mockResolvedValue({workspaceId:'w',filters:{},items:[],groups:[{dimension:'cost_center',id:'center-1',label:'ADM · Administrativo',amount:'100'}],groupDimension:'cost_center',total:'100',totalCount:1,limit:25,offset:0,hasMore:false,generatedAt:'2026-08-03T12:00:00Z'});
    vi.mocked(klmApi.financeAnalyticsDrilldown).mockResolvedValue({workspaceId:'w',filters:{costCenterId:'center-1'},items:[],groups:[],groupDimension:'client',total:'100',totalCount:1,limit:25,offset:0,hasMore:false,coverage:{allocatedAmount:'100',unallocatedAmount:'0',reconciled:true,consolidatesDescendants:true,costCenterScopeCount:3},generatedAt:'2026-08-03T12:00:00Z'});
    const pushState=vi.spyOn(window.history,'pushState');
    renderPanel();
    fireEvent.click(await screen.findByRole('button',{name:/ADM · Administrativo.*R\$/}));
    await waitFor(()=>expect(pushState).toHaveBeenCalledWith(expect.anything(),'',expect.stringContaining('financeCenter=center-1')));
    expect(await screen.findByText(/centro selecionado e de seus 2 centros descendentes/)).toBeInTheDocument();
  });

  it('hydrates saved lookup labels beyond the first page and marks inactive or removed records without exposing ids', async () => {
    window.history.replaceState({},'', '/app/financeiro?financeClient=client-999&financeMatter=matter-removed');
    vi.mocked(klmApi.resolveFinanceLookups).mockImplementation(async (type,ids)=>({entityType:type,items:type==='client' ? [{id:ids[0],label:'Cliente histórico',inactive:true}] : []}));
    renderPanel();
    expect(await screen.findByRole('option',{name:'Cliente histórico · inativo'})).toBeInTheDocument();
    expect(await screen.findByRole('option',{name:'Registro removido ou inacessível · inativo'})).toBeInTheDocument();
    expect(screen.queryByText(/client-999|matter-removed/)).not.toBeInTheDocument();
    expect(klmApi.resolveFinanceLookups).toHaveBeenCalledWith('client',['client-999']);
    expect(klmApi.resolveFinanceLookups).toHaveBeenCalledWith('matter',['matter-removed']);
  });

  it('loads the next opaque lookup cursor without replacing previously loaded people', async () => {
    vi.mocked(klmApi.financeLookup).mockImplementation(async (type,options) => {
      const cursor=typeof options==='string' ? null : options.cursor;
      return {entityType:type,search:'',limit:50,nextCursor:type==='user' && !cursor ? 'opaque-next' : null,items:type==='user' ? cursor ? [{id:'user-51',label:'Pessoa da página seguinte'}] : [{id:'user-1',label:'Responsável Financeiro'}] : []};
    });
    renderPanel();
    searchFinanceDimension('Buscar responsável humano', 'Responsável');
    expect(await screen.findByRole('button',{name:'Carregar mais responsáveis'})).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'Carregar mais responsáveis'}));
    expect((await screen.findAllByRole('option',{name:'Pessoa da página seguinte'})).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('option',{name:'Responsável Financeiro'}).length).toBeGreaterThan(0);
    expect(klmApi.financeLookup).toHaveBeenCalledWith('user',expect.objectContaining({cursor:'opaque-next'}));
  }, 10_000);

  it('searches and paginates non-user dimensions beyond the first 50 records', async () => {
    vi.mocked(klmApi.financeLookup).mockImplementation(async (type,options) => {
      const normalized=typeof options==='string' ? {search:options,cursor:null} : options;
      const cursor=normalized.cursor;
      return {entityType:type,search:normalized.search ?? '',limit:50,nextCursor:type==='client' && !cursor ? 'client-next' : null,items:type==='client' ? cursor ? [{id:'client-51',label:'Cliente 51'}] : [{id:'client-1',label:'Cliente 1'}] : []};
    });
    renderPanel();
    fireEvent.change(await screen.findByLabelText('Buscar cliente'),{target:{value:'Cliente'}});
    await waitFor(()=>expect(klmApi.financeLookup).toHaveBeenCalledWith('client',expect.objectContaining({search:'Cliente',cursor:null,limit:50})));
    fireEvent.click(await screen.findByRole('button',{name:'Carregar mais resultados'}));
    expect(await screen.findByRole('option',{name:'Cliente 51'})).toBeInTheDocument();
    expect(screen.getByRole('option',{name:'Cliente 1'})).toBeInTheDocument();
    expect(klmApi.financeLookup).toHaveBeenCalledWith('client',expect.objectContaining({cursor:'client-next'}));
  }, 10_000);

  it('generates persisted scenarios and calculates valuation from explicit premises', async () => {
    const generate = vi.spyOn(klmApi,'generateFinanceForecasts').mockResolvedValue([]);
    const recordForecast = vi.spyOn(klmApi,'createFinanceForecast').mockResolvedValue({id:'f1',workspaceId:'w',periodStart:'2026-08-01',periodEnd:'2026-10-29',revenue:'90000',expense:'30000',assumptions:{},scenario:'base',horizon:'3y',version:1});
    const valuation = vi.spyOn(klmApi,'calculateFinanceValuation').mockResolvedValue({id:'v1',workspaceId:'w',method:'dcf_simplified',valuationDate:'2026-08-02',enterpriseValue:'1000',assumptions:{},premiseQuality:'partial',version:1});
    const recordValuation = vi.spyOn(klmApi,'createFinanceValuation').mockResolvedValue({id:'v2',workspaceId:'w',method:'dcf_simplified',valuationDate:'2026-07-31',enterpriseValue:'2500000',assumptions:{},premiseQuality:'verified',version:1});
    renderPanel();
    fireEvent.change(screen.getByLabelText('Horizonte do forecast manual'),{target:{value:'3y'}});
    fireEvent.click(screen.getByRole('button',{name:'Gerar três cenários reais'}));
    await waitFor(()=>expect(generate).toHaveBeenCalledWith(expect.objectContaining({horizon:'3y',scenarios:['conservative','base','optimistic']})));
    fireEvent.change(screen.getByLabelText('Início do forecast manual'),{target:{value:'2026-08-01'}});
    fireEvent.change(screen.getByLabelText('Fim do forecast manual'),{target:{value:'2026-10-29'}});
    fireEvent.change(screen.getByLabelText('Receita do forecast manual'),{target:{value:'90000'}});
    fireEvent.change(screen.getByLabelText('Despesa do forecast manual'),{target:{value:'30000'}});
    fireEvent.click(screen.getByRole('button',{name:'Registrar forecast manual'}));
    await waitFor(()=>expect(recordForecast).toHaveBeenCalledWith(expect.objectContaining({periodStart:'2026-08-01',periodEnd:'2026-10-29',revenue:90000,expense:30000,scenario:'base',horizon:'3y',assumptions:expect.objectContaining({source:'manual_finance_ui'})})));
    fireEvent.change(screen.getByLabelText('Data-base do valuation'),{target:{value:'2026-07-31'}});
    fireEvent.change(screen.getByLabelText('Taxa de desconto'),{target:{value:'18'}});
    fireEvent.change(screen.getByLabelText('Taxa de crescimento'),{target:{value:'4'}});
    fireEvent.click(screen.getByRole('button',{name:'Calcular valuation'}));
    await waitFor(()=>expect(valuation).toHaveBeenCalledWith(expect.objectContaining({method:'dcf_simplified',asOf:'2026-07-31',assumptions:{discountRate:18,growthRate:4,years:3}})));
    fireEvent.change(screen.getByLabelText('Valor manual da empresa'),{target:{value:'2500000'}});
    fireEvent.change(screen.getByLabelText('Qualidade do valuation manual'),{target:{value:'verified'}});
    fireEvent.click(screen.getByRole('button',{name:'Registrar valuation manual'}));
    await waitFor(()=>expect(recordValuation).toHaveBeenCalledWith(expect.objectContaining({method:'dcf_simplified',valuationDate:'2026-07-31',enterpriseValue:2500000,premiseQuality:'verified',assumptions:expect.objectContaining({source:'manual_finance_ui'})})));
  });

  it('simulates tax reform and keeps accountant review explicit', async () => {
    vi.mocked(klmApi.financeTaxProfile).mockResolvedValue({id:'tax-1',workspaceId:'w',regime:'lucro_presumido',taxRate:14,effectiveFrom:'2026-01-01',sources:[{reference:'contador'}],reformCalendar:{},settings:{},version:1});
    const simulate = vi.spyOn(klmApi,'simulateFinanceTaxReform').mockResolvedValue({regime:'lucro_presumido',currentRate:'14',currentEstimatedTax:'14000',transition:[{year:2026,rate:'15',estimatedTax:'15000',taxDelta:'1000',estimatedMargin:'65000',estimatedCashImpact:'-1000'}],quality:'simulation_requires_accountant_validation',humanReviewRequired:true,ownerAlerts:['D-180','D-90'],sources:[],generatedAt:'2026-08-02T12:00:00Z'});
    const review = vi.spyOn(klmApi,'requestFinanceTaxReview').mockResolvedValue({decisionId:'decision-1',status:'pending',assignedUserId:'accountant-1',ownerUserId:'owner-1',dueAt:'2026-08-02T16:00:00Z',routingPath:['accountant-1','owner-1'],ownerNotified:true,phoneDeliveryStatus:'pending'});
    renderPanel();
    await waitFor(()=>expect(screen.getByRole('button',{name:'Simular transição 2026–2033'})).toBeEnabled());
    fireEvent.change(screen.getByLabelText('Receita anual da simulação'),{target:{value:'100000'}});
    fireEvent.change(screen.getByLabelText('Despesa anual da simulação'),{target:{value:'20000'}});
    fireEvent.change(screen.getByLabelText('Repasse de preço da simulação'),{target:{value:'50'}});
    fireEvent.click(screen.getByRole('button',{name:'Simular transição 2026–2033'}));
    await waitFor(()=>expect(simulate).toHaveBeenCalledWith(expect.objectContaining({annualRevenue:100000,annualExpense:20000,pricePassThroughPercent:50})));
    expect(await screen.findByText('Validação humana obrigatória')).toBeInTheDocument();
    expect(screen.getByText(/contador responsável deve validar/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'Chamar contador e avisar o dono'}));
    await waitFor(()=>expect(review).toHaveBeenCalledWith(expect.objectContaining({simulation:expect.objectContaining({pricePassThroughPercent:50}),slaMinutes:240})));
    expect(await screen.findByText(/dono avisado no sistema/i)).toHaveTextContent('telefone pending');
  });

  it('searches a human owner and requires impact preview before moving or archiving a cost center', async () => {
    vi.mocked(klmApi.financeCostCenterImpact).mockResolvedValue({costCenterId:'center-1',version:1,childCount:1,allocationCount:5,allocatedAmount:'3100',requiresConfirmation:true});
    const impact=vi.mocked(klmApi.previewFinanceCostCenterImpact).mockResolvedValue({costCenterId:'center-1',version:3,childCount:2,allocationCount:7,allocatedAmount:'4200',requiresConfirmation:true,proposed:{status:'archived'},expectedVersion:1,eligible:true});
    const update=vi.spyOn(klmApi,'updateFinanceCostCenter').mockResolvedValue({id:'center-1',workspaceId:'w',code:'ADM',name:'Administrativo',active:false,version:4,type:'operational',status:'archived'});
    renderPanel();
    await screen.findAllByRole('option',{name:'ADM · Administrativo'});
    fireEvent.change(screen.getByLabelText('Buscar responsável humano'),{target:{value:'Responsável'}});
    await waitFor(()=>expect(klmApi.financeLookup).toHaveBeenCalledWith('user',expect.objectContaining({search:'Responsável'})));
    fireEvent.change(screen.getByLabelText('Centro para atualizar'),{target:{value:'center-1'}});
    expect(await screen.findByText('Impacto atual')).toBeInTheDocument();
    expect(screen.getByText('1 centros filhos')).toBeInTheDocument();
    expect(screen.getByText(/R\$ 3\.100,00 alocados/)).toBeInTheDocument();
    expect(klmApi.financeCostCenterImpact).toHaveBeenCalledWith('center-1');
    expect(screen.queryByRole('button',{name:'Confirmar alteração'})).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Novo estado do centro'),{target:{value:'archived'}});
    fireEvent.click(screen.getByRole('button',{name:'Ver prévia de impacto'}));
    await waitFor(()=>expect(impact).toHaveBeenCalledWith('center-1',expect.objectContaining({patch:expect.objectContaining({status:'archived'}),expectedVersion:1})));
    expect(await screen.findByText('2 centros filhos')).toBeInTheDocument();
    expect(screen.getByText(/R\$ 4\.200,00 historicamente alocados/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'Confirmar alteração'}));
    await waitFor(()=>expect(update).toHaveBeenCalledWith('center-1',expect.objectContaining({status:'archived',expectedVersion:3})));
  });

  it('creates and lists an auditable budget line using human labels', async () => {
    const create=vi.spyOn(klmApi,'createFinanceBudgetLine').mockResolvedValue({id:'budget-1',workspaceId:'w',periodStart:'2026-08-01',periodEnd:'2026-08-31',costCenterId:'center-1',category:'Marketing',entryType:'expense',amount:'1500',assumptions:{premise:'Campanha aprovada'},status:'active',version:1});
    vi.mocked(klmApi.listFinanceBudgetLines).mockResolvedValue([{id:'budget-1',workspaceId:'w',periodStart:'2026-08-01',periodEnd:'2026-08-31',costCenterId:'center-1',category:'Marketing',entryType:'expense',amount:'1500',assumptions:{premise:'Campanha aprovada'},status:'active',version:1}]);
    renderPanel();
    expect(await screen.findByText('Marketing')).toBeInTheDocument();
    expect(screen.getByText('Administrativo')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Centro orçado'),{target:{value:'center-1'}});
    fireEvent.change(screen.getByLabelText('Categoria orçamentária'),{target:{value:'Marketing'}});
    fireEvent.change(screen.getByLabelText('Valor orçado'),{target:{value:'1500'}});
    fireEvent.change(screen.getByLabelText('Premissa do orçamento'),{target:{value:'Campanha aprovada'}});
    fireEvent.click(screen.getByRole('button',{name:'Adicionar linha orçamentária'}));
    await waitFor(()=>expect(create).toHaveBeenCalledWith(expect.objectContaining({costCenterId:'center-1',category:'Marketing',entryType:'expense',amount:1500,assumptions:{premise:'Campanha aprovada',source:'finance-ui'}})));
  });

  it('opens an authorized financial evidence URL without exposing its internal reference', async () => {
    vi.mocked(klmApi.financeAnalytics).mockResolvedValue({
      workspaceId:'w', filters:{}, groups:[], groupDimension:'cost_center', total:'100', totalCount:1, limit:25, offset:0, hasMore:false, generatedAt:'2026-08-04T12:00:00Z',
      items:[{allocationId:'allocation-1',entryId:'entry-1',entryLabel:'Honorários comprovados',costCenterId:'center-1',costCenterLabel:'ADM',clientLabel:'Cliente Alfa',contractLabel:'Contrato Alfa',matterLabel:'Caso Alfa',percentage:'100',amount:'100',source:'manual',evidenceAvailable:true,evidenceRef:'private/evidence/ref-1'}],
    });
    vi.spyOn(klmApi, 'openFinanceEvidence').mockResolvedValue({ downloadUrl:'https://private.example/signed', expiresAt:'2026-08-04T12:05:00Z' });
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Abrir comprovante · Honorários comprovados' }));
    await waitFor(() => expect(klmApi.openFinanceEvidence).toHaveBeenCalledWith('private/evidence/ref-1'));
    expect(open).toHaveBeenCalledWith('https://private.example/signed', '_blank', 'noopener,noreferrer');
    expect(screen.queryByText('private/evidence/ref-1')).not.toBeInTheDocument();
  });

  it.each([
    ['403 não autorizado', Object.assign(new Error('Forbidden'), { status: 403 })],
    ['404 removido', Object.assign(new Error('Not found'), { status: 404 })],
    ['serviço indisponível', Object.assign(new Error('Unavailable'), { status: 503 })],
  ])('fails closed when financial evidence is %s', async (_case, error) => {
    vi.mocked(klmApi.financeAnalytics).mockResolvedValue({
      workspaceId:'w', filters:{}, groups:[], groupDimension:'cost_center', total:'100', totalCount:1, limit:25, offset:0, hasMore:false, generatedAt:'2026-08-04T12:00:00Z',
      items:[{allocationId:'allocation-1',entryId:'entry-1',entryLabel:'Honorários comprovados',costCenterId:'center-1',costCenterLabel:'ADM',clientLabel:'Cliente Alfa',contractLabel:'Contrato Alfa',matterLabel:'Caso Alfa',percentage:'100',amount:'100',source:'manual',evidenceAvailable:true,evidenceRef:'private/ref'}],
    });
    vi.spyOn(klmApi, 'openFinanceEvidence').mockRejectedValue(error);
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Abrir comprovante · Honorários comprovados' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Comprovante indisponível ou não autorizado.');
    expect(open).not.toHaveBeenCalled();
  });
});
