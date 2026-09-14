import { useEffect, useMemo, useState } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useOutletContext, useSearchParams } from 'react-router-dom';
import {
  AlertCircle,
  CalendarDays,
  CheckCircle2,
  Columns3,
  List,
  LoaderCircle,
  Plus,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  UserRoundCheck,
  UsersRound,
  X,
} from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { TaskCollaborationDrawer } from '@/klm/components/TaskCollaborationDrawer';
import LegalTaskOrganization from '@/klm/components/LegalTaskOrganization';
import { KlmPanel, KlmPill } from '@/klm/components/Primitives';
import { cn } from '@/lib/utils';
import {
  classifyTaskError,
  createClientContract,
  createCollaborativeTask,
  createContextualTask,
  listClientContracts,
  listCollaborativeTasks,
  listTaskLabelCatalog,
  listTaskFilterClientOptions,
  listTaskFilterMatterOptions,
  listTaskMatterOptions,
  listTaskMemberOptionsPage,
  taskErrorMessage,
  type CollaborativeTask,
  type TaskParticipantInput,
  type TaskPriority,
  type TaskStatus,
  type TaskView,
} from '@/services/tasks';
import { useAppStore } from '@/store/app-context';
import { workspaceExperienceForMode, type KlmOutletContext } from '@/klm/workspace-experience';

type DisplayMode = 'list' | 'board' | 'calendar';

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function isLegacyUuidFilter(value: string): boolean {
  return uuidPattern.test(value);
}

const views: Array<{ id: TaskView; label: string; icon: typeof List }> = [
  { id: 'mine', label: 'Minhas tarefas', icon: UserRoundCheck },
  { id: 'assigned_by_me', label: 'Atribuídas por mim', icon: CheckCircle2 },
  { id: 'reviews', label: 'Revisões', icon: ShieldCheck },
  { id: 'team', label: 'Minha equipe', icon: UsersRound },
  { id: 'overdue', label: 'Atrasadas', icon: CalendarDays },
  { id: 'blocked', label: 'Bloqueadas', icon: AlertCircle },
  { id: 'completed', label: 'Concluídas', icon: CheckCircle2 },
];

const statusLabel = {
  pending: 'Pendente',
  in_progress: 'Em andamento',
  blocked: 'Bloqueada',
  done: 'Concluída',
  cancelled: 'Cancelada',
} as const;

function taskStatus(task: CollaborativeTask): keyof typeof statusLabel {
  return task.cancelledAt ? 'cancelled' : task.status;
}

function taskStatusTone(task: CollaborativeTask): 'green' | 'red' | 'blue' | 'muted' {
  const visibleStatus = taskStatus(task);
  if (visibleStatus === 'done') return 'green';
  if (visibleStatus === 'blocked') return 'red';
  if (visibleStatus === 'cancelled') return 'muted';
  return 'blue';
}

function formatDate(value: string | null | undefined): string {
  if (!value) return 'Sem prazo';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' }).format(date);
}

function dueTone(task: CollaborativeTask): 'green' | 'red' | 'muted' | 'gold' {
  if (task.cancelledAt) return 'muted';
  if (task.status === 'done') return 'green';
  if (task.status === 'blocked') return 'red';
  if (task.dueAt && new Date(task.dueAt).getTime() < Date.now()) return 'red';
  return task.dueAt ? 'gold' : 'muted';
}

function localDayStartIso(value: string): string | null {
  if (!value) return null;
  const date = new Date(`${value}T00:00:00`);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function localDayEndExclusiveIso(value: string): string | null {
  if (!value) return null;
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return null;
  date.setDate(date.getDate() + 1);
  return date.toISOString();
}

function TaskCard({ task, onOpen, compact = false }: { task: CollaborativeTask; onOpen: () => void; compact?: boolean }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        'w-full rounded-lg border bg-card p-4 text-left shadow-[var(--shadow-card)] transition hover:-translate-y-0.5 hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        compact && 'p-3',
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="line-clamp-2 text-sm font-semibold text-foreground">{task.title}</h3>
          <p className="mt-1 truncate text-xs text-muted-foreground">
            {[task.clientLabel, task.matterLabel].filter(Boolean).join(' · ') ||
              (task.contextType === 'workspace' ? 'Escritório' : 'Contexto não identificado')}
          </p>
        </div>
        <KlmPill tone={taskStatusTone(task)}>{statusLabel[taskStatus(task)]}</KlmPill>
      </div>
      {!compact && task.description && <p className="mt-3 line-clamp-2 text-xs leading-5 text-muted-foreground">{task.description}</p>}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <KlmPill tone={dueTone(task)}>{formatDate(task.dueAt ?? task.dueDate ?? task.legacyDueText)}</KlmPill>
        {task.reviewState === 'pending' && <KlmPill tone="gold">Aguardando revisão</KlmPill>}
        {task.priority && <KlmPill tone={task.priority === 'alta' ? 'red' : 'muted'}>{task.priority}</KlmPill>}
        {task.participants.length > 0 && <span className="ml-auto text-2xs text-muted-foreground">{task.participants.filter((item) => item.active).length} participantes</span>}
      </div>
    </button>
  );
}

function EmptyState({ view }: { view: TaskView }) {
  const current = views.find((item) => item.id === view);
  const Icon = current?.icon ?? CheckCircle2;
  return (
    <div className="grid min-h-72 place-items-center rounded-lg border border-dashed bg-card p-8 text-center">
      <div>
        <Icon className="mx-auto h-9 w-9 text-muted-foreground/60" />
        <h2 className="mt-4 text-sm font-semibold">Nenhuma tarefa nesta visão</h2>
        <p className="mt-1 text-xs text-muted-foreground">A fila “{current?.label}” está vazia para os filtros atuais.</p>
      </div>
    </div>
  );
}

function Board({ items, onOpen }: { items: CollaborativeTask[]; onOpen: (task: CollaborativeTask) => void }) {
  const columns = [
    { status: 'pending', label: 'Pendente' },
    { status: 'in_progress', label: 'Em andamento' },
    { status: 'blocked', label: 'Bloqueada' },
    { status: 'done', label: 'Concluída' },
    { status: 'cancelled', label: 'Cancelada' },
  ] as const;
  return (
    <div className="grid min-w-[1120px] grid-cols-5 gap-3">
      {columns.map((column) => {
        const columnItems = items.filter((item) => taskStatus(item) === column.status);
        return (
          <section key={column.status} className="rounded-lg border bg-secondary/30 p-3">
            <div className="mb-3 flex items-center justify-between"><h2 className="text-xs font-semibold">{column.label}</h2><span className="text-2xs text-muted-foreground">{columnItems.length}</span></div>
            <div className="space-y-2">{columnItems.length ? columnItems.map((task) => <TaskCard key={task.id} task={task} compact onOpen={() => onOpen(task)} />) : <p className="rounded-md border border-dashed p-5 text-center text-2xs text-muted-foreground">Sem tarefas</p>}</div>
          </section>
        );
      })}
    </div>
  );
}

function CalendarView({ items, onOpen }: { items: CollaborativeTask[]; onOpen: (task: CollaborativeTask) => void }) {
  const groups = new Map<string, CollaborativeTask[]>();
  items.forEach((task) => {
    const value = task.dueAt ?? task.dueDate;
    const date = value ? new Date(value) : null;
    const key = date && !Number.isNaN(date.getTime()) ? date.toISOString().slice(0, 10) : 'sem-prazo';
    groups.set(key, [...(groups.get(key) ?? []), task]);
  });
  const ordered = [...groups.entries()].sort(([left], [right]) => {
    if (left === 'sem-prazo') return 1;
    if (right === 'sem-prazo') return -1;
    return left.localeCompare(right);
  });
  return (
    <div className="space-y-4">
      {ordered.map(([dateKey, tasks]) => (
        <section key={dateKey}>
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">{dateKey === 'sem-prazo' ? 'Sem prazo definido' : new Intl.DateTimeFormat('pt-BR', { dateStyle: 'full' }).format(new Date(`${dateKey}T12:00:00`))}</h2>
          <div className="grid gap-2 lg:grid-cols-2">{tasks.map((task) => <TaskCard key={task.id} task={task} compact onOpen={() => onOpen(task)} />)}</div>
        </section>
      ))}
    </div>
  );
}

export default function TasksPage() {
  const outletContext = useOutletContext<KlmOutletContext | null>();
  const workspaceExperience = outletContext?.workspaceExperience ?? workspaceExperienceForMode('general');
  const {
    can,
    state,
    capabilityManifestStatus = 'ready',
    getCapability = () => null,
    // A manifest that was not loaded/provided never grants a paid capability.
    // The backend remains authoritative, but the client must fail closed too.
    canUseCapability = () => false,
  } = useAppStore();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawView = searchParams.get('view');
  const view: TaskView = views.some((item) => item.id === rawView) ? rawView as TaskView : 'mine';
  const rawMode = searchParams.get('mode');
  const displayMode: DisplayMode = rawMode === 'board' || rawMode === 'calendar' ? rawMode : 'list';
  const querySearch = searchParams.get('q') ?? '';
  const [search, setSearch] = useState(querySearch);
  const [debouncedSearch, setDebouncedSearch] = useState(search);
  const matterFilter = searchParams.get('matter') ?? '';
  const clientFilter = searchParams.get('client') ?? '';
  const ownerFilter = searchParams.get('owner') ?? '';
  const assigneeFilter = searchParams.get('assignee') ?? '';
  const reviewerFilter = searchParams.get('reviewer') ?? '';
  const labelFilter = searchParams.get('label') ?? '';
  const matterFilterIsLegacy = isLegacyUuidFilter(matterFilter);
  const clientFilterIsLegacy = isLegacyUuidFilter(clientFilter);
  const ownerFilterIsLegacy = isLegacyUuidFilter(ownerFilter);
  const assigneeFilterIsLegacy = isLegacyUuidFilter(assigneeFilter);
  const reviewerFilterIsLegacy = isLegacyUuidFilter(reviewerFilter);
  const hasLegacyEntityFilter = matterFilterIsLegacy
    || clientFilterIsLegacy
    || ownerFilterIsLegacy
    || assigneeFilterIsLegacy
    || reviewerFilterIsLegacy;
  const rawPriorityFilter = searchParams.get('priority');
  const priorityFilter: TaskPriority | '' = (
    rawPriorityFilter === 'alta'
    || rawPriorityFilter === 'media'
    || rawPriorityFilter === 'baixa'
  ) ? rawPriorityFilter : '';
  const rawStatusFilter = searchParams.get('status');
  const statusFilter: TaskStatus | '' = (
    rawStatusFilter === 'pending'
    || rawStatusFilter === 'in_progress'
    || rawStatusFilter === 'blocked'
    || rawStatusFilter === 'done'
  ) ? rawStatusFilter : '';
  const periodFrom = searchParams.get('from') ?? '';
  const periodTo = searchParams.get('to') ?? '';
  const [filterMatterSearch, setFilterMatterSearch] = useState('');
  const [debouncedFilterMatterSearch, setDebouncedFilterMatterSearch] = useState('');
  const [filterClientSearch, setFilterClientSearch] = useState('');
  const [debouncedFilterClientSearch, setDebouncedFilterClientSearch] = useState('');
  const [filterMemberSearch, setFilterMemberSearch] = useState('');
  const [debouncedFilterMemberSearch, setDebouncedFilterMemberSearch] = useState('');
  const [selectedTask, setSelectedTask] = useState<CollaborativeTask | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [matterId, setMatterId] = useState('');
  const [createContextType, setCreateContextType] = useState<'workspace' | 'client' | 'contract' | 'matter'>('matter');
  const [createClientId, setCreateClientId] = useState('');
  const [createClientSearch, setCreateClientSearch] = useState('');
  const [debouncedCreateClientSearch, setDebouncedCreateClientSearch] = useState('');
  const [createContractId, setCreateContractId] = useState('');
  const [createContractSearch, setCreateContractSearch] = useState('');
  const [newContractTitle, setNewContractTitle] = useState('');
  const [matterSearch, setMatterSearch] = useState('');
  const [debouncedMatterSearch, setDebouncedMatterSearch] = useState('');
  const [memberSearch, setMemberSearch] = useState('');
  const [debouncedMemberSearch, setDebouncedMemberSearch] = useState('');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [priority, setPriority] = useState<TaskPriority | 'none'>('none');
  const [dueAt, setDueAt] = useState('');
  const [reviewDueAt, setReviewDueAt] = useState('');
  const [ownerId, setOwnerId] = useState('');
  const [assigneeIds, setAssigneeIds] = useState<string[]>([]);
  const [reviewerId, setReviewerId] = useState('');

  const activeTasksCapability = getCapability('active_tasks');
  const participantCapability = getCapability('task_participants');
  const reviewCapability = getCapability('task_reviews');
  const hasCreatePermission = can('tasks', 'create');
  const canCreate = hasCreatePermission && canUseCapability('active_tasks');
  const canAssign = can('tasks', 'assign');
  const reviewEnabled = Boolean(
    reviewCapability?.contracted
      && reviewCapability.enabled
      && reviewCapability.permitted,
  );
  const participantLimit = participantCapability?.limit ?? null;

  useEffect(() => {
    if (searchParams.get('action') !== 'new' || !canCreate) return;
    setCreateOpen(true);
    const next = new URLSearchParams(searchParams);
    next.delete('action');
    setSearchParams(next, { replace: true });
  }, [canCreate, searchParams, setSearchParams]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedCreateClientSearch(createClientSearch.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [createClientSearch]);

  useEffect(() => {
    setSearch(querySearch);
    setDebouncedSearch(querySearch);
  }, [querySearch]);

  useEffect(() => {
    const timer = window.setTimeout(
      () => setDebouncedFilterMatterSearch(filterMatterSearch.trim()),
      250,
    );
    return () => window.clearTimeout(timer);
  }, [filterMatterSearch]);

  useEffect(() => {
    const timer = window.setTimeout(
      () => setDebouncedFilterClientSearch(filterClientSearch.trim()),
      250,
    );
    return () => window.clearTimeout(timer);
  }, [filterClientSearch]);

  useEffect(() => {
    const timer = window.setTimeout(
      () => setDebouncedFilterMemberSearch(filterMemberSearch.trim()),
      250,
    );
    return () => window.clearTimeout(timer);
  }, [filterMemberSearch]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedMatterSearch(matterSearch.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [matterSearch]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedMemberSearch(memberSearch.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [memberSearch]);

  useEffect(() => {
    const next = new URLSearchParams(searchParams);
    if (debouncedSearch) next.set('q', debouncedSearch); else next.delete('q');
    if (next.toString() !== searchParams.toString()) setSearchParams(next, { replace: true });
  }, [debouncedSearch, searchParams, setSearchParams]);

  const periodInvalid = Boolean(periodFrom && periodTo && periodFrom > periodTo);
  const tasksQuery = useInfiniteQuery({
    queryKey: [
      'collaborative-tasks',
      view,
      debouncedSearch,
      matterFilter,
      clientFilter,
      ownerFilter,
      assigneeFilter,
      reviewerFilter,
      labelFilter,
      priorityFilter,
      statusFilter,
      periodFrom,
      periodTo,
    ],
    queryFn: ({ pageParam }) => listCollaborativeTasks({
      view,
      search: debouncedSearch,
      cursor: pageParam,
      limit: 50,
      matterRef: matterFilter && !matterFilterIsLegacy ? matterFilter : null,
      clientRef: clientFilter && !clientFilterIsLegacy ? clientFilter : null,
      ownerRef: ownerFilter && !ownerFilterIsLegacy ? ownerFilter : null,
      assigneeRef: assigneeFilter && !assigneeFilterIsLegacy ? assigneeFilter : null,
      reviewerRef: reviewerFilter && !reviewerFilterIsLegacy ? reviewerFilter : null,
      labelRef: workspaceExperience.taskExperience === 'legal_360' ? labelFilter || null : null,
      priority: priorityFilter || null,
      status: statusFilter || null,
      dueFrom: localDayStartIso(periodFrom),
      dueTo: localDayEndExclusiveIso(periodTo),
    }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.hasMore ? lastPage.nextCursor : undefined,
    enabled: !periodInvalid && !hasLegacyEntityFilter,
    retry: false,
  });
  const validMatterSearch = debouncedMatterSearch.length === 0 || debouncedMatterSearch.length >= 2;
  const validMemberSearch = debouncedMemberSearch.length === 0 || debouncedMemberSearch.length >= 2;
  const mattersQuery = useQuery({ queryKey: ['task-matter-options', debouncedMatterSearch], queryFn: () => listTaskMatterOptions(debouncedMatterSearch), enabled: createOpen && canCreate && validMatterSearch, staleTime: 30_000, retry: false });
  const validCreateClientSearch = debouncedCreateClientSearch.length === 0 || debouncedCreateClientSearch.length >= 2;
  const createClientsQuery = useQuery({
    queryKey: ['task-create-client-options', debouncedCreateClientSearch],
    queryFn: () => listTaskFilterClientOptions('workspace', debouncedCreateClientSearch),
    enabled: createOpen && canCreate && workspaceExperience.taskExperience === 'legal_360' && (createContextType === 'client' || createContextType === 'contract') && validCreateClientSearch,
    staleTime: 30_000,
    retry: false,
  });
  const contractsQuery = useQuery({
    queryKey: ['task-create-contract-options', createClientId, createContractSearch],
    queryFn: () => listClientContracts(createClientId, createContractSearch),
    enabled: createOpen && canCreate && createContextType === 'contract' && Boolean(createClientId),
    staleTime: 30_000,
    retry: false,
  });
  const createContractMutation = useMutation({
    mutationFn: () => createClientContract({ clientId: createClientId, title: newContractTitle.trim() }),
    onSuccess: async (contract) => {
      setCreateContractId(contract.id);
      setNewContractTitle('');
      await queryClient.invalidateQueries({ queryKey: ['task-create-contract-options', createClientId] });
      toast({ title: 'Contrato criado', description: 'O contrato jurídico já pode ser usado como contexto da tarefa.' });
    },
    onError: (error) => toast({ variant: 'destructive', title: 'Não foi possível criar o contrato', description: taskErrorMessage(error, 'Revise os dados e tente novamente.') }),
  });
  const membersQuery = useInfiniteQuery({
    queryKey: ['task-member-options', debouncedMemberSearch],
    queryFn: ({ pageParam }) => listTaskMemberOptionsPage(debouncedMemberSearch, pageParam, 100),
    enabled: createOpen && canAssign && validMemberSearch,
    initialPageParam: 0,
    getNextPageParam: (lastPage) => lastPage.hasMore ? lastPage.offset + lastPage.items.length : undefined,
    staleTime: 60_000,
    retry: false,
  });
  const validFilterMatterSearch = debouncedFilterMatterSearch.length === 0
    || debouncedFilterMatterSearch.length >= 2;
  const validFilterClientSearch = debouncedFilterClientSearch.length === 0
    || debouncedFilterClientSearch.length >= 2;
  const validFilterMemberSearch = debouncedFilterMemberSearch.length === 0
    || debouncedFilterMemberSearch.length >= 2;
  const filterMattersQuery = useQuery({
    queryKey: ['task-filter-matter-options', view, debouncedFilterMatterSearch, matterFilter],
    queryFn: () => listTaskFilterMatterOptions(
      view,
      debouncedFilterMatterSearch,
      matterFilter && !matterFilterIsLegacy ? [matterFilter] : [],
      matterFilterIsLegacy ? [matterFilter] : [],
    ),
    enabled: validFilterMatterSearch,
    staleTime: 30_000,
    retry: false,
  });
  const filterClientsQuery = useQuery({
    queryKey: ['task-filter-client-options', view, debouncedFilterClientSearch, clientFilter],
    queryFn: () => listTaskFilterClientOptions(
      view,
      debouncedFilterClientSearch,
      clientFilter && !clientFilterIsLegacy ? [clientFilter] : [],
      clientFilterIsLegacy ? [clientFilter] : [],
    ),
    enabled: validFilterClientSearch,
    staleTime: 30_000,
    retry: false,
  });
  const labelCatalogQuery = useQuery({
    queryKey: ['task-label-catalog'],
    queryFn: listTaskLabelCatalog,
    enabled: workspaceExperience.taskExperience === 'legal_360',
    staleTime: 60_000,
    retry: false,
  });
  const selectedFilterMemberRefs = [
    !ownerFilterIsLegacy ? ownerFilter : '',
    !assigneeFilterIsLegacy ? assigneeFilter : '',
    !reviewerFilterIsLegacy ? reviewerFilter : '',
  ].filter(Boolean);
  const selectedLegacyFilterMemberIds = [
    ownerFilterIsLegacy ? ownerFilter : '',
    assigneeFilterIsLegacy ? assigneeFilter : '',
    reviewerFilterIsLegacy ? reviewerFilter : '',
  ].filter(Boolean);
  const filterMembersQuery = useQuery({
    queryKey: [
      'task-filter-member-options',
      view,
      debouncedFilterMemberSearch,
      ...selectedFilterMemberRefs,
      ...selectedLegacyFilterMemberIds,
    ],
    queryFn: () => listTaskMemberOptionsPage(
      debouncedFilterMemberSearch,
      0,
      100,
      selectedFilterMemberRefs,
      view,
      selectedLegacyFilterMemberIds,
    ),
    enabled: validFilterMemberSearch,
    staleTime: 60_000,
    retry: false,
  });
  const items = useMemo(() => tasksQuery.data?.pages.flatMap((page) => page.items) ?? [], [tasksQuery.data]);
  const memberOptions = useMemo(
    () => membersQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [membersQuery.data],
  );
  const filterMemberOptions = useMemo(
    () => filterMembersQuery.data?.items ?? [],
    [filterMembersQuery.data],
  );

  useEffect(() => {
    if (!hasLegacyEntityFilter) return;
    const next = new URLSearchParams(searchParams);
    let changed = false;

    const replaceLegacy = (
      key: string,
      value: string,
      legacy: boolean,
      ready: boolean,
      options: Array<{ id: string; handle: string }> | undefined,
    ) => {
      if (!legacy || !ready) return;
      const match = options?.find((option) => option.id === value);
      if (match) next.set(key, match.handle);
      else next.delete(key);
      changed = true;
    };

    replaceLegacy(
      'matter',
      matterFilter,
      matterFilterIsLegacy,
      filterMattersQuery.isSuccess,
      filterMattersQuery.data,
    );
    replaceLegacy(
      'client',
      clientFilter,
      clientFilterIsLegacy,
      filterClientsQuery.isSuccess,
      filterClientsQuery.data,
    );
    replaceLegacy(
      'owner',
      ownerFilter,
      ownerFilterIsLegacy,
      filterMembersQuery.isSuccess,
      filterMemberOptions,
    );
    replaceLegacy(
      'assignee',
      assigneeFilter,
      assigneeFilterIsLegacy,
      filterMembersQuery.isSuccess,
      filterMemberOptions,
    );
    replaceLegacy(
      'reviewer',
      reviewerFilter,
      reviewerFilterIsLegacy,
      filterMembersQuery.isSuccess,
      filterMemberOptions,
    );

    if (changed) setSearchParams(next, { replace: true });
  }, [
    assigneeFilter,
    assigneeFilterIsLegacy,
    clientFilter,
    clientFilterIsLegacy,
    filterClientsQuery.data,
    filterClientsQuery.isSuccess,
    filterMattersQuery.data,
    filterMattersQuery.isSuccess,
    filterMemberOptions,
    filterMembersQuery.isSuccess,
    hasLegacyEntityFilter,
    matterFilter,
    matterFilterIsLegacy,
    ownerFilter,
    ownerFilterIsLegacy,
    reviewerFilter,
    reviewerFilterIsLegacy,
    searchParams,
    setSearchParams,
  ]);

  useEffect(() => {
    if (!createOpen || !canAssign || ownerId || memberOptions.length === 0) return;
    const currentUserIsWorkspaceMember = memberOptions.some((member) => member.id === state.currentUser?.id);
    if (currentUserIsWorkspaceMember) setOwnerId(state.currentUser!.id);
  }, [canAssign, createOpen, memberOptions, ownerId, state.currentUser]);

  const createParticipantCount = (ownerId ? 1 : 0)
    + assigneeIds.filter((id) => id !== ownerId && id !== reviewerId).length
    + (reviewerId ? 1 : 0);
  const participantLimitReached = participantLimit != null
    && createParticipantCount >= participantLimit;
  const participantLimitExceeded = participantLimit != null
    && createParticipantCount > participantLimit;
  const createDateError = Boolean(
    dueAt
      && reviewDueAt
      && new Date(reviewDueAt).getTime() > new Date(dueAt).getTime(),
  );

  const createMutation = useMutation({
    mutationFn: () => {
      const participants: TaskParticipantInput[] | undefined = canAssign ? [
        { userId: ownerId, role: 'owner' },
        ...assigneeIds
          .filter((id) => id !== ownerId && id !== reviewerId)
          .map((userId): TaskParticipantInput => ({
            userId,
            role: 'assignee',
            requiredForCompletion: true,
          })),
        ...(reviewEnabled && reviewerId
          ? [{ userId: reviewerId, role: 'reviewer' } satisfies TaskParticipantInput]
          : []),
      ] : undefined;
      const common = {
        title: title.trim(),
        description: description.trim() || null,
        priority: priority === 'none' ? null : priority,
        dueAt: dueAt ? new Date(dueAt).toISOString() : null,
        reviewDueAt: canAssign && reviewEnabled && reviewDueAt ? new Date(reviewDueAt).toISOString() : null,
        reviewRequired: canAssign && reviewEnabled && Boolean(reviewerId),
        participants,
      };
      if (workspaceExperience.taskExperience === 'legal_360' && createContextType !== 'matter') {
        return createContextualTask({
          ...common,
          context: createContextType === 'workspace'
            ? { type: 'workspace' }
            : createContextType === 'contract'
              ? { type: 'contract', clientId: createClientId, contractId: createContractId }
              : { type: 'client', clientId: createClientId },
          origin: 'manual',
        });
      }
      return createCollaborativeTask({ ...common, matterId });
    },
    onSuccess: async (task) => {
      setCreateOpen(false);
      setMatterId('');
      setCreateContextType('matter');
      setCreateClientId('');
      setCreateClientSearch('');
      setCreateContractId('');
      setCreateContractSearch('');
      setNewContractTitle('');
      setMatterSearch('');
      setMemberSearch('');
      setTitle('');
      setDescription('');
      setPriority('none');
      setDueAt('');
      setReviewDueAt('');
      setOwnerId('');
      setAssigneeIds([]);
      setReviewerId('');
      await queryClient.invalidateQueries({ queryKey: ['collaborative-tasks'] });
      toast({ title: 'Tarefa criada', description: task.contextType === 'matter' ? 'A tarefa foi vinculada ao Caso 360 e já está disponível na fila.' : 'A tarefa foi criada no contexto selecionado e já está disponível na fila.' });
      setSelectedTask(task);
      setDrawerOpen(true);
    },
    onError: (error) => {
      const kind = classifyTaskError(error);
      toast({
        variant: 'destructive',
        title: kind === 'quota' ? 'Limite do plano atingido' : kind === 'forbidden' ? 'Sem permissão' : 'Não foi possível criar a tarefa',
        description: taskErrorMessage(error, 'Revise os dados e tente novamente.'),
      });
    },
  });

  const changeView = (nextView: TaskView) => {
    const next = new URLSearchParams(searchParams);
    next.set('view', nextView);
    setSearchParams(next);
  };
  const changeMode = (mode: DisplayMode) => {
    const next = new URLSearchParams(searchParams);
    if (mode === 'list') next.delete('mode'); else next.set('mode', mode);
    setSearchParams(next);
  };
  const changeFilter = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value); else next.delete(key);
    setSearchParams(next, { replace: true });
  };
  const clearOperationalFilters = () => {
    const next = new URLSearchParams(searchParams);
    ['matter', 'client', 'owner', 'assignee', 'reviewer', 'label', 'priority', 'status', 'from', 'to']
      .forEach((key) => next.delete(key));
    setFilterMatterSearch('');
    setFilterClientSearch('');
    setFilterMemberSearch('');
    setSearchParams(next, { replace: true });
  };
  const openTask = (task: CollaborativeTask) => {
    setSelectedTask(task);
    setDrawerOpen(true);
  };

  const errorKind = tasksQuery.isError ? classifyTaskError(tasksQuery.error) : null;
  const selectedMatter = mattersQuery.data?.find((matter) => matter.id === matterId);
  const filteredReviewerOptions = memberOptions.filter((member) => member.id !== ownerId && !assigneeIds.includes(member.id));
  const activeOperationalFilterCount = [
    matterFilter,
    clientFilter,
    ownerFilter,
    assigneeFilter,
    reviewerFilter,
    workspaceExperience.taskExperience === 'legal_360' ? labelFilter : '',
    priorityFilter,
    statusFilter,
    periodFrom,
    periodTo,
  ].filter(Boolean).length;
  const selectedFilterMatter = filterMattersQuery.data?.find(
    (matter) => matter.handle === matterFilter || matter.id === matterFilter,
  );
  const selectedFilterClient = filterClientsQuery.data?.find(
    (client) => client.handle === clientFilter || client.id === clientFilter,
  );

  return (
    <div className="min-h-full overflow-y-auto bg-background px-4 py-5 sm:px-6 lg:px-8" data-testid="tasks-page">
      <div className="mx-auto max-w-[1500px] space-y-5">
        <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-primary"><CheckCircle2 className="h-4 w-4" />Operação colaborativa</div>
            <h2 className="mt-2 text-2xl font-bold tracking-tight">Tarefas</h2>
            <p className="mt-1 max-w-2xl text-sm text-muted-foreground">Responsável, executores, revisão, prazos e histórico conectados ao Caso 360.</p>
          </div>
          {hasCreatePermission && (
            <Button
              onClick={() => setCreateOpen(true)}
              disabled={!canCreate}
              title={canCreate ? 'Criar tarefa' : 'O plano ou a quota atual não permite novas tarefas'}
            ><Plus className="mr-2 h-4 w-4" />Nova tarefa</Button>
          )}
        </header>

        {hasCreatePermission && !canCreate && capabilityManifestStatus === 'ready' && (
          <Alert>
            <AlertCircle className="h-4 w-4" />
            <AlertTitle>{activeTasksCapability?.denialReason === 'quota_exhausted' ? 'Limite de tarefas atingido' : 'Criação de tarefas indisponível'}</AlertTitle>
            <AlertDescription>
              {activeTasksCapability?.used != null && activeTasksCapability.limit != null
                ? `${activeTasksCapability.used} de ${activeTasksCapability.limit} tarefas ativas estão em uso. A consulta e o histórico permanecem disponíveis.`
                : 'A consulta e o histórico permanecem disponíveis, mas uma nova tarefa depende da configuração comercial da conta.'}
            </AlertDescription>
          </Alert>
        )}

        <KlmPanel className="p-3">
          <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
            <div className="overflow-x-auto">
              <Tabs value={view} onValueChange={(value) => changeView(value as TaskView)}>
                <TabsList className="h-auto min-w-max flex-wrap justify-start bg-transparent p-0">
                  {views.map((item) => <TabsTrigger key={item.id} value={item.id} className="gap-1.5"><item.icon className="h-3.5 w-3.5" />{item.label}</TabsTrigger>)}
                </TabsList>
              </Tabs>
            </div>
            <div className="flex flex-col gap-2 sm:flex-row">
              <div className="relative min-w-0 sm:w-72"><Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" /><Input value={search} onChange={(event) => setSearch(event.target.value)} className="pl-9" placeholder="Buscar por tarefa, caso ou cliente" aria-label="Buscar tarefas" /></div>
              <div className="flex rounded-md border bg-card p-1" role="group" aria-label="Modo de visualização">
                <Button size="sm" variant={displayMode === 'list' ? 'secondary' : 'ghost'} onClick={() => changeMode('list')} aria-label="Lista" aria-pressed={displayMode === 'list'}><List className="h-4 w-4" /></Button>
                <Button size="sm" variant={displayMode === 'board' ? 'secondary' : 'ghost'} onClick={() => changeMode('board')} aria-label="Quadro" aria-pressed={displayMode === 'board'}><Columns3 className="h-4 w-4" /></Button>
                <Button size="sm" variant={displayMode === 'calendar' ? 'secondary' : 'ghost'} onClick={() => changeMode('calendar')} aria-label="Calendário" aria-pressed={displayMode === 'calendar'}><CalendarDays className="h-4 w-4" /></Button>
              </div>
            </div>
          </div>
          <div className="mt-3 border-t pt-3">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-xs font-semibold">
                <SlidersHorizontal className="h-4 w-4 text-primary" />
                Filtros operacionais
                {activeOperationalFilterCount > 0 && (
                  <KlmPill tone="blue">{activeOperationalFilterCount} ativos</KlmPill>
                )}
              </div>
              {activeOperationalFilterCount > 0 && (
                <Button size="sm" variant="ghost" onClick={clearOperationalFilters}>
                  <X className="mr-1.5 h-3.5 w-3.5" />
                  Limpar filtros
                </Button>
              )}
            </div>

            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              <div className="space-y-2">
                <Label htmlFor="task-filter-matter-search">Caso 360</Label>
                <Input
                  id="task-filter-matter-search"
                  value={filterMatterSearch}
                  onChange={(event) => setFilterMatterSearch(event.target.value)}
                  placeholder="Pesquisar caso ou cliente"
                />
                <Select
                  value={matterFilter && !matterFilterIsLegacy ? matterFilter : 'all'}
                  onValueChange={(value) => changeFilter('matter', value === 'all' ? '' : value)}
                >
                  <SelectTrigger aria-label="Filtrar por caso">
                    <SelectValue
                      placeholder={
                        !validFilterMatterSearch
                          ? 'Digite pelo menos 2 caracteres'
                          : 'Todos os casos'
                      }
                    />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Todos os casos</SelectItem>
                    {matterFilter && !matterFilterIsLegacy && !selectedFilterMatter && (
                      <SelectItem value={matterFilter}>Caso selecionado no Caso 360</SelectItem>
                    )}
                    {filterMattersQuery.data?.map((matter) => (
                      <SelectItem key={matter.id} value={matter.handle}>
                        {matter.clientLabel} · {matter.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="task-filter-client-search">Cliente</Label>
                <Input
                  id="task-filter-client-search"
                  value={filterClientSearch}
                  onChange={(event) => setFilterClientSearch(event.target.value)}
                  placeholder="Pesquisar cliente"
                />
                <Select
                  value={clientFilter && !clientFilterIsLegacy ? clientFilter : 'all'}
                  onValueChange={(value) => changeFilter('client', value === 'all' ? '' : value)}
                >
                  <SelectTrigger aria-label="Filtrar por cliente">
                    <SelectValue
                      placeholder={
                        !validFilterClientSearch
                          ? 'Digite pelo menos 2 caracteres'
                          : 'Todos os clientes'
                      }
                    />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Todos os clientes</SelectItem>
                    {clientFilter && !clientFilterIsLegacy && !selectedFilterClient && (
                      <SelectItem value={clientFilter}>Cliente selecionado</SelectItem>
                    )}
                    {filterClientsQuery.data?.map((client) => (
                      <SelectItem key={client.id} value={client.handle}>{client.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2 md:col-span-2">
                <Label htmlFor="task-filter-member-search">Equipe</Label>
                <Input
                  id="task-filter-member-search"
                  value={filterMemberSearch}
                  onChange={(event) => setFilterMemberSearch(event.target.value)}
                  placeholder="Pesquisar responsável, executor ou revisor"
                />
                <div className="grid gap-2 sm:grid-cols-3">
                  {([
                    ['owner', ownerFilter, 'Responsável'],
                    ['assignee', assigneeFilter, 'Executor'],
                    ['reviewer', reviewerFilter, 'Revisor'],
                  ] as const).map(([key, value, label]) => {
                    const legacy = isLegacyUuidFilter(value);
                    const selectedMember = filterMemberOptions.find(
                      (member) => member.handle === value || member.id === value,
                    );
                    return (
                      <Select
                        key={key}
                        value={value && !legacy ? value : 'all'}
                        onValueChange={(nextValue) => changeFilter(
                          key,
                          nextValue === 'all' ? '' : nextValue,
                        )}
                      >
                        <SelectTrigger aria-label={`Filtrar por ${label.toLowerCase()}`}>
                          <SelectValue placeholder={label} />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="all">Qualquer {label.toLowerCase()}</SelectItem>
                          {value && !legacy && !selectedMember && (
                            <SelectItem value={value}>{label} selecionado</SelectItem>
                          )}
                          {filterMemberOptions.map((member) => (
                            <SelectItem key={member.id} value={member.handle}>
                              {member.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    );
                  })}
                </div>
              </div>

              <div className="space-y-2">
                <Label>Prioridade</Label>
                <Select
                  value={priorityFilter || 'all'}
                  onValueChange={(value) => changeFilter(
                    'priority',
                    value === 'all' ? '' : value,
                  )}
                >
                  <SelectTrigger aria-label="Filtrar por prioridade">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Todas as prioridades</SelectItem>
                    <SelectItem value="alta">Alta</SelectItem>
                    <SelectItem value="media">Média</SelectItem>
                    <SelectItem value="baixa">Baixa</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {workspaceExperience.taskExperience === 'legal_360' && (
                <div className="space-y-2">
                  <Label>Etiqueta</Label>
                  <Select
                    value={labelFilter || 'all'}
                    onValueChange={(value) => changeFilter('label', value === 'all' ? '' : value)}
                  >
                    <SelectTrigger aria-label="Filtrar por etiqueta">
                      <SelectValue placeholder={labelCatalogQuery.isPending ? 'Carregando etiquetas…' : 'Todas as etiquetas'} />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Todas as etiquetas</SelectItem>
                      {labelFilter && !labelCatalogQuery.data?.some((label) => label.handle === labelFilter) && (
                        <SelectItem value={labelFilter}>Etiqueta selecionada</SelectItem>
                      )}
                      {labelCatalogQuery.data?.map((label) => (
                        <SelectItem key={label.id} value={label.handle}>
                          <span className="inline-flex items-center gap-2">
                            <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: label.color }} />
                            {label.name}
                          </span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}

              {workspaceExperience.taskExperience === 'legal_360' && (
                <div className="space-y-2">
                  <Label>Estado jurídico</Label>
                  <Select
                    value={statusFilter || 'all'}
                    onValueChange={(value) => changeFilter(
                      'status',
                      value === 'all' ? '' : value,
                    )}
                  >
                    <SelectTrigger aria-label="Filtrar por estado jurídico">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Todos os estados</SelectItem>
                      <SelectItem value="pending">Pendente</SelectItem>
                      <SelectItem value="in_progress">Em andamento</SelectItem>
                      <SelectItem value="blocked">Bloqueada</SelectItem>
                      <SelectItem value="done">Concluída</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              )}

              <div className="space-y-2">
                <Label htmlFor="task-filter-from">Prazo a partir de</Label>
                <Input
                  id="task-filter-from"
                  type="date"
                  value={periodFrom}
                  onChange={(event) => changeFilter('from', event.target.value)}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="task-filter-to">Prazo até</Label>
                <Input
                  id="task-filter-to"
                  type="date"
                  value={periodTo}
                  onChange={(event) => changeFilter('to', event.target.value)}
                />
              </div>
            </div>

            {(filterMattersQuery.isError
              || filterClientsQuery.isError
              || filterMembersQuery.isError) && (
              <p className="mt-3 text-xs text-destructive" role="status">
                Parte dos seletores está indisponível. Os filtros já aplicados continuam válidos.
              </p>
            )}
          </div>
        </KlmPanel>

        {periodInvalid ? (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertTitle>Período inválido</AlertTitle>
            <AlertDescription>
              A data inicial do prazo não pode ser posterior à data final.
            </AlertDescription>
          </Alert>
        ) : tasksQuery.isPending ? (
          <div className="grid gap-3 lg:grid-cols-2" aria-live="polite"><Skeleton className="h-36 w-full" /><Skeleton className="h-36 w-full" /><Skeleton className="h-36 w-full" /><Skeleton className="h-36 w-full" /></div>
        ) : tasksQuery.isError ? (
          <Alert variant={errorKind === 'forbidden' ? 'destructive' : 'default'}>
            <AlertCircle className="h-4 w-4" />
            <AlertTitle>{errorKind === 'forbidden' ? 'Sem permissão para tarefas' : 'Tarefas indisponíveis'}</AlertTitle>
            <AlertDescription className="mt-1 flex flex-wrap items-center gap-3"><span>{taskErrorMessage(tasksQuery.error, 'Não foi possível consultar a fila.')}</span><Button size="sm" variant="outline" onClick={() => void tasksQuery.refetch()}>Tentar novamente</Button></AlertDescription>
          </Alert>
        ) : items.length === 0 && !(displayMode === 'board' && workspaceExperience.taskExperience === 'legal_360') ? <EmptyState view={view} /> : (
          <>
            {displayMode === 'list' && <div className="grid gap-3 lg:grid-cols-2">{items.map((task) => <TaskCard key={task.id} task={task} onOpen={() => openTask(task)} />)}</div>}
            {displayMode === 'board' && workspaceExperience.taskExperience === 'legal_360' && (
              <div className="overflow-x-auto pb-2">
                <LegalTaskOrganization
                  tasks={items}
                  canEdit={can('tasks', 'edit')}
                  canArchive={can('tasks', 'complete')}
                  onOpen={openTask}
                />
              </div>
            )}
            {displayMode === 'board' && workspaceExperience.taskExperience === 'standard' && <div className="overflow-x-auto pb-2"><Board items={items} onOpen={openTask} /></div>}
            {displayMode === 'calendar' && <CalendarView items={items} onOpen={openTask} />}
            {tasksQuery.hasNextPage && <div className="flex justify-center pt-2"><Button variant="outline" disabled={tasksQuery.isFetchingNextPage} onClick={() => void tasksQuery.fetchNextPage()}>{tasksQuery.isFetchingNextPage && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}Carregar mais</Button></div>}
          </>
        )}
      </div>

      <TaskCollaborationDrawer taskId={selectedTask?.id ?? null} summary={selectedTask} experience={workspaceExperience.taskExperience} open={drawerOpen} onOpenChange={setDrawerOpen} />

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-h-[92vh] max-w-2xl overflow-y-auto">
          <DialogHeader><DialogTitle>Nova tarefa colaborativa</DialogTitle><DialogDescription>Defina o contexto pelo nome. Nenhum identificador técnico precisa ser digitado.</DialogDescription></DialogHeader>
          <div className="grid gap-4">
            {workspaceExperience.taskExperience === 'legal_360' && <div className="space-y-2"><Label>Contexto da tarefa</Label><Select value={createContextType} onValueChange={(value) => { setCreateContextType(value as 'workspace' | 'client' | 'contract' | 'matter'); setMatterId(''); setCreateClientId(''); setCreateContractId(''); }}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="matter">Caso 360</SelectItem><SelectItem value="contract">Contrato jurídico</SelectItem><SelectItem value="client">Cliente</SelectItem><SelectItem value="workspace">Escritório</SelectItem></SelectContent></Select><p className="text-xs text-muted-foreground">Publicação, processo, audiência e prazo continuam exigindo Caso 360. Contratos são validados no mesmo cliente e escritório.</p></div>}
            {createContextType === 'matter' && <><div className="space-y-2"><Label htmlFor="new-task-matter-search">Caso 360 e cliente</Label><Input id="new-task-matter-search" value={matterSearch} onChange={(event) => setMatterSearch(event.target.value)} placeholder="Buscar caso ou cliente" /><Select value={matterId} onValueChange={setMatterId}><SelectTrigger><SelectValue placeholder={!validMatterSearch ? 'Digite pelo menos 2 caracteres' : mattersQuery.isPending ? 'Carregando casos...' : 'Selecione o caso'} /></SelectTrigger><SelectContent>{mattersQuery.data?.map((matter) => <SelectItem key={matter.id} value={matter.id}>{matter.clientLabel} · {matter.label}</SelectItem>)}</SelectContent></Select>{mattersQuery.isError && <p className="text-xs text-destructive">Não foi possível carregar os casos disponíveis.</p>}</div>{selectedMatter && <p className="rounded-md bg-secondary px-3 py-2 text-xs">Cliente: <strong>{selectedMatter.clientLabel}</strong> · Caso: {selectedMatter.label}</p>}</>}
            {workspaceExperience.taskExperience === 'legal_360' && (createContextType === 'client' || createContextType === 'contract') && <div className="space-y-2"><Label htmlFor="new-task-client-search">Cliente</Label><Input id="new-task-client-search" value={createClientSearch} onChange={(event) => setCreateClientSearch(event.target.value)} placeholder="Buscar cliente" /><Select value={createClientId} onValueChange={(value) => { setCreateClientId(value); setCreateContractId(''); }}><SelectTrigger><SelectValue placeholder={!validCreateClientSearch ? 'Digite pelo menos 2 caracteres' : createClientsQuery.isPending ? 'Carregando clientes...' : 'Selecione o cliente'} /></SelectTrigger><SelectContent>{createClientsQuery.data?.map((client) => <SelectItem key={client.id} value={client.id}>{client.label}</SelectItem>)}</SelectContent></Select>{createClientsQuery.isError && <p className="text-xs text-destructive">Não foi possível carregar os clientes disponíveis.</p>}</div>}
            {workspaceExperience.taskExperience === 'legal_360' && createContextType === 'contract' && createClientId && <div className="space-y-3 rounded-md border p-3"><div className="space-y-2"><Label htmlFor="new-task-contract-search">Contrato jurídico</Label><Input id="new-task-contract-search" value={createContractSearch} onChange={(event) => setCreateContractSearch(event.target.value)} placeholder="Buscar contrato" /><Select value={createContractId} onValueChange={setCreateContractId}><SelectTrigger><SelectValue placeholder={contractsQuery.isPending ? 'Carregando contratos...' : 'Selecione o contrato'} /></SelectTrigger><SelectContent>{contractsQuery.data?.map((contract) => <SelectItem key={contract.id} value={contract.id}>{contract.title}</SelectItem>)}</SelectContent></Select>{contractsQuery.isError && <p className="text-xs text-destructive">Não foi possível carregar os contratos deste cliente.</p>}</div>{can('legal', 'manage') && <div className="flex flex-col gap-2 sm:flex-row"><Input value={newContractTitle} onChange={(event) => setNewContractTitle(event.target.value)} placeholder="Nome do novo contrato" aria-label="Nome do novo contrato" /><Button type="button" variant="outline" disabled={newContractTitle.trim().length < 2 || createContractMutation.isPending} onClick={() => createContractMutation.mutate()}>{createContractMutation.isPending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}Criar contrato</Button></div>}</div>}
            {workspaceExperience.taskExperience === 'legal_360' && createContextType === 'workspace' && <p className="rounded-md bg-secondary px-3 py-2 text-xs">A tarefa será administrativa e ficará vinculada ao escritório, sem inventar cliente ou Caso 360.</p>}
            <div className="space-y-2"><Label htmlFor="new-task-title">Título</Label><Input id="new-task-title" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={500} /></div>
            <div className="space-y-2"><Label htmlFor="new-task-description">Descrição</Label><Textarea id="new-task-description" value={description} onChange={(event) => setDescription(event.target.value)} maxLength={10_000} /></div>
            <div className="grid gap-4 sm:grid-cols-3">
              <div className="space-y-2"><Label>Prioridade</Label><Select value={priority} onValueChange={(value) => setPriority(value as TaskPriority | 'none')}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="none">Sem prioridade</SelectItem><SelectItem value="alta">Alta</SelectItem><SelectItem value="media">Média</SelectItem><SelectItem value="baixa">Baixa</SelectItem></SelectContent></Select></div>
              <div className="space-y-2"><Label htmlFor="new-task-due">Prazo geral</Label><Input id="new-task-due" type="datetime-local" value={dueAt} onChange={(event) => setDueAt(event.target.value)} /></div>
              {canAssign && reviewEnabled && <div className="space-y-2"><Label htmlFor="new-review-due">Prazo de revisão</Label><Input id="new-review-due" type="datetime-local" value={reviewDueAt} onChange={(event) => setReviewDueAt(event.target.value)} disabled={!reviewerId} /></div>}
            </div>
            {canAssign && (
              <>
                <div className="space-y-2"><Label htmlFor="new-task-member-search">Buscar na equipe</Label><Input id="new-task-member-search" value={memberSearch} onChange={(event) => setMemberSearch(event.target.value)} placeholder="Nome do membro" />{!validMemberSearch && <p className="text-xs text-muted-foreground">Digite pelo menos 2 caracteres para pesquisar.</p>}</div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2"><Label>Responsável</Label><Select value={ownerId} onValueChange={(nextOwner) => { setOwnerId(nextOwner); setAssigneeIds((current) => current.filter((id) => id !== nextOwner)); if (reviewerId === nextOwner) { setReviewerId(''); setReviewDueAt(''); } }}><SelectTrigger><SelectValue placeholder={membersQuery.isPending ? 'Carregando equipe...' : 'Selecione'} /></SelectTrigger><SelectContent>{memberOptions.map((member) => <SelectItem key={member.id} value={member.id}>{member.label}</SelectItem>)}</SelectContent></Select></div>
                  <div className="space-y-2"><Label>Revisor opcional</Label><Select disabled={!reviewEnabled || (participantLimitReached && !reviewerId)} value={reviewerId || 'none'} onValueChange={(value) => { setReviewerId(value === 'none' ? '' : value); if (value === 'none') setReviewDueAt(''); }}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="none">Sem revisor</SelectItem>{filteredReviewerOptions.map((member) => <SelectItem key={member.id} value={member.id}>{member.label}</SelectItem>)}</SelectContent></Select>{!reviewEnabled && <p className="text-xs text-muted-foreground">Revisão não incluída ou sem saldo na configuração atual.</p>}</div>
                </div>
                <div><Label>Executores</Label><div className="mt-2 grid gap-2 rounded-lg border p-3 sm:grid-cols-2">{memberOptions.filter((member) => member.id !== ownerId && member.id !== reviewerId).map((member) => { const selected = assigneeIds.includes(member.id); return <label key={member.id} className="flex items-center gap-2 text-sm"><Checkbox disabled={!selected && participantLimitReached} checked={selected} onCheckedChange={(checked) => setAssigneeIds((current) => checked === true ? [...current, member.id] : current.filter((id) => id !== member.id))} />{member.label}</label>; })}{membersQuery.isError && <p className="col-span-full text-xs text-destructive">O seletor seguro da equipe está indisponível.</p>}</div>{participantLimit != null && <p className="mt-2 text-xs text-muted-foreground">{createParticipantCount} de {participantLimit} participantes selecionados.</p>}{membersQuery.hasNextPage && <Button className="mt-2" size="sm" variant="outline" disabled={membersQuery.isFetchingNextPage} onClick={() => void membersQuery.fetchNextPage()}>{membersQuery.isFetchingNextPage && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}Carregar mais membros</Button>}</div>
              </>
            )}
            {createDateError && <p className="text-xs text-destructive" role="alert">O prazo de revisão não pode ser posterior ao prazo geral.</p>}
            {participantLimitExceeded && <p className="text-xs text-destructive" role="alert">Reduza a equipe para o limite de {participantLimit} participantes desta conta.</p>}
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setCreateOpen(false)}>Cancelar</Button><Button disabled={!canCreate || (workspaceExperience.taskExperience === 'standard' ? !matterId : (createContextType === 'matter' && !matterId) || (createContextType === 'client' && !createClientId) || (createContextType === 'contract' && (!createClientId || !createContractId))) || !title.trim() || (canAssign && !ownerId) || createDateError || participantLimitExceeded || createMutation.isPending} onClick={() => createMutation.mutate()}>{createMutation.isPending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}Criar tarefa</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
