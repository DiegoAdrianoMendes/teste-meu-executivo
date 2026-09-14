import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowUpRight,
  AtSign,
  Bot,
  CheckCircle2,
  CheckSquare2,
  Circle,
  Clock3,
  Hash,
  Layers3,
  MessageSquareText,
  Plus,
  Search,
  Send,
  ShieldCheck,
  Trophy,
  UsersRound,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { KlmPanel, KlmPill, KlmProgress } from '@/klm/components/Primitives';
import { NativeModuleBar } from '@/klm/components/NativeModuleBar';
import { NativeCollaborationPanel } from '@/klm/components/NativeCollaborationPanel';
import {
  klmApi,
  type KlmMessage,
  type KlmTask,
  type KlmThread,
  type KlmWorkspaceTeam,
} from '@/klm/api';
import { cn } from '@/lib/utils';
import { useAppStore } from '@/store/app-context';

type InternalEntry = KlmMessage & { thread: KlmThread };
type RoomDefinition = {
  id: string;
  label: string;
  description: string;
  kind: 'fixed' | 'team';
  teamId?: string;
};

const fixedRooms: RoomDefinition[] = [
  { id: 'todos', label: 'operação-geral', description: 'Todas as decisões internas', kind: 'fixed' },
  { id: 'sdr', label: 'triagem-sdr', description: 'Qualificação e documentos', kind: 'fixed' },
  { id: 'juridico', label: 'jurídico', description: 'Análise e estratégia', kind: 'fixed' },
  { id: 'closer', label: 'closer-secretaria', description: 'Reunião e fechamento', kind: 'fixed' },
  { id: 'ti', label: 'ti-incidentes', description: 'Bugs e mudanças técnicas', kind: 'fixed' },
];

function roomMatches(entry: InternalEntry, room: string) {
  if (room === 'todos') return true;
  const persistedRoom = entry.messageMetadata.roomKey;
  if (typeof persistedRoom === 'string' && persistedRoom) return persistedRoom === room;
  const content = `${entry.authorLabel} ${entry.body} ${entry.thread.ownerAgentKey ?? ''}`.toLocaleLowerCase('pt-BR');
  if (room === 'sdr') return content.includes('sdr') || content.includes('triagem') || content.includes('document');
  if (room === 'juridico') return content.includes('juríd') || content.includes('jurid') || content.includes('liminar');
  if (room === 'closer') return content.includes('closer') || content.includes('secretaria') || content.includes('reuni');
  if (room === 'ti') return content.includes('ti') || content.includes('bug') || content.includes('incidente');
  return false;
}

function roomFromTeam(team: KlmWorkspaceTeam): RoomDefinition {
  return {
    id: `team:${team.id}`,
    label: team.slug,
    description: team.description || `${team.memberCount} membro(s) · ${team.openThreadCount} atendimento(s)`,
    kind: 'team',
    teamId: team.id,
  };
}

function referenceTaskId(entry: InternalEntry) {
  const references = entry.messageMetadata.references;
  if (!references || typeof references !== 'object' || Array.isArray(references)) return null;
  const taskId = (references as Record<string, unknown>).taskId;
  return typeof taskId === 'string' && taskId ? taskId : null;
}

function CaseWorkPanel({
  thread,
  tasks,
  tasksPending,
  tasksError,
  canEditTasks,
  taskTitle,
  selectedTaskId,
  creatingTask,
  togglingTask,
  onTaskTitleChange,
  onSelectTask,
  onCreateTask,
  onToggleTask,
  onOpenConversation,
  onOpenMatter,
  onRetryTasks,
}: {
  thread?: KlmThread;
  tasks: KlmTask[];
  tasksPending: boolean;
  tasksError: boolean;
  canEditTasks: boolean;
  taskTitle: string;
  selectedTaskId: string | null;
  creatingTask: boolean;
  togglingTask: boolean;
  onTaskTitleChange: (value: string) => void;
  onSelectTask: (value: string | null) => void;
  onCreateTask: () => void;
  onToggleTask: (task: KlmTask) => void;
  onOpenConversation: () => void;
  onOpenMatter: () => void;
  onRetryTasks: () => void;
}) {
  if (!thread) {
    return <div className="p-4 text-2xs leading-4 text-muted-foreground">Selecione uma mensagem ou um caso para abrir o contexto, os vínculos e as tarefas da equipe.</div>;
  }
  const openTasks = tasks.filter((task) => task.status !== 'done').length;
  return (
    <div className="space-y-4 p-4">
      <div>
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0"><p className="text-2xs font-semibold uppercase text-[#0057d8]">Contexto selecionado</p><h3 className="mt-1 truncate text-sm font-semibold">{thread.objectName ?? thread.subject ?? 'Conversa em triagem'}</h3></div>
          <KlmPill tone={thread.matterId ? 'green' : 'gold'}>{thread.matterId ? 'Caso 360' : 'sem card'}</KlmPill>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2">
          <Button variant="outline" size="sm" className="h-8 text-2xs" onClick={onOpenConversation}><MessageSquareText className="mr-1.5 h-3 w-3" />Conversa</Button>
          <Button variant="outline" size="sm" className="h-8 text-2xs" disabled={!thread.matterId} onClick={onOpenMatter}><ArrowUpRight className="mr-1.5 h-3 w-3" />Caso 360</Button>
        </div>
        <dl className="mt-3 grid grid-cols-[72px_1fr] gap-x-2 gap-y-1 text-2xs">
          <dt className="text-muted-foreground">Cliente</dt><dd className="truncate font-semibold">{thread.objectName ?? 'Identidade em consolidação'}</dd>
          <dt className="text-muted-foreground">Canal</dt><dd>{thread.primaryChannel}</dd>
          <dt className="text-muted-foreground">Responsável</dt><dd>{thread.ownerAgentKey ?? 'não atribuído'}</dd>
          <dt className="text-muted-foreground">Pendências</dt><dd>{openTasks} de {tasks.length}</dd>
        </dl>
      </div>

      <div className="border-t pt-4">
        <div className="flex items-center justify-between gap-2"><p className="flex items-center gap-1.5 text-2xs font-semibold"><CheckSquare2 className="h-3.5 w-3.5 text-[#0057d8]" />Tarefas do caso</p><KlmPill tone={openTasks ? 'gold' : 'green'}>{openTasks} abertas</KlmPill></div>
        {tasksPending && <p className="mt-3 text-2xs text-muted-foreground">Carregando tarefas...</p>}
        {tasksError && <div className="mt-3"><p className="text-2xs text-red-700">Não foi possível carregar as tarefas.</p><Button variant="ghost" size="sm" className="mt-1 h-7 px-2 text-2xs" onClick={onRetryTasks}>Tentar novamente</Button></div>}
        {!tasksPending && !tasksError && <div className="mt-3 max-h-48 space-y-1 overflow-y-auto pr-1">
          {tasks.map((task) => (
            <div key={task.id} className={cn('flex items-start gap-2 rounded-md border p-2', selectedTaskId === task.id && 'border-primary bg-primary/5')}>
              <button type="button" className="mt-0.5 shrink-0" aria-label={`${task.status === 'done' ? 'Reabrir' : 'Concluir'} tarefa: ${task.title}`} disabled={!canEditTasks || togglingTask} onClick={() => onToggleTask(task)}>{task.status === 'done' ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" /> : <Circle className="h-3.5 w-3.5 text-muted-foreground" />}</button>
              <button type="button" className="min-w-0 flex-1 text-left" onClick={() => onSelectTask(selectedTaskId === task.id ? null : task.id)}>
                <strong className={cn('block text-2xs leading-4', task.status === 'done' && 'text-muted-foreground line-through')}>{task.title}</strong>
                <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-2xs text-muted-foreground">
                  <span className="flex items-center gap-1"><Clock3 className="h-2.5 w-2.5" />{task.dueDate ? new Date(task.dueDate).toLocaleDateString('pt-BR') : 'sem prazo'} · {task.priority ?? 'sem prioridade'}</span>
                  {task.sourceExceptionId && thread?.matterId && (
                    <Link
                      to={`/app/leads?matter=${thread.matterId}`}
                      onClick={(event) => event.stopPropagation()}
                      className="inline-flex items-center gap-0.5 rounded-full border border-destructive/30 bg-destructive/10 px-1.5 py-0 text-2xs font-medium text-destructive hover:bg-destructive/20"
                      title="Esta tarefa nasceu de uma exceção — abrir o caso"
                    >
                      <AlertTriangle className="h-2.5 w-2.5 shrink-0" />gerado por falha
                    </Link>
                  )}
                </span>
              </button>
            </div>
          ))}
          {!tasks.length && <p className="py-3 text-center text-2xs text-muted-foreground">Nenhuma tarefa vinculada ao caso.</p>}
        </div>}
        <div className="mt-3 flex gap-2"><Input aria-label="Nova tarefa interna" value={taskTitle} onChange={(event) => onTaskTitleChange(event.target.value)} className="h-8 text-2xs" placeholder="Nova tarefa para a equipe" disabled={!thread.matterId || !canEditTasks} /><Button size="icon" className="h-8 w-8 shrink-0" title="Criar tarefa vinculada" disabled={!thread.matterId || !canEditTasks || !taskTitle.trim() || creatingTask} onClick={onCreateTask}><Plus className="h-3.5 w-3.5" /></Button></div>
        {selectedTaskId && <p className="mt-2 text-2xs text-[#0057d8]">A próxima mensagem citará esta tarefa.</p>}
      </div>
    </div>
  );
}

export default function InternalPage() {
  const { can, state } = useAppStore();
  const canWrite = can('mensagens', 'send_message');
  const canEditTasks = can('kanban', 'edit');
  const canViewReports = can('relatorios', 'view');
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const threadsQuery = useQuery({ queryKey: ['klm', 'threads'], queryFn: klmApi.threads });
  const agentsQuery = useQuery({ queryKey: ['klm', 'agents'], queryFn: klmApi.agents });
  const teamsQuery = useQuery({ queryKey: ['klm', 'omnichannel-teams'], queryFn: klmApi.omnichannelTeams, retry: false });
  const performanceQuery = useQuery({
    queryKey: ['klm', 'kpis', 30, null],
    queryFn: () => klmApi.kpis(30, null),
    enabled: canViewReports,
    retry: false,
  });
  const [room, setRoom] = useState('todos');
  const [query, setQuery] = useState('');
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(() => searchParams.get('thread'));
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [taskTitle, setTaskTitle] = useState('');
  const [notice, setNotice] = useState<string | null>(null);

  const rooms = useMemo(() => [
    ...fixedRooms,
    ...(teamsQuery.data ?? []).filter((team) => team.isActive).map(roomFromTeam),
  ], [teamsQuery.data]);
  const selectedRoom = rooms.find((item) => item.id === room) ?? rooms[0];
  const selectedThread = threadsQuery.data?.find((thread) => thread.id === selectedThreadId);
  const performanceRows = performanceQuery.data?.teamPerformance?.rows.slice(0, 5) ?? [];
  const highestPerformanceScore = Math.max(1, ...performanceRows.map((item) => item.score));
  const tasksQuery = useQuery({
    queryKey: ['klm', 'matter-tasks', selectedThread?.matterId],
    queryFn: () => klmApi.matterTasks(selectedThread!.matterId!),
    enabled: Boolean(selectedThread?.matterId),
    retry: false,
  });

  const internalQuery = useQuery({
    queryKey: ['klm', 'internal-messages'],
    queryFn: async () => {
      const rows = await klmApi.internalMessageFeed();
      return rows.map(({ message, thread }) => ({ ...message, thread })) satisfies InternalEntry[];
    },
  });

  const entries = useMemo(() => (internalQuery.data ?? []).filter((entry) => {
    const matchesRoom = roomMatches(entry, room);
    const search = `${entry.authorLabel} ${entry.body} ${entry.thread.subject ?? ''} ${entry.thread.objectName ?? ''}`.toLocaleLowerCase('pt-BR');
    return matchesRoom && search.includes(query.toLocaleLowerCase('pt-BR'));
  }), [internalQuery.data, query, room]);

  const sendMutation = useMutation({
    mutationFn: async () => {
      if (!selectedThread || !draft.trim()) throw new Error('Selecione um caso e escreva a mensagem interna.');
      const mentions = Array.from(new Set(draft.match(/@[A-Za-z0-9._-]+/g) ?? []));
      const message = await klmApi.sendInternalMessage(
        selectedThread.id,
        draft.trim(),
        state?.currentUser?.name?.trim() || state?.currentUser?.email || 'Usuário do workspace',
        {
          roomKey: selectedRoom.id,
          roomLabel: selectedRoom.label,
          roomType: selectedRoom.kind,
          teamId: selectedRoom.teamId ?? null,
          mentions,
          references: {
            threadId: selectedThread.id,
            matterId: selectedThread.matterId,
            clientId: selectedThread.clientId,
            taskId: selectedTaskId,
          },
        },
      );
      try {
        const projection = await klmApi.reconcileNativeCollaborationThread(selectedThread.id);
        return { message, projection, projected: true as const };
      } catch (projectionError) {
        return { message, projectionError, projected: false as const };
      }
    },
    onSuccess: (result) => {
      setDraft('');
      setNotice(result.projected
        ? 'Mensagem interna registrada com vínculos e reconciliada na sala privada da plataforma.'
        : 'Mensagem interna registrada na plataforma. A atualização da sala ficou pendente e pode ser repetida no painel de colaboração.');
      void queryClient.invalidateQueries({ queryKey: ['klm', 'internal-messages'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'messages', selectedThreadId] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'collaboration-context', selectedThreadId] });
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Não foi possível registrar a mensagem interna.'),
  });

  const createTaskMutation = useMutation({
    mutationFn: () => {
      if (!selectedThread?.matterId || !taskTitle.trim()) throw new Error('Selecione um caso e informe a tarefa.');
      return klmApi.createMatterTask(selectedThread.matterId, {
        title: taskTitle.trim(),
        description: `Criada na colaboração interna #${selectedRoom.label} e vinculada à conversa ${selectedThread.id}.`,
        priority: 'media',
        idempotencyKey: `internal-team-task:${selectedThread.id}:${crypto.randomUUID()}`,
      });
    },
    onSuccess: (task) => {
      setTaskTitle('');
      setSelectedTaskId(task.id);
      setNotice(`Tarefa “${task.title}” criada e selecionada para referência na mensagem.`);
      void queryClient.invalidateQueries({ queryKey: ['klm', 'matter-tasks', selectedThread?.matterId] });
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Não foi possível criar a tarefa.'),
  });

  const toggleTaskMutation = useMutation({
    mutationFn: (task: KlmTask) => klmApi.toggleMatterTask(task.id, task.version),
    onSuccess: (task) => {
      setNotice(`Tarefa “${task.title}” atualizada para ${task.status.replaceAll('_', ' ')}.`);
      void queryClient.invalidateQueries({ queryKey: ['klm', 'matter-tasks', selectedThread?.matterId] });
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : 'Não foi possível atualizar a tarefa.'),
  });

  const changeRoom = (nextRoom: string) => setRoom(nextRoom);
  const appendMention = (agentKey: string) => setDraft((current) => `${current}${current && !current.endsWith(' ') ? ' ' : ''}@${agentKey} `);
  const openConversation = () => selectedThread && navigate(`/app/conversas?thread=${selectedThread.id}${selectedThread.matterId ? `&matter=${selectedThread.matterId}` : ''}`);
  const openMatter = () => selectedThread?.matterId && navigate(`/app/leads?matter=${selectedThread.matterId}&thread=${selectedThread.id}`);

  useEffect(() => {
    setSelectedTaskId(null);
  }, [selectedThread?.matterId]);

  useEffect(() => {
    if (!selectedThread?.id) return;
    const selectedMatterId = selectedThread.matterId ?? null;
    if (searchParams.get('thread') === selectedThread.id && searchParams.get('matter') === selectedMatterId) return;
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set('thread', selectedThread.id);
    if (selectedMatterId) nextParams.set('matter', selectedMatterId);
    else nextParams.delete('matter');
    setSearchParams(nextParams, { replace: true });
  }, [searchParams, selectedThread?.id, selectedThread?.matterId, setSearchParams]);

  const casePanel = <CaseWorkPanel
    thread={selectedThread}
    tasks={tasksQuery.data ?? []}
    tasksPending={tasksQuery.isPending && Boolean(selectedThread?.matterId)}
    tasksError={tasksQuery.isError}
    canEditTasks={canEditTasks}
    taskTitle={taskTitle}
    selectedTaskId={selectedTaskId}
    creatingTask={createTaskMutation.isPending}
    togglingTask={toggleTaskMutation.isPending}
    onTaskTitleChange={setTaskTitle}
    onSelectTask={setSelectedTaskId}
    onCreateTask={() => createTaskMutation.mutate()}
    onToggleTask={(task) => toggleTaskMutation.mutate(task)}
    onOpenConversation={openConversation}
    onOpenMatter={openMatter}
    onRetryTasks={() => void tasksQuery.refetch()}
  />;

  return (
    <div className="mx-auto max-w-[1420px] space-y-4">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-2xs font-semibold uppercase text-[#0057d8]">Meu Executivo / Equipe</p>
          <h1 className="mt-2 text-2xl font-semibold">Comunicação interna</h1>
          <p className="mt-1 max-w-3xl text-xs leading-4 text-muted-foreground">Canais, equipes, agentes, decisões e tarefas ligados ao cliente, ao card e à conversa canônica.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2"><KlmPill tone="green">histórico local ativo</KlmPill><KlmPill tone="muted">não visível ao cliente</KlmPill>{selectedThread && <Button variant="outline" size="sm" className="h-8 text-2xs" onClick={openConversation}>Abrir conversa<ArrowUpRight className="ml-1.5 h-3 w-3" /></Button>}</div>
      </header>

      <NativeModuleBar
        domain="collaboration"
        surface="Comunicação interna"
        threadId={selectedThread?.id}
        matterId={selectedThread?.matterId}
        capabilityIds={['channels-case-rooms', 'direct-messages', 'threads-discussions', 'mentions-agent-bots', 'reactions-pins-stars', 'files-media', 'search-history', 'teams-membership']}
      />

      {canViewReports && (
        <KlmPanel className="p-4" data-testid="team-operational-gamification">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <Trophy className="h-4 w-4 text-[#0057d8]" />
                <h2 className="text-sm font-semibold">Gamificação operacional</h2>
                <KlmPill tone="blue">30 dias</KlmPill>
              </div>
              <p className="mt-1 text-2xs text-muted-foreground">
                Ranking privado da equipe desta conta, calculado por entregas e casos realmente atribuídos.
              </p>
            </div>
            <Button variant="outline" size="sm" className="h-8 text-2xs" onClick={() => navigate('/app/kpis')}>
              Abrir relatório completo
              <ArrowUpRight className="ml-1.5 h-3.5 w-3.5" />
            </Button>
          </div>
          {performanceQuery.isPending && (
            <div className="mt-3 grid gap-2 lg:grid-cols-2">
              {Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-14 w-full" />)}
            </div>
          )}
          {performanceQuery.isError && (
            <p className="mt-3 rounded-md border border-[#b94a48]/30 px-3 py-4 text-center text-2xs text-[#a53e3c]">
              Não foi possível calcular o ranking da equipe. Nenhuma pontuação substituta foi exibida.
            </p>
          )}
          {performanceQuery.isSuccess && performanceRows.length > 0 && (
            <div className="mt-3 grid gap-2 lg:grid-cols-2">
              {performanceRows.map((row, index) => (
                <div key={row.id} className="grid min-w-0 grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-3 rounded-md border px-3 py-2">
                  <span className="grid h-7 w-7 place-items-center rounded-md bg-secondary text-2xs font-semibold">{index + 1}</span>
                  <span className="min-w-0">
                    <strong className="block truncate text-xs">{row.name}</strong>
                    <small className="block truncate text-2xs text-muted-foreground">
                      {row.completedCount} concluídos · {row.pendingCount} pendentes · {row.primaryCount} casos
                    </small>
                    <span className="mt-1.5 block">
                      <KlmProgress value={row.score / highestPerformanceScore * 100} tone={index === 0 ? 'green' : 'gold'} />
                    </span>
                  </span>
                  <strong className="text-2xs">{row.score} pts</strong>
                </div>
              ))}
            </div>
          )}
          {performanceQuery.isSuccess && performanceRows.length === 0 && (
            <p className="mt-3 rounded-md border px-3 py-4 text-center text-2xs text-muted-foreground">
              O ranking aparecerá quando houver entregas ou casos atribuídos à equipe.
            </p>
          )}
        </KlmPanel>
      )}

      {notice && <div aria-live="polite" className="flex items-center justify-between gap-3 rounded-lg border border-primary/30 bg-primary/10 px-3 py-2 text-2xs"><span>{notice}</span><Button variant="ghost" size="sm" className="h-7 text-2xs" onClick={() => setNotice(null)}>Fechar</Button></div>}

      <KlmPanel className="overflow-hidden">
        <div className="grid min-h-[720px] lg:grid-cols-[250px_minmax(0,1fr)] xl:grid-cols-[250px_minmax(0,1fr)_320px]">
          <aside className="border-b bg-[#080909] text-white lg:border-b-0 lg:border-r">
            <div className="border-b border-white/10 p-4"><div className="flex items-center gap-2"><Layers3 className="h-4 w-4 text-primary" /><strong className="text-xs">Meu Executivo · Equipe</strong></div><p className="mt-1 text-2xs text-white/45">workspace interno e privado</p></div>
            <nav aria-label="Canais internos" className="flex gap-2 overflow-x-auto p-3 lg:block lg:space-y-1 lg:overflow-visible">
              {rooms.map((item, index) => <div key={item.id}>{index === fixedRooms.length && <p className="mb-2 mt-4 hidden px-3 text-2xs font-semibold uppercase text-white/35 lg:block">Equipes cadastradas</p>}<button onClick={() => changeRoom(item.id)} className={cn('min-w-[180px] rounded-md px-3 py-2.5 text-left transition lg:w-full', room === item.id ? 'bg-white/12 text-white' : 'text-white/55 hover:bg-white/7 hover:text-white')}><span className="flex items-center gap-2 text-2xs font-semibold">{item.kind === 'team' ? <UsersRound className="h-3.5 w-3.5 text-primary" /> : <Hash className="h-3.5 w-3.5 text-primary" />}{item.label}</span><span className="mt-1 block truncate pl-5 text-2xs text-white/35">{item.description}</span></button></div>)}
            </nav>
            <div className="hidden border-t border-white/10 p-4 lg:block"><div className="flex items-center justify-between text-2xs"><span className="text-white/45">Mensagens auditadas</span><strong>{internalQuery.data?.length ?? 0}</strong></div><div className="mt-2 flex items-center justify-between text-2xs"><span className="text-white/45">Equipes ativas</span><strong>{teamsQuery.data?.filter((team) => team.isActive).length ?? 0}</strong></div><div className="mt-2 flex items-center justify-between text-2xs"><span className="text-white/45">Ações externas</span><strong className="text-primary">0</strong></div></div>
          </aside>

          <section aria-label="Comunicação interna" className="flex min-h-[640px] min-w-0 flex-col bg-card">
            <div className="flex flex-col gap-3 border-b p-4 sm:flex-row sm:items-center sm:justify-between"><div><h2 className="flex items-center gap-2 text-sm font-semibold">{selectedRoom.kind === 'team' ? <UsersRound className="h-4 w-4 text-[#0057d8]" /> : <Hash className="h-4 w-4 text-[#0057d8]" />}{selectedRoom.label}</h2><p className="mt-1 text-2xs text-muted-foreground">{selectedRoom.description} · cada entrada mantém vínculos estruturados.</p></div><div className="relative w-full sm:w-64"><Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" /><Input value={query} onChange={(event) => setQuery(event.target.value)} className="h-9 bg-secondary pl-9 text-2xs" placeholder="Buscar decisão, caso ou agente" /></div></div>

            <div className="flex-1 space-y-1 overflow-y-auto p-3 sm:p-5">
              {internalQuery.isPending && <div className="flex h-40 items-center justify-center text-2xs text-muted-foreground">Carregando histórico interno...</div>}
              {internalQuery.isError && <div className="flex h-40 flex-col items-center justify-center text-center"><MessageSquareText className="h-5 w-5 text-[#a53e3c]" /><p className="mt-3 text-2xs font-semibold">Não foi possível carregar o histórico interno</p><Button variant="outline" size="sm" className="mt-3 h-8 text-2xs" onClick={() => void internalQuery.refetch()}>Tentar novamente</Button></div>}
              {!internalQuery.isPending && !internalQuery.isError && entries.length === 0 && <div className="flex h-40 flex-col items-center justify-center text-center"><MessageSquareText className="h-5 w-5 text-muted-foreground" /><p className="mt-3 text-2xs font-semibold">Nenhuma mensagem nesta visão</p><p className="mt-1 text-2xs text-muted-foreground">Ajuste o filtro ou registre uma mensagem no caso selecionado.</p></div>}
              {entries.map((entry) => {
                const isHuman = entry.authorType === 'human';
                const taskId = referenceTaskId(entry);
                return <article key={entry.id} onClick={() => setSelectedThreadId(entry.thread.id)} className={cn('group grid cursor-pointer grid-cols-[34px_1fr] gap-3 rounded-md px-2 py-3 hover:bg-secondary', selectedThreadId === entry.thread.id && 'bg-primary/8')}>
                  <span className={cn('flex h-8 w-8 items-center justify-center rounded-md', isHuman ? 'bg-emerald-700 text-white' : 'bg-[#e6e7e9] text-[#3d4248] dark:bg-[#303237] dark:text-[#d7dade]')}>{isHuman ? <UsersRound className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}</span>
                  <span className="min-w-0"><span className="flex flex-wrap items-center gap-x-2 gap-y-1"><strong className="text-2xs">{entry.authorLabel}</strong><span className="text-2xs text-muted-foreground">{new Date(entry.occurredAt).toLocaleString('pt-BR', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}</span><KlmPill tone={isHuman ? 'green' : 'muted'}>{isHuman ? 'humano' : 'agente'}</KlmPill></span><span className="mt-1 block whitespace-pre-wrap text-2xs leading-5 text-foreground/85">{entry.body}</span><span className="mt-2 flex flex-wrap items-center gap-1.5 text-2xs text-muted-foreground"><button type="button" className="rounded bg-secondary px-1.5 py-0.5 hover:text-foreground" onClick={(event) => { event.stopPropagation(); setSelectedThreadId(entry.thread.id); navigate(`/app/leads?matter=${entry.thread.matterId ?? ''}&thread=${entry.thread.id}`); }}>caso {entry.thread.objectName ?? entry.thread.subject ?? 'em triagem'}</button><button type="button" className="rounded bg-secondary px-1.5 py-0.5 hover:text-foreground" onClick={(event) => { event.stopPropagation(); navigate(`/app/conversas?thread=${entry.thread.id}${entry.thread.matterId ? `&matter=${entry.thread.matterId}` : ''}`); }}>abrir conversa</button>{taskId && <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[#0057d8]">tarefa vinculada</span>}<span className="rounded bg-secondary px-1.5 py-0.5">somente equipe</span></span></span>
                </article>;
              })}
            </div>

            <div className="border-t bg-card p-3 sm:p-4">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-2xs text-muted-foreground"><label className="flex min-w-0 flex-1 items-center gap-2"><span className="shrink-0">Contexto</span><select aria-label="Caso da nota interna" value={selectedThreadId ?? ''} onChange={(event) => setSelectedThreadId(event.target.value || null)} className="h-7 min-w-0 flex-1 rounded-md border bg-background px-2 text-2xs text-foreground"><option value="">Selecione explicitamente</option>{(threadsQuery.data ?? []).map((thread) => <option key={thread.id} value={thread.id}>{thread.objectName ?? thread.subject ?? `Conversa ${thread.id.slice(0, 8)}`}</option>)}</select></label><span className="flex items-center gap-1"><ShieldCheck className="h-3 w-3 text-emerald-600" />somente equipe</span></div>
              {agentsQuery.data?.agents.length ? <div className="mb-2 flex items-center gap-1 overflow-x-auto pb-1"><AtSign className="h-3 w-3 shrink-0 text-muted-foreground" />{agentsQuery.data.agents.slice(0, 8).map((agent) => <Button key={agent.key} type="button" variant="ghost" size="sm" className="h-6 shrink-0 px-2 text-2xs" onClick={() => appendMention(agent.key)}>@{agent.key}</Button>)}</div> : null}
              <div className="flex items-end gap-2"><Textarea value={draft} onChange={(event) => setDraft(event.target.value)} disabled={!canWrite} className="min-h-[58px] resize-none text-xs" placeholder={canWrite ? 'Mensagem interna, @menção, decisão ou orientação para a equipe...' : 'Seu perfil possui acesso somente de leitura.'} /><Button size="icon" className="h-10 w-10 shrink-0 bg-[#031121] text-white hover:bg-[#031121]" title="Enviar mensagem interna" disabled={!canWrite || !draft.trim() || !selectedThreadId || sendMutation.isPending} onClick={() => sendMutation.mutate()}><Send className="h-4 w-4" /></Button></div>
              <p className="mt-2 text-2xs text-muted-foreground">A mensagem é fonte interna restrita do contexto dos agentes; não é enviada ao cliente.</p>
            </div>
          </section>

          <aside className="hidden max-h-[780px] overflow-y-auto border-l bg-secondary/35 xl:block">
            {casePanel}
            <div className="border-t p-4"><div className="flex items-center gap-2"><UsersRound className="h-4 w-4 text-[#0057d8]" /><h2 className="text-sm font-semibold">Pessoas e agentes</h2></div><p className="mt-1 text-2xs text-muted-foreground">Estado confirmado pelo registry local.</p></div>
            <div className="max-h-52 divide-y overflow-y-auto">{agentsQuery.isPending && <div className="p-4 text-2xs text-muted-foreground">Carregando registry...</div>}{agentsQuery.isError && <div className="p-4 text-2xs text-[#a53e3c]">Registry indisponível.</div>}{agentsQuery.data?.agents.map((agent) => <button type="button" key={agent.key} className="flex w-full items-center gap-3 p-3 text-left hover:bg-secondary" onClick={() => appendMention(agent.key)}><span className="relative flex h-8 w-8 items-center justify-center rounded-md bg-[#e6e7e9] text-[#3d4248] dark:bg-[#303237] dark:text-[#d7dade]"><Bot className="h-3.5 w-3.5" /><Circle className={cn('absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 fill-current stroke-card', agent.state === 'active' ? 'text-emerald-500' : 'text-amber-500')} /></span><span className="min-w-0 flex-1"><strong className="block truncate text-2xs">{agent.name}</strong><span className="mt-0.5 block truncate text-2xs text-muted-foreground">@{agent.key} · {agent.role}</span></span></button>)}</div>
            <div className="border-t"><NativeCollaborationPanel threadId={selectedThreadId} canSync={canWrite} canNavigate={can('integrations', 'view')} /></div>
          </aside>
        </div>
      </KlmPanel>
      <div className="grid gap-4 xl:hidden"><KlmPanel>{casePanel}</KlmPanel><KlmPanel><NativeCollaborationPanel threadId={selectedThreadId} canSync={canWrite} canNavigate={can('integrations', 'view')} /></KlmPanel></div>
    </div>
  );
}
