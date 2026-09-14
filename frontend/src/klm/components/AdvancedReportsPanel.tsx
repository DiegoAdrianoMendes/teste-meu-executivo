import { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  ArrowDown,
  ArrowUp,
  BarChart3,
  ChevronLeft,
  ChevronRight,
  Download,
  Filter,
  Search,
} from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import {
  type KlmAdvancedKpiMetric,
  klmApi,
} from '@/klm/api';
import {
  KlmPanel,
  KlmPill,
  KlmProgress,
  KlmSectionTitle,
} from '@/klm/components/Primitives';
import { formatBRL, formatDecimal } from '@/lib/formatters';

const ALL = '__all__';
const PAGE_SIZE = 50;

const numberFormatter = new Intl.NumberFormat('pt-BR', {
  maximumFractionDigits: 2,
});

function formatSeconds(value: number) {
  if (value < 60) return `${formatDecimal(value)} s`;
  if (value < 3600) return `${formatDecimal(value / 60)} min`;
  return `${formatDecimal(value / 3600)} h`;
}

function formatMetricValue(value: number | null, unit: string) {
  if (value == null) return 'Sem dado';
  if (unit === 'BRL') return formatBRL(value);
  if (unit === 'percent') return `${formatDecimal(value)}%`;
  if (unit === 'seconds') return formatSeconds(value);
  if (unit === 'score') return `${formatDecimal(value)} pts`;
  return numberFormatter.format(value);
}

function csvCell(value: unknown) {
  const text = value == null ? '' : String(value);
  return `"${text.replaceAll('"', '""')}"`;
}

function downloadCsv(rows: KlmAdvancedKpiMetric[], periodDays: number) {
  const header = [
    'chave',
    'indicador',
    'categoria',
    'valor_atual',
    'valor_anterior',
    'variacao_percentual',
    'unidade',
    'fonte',
    'dimensoes',
    'observacao',
  ];
  const body = rows.map((row) => [
    row.key,
    row.label,
    row.categoryLabel,
    row.value,
    row.previousValue,
    row.deltaPct,
    row.unit,
    row.source,
    JSON.stringify(row.dimensions),
    row.note,
  ]);
  const content = `\uFEFF${[header, ...body].map((row) => row.map(csvCell).join(',')).join('\n')}`;
  const blob = new Blob([content], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `meu-executivo-relatorio-avancado-${periodDays}d.csv`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function Delta({
  value,
  polarity = 'neutral',
}: {
  value: number | null;
  polarity?: KlmAdvancedKpiMetric['polarity'];
}) {
  if (value == null) return <span className="text-muted-foreground">sem comparação</span>;
  if (value === 0) return <span className="text-muted-foreground">0%</span>;
  const improved = polarity === 'neutral'
    ? null
    : polarity === 'positive'
      ? value > 0
      : value < 0;
  const color = improved == null
    ? 'text-muted-foreground'
    : improved
      ? 'text-[#1a7840]'
      : 'text-[#a53e3c]';
  return (
    <span className={color}>
      {value > 0
        ? <ArrowUp className="mr-1 inline h-3 w-3" />
        : <ArrowDown className="mr-1 inline h-3 w-3" />}
      {formatDecimal(Math.abs(value))}%
    </span>
  );
}

type AdvancedReportsPanelProps = {
  periodDays: number;
};

export function AdvancedReportsPanel({ periodDays }: AdvancedReportsPanelProps) {
  const [legalArea, setLegalArea] = useState(ALL);
  const [legalProduct, setLegalProduct] = useState(ALL);
  const [channel, setChannel] = useState(ALL);
  const [responsible, setResponsible] = useState(ALL);
  const [stage, setStage] = useState(ALL);
  const [category, setCategory] = useState(ALL);
  const [onlyWithData, setOnlyWithData] = useState(false);
  const [sortBy, setSortBy] = useState<'label' | 'category' | 'value'>('category');
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [selectedMetric, setSelectedMetric] = useState<KlmAdvancedKpiMetric | null>(null);
  const deferredSearch = useDeferredValue(search.trim());

  const filters = useMemo(() => ({
    periodDays,
    legalAreaKey: legalArea === ALL ? null : legalArea,
    legalProductKey: legalProduct === ALL ? null : legalProduct,
    channel: channel === ALL ? null : channel,
    responsibleUserId: responsible === ALL ? null : responsible,
    stage: stage === ALL ? null : stage,
    category: category === ALL ? null : category,
    query: deferredSearch || null,
    onlyWithData,
    sortBy,
    sortDirection,
    limit: 600,
  }), [
    category,
    channel,
    deferredSearch,
    legalArea,
    legalProduct,
    onlyWithData,
    periodDays,
    responsible,
    sortBy,
    sortDirection,
    stage,
  ]);

  const reportQuery = useQuery({
    queryKey: ['klm', 'advanced-kpis', filters],
    queryFn: () => klmApi.advancedKpis(filters),
    retry: false,
  });

  useEffect(() => {
    setPage(0);
  }, [
    category,
    channel,
    deferredSearch,
    legalArea,
    legalProduct,
    onlyWithData,
    periodDays,
    responsible,
    sortBy,
    sortDirection,
    stage,
  ]);

  useEffect(() => {
    setLegalProduct(ALL);
  }, [legalArea]);

  const report = reportQuery.data;
  const categories = report?.categories ?? [];
  const options = report?.options ?? {};
  const products = (options.legalProducts ?? []).filter(
    (item) => legalArea === ALL || !item.parentValue || item.parentValue === legalArea,
  );
  const pageCount = Math.max(1, Math.ceil((report?.rows.length ?? 0) / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const visibleRows = (report?.rows ?? []).slice(
    safePage * PAGE_SIZE,
    safePage * PAGE_SIZE + PAGE_SIZE,
  );
  const coveragePercent = report?.coverageTotal
    ? Math.round((report.coverageWithData / report.coverageTotal) * 100)
    : 0;
  const chartRows = categories
    .filter((item) => item.metricCount > 0)
    .map((item) => ({
      name: item.label,
      total: item.metricCount,
      comDados: item.metricsWithData,
    }));

  return (
    <KlmPanel className="overflow-hidden" data-testid="advanced-reports-panel">
      <div className="border-b p-4">
        <KlmSectionTitle
          title="Relatórios avançados"
          description="Catálogo multiárea calculado do workspace, com período anterior equivalente e fonte auditável por indicador."
          action={(
            <div className="flex flex-wrap items-center gap-2">
              <KlmPill tone="blue">
                <BarChart3 className="mr-1 h-3 w-3" />
                {report ? `${report.catalogTotal} indicadores` : 'catálogo em cálculo'}
              </KlmPill>
              <Button
                variant="outline"
                size="sm"
                className="h-8 text-2xs"
                disabled={!report?.rows.length}
                onClick={() => report && downloadCsv(report.rows, periodDays)}
              >
                <Download className="mr-1.5 h-3.5 w-3.5" />
                {report?.truncated ? 'Exportar resultados carregados' : 'Exportar tabela'}
              </Button>
            </div>
          )}
        />

        <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          <label className="relative sm:col-span-2">
            <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              aria-label="Buscar indicador avançado"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className="h-9 pl-9 text-2xs"
              placeholder="Buscar KPI, definição ou fonte"
            />
          </label>
          <Select value={legalArea} onValueChange={setLegalArea}>
            <SelectTrigger aria-label="Área jurídica dos relatórios" className="h-9 text-2xs">
              <SelectValue placeholder="Todas as áreas" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>Todas as áreas</SelectItem>
              {(options.legalAreas ?? []).map((item) => (
                <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={legalProduct} onValueChange={setLegalProduct}>
            <SelectTrigger aria-label="Produto jurídico dos relatórios" className="h-9 text-2xs">
              <SelectValue placeholder="Todos os produtos" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>Todos os produtos</SelectItem>
              {products.map((item) => (
                <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={category} onValueChange={setCategory}>
            <SelectTrigger aria-label="Categoria dos relatórios" className="h-9 text-2xs">
              <SelectValue placeholder="Todas as categorias" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>Todas as categorias</SelectItem>
              {categories.map((item) => (
                <SelectItem key={item.key} value={item.key}>{item.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={channel} onValueChange={setChannel}>
            <SelectTrigger aria-label="Canal dos relatórios" className="h-9 text-2xs">
              <SelectValue placeholder="Todos os canais" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>Todos os canais</SelectItem>
              {(options.channels ?? []).map((item) => (
                <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={responsible} onValueChange={setResponsible}>
            <SelectTrigger aria-label="Responsável dos relatórios" className="h-9 text-2xs">
              <SelectValue placeholder="Toda a equipe" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>Toda a equipe</SelectItem>
              {(options.responsibleUsers ?? []).map((item) => (
                <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={stage} onValueChange={setStage}>
            <SelectTrigger aria-label="Etapa dos relatórios" className="h-9 text-2xs">
              <SelectValue placeholder="Todas as etapas" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>Todas as etapas</SelectItem>
              {(options.stages ?? []).map((item) => (
                <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <div className="flex min-w-0 items-center gap-2">
            <Select value={sortBy} onValueChange={(value) => setSortBy(value as typeof sortBy)}>
              <SelectTrigger aria-label="Ordenação dos relatórios" className="h-9 min-w-0 flex-1 text-2xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="category">Categoria</SelectItem>
                <SelectItem value="label">Nome</SelectItem>
                <SelectItem value="value">Valor</SelectItem>
              </SelectContent>
            </Select>
            <Button
              type="button"
              variant="outline"
              size="icon"
              className="h-9 w-9 shrink-0"
              title={sortDirection === 'asc' ? 'Ordenação crescente' : 'Ordenação decrescente'}
              onClick={() => setSortDirection((current) => current === 'asc' ? 'desc' : 'asc')}
            >
              {sortDirection === 'asc'
                ? <ArrowUp className="h-3.5 w-3.5" />
                : <ArrowDown className="h-3.5 w-3.5" />}
            </Button>
          </div>
          <label className="flex h-9 cursor-pointer items-center gap-2 rounded-md border px-3 text-2xs">
            <input
              type="checkbox"
              aria-label="Somente indicadores com dados"
              checked={onlyWithData}
              onChange={(event) => setOnlyWithData(event.target.checked)}
              className="h-3.5 w-3.5 accent-[#0057d8]"
            />
            Somente indicadores com dados
          </label>
        </div>
      </div>

      {reportQuery.isPending && (
        <div className="space-y-4 p-4">
          <div className="grid gap-3 sm:grid-cols-3">
            {Array.from({ length: 3 }).map((_, index) => (
              <Skeleton key={index} className="h-20 w-full" />
            ))}
          </div>
          <Skeleton className="h-64 w-full" />
        </div>
      )}

      {reportQuery.isError && (
        <div className="p-8 text-center">
          <p className="text-xs font-semibold text-[#a53e3c]">Não foi possível calcular os relatórios avançados.</p>
          <p className="mt-1 text-2xs text-muted-foreground">Nenhum valor estimado foi exibido.</p>
          <Button variant="outline" size="sm" className="mt-4 h-8 text-2xs" onClick={() => void reportQuery.refetch()}>
            Tentar novamente
          </Button>
        </div>
      )}

      {report && (
        <>
          <div className="grid gap-3 border-b p-4 sm:grid-cols-3">
            <div className="rounded-lg border bg-card p-3">
              <span className="text-2xs text-muted-foreground">Catálogo auditável</span>
              <strong className="mt-1 block text-lg">{report.catalogTotal}</strong>
              <span className="text-2xs text-muted-foreground">KPIs canônicos disponíveis</span>
            </div>
            <div className="rounded-lg border bg-card p-3">
              <span className="text-2xs text-muted-foreground">Resultado do filtro</span>
              <strong className="mt-1 block text-lg">{report.totalRows}</strong>
              <span className="text-2xs text-muted-foreground">
                {report.rows.length} carregados nesta consulta
                {report.truncated ? ' · resultado parcial' : ''}
              </span>
            </div>
            <div className="rounded-lg border bg-card p-3">
              <span className="text-2xs text-muted-foreground">Cobertura com dados</span>
              <strong className="mt-1 block text-lg">{coveragePercent}%</strong>
              <span className="mt-2 block"><KlmProgress value={coveragePercent} tone={coveragePercent >= 70 ? 'green' : 'gold'} /></span>
            </div>
          </div>

          <div className="grid gap-4 border-b p-4 xl:grid-cols-[1fr_320px]">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <BarChart3 className="h-4 w-4 text-[#0057d8]" />
                <h3 className="text-xs font-semibold">Cobertura por categoria</h3>
              </div>
              <p className="mt-1 text-2xs text-muted-foreground">Indicadores com dados frente ao catálogo disponível no recorte.</p>
              <div className="mt-4 h-64 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={chartRows} layout="vertical" margin={{ top: 0, right: 8, bottom: 0, left: 18 }}>
                    <CartesianGrid strokeDasharray="3 3" horizontal={false} className="stroke-border" />
                    <XAxis type="number" tick={{ fontSize: 10 }} />
                    <YAxis type="category" dataKey="name" width={118} tick={{ fontSize: 9 }} />
                    <Tooltip />
                    <Bar dataKey="total" name="Catálogo" fill="#dce8f8" radius={[0, 3, 3, 0]} />
                    <Bar dataKey="comDados" name="Com dados" fill="#0057d8" radius={[0, 3, 3, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <Filter className="h-4 w-4 text-[#0057d8]" />
                <h3 className="text-xs font-semibold">Famílias de indicadores</h3>
              </div>
              <div className="mt-3 max-h-64 space-y-1 overflow-y-auto pr-1">
                {categories.map((item) => (
                  <button
                    key={item.key}
                    type="button"
                    onClick={() => setCategory((current) => current === item.key ? ALL : item.key)}
                    className="flex w-full items-center justify-between gap-3 rounded-md border px-3 py-2 text-left hover:border-primary"
                  >
                    <span className="min-w-0">
                      <strong className="block truncate text-2xs">{item.label}</strong>
                      <span className="text-2xs text-muted-foreground">{item.metricsWithData} com dados</span>
                    </span>
                    <KlmPill tone={category === item.key ? 'blue' : 'muted'}>{item.metricCount}</KlmPill>
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="p-4">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <h3 className="text-xs font-semibold">Tabela analítica</h3>
                <p className="mt-1 text-2xs text-muted-foreground">
                  Atual: {periodDays} dias · comparação: {report.previousPeriodDays} dias anteriores.
                </p>
              </div>
              <span className="text-2xs text-muted-foreground">
                {report.rows.length
                  ? `${safePage * PAGE_SIZE + 1}–${Math.min((safePage + 1) * PAGE_SIZE, report.rows.length)} de ${report.rows.length}`
                  : '0 indicadores'}
              </span>
            </div>

            <div className="mt-3 max-h-[560px] overflow-auto rounded-lg border">
              <table className="w-full min-w-[880px] border-collapse text-left">
                <thead className="sticky top-0 z-10 bg-secondary">
                  <tr className="border-b text-2xs text-muted-foreground">
                    <th className="px-3 py-2 font-semibold">Indicador</th>
                    <th className="px-3 py-2 font-semibold">Categoria</th>
                    <th className="px-3 py-2 text-right font-semibold">Atual</th>
                    <th className="px-3 py-2 text-right font-semibold">Anterior</th>
                    <th className="px-3 py-2 text-right font-semibold">Variação</th>
                    <th className="px-3 py-2 font-semibold">Fonte</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRows.map((row) => (
                    <tr
                      key={row.key}
                      className="cursor-pointer border-b last:border-0 hover:bg-secondary/70"
                      onClick={() => setSelectedMetric(row)}
                      onDoubleClick={() => setSelectedMetric(row)}
                    >
                      <td className="max-w-[340px] px-3 py-2">
                        <strong className="block truncate text-2xs">{row.label}</strong>
                        <span className="mt-0.5 block truncate text-2xs text-muted-foreground">{row.description}</span>
                      </td>
                      <td className="px-3 py-2 text-2xs">{row.categoryLabel}</td>
                      <td className="px-3 py-2 text-right text-2xs font-semibold">{formatMetricValue(row.value, row.unit)}</td>
                      <td className="px-3 py-2 text-right text-2xs">{formatMetricValue(row.previousValue, row.unit)}</td>
                      <td className="px-3 py-2 text-right text-2xs">
                        <Delta value={row.deltaPct} polarity={row.polarity} />
                      </td>
                      <td className="max-w-[180px] px-3 py-2 text-2xs text-muted-foreground"><span className="block truncate">{row.source}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!visibleRows.length && (
                <div className="p-8 text-center text-2xs text-muted-foreground">
                  Nenhum indicador corresponde aos filtros. O sistema não fabricou substitutos.
                </div>
              )}
            </div>

            <div className="mt-3 flex items-center justify-end gap-2">
              <Button
                variant="outline"
                size="icon"
                className="h-8 w-8"
                title="Página anterior"
                disabled={safePage === 0}
                onClick={() => setPage((current) => Math.max(0, current - 1))}
              >
                <ChevronLeft className="h-3.5 w-3.5" />
              </Button>
              <span className="text-2xs text-muted-foreground">Página {safePage + 1} de {pageCount}</span>
              <Button
                variant="outline"
                size="icon"
                className="h-8 w-8"
                title="Próxima página"
                disabled={safePage >= pageCount - 1}
                onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))}
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        </>
      )}

      <Dialog open={Boolean(selectedMetric)} onOpenChange={(open) => { if (!open) setSelectedMetric(null); }}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>{selectedMetric?.label}</DialogTitle>
            <DialogDescription>{selectedMetric?.categoryLabel}</DialogDescription>
          </DialogHeader>
          {selectedMetric && (
            <div className="space-y-3 text-xs">
              <div className="grid gap-2 sm:grid-cols-3">
                <div className="rounded-md border p-3"><span className="text-2xs text-muted-foreground">Atual</span><strong className="mt-1 block">{formatMetricValue(selectedMetric.value, selectedMetric.unit)}</strong></div>
                <div className="rounded-md border p-3"><span className="text-2xs text-muted-foreground">Anterior</span><strong className="mt-1 block">{formatMetricValue(selectedMetric.previousValue, selectedMetric.unit)}</strong></div>
                <div className="rounded-md border p-3">
                  <span className="text-2xs text-muted-foreground">Variação</span>
                  <strong className="mt-1 block">
                    <Delta value={selectedMetric.deltaPct} polarity={selectedMetric.polarity} />
                  </strong>
                </div>
              </div>
              <p className="leading-5 text-muted-foreground">{selectedMetric.description}</p>
              <dl className="grid gap-2 rounded-md bg-secondary p-3 text-2xs sm:grid-cols-2">
                <div><dt className="font-semibold">Chave</dt><dd className="mt-1 break-all text-muted-foreground">{selectedMetric.key}</dd></div>
                <div><dt className="font-semibold">Fonte</dt><dd className="mt-1 break-all text-muted-foreground">{selectedMetric.source}</dd></div>
                <div><dt className="font-semibold">Dimensões</dt><dd className="mt-1 break-all text-muted-foreground">{Object.keys(selectedMetric.dimensions).length ? JSON.stringify(selectedMetric.dimensions) : 'Sem dimensão adicional'}</dd></div>
                <div><dt className="font-semibold">Observação</dt><dd className="mt-1 text-muted-foreground">{selectedMetric.note ?? 'Nenhuma'}</dd></div>
              </dl>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </KlmPanel>
  );
}
