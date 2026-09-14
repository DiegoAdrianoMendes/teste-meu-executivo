import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bot, Brain, Loader2, RefreshCw, ShieldCheck, UsersRound, Workflow, Wrench } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { KlmPanel, KlmPill } from '@/klm/components/Primitives';
import { klmApi } from '@/klm/api';

type AutomationView = 'agents' | 'flows' | 'quality' | 'knowledge' | 'tools' | 'people';

const viewCopy: Record<AutomationView, { title: string; description: string }> = {
  agents: { title: 'Agentes nativos', description: 'Registro operacional, políticas, handoff e estado executados no runtime nativo.' },
  flows: { title: 'Workflows nativos', description: 'Grafo, revisões e execuções canônicas do editor nativo.' },
  quality: { title: 'Telemetria de agentes', description: 'Execuções, handoffs, qualidade e custo lidos do runtime nativo.' },
  knowledge: { title: 'Conhecimento dos agentes', description: 'Fontes tenant-scoped e fila de indexação do RAG local.' },
  tools: { title: 'Ferramentas, grupos e avisos', description: 'Contratos de tools e colaboração configurados na plataforma.' },
  people: { title: 'Pessoas e leads', description: 'Visão canônica do CRM jurídico, sem base paralela.' },
};

export function NativeAgentOperationsPanel({ view, canManage = false }: { view: AutomationView; canManage?: boolean }) {
  const queryClient = useQueryClient();
  const contextQuery = useQuery({
    queryKey: ['klm', 'automation-context', '7d'],
    queryFn: () => klmApi.nativeAgentContext('7d'),
    retry: false,
  });
  const syncMutation = useMutation({
    mutationFn: klmApi.reconcileNativeAgents,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['klm', 'automation-context'] }),
  });
  const context = contextQuery.data;
  const copy = viewCopy[view];

  if (contextQuery.isPending) return <KlmPanel className="flex min-h-24 items-center justify-center p-4 text-2xs text-muted-foreground"><Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />Lendo o runtime nativo...</KlmPanel>;
  if (contextQuery.isError) return <KlmPanel className="p-4 text-2xs"><p className="text-red-700">O runtime nativo não respondeu.</p><Button variant="outline" size="sm" className="mt-3 h-7 text-2xs" onClick={() => void contextQuery.refetch()}><RefreshCw className="mr-1.5 h-3 w-3" />Tentar novamente</Button></KlmPanel>;
  if (!context?.linked) return <KlmPanel className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between"><div><KlmPill tone="gold">registro pendente</KlmPill><p className="mt-2 text-2xs font-semibold">Materializar agentes na plataforma</p><p className="mt-1 text-2xs leading-4 text-muted-foreground">A operação cria apenas configurações locais e não executa modelos nem canais externos.</p></div>{canManage && <Button size="sm" className="h-8 text-2xs" disabled={syncMutation.isPending} onClick={() => syncMutation.mutate()}><RefreshCw className={syncMutation.isPending ? 'mr-1.5 h-3.5 w-3.5 animate-spin' : 'mr-1.5 h-3.5 w-3.5'} />Reconciliar agentes</Button>}{syncMutation.isError && <p className="text-2xs text-red-700">{syncMutation.error instanceof Error ? syncMutation.error.message : 'Falha na reconciliação.'}</p>}</KlmPanel>;

  const agents = context.agents;
  const workflows = context.workflows;
  const knowledge = context.knowledge.items;
  const people = context.people.items;
  const botPerformance = context.performance;

  let content;
  if (view === 'agents') {
    content = <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">{agents.slice(0, 13).map((agent) => <div key={agent.id} className="border p-3"><div className="flex items-center justify-between gap-2"><strong className="truncate text-2xs">{agent.displayName || agent.name}</strong><KlmPill tone={agent.isActive ? 'green' : 'muted'}>{agent.isActive ? 'ativo' : 'pausado'}</KlmPill></div><p className="mt-2 line-clamp-2 text-2xs leading-4 text-muted-foreground">{agent.description}</p><div className="mt-2 flex flex-wrap gap-1"><KlmPill tone="muted">{agent.transferToHuman ? 'handoff' : 'sem handoff'}</KlmPill><KlmPill tone="muted">{agent.queueSize} na fila</KlmPill></div></div>)}</div>;
  } else if (view === 'flows') {
    content = workflows.length ? <div className="grid gap-2 md:grid-cols-2">{workflows.map((workflow) => <div key={workflow.id} className="flex items-center justify-between gap-3 border p-3"><div><strong className="text-2xs">{workflow.name}</strong><p className="mt-1 text-2xs text-muted-foreground">revisão {workflow.revision} · {workflow.status || 'draft'}</p></div><KlmPill tone="green">editor nativo</KlmPill></div>)}</div> : <p className="text-2xs text-muted-foreground">Nenhum workflow criado neste workspace.</p>;
  } else if (view === 'quality') {
    const byStatus = context.analytics.byStatus;
    content = <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4"><div className="border p-3"><strong className="text-2xl font-bold tabular-nums">{botPerformance.reduce((sum, row) => sum + row.totalChats, 0)}</strong><p className="text-xs text-muted-foreground">execuções no período</p></div><div className="border p-3"><strong className="text-2xl font-bold tabular-nums">{byStatus.completed ?? 0}</strong><p className="text-xs text-muted-foreground">execuções concluídas</p></div><div className="border p-3"><strong className="text-2xl font-bold tabular-nums">{byStatus.waiting_human ?? 0}</strong><p className="text-xs text-muted-foreground">aguardando humano</p></div><div className="border p-3"><strong className="text-2xl font-bold tabular-nums">{byStatus.failed ?? 0}</strong><p className="text-xs text-muted-foreground">falhas auditáveis</p></div></div>;
  } else if (view === 'knowledge') {
    content = <div className="grid gap-2 md:grid-cols-2"><div className="border p-3"><p className="text-2xs font-semibold uppercase text-[#0057d8]">Fontes ({knowledge.length})</p>{knowledge.slice(0, 6).map((item) => <p key={item.id} className="mt-2 truncate text-2xs">{item.name} <span className="text-muted-foreground">· {item.documentType}</span></p>)}{!knowledge.length && <p className="mt-2 text-2xs text-muted-foreground">Nenhuma fonte vinculada.</p>}</div><div className="border p-3"><p className="text-2xs font-semibold uppercase text-[#0057d8]">Fila ({context.knowledge.queueSize})</p><p className="mt-2 text-2xs text-muted-foreground">{context.knowledge.queueSize ? 'Itens aguardando indexação.' : 'Fila de indexação vazia.'}</p></div></div>;
  } else if (view === 'people') {
    content = <div className="grid gap-2 sm:grid-cols-4"><div className="border p-3"><strong className="text-2xl font-bold tabular-nums">{context.peopleStats.totalPeople}</strong><p className="text-xs text-muted-foreground">pessoas</p></div><div className="border p-3"><strong className="text-2xl font-bold tabular-nums">{context.peopleStats.newLeads7D}</strong><p className="text-xs text-muted-foreground">novos em 7 dias</p></div>{people.slice(0, 2).map((person) => <div key={person.id} className="border p-3"><strong className="block truncate text-xs">{person.name || 'Pessoa capturada'}</strong><p className="mt-1 truncate text-2xs text-muted-foreground">{person.stage || 'lead'}</p></div>)}</div>;
  } else {
    content = <div className="grid gap-2 sm:grid-cols-3"><div className="border p-3"><Wrench className="h-4 w-4 text-[#0057d8]" /><strong className="mt-2 block text-2xl font-bold tabular-nums">{context.tools.length}</strong><p className="text-xs text-muted-foreground">ferramentas nativas</p></div><div className="border p-3"><UsersRound className="h-4 w-4 text-[#0057d8]" /><strong className="mt-2 block text-2xl font-bold tabular-nums">{context.groups.length}</strong><p className="text-xs text-muted-foreground">grupos</p></div><div className="border p-3"><ShieldCheck className="h-4 w-4 text-[#0057d8]" /><strong className="mt-2 block text-2xl font-bold tabular-nums">{context.notifications.length}</strong><p className="text-xs text-muted-foreground">avisos operacionais</p></div></div>;
  }

  const Icon = view === 'agents' ? Bot : view === 'flows' ? Workflow : view === 'knowledge' ? Brain : view === 'tools' ? Wrench : UsersRound;
  return <KlmPanel data-testid={`automation-${view}`} className="p-4"><div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"><div><p className="flex items-center gap-2 text-2xs font-semibold"><Icon className="h-4 w-4 text-[#0057d8]" />{copy.title}</p><p className="mt-1 text-2xs text-muted-foreground">{copy.description}</p></div><div className="flex flex-wrap items-center gap-2"><KlmPill tone="green">nativo</KlmPill><KlmPill tone="muted">execução externa bloqueada</KlmPill>{context.sensitiveDataHidden && <KlmPill tone="gold">dados ocultos</KlmPill>}{canManage && <Button variant="outline" size="sm" className="h-7 text-2xs" disabled={syncMutation.isPending} onClick={() => syncMutation.mutate()}><RefreshCw className={syncMutation.isPending ? 'mr-1.5 h-3 w-3 animate-spin' : 'mr-1.5 h-3 w-3'} />Reconciliar</Button>}</div></div>{content}{syncMutation.isSuccess && <p aria-live="polite" className="mt-3 text-2xs text-emerald-700">Reconciliação concluída: {syncMutation.data.created} criado(s), {syncMutation.data.updated} atualizado(s), {syncMutation.data.seen} verificados.</p>}{syncMutation.isError && <p aria-live="polite" className="mt-3 text-2xs text-red-700">{syncMutation.error instanceof Error ? syncMutation.error.message : 'Falha ao reconciliar o runtime.'}</p>}</KlmPanel>;
}
