import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { useBlackboxManifest } from '@/blackbox/session-manifest';
import { PlatformEntityCombobox } from '@/components/platform/PlatformEntityCombobox';
import { MobileDataCard, ResponsiveDataExplorer } from '@/components/responsive';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  cancelPlatformWorkspaceFinanceExport,
  createPlatformWorkspaceFinanceBudgetLine,
  createPlatformWorkspaceFinanceExport,
  downloadPlatformWorkspaceFinanceExport,
  fetchPlatformWorkspaceFinanceDrilldown,
  fetchPlatformWorkspaceFinanceDre,
  fetchPlatformWorkspaceFinanceExecutive,
  generatePlatformWorkspaceFinanceForecast,
  calculatePlatformWorkspaceFinanceValuation,
  fetchPlatformWorkspaceFinanceInsights,
  getPlatformWorkspaceFinanceTaxProfile,
  updatePlatformWorkspaceFinanceTaxProfile,
  listPlatformWorkspaceFinanceBudgetLines,
  simulatePlatformWorkspaceFinanceTaxReform,
  getPlatformWorkspaceFinanceExport,
  listPlatformWorkspaceFinanceExports,
} from '@/services/platform';

type Metric = {
  value?: string | number | null;
  quality?: string;
  source?: string;
  definition?: string;
  unit?: string;
};

type DrillGroup = {
  dimension: 'cost_center' | 'client' | 'contract' | 'matter' | 'entry';
  id: string | null;
  label: string;
  amount: string | number;
};

type DrillItem = {
  entryId: string;
  entryLabel: string;
  costCenterLabel: string;
  clientLabel: string;
  contractLabel: string;
  matterLabel: string;
  amount: string | number;
  source: string;
};

type DrillResponse = {
  filters?: Record<string, string | null>;
  total?: string | number;
  groupDimension?: DrillGroup['dimension'];
  groups?: DrillGroup[];
  items?: DrillItem[];
  totalCount?: number;
  hasMore?: boolean;
  coverage?: {
    allocatedAmount?: string | number;
    unallocatedAmount?: string | number;
    reconciled?: boolean;
  };
};

type ExportJob = {
  id: string;
  format: 'pdf' | 'xlsx' | 'docx' | string;
  status: string;
  progress?: number;
  rowCount?: number | null;
  expiresAt?: string | null;
  errorDetail?: string | null;
};

type ExportPage = { items: ExportJob[]; total: number; limit: number; offset: number; hasMore: boolean };
function normalizeExportPage(value: unknown): ExportPage {
  if (Array.isArray(value)) return { items: value as ExportJob[], total: value.length, limit: value.length || 50, offset: 0, hasMore: false };
  const page = (value ?? {}) as Partial<ExportPage>;
  return { items: Array.isArray(page.items) ? page.items : [], total: Number(page.total ?? 0), limit: Number(page.limit ?? 50), offset: Number(page.offset ?? 0), hasMore: Boolean(page.hasMore) };
}

const KPI_LABELS: Record<string, string> = {
  arr: 'ARR',
  contractedRevenue: 'Receita contratada',
  grossMargin: 'Margem bruta',
  contributionMargin: 'Margem de contribuição',
  ebitda: 'EBITDA gerencial',
  burn: 'Burn rate',
  runway: 'Runway',
  breakEven: 'Ponto de equilíbrio',
  concentration: 'Concentração de receita',
  dimensionalProfitability: 'Rentabilidade dimensional',
  cac: 'CAC',
  ltv: 'LTV',
  payback: 'Payback',
};

const FILTER_BY_DIMENSION: Record<DrillGroup['dimension'], string | null> = {
  cost_center: 'cost_center_id',
  client: 'client_id',
  contract: 'contract_id',
  matter: 'matter_id',
  entry: null,
};

function formatMetric(metric: Metric): string {
  if (metric.value === null || metric.value === undefined) return 'Dados insuficientes';
  const value = Number(metric.value);
  if (!Number.isFinite(value)) return String(metric.value);
  if (metric.unit === 'BRL' || metric.unit === 'brl') {
    return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(value);
  }
  if (metric.unit === '%' || metric.unit === 'pct') return `${value.toLocaleString('pt-BR')}%`;
  return value.toLocaleString('pt-BR');
}

function formatMoney(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return 'Dados insuficientes';
  const amount = Number(value);
  if (!Number.isFinite(amount)) return 'Dados insuficientes';
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(
    amount,
  );
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function asRecords(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(asRecord).filter((item) => Object.keys(item).length > 0) : [];
}

function humanLabel(value: string): string {
  const labels: Record<string, string> = {
    conservative: 'Conservador', base: 'Base', optimistic: 'Otimista',
    revenue_multiple: 'Múltiplo de receita', ebitda_multiple: 'Múltiplo de EBITDA',
    dcf_simplified: 'Fluxo de caixa descontado simplificado',
    simulation_requires_accountant_validation: 'Requer validação do contador',
  };
  return labels[value] ?? value.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase());
}

function AssumptionsTable({ value }: { value: unknown }) {
  const entries = Object.entries(asRecord(value)).filter(([, item]) =>
    item === null || ['string', 'number', 'boolean'].includes(typeof item),
  );
  if (!entries.length) return <p className="text-sm text-muted-foreground">Premissas não informadas.</p>;
  return <dl className="grid gap-2 sm:grid-cols-2">{entries.map(([key, item]) => <div key={key} className="rounded border p-3"><dt className="text-xs text-muted-foreground">{humanLabel(key)}</dt><dd className="mt-1 text-sm font-medium">{item === null ? 'Não informado' : typeof item === 'boolean' ? (item ? 'Sim' : 'Não') : String(item)}</dd></div>)}</dl>;
}

function DreOperationalView({ value }: { value: unknown }) {
  const data = asRecord(value);
  const lines = asRecords(data.lines);
  const comparison = asRecord(data.comparison);
  const budget = asRecord(data.budget);
  const series = asRecords(data.series);
  return <div className="mt-3 space-y-4">
    <div className="grid gap-2 sm:grid-cols-3">
      <article className="rounded border p-3"><p className="text-xs text-muted-foreground">Resultado gerencial</p><p className="mt-1 font-semibold">{formatMoney(data.result as string | number)}</p></article>
      <article className="rounded border p-3"><p className="text-xs text-muted-foreground">Período</p><p className="mt-1 text-sm font-medium">{String(data.periodStart ?? '—')} a {String(data.periodEnd ?? '—')}</p></article>
      <article className="rounded border p-3"><p className="text-xs text-muted-foreground">Qualidade orçamentária</p><p className="mt-1 text-sm font-medium">{humanLabel(String(data.budgetQuality ?? 'não informada'))}</p></article>
    </div>
    {lines.length ? <ResponsiveDataExplorer label="Linhas da DRE" mobile={<div className="grid gap-2">{lines.map((line) => <MobileDataCard key={String(line.key)} title={String(line.label)} subtitle={formatMoney(line.amount as string | number)} />)}</div>}><div className="overflow-x-auto rounded border"><table className="w-full text-sm"><thead className="bg-muted/60 text-left text-xs"><tr><th className="p-3">Linha</th><th className="p-3 text-right">Valor</th></tr></thead><tbody>{lines.map((line) => <tr key={String(line.key)} className="border-t"><td className="p-3">{String(line.label)}</td><td className="p-3 text-right font-medium">{formatMoney(line.amount as string | number)}</td></tr>)}</tbody></table></div></ResponsiveDataExplorer> : <p className="text-sm text-muted-foreground">Sem linhas para o período.</p>}
    {Object.keys(comparison).length ? <section><h3 className="mb-2 text-sm font-semibold">Comparação com período anterior</h3><div className="grid gap-2 sm:grid-cols-3"><p className="rounded border p-3 text-sm">Atual: <strong>{formatMoney(comparison.currentResult as string | number)}</strong></p><p className="rounded border p-3 text-sm">Anterior: <strong>{formatMoney(comparison.previousResult as string | number)}</strong></p><p className="rounded border p-3 text-sm">Variação: <strong>{formatMoney(comparison.variance as string | number)}</strong>{comparison.variancePercent != null ? ` (${comparison.variancePercent}%)` : ''}</p></div></section> : null}
    {Object.keys(budget).length ? <section><h3 className="mb-2 text-sm font-semibold">Orçado versus realizado</h3><div className="grid gap-2 sm:grid-cols-4">{Object.entries(budget).map(([key, amount]) => <p key={key} className="rounded border p-3 text-sm">{humanLabel(key)}: <strong>{formatMoney(amount as string | number)}</strong></p>)}</div></section> : null}
    {series.length ? <section><h3 className="mb-2 text-sm font-semibold">Série do resultado</h3><div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{series.map((item) => <article key={String(item.periodStart)} className="rounded border p-3 text-sm"><p className="text-xs text-muted-foreground">{String(item.periodStart)} a {String(item.periodEnd)}</p><p className="mt-1">Resultado: <strong>{formatMoney(item.result as string | number)}</strong></p><p>Acumulado: <strong>{formatMoney(item.accumulatedResult as string | number)}</strong></p></article>)}</div></section> : null}
    <p className="text-xs text-muted-foreground">Fonte: {String(data.source ?? 'não informada')} · Atualizado em {data.generatedAt ? new Date(String(data.generatedAt)).toLocaleString('pt-BR') : 'horário indisponível'}.</p>
  </div>;
}

function ForecastOperationalView({ value }: { value: unknown }) {
  const scenarios = asRecords(value);
  return <div className="mt-3 space-y-3">{scenarios.length ? <div className="grid gap-3 sm:grid-cols-3">{scenarios.map((scenario) => <article key={String(scenario.id ?? scenario.scenario)} className="rounded border p-3"><div className="flex items-center justify-between gap-2"><h3 className="font-medium">{humanLabel(String(scenario.scenario ?? 'cenário'))}</h3><Badge variant="outline">{String(scenario.horizon ?? 'período não informado')}</Badge></div><p className="mt-3 text-sm">Receita: <strong>{formatMoney(scenario.revenue as string | number)}</strong></p><p className="text-sm">Despesa: <strong>{formatMoney(scenario.expense as string | number)}</strong></p><p className="text-sm">Saldo projetado: <strong>{formatMoney(Number(scenario.revenue ?? 0) - Number(scenario.expense ?? 0))}</strong></p><p className="mt-2 text-xs text-muted-foreground">{String(scenario.periodStart ?? '—')} a {String(scenario.periodEnd ?? '—')}</p><details className="mt-3"><summary className="cursor-pointer text-xs font-medium">Premissas</summary><div className="mt-2"><AssumptionsTable value={scenario.assumptions} /></div></details></article>)}</div> : <p className="text-sm text-muted-foreground">Nenhum cenário foi gerado.</p>}</div>;
}

function ValuationOperationalView({ value }: { value: unknown }) {
  const data = asRecord(value);
  const assumptions = asRecord(data.assumptions);
  const range = asRecord(assumptions.range);
  const decomposition = asRecord(assumptions.decomposition);
  const contracts = asRecords(assumptions.supportingContracts);
  return <div className="mt-3 space-y-4"><div className="grid gap-2 sm:grid-cols-4"><article className="rounded border p-3"><p className="text-xs text-muted-foreground">Valor-base</p><p className="mt-1 font-semibold">{formatMoney(data.enterpriseValue as string | number)}</p></article><article className="rounded border p-3"><p className="text-xs text-muted-foreground">Faixa baixa</p><p className="mt-1 font-semibold">{formatMoney(range.low as string | number)}</p></article><article className="rounded border p-3"><p className="text-xs text-muted-foreground">Faixa alta</p><p className="mt-1 font-semibold">{formatMoney(range.high as string | number)}</p></article><article className="rounded border p-3"><p className="text-xs text-muted-foreground">Qualidade</p><p className="mt-1 text-sm font-medium">{humanLabel(String(data.premiseQuality ?? 'não informada'))}</p></article></div><section><h3 className="mb-2 text-sm font-semibold">Composição</h3><AssumptionsTable value={decomposition} /></section><section><h3 className="mb-2 text-sm font-semibold">Premissas do método</h3><AssumptionsTable value={assumptions} /></section>{contracts.length ? <section><h3 className="mb-2 text-sm font-semibold">Contratos que sustentam a estimativa</h3><div className="grid gap-2 sm:grid-cols-2">{contracts.map((contract) => <article key={String(contract.contractId)} className="rounded border p-3"><p className="text-sm font-medium">{String(contract.label)}</p><p className="text-xs text-muted-foreground">Receita alocada: {formatMoney(contract.allocatedRevenue as string | number)}</p></article>)}</div></section> : null}<p className="text-xs text-muted-foreground">Método: {humanLabel(String(data.method ?? decomposition.method ?? 'não informado'))} · Data-base: {String(data.valuationDate ?? '—')}.</p></div>;
}

function TaxReformOperationalView({ value }: { value: unknown }) {
  const data = asRecord(value);
  const transition = asRecords(data.transition);
  const impact = asRecord(data.impact);
  const sources = asRecords(data.sources);
  return <div className="space-y-4"><Alert><AlertTitle>Revisão humana obrigatória</AlertTitle><AlertDescription>Esta simulação é orientativa e deve ser validada pelo contador antes de qualquer decisão fiscal ou comercial.</AlertDescription></Alert><div className="grid gap-2 sm:grid-cols-3"><p className="rounded border p-3 text-sm">Impacto em preço: <strong>{formatMoney(impact.priceImpact as string | number)}</strong></p><p className="rounded border p-3 text-sm">Margem estimada: <strong>{formatMoney(impact.marginImpact as string | number)}</strong></p><p className="rounded border p-3 text-sm">Impacto de caixa: <strong>{formatMoney(impact.cashImpact as string | number)}</strong></p></div>{transition.length ? <ResponsiveDataExplorer label="Transição tributária" mobile={<div className="grid gap-2">{transition.map((item) => <MobileDataCard key={String(item.year)} title={String(item.year)} subtitle={`${String(item.rate)}% · ${formatMoney(item.estimatedTax as string | number)}`} />)}</div>}><div className="overflow-x-auto rounded border"><table className="w-full min-w-[700px] text-sm"><thead className="bg-muted/60 text-left text-xs"><tr><th className="p-3">Ano</th><th className="p-3 text-right">Alíquota</th><th className="p-3 text-right">Tributo</th><th className="p-3 text-right">Variação</th><th className="p-3 text-right">Margem</th><th className="p-3 text-right">Caixa</th></tr></thead><tbody>{transition.map((item) => <tr key={String(item.year)} className="border-t"><td className="p-3">{String(item.year)}</td><td className="p-3 text-right">{String(item.rate)}%</td><td className="p-3 text-right">{formatMoney(item.estimatedTax as string | number)}</td><td className="p-3 text-right">{formatMoney(item.taxDelta as string | number)}</td><td className="p-3 text-right">{formatMoney(item.estimatedMargin as string | number)}</td><td className="p-3 text-right">{formatMoney(item.estimatedCashImpact as string | number)}</td></tr>)}</tbody></table></div></ResponsiveDataExplorer> : <p className="text-sm text-muted-foreground">Nenhuma etapa de transição calculada.</p>}<div className="flex flex-wrap gap-2">{(Array.isArray(data.ownerAlerts) ? data.ownerAlerts : []).map((alert) => <Badge key={String(alert)} variant="secondary">Avisar dono {String(alert)}</Badge>)}</div>{sources.length ? <section><h3 className="mb-2 text-sm font-semibold">Fontes informadas</h3><ul className="space-y-1 text-sm">{sources.map((source, index) => <li key={String(source.url ?? index)}>{String(source.label ?? source.url ?? 'Fonte sem rótulo')}</li>)}</ul></section> : <p className="text-xs text-muted-foreground">Nenhuma fonte foi informada no perfil tributário.</p>}<p className="text-xs text-muted-foreground">Qualidade: {humanLabel(String(data.quality ?? 'não informada'))} · Regime: {humanLabel(String(data.regime ?? 'não configurado'))}.</p></div>;
}

export default function BlackboxFinance() {
  const { manifest } = useBlackboxManifest();
  const queryClient = useQueryClient();
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [drillFilters, setDrillFilters] = useState<Record<string, string>>({});
  const [drillOffset, setDrillOffset] = useState(0);
  const [exportFormat, setExportFormat] = useState<'pdf' | 'xlsx' | 'docx'>('pdf');
  const [selectedExportId, setSelectedExportId] = useState<string | null>(null);
  const [exportOffset, setExportOffset] = useState(0);
  const [financeView, setFinanceView] = useState<'overview' | 'dre' | 'forecast' | 'valuation' | 'tax' | 'budget' | 'insights'>('overview');
  const [taxRevenue, setTaxRevenue] = useState('0');
  const [taxExpense, setTaxExpense] = useState('0');
  const [taxRegime, setTaxRegime] = useState('simples_anexo_iv');
  const [taxRate, setTaxRate] = useState('');
  const [taxEffectiveFrom, setTaxEffectiveFrom] = useState(new Date().toISOString().slice(0, 10));
  const [taxMunicipality, setTaxMunicipality] = useState('');
  const [taxSource, setTaxSource] = useState('');
  const [budgetCategory, setBudgetCategory] = useState('');
  const [budgetAmount, setBudgetAmount] = useState('');
  const [budgetType, setBudgetType] = useState<'receivable' | 'expense' | 'refund'>('expense');
  const [budgetPeriodStart, setBudgetPeriodStart] = useState(new Date().toISOString().slice(0, 10));
  const [budgetPeriodEnd, setBudgetPeriodEnd] = useState(new Date().toISOString().slice(0, 10));
  const [budgetAssumption, setBudgetAssumption] = useState('');
  const DRILL_LIMIT = 50;
  const canView = manifest.isPlatformRoot || manifest.permissions.some(
    (permission) => permission.module === 'finance' && permission.action === 'view',
  );
  const canExportPermission = manifest.isPlatformRoot || manifest.permissions.some(
    (permission) => permission.module === 'finance' && permission.action === 'export',
  );
  const session = manifest.supportSession && typeof manifest.supportSession === 'object' ? manifest.supportSession as Record<string, unknown> : null;
  const sessionType = session && (session.type ?? session.sessionType);
  const sessionWorkspaceId = session && String(session.workspaceId ?? '');
  const hasTenantSession = Boolean(workspaceId && (sessionType === 'root_direct' || sessionType === 'support_approved') && sessionWorkspaceId === workspaceId);
  const canReadTenant = canView && hasTenantSession;
  const canMutateTenant = hasTenantSession && (manifest.isPlatformRoot || manifest.permissions.some((permission) => permission.module === 'finance' && permission.action === 'manage'));
  const canExport = canExportPermission && hasTenantSession;

  const analytics = useQuery({
    queryKey: ['blackbox-finance-executive', workspaceId],
    queryFn: () => fetchPlatformWorkspaceFinanceExecutive(workspaceId as string),
    enabled: Boolean(workspaceId && canReadTenant),
  });
  const drilldown = useQuery({
    queryKey: ['blackbox-finance-drilldown', workspaceId, drillFilters, drillOffset],
    queryFn: () => fetchPlatformWorkspaceFinanceDrilldown(workspaceId as string, {
      ...drillFilters,
      limit: DRILL_LIMIT,
      offset: drillOffset,
    }) as Promise<DrillResponse>,
    enabled: Boolean(workspaceId && canReadTenant),
  });
  const dre = useQuery({
    queryKey: ['blackbox-finance-dre', workspaceId],
    queryFn: () => fetchPlatformWorkspaceFinanceDre(workspaceId as string, { mode: 'accrual', period_start: new Date().toISOString().slice(0, 8) + '01', period_end: new Date().toISOString().slice(0, 10), granularity: 'monthly', accumulated: true, compare_previous: true }),
    enabled: Boolean(workspaceId && canReadTenant && financeView === 'dre'),
  });
  const forecasts = useMutation({ mutationFn: () => generatePlatformWorkspaceFinanceForecast(workspaceId as string, { as_of: new Date().toISOString().slice(0, 10), horizon: '90d', scenarios: ['conservative', 'base', 'optimistic'] }) });
  const valuation = useMutation({ mutationFn: () => calculatePlatformWorkspaceFinanceValuation(workspaceId as string, { method: 'revenue_multiple', as_of: new Date().toISOString().slice(0, 10), assumptions: { multiple: 3 } }) });
  const taxProfile = useQuery({ queryKey: ['blackbox-finance-tax-profile', workspaceId], queryFn: () => getPlatformWorkspaceFinanceTaxProfile(workspaceId as string), enabled: Boolean(workspaceId && canReadTenant && financeView === 'tax') });
  const taxReform = useMutation({ mutationFn: () => simulatePlatformWorkspaceFinanceTaxReform(workspaceId as string, { annual_revenue: Number(taxRevenue) || 0, annual_expense: Number(taxExpense) || 0, transition_rates: { '2026': 0, '2027': 0 } }) });
  const taxProfileMutation = useMutation({ mutationFn: () => updatePlatformWorkspaceFinanceTaxProfile(workspaceId as string, { regime: taxRegime, tax_rate: Number(taxRate), effective_from: taxEffectiveFrom, municipality: taxMunicipality || null, sources: [{ label: 'Fonte informada pelo operador', url: taxSource.trim() }], reform_calendar: {}, settings: {} }), onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ['blackbox-finance-tax-profile', workspaceId] }); } });
  const budgetLines = useQuery({ queryKey: ['blackbox-finance-budget-lines', workspaceId], queryFn: () => listPlatformWorkspaceFinanceBudgetLines(workspaceId as string), enabled: Boolean(workspaceId && canReadTenant && financeView === 'budget') });
  const canManage = canMutateTenant;
  const budgetMutation = useMutation({
    mutationFn: () => createPlatformWorkspaceFinanceBudgetLine(workspaceId as string, {
      period_start: budgetPeriodStart,
      period_end: budgetPeriodEnd,
      category: budgetCategory.trim() || null,
      entry_type: budgetType,
      amount: Number(budgetAmount),
      assumptions: { source: 'blackbox', premise: budgetAssumption.trim() },
    }),
      onSuccess: async () => { setBudgetCategory(''); setBudgetAmount(''); setBudgetAssumption(''); await queryClient.invalidateQueries({ queryKey: ['blackbox-finance-budget-lines', workspaceId] }); },
  });
  const insights = useQuery({ queryKey: ['blackbox-finance-insights', workspaceId], queryFn: () => fetchPlatformWorkspaceFinanceInsights(workspaceId as string), enabled: Boolean(workspaceId && canReadTenant && financeView === 'insights') });
  const selectedExport = useQuery({
    queryKey: ['blackbox-finance-export', workspaceId, selectedExportId],
    queryFn: () => getPlatformWorkspaceFinanceExport(workspaceId as string, selectedExportId as string) as Promise<ExportJob>,
    enabled: Boolean(workspaceId && selectedExportId && canExport),
    refetchInterval: (query) => ['queued', 'running'].includes(String((query.state.data as ExportJob | undefined)?.status ?? '')) ? 2_000 : false,
  });
  const exportsQuery = useQuery({
    queryKey: ['blackbox-finance-exports', workspaceId, exportOffset],
    queryFn: () => listPlatformWorkspaceFinanceExports(workspaceId as string, { limit: 50, offset: exportOffset }),
    enabled: Boolean(workspaceId && canExport),
    refetchInterval: (query) => {
      const jobs = normalizeExportPage(query.state.data).items;
      return jobs.some((job) => ['queued', 'running'].includes(job.status)) ? 2_000 : false;
    },
  });

  const exportMutation = useMutation({
    mutationFn: () => createPlatformWorkspaceFinanceExport(workspaceId as string, {
      format: exportFormat,
      // The analytics endpoint accepts snake_case query parameters, while the
      // persisted export contract is intentionally camelCase.  Keep this
      // conversion at the UI boundary so an export never silently falls back
      // to the workspace-wide report after the operator drilled into a row.
      drill_context: {
        source: 'blackbox-finance',
        costCenterId: drillFilters.cost_center_id ?? null,
        clientId: drillFilters.client_id ?? null,
        contractId: drillFilters.contract_id ?? null,
        matterId: drillFilters.matter_id ?? null,
      },
    }),
    onSuccess: async (job) => {
      setSelectedExportId(String((job as Record<string, unknown>).id ?? ''));
      await queryClient.invalidateQueries({ queryKey: ['blackbox-finance-exports', workspaceId] });
    },
  });
  const cancelMutation = useMutation({
    mutationFn: (jobId: string) => cancelPlatformWorkspaceFinanceExport(workspaceId as string, jobId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['blackbox-finance-exports', workspaceId] });
    },
  });
  const downloadMutation = useMutation({
    mutationFn: (job: ExportJob) => downloadPlatformWorkspaceFinanceExport(
      workspaceId as string,
      job.id,
      job.format,
    ),
  });

  const kpis = useMemo(() => {
    const data = analytics.data ?? {};
    return Object.entries(KPI_LABELS)
      .map(([key, label]) => ({ key, label, metric: data[key] as Metric | undefined }))
      .filter((item) => item.metric && typeof item.metric === 'object');
  }, [analytics.data]);
  const drill = drilldown.data;
  const activeBreadcrumbs = Object.entries(drillFilters);

  const descend = (group: DrillGroup) => {
    const filter = FILTER_BY_DIMENSION[group.dimension];
    if (!filter || !group.id) return;
    setDrillOffset(0);
    setDrillFilters((current) => ({ ...current, [filter]: group.id as string }));
  };

  if (!canView) {
    return <Alert variant="destructive"><AlertTitle>Financeiro não autorizado</AlertTitle><AlertDescription>Seu papel não possui finance:view. A API aplica a mesma decisão.</AlertDescription></Alert>;
  }

  return (
    <main className="space-y-5 p-4 sm:p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Financeiro Executivo 360</h1>
          <p className="text-sm text-muted-foreground">Conta explícita, ator administrativo preservado e nenhum contexto tenant implícito.</p>
        </div>
        {workspaceId ? <Button asChild variant="outline"><Link to={`/blackbox/workspaces?workspaceId=${encodeURIComponent(workspaceId)}`}>Abrir Conta 360</Link></Button> : null}
      </header>

      <section className="max-w-xl rounded-lg border p-4" aria-label="Contexto financeiro da conta">
        <PlatformEntityCombobox
          entityType="accounts"
          purpose="platform_finance_context"
          value={workspaceId}
          onChange={(value) => { setWorkspaceId(value); setDrillFilters({}); setDrillOffset(0); setSelectedExportId(null); }}
          label="Conta"
          placeholder="Pesquisar e selecionar conta"
          allowClear
        />
      </section>

      {workspaceId && !hasTenantSession ? <Alert><AlertTitle>Sessão administrativa necessária</AlertTitle><AlertDescription>Abra a Conta 360 para iniciar acesso direto root ou solicitar uma sessão aprovada antes de executar alterações.</AlertDescription><Button asChild variant="outline" className="mt-2"><Link to={`/blackbox/workspaces?workspaceId=${encodeURIComponent(workspaceId)}`}>Abrir Conta 360</Link></Button></Alert> : null}
      {!workspaceId ? <p className="text-sm text-muted-foreground">Selecione uma conta para consultar KPIs, drill-down e exportações.</p> : null}
      {canReadTenant && analytics.isPending ? <p role="status">Carregando KPIs…</p> : null}
      {canReadTenant && analytics.isError ? <Alert variant="destructive"><AlertTitle>Financeiro indisponível</AlertTitle><AlertDescription>Não foi possível consultar esta conta. Nenhum valor foi convertido em zero.</AlertDescription></Alert> : null}
      {canReadTenant && analytics.data ? (
        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="KPIs financeiros">
          {kpis.length ? kpis.map(({ key, label, metric }) => (
            <article key={key} className="rounded-lg border p-4">
              <div className="flex items-start justify-between gap-2"><p className="text-xs text-muted-foreground">{label}</p><Badge variant="outline">{metric?.quality ?? 'qualidade indisponível'}</Badge></div>
              <p className="mt-2 text-lg font-semibold">{formatMetric(metric ?? {})}</p>
              <p className="mt-2 text-[11px] text-muted-foreground">{metric?.definition ?? 'Definição indisponível.'}</p>
              <p className="mt-1 text-[10px] text-muted-foreground">Fonte: {metric?.source ?? 'não informada'}</p>
            </article>
          )) : <p className="rounded-lg border p-4 text-sm text-muted-foreground sm:col-span-2 xl:col-span-4">Nenhum KPI financeiro disponível para esta conta.</p>}
        </section>
      ) : null}

      {workspaceId && hasTenantSession ? <div className="flex flex-wrap gap-2" role="tablist" aria-label="Análises financeiras avançadas">
        {(['overview', 'dre', 'forecast', 'valuation', 'tax', 'budget', 'insights'] as const).map((view) => <Button key={view} role="tab" aria-selected={financeView === view} variant={financeView === view ? 'default' : 'outline'} size="sm" onClick={() => setFinanceView(view)}>{view === 'overview' ? 'Visão geral' : view === 'dre' ? 'DRE' : view === 'forecast' ? 'Projeções' : view === 'valuation' ? 'Valuation' : view === 'tax' ? 'Tributação e reforma' : view === 'budget' ? 'Orçamento' : 'Insights'}</Button>)}
      </div> : null}
      {workspaceId && hasTenantSession && financeView === 'dre' ? <section className="rounded-lg border p-4" aria-label="DRE financeiro"><h2 className="font-semibold">DRE por competência</h2>{dre.isPending ? <p role="status">Calculando DRE…</p> : dre.isError ? <p role="alert">DRE indisponível.</p> : dre.data ? <DreOperationalView value={dre.data} /> : <p className="mt-3 text-sm text-muted-foreground">Dados insuficientes para a DRE.</p>}</section> : null}
      {workspaceId && hasTenantSession && financeView === 'forecast' ? <section className="rounded-lg border p-4" aria-label="Projeções financeiras"><h2 className="font-semibold">Projeções de caixa</h2><p className="mt-1 text-xs text-muted-foreground">Cenários persistidos, com premissas e saldo projetado para 90 dias.</p><Button className="mt-3 min-h-11" onClick={() => forecasts.mutate()} disabled={forecasts.isPending || !canManage}>{forecasts.isPending ? 'Calculando…' : canManage ? 'Gerar cenários 90 dias' : 'Sem permissão para gerar'}</Button>{forecasts.isError ? <p role="alert" className="mt-2 text-xs text-destructive">Projeção indisponível.</p> : null}{forecasts.data ? <ForecastOperationalView value={forecasts.data} /> : <p className="mt-3 text-sm text-muted-foreground">Gere os cenários para comparar receita, despesa e saldo.</p>}</section> : null}
      {workspaceId && hasTenantSession && financeView === 'valuation' ? <section className="rounded-lg border p-4" aria-label="Valuation financeiro"><h2 className="font-semibold">Valuation básico</h2><p className="mt-1 text-xs text-muted-foreground">Estimativa orientativa; valide as premissas antes de uso externo.</p><Button className="mt-3 min-h-11" onClick={() => valuation.mutate()} disabled={valuation.isPending || !canManage}>{valuation.isPending ? 'Calculando…' : canManage ? 'Calcular múltiplo de receita' : 'Sem permissão para calcular'}</Button>{valuation.isError ? <p role="alert" className="mt-2 text-xs text-destructive">Valuation indisponível.</p> : null}{valuation.data ? <ValuationOperationalView value={valuation.data} /> : <p className="mt-3 text-sm text-muted-foreground">Calcule para visualizar faixa, composição e contratos de suporte.</p>}</section> : null}
      {workspaceId && hasTenantSession && financeView === 'tax' ? <section className="space-y-3 rounded-lg border p-4" aria-label="Tributação e reforma tributária"><h2 className="font-semibold">Tributação e reforma</h2>{canManage ? <form className="grid gap-3 rounded border bg-muted/20 p-3 sm:grid-cols-2" onSubmit={(event) => { event.preventDefault(); if (taxRate && taxSource.trim()) taxProfileMutation.mutate(); }}><label className="text-xs">Regime<select className="mt-1 min-h-11 w-full rounded border bg-background px-3" value={taxRegime} onChange={(event) => setTaxRegime(event.target.value)}><option value="simples_anexo_iv">Simples Nacional — Anexo IV</option><option value="lucro_presumido">Lucro Presumido</option><option value="lucro_real">Lucro Real</option></select></label><label className="text-xs">Alíquota (%)<input className="mt-1 min-h-11 w-full rounded border px-3" inputMode="decimal" value={taxRate} onChange={(event) => setTaxRate(event.target.value)} required /></label><label className="text-xs">Vigência<input type="date" className="mt-1 min-h-11 w-full rounded border px-3" value={taxEffectiveFrom} onChange={(event) => setTaxEffectiveFrom(event.target.value)} required /></label><label className="text-xs">Município<input className="mt-1 min-h-11 w-full rounded border px-3" value={taxMunicipality} onChange={(event) => setTaxMunicipality(event.target.value)} /></label><label className="text-xs sm:col-span-2">Fonte oficial<input className="mt-1 min-h-11 w-full rounded border px-3" value={taxSource} onChange={(event) => setTaxSource(event.target.value)} placeholder="URL ou referência oficial" required /></label><div className="sm:col-span-2"><Button type="submit" className="min-h-11" disabled={taxProfileMutation.isPending || !taxRate || !taxSource.trim()}>{taxProfileMutation.isPending ? 'Salvando…' : 'Salvar perfil tributário'}</Button>{taxProfileMutation.isError ? <p role="alert" className="mt-2 text-xs text-destructive">Não foi possível salvar o perfil.</p> : taxProfileMutation.isSuccess ? <p role="status" className="mt-2 text-xs text-emerald-700">Perfil versionado e auditado.</p> : null}</div></form> : null}<div className="grid gap-3 sm:grid-cols-2"><label className="text-xs">Receita anual<input className="mt-1 min-h-11 w-full rounded border px-3" inputMode="decimal" value={taxRevenue} onChange={(event) => setTaxRevenue(event.target.value)} /></label><label className="text-xs">Despesa anual<input className="mt-1 min-h-11 w-full rounded border px-3" inputMode="decimal" value={taxExpense} onChange={(event) => setTaxExpense(event.target.value)} /></label></div><Button className="min-h-11" onClick={() => taxReform.mutate()} disabled={taxReform.isPending}>{taxReform.isPending ? 'Simulando…' : 'Simular transição tributária'}</Button>{taxProfile.data ? <p className="text-xs text-muted-foreground">{taxProfile.data.length} perfil(is) tributário(s) versionado(s).</p> : null}{taxReform.isError ? <p role="alert" className="text-xs text-destructive">Simulação indisponível.</p> : null}{taxReform.data ? <TaxReformOperationalView value={taxReform.data} /> : <p className="text-sm text-muted-foreground">Informe a receita e a despesa para simular a transição de 2026 a 2033.</p>}</section> : null}
      {workspaceId && hasTenantSession && financeView === 'budget' ? <section className="space-y-3 rounded-lg border p-4" aria-label="Orçamento financeiro"><h2 className="font-semibold">Orçamento e realizado</h2>{canManage ? <form className="grid gap-3 rounded border bg-muted/20 p-3 sm:grid-cols-4" onSubmit={(event) => { event.preventDefault(); if (Number(budgetAmount) > 0 && budgetPeriodEnd >= budgetPeriodStart && budgetAssumption.trim()) budgetMutation.mutate(); }}><label className="text-xs sm:col-span-2">Categoria<input className="mt-1 min-h-11 w-full rounded border px-3" value={budgetCategory} onChange={(event) => setBudgetCategory(event.target.value)} placeholder="Ex.: pessoal, software" /></label><label className="text-xs">Natureza<select className="mt-1 min-h-11 w-full rounded border bg-background px-3" value={budgetType} onChange={(event) => setBudgetType(event.target.value as typeof budgetType)}><option value="expense">Despesa</option><option value="receivable">Receita</option><option value="refund">Estorno</option></select></label><label className="text-xs">Valor (R$)<input className="mt-1 min-h-11 w-full rounded border px-3" inputMode="decimal" value={budgetAmount} onChange={(event) => setBudgetAmount(event.target.value)} required /></label><label className="text-xs">Início<input type="date" className="mt-1 min-h-11 w-full rounded border px-3" value={budgetPeriodStart} onChange={(event) => setBudgetPeriodStart(event.target.value)} required /></label><label className="text-xs">Fim<input type="date" className="mt-1 min-h-11 w-full rounded border px-3" value={budgetPeriodEnd} onChange={(event) => setBudgetPeriodEnd(event.target.value)} required /></label><label className="text-xs sm:col-span-3">Premissa<input className="mt-1 min-h-11 w-full rounded border px-3" value={budgetAssumption} onChange={(event) => setBudgetAssumption(event.target.value)} placeholder="Ex.: crescimento de 10% no trimestre" required /></label><div className="sm:col-span-1"><Button type="submit" className="min-h-11 w-full" disabled={budgetMutation.isPending || !Number(budgetAmount) || !budgetAssumption.trim() || budgetPeriodEnd < budgetPeriodStart}>{budgetMutation.isPending ? 'Salvando…' : 'Adicionar linha'}</Button>{budgetMutation.isError ? <p role="alert" className="mt-2 text-xs text-destructive">Não foi possível salvar.</p> : budgetMutation.isSuccess ? <p role="status" className="mt-2 text-xs text-emerald-700">Linha adicionada e auditada.</p> : null}</div></form> : <p className="text-xs text-muted-foreground">Você pode consultar o orçamento, mas não possui finance:manage para alterá-lo.</p>}{budgetLines.isPending ? <p role="status">Carregando linhas de orçamento…</p> : budgetLines.isError ? <p role="alert">Orçamento indisponível.</p> : budgetLines.data?.length ? <div className="grid gap-2 sm:grid-cols-2">{budgetLines.data.map((line) => <article key={String(line.id)} className="rounded border p-3 text-sm"><p className="font-medium">{String(line.category ?? 'Sem categoria')}</p><p className="text-xs text-muted-foreground">{String(line.entryType ?? '')} · {String(line.periodStart ?? '')} a {String(line.periodEnd ?? '')}</p><p className="mt-1 font-semibold">{formatMoney(line.amount as string | number)}</p></article>)}</div> : <p className="text-sm text-muted-foreground">Nenhuma linha de orçamento cadastrada.</p>}</section> : null}
      {workspaceId && hasTenantSession && financeView === 'insights' ? <section className="space-y-3 rounded-lg border p-4" aria-label="Insights financeiros"><h2 className="font-semibold">Insights financeiros</h2>{insights.isPending ? <p role="status">Analisando dados financeiros…</p> : insights.isError ? <p role="alert">Insights indisponíveis.</p> : insights.data ? <><p className="text-xs text-muted-foreground">Qualidade: {String(insights.data.quality ?? 'não informada')} · revisão humana recomendada.</p>{Array.isArray(insights.data.insights) && insights.data.insights.length ? <div className="grid gap-2 sm:grid-cols-2">{(insights.data.insights as Array<Record<string, unknown>>).map((item, index) => <article key={String(item.id ?? index)} className="rounded border p-3"><p className="font-medium">{String(item.title ?? item.message ?? 'Insight financeiro')}</p><p className="mt-1 text-xs text-muted-foreground">Severidade: {String(item.severity ?? 'informativa')}</p>{item.message && item.title ? <p className="mt-2 text-sm">{String(item.message)}</p> : null}</article>)}</div> : <p className="text-sm text-muted-foreground">Nenhum insight disponível para os dados atuais.</p>}</> : <p className="text-sm text-muted-foreground">Dados insuficientes para gerar insights.</p>}</section> : null}

      {workspaceId && hasTenantSession ? (
        <section className="space-y-3 rounded-lg border p-4" aria-label="Drill-down financeiro">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><h2 className="font-semibold">Centro de custo → cliente → contrato → caso → lançamento</h2><p className="text-xs text-muted-foreground">Clique num agrupamento para descer um nível. O contexto também será aplicado à exportação.</p></div>
            {activeBreadcrumbs.length ? <Button size="sm" variant="outline" onClick={() => { setDrillFilters({}); setDrillOffset(0); }}>Voltar ao consolidado</Button> : null}
          </div>
          {activeBreadcrumbs.length ? <div className="flex flex-wrap gap-2" aria-label="Filtros ativos">{activeBreadcrumbs.map(([key]) => <Badge key={key} variant="secondary">{key.replace('_id', '').replace('_', ' ')}</Badge>)}</div> : null}
          {drilldown.isPending ? <p role="status">Carregando detalhamento…</p> : null}
          {drilldown.isError ? <Alert variant="destructive"><AlertTitle>Detalhamento indisponível</AlertTitle><AlertDescription>O contexto selecionado não pôde ser carregado.</AlertDescription></Alert> : null}
          {drill ? (
            <>
              <div className="grid gap-2 sm:grid-cols-3">
                <p className="rounded border p-3 text-sm">Total: <strong>{formatMoney(drill.total)}</strong></p>
                <p className="rounded border p-3 text-sm">Alocado: <strong>{formatMoney(drill.coverage?.allocatedAmount)}</strong></p>
                <p className="rounded border p-3 text-sm">Não alocado: <strong>{formatMoney(drill.coverage?.unallocatedAmount)}</strong></p>
              </div>
              {(drill.groups ?? []).length ? <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{(drill.groups ?? []).map((group) => <button key={`${group.dimension}:${group.id ?? 'none'}`} type="button" className="min-h-11 rounded-lg border p-3 text-left hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => descend(group)} disabled={!FILTER_BY_DIMENSION[group.dimension] || !group.id}><span className="block text-sm font-medium">{group.label}</span><span className="text-xs text-muted-foreground">{formatMoney(group.amount)}</span></button>)}</div> : <p className="text-sm text-muted-foreground">Nenhum agrupamento neste nível.</p>}
              <ResponsiveDataExplorer
                label="Lançamentos financeiros"
                mobile={<div className="grid gap-2">{(drill.items ?? []).map((item) => <MobileDataCard key={`${item.entryId}:${item.source}`} title={item.entryLabel} subtitle={`${item.costCenterLabel} · ${formatMoney(item.amount)}`}><dl className="grid gap-1 text-xs"><div><dt className="inline text-muted-foreground">Cliente: </dt><dd className="inline">{item.clientLabel}</dd></div><div><dt className="inline text-muted-foreground">Contrato: </dt><dd className="inline">{item.contractLabel}</dd></div><div><dt className="inline text-muted-foreground">Caso: </dt><dd className="inline">{item.matterLabel}</dd></div></dl></MobileDataCard>)}</div>}
              >
                <div className="overflow-x-auto rounded-lg border"><table className="w-full min-w-[860px] text-left text-sm"><thead className="bg-muted/60 text-xs"><tr><th scope="col" className="p-3">Lançamento</th><th scope="col" className="p-3">Centro</th><th scope="col" className="p-3">Cliente</th><th scope="col" className="p-3">Contrato</th><th scope="col" className="p-3">Caso</th><th scope="col" className="p-3 text-right">Valor</th></tr></thead><tbody>{(drill.items ?? []).map((item) => <tr key={`${item.entryId}:${item.source}`} className="border-t"><td className="p-3">{item.entryLabel}</td><td className="p-3">{item.costCenterLabel}</td><td className="p-3">{item.clientLabel}</td><td className="p-3">{item.contractLabel}</td><td className="p-3">{item.matterLabel}</td><td className="p-3 text-right font-medium">{formatMoney(item.amount)}</td></tr>)}</tbody></table></div>
              </ResponsiveDataExplorer>
              <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-3 text-sm">
                <span className="text-muted-foreground">{drill.totalCount ?? 0} lançamento(s) · Página {Math.floor(drillOffset / DRILL_LIMIT) + 1}</span>
                <div className="flex gap-2">
                  <Button size="sm" variant="outline" disabled={drillOffset === 0 || drilldown.isFetching} onClick={() => setDrillOffset((current) => Math.max(0, current - DRILL_LIMIT))}>Anterior</Button>
                  <Button size="sm" variant="outline" disabled={!drill.hasMore || drilldown.isFetching} onClick={() => setDrillOffset((current) => current + DRILL_LIMIT)}>Próxima</Button>
                </div>
              </div>
            </>
          ) : null}
        </section>
      ) : null}

      {workspaceId && hasTenantSession ? (
        <section className="space-y-3 rounded-lg border p-4" aria-label="Exportações financeiras da conta">
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-xs">Formato<select className="mt-1 min-h-11 rounded-md border bg-background px-3" value={exportFormat} onChange={(event) => setExportFormat(event.target.value as typeof exportFormat)} disabled={!canExport}><option value="pdf">PDF</option><option value="xlsx">Excel (XLSX)</option><option value="docx">Word (DOCX)</option></select></label>
            <Button variant="outline" disabled={!canExport || exportMutation.isPending} onClick={() => exportMutation.mutate()}>{exportMutation.isPending ? 'Solicitando…' : 'Exportar contexto atual'}</Button>
          </div>
          {!canExport ? <p className="text-sm text-muted-foreground">Exportação indisponível: falta finance:export.</p> : null}
          {exportMutation.isError ? <p role="alert" className="text-sm text-destructive">Não foi possível criar a exportação.</p> : null}
          {cancelMutation.isError ? <p role="alert" className="text-sm text-destructive">Não foi possível cancelar a exportação.</p> : null}
          {downloadMutation.isError ? <p role="alert" className="text-sm text-destructive">Não foi possível baixar a exportação. Verifique se ela expirou.</p> : null}
          {selectedExport.data ? <div className="rounded border bg-muted/30 p-3 text-sm" role="status"><strong>Exportação selecionada:</strong> {selectedExport.data.format?.toUpperCase()} · {selectedExport.data.status} · {selectedExport.data.progress ?? 0}%{selectedExport.data.errorDetail ? ` · ${selectedExport.data.errorDetail}` : ''}</div> : null}
          {exportsQuery.isPending ? <p role="status">Carregando exportações…</p> : null}
          {exportsQuery.isError ? <p role="alert" className="text-sm text-destructive">Histórico de exportações indisponível.</p> : null}
          {normalizeExportPage(exportsQuery.data).items.length ? <div className="space-y-2">{normalizeExportPage(exportsQuery.data).items.map((job) => <article key={job.id} className={`flex flex-wrap items-center justify-between gap-3 rounded border p-3 ${selectedExportId === job.id ? 'border-primary' : ''}`}><div><p className="text-sm font-medium">{job.format.toUpperCase()} · {job.status}</p><p className="text-xs text-muted-foreground">Progresso {job.progress ?? 0}%{job.rowCount != null ? ` · ${job.rowCount} linhas` : ''}{job.expiresAt ? ` · expira ${new Date(job.expiresAt).toLocaleString('pt-BR')}` : ''}</p>{job.errorDetail ? <p className="text-xs text-destructive">{job.errorDetail}</p> : null}</div><div className="flex flex-wrap gap-2"><Button size="sm" variant="ghost" onClick={() => setSelectedExportId(job.id)}>Ver status</Button>{['queued', 'running'].includes(job.status) ? <Button size="sm" variant="outline" onClick={() => cancelMutation.mutate(job.id)} disabled={cancelMutation.isPending}>Cancelar</Button> : null}{job.status === 'completed' ? <Button size="sm" onClick={() => { setSelectedExportId(job.id); downloadMutation.mutate(job); }} disabled={downloadMutation.isPending}>Baixar</Button> : null}</div></article>)}</div> : canExport && !exportsQuery.isPending ? <p className="text-sm text-muted-foreground">Nenhuma exportação solicitada para esta conta.</p> : null}
          {normalizeExportPage(exportsQuery.data).total > 0 ? <nav aria-label="Paginação de exportações financeiras" className="flex flex-wrap items-center justify-between gap-2 border-t pt-3"><span className="text-xs text-muted-foreground">Exibindo {exportOffset + 1}–{exportOffset + normalizeExportPage(exportsQuery.data).items.length} de {normalizeExportPage(exportsQuery.data).total}</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={exportOffset === 0 || exportsQuery.isFetching} onClick={() => setExportOffset((value) => Math.max(0, value - 50))}>Anterior exportações</Button><Button size="sm" variant="outline" disabled={!normalizeExportPage(exportsQuery.data).hasMore || exportsQuery.isFetching} onClick={() => setExportOffset((value) => value + 50)}>Próxima exportações</Button></div></nav> : null}
        </section>
      ) : null}
    </main>
  );
}
