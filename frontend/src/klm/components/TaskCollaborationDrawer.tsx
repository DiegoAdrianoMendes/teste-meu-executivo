import { useEffect, useMemo, useState } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertCircle,
  ArrowLeft,
  AtSign,
  CalendarClock,
  Check,
  CircleUserRound,
  Clock3,
  History,
  LoaderCircle,
  MessageSquareText,
  Paperclip,
  Pencil,
  RefreshCw,
  ShieldCheck,
  UserRoundCheck,
  UsersRound,
} from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/lib/utils';
import { useAppStore } from '@/store/app-context';
import { KlmPill } from './Primitives';
import { TaskReminderSection } from './TaskReminderSection';
import { TaskChecklistSection } from './TaskChecklistSection';
import { TaskLegalResourcesSection } from './TaskLegalResourcesSection';
import { TaskExternalShareSection } from './TaskExternalShareSection';
import { TaskWorkflowSection } from './TaskWorkflowSection';
import type { WorkspaceTaskExperience } from '@/klm/workspace-experience';
import {
  addTaskComment,
  classifyTaskError,
  executeTaskCommand,
  getCollaborativeTask,
  getTaskLegalResources,
  listTaskEvents,
  listTaskMatterDocuments,
  listTaskMemberOptionsPage,
  replaceTaskParticipants,
  taskErrorMessage,
  updateCollaborativeTask,
  updateTaskParticipantState,
  type CollaborativeTask,
  type TaskCommand,
  type TaskMemberOption,
  type TaskParticipantInput,
  type TaskParticipantState,
  type TaskPriority,
} from '@/services/tasks';

const statusLabel = {
  pending: 'Pendente',
  in_progress: 'Em andamento',
  blocked: 'Bloqueada',
  done: 'Concluída',
  cancelled: 'Cancelada',
} as const;

const reviewLabel = {
  not_required: 'Sem revisão',
  draft: 'Revisão configurada',
  pending: 'Aguardando revisão',
  approved: 'Revisão aprovada',
  changes_requested: 'Ajustes solicitados',
} as const;

const roleLabel = {
  owner: 'Responsável',
  assignee: 'Executor',
  reviewer: 'Revisor',
  observer: 'Observador',
} as const;

const participantStateLabel = {
  assigned: 'Atribuído',
  in_progress: 'Em execução',
  completed: 'Concluído',
  removed: 'Removido',
} as const;

const eventLabel = {
  created: 'Tarefa criada',
  updated: 'Tarefa atualizada',
  participants_changed: 'Equipe alterada',
  participant_state_changed: 'Progresso de executor atualizado',
  started: 'Tarefa iniciada',
  blocked: 'Tarefa bloqueada',
  unblocked: 'Tarefa desbloqueada',
  review_submitted: 'Enviada para revisão',
  review_approved: 'Revisão aprovada',
  review_changes_requested: 'Ajustes solicitados',
  completed: 'Tarefa concluída',
  reopened: 'Tarefa reaberta',
  cancelled: 'Tarefa cancelada',
  comment_added: 'Comentário adicionado',
  checklist_changed: 'Checklist atualizado',
  labels_changed: 'Etiquetas atualizadas',
  dependency_changed: 'Dependências atualizadas',
  attachment_changed: 'Arquivos vinculados',
} as const;

function formatDate(value: string | null | undefined): string {
  if (!value) return 'Não definido';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('pt-BR', {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(date);
}

function toLocalInput(value: string | null | undefined): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function toApiDate(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function commandNeedsReason(command: TaskCommand, rootReviewOverride = false): boolean {
  return ['block', 'request-changes', 'reopen', 'cancel'].includes(command)
    || (rootReviewOverride && ['approve', 'request-changes'].includes(command));
}

const commandPermission = {
  start: 'edit',
  block: 'edit',
  unblock: 'edit',
  'submit-for-review': 'edit',
  approve: 'review',
  'request-changes': 'review',
  complete: 'complete',
  reopen: 'complete',
  cancel: 'complete',
} as const satisfies Record<TaskCommand, 'edit' | 'review' | 'complete'>;

function availableCommands(task: CollaborativeTask, currentUserId: string | null, canRootOverrideReview: boolean): Array<{ command: TaskCommand; label: string; variant?: 'default' | 'outline' | 'destructive' }> {
  const reviewer = task.participants.find((item) => item.active && item.role === 'reviewer');
  const requiredExecutorsComplete = task.participants
    .filter((item) => item.active
      && (item.role === 'owner' || item.role === 'assignee')
      && item.requiredForCompletion !== false)
    .every((item) => item.state === 'completed');
  if (task.cancelledAt || task.status === 'done') {
    return [{ command: 'reopen', label: 'Reabrir', variant: 'outline' }];
  }
  if (task.status === 'blocked') {
    return [
      { command: 'unblock', label: 'Desbloquear', variant: 'outline' },
      { command: 'cancel', label: 'Cancelar', variant: 'destructive' },
    ];
  }
  const actions: Array<{ command: TaskCommand; label: string; variant?: 'default' | 'outline' | 'destructive' }> = [];
  if (task.status === 'pending') actions.push({ command: 'start', label: 'Iniciar' });
  if (task.status === 'in_progress' && requiredExecutorsComplete && task.reviewRequired && task.reviewState !== 'pending' && task.reviewState !== 'approved') {
    actions.push({ command: 'submit-for-review', label: 'Enviar para revisão' });
  }
  if (task.reviewState === 'pending' && (reviewer?.userId === currentUserId || canRootOverrideReview)) {
    actions.push({ command: 'approve', label: 'Aprovar' });
    actions.push({ command: 'request-changes', label: 'Solicitar ajustes', variant: 'outline' });
  }
  if (requiredExecutorsComplete && (!task.reviewRequired || task.reviewState === 'approved')) {
    actions.push({ command: 'complete', label: 'Concluir' });
  }
  actions.push({ command: 'block', label: 'Bloquear', variant: 'outline' });
  actions.push({ command: 'cancel', label: 'Cancelar', variant: 'destructive' });
  return actions;
}

function visibleStatus(task: CollaborativeTask): keyof typeof statusLabel {
  return task.cancelledAt ? 'cancelled' : task.status;
}

function workflowStage(task: CollaborativeTask): string {
  if (task.cancelledAt) return 'Encerrada por cancelamento';
  if (task.status === 'done') return 'Concluída';
  if (task.reviewState === 'pending') return 'Aguardando decisão do revisor';
  if (task.reviewState === 'changes_requested') return 'Ajustes solicitados';
  if (task.status === 'blocked') return 'Bloqueada';
  if (task.status === 'in_progress') return 'Em execução';
  return 'Aguardando início';
}

function isAfterBoundary(value: string, boundary: string | null | undefined): boolean {
  if (!value || !boundary) return false;
  const valueTime = new Date(value).getTime();
  const boundaryTime = new Date(boundary).getTime();
  return Number.isFinite(valueTime) && Number.isFinite(boundaryTime) && valueTime > boundaryTime;
}

interface EditState {
  title: string;
  description: string;
  priority: TaskPriority | 'none';
  dueAt: string;
  reviewDueAt: string;
  reviewRequired: boolean;
}

interface TeamState {
  ownerId: string;
  assigneeIds: string[];
  reviewerId: string;
  observerIds: string[];
  dueByKey: Record<string, string>;
  requiredByUserId: Record<string, boolean>;
  reason: string;
}

interface ParticipantStateDialog {
  userId: string;
  label: string;
  state: Exclude<TaskParticipantState, 'removed'>;
}

function memberName(
  options: TaskMemberOption[] | undefined,
  userId: string,
  currentUserId: string | null,
  currentUserName: string | null,
  labelSnapshot?: string | null,
): string {
  if (labelSnapshot?.trim()) return labelSnapshot.trim();
  const option = options?.find((item) => item.id === userId);
  if (option) return option.label;
  if (userId === currentUserId && currentUserName) return currentUserName;
  return 'Pessoa vinculada';
}

export interface TaskCollaborationDrawerProps {
  taskId: string | null;
  summary?: CollaborativeTask | null;
  contextLabels?: {
    clientLabel?: string | null;
    matterLabel?: string | null;
  };
  open: boolean;
  onOpenChange: (open: boolean) => void;
  experience?: WorkspaceTaskExperience;
}

export function TaskCollaborationDrawer({ taskId, summary, contextLabels, open, onOpenChange, experience = 'standard' }: TaskCollaborationDrawerProps) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const {
    can,
    state,
    supportMode,
    getCapability = () => null,
  } = useAppStore();
  const canView = can('tasks', 'view');
  const canEdit = can('tasks', 'edit');
  const canPrepareWhatsAppShare = can('mensagens', 'view') && can('mensagens', 'send_message');
  const canAssign = can('tasks', 'assign');
  const canComment = can('tasks', 'comment');
  const participantCapability = getCapability('task_participants');
  const reviewCapability = getCapability('task_reviews');
  const reviewRoundCapability = getCapability('review_rounds');
  const participantLimit = participantCapability?.limit ?? null;
  const canAddReviewer = Boolean(
    reviewCapability?.contracted
      && reviewCapability.enabled
      && reviewCapability.permitted,
  );
  // Some read-only surfaces (and their isolated tests) provide only the
  // authorization slice of the app context.  Identity enriches labels, but it
  // must never be required just to render an unopened/read-only drawer.
  const currentUserId = state?.currentUser?.id ?? null;
  const currentUserName = state?.currentUser?.name ?? null;
  const [conflictMessage, setConflictMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [commandDialog, setCommandDialog] = useState<TaskCommand | null>(null);
  const [commandReason, setCommandReason] = useState('');
  const [commandComment, setCommandComment] = useState('');
  const [comment, setComment] = useState('');
  const [commentMemberSearch, setCommentMemberSearch] = useState('');
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);
  const [selectedMentionIds, setSelectedMentionIds] = useState<string[]>([]);
  const [memberSearch, setMemberSearch] = useState('');
  const [debouncedMemberSearch, setDebouncedMemberSearch] = useState('');
  const [editOpen, setEditOpen] = useState(false);
  const [teamOpen, setTeamOpen] = useState(false);
  const [editState, setEditState] = useState<EditState>({
    title: '', description: '', priority: 'none', dueAt: '', reviewDueAt: '', reviewRequired: false,
  });
  const [teamState, setTeamState] = useState<TeamState>({
    ownerId: '', assigneeIds: [], reviewerId: '', observerIds: [], dueByKey: {}, requiredByUserId: {}, reason: '',
  });
  const [participantStateDialog, setParticipantStateDialog] = useState<ParticipantStateDialog | null>(null);
  const [participantStateReason, setParticipantStateReason] = useState('');
  const [activeTaskId, setActiveTaskId] = useState<string | null>(taskId);
  const [taskTrail, setTaskTrail] = useState<Array<{ id: string; title: string }>>([]);

  const taskQuery = useQuery({
    queryKey: ['collaborative-task', activeTaskId],
    queryFn: () => getCollaborativeTask(activeTaskId!),
    enabled: open && Boolean(activeTaskId),
    initialData: summary?.id === activeTaskId ? summary : undefined,
    staleTime: 10_000,
    retry: false,
  });
  const eventsQuery = useInfiniteQuery({
    queryKey: ['collaborative-task-events', activeTaskId],
    queryFn: ({ pageParam }) => listTaskEvents(activeTaskId!, pageParam, 50),
    enabled: open && Boolean(activeTaskId),
    initialPageParam: 0,
    getNextPageParam: (lastPage, pages) => {
      const loaded = pages.reduce((total, page) => total + page.items.length, 0);
      return loaded < lastPage.total ? loaded : undefined;
    },
    retry: false,
  });
  const membersQuery = useInfiniteQuery({
    queryKey: ['task-member-options', debouncedMemberSearch],
    queryFn: ({ pageParam }) => listTaskMemberOptionsPage(debouncedMemberSearch, pageParam, 100),
    enabled: open && canView && (debouncedMemberSearch.length === 0 || debouncedMemberSearch.length >= 2),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => lastPage.hasMore ? lastPage.offset + lastPage.items.length : undefined,
    staleTime: 60_000,
    retry: false,
  });
  const task = taskQuery.data;
  const legalResourcesQuery = useQuery({
    queryKey: ['task-legal-resources', activeTaskId],
    queryFn: () => getTaskLegalResources(activeTaskId!),
    enabled: open && experience === 'legal_360' && Boolean(activeTaskId),
    retry: false,
  });
  const matterDocumentsQuery = useQuery({
    queryKey: ['task-matter-documents', task?.matterId],
    queryFn: () => listTaskMatterDocuments(task!.matterId!),
    enabled: open && experience === 'legal_360' && Boolean(task?.matterId),
    retry: false,
  });

  useEffect(() => {
    if (open) {
      setActiveTaskId(taskId);
      setTaskTrail([]);
    }
  }, [open, taskId]);

  useEffect(() => {
    setConflictMessage(null);
    setSuccessMessage(null);
    setCommandDialog(null);
    setCommandReason('');
    setCommandComment('');
    setComment('');
    setCommentMemberSearch('');
    setSelectedDocumentIds([]);
    setSelectedMentionIds([]);
    setMemberSearch('');
    setParticipantStateDialog(null);
    setParticipantStateReason('');
  }, [activeTaskId]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedMemberSearch(memberSearch.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [memberSearch]);

  const refresh = async () => {
    if (!activeTaskId) return;
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['collaborative-task', activeTaskId] }),
      queryClient.invalidateQueries({ queryKey: ['collaborative-task-events', activeTaskId] }),
      queryClient.invalidateQueries({ queryKey: ['task-legal-resources', activeTaskId] }),
      queryClient.invalidateQueries({ queryKey: ['collaborative-tasks'] }),
    ]);
  };

  const handleMutationError = (error: unknown) => {
    const kind = classifyTaskError(error);
    const message = taskErrorMessage(error, 'Não foi possível alterar a tarefa.');
    if (kind === 'conflict') {
      setConflictMessage(`${message} A tarefa foi atualizada para a versão mais recente.`);
      void refresh();
      return;
    }
    if (kind === 'quota') {
      toast({ variant: 'destructive', title: 'Limite do plano atingido', description: message });
      return;
    }
    toast({ variant: 'destructive', title: 'Não foi possível concluir a ação', description: message });
  };

  const commandMutation = useMutation({
    mutationFn: ({ command, reason, comment: decisionComment }: { command: TaskCommand; reason?: string; comment?: string }) => {
      if (!task) throw new Error('A tarefa ainda não foi carregada.');
      return executeTaskCommand(task.id, command, task.version, { reason, comment: decisionComment });
    },
    onSuccess: async (_, variables) => {
      setCommandDialog(null);
      setCommandReason('');
      setCommandComment('');
      setConflictMessage(null);
      setSuccessMessage(`Ação “${variables.command}” confirmada.`);
      await refresh();
    },
    onError: handleMutationError,
  });

  const commentMutation = useMutation({
    mutationFn: () => {
      if (!task) throw new Error('A tarefa ainda não foi carregada.');
      return addTaskComment(task.id, task.version, comment.trim(), {
        documentIds: experience === 'legal_360' && task.matterId ? selectedDocumentIds : [],
        mentionedUserIds: experience === 'legal_360' ? selectedMentionIds : [],
      });
    },
    onSuccess: async () => {
      setComment('');
      setSelectedDocumentIds([]);
      setSelectedMentionIds([]);
      setSuccessMessage('Comentário registrado no histórico.');
      await refresh();
    },
    onError: handleMutationError,
  });

  const editMutation = useMutation({
    mutationFn: () => {
      if (!task) throw new Error('A tarefa ainda não foi carregada.');
      return updateCollaborativeTask(task.id, {
        expectedVersion: task.version,
        title: editState.title.trim(),
        description: editState.description.trim() || null,
        priority: editState.priority === 'none' ? null : editState.priority,
        dueAt: toApiDate(editState.dueAt),
        reviewDueAt: toApiDate(editState.reviewDueAt),
        reviewRequired: editState.reviewRequired,
      });
    },
    onSuccess: async () => {
      setEditOpen(false);
      setSuccessMessage('Tarefa atualizada com sucesso.');
      await refresh();
    },
    onError: handleMutationError,
  });

  const teamMutation = useMutation({
    mutationFn: () => {
      if (!task) throw new Error('A tarefa ainda não foi carregada.');
      const participants: TaskParticipantInput[] = [];
      participants.push({
        userId: teamState.ownerId,
        role: 'owner',
        dueAt: toApiDate(teamState.dueByKey[`owner:${teamState.ownerId}`] ?? ''),
        requiredForCompletion: teamState.requiredByUserId[teamState.ownerId] === true,
      });
      teamState.assigneeIds.forEach((userId) => participants.push({
        userId,
        role: 'assignee',
        dueAt: toApiDate(teamState.dueByKey[`assignee:${userId}`] ?? ''),
        requiredForCompletion: teamState.requiredByUserId[userId] !== false,
      }));
      if (teamState.reviewerId) participants.push({
        userId: teamState.reviewerId,
        role: 'reviewer',
        dueAt: toApiDate(teamState.dueByKey[`reviewer:${teamState.reviewerId}`] ?? ''),
      });
      teamState.observerIds.forEach((userId) => participants.push({
        userId,
        role: 'observer',
        dueAt: null,
      }));
      return replaceTaskParticipants(task.id, task.version, participants, teamState.reason.trim());
    },
    onSuccess: async () => {
      setTeamOpen(false);
      setSuccessMessage('Equipe e prazos individuais atualizados.');
      await refresh();
    },
    onError: handleMutationError,
  });

  const participantStateMutation = useMutation({
    mutationFn: () => {
      if (!task || !participantStateDialog) throw new Error('O executor ainda não foi carregado.');
      return updateTaskParticipantState(
        task.id,
        participantStateDialog.userId,
        task.version,
        participantStateDialog.state,
        participantStateReason.trim(),
      );
    },
    onSuccess: async () => {
      setParticipantStateDialog(null);
      setParticipantStateReason('');
      setSuccessMessage('Progresso individual atualizado.');
      await refresh();
    },
    onError: handleMutationError,
  });

  const openEdit = () => {
    if (!task) return;
    setEditState({
      title: task.title,
      description: task.description ?? '',
      priority: task.priority ?? 'none',
      dueAt: toLocalInput(task.dueAt),
      reviewDueAt: toLocalInput(task.reviewDueAt),
      reviewRequired: task.reviewRequired,
    });
    setEditOpen(true);
  };

  const openTeam = () => {
    if (!task) return;
    const active = task.participants.filter((item) => item.active);
    const dueByKey: Record<string, string> = {};
    const requiredByUserId: Record<string, boolean> = {};
    active.forEach((item) => { dueByKey[`${item.role}:${item.userId}`] = toLocalInput(item.dueAt); });
    active.filter((item) => item.role === 'owner' || item.role === 'assignee').forEach((item) => {
      requiredByUserId[item.userId] = item.requiredForCompletion !== false;
    });
    setTeamState({
      ownerId: active.find((item) => item.role === 'owner')?.userId ?? task.assignedTo ?? '',
      assigneeIds: active.filter((item) => item.role === 'assignee').map((item) => item.userId),
      reviewerId: active.find((item) => item.role === 'reviewer')?.userId ?? '',
      observerIds: active.filter((item) => item.role === 'observer').map((item) => item.userId),
      dueByKey,
      requiredByUserId,
      reason: '',
    });
    setMemberSearch('');
    setTeamOpen(true);
  };

  const activeParticipants = task?.participants.filter((item) => item.active) ?? [];
  const activeReviewer = activeParticipants.find((item) => item.role === 'reviewer');
  const requiredExecutors = activeParticipants.filter(
    (item) => (item.role === 'owner' || item.role === 'assignee')
      && item.requiredForCompletion !== false,
  );
  const completedRequiredExecutors = requiredExecutors.filter((item) => item.state === 'completed');
  const rootReviewOverride = Boolean(
    supportMode?.sessionType === 'root_direct'
      && task?.reviewState === 'pending'
      && activeReviewer?.userId !== currentUserId,
  );
  const reviewRoundLimit = reviewRoundCapability?.limit ?? null;
  const canCreateReviewRound = reviewRoundCapability == null
    || (
      reviewRoundCapability.enabled
      && reviewRoundCapability.permitted
      && (reviewRoundLimit == null || (task?.reviewRounds.length ?? 0) < reviewRoundLimit)
    );
  const actions = task
    ? availableCommands(task, currentUserId, rootReviewOverride).filter(({ command }) => (
      can('tasks', commandPermission[command])
      && (command !== 'submit-for-review' || canCreateReviewRound)
    ))
    : [];
  const runCommand = (command: TaskCommand) => {
    setSuccessMessage(null);
    if (commandNeedsReason(command, rootReviewOverride) || ['approve'].includes(command)) {
      setCommandDialog(command);
      return;
    }
    commandMutation.mutate({ command });
  };
  const events = useMemo(
    () => eventsQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [eventsQuery.data],
  );
  const actorOptions = useMemo(
    () => membersQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [membersQuery.data],
  );
  const commentMemberOptions = useMemo(() => {
    const search = commentMemberSearch.trim().toLocaleLowerCase('pt-BR');
    if (!search) return actorOptions;
    return actorOptions.filter((member) => (
      member.label.toLocaleLowerCase('pt-BR').includes(search)
      || member.handle.toLocaleLowerCase('pt-BR').includes(search)
      || member.role.toLocaleLowerCase('pt-BR').includes(search)
    ));
  }, [actorOptions, commentMemberSearch]);
  const attachmentsByEvent = useMemo(() => {
    const grouped = new Map<string, NonNullable<typeof legalResourcesQuery.data>['attachments']>();
    for (const attachment of legalResourcesQuery.data?.attachments ?? []) {
      grouped.set(attachment.taskEventId, [...(grouped.get(attachment.taskEventId) ?? []), attachment]);
    }
    return grouped;
  }, [legalResourcesQuery.data]);
  const mentionsByEvent = useMemo(() => {
    const grouped = new Map<string, NonNullable<typeof legalResourcesQuery.data>['mentions']>();
    for (const mention of legalResourcesQuery.data?.mentions ?? []) {
      grouped.set(mention.taskEventId, [...(grouped.get(mention.taskEventId) ?? []), mention]);
    }
    return grouped;
  }, [legalResourcesQuery.data]);
  const busy = commandMutation.isPending || editMutation.isPending || teamMutation.isPending || participantStateMutation.isPending;

  const editDateError = isAfterBoundary(editState.reviewDueAt, editState.dueAt);
  const participantDeadlineConflict = Boolean(
    editState.dueAt
      && activeParticipants.some((participant) => participant.dueAt && isAfterBoundary(participant.dueAt, editState.dueAt)),
  );
  const teamDeadlineConflicts = task
    ? Object.values(teamState.dueByKey).filter((value) => isAfterBoundary(value, task.dueAt ?? task.dueDate))
    : [];

  const assigneeCandidates = useMemo(
    () => (actorOptions ?? []).filter((member) => member.id !== teamState.ownerId && member.id !== teamState.reviewerId),
    [actorOptions, teamState.ownerId, teamState.reviewerId],
  );
  const reviewerCandidates = useMemo(
    () => canAddReviewer
      ? (actorOptions ?? []).filter((member) => member.id !== teamState.ownerId && !teamState.assigneeIds.includes(member.id))
      : [],
    [actorOptions, canAddReviewer, teamState.assigneeIds, teamState.ownerId],
  );
  const teamParticipantCount = (teamState.ownerId ? 1 : 0)
    + teamState.assigneeIds.length
    + teamState.observerIds.length
    + (teamState.reviewerId ? 1 : 0);
  const teamParticipantLimitReached = participantLimit != null
    && teamParticipantCount >= participantLimit;
  const teamParticipantLimitExceeded = participantLimit != null
    && teamParticipantCount > participantLimit;

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent className={cn('flex h-dvh w-[98vw] max-w-none flex-col gap-0 overflow-hidden p-0', experience === 'legal_360' ? 'sm:max-w-[960px]' : 'sm:max-w-[780px]')} data-task-experience={experience}>
          <SheetHeader className="border-b bg-card px-5 py-4 pr-12 text-left">
            {experience === 'legal_360' && taskTrail.length > 0 && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="w-fit px-0 text-muted-foreground hover:bg-transparent hover:text-foreground"
                onClick={() => {
                  const parent = taskTrail[taskTrail.length - 1];
                  setTaskTrail((trail) => trail.slice(0, -1));
                  setActiveTaskId(parent.id);
                }}
              >
                <ArrowLeft className="mr-1.5 h-4 w-4" />Voltar para {taskTrail[taskTrail.length - 1].title}
              </Button>
            )}
            <div className="flex flex-wrap items-center gap-2">
              {task && <KlmPill tone={task.cancelledAt ? 'muted' : task.status === 'done' ? 'green' : task.status === 'blocked' ? 'red' : 'blue'}>{statusLabel[visibleStatus(task)]}</KlmPill>}
              {task?.reviewState && <KlmPill tone={task.reviewState === 'approved' ? 'green' : task.reviewState === 'pending' ? 'gold' : 'muted'}>{reviewLabel[task.reviewState]}</KlmPill>}
              {task?.priority && <KlmPill tone={task.priority === 'alta' ? 'red' : 'muted'}>Prioridade {task.priority}</KlmPill>}
            </div>
            <SheetTitle className="line-clamp-2 text-xl">{task?.title ?? summary?.title ?? 'Carregando tarefa'}</SheetTitle>
            <SheetDescription>
              {[
                task?.clientLabel ?? summary?.clientLabel ?? contextLabels?.clientLabel,
                task?.matterLabel ?? summary?.matterLabel ?? contextLabels?.matterLabel,
              ].filter(Boolean).join(' · ') ||
                (task?.contextType === 'workspace' ? 'Tarefa do escritório' : 'Contexto não identificado')}
            </SheetDescription>
          </SheetHeader>

          {taskQuery.isPending && !task ? (
            <div className="space-y-4 p-6" aria-live="polite">
              <Skeleton className="h-8 w-2/3" />
              <Skeleton className="h-28 w-full" />
              <Skeleton className="h-48 w-full" />
            </div>
          ) : taskQuery.isError && !task ? (
            <div className="p-6">
              <Alert variant={classifyTaskError(taskQuery.error) === 'forbidden' ? 'destructive' : 'default'}>
                <AlertCircle className="h-4 w-4" />
                <AlertTitle>{classifyTaskError(taskQuery.error) === 'forbidden' ? 'Sem permissão' : 'Tarefa indisponível'}</AlertTitle>
                <AlertDescription>{taskErrorMessage(taskQuery.error, 'Não foi possível carregar esta tarefa.')}</AlertDescription>
              </Alert>
            </div>
          ) : task ? (
            <>
              <div className="space-y-2 border-b px-5 py-3">
                {conflictMessage && (
                  <Alert variant="destructive">
                    <RefreshCw className="h-4 w-4" />
                    <AlertTitle>Conflito de versão</AlertTitle>
                    <AlertDescription>{conflictMessage}</AlertDescription>
                  </Alert>
                )}
                {successMessage && (
                  <div className="flex items-center gap-2 rounded-md border border-emerald-600/20 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-800 dark:text-emerald-300" role="status">
                    <Check className="h-4 w-4" /> {successMessage}
                  </div>
                )}
                <div className="flex flex-wrap gap-2">
                  {canEdit && <Button size="sm" variant="outline" onClick={openEdit}><Pencil className="mr-1.5 h-3.5 w-3.5" />Editar</Button>}
                  {canAssign && <Button size="sm" variant="outline" onClick={openTeam} disabled={membersQuery.isError}><UsersRound className="mr-1.5 h-3.5 w-3.5" />Equipe e prazos</Button>}
                  {actions.map((action) => (
                    <Button key={action.command} size="sm" variant={action.variant ?? 'default'} disabled={busy} onClick={() => runCommand(action.command)}>
                      {busy && commandMutation.variables?.command === action.command && <LoaderCircle className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                      {action.label}
                    </Button>
                  ))}
                </div>
                {task.reviewRequired && !canCreateReviewRound && task.reviewState !== 'pending' && (
                  <p className="text-xs text-amber-700 dark:text-amber-300">O histórico permanece disponível, mas uma nova rodada de revisão excede a capacidade contratada.</p>
                )}
              </div>

              <Tabs defaultValue="resumo" className="flex min-h-0 flex-1 flex-col">
                <div className="overflow-x-auto border-b px-5">
                  <TabsList className="h-11 min-w-max bg-transparent p-0">
                    <TabsTrigger value="resumo">Resumo</TabsTrigger>
                    {experience === 'legal_360' && <TabsTrigger value="execucao">Execução</TabsTrigger>}
                    {experience === 'legal_360' && <TabsTrigger value="checklists">Checklists</TabsTrigger>}
                    {experience === 'legal_360' && <TabsTrigger value="recursos">Recursos</TabsTrigger>}
                    {experience === 'legal_360' && <TabsTrigger value="compartilhar">Compartilhar</TabsTrigger>}
                    <TabsTrigger value="equipe">Equipe</TabsTrigger>
                    <TabsTrigger value="revisao">Revisão</TabsTrigger>
                    <TabsTrigger value="comentarios">{experience === 'legal_360' ? 'Comentários e arquivos' : 'Comentários'}</TabsTrigger>
                    <TabsTrigger value="historico">Histórico</TabsTrigger>
                    <TabsTrigger value="lembretes">Lembretes</TabsTrigger>
                  </TabsList>
                </div>
                <ScrollArea className="min-h-0 flex-1">
                  <TabsContent value="resumo" className="m-0 space-y-5 p-5">
                    <section className="rounded-lg border bg-card p-4">
                      <div className="flex items-center justify-between gap-3">
                        <h3 className="text-sm font-semibold">Estado operacional</h3>
                        <KlmPill tone={task.cancelledAt ? 'muted' : task.status === 'done' ? 'green' : task.status === 'blocked' ? 'red' : 'gold'}>{workflowStage(task)}</KlmPill>
                      </div>
                      <p className="mt-3 text-xs text-muted-foreground">O estado é derivado das transições persistidas da tarefa e da revisão, sem estimar percentuais.</p>
                      {requiredExecutors.length > 0 && (
                        <p className="mt-2 text-xs font-medium">
                          {completedRequiredExecutors.length} de {requiredExecutors.length} owners/executores obrigatórios concluíram sua parte.
                        </p>
                      )}
                    </section>
                    {requiredExecutors.length > completedRequiredExecutors.length && !task.cancelledAt && task.status !== 'done' && (
                      <Alert>
                        <UsersRound className="h-4 w-4" />
                        <AlertTitle>Conclusão aguardando executores</AlertTitle>
                        <AlertDescription>Concluir a tarefa ou enviá-la para revisão será liberado quando todos os executores obrigatórios terminarem sua parte.</AlertDescription>
                      </Alert>
                    )}
                    <div className="grid gap-3 sm:grid-cols-2">
                      <div className="rounded-lg border p-4">
                        <div className="flex items-center gap-2 text-xs font-semibold"><CalendarClock className="h-4 w-4 text-primary" />Prazo geral</div>
                        <p className="mt-2 text-sm">{formatDate(task.dueAt ?? task.dueDate ?? task.legacyDueText)}</p>
                      </div>
                      <div className="rounded-lg border p-4">
                        <div className="flex items-center gap-2 text-xs font-semibold"><ShieldCheck className="h-4 w-4 text-primary" />Prazo de revisão</div>
                        <p className="mt-2 text-sm">{formatDate(task.reviewDueAt)}</p>
                      </div>
                    </div>
                    <section>
                      <h3 className="text-sm font-semibold">Descrição</h3>
                      <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">{task.description || 'Nenhuma descrição informada.'}</p>
                    </section>
                    {task.cancelledAt && (
                      <Alert variant="destructive">
                        <AlertCircle className="h-4 w-4" />
                        <AlertTitle>Tarefa cancelada</AlertTitle>
                        <AlertDescription>{task.cancellationReason || 'Sem motivo visível.'}</AlertDescription>
                      </Alert>
                    )}
                  </TabsContent>

                  {experience === 'legal_360' && (
                    <TabsContent value="execucao" className="m-0 space-y-4 p-5">
                      <section className="rounded-lg border bg-card p-4">
                        <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="text-sm font-semibold">Execução da tarefa</h3><KlmPill tone={task.cancelledAt ? 'muted' : task.status === 'done' ? 'green' : task.status === 'blocked' ? 'red' : 'gold'}>{workflowStage(task)}</KlmPill></div>
                        <p className="mt-2 text-xs leading-5 text-muted-foreground">Status, revisão e conclusão são comandos distintos. Mover uma tarefa em um quadro não altera seu estado jurídico automaticamente.</p>
                        <div className="mt-4 grid gap-3 sm:grid-cols-3"><div className="rounded-md bg-secondary p-3"><p className="text-2xs uppercase text-muted-foreground">Executores obrigatórios</p><strong className="mt-1 block text-lg">{requiredExecutors.length}</strong></div><div className="rounded-md bg-secondary p-3"><p className="text-2xs uppercase text-muted-foreground">Partes concluídas</p><strong className="mt-1 block text-lg">{completedRequiredExecutors.length}</strong></div><div className="rounded-md bg-secondary p-3"><p className="text-2xs uppercase text-muted-foreground">Rodadas de revisão</p><strong className="mt-1 block text-lg">{task.reviewRounds.length}</strong></div></div>
                      </section>
                      <TaskWorkflowSection task={task} canManage={canEdit && can('automations', 'manage')} />
                      <div className="flex flex-wrap gap-2">{actions.map((action) => <Button key={`execution-${action.command}`} size="sm" variant={action.variant ?? 'default'} disabled={busy} onClick={() => runCommand(action.command)}>{action.label}</Button>)}</div>
                    </TabsContent>
                  )}

                  {experience === 'legal_360' && (
                    <TabsContent value="checklists" className="m-0 p-5">
                      <TaskChecklistSection task={task} canEdit={canEdit} onConflict={(message) => setConflictMessage(message)} />
                    </TabsContent>
                  )}

                  {experience === 'legal_360' && (
                    <TabsContent value="recursos" className="m-0 p-5">
                      <TaskLegalResourcesSection
                        task={task}
                        canEdit={canEdit}
                        onConflict={(message) => setConflictMessage(message)}
                        onOpenTask={(subtask) => {
                          setTaskTrail((trail) => [...trail, { id: task.id, title: task.title }]);
                          setActiveTaskId(subtask.id);
                        }}
                      />
                    </TabsContent>
                  )}

                  {experience === 'legal_360' && (
                    <TabsContent value="compartilhar" className="m-0 p-5">
                      <TaskExternalShareSection
                        taskId={task.id}
                        canEdit={canEdit}
                        canPrepareWhatsApp={canPrepareWhatsAppShare}
                      />
                    </TabsContent>
                  )}

                  <TabsContent value="equipe" className="m-0 space-y-3 p-5">
                    {activeParticipants.length === 0 ? (
                      <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">Nenhum participante ativo.</p>
                    ) : activeParticipants.map((participant) => {
                      const label = memberName(actorOptions, participant.userId, currentUserId, currentUserName, participant.labelSnapshot);
                      const state = participant.state ?? 'assigned';
                      const isExecutorRole = participant.role === 'assignee'
                        || (participant.role === 'owner' && participant.requiredForCompletion !== false);
                      const canUpdateState = isExecutorRole
                        && canEdit
                        && (canAssign || participant.userId === currentUserId)
                        && task.reviewState !== 'pending'
                        && !task.cancelledAt
                        && task.status !== 'done';
                      const nextState: Exclude<TaskParticipantState, 'removed'> = state === 'completed' ? 'in_progress' : state === 'in_progress' ? 'completed' : 'in_progress';
                      const actionLabel = state === 'completed' ? 'Reabrir parte' : state === 'in_progress' ? 'Concluir parte' : 'Iniciar parte';
                      return (
                        <article key={participant.id} className="rounded-lg border bg-card p-4">
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div className="flex min-w-0 items-start gap-3">
                              <CircleUserRound className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
                              <div className="min-w-0">
                                <p className="truncate text-sm font-semibold">{label}</p>
                                <p className="text-xs text-muted-foreground">{roleLabel[participant.role]}</p>
                                {isExecutorRole && (
                                  <div className="mt-2 flex flex-wrap gap-1.5">
                                    <KlmPill tone={state === 'completed' ? 'green' : state === 'in_progress' ? 'gold' : 'muted'}>{participantStateLabel[state]}</KlmPill>
                                    {participant.requiredForCompletion !== false && <KlmPill tone="blue">Executor obrigatório</KlmPill>}
                                  </div>
                                )}
                              </div>
                            </div>
                            <div className="text-right text-xs text-muted-foreground">
                              <p>Prazo individual</p>
                              <p className="mt-1 font-medium text-foreground">{formatDate(participant.dueAt)}</p>
                            </div>
                          </div>
                          {canUpdateState && (
                            <div className="mt-3 flex justify-end">
                              <Button size="sm" variant="outline" onClick={() => { setParticipantStateDialog({ userId: participant.userId, label, state: nextState }); setParticipantStateReason(''); }}>
                                {actionLabel}
                              </Button>
                            </div>
                          )}
                        </article>
                      );
                    })}
                    {membersQuery.isError && canAssign && (
                      <Alert>
                        <AlertCircle className="h-4 w-4" />
                        <AlertTitle>Equipe indisponível</AlertTitle>
                        <AlertDescription>Os participantes continuam visíveis, mas o seletor seguro de membros não pôde ser carregado.</AlertDescription>
                      </Alert>
                    )}
                  </TabsContent>

                  <TabsContent value="revisao" className="m-0 space-y-3 p-5">
                    {!task.reviewRequired ? (
                      <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">Esta tarefa não exige revisão.</p>
                    ) : task.reviewRounds.length === 0 ? (
                      <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">A revisão está configurada, mas ainda não foi submetida.</p>
                    ) : [...task.reviewRounds].reverse().map((round) => (
                      <article key={round.id} className="rounded-lg border bg-card p-4">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <p className="text-sm font-semibold">Rodada {round.roundNumber}</p>
                          <KlmPill tone={round.state === 'approved' ? 'green' : round.state === 'pending' ? 'gold' : 'red'}>{reviewLabel[round.state]}</KlmPill>
                        </div>
                        <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                          <div><dt className="text-muted-foreground">Revisor</dt><dd className="mt-1 font-medium">{memberName(actorOptions, round.reviewerUserId, currentUserId, currentUserName)}</dd></div>
                          <div><dt className="text-muted-foreground">Prazo</dt><dd className="mt-1 font-medium">{formatDate(round.dueAt)}</dd></div>
                          <div><dt className="text-muted-foreground">Enviada em</dt><dd className="mt-1 font-medium">{formatDate(round.submittedAt)}</dd></div>
                          <div><dt className="text-muted-foreground">Decidida em</dt><dd className="mt-1 font-medium">{formatDate(round.resolvedAt)}</dd></div>
                        </dl>
                        {round.decisionComment && <p className="mt-3 rounded-md bg-secondary px-3 py-2 text-xs">{round.decisionComment}</p>}
                      </article>
                    ))}
                  </TabsContent>

                  <TabsContent value="comentarios" className="m-0 space-y-4 p-5">
                    {canComment && (
                      <div className="rounded-lg border bg-card p-4">
                        <Label htmlFor="task-comment">Novo comentário</Label>
                        <Textarea id="task-comment" value={comment} onChange={(event) => setComment(event.target.value)} className="mt-2 min-h-24" maxLength={10_000} placeholder="Registre um update factual da tarefa..." />
                        {experience === 'legal_360' && (
                          <div className="mt-4 grid gap-4 lg:grid-cols-2">
                            <section className="space-y-2 rounded-md border p-3">
                              <div className="flex items-center gap-2"><AtSign className="h-4 w-4 text-primary" /><Label htmlFor="task-comment-member-search">Mencionar pessoas</Label></div>
                              <Input id="task-comment-member-search" value={commentMemberSearch} onChange={(event) => { setCommentMemberSearch(event.target.value); setMemberSearch(event.target.value); }} placeholder="Buscar membro ativo" />
                              {membersQuery.isError ? (
                                <p className="text-xs text-destructive">Os membros ativos não puderam ser carregados.</p>
                              ) : (
                                <div className="max-h-36 space-y-1 overflow-y-auto" aria-label="Membros ativos para menção">
                                  {commentMemberOptions.length === 0 && <p className="text-xs text-muted-foreground">Nenhum membro ativo encontrado.</p>}
                                  {commentMemberOptions.map((member) => (
                                    <label key={member.id} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-secondary">
                                      <Checkbox
                                        checked={selectedMentionIds.includes(member.id)}
                                        onCheckedChange={(checked) => setSelectedMentionIds((current) => checked ? [...new Set([...current, member.id])] : current.filter((id) => id !== member.id))}
                                      />
                                      <span className="min-w-0"><span className="block truncate font-medium">{member.label}</span><span className="block truncate text-xs text-muted-foreground">{member.role}</span></span>
                                    </label>
                                  ))}
                                </div>
                              )}
                              {selectedMentionIds.length > 0 && <p className="text-xs text-muted-foreground">{selectedMentionIds.length} pessoa(s) será(ão) mencionada(s).</p>}
                            </section>

                            <section className="space-y-2 rounded-md border p-3">
                              <div className="flex items-center gap-2"><Paperclip className="h-4 w-4 text-primary" /><span className="text-sm font-medium">Documentos do Caso 360</span></div>
                              {!task.matterId ? (
                                <Alert>
                                  <AlertCircle className="h-4 w-4" />
                                  <AlertTitle>Anexos indisponíveis</AlertTitle>
                                  <AlertDescription>Esta tarefa não está vinculada a um Caso 360. Nenhum documento será inferido.</AlertDescription>
                                </Alert>
                              ) : matterDocumentsQuery.isError ? (
                                <p className="text-xs text-destructive">Os documentos deste Caso 360 não puderam ser carregados.</p>
                              ) : matterDocumentsQuery.isPending ? (
                                <Skeleton className="h-16 w-full" />
                              ) : (
                                <div className="max-h-44 space-y-1 overflow-y-auto" aria-label="Documentos do mesmo Caso 360">
                                  {(matterDocumentsQuery.data ?? []).length === 0 && <p className="text-xs text-muted-foreground">Nenhum documento disponível neste caso.</p>}
                                  {(matterDocumentsQuery.data ?? []).map((document) => (
                                    <label key={document.id} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-secondary">
                                      <Checkbox
                                        checked={selectedDocumentIds.includes(document.id)}
                                        onCheckedChange={(checked) => setSelectedDocumentIds((current) => checked ? [...new Set([...current, document.id])] : current.filter((id) => id !== document.id))}
                                      />
                                      <span className="min-w-0"><span className="block truncate font-medium">{document.name}</span><span className="block truncate text-xs text-muted-foreground">{document.mimeType ?? document.status}</span></span>
                                    </label>
                                  ))}
                                </div>
                              )}
                              {selectedDocumentIds.length > 0 && <p className="text-xs text-muted-foreground">{selectedDocumentIds.length} documento(s) será(ão) vinculado(s).</p>}
                            </section>
                          </div>
                        )}
                        <div className="mt-3 flex justify-end">
                          <Button disabled={!comment.trim() || commentMutation.isPending} onClick={() => commentMutation.mutate()}>
                            {commentMutation.isPending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}
                            Registrar update
                          </Button>
                        </div>
                      </div>
                    )}
                    {experience === 'legal_360' && legalResourcesQuery.isError && (
                      <Alert>
                        <AlertCircle className="h-4 w-4" />
                        <AlertTitle>Vínculos do histórico indisponíveis</AlertTitle>
                        <AlertDescription>Os comentários continuam acessíveis, mas arquivos e menções não puderam ser carregados.</AlertDescription>
                      </Alert>
                    )}
                    {eventsQuery.isPending ? <Skeleton className="h-32 w-full" /> : (
                      events.filter((event) => event.eventType === 'comment_added').length
                        ? events.filter((event) => event.eventType === 'comment_added').map((event) => (
                          <article key={event.id} className="rounded-lg border p-4">
                            <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground"><span>{memberName(actorOptions, event.actorUserId ?? '', currentUserId, currentUserName)}</span><time>{formatDate(event.occurredAt)}</time></div>
                            <p className="mt-2 whitespace-pre-wrap text-sm">{event.comment}</p>
                            {experience === 'legal_360' && ((attachmentsByEvent.get(event.id)?.length ?? 0) > 0 || (mentionsByEvent.get(event.id)?.length ?? 0) > 0) && (
                              <div className="mt-3 flex flex-wrap gap-2">
                                {(attachmentsByEvent.get(event.id) ?? []).map((attachment) => <span key={attachment.id} className="inline-flex max-w-full items-center gap-1 rounded-full border px-2.5 py-1 text-xs"><Paperclip className="h-3 w-3" /><span className="truncate">{attachment.nameSnapshot}</span></span>)}
                                {(mentionsByEvent.get(event.id) ?? []).map((mention) => <span key={mention.id} className="inline-flex items-center gap-1 rounded-full bg-secondary px-2.5 py-1 text-xs"><AtSign className="h-3 w-3" />{mention.labelSnapshot}</span>)}
                              </div>
                            )}
                          </article>
                        ))
                        : <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">Nenhum comentário registrado.</p>
                    )}
                    {eventsQuery.hasNextPage && <div className="flex justify-center"><Button variant="outline" size="sm" disabled={eventsQuery.isFetchingNextPage} onClick={() => void eventsQuery.fetchNextPage()}>{eventsQuery.isFetchingNextPage && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}Carregar comentários anteriores</Button></div>}
                  </TabsContent>

                  <TabsContent value="historico" className="m-0 space-y-3 p-5">
                    {eventsQuery.isPending ? (
                      <div className="space-y-3"><Skeleton className="h-20 w-full" /><Skeleton className="h-20 w-full" /></div>
                    ) : eventsQuery.isError ? (
                      <Alert><AlertCircle className="h-4 w-4" /><AlertTitle>Histórico indisponível</AlertTitle><AlertDescription>{taskErrorMessage(eventsQuery.error, 'Não foi possível carregar o histórico.')}</AlertDescription></Alert>
                    ) : events.length ? [...events].reverse().map((event) => (
                      <article key={event.id} className="relative border-l-2 border-primary/25 pl-4">
                        <span className="absolute -left-[5px] top-1 h-2 w-2 rounded-full bg-primary" />
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <p className="text-sm font-semibold">{eventLabel[event.eventType]}</p>
                          <time className="text-xs text-muted-foreground">{formatDate(event.occurredAt)}</time>
                        </div>
                        <p className="mt-1 text-xs text-muted-foreground">Por {memberName(actorOptions, event.actorUserId ?? '', currentUserId, currentUserName)} · versão {event.resourceVersionAfter ?? '—'}</p>
                        {event.comment && <p className="mt-2 whitespace-pre-wrap rounded-md bg-secondary px-3 py-2 text-xs">{event.comment}</p>}
                        {experience === 'legal_360' && ((attachmentsByEvent.get(event.id)?.length ?? 0) > 0 || (mentionsByEvent.get(event.id)?.length ?? 0) > 0) && (
                          <div className="mt-2 flex flex-wrap gap-2">
                            {(attachmentsByEvent.get(event.id) ?? []).map((attachment) => <span key={attachment.id} className="inline-flex max-w-full items-center gap-1 rounded-full border px-2 py-1 text-xs"><Paperclip className="h-3 w-3" /><span className="truncate">{attachment.nameSnapshot}</span></span>)}
                            {(mentionsByEvent.get(event.id) ?? []).map((mention) => <span key={mention.id} className="inline-flex items-center gap-1 rounded-full bg-secondary px-2 py-1 text-xs"><AtSign className="h-3 w-3" />{mention.labelSnapshot}</span>)}
                          </div>
                        )}
                        {event.changedPaths.length > 0 && <p className="mt-2 text-2xs text-muted-foreground">Campos alterados: {event.changedPaths.join(', ')}</p>}
                      </article>
                    )) : <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">Nenhum evento registrado.</p>}
                    {eventsQuery.hasNextPage && <div className="flex justify-center"><Button variant="outline" size="sm" disabled={eventsQuery.isFetchingNextPage} onClick={() => void eventsQuery.fetchNextPage()}>{eventsQuery.isFetchingNextPage && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}Carregar eventos anteriores</Button></div>}
                  </TabsContent>

                  <TabsContent value="lembretes" className="m-0 p-5">
                    <TaskReminderSection
                      task={task}
                      memberOptions={actorOptions}
                      canEdit={canEdit}
                      onConflict={(message) => setConflictMessage(message || null)}
                      onSuccess={setSuccessMessage}
                    />
                  </TabsContent>
                </ScrollArea>
              </Tabs>
            </>
          ) : null}
        </SheetContent>
      </Sheet>

      <Dialog open={Boolean(commandDialog)} onOpenChange={(next) => { if (!next) setCommandDialog(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirmar ação na tarefa</DialogTitle>
            <DialogDescription>A ação usa a versão atual da tarefa e será registrada no histórico.</DialogDescription>
          </DialogHeader>
          {commandDialog && commandNeedsReason(commandDialog, rootReviewOverride) && (
            <div className="space-y-2"><Label htmlFor="task-command-reason">Motivo</Label><Textarea id="task-command-reason" value={commandReason} onChange={(event) => setCommandReason(event.target.value)} minLength={8} maxLength={500} placeholder="Informe o motivo da decisão..." /></div>
          )}
          {commandDialog && ['approve', 'request-changes'].includes(commandDialog) && (
            <div className="space-y-2"><Label htmlFor="task-command-comment">Comentário da revisão</Label><Textarea id="task-command-comment" value={commandComment} onChange={(event) => setCommandComment(event.target.value)} maxLength={10_000} placeholder="Registre a decisão da revisão..." /></div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setCommandDialog(null)}>Voltar</Button>
            <Button
              disabled={commandMutation.isPending || (Boolean(commandDialog && commandNeedsReason(commandDialog, rootReviewOverride)) && commandReason.trim().length < 8)}
              onClick={() => commandDialog && commandMutation.mutate({ command: commandDialog, reason: commandReason.trim() || undefined, comment: commandComment.trim() || undefined })}
            >
              {commandMutation.isPending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}
              Confirmar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(participantStateDialog)} onOpenChange={(next) => { if (!next) { setParticipantStateDialog(null); setParticipantStateReason(''); } }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Atualizar progresso do executor</DialogTitle>
            <DialogDescription>
              {participantStateDialog
                ? `${participantStateDialog.label}: ${participantStateLabel[participantStateDialog.state]}. A alteração será versionada no histórico.`
                : 'A alteração será versionada no histórico.'}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="participant-state-reason">Motivo</Label>
            <Textarea id="participant-state-reason" value={participantStateReason} onChange={(event) => setParticipantStateReason(event.target.value)} minLength={8} maxLength={500} placeholder="Registre o update factual do executor..." />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setParticipantStateDialog(null); setParticipantStateReason(''); }}>Voltar</Button>
            <Button disabled={participantStateReason.trim().length < 8 || participantStateMutation.isPending} onClick={() => participantStateMutation.mutate()}>
              {participantStateMutation.isPending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}
              Confirmar progresso
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader><DialogTitle>Editar tarefa</DialogTitle><DialogDescription>O salvamento usa controle de versão e não altera prazos processuais.</DialogDescription></DialogHeader>
          <div className="grid gap-4">
            <div className="space-y-2"><Label htmlFor="task-title">Título</Label><Input id="task-title" value={editState.title} onChange={(event) => setEditState((current) => ({ ...current, title: event.target.value }))} maxLength={500} /></div>
            <div className="space-y-2"><Label htmlFor="task-description">Descrição</Label><Textarea id="task-description" value={editState.description} onChange={(event) => setEditState((current) => ({ ...current, description: event.target.value }))} maxLength={10_000} /></div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2"><Label htmlFor="task-due">Prazo geral</Label><Input id="task-due" type="datetime-local" value={editState.dueAt} onChange={(event) => setEditState((current) => ({ ...current, dueAt: event.target.value }))} /></div>
              <div className="space-y-2"><Label htmlFor="task-review-due">Prazo de revisão</Label><Input id="task-review-due" type="datetime-local" value={editState.reviewDueAt} disabled={!activeReviewer} onChange={(event) => setEditState((current) => ({ ...current, reviewDueAt: event.target.value }))} /></div>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2"><Label>Prioridade</Label><Select value={editState.priority} onValueChange={(value) => setEditState((current) => ({ ...current, priority: value as EditState['priority'] }))}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="none">Sem prioridade</SelectItem><SelectItem value="alta">Alta</SelectItem><SelectItem value="media">Média</SelectItem><SelectItem value="baixa">Baixa</SelectItem></SelectContent></Select></div>
              <div className="rounded-lg border p-3 text-sm"><strong className="block text-xs">Política de revisão</strong><span className="mt-1 block text-xs text-muted-foreground">{activeReviewer ? 'Revisão obrigatória; remova o revisor em “Equipe e prazos” para desativar.' : 'Sem revisão; adicione um revisor em “Equipe e prazos” para ativar.'}</span></div>
            </div>
            {editDateError && <p className="text-xs text-destructive" role="alert">O prazo de revisão não pode ser posterior ao prazo geral.</p>}
            {participantDeadlineConflict && <p className="text-xs text-destructive" role="alert">Há prazo individual posterior ao novo prazo geral. Ajuste a equipe antes de salvar.</p>}
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setEditOpen(false)}>Cancelar</Button><Button disabled={!editState.title.trim() || editDateError || participantDeadlineConflict || editMutation.isPending} onClick={() => editMutation.mutate()}>{editMutation.isPending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}Salvar</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={teamOpen} onOpenChange={setTeamOpen}>
        <DialogContent className="max-h-[92vh] max-w-2xl overflow-y-auto">
          <DialogHeader><DialogTitle>Equipe e prazos individuais</DialogTitle><DialogDescription>Somente membros ativos do workspace podem participar. O revisor não pode ser responsável ou executor.</DialogDescription></DialogHeader>
          <div className="space-y-5">
            <div className="space-y-2"><Label htmlFor="task-team-search">Buscar na equipe</Label><Input id="task-team-search" value={memberSearch} onChange={(event) => setMemberSearch(event.target.value)} placeholder="Nome do membro" />{memberSearch.trim().length === 1 && <p className="text-xs text-muted-foreground">Digite pelo menos 2 caracteres para pesquisar.</p>}</div>
            <div className="grid gap-3 sm:grid-cols-[1fr_220px]">
              <div className="space-y-2"><Label>Responsável</Label><Select value={teamState.ownerId} onValueChange={(ownerId) => setTeamState((current) => ({ ...current, ownerId, assigneeIds: current.assigneeIds.filter((id) => id !== ownerId), reviewerId: current.reviewerId === ownerId ? '' : current.reviewerId }))}><SelectTrigger><SelectValue placeholder="Selecione" /></SelectTrigger><SelectContent>{actorOptions?.map((member) => <SelectItem key={member.id} value={member.id}>{member.label}</SelectItem>)}</SelectContent></Select><label className="flex items-center gap-2 text-xs text-muted-foreground"><Checkbox checked={teamState.requiredByUserId[teamState.ownerId] === true} onCheckedChange={(required) => setTeamState((current) => ({ ...current, requiredByUserId: { ...current.requiredByUserId, [current.ownerId]: required === true } }))} />Responsável também é executor obrigatório</label></div>
              <div className="space-y-2"><Label htmlFor="owner-due">Prazo do responsável</Label><Input id="owner-due" type="datetime-local" value={teamState.dueByKey[`owner:${teamState.ownerId}`] ?? ''} onChange={(event) => setTeamState((current) => ({ ...current, dueByKey: { ...current.dueByKey, [`owner:${current.ownerId}`]: event.target.value } }))} /></div>
            </div>
            <div>
              <Label>Executores</Label>
              <div className="mt-2 space-y-2 rounded-lg border p-3">
                {assigneeCandidates.length ? assigneeCandidates.map((member) => {
                  const checked = teamState.assigneeIds.includes(member.id);
                  return <div key={member.id} className="grid items-center gap-3 sm:grid-cols-[1fr_220px]"><div><label className="flex items-center gap-2 text-sm"><Checkbox disabled={!checked && teamParticipantLimitReached} checked={checked} onCheckedChange={(next) => setTeamState((current) => ({ ...current, assigneeIds: next === true ? [...current.assigneeIds, member.id] : current.assigneeIds.filter((id) => id !== member.id), requiredByUserId: { ...current.requiredByUserId, [member.id]: next === true ? current.requiredByUserId[member.id] !== false : false } }))} />{member.label}</label>{checked && <label className="mt-2 flex items-center gap-2 pl-6 text-xs text-muted-foreground"><Checkbox checked={teamState.requiredByUserId[member.id] !== false} onCheckedChange={(required) => setTeamState((current) => ({ ...current, requiredByUserId: { ...current.requiredByUserId, [member.id]: required === true } }))} />Obrigatório para concluir</label>}</div>{checked && <Input aria-label={`Prazo de ${member.label}`} type="datetime-local" value={teamState.dueByKey[`assignee:${member.id}`] ?? ''} onChange={(event) => setTeamState((current) => ({ ...current, dueByKey: { ...current.dueByKey, [`assignee:${member.id}`]: event.target.value } }))} />}</div>;
                }) : <p className="text-sm text-muted-foreground">Nenhum outro membro ativo disponível.</p>}
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-[1fr_220px]">
              <div className="space-y-2"><Label>Revisor</Label><Select disabled={(teamParticipantLimitReached && !teamState.reviewerId) || (!canAddReviewer && !teamState.reviewerId)} value={teamState.reviewerId || 'none'} onValueChange={(reviewerId) => setTeamState((current) => ({ ...current, reviewerId: reviewerId === 'none' ? '' : reviewerId }))}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="none">Sem revisor</SelectItem>{teamState.reviewerId && !canAddReviewer && <SelectItem value={teamState.reviewerId}>Revisor atual</SelectItem>}{reviewerCandidates.map((member) => <SelectItem key={member.id} value={member.id}>{member.label}</SelectItem>)}</SelectContent></Select>{!canAddReviewer && <p className="text-xs text-muted-foreground">É possível remover o revisor atual, mas não adicionar outro sem capacidade contratada.</p>}</div>
              {teamState.reviewerId && <div className="space-y-2"><Label htmlFor="reviewer-due">Prazo do revisor</Label><Input id="reviewer-due" type="datetime-local" value={teamState.dueByKey[`reviewer:${teamState.reviewerId}`] ?? ''} onChange={(event) => setTeamState((current) => ({ ...current, dueByKey: { ...current.dueByKey, [`reviewer:${current.reviewerId}`]: event.target.value } }))} /></div>}
            </div>
            <div className="space-y-2"><Label htmlFor="team-reason">Motivo da alteração</Label><Textarea id="team-reason" value={teamState.reason} onChange={(event) => setTeamState((current) => ({ ...current, reason: event.target.value }))} minLength={8} maxLength={500} placeholder="Explique a redistribuição da tarefa..." /></div>
            {membersQuery.hasNextPage && <Button size="sm" variant="outline" disabled={membersQuery.isFetchingNextPage} onClick={() => void membersQuery.fetchNextPage()}>{membersQuery.isFetchingNextPage && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}Carregar mais membros</Button>}
            {participantLimit != null && <p className="text-xs text-muted-foreground">{teamParticipantCount} de {participantLimit} participantes selecionados.</p>}
            {teamDeadlineConflicts.length > 0 && <p className="text-xs text-destructive" role="alert">Nenhum prazo individual pode ser posterior ao prazo geral da tarefa.</p>}
            {teamParticipantLimitExceeded && <p className="text-xs text-destructive" role="alert">Reduza a equipe para o limite de {participantLimit} participantes.</p>}
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setTeamOpen(false)}>Cancelar</Button><Button disabled={!teamState.ownerId || teamState.reason.trim().length < 8 || teamDeadlineConflicts.length > 0 || teamParticipantLimitExceeded || teamMutation.isPending} onClick={() => teamMutation.mutate()}>{teamMutation.isPending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}Atualizar equipe</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
