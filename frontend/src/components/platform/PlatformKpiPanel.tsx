/**
 * Bateria completa de KPIs do Super Admin (Sprint "Painel Admin com bateria
 * completa de KPIs"). Consome GET /platform/metrics, agrupado por categoria
 * (receita, assinaturas, cobrança, retenção, churn, aquisição, vendas,
 * onboarding, sucesso, suporte, conversas, canais, produto, IA,
 * infraestrutura, equipe e segurança).
 *
 * Cada card mostra valor + sparkline (quando há série) + delta vs período
 * anterior. KPIs sem dado (política no-fake) aparecem com estado vazio
 * honesto e a nota explicando o motivo — nunca um número fabricado.
 */
import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Line, LineChart, ResponsiveContainer } from 'recharts';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { fetchPlatformMetrics, type PlatformKpi, type PlatformKpiCategory } from '@/services/platform';
import { AlertCircle, TrendingDown, TrendingUp } from 'lucide-react';

const PERIOD_OPTIONS = [
  { value: '7', label: '7 dias' },
  { value: '30', label: '30 dias' },
  { value: '90', label: '90 dias' },
  { value: '365', label: '12 meses' },
];

// Chaves em destaque no topo do painel — os "grandes números" do negócio.
const HIGHLIGHT_KEYS = [
  'receita.mrr',
  'receita.arr',
  'receita.lucro_liquido',
  'assinantes.pagantes',
  'assinantes.total',
  'receita.churn_receita_pct',
];

function fmtValue(kpi: PlatformKpi): string {
  if (kpi.sem_dado || kpi.value === null || kpi.value === undefined) return '—';
  if (kpi.unit === 'brl') {
    return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(kpi.value);
  }
  if (kpi.unit === 'pct') {
    return `${kpi.value.toLocaleString('pt-BR', { maximumFractionDigits: 2 })}%`;
  }
  return kpi.value.toLocaleString('pt-BR', { maximumFractionDigits: 2 });
}

export function PlatformKpiPanel({ enabled, defaultPeriodDays = 30 }: { enabled: boolean; defaultPeriodDays?: number }) {
  const [periodDays, setPeriodDays] = useState(String(defaultPeriodDays));
  const [activeCategory, setActiveCategory] = useState<string>('all');

  const metricsQuery = useQuery({
    queryKey: ['platform', 'metrics', periodDays],
    queryFn: () => fetchPlatformMetrics(Number(periodDays)),
    staleTime: 30_000,
    enabled,
  });

  const categories = useMemo(
    () => metricsQuery.data?.categories ?? [],
    [metricsQuery.data?.categories],
  );
  const highlightKpis = useMemo(() => {
    const flat = categories.flatMap((c) => c.kpis);
    return HIGHLIGHT_KEYS.map((key) => flat.find((k) => k.key === key)).filter(Boolean) as PlatformKpi[];
  }, [categories]);

  const visibleCategories = activeCategory === 'all'
    ? categories
    : categories.filter((c) => c.key === activeCategory);

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h3 className="text-base font-semibold">Bateria de KPIs</h3>
          <p className="text-xs text-muted-foreground">
            {metricsQuery.data ? `${metricsQuery.data.total_kpis} indicadores em ${categories.length} categorias.` : 'Carregando indicadores...'}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Select value={periodDays} onValueChange={setPeriodDays}>
            <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
            <SelectContent>
              {PERIOD_OPTIONS.map((opt) => <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>)}
            </SelectContent>
          </Select>
          <Select value={activeCategory} onValueChange={setActiveCategory}>
            <SelectTrigger className="w-48"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Todas as categorias</SelectItem>
              {categories.map((c) => (
                <SelectItem key={c.key} value={c.key}>{c.label} ({c.count})</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {metricsQuery.isLoading ? (
        <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-24 w-full" />)}
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
          {highlightKpis.map((kpi) => <HighlightTile key={kpi.key} kpi={kpi} />)}
        </div>
      )}

      {metricsQuery.isError ? (
        <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
          Não foi possível carregar os KPIs agora.
        </div>
      ) : metricsQuery.isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 2 }).map((_, i) => <Skeleton key={i} className="h-48 w-full" />)}
        </div>
      ) : (
        visibleCategories.map((category) => <CategorySection key={category.key} category={category} />)
      )}
    </div>
  );
}

function HighlightTile({ kpi }: { kpi: PlatformKpi }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-xs font-medium text-muted-foreground">{kpi.label}</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-2xl font-bold tabular-nums">{fmtValue(kpi)}</p>
        <DeltaBadge kpi={kpi} />
      </CardContent>
    </Card>
  );
}

function DeltaBadge({ kpi }: { kpi: PlatformKpi }) {
  if (kpi.sem_dado) {
    return <p className="mt-1 text-xs text-muted-foreground">sem dado</p>;
  }
  if (kpi.delta_pct === null || kpi.delta_pct === undefined) return null;
  const positive = kpi.delta_pct >= 0;
  const Icon = positive ? TrendingUp : TrendingDown;
  return (
    <p className={`mt-1 flex items-center gap-1 text-xs ${positive ? 'text-emerald-600' : 'text-red-600'}`}>
      <Icon className="h-3 w-3" /> {Math.abs(kpi.delta_pct).toFixed(1)}% vs período anterior
    </p>
  );
}

function CategorySection({ category }: { category: PlatformKpiCategory }) {
  const withData = category.kpis.filter((k) => !k.sem_dado);
  const semDado = category.kpis.filter((k) => k.sem_dado);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{category.label}</CardTitle>
        <CardDescription>
          {withData.length} com dado{semDado.length > 0 ? ` · ${semDado.length} sem dado` : ''} · {category.count} indicadores no total
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {category.kpis.map((kpi) => <KpiCard key={kpi.key} kpi={kpi} />)}
        </div>
      </CardContent>
    </Card>
  );
}

function KpiCard({ kpi }: { kpi: PlatformKpi }) {
  const hasSeries = !kpi.sem_dado && kpi.series && kpi.series.some((p) => p.value !== null);
  return (
    <div className={`rounded-lg border p-3 ${kpi.sem_dado ? 'border-dashed bg-muted/30' : ''}`}>
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs font-medium leading-snug text-muted-foreground">{kpi.label}</p>
        {kpi.sem_dado && kpi.nota && (
          <Tooltip>
            <TooltipTrigger asChild>
              <AlertCircle className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            </TooltipTrigger>
            <TooltipContent className="max-w-xs text-xs">{kpi.nota}</TooltipContent>
          </Tooltip>
        )}
      </div>
      <div className="mt-1 text-lg font-semibold tabular-nums">
        {kpi.sem_dado ? <Badge variant="outline" className="font-normal">sem dado</Badge> : fmtValue(kpi)}
      </div>
      {hasSeries && (
        <div className="mt-1 h-8 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={kpi.series ?? []}>
              <Line type="monotone" dataKey="value" stroke="hsl(var(--primary))" strokeWidth={1.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
      {!kpi.sem_dado && kpi.nota && (
        <p className="mt-1 text-2xs text-muted-foreground">{kpi.nota}</p>
      )}
      <details className="mt-2 text-2xs text-muted-foreground">
        <summary className="cursor-pointer select-none">Fonte e cálculo</summary>
        <dl className="mt-2 space-y-1 border-l pl-2">
          <div><dt className="inline font-medium">Definição: </dt><dd className="inline">{kpi.definition}</dd></div>
          <div><dt className="inline font-medium">Fórmula: </dt><dd className="inline">{kpi.formula}</dd></div>
          <div><dt className="inline font-medium">Fonte: </dt><dd className="inline">{kpi.source}</dd></div>
          <div><dt className="inline font-medium">Período: </dt><dd className="inline">{kpi.period}</dd></div>
          <div><dt className="inline font-medium">Qualidade: </dt><dd className="inline">{kpi.quality} · {kpi.coverage}</dd></div>
          <div><dt className="inline font-medium">Responsável: </dt><dd className="inline">{kpi.owner}</dd></div>
        </dl>
        {kpi.drill_down && (
          <a className="mt-2 inline-block text-primary underline-offset-2 hover:underline" href={kpi.drill_down}>
            Abrir detalhamento
          </a>
        )}
      </details>
    </div>
  );
}
