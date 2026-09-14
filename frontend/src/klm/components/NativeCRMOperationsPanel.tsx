import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  Banknote,
  Bell,
  BriefcaseBusiness,
  CheckCircle2,
  Clock3,
  Database,
  FileCheck2,
  KanbanSquare,
  Layers3,
  Loader2,
  RefreshCw,
  ShieldOff,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { KlmPanel, KlmPill, KlmProgress } from '@/klm/components/Primitives';
import {
  klmApi,
  type KlmNativeCRMFinanceContext,
  type KlmNativeCRMFunnelContext,
  type KlmNativeCRMMatterContext,
  type KlmNativeCRMProjection,
  type KlmNativeCRMWorkContext,
} from '@/klm/api';
import { formatBRL, formatDecimal } from '@/lib/formatters';
import { cn } from '@/lib/utils';

type CRMView = 'funnel' | 'matter' | 'work' | 'finance' | 'projection';
type CRMContext = KlmNativeCRMFunnelContext | KlmNativeCRMMatterContext | KlmNativeCRMWorkContext | KlmNativeCRMFinanceContext;

const viewCopy: Record<CRMView, { title: string; description: string; icon: typeof Database }> = {
  funnel: {
    title: 'Inteligência comercial nativa',
    description: 'Pipeline, tempo por etapa, metas e prazos da operação comercial.',
    icon: KanbanSquare,
  },
  matter: {
    title: 'CRM completo deste caso',
    description: 'Contato, lead, oportunidade, caso e tarefas unidos pela identidade nativa.',
    icon: BriefcaseBusiness,
  },
  work: {
    title: 'Trabalho no CRM jurídico',
    description: 'Tarefas, Kanban e quadros associados à operação jurídica.',
    icon: Layers3,
  },
  finance: {
    title: 'Financeiro local',
    description: 'Faturas, estimativas, produtos e aging permanecem disponíveis localmente; cobranças e automações avançadas estão Em breve.',
    icon: Banknote,
  },
  projection: {
    title: 'Saúde dos dados do CRM',
    description: 'Integridade, entidades, falhas e registros obsoletos do banco canônico.',
    icon: Database,
  },
};

const displayLabels: Record<string, string> = {
  case: 'Caso',
  contact: 'Contato',
  lead: 'Lead',
  opportunity: 'Oportunidade',
  opportunities: 'Oportunidades',
  contacts: 'Contatos',
  task: 'Tarefa',
  assigned: 'Atribuído',
  pending: 'Pendente',
  in_process: 'Em andamento',
  new: 'Novas',
  in_progress: 'Em andamento',
  completed: 'Concluídas',
  done: 'Concluídas',
  blocked: 'Bloqueadas',
  prospecting: 'Prospecção',
  qualification: 'Qualificação',
  proposal: 'Proposta',
  negotiation: 'Negociação',
  closed_won: 'Ganho',
  closed_lost: 'Perdido',
  healthy: 'Saudável',
  green: 'Saudável',
  warning: 'Atenção',
  yellow: 'Atenção',
  rotten: 'Crítico',
  red: 'Crítico',
  current: 'A vencer',
  '1_30_days': '1 a 30 dias',
  '31_60_days': '31 a 60 dias',
  '61_90_days': '61 a 90 dias',
  over_90_days: 'Acima de 90 dias',
};

const normalizeLabelKey = (value: string) => value.trim().toLocaleLowerCase('pt-BR').replaceAll(' ', '_').replaceAll('-', '_');
const label = (value: string | null | undefined) => {
  if (!value) return 'Sem estado';
  const normalized = normalizeLabelKey(value);
  return displayLabels[normalized] ?? value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
};
const isHealthyAging = (value: string | null | undefined) => !value || ['healthy', 'green', 'on_track'].includes(normalizeLabelKey(value));
const agingTone = (value: string | null | undefined) => ['rotten', 'red'].includes(normalizeLabelKey(value ?? '')) ? 'red' as const : isHealthyAging(value) ? 'green' as const : 'gold' as const;
const number = (value: string | number | null | undefined) => Number(value ?? 0);

function ProjectionStatus({ projection }: { projection: KlmNativeCRMProjection }) {
  const health = projection.totalRecords
    ? Math.round((projection.availableRecords / projection.totalRecords) * 100)
    : 0;
  return (
    <div className="grid gap-3 border-t border-border pt-3 sm:grid-cols-[minmax(180px,1fr)_minmax(180px,1fr)_auto] sm:items-center">
      <div>
        <div className="flex items-center justify-between text-2xs"><span className="font-semibold">Integridade dos dados</span><span>{health}%</span></div>
        <KlmProgress value={health} tone={projection.failedRecords ? 'red' : 'green'} />
      </div>
      <div className="flex flex-wrap gap-1.5">
        <KlmPill tone="green">{projection.availableRecords} disponíveis</KlmPill>
        {projection.staleRecords > 0 && <KlmPill tone="gold">{projection.staleRecords} desatualizados</KlmPill>}
        {projection.failedRecords > 0 && <KlmPill tone="red">{projection.failedRecords} falharam</KlmPill>}
      </div>
      <p className="text-2xs text-muted-foreground sm:text-right">
        {projection.updatedAt ? `Atualizado ${new Date(projection.updatedAt).toLocaleString('pt-BR')}` : 'Base local pronta'}
      </p>
    </div>
  );
}

function FunnelContent({ context }: { context: KlmNativeCRMFunnelContext }) {
  const pipelineMax = Math.max(...context.pipeline.map((stage) => stage.value), 1);
  const agingItems = context.opportunityColumns.flatMap((column) => column.items)
    .filter((item) => !isHealthyAging(item.agingStatus))
    .sort((left, right) => (right.daysInStage ?? 0) - (left.daysInStage ?? 0));
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 divide-x divide-y border border-border sm:grid-cols-4 sm:divide-y-0">
        {[
          ['Leads no CRM', context.counts.leads ?? 0],
          ['Oportunidades', context.counts.opportunities ?? 0],
          ['Pipeline ponderado', formatBRL(number(context.revenue.weightedPipeline))],
          ['Conversão', `${formatDecimal(number(context.revenue.conversionRate))}%`],
        ].map(([itemLabel, value]) => <div key={String(itemLabel)} className="min-w-0 p-3"><p className="text-xs text-muted-foreground">{itemLabel}</p><strong className="mt-1 block truncate text-2xl font-bold tabular-nums">{value}</strong></div>)}
      </div>
      <div className="grid gap-4 lg:grid-cols-[1.15fr_.85fr]">
        <div>
          <div className="mb-2 flex items-center justify-between"><p className="text-2xs font-semibold uppercase text-[#0057d8]">Pipeline canônico</p><KlmPill tone="muted">{context.pipeline.reduce((sum, stage) => sum + stage.count, 0)} itens</KlmPill></div>
          <div className="space-y-2">
            {context.pipeline.map((stage) => <div key={stage.id} className="grid grid-cols-[96px_1fr_auto] items-center gap-2 text-2xs"><span className="truncate">{label(stage.label)}</span><KlmProgress value={(stage.value / pipelineMax) * 100} /><span className="whitespace-nowrap font-semibold tabular-nums">{formatBRL(stage.value)}</span></div>)}
          </div>
        </div>
        <div className="border-l-0 border-border lg:border-l lg:pl-4">
          <div className="mb-2 flex items-center justify-between"><p className="text-2xs font-semibold uppercase text-[#0057d8]">Aging e atenção</p><Clock3 className="h-3.5 w-3.5 text-muted-foreground" /></div>
          <div className="space-y-2">
            {agingItems.slice(0, 4).map((item) => <div key={item.id} className="flex items-center justify-between gap-3 border-b border-border pb-2 text-2xs last:border-0"><span className="min-w-0"><strong className="block truncate text-2xs">{item.name}</strong><span className="text-muted-foreground">{label(item.stage)} · {item.daysInStage ?? 0} dias</span></span><KlmPill tone={agingTone(item.agingStatus)}>{label(item.agingStatus)}</KlmPill></div>)}
            {!agingItems.length && <p className="py-3 text-2xs text-muted-foreground">Nenhuma oportunidade fora do tempo esperado.</p>}
          </div>
        </div>
      </div>
      <div className="flex flex-wrap gap-2 text-2xs">
        <KlmPill tone={context.urgentCounts.overdueTasks > 0 ? 'red' : 'green'}>{context.urgentCounts.overdueTasks} tarefas vencidas</KlmPill>
        <KlmPill tone={context.urgentCounts.followupsToday > 0 ? 'gold' : 'muted'}>{context.urgentCounts.followupsToday} follow-ups hoje</KlmPill>
        <KlmPill tone={context.urgentCounts.hotLeads > 0 ? 'gold' : 'muted'}>{context.urgentCounts.hotLeads} leads quentes</KlmPill>
        <KlmPill tone="muted">{context.goals.length} metas ativas</KlmPill>
      </div>
    </div>
  );
}

function MatterContent({ context }: { context: KlmNativeCRMMatterContext }) {
  return context.records.length ? (
    <div className={cn(
      'grid gap-px overflow-hidden border border-border bg-border sm:grid-cols-2',
      context.records.length >= 5 ? 'xl:grid-cols-5' : context.records.length === 4 ? 'xl:grid-cols-4' : context.records.length === 3 ? 'xl:grid-cols-3' : 'xl:grid-cols-2',
    )}>
      {context.records.map((record) => <div key={`${record.entityType}-${record.id}`} className="min-w-0 bg-card p-3"><div className="flex items-center justify-between gap-2"><KlmPill tone={record.syncState === 'synced' ? 'green' : record.syncState === 'failed' ? 'red' : 'gold'}>{label(record.entityType)}</KlmPill>{record.syncState === 'synced' && <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />}</div><strong className="mt-3 block truncate text-2xs">{context.sensitiveDataHidden ? 'Registro restrito' : record.name}</strong><p className="mt-1 truncate text-2xs text-muted-foreground">{label(record.stage ?? record.status)}{record.daysInStage !== null ? ` · ${record.daysInStage} dias` : ''}</p><div className="mt-3 flex flex-wrap gap-1">{record.slaBreached && <KlmPill tone="red">Resposta atrasada</KlmPill>}{record.agingStatus && <KlmPill tone={agingTone(record.agingStatus)}>{label(record.agingStatus)}</KlmPill>}</div></div>)}
    </div>
  ) : <p className="border border-dashed border-border p-4 text-2xs text-muted-foreground">Este caso ainda não possui registros relacionados no CRM jurídico.</p>;
}

function WorkContent({ context }: { context: KlmNativeCRMWorkContext }) {
  const total = context.taskColumns.reduce((sum, column) => sum + column.itemCount, 0);
  return (
    <div className="grid gap-4 lg:grid-cols-[1.25fr_.75fr]">
      <div>
        <div className="mb-2 flex items-center justify-between"><p className="text-2xs font-semibold uppercase text-[#0057d8]">Kanban de tarefas</p><KlmPill tone="muted">{total} tarefas</KlmPill></div>
        <div className="grid gap-px overflow-hidden border border-border bg-border sm:grid-cols-3">
          {context.taskColumns.map((column) => <div key={column.id} className="bg-card p-3"><div className="flex items-center justify-between"><strong className="text-2xs">{label(column.name)}</strong><KlmPill tone={column.wipLimit && column.itemCount > column.wipLimit ? 'red' : 'muted'}>{column.itemCount}</KlmPill></div><div className="mt-3 space-y-2">{column.items.slice(0, 3).map((task) => <div key={task.id} className="border-l-2 border-primary pl-2 text-2xs"><strong className="block truncate">{task.name}</strong><span className="text-muted-foreground">{task.dueDate ? new Date(task.dueDate).toLocaleDateString('pt-BR') : 'sem prazo'}</span></div>)}</div></div>)}
        </div>
      </div>
      <div className="border-l-0 border-border lg:border-l lg:pl-4">
        <div className="mb-2 flex items-center justify-between"><p className="text-2xs font-semibold uppercase text-[#0057d8]">Quadros especializados</p><Layers3 className="h-3.5 w-3.5 text-muted-foreground" /></div>
        {context.boards.slice(0, 5).map((board) => <div key={board.id} className="flex items-center justify-between border-b border-border py-2 text-2xs"><span className="truncate font-semibold">{board.name}</span><KlmPill tone="muted">{board.columnCount} colunas</KlmPill></div>)}
        {!context.boards.length && <p className="py-3 text-2xs text-muted-foreground">Nenhum quadro adicional configurado.</p>}
      </div>
    </div>
  );
}

function FinanceContent({ context }: { context: KlmNativeCRMFinanceContext }) {
  const agingMax = Math.max(...context.aging.map((bucket) => bucket.amount), 1);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 divide-x divide-y border border-border sm:grid-cols-4 sm:divide-y-0">
        {[
          ['Faturado', formatBRL(number(context.summary.totalInvoiced))],
          ['Recebido', formatBRL(number(context.summary.totalPaid))],
          ['A receber', formatBRL(number(context.summary.totalDue))],
          ['Vencido', formatBRL(number(context.overdue.amount))],
        ].map(([itemLabel, value]) => <div key={String(itemLabel)} className="min-w-0 p-3"><p className="text-xs text-muted-foreground">{itemLabel}</p><strong className="mt-1 block truncate text-2xl font-bold tabular-nums">{value}</strong></div>)}
      </div>
      <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        <div><p className="mb-2 text-2xs font-semibold uppercase text-[#0057d8]">Aging de recebíveis</p><div className="space-y-2">{context.aging.map((bucket) => <div key={bucket.bucket} className="grid grid-cols-[82px_1fr_auto] items-center gap-2 text-2xs"><span>{label(bucket.bucket)}</span><KlmProgress value={(bucket.amount / agingMax) * 100} tone={bucket.bucket === 'over_90_days' ? 'red' : 'gold'} /><span className="font-semibold tabular-nums">{formatBRL(bucket.amount)}</span></div>)}</div></div>
        <div><div className="mb-2 flex items-center justify-between"><p className="text-2xs font-semibold uppercase text-[#0057d8]">Documentos financeiros</p><FileCheck2 className="h-3.5 w-3.5 text-muted-foreground" /></div><div className="grid grid-cols-3 gap-px overflow-hidden border border-border bg-border text-center">{[['Faturas', context.invoices.length], ['Estimativas', context.estimates.length], ['Produtos', context.products.length]].map(([itemLabel, value]) => <div key={String(itemLabel)} className="bg-card p-3"><strong className="block text-2xl font-bold tabular-nums">{value}</strong><span className="text-xs text-muted-foreground">{itemLabel}</span></div>)}</div>{context.invoices.slice(0, 3).map((invoice) => <div key={invoice.id} className="flex items-center justify-between gap-3 border-b border-border py-2 text-2xs"><span className="min-w-0"><strong className="block truncate">{invoice.name}</strong><span className="text-muted-foreground">{invoice.dueDate ? `vence ${new Date(invoice.dueDate).toLocaleDateString('pt-BR')}` : 'sem vencimento'}</span></span><span className="whitespace-nowrap font-semibold tabular-nums">{formatBRL(invoice.amountDue ?? invoice.amount ?? 0)}</span></div>)}</div>
      </div>
    </div>
  );
}

function ProjectionContent({ context }: { context: KlmNativeCRMFunnelContext }) {
  const entities = Object.entries(context.projection.byEntityType)
    .sort((left, right) => right[1] - left[1]);
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_auto] lg:items-center">
      <div className="grid grid-cols-2 gap-px overflow-hidden border border-border bg-border sm:grid-cols-4">
        {entities.slice(0, 8).map(([entityType, count]) => (
          <div key={entityType} className="bg-card p-3">
            <strong className="block text-2xl font-bold tabular-nums">{count}</strong>
            <span className="text-2xs text-muted-foreground">{label(entityType)}</span>
          </div>
        ))}
        {!entities.length && <p className="col-span-full bg-card p-4 text-2xs text-muted-foreground">Nenhuma entidade cadastrada.</p>}
      </div>
      <div className="flex flex-wrap gap-2 lg:max-w-[260px] lg:justify-end">
        <KlmPill tone={context.projection.failedRecords ? 'red' : 'green'}>{context.projection.failedRecords} falhas</KlmPill>
        <KlmPill tone={context.projection.staleRecords ? 'gold' : 'green'}>{context.projection.staleRecords} obsoletos</KlmPill>
        <KlmPill tone={context.notificationsUnread ? 'gold' : 'muted'}>{context.notificationsUnread} avisos</KlmPill>
        <KlmPill tone="muted">zero execução externa</KlmPill>
      </div>
    </div>
  );
}

export function NativeCRMOperationsPanel({ view, matterId }: { view: CRMView; matterId?: string | null }) {
  const copy = viewCopy[view];
  const Icon = copy.icon;
  const contextQuery = useQuery<CRMContext>({
    queryKey: ['klm', 'crm-context', view, matterId ?? 'workspace'],
    queryFn: () => {
      if (view === 'funnel' || view === 'projection') return klmApi.nativeCRMFunnelContext();
      if (view === 'matter') {
        if (!matterId) throw new Error('Selecione um caso para ler o CRM completo.');
        return klmApi.nativeCRMMatterContext(matterId);
      }
      if (view === 'work') return klmApi.nativeCRMWorkContext();
      return klmApi.nativeCRMFinanceContext();
    },
    enabled: view !== 'matter' || Boolean(matterId),
    retry: false,
  });
  const context = contextQuery.data;

  if (view === 'matter' && !matterId) return null;
  if (contextQuery.isPending) return <KlmPanel data-testid={`crm-${view}`} className="flex min-h-28 items-center justify-center p-4 text-2xs text-muted-foreground"><Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />Lendo CRM jurídico...</KlmPanel>;
  if (contextQuery.isError || !context) return <KlmPanel data-testid={`crm-${view}`} className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between"><div className="flex items-start gap-2"><AlertTriangle className="mt-0.5 h-4 w-4 text-red-600" /><div><p className="text-2xs font-semibold">Não foi possível carregar os dados do CRM</p><p className="mt-1 text-2xs text-muted-foreground">Tente atualizar a tela. Se o problema continuar, acione o suporte.</p></div></div><Button variant="outline" size="sm" className="h-8 text-2xs" onClick={() => void contextQuery.refetch()}><RefreshCw className="mr-1.5 h-3 w-3" />Tentar novamente</Button></KlmPanel>;
  if (!context.projection.linked) return <KlmPanel data-testid={`crm-${view}`} className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between"><div className="flex items-start gap-2"><Database className="mt-0.5 h-4 w-4 text-[#0057d8]" /><div><p className="text-2xs font-semibold">CRM do workspace em preparação</p><p className="mt-1 text-2xs text-muted-foreground">Reconcilie os dados locais sem publicar nada externamente.</p></div></div><KlmPill tone="gold">contexto pendente</KlmPill></KlmPanel>;

  return (
    <KlmPanel data-testid={`crm-${view}`} className="p-4">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-start gap-3"><span className="flex h-9 w-9 shrink-0 items-center justify-center bg-[#031121] text-primary"><Icon className="h-4 w-4" /></span><div className="min-w-0"><p className="text-xs font-semibold">{copy.title}</p><p className="mt-1 text-2xs leading-4 text-muted-foreground">{copy.description}</p></div></div>
        <div className="flex flex-wrap items-center gap-1.5"><KlmPill tone="green">Dados integrados</KlmPill>{context.sensitiveDataHidden && <KlmPill tone="gold"><ShieldOff className="mr-1 h-3 w-3" />dados ocultos</KlmPill>}{'notificationsUnread' in context && context.notificationsUnread > 0 && <KlmPill tone="gold"><Bell className="mr-1 h-3 w-3" />{context.notificationsUnread}</KlmPill>}<Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => void contextQuery.refetch()} aria-label="Atualizar dados do CRM" title="Atualizar dados do CRM"><RefreshCw className={contextQuery.isFetching ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'} /></Button></div>
      </div>
      {view === 'funnel' && <FunnelContent context={context as KlmNativeCRMFunnelContext} />}
      {view === 'matter' && <MatterContent context={context as KlmNativeCRMMatterContext} />}
      {view === 'work' && <WorkContent context={context as KlmNativeCRMWorkContext} />}
      {view === 'finance' && <FinanceContent context={context as KlmNativeCRMFinanceContext} />}
      {view === 'projection' && <ProjectionContent context={context as KlmNativeCRMFunnelContext} />}
      <ProjectionStatus projection={context.projection} />
    </KlmPanel>
  );
}
