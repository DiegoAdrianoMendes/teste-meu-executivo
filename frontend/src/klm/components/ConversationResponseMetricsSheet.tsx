import { useEffect, useMemo, useState, type ComponentType } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  BarChart3,
  Building2,
  CheckCircle2,
  Clock3,
  Gauge,
  LoaderCircle,
  RefreshCw,
  ShieldAlert,
  Timer,
  TriangleAlert,
  UserRound,
  UsersRound,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { KlmPill } from '@/klm/components/Primitives';
import {
  klmApi,
  type KlmConversationResponseAggregate,
  type KlmConversationResponseFilterPreset,
  type KlmConversationResponseMetric,
  type KlmConversationResponsePeriod,
  type KlmConversationResponseSlaStatus,
  type KlmConversationResponseState,
} from '@/klm/api';
import { cn } from '@/lib/utils';

export type ConversationResponseMetricsSheetProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

type ScopeDimension = 'account' | 'team' | 'attendant';

const PERIODS: ReadonlyArray<{ value: KlmConversationResponsePeriod; label: string }> = [
  { value: 'last_24_hours', label: '24h' },
  { value: 'last_7_days', label: '7d' },
  { value: 'last_30_days', label: '30d' },
  { value: 'last_90_days', label: '90d' },
  { value: 'all_time', label: 'Tudo' },
  { value: 'custom', label: 'Personalizado' },
];

const STATE_LABELS: Record<KlmConversationResponseState, string> = {
  cliente_aguardando_escritorio: 'Cliente aguarda o escritório',
  escritorio_aguardando_cliente: 'Escritório aguarda o cliente',
  respondido: 'Atendimento respondido',
  sem_mensagem: 'Sem mensagem',
};

const SLA_LABELS: Record<KlmConversationResponseSlaStatus, string> = {
  within_sla: 'Dentro do prazo',
  overdue: 'Resposta atrasada',
  without_sla: 'Sem prazo configurado',
  not_applicable: 'Prazo não aplicável',
};

const PRESET_LABELS: Record<KlmConversationResponseFilterPreset, string> = {
  all: 'Todas as conversas',
  pending: 'Cliente aguardando resposta',
  overdue: 'Resposta atrasada',
  waiting_for_client: 'Escritório aguardando cliente',
  responded: 'Respondidas',
  without_messages: 'Sem mensagens',
};

function conversationResponseStateLabel(state: KlmConversationResponseState): string {
  return STATE_LABELS[state];
}

// eslint-disable-next-line react-refresh/only-export-components
export function conversationResponseWaitSeconds(
  metric: KlmConversationResponseMetric,
  nowMs = Date.now(),
): number | null {
  if (!['cliente_aguardando_escritorio', 'escritorio_aguardando_cliente'].includes(metric.state)) {
    return metric.durationSeconds;
  }
  const startedAt = metric.stateStartedAt ? new Date(metric.stateStartedAt).getTime() : Number.NaN;
  if (Number.isFinite(startedAt)) return Math.max(0, Math.floor((nowMs - startedAt) / 1_000));
  return metric.durationSeconds === null ? null : Math.max(0, Math.floor(metric.durationSeconds));
}

function formatConversationResponseDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return 'Sem amostra';
  const total = Math.max(0, Math.floor(seconds));
  const days = Math.floor(total / 86_400);
  const hours = Math.floor((total % 86_400) / 3_600);
  const minutes = Math.floor((total % 3_600) / 60);
  const remainingSeconds = total % 60;
  if (days > 0) return `${days}d ${String(hours).padStart(2, '0')}h ${String(minutes).padStart(2, '0')}m`;
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, '0')}m ${String(remainingSeconds).padStart(2, '0')}s`;
  return `${minutes}m ${String(remainingSeconds).padStart(2, '0')}s`;
}

type ClientResponseWaitBand = 'green' | 'yellow' | 'red' | 'unavailable';

function clientResponseWaitBand(seconds: number | null): ClientResponseWaitBand {
  if (seconds === null || !Number.isFinite(seconds)) return 'unavailable';
  if (seconds <= 5 * 60) return 'green';
  if (seconds <= 10 * 60) return 'yellow';
  return 'red';
}

function useLiveClock(enabled: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return undefined;
    setNow(Date.now());
    const interval = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(interval);
  }, [enabled]);
  return now;
}

export function ConversationResponseWaitClock({
  metric,
  nowMs,
  className,
  layout = 'inline',
}: {
  metric: KlmConversationResponseMetric | null | undefined;
  nowMs?: number;
  className?: string;
  layout?: 'inline' | 'card';
}) {
  const isWaiting = Boolean(metric && ['cliente_aguardando_escritorio', 'escritorio_aguardando_cliente'].includes(metric.state));
  const localNow = useLiveClock(isWaiting && nowMs === undefined);
  if (!metric || !isWaiting) return null;
  const duration = conversationResponseWaitSeconds(metric, nowMs ?? localNow);
  const clientWaits = metric.state === 'cliente_aguardando_escritorio';
  const officeWaitsForClient = metric.state === 'escritorio_aguardando_cliente';
  const waitBand = officeWaitsForClient ? clientResponseWaitBand(duration) : null;
  const tone = waitBand === 'green'
    ? 'green'
    : waitBand === 'yellow'
      ? 'gold'
      : waitBand === 'red'
        ? 'red'
        : metric.isOverdue
          ? 'red'
          : clientWaits
            ? 'gold'
            : 'muted';
  const showAlert = waitBand === 'red' || (!officeWaitsForClient && metric.isOverdue);
  const label = conversationResponseStateLabel(metric.state);
  const operationalLabel = clientWaits ? 'Responder cliente' : 'Aguardando cliente';
  const formattedDuration = formatConversationResponseDuration(duration);
  const StatusIcon = showAlert ? ShieldAlert : Clock3;
  return (
    <KlmPill
      tone={tone}
      className={cn(
        'max-w-full tabular-nums',
        layout === 'card'
          ? 'grid w-full min-w-0 grid-cols-[minmax(0,1fr)_auto] items-start gap-x-1 whitespace-normal'
          : 'gap-1 whitespace-nowrap',
        className,
      )}
      data-response-wait-band={waitBand ?? undefined}
      aria-label={`${label}: ${formattedDuration}`}
      title={`${label} há ${formattedDuration}`}
    >
      {layout === 'card' ? (
        <>
          <span className="flex min-w-0 items-start gap-1">
            <StatusIcon className="mt-0.5 h-3 w-3 shrink-0" />
            <span className="min-w-0 break-words">{operationalLabel}</span>
          </span>
          <span aria-hidden="true" className="shrink-0 border-l border-current/20 pl-1 font-bold">{formattedDuration}</span>
        </>
      ) : (
        <>
          <StatusIcon className="h-3 w-3 shrink-0" />
          <span className="shrink-0">{operationalLabel}</span>
          <span aria-hidden="true" className="ml-auto shrink-0 border-l border-current/20 pl-1 font-bold">{formattedDuration}</span>
        </>
      )}
      {waitBand && waitBand !== 'unavailable' ? <span className="sr-only">Faixa {waitBand === 'yellow' ? 'amarela' : waitBand === 'green' ? 'verde' : 'vermelha'}</span> : null}
      {!officeWaitsForClient && metric.isOverdue ? <span className="sr-only">Resposta atrasada</span> : null}
    </KlmPill>
  );
}

function localDateTimeInput(date: Date): string {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function aggregateKey(aggregate: KlmConversationResponseAggregate): string {
  return aggregate.groupId ?? `unassigned:${aggregate.groupName}`;
}

function formatPercent(value: number | null): string {
  return value === null ? 'Sem amostra' : `${value.toLocaleString('pt-BR', { maximumFractionDigits: 1 })}%`;
}

function MetricTile({
  label,
  value,
  detail,
  icon: Icon,
  danger = false,
}: {
  label: string;
  value: string;
  detail: string;
  icon: ComponentType<{ className?: string }>;
  danger?: boolean;
}) {
  return (
    <article className={cn('min-h-[96px] border-l-2 bg-secondary/45 px-3 py-3', danger ? 'border-[#a53e3c]' : 'border-primary')}>
      <div className="flex items-center gap-2 text-2xs font-semibold text-muted-foreground">
        <Icon className={cn('h-3.5 w-3.5', danger ? 'text-[#a53e3c]' : 'text-[#0057d8] dark:text-primary')} />
        <span>{label}</span>
      </div>
      <p className={cn('mt-2 break-words text-2xl font-bold tabular-nums', danger && 'text-[#a53e3c] dark:text-red-400')}>{value}</p>
      <p className="mt-1 text-2xs leading-4 text-muted-foreground">{detail}</p>
    </article>
  );
}

function queryErrorMessage(error: unknown): string {
  if (error && typeof error === 'object' && 'message' in error && typeof error.message === 'string') {
    return error.message;
  }
  return 'A API de tempos de resposta não respondeu. Nenhum KPI alternativo foi calculado.';
}

export function ConversationResponseMetricsSheet({ open, onOpenChange }: ConversationResponseMetricsSheetProps) {
  const now = useLiveClock(open);
  const [period, setPeriod] = useState<KlmConversationResponsePeriod>('last_7_days');
  const [preset, setPreset] = useState<KlmConversationResponseFilterPreset>('all');
  const [stateFilter, setStateFilter] = useState<KlmConversationResponseState | 'all'>('all');
  const [slaFilter, setSlaFilter] = useState<KlmConversationResponseSlaStatus | 'all'>('all');
  const [minimumWaitSeconds, setMinimumWaitSeconds] = useState(0);
  const [dimension, setDimension] = useState<ScopeDimension>('account');
  const [selectedAggregateKey, setSelectedAggregateKey] = useState<string>('account');
  const [customFrom, setCustomFrom] = useState(() => localDateTimeInput(new Date(Date.now() - 7 * 86_400_000)));
  const [customTo, setCustomTo] = useState(() => localDateTimeInput(new Date()));

  const customFromMs = new Date(customFrom).getTime();
  const customToMs = new Date(customTo).getTime();
  const customPeriodError = period === 'custom' && (
    !Number.isFinite(customFromMs)
      || !Number.isFinite(customToMs)
      || customToMs < customFromMs
  )
    ? 'Informe um início válido e um fim igual ou posterior ao início.'
    : null;

  const queryInput = useMemo(() => ({
    period,
    ...(period === 'custom' && !customPeriodError ? {
      dateFrom: new Date(customFromMs).toISOString(),
      dateTo: new Date(customToMs).toISOString(),
    } : {}),
    preset,
    statuses: stateFilter === 'all' ? [] : [stateFilter],
    slaStatuses: slaFilter === 'all' ? [] : [slaFilter],
    sortBy: 'duration' as const,
    sortDirection: 'desc' as const,
    limit: 500,
  }), [customFromMs, customPeriodError, customToMs, period, preset, slaFilter, stateFilter]);

  const metricsQuery = useQuery({
    queryKey: ['klm', 'conversation-response-metrics', queryInput],
    queryFn: () => klmApi.conversationResponseMetrics(queryInput),
    enabled: open && !customPeriodError,
    retry: false,
    refetchInterval: open ? 60_000 : false,
    refetchIntervalInBackground: false,
  });

  const aggregates = useMemo(() => {
    if (!metricsQuery.data) return [];
    if (dimension === 'account') return [metricsQuery.data.account];
    return dimension === 'team' ? metricsQuery.data.byTeam : metricsQuery.data.byAttendant;
  }, [dimension, metricsQuery.data]);

  useEffect(() => {
    if (dimension === 'account') {
      setSelectedAggregateKey('account');
      return;
    }
    if (!aggregates.some((aggregate) => aggregateKey(aggregate) === selectedAggregateKey)) {
      setSelectedAggregateKey(aggregates[0] ? aggregateKey(aggregates[0]) : '');
    }
  }, [aggregates, dimension, selectedAggregateKey]);

  const activeAggregate = dimension === 'account'
    ? metricsQuery.data?.account ?? null
    : aggregates.find((aggregate) => aggregateKey(aggregate) === selectedAggregateKey) ?? aggregates[0] ?? null;
  const activeMetrics = activeAggregate?.metrics;
  const minimumFilteredItems = (metricsQuery.data?.items ?? [])
    .filter((metric) => metric.state === 'cliente_aguardando_escritorio' || metric.state === 'escritorio_aguardando_cliente')
    .filter((metric) => minimumWaitSeconds <= 0 || (metric.durationSeconds ?? -1) >= minimumWaitSeconds);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-[98vw] overflow-y-auto p-0 sm:max-w-[920px]">
        <SheetHeader className="border-b border-border px-4 py-4 text-left sm:px-6">
          <div className="flex flex-wrap items-start justify-between gap-3 pr-8">
            <div className="min-w-0">
              <SheetTitle className="flex items-center gap-2 text-lg"><Timer className="h-4 w-4 text-[#0057d8] dark:text-primary" />Tempo sem resposta</SheetTitle>
              <SheetDescription className="mt-1 text-2xs leading-4">
                Tempos de espera e prazos de resposta da conta, das equipes e dos atendentes. Atualização automática a cada minuto.
              </SheetDescription>
            </div>
            <Button
              variant="outline"
              size="sm"
              className="h-8 text-2xs"
              onClick={() => void metricsQuery.refetch()}
              disabled={metricsQuery.isFetching || Boolean(customPeriodError)}
            >
              <RefreshCw className={cn('mr-1.5 h-3.5 w-3.5', metricsQuery.isFetching && 'animate-spin')} />Atualizar
            </Button>
          </div>
        </SheetHeader>

        <div className="space-y-5 px-4 py-5 sm:px-6">
          <section aria-labelledby="response-period-title">
            <Label id="response-period-title" className="text-2xs font-semibold">Período</Label>
            <div className="mt-2 flex gap-1.5 overflow-x-auto pb-1" role="group" aria-label="Período dos KPIs">
              {PERIODS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  aria-pressed={period === option.value}
                  className={cn('min-h-8 shrink-0 rounded-md border px-3 text-2xs font-semibold', period === option.value ? 'border-primary bg-primary text-[#131720]' : 'border-border bg-card text-muted-foreground hover:text-foreground')}
                  onClick={() => setPeriod(option.value)}
                >
                  {option.label}
                </button>
              ))}
            </div>
            {period === 'custom' ? (
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <div><Label htmlFor="response-custom-from" className="text-2xs">Início</Label><Input id="response-custom-from" type="datetime-local" className="mt-1 h-9 text-2xs" value={customFrom} onChange={(event) => setCustomFrom(event.target.value)} /></div>
                <div><Label htmlFor="response-custom-to" className="text-2xs">Fim</Label><Input id="response-custom-to" type="datetime-local" className="mt-1 h-9 text-2xs" value={customTo} onChange={(event) => setCustomTo(event.target.value)} /></div>
              </div>
            ) : null}
            {customPeriodError ? <p role="alert" className="mt-2 text-2xs font-medium text-[#a53e3c]">{customPeriodError}</p> : null}
          </section>

          <section aria-labelledby="response-filter-title" className="border-y border-border py-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div><h3 id="response-filter-title" className="text-sm font-semibold">Filtros operacionais</h3><p className="mt-0.5 text-2xs text-muted-foreground">O servidor recalcula os indicadores para a visão, o estado e o prazo selecionados.</p></div>
              {(preset !== 'all' || stateFilter !== 'all' || slaFilter !== 'all' || minimumWaitSeconds > 0) ? <Button variant="ghost" size="sm" className="h-7 text-2xs" onClick={() => { setPreset('all'); setStateFilter('all'); setSlaFilter('all'); setMinimumWaitSeconds(0); }}>Limpar</Button> : null}
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div><Label className="text-2xs">Visão predefinida</Label><Select value={preset} onValueChange={(value: KlmConversationResponseFilterPreset) => setPreset(value)}><SelectTrigger className="mt-1 h-9 text-2xs" aria-label="Visão predefinida"><SelectValue /></SelectTrigger><SelectContent>{Object.entries(PRESET_LABELS).map(([value, label]) => <SelectItem key={value} value={value} className="text-2xs">{label}</SelectItem>)}</SelectContent></Select></div>
              <div><Label className="text-2xs">Quem aguarda</Label><Select value={stateFilter} onValueChange={(value: KlmConversationResponseState | 'all') => setStateFilter(value)}><SelectTrigger className="mt-1 h-9 text-2xs" aria-label="Estado da espera"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all" className="text-2xs">Todos os estados</SelectItem>{Object.entries(STATE_LABELS).map(([value, label]) => <SelectItem key={value} value={value} className="text-2xs">{label}</SelectItem>)}</SelectContent></Select></div>
              <div><Label className="text-2xs">Prazo de resposta</Label><Select value={slaFilter} onValueChange={(value: KlmConversationResponseSlaStatus | 'all') => setSlaFilter(value)}><SelectTrigger className="mt-1 h-9 text-2xs" aria-label="Prazo de resposta"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all" className="text-2xs">Todos os prazos</SelectItem>{Object.entries(SLA_LABELS).map(([value, label]) => <SelectItem key={value} value={value} className="text-2xs">{label}</SelectItem>)}</SelectContent></Select></div>
              <div><Label htmlFor="response-minimum-wait" className="text-2xs">Espera mínima (segundos)</Label><Input id="response-minimum-wait" type="number" min={0} step={30} className="mt-1 h-9 text-2xs" value={minimumWaitSeconds} onChange={(event) => setMinimumWaitSeconds(Math.max(0, Number(event.target.value) || 0))} /></div>
            </div>
            {minimumWaitSeconds > 0 ? <p className="mt-2 text-2xs leading-4 text-muted-foreground">A espera mínima filtra apenas a lista detalhada carregada. Os KPIs permanecem no recorte completo calculado pelo servidor.</p> : null}
          </section>

          {metricsQuery.isPending && !customPeriodError ? <div className="flex min-h-56 items-center justify-center gap-2 text-2xs text-muted-foreground"><LoaderCircle className="h-4 w-4 animate-spin" />Calculando tempos reais</div> : null}
          {metricsQuery.isError ? (
            <div role="alert" className="border border-red-300 bg-red-50 px-4 py-4 text-red-950 dark:border-red-900 dark:bg-red-950/30 dark:text-red-100">
              <div className="flex items-start gap-2"><TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" /><div><p className="text-2xs font-semibold">KPIs de resposta indisponíveis</p><p className="mt-1 text-2xs leading-4">{queryErrorMessage(metricsQuery.error)}</p><p className="mt-1 text-2xs leading-4 opacity-75">Nenhum número estimado ou dado de demonstração será exibido.</p></div></div>
              <Button variant="outline" size="sm" className="mt-3 h-8 text-2xs" onClick={() => void metricsQuery.refetch()}><RefreshCw className="mr-1.5 h-3.5 w-3.5" />Tentar novamente</Button>
            </div>
          ) : null}

          {metricsQuery.data ? (
            <>
              <section aria-labelledby="response-scope-title">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                  <div><h3 id="response-scope-title" className="text-sm font-semibold">Escopo do painel</h3><p className="mt-0.5 text-2xs text-muted-foreground">Selecione a dimensão e compare a operação sem sair da conversa.</p></div>
                  <div className="inline-flex min-h-9 items-center rounded-md border bg-secondary p-1" role="group" aria-label="Dimensão dos KPIs">
                    {([['account', Building2, 'Conta'], ['team', UsersRound, 'Equipes'], ['attendant', UserRound, 'Atendentes']] as const).map(([value, Icon, label]) => <button key={value} type="button" aria-pressed={dimension === value} onClick={() => setDimension(value)} className={cn('inline-flex h-7 items-center gap-1.5 rounded px-2.5 text-2xs font-semibold', dimension === value ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground')}><Icon className="h-3 w-3" />{label}</button>)}
                  </div>
                </div>
                {dimension !== 'account' ? (
                  <div className="mt-3 max-w-md">
                    <Label className="text-2xs">{dimension === 'team' ? 'Equipe atual' : 'Atendente atual'}</Label>
                    <Select value={selectedAggregateKey} onValueChange={setSelectedAggregateKey} disabled={!aggregates.length}><SelectTrigger className="mt-1 h-9 text-2xs" aria-label={dimension === 'team' ? 'Equipe dos KPIs' : 'Atendente dos KPIs'}><SelectValue placeholder="Sem dados no recorte" /></SelectTrigger><SelectContent>{aggregates.map((aggregate) => <SelectItem key={aggregateKey(aggregate)} value={aggregateKey(aggregate)} className="text-2xs">{aggregate.groupName}</SelectItem>)}</SelectContent></Select>
                  </div>
                ) : null}
              </section>

              {activeAggregate && activeMetrics ? (
                <section aria-label={`KPIs de ${activeAggregate.groupName}`}>
                  <div className="mb-3 flex flex-wrap items-center gap-2"><h3 className="text-sm font-semibold">{activeAggregate.groupName}</h3><KlmPill tone="muted">{activeMetrics.conversations} conversa{activeMetrics.conversations === 1 ? '' : 's'}</KlmPill></div>
                  <div className="grid gap-px overflow-hidden border border-border bg-border sm:grid-cols-2 lg:grid-cols-4">
                    <MetricTile label="Cliente aguardando" value={String(activeMetrics.pending)} detail="Conversas pendentes de resposta do escritório" icon={ShieldAlert} danger={activeMetrics.pending > 0} />
                    <MetricTile label="Espera média" value={formatConversationResponseDuration(activeMetrics.averageWaitSeconds)} detail="Média das esperas pendentes" icon={Clock3} />
                    <MetricTile label="Mediana" value={formatConversationResponseDuration(activeMetrics.medianWaitSeconds)} detail="Ponto central das esperas" icon={Activity} />
                    <MetricTile label="P90" value={formatConversationResponseDuration(activeMetrics.p90WaitSeconds)} detail="90% das esperas ficam abaixo deste valor" icon={BarChart3} />
                    <MetricTile label="Maior espera" value={formatConversationResponseDuration(activeMetrics.longestWaitSeconds)} detail="Conversa pendente mais antiga" icon={Timer} danger={activeMetrics.pending > 0} />
                    <MetricTile label="Primeira resposta" value={formatConversationResponseDuration(activeMetrics.averageFirstResponseSeconds)} detail="Média até a primeira resposta" icon={Gauge} />
                    <MetricTile label="Dentro do prazo" value={formatPercent(activeMetrics.withinSlaRate)} detail={`${activeMetrics.withinSla} de ${activeMetrics.slaEvaluated} conversas avaliadas`} icon={CheckCircle2} />
                    <MetricTile label="Base analisada" value={String(activeMetrics.conversations)} detail="Conversas no período e filtros" icon={Building2} />
                  </div>
                </section>
              ) : <p className="py-8 text-center text-2xs text-muted-foreground">Nenhum agregado disponível para esta dimensão.</p>}

              <div role="note" className="flex items-start gap-2 border-l-2 border-primary bg-primary/10 px-3 py-2.5">
                <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#0057d8] dark:text-primary" />
                <p className="text-2xs leading-4 text-muted-foreground"><strong className="text-foreground">Atribuição atual:</strong> os agrupamentos por equipe e atendente usam o responsável atual da conversa. Eles não reconstroem transferências históricas.</p>
              </div>

              <section aria-labelledby="response-ranking-title">
                <div className="flex flex-wrap items-center justify-between gap-2"><div><h3 id="response-ranking-title" className="text-sm font-semibold">Comparativo da dimensão</h3><p className="mt-0.5 text-2xs text-muted-foreground">Ordenado pela maior espera pendente.</p></div><KlmPill tone="muted">Atualizado {new Date(metricsQuery.data.generatedAt).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}</KlmPill></div>
                <div className="mt-3 overflow-x-auto border-y border-border">
                  <table className="w-full min-w-[620px] text-left text-2xs">
                    <thead className="bg-secondary text-muted-foreground"><tr><th className="px-3 py-2 font-semibold">Escopo</th><th className="px-3 py-2 font-semibold">Pendentes</th><th className="px-3 py-2 font-semibold">Média</th><th className="px-3 py-2 font-semibold">P90</th><th className="px-3 py-2 font-semibold">Maior espera</th><th className="px-3 py-2 font-semibold">No prazo</th></tr></thead>
                    <tbody>{aggregates.slice().sort((left, right) => (right.metrics.longestWaitSeconds ?? -1) - (left.metrics.longestWaitSeconds ?? -1)).map((aggregate) => <tr key={aggregateKey(aggregate)} className="border-t border-border"><td className="px-3 py-2.5 font-semibold">{aggregate.groupName}</td><td className="px-3 py-2.5 tabular-nums">{aggregate.metrics.pending}</td><td className="px-3 py-2.5 tabular-nums">{formatConversationResponseDuration(aggregate.metrics.averageWaitSeconds)}</td><td className="px-3 py-2.5 tabular-nums">{formatConversationResponseDuration(aggregate.metrics.p90WaitSeconds)}</td><td className="px-3 py-2.5 tabular-nums">{formatConversationResponseDuration(aggregate.metrics.longestWaitSeconds)}</td><td className="px-3 py-2.5 tabular-nums">{formatPercent(aggregate.metrics.withinSlaRate)}</td></tr>)}</tbody>
                  </table>
                </div>
              </section>

              <section aria-labelledby="response-detail-title">
                <div className="flex flex-wrap items-end justify-between gap-2"><div><h3 id="response-detail-title" className="text-sm font-semibold">Conversas por maior espera</h3><p className="mt-0.5 text-2xs text-muted-foreground">{minimumFilteredItems.length} de {metricsQuery.data.items.length} itens carregados{metricsQuery.data.hasMore ? ' · há mais registros no servidor' : ''}.</p></div></div>
                <div className="mt-3 divide-y divide-border border-y border-border">
                  {minimumFilteredItems.slice(0, 50).map((metric) => (
                    <article key={metric.threadId} className="grid gap-2 px-2 py-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
                      <div className="min-w-0"><p className="truncate text-2xs font-semibold">Conversa {metric.threadId.slice(0, 8)}</p><p className="mt-1 truncate text-2xs text-muted-foreground">{metric.assignedTeamName ?? 'Sem equipe'} · {metric.assignedAttendantName ?? 'Sem atendente'} · {metric.primaryChannel}</p></div>
                      <ConversationResponseWaitClock metric={metric} nowMs={now} className="justify-self-start sm:justify-self-end" />
                    </article>
                  ))}
                  {minimumFilteredItems.length === 0 ? <p className="px-3 py-8 text-center text-2xs text-muted-foreground">Nenhuma conversa corresponde à espera mínima e aos filtros.</p> : null}
                </div>
              </section>
            </>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}
