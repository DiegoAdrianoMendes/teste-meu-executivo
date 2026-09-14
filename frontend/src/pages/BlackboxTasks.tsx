import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, Clock3, ListTodo, Plus, RefreshCw, ShieldCheck, UserRoundX, UsersRound } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { useBlackboxManifest } from '@/blackbox/session-manifest';
import { PlatformTaskDrawer } from '@/components/platform/PlatformTaskDrawer';
import { PlatformTaskCreateDialog } from '@/components/platform/PlatformTaskEditor';
import { PlatformWorkflowAdministration } from '@/components/platform/PlatformWorkflowAdministration';
import { PlatformEntityCombobox } from '@/components/platform/PlatformEntityCombobox';
import { AgentPlatformOperationsPanel } from '@/klm/components/AgentPlatformOperationsPanel';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { canPlatform } from '@/lib/platform-access';
import {
  listPlatformTasks,
  type PlatformTask,
  type PlatformTaskPriority,
  type PlatformTaskScope,
  type PlatformTaskStatus,
} from '@/services/platform-tasks';
import {
  dispatchPlatformWorkItemCommand,
  fetchPlatformWorkItemCounts,
  fetchPlatformWorkItems,
  reconcilePlatformWorkItems,
  type PlatformWorkItem,
} from '@/services/platform-work-items';

type TaskTab = 'mine' | 'team' | 'reviews' | 'work-items' | 'agent-decisions' | 'unassigned' | 'critical' | 'overdue' | 'completed';
type WorkItemView = 'mine' | 'unassigned' | 'platform';

const TASK_TABS: Array<{ id: TaskTab; label: string }> = [
  { id: 'mine', label: 'Minhas tarefas' },
  { id: 'team', label: 'Minha equipe' },
  { id: 'reviews', label: 'Revisões' },
  { id: 'work-items', label: 'Work items' },
  { id: 'agent-decisions', label: 'Chamadas de agentes' },
  { id: 'unassigned', label: 'Não atribuídas' },
  { id: 'critical', label: 'P0/P1' },
  { id: 'overdue', label: 'Vencidas' },
  { id: 'completed', label: 'Concluídas' },
];
const SOURCE_LABELS: Record<string, string> = {
  support_ticket: 'Ticket', incident: 'Incidente', support_access: 'Acesso assistido',
  workspace_onboarding: 'Onboarding', billing: 'Cobrança', workspace_invitation: 'Convite de conta',
  staff_invitation: 'Convite de funcionário', publication: 'Publicação', configuration: 'Configuração',
  commercial_request: 'Solicitação comercial',
  commercial_checkout_reconcile: 'Reconciliação de checkout',
  commercial_subscription_reconcile: 'Reconciliação de assinatura',
};
const WORK_ITEM_COMMAND_LABELS: Record<string, string> = {
  assign_to_me: 'Assumir', mark_complete: 'Marcar como concluído', revoke: 'Revogar',
  submit_review: 'Enviar para revisão', approve: 'Aprovar',
};
const TASK_STATUS_LABEL: Record<string, string> = {
  pending: 'Pendente', in_progress: 'Em andamento', blocked: 'Bloqueada', done: 'Concluída', cancelled: 'Cancelada',
};
const WORK_ITEM_VIEWS: Array<{ id: WorkItemView; label: string; description: string }> = [
  {
    id: 'mine',
    label: 'Minhas atribuições',
    description: 'Itens atualmente atribuídos a você.',
  },
  {
    id: 'unassigned',
    label: 'Sem responsável',
    description: 'Itens autorizados pelo seu RBAC que ainda podem ser assumidos.',
  },
  {
    id: 'platform',
    label: 'Toda fila autorizada',
    description: 'Todos os itens permitidos pelos módulos e ações da sua sessão.',
  },
];

const SAFE_WORK_ITEM_SOURCE_ROUTES: Record<string, {
  path: string;
  tab: string;
  focus: 'context' | 'workspace';
}> = {
  support_ticket: { path: '/blackbox/support-tickets', tab: 'tickets', focus: 'context' },
  incident: { path: '/blackbox/governance/security', tab: 'incidents', focus: 'context' },
  support_access: { path: '/blackbox/support-access', tab: 'requests', focus: 'context' },
  workspace_onboarding: { path: '/blackbox/workspaces', tab: 'onboarding', focus: 'workspace' },
  billing: { path: '/blackbox/billing', tab: 'subscriptions', focus: 'context' },
  workspace_invitation: { path: '/blackbox/workspaces', tab: 'invitations', focus: 'context' },
  staff_invitation: { path: '/blackbox/team', tab: 'invitations', focus: 'context' },
  publication: { path: '/blackbox/announcements', tab: 'publications', focus: 'context' },
  configuration: { path: '/blackbox/settings', tab: 'configuration', focus: 'context' },
  commercial_request: { path: '/blackbox/workspaces', tab: 'commercial', focus: 'workspace' },
  commercial_checkout_reconcile: { path: '/blackbox/workspaces', tab: 'commercial', focus: 'workspace' },
  commercial_subscription_reconcile: { path: '/blackbox/workspaces', tab: 'commercial', focus: 'workspace' },
};

function safeWorkItemSourceHref(item: PlatformWorkItem): string {
  const fallback = new URLSearchParams({
    tab: 'work-items',
    sourceType: item.sourceType,
    sourceId: item.sourceId,
    focus: 'context',
  });
  if (item.workspaceId) fallback.set('workspaceId', item.workspaceId);
  try {
    const parsed = new URL(item.href, 'https://blackbox.internal');
    const expected = SAFE_WORK_ITEM_SOURCE_ROUTES[item.sourceType];
    if (
      parsed.origin !== 'https://blackbox.internal'
      || !expected
      || parsed.pathname !== expected.path
      || parsed.searchParams.get('sourceType') !== item.sourceType
      || parsed.searchParams.get('sourceId') !== item.sourceId
      || parsed.searchParams.get('tab') !== expected.tab
      || parsed.searchParams.get('focus') !== expected.focus
      || (
        item.workspaceId !== null
        && parsed.searchParams.get('workspaceId') !== item.workspaceId
      )
    ) {
      return `/blackbox/tasks?${fallback.toString()}`;
    }

    const canonical = new URLSearchParams({
      sourceType: item.sourceType,
      sourceId: item.sourceId,
      tab: expected.tab,
      focus: expected.focus,
    });
    if (item.workspaceId) canonical.set('workspaceId', item.workspaceId);
    if (item.sourceType === 'configuration') {
      const namespace = typeof item.payloadSafe.namespace === 'string'
        ? item.payloadSafe.namespace.trim()
        : '';
      if (!namespace || parsed.searchParams.get('namespace') !== namespace) {
        return `/blackbox/tasks?${fallback.toString()}`;
      }
      canonical.set('namespace', namespace);
    }
    return `${expected.path}?${canonical.toString()}`;
  } catch {
    return `/blackbox/tasks?${fallback.toString()}`;
  }
}

function formatDate(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString('pt-BR') : 'Sem prazo';
}

function tabFilters(tab: TaskTab, platformWide: boolean): {
  scope: PlatformTaskScope;
  statuses?: PlatformTaskStatus[];
  priorities?: PlatformTaskPriority[];
  overdue?: boolean;
  unassigned?: boolean;
} {
  const broadScope: PlatformTaskScope = platformWide ? 'platform' : 'mine';
  if (tab === 'team') return { scope: 'team' };
  if (tab === 'reviews') return { scope: 'reviews' };
  if (tab === 'unassigned') return { scope: broadScope, unassigned: true, statuses: ['pending', 'in_progress', 'blocked'] };
  if (tab === 'critical') return { scope: broadScope, priorities: ['P0', 'P1'], statuses: ['pending', 'in_progress', 'blocked'] };
  if (tab === 'overdue') return { scope: broadScope, overdue: true };
  if (tab === 'completed') return { scope: broadScope, statuses: ['done', 'cancelled'] };
  return { scope: 'mine', statuses: ['pending', 'in_progress', 'blocked'] };
}

export default function BlackboxTasks() {
  const { effectiveUser } = useBlackboxManifest();
  const queryClient = useQueryClient();
  const [urlParams, setUrlParams] = useSearchParams();
  const requestedTab = urlParams.get('tab');
  const initialTab = TASK_TABS.some((tab) => tab.id === requestedTab)
    ? requestedTab as TaskTab
    : 'mine';
  const [activeTab, setActiveTab] = useState<TaskTab>(initialTab);
  const [taskSearch, setTaskSearch] = useState('');
  const [workItemSearch, setWorkItemSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [selectedTask, setSelectedTask] = useState<PlatformTask | null>(null);
  const [workItemCursor, setWorkItemCursor] = useState<string | undefined>();
  const [workItemCursorHistory, setWorkItemCursorHistory] = useState<Array<string | undefined>>([]);
  const [workItemView, setWorkItemView] = useState<WorkItemView>('mine');
  const [workItemTarget, setWorkItemTarget] = useState<{ item: PlatformWorkItem; command: string } | null>(null);
  const [workItemReason, setWorkItemReason] = useState('');
  const [agentWorkspaceId, setAgentWorkspaceId] = useState<string | null>(null);
  const platformWide = effectiveUser.isPlatformRoot || effectiveUser.platformRolePackage === 'platform_admin';
  const filters = useMemo(() => tabFilters(activeTab, platformWide), [activeTab, platformWide]);
  const platformTasksQuery = useQuery({
    queryKey: ['blackbox', 'platform-tasks', activeTab, taskSearch, offset, filters],
    queryFn: () => listPlatformTasks({ ...filters, search: taskSearch, limit: 50, offset }),
    enabled: activeTab !== 'work-items',
    retry: false,
  });
  const workItemsQuery = useQuery({
    queryKey: ['blackbox', 'work-items', workItemView, workItemCursor, workItemSearch],
    queryFn: () => fetchPlatformWorkItems({
      scope: workItemView === 'mine' ? 'mine' : 'platform',
      unassigned: workItemView === 'unassigned' ? true : undefined,
      search: workItemSearch,
      cursor: workItemCursor,
    }),
    enabled: activeTab === 'work-items',
    retry: false,
  });
  const workItemCountsQuery = useQuery({ queryKey: ['blackbox', 'work-items', 'counts'], queryFn: fetchPlatformWorkItemCounts, enabled: activeTab === 'work-items', retry: false });
  const workItemCommandMutation = useMutation({
    mutationFn: () => {
      if (!workItemTarget) throw new Error('Nenhuma ação selecionada.');
      return dispatchPlatformWorkItemCommand(workItemTarget.item, workItemTarget.command, workItemReason.trim());
    },
    onSuccess: async () => { setWorkItemTarget(null); setWorkItemReason(''); await queryClient.invalidateQueries({ queryKey: ['blackbox', 'work-items'] }); },
  });
  const reconcileMutation = useMutation({ mutationFn: reconcilePlatformWorkItems, onSuccess: async () => queryClient.invalidateQueries({ queryKey: ['blackbox', 'work-items'] }) });
  const canCreate = canPlatform(effectiveUser, 'platform_tasks', 'create');
  const canAssign = canPlatform(effectiveUser, 'platform_tasks', 'assign');
  const canReconcile = canPlatform(effectiveUser, 'settings', 'manage');
  const page = platformTasksQuery.data;
  const currentPage = page ? Math.floor(page.offset / page.limit) + 1 : 1;
  const totalPages = page ? Math.max(1, Math.ceil(page.total / page.limit)) : 1;
  useEffect(() => {
    if (!requestedTab || !TASK_TABS.some((tab) => tab.id === requestedTab)) return;
    setActiveTab(requestedTab as TaskTab);
  }, [requestedTab]);
  const changeTab = (tab: string) => {
    const nextTab = tab as TaskTab;
    setActiveTab(nextTab);
    const next = new URLSearchParams(urlParams);
    if (nextTab === 'mine') next.delete('tab');
    else next.set('tab', nextTab);
    setUrlParams(next, { replace: true });
    setOffset(0);
    setWorkItemCursor(undefined);
    setWorkItemCursorHistory([]);
  };
  const changeWorkItemView = (view: WorkItemView) => {
    setWorkItemView(view);
    setWorkItemCursor(undefined);
    setWorkItemCursorHistory([]);
  };
  const openWorkItemCommand = (item: PlatformWorkItem, command: string) => {
    workItemCommandMutation.reset();
    setWorkItemReason('');
    setWorkItemTarget({ item, command });
  };
  const closeWorkItemCommand = () => {
    if (workItemCommandMutation.isPending) return;
    workItemCommandMutation.reset();
    setWorkItemReason('');
    setWorkItemTarget(null);
  };

  return <div className="mx-auto w-full max-w-[1500px] space-y-6 p-6">
    <div className="flex flex-wrap items-start justify-between gap-4"><div><p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">Operação da equipe do SaaS</p><h1 className="mt-1 text-2xl font-semibold">Tarefas e aprovações</h1><p className="mt-1 max-w-3xl text-sm text-muted-foreground">Tarefas internas autoritativas e work items projetados permanecem separados. Responsáveis, executores, reviewer, prazos, comentários e histórico são persistidos no backend.</p></div><div className="flex gap-2"><Button variant="outline" onClick={() => void (activeTab === 'work-items' ? Promise.all([workItemsQuery.refetch(), workItemCountsQuery.refetch()]) : platformTasksQuery.refetch())}><RefreshCw className="mr-2 h-4 w-4" />Atualizar</Button>{canCreate ? <Button onClick={() => setCreateOpen(true)}><Plus className="mr-2 h-4 w-4" />Nova tarefa</Button> : null}</div></div>

    <Tabs value={activeTab} onValueChange={changeTab}>
      <div className="overflow-x-auto"><TabsList className="h-auto min-w-max flex-wrap justify-start">{TASK_TABS.map((tab) => <TabsTrigger key={tab.id} value={tab.id}>{tab.label}</TabsTrigger>)}</TabsList></div>
      {TASK_TABS.filter((tab) => tab.id !== 'work-items' && tab.id !== 'agent-decisions').map((tab) => <TabsContent key={tab.id} value={tab.id} className="mt-5 space-y-4">
        <Card><CardHeader><div className="flex flex-wrap items-start justify-between gap-3"><div><CardTitle className="flex items-center gap-2"><ListTodo className="h-5 w-5" />{tab.label}</CardTitle><CardDescription>Consulta paginada no servidor; total e filtros representam o conjunto completo autorizado.</CardDescription></div>{page ? <div className="text-right"><p className="text-2xl font-semibold">{page.total}</p><p className="text-xs text-muted-foreground">registro(s) no recorte</p></div> : null}</div></CardHeader><CardContent className="space-y-4">
          <Input className="max-w-md" value={taskSearch} onChange={(event) => { setTaskSearch(event.target.value); setOffset(0); }} placeholder="Buscar título ou descrição interna" aria-label="Buscar tarefas internas" />
          {platformTasksQuery.isPending ? <p role="status" aria-live="polite" className="py-12 text-center text-sm text-muted-foreground">Carregando tarefas internas…</p> : null}
          {platformTasksQuery.isError ? <Alert variant="destructive"><AlertTriangle className="h-4 w-4" /><AlertTitle>{activeTab === 'team' ? 'Escopo de equipe indisponível' : 'Tarefas indisponíveis'}</AlertTitle><AlertDescription>A API negou o escopo, detectou conflito de sessão ou está indisponível. A falha não foi convertida em lista vazia.</AlertDescription></Alert> : null}
          {!platformTasksQuery.isPending && !platformTasksQuery.isError && page?.items.length === 0 ? <div className="grid place-items-center rounded-lg border border-dashed py-12 text-center"><CheckCircle2 className="h-8 w-8 text-emerald-600" /><p className="mt-3 font-medium">Nenhuma tarefa neste recorte</p><p className="text-sm text-muted-foreground">A consulta foi concluída sem resultados.</p></div> : null}
          <div className="space-y-3">{page?.items.map((task) => <button key={task.id} type="button" className="flex w-full flex-wrap items-start justify-between gap-4 rounded-lg border p-4 text-left transition-colors hover:bg-muted/40" onClick={() => setSelectedTask(task)}><div className="min-w-0 flex-1"><div className="flex flex-wrap gap-2"><Badge variant={task.priority === 'P0' || task.priority === 'P1' ? 'destructive' : 'secondary'}>{task.priority}</Badge><Badge variant="outline">{TASK_STATUS_LABEL[task.status]}</Badge>{task.reviewRequired ? <Badge variant="secondary">Revisão: {task.reviewState}</Badge> : null}</div><h2 className="mt-2 truncate font-semibold">{task.title}</h2><p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{task.description || 'Sem descrição interna.'}</p><p className="mt-2 text-xs text-muted-foreground">Prazo geral: {formatDate(task.dueAt)} · {task.participants.filter((item) => item.role === 'assignee').length} executor(es) · versão {task.version}</p></div><span className="text-xs font-medium text-primary">Abrir tarefa</span></button>)}</div>
          <div className="flex flex-wrap items-center justify-between gap-3"><span className="text-xs text-muted-foreground">Página {currentPage} de {totalPages} · {page?.items.length ?? 0} nesta página</span><div className="flex gap-2"><Button variant="outline" disabled={!page || page.offset === 0 || platformTasksQuery.isFetching} onClick={() => setOffset((value) => Math.max(0, value - 50))}>Anterior</Button><Button variant="outline" disabled={!page?.hasMore || platformTasksQuery.isFetching} onClick={() => setOffset((value) => value + 50)}>Próxima</Button></div></div>
        </CardContent></Card>
      </TabsContent>)}

      <TabsContent value="agent-decisions" className="mt-5 space-y-4">
        <Card><CardHeader><CardTitle>Conta das chamadas humanas</CardTitle><CardDescription>Selecione a conta explicitamente. Nenhuma decisão, agente ou contexto usa o workspace implícito do operador.</CardDescription></CardHeader><CardContent><PlatformEntityCombobox entityType="accounts" purpose="agent_operations" value={agentWorkspaceId} onChange={(value) => setAgentWorkspaceId(value)} label="Selecionar conta para agentes e chamadas" placeholder="Pesquisar conta" allowClear /></CardContent></Card>
        <AgentPlatformOperationsPanel
          canManage={canPlatform(effectiveUser, 'ai_operations', 'manage') || effectiveUser.isPlatformRoot}
          isPlatformRoot={effectiveUser.isPlatformRoot}
          currentUserId={effectiveUser.id}
          workspaceId={agentWorkspaceId}
        />
      </TabsContent>

      <TabsContent value="work-items" className="mt-5 space-y-4">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><Card><CardHeader className="pb-2"><CardDescription>Itens visíveis</CardDescription></CardHeader><CardContent><p className="text-2xl font-semibold">{workItemCountsQuery.data?.total ?? 'Indisponível'}</p></CardContent></Card><Card><CardHeader className="pb-2"><CardDescription>Em aberto</CardDescription></CardHeader><CardContent><p className="text-2xl font-semibold">{workItemCountsQuery.data?.open ?? 'Indisponível'}</p></CardContent></Card><Card><CardHeader className="pb-2"><CardDescription>Vencidos</CardDescription></CardHeader><CardContent><p className="text-2xl font-semibold">{workItemCountsQuery.data?.overdue ?? 'Indisponível'}</p></CardContent></Card><Card><CardHeader className="pb-2"><CardDescription>Sem responsável</CardDescription></CardHeader><CardContent><p className="text-2xl font-semibold">{workItemCountsQuery.data?.unassigned ?? 'Indisponível'}</p></CardContent></Card></div>
        <Card><CardHeader><div className="flex flex-wrap items-start justify-between gap-3"><div><CardTitle>Work items projetados</CardTitle><CardDescription>Fila reconstruível de fontes operacionais. Cada comando volta à fonte e revalida permissão e versão.</CardDescription></div>{canReconcile ? <Button variant="outline" onClick={() => reconcileMutation.mutate()} disabled={reconcileMutation.isPending}><RefreshCw className={`mr-2 h-4 w-4 ${reconcileMutation.isPending ? 'animate-spin' : ''}`} />Reconciliar fontes</Button> : null}</div></CardHeader><CardContent className="space-y-4">
          {reconcileMutation.isError ? <Alert variant="destructive"><AlertTriangle className="h-4 w-4" /><AlertTitle>Reconciliação não concluída</AlertTitle><AlertDescription>Nenhuma falha foi convertida em sucesso. Tente novamente ou consulte a fonte original.</AlertDescription></Alert> : null}
          {reconcileMutation.data?.adapters.some((adapter) => adapter.status !== 'available') ? <Alert><AlertTriangle className="h-4 w-4" /><AlertTitle>Reconciliação parcial</AlertTitle><AlertDescription>{reconcileMutation.data.adapters.filter((adapter) => adapter.status !== 'available').map((adapter) => SOURCE_LABELS[adapter.sourceType] ?? adapter.sourceType).join(', ')} ainda possuem cobertura parcial ou indisponível.</AlertDescription></Alert> : null}
          <div className="space-y-2">
            <Label>Escopo da fila</Label>
            <div className="flex flex-wrap gap-2" role="group" aria-label="Escopo dos work items">
              {WORK_ITEM_VIEWS.map((view) => (
                <Button
                  key={view.id}
                  type="button"
                  variant={workItemView === view.id ? 'default' : 'outline'}
                  aria-pressed={workItemView === view.id}
                  onClick={() => changeWorkItemView(view.id)}
                >
                  {view.id === 'mine' ? <Clock3 className="mr-2 h-4 w-4" /> : null}
                  {view.id === 'unassigned' ? <UserRoundX className="mr-2 h-4 w-4" /> : null}
                  {view.id === 'platform' ? <UsersRound className="mr-2 h-4 w-4" /> : null}
                  {view.label}
                </Button>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">
              {WORK_ITEM_VIEWS.find((view) => view.id === workItemView)?.description}
            </p>
          </div>
          <Input
            className="max-w-md"
            value={workItemSearch}
            onChange={(event) => {
              setWorkItemSearch(event.target.value);
              setWorkItemCursor(undefined);
              setWorkItemCursorHistory([]);
            }}
            placeholder="Buscar resumo seguro"
            aria-label="Buscar work items"
          />
          {workItemsQuery.isPending ? <p role="status" aria-live="polite" className="py-10 text-center text-sm text-muted-foreground">Carregando work items…</p> : null}
          {workItemsQuery.isError ? <Alert variant="destructive"><AlertTriangle className="h-4 w-4" /><AlertTitle>Fila indisponível</AlertTitle><AlertDescription>A falha da projeção não foi convertida em fila vazia.</AlertDescription></Alert> : null}
          {!workItemsQuery.isPending && !workItemsQuery.isError && workItemsQuery.data?.items.length === 0 ? <div className="grid place-items-center rounded-lg border border-dashed py-12 text-center"><CheckCircle2 className="h-8 w-8 text-emerald-600" /><p className="mt-3 font-medium">Nenhum work item neste escopo</p><p className="text-sm text-muted-foreground">A consulta autorizada foi concluída sem resultados.</p></div> : null}
          <div className="space-y-3">{workItemsQuery.data?.items.map((item) => <article key={item.id} className="flex flex-wrap items-start justify-between gap-4 rounded-lg border p-4"><div className="min-w-0 flex-1"><div className="flex flex-wrap gap-2"><Badge variant={item.priority === 'P0' || item.priority === 'P1' ? 'destructive' : 'secondary'}>{item.priority}</Badge><Badge variant="outline">{SOURCE_LABELS[item.sourceType] ?? item.sourceType}</Badge><Badge variant="secondary">{item.state}</Badge>{item.assignedUserId === null ? <Badge variant="outline">Sem responsável</Badge> : null}</div><h2 className="mt-2 font-semibold">{item.safeTitle}</h2><p className="mt-1 text-sm text-muted-foreground">{item.safeSummary}</p><p className="mt-2 text-xs text-muted-foreground">Prazo: {formatDate(item.dueAt)}</p></div><div className="flex flex-wrap gap-2"><Button asChild size="sm" variant="outline"><Link to={safeWorkItemSourceHref(item)}>Abrir fonte</Link></Button>{item.allowedCommands.map((command) => <Button key={command} size="sm" onClick={() => openWorkItemCommand(item, command)}>{WORK_ITEM_COMMAND_LABELS[command] ?? command}</Button>)}</div></article>)}</div>
          <div className="flex justify-end gap-2"><Button variant="outline" disabled={workItemCursorHistory.length === 0 || workItemsQuery.isFetching} onClick={() => { const history = [...workItemCursorHistory]; setWorkItemCursor(history.pop()); setWorkItemCursorHistory(history); }}>Anterior</Button><Button variant="outline" disabled={workItemsQuery.isFetching || !workItemsQuery.data?.hasMore || !workItemsQuery.data.nextCursor} onClick={() => { if (!workItemsQuery.data?.nextCursor) return; setWorkItemCursorHistory((history) => [...history, workItemCursor]); setWorkItemCursor(workItemsQuery.data.nextCursor); }}>Próxima</Button></div>
        </CardContent></Card>
        <PlatformWorkflowAdministration />
      </TabsContent>
    </Tabs>

    <PlatformTaskCreateDialog open={createOpen} onOpenChange={setCreateOpen} currentUser={effectiveUser} canAssign={canAssign} onSaved={(task) => setSelectedTask(task)} />
    <PlatformTaskDrawer taskId={selectedTask?.id ?? null} summary={selectedTask} open={Boolean(selectedTask)} onOpenChange={(next) => { if (!next) setSelectedTask(null); }} currentUser={effectiveUser} />

    <Dialog open={Boolean(workItemTarget)} onOpenChange={(open) => { if (!open) closeWorkItemCommand(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Confirmar: {workItemTarget ? WORK_ITEM_COMMAND_LABELS[workItemTarget.command] ?? workItemTarget.command : 'ação'}</DialogTitle>
          <DialogDescription>{workItemTarget?.item.safeTitle}. A fonte será revalidada.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div><Label htmlFor="work-item-reason">Motivo</Label><Input id="work-item-reason" value={workItemReason} onChange={(event) => setWorkItemReason(event.target.value)} /></div>
          {workItemCommandMutation.isError ? <Alert variant="destructive"><UserRoundX className="h-4 w-4" /><AlertTitle>Ação não concluída</AlertTitle><AlertDescription>A fonte mudou, a permissão expirou ou ocorreu uma falha.</AlertDescription></Alert> : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={closeWorkItemCommand} disabled={workItemCommandMutation.isPending}>Cancelar</Button>
          <Button disabled={workItemReason.trim().length < 8 || workItemCommandMutation.isPending} onClick={() => workItemCommandMutation.mutate()}><ShieldCheck className="mr-2 h-4 w-4" />Confirmar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>;
}
