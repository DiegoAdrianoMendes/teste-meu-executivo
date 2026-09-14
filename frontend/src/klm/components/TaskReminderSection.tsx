import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertCircle,
  BellRing,
  CalendarClock,
  LoaderCircle,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
} from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/hooks/use-toast';
import { useAppStore } from '@/store/app-context';
import {
  createTaskReminderRule,
  disableTaskReminderRule,
  listTaskReminderRules,
  newTaskReminderIdempotencyKey,
  updateTaskReminderRule,
  type TaskReminderChannel,
  type TaskReminderRecipient,
  type TaskReminderRelativeTo,
  type TaskReminderRule,
  type TaskReminderRuleState,
} from '@/services/task-reminders';
import {
  classifyTaskError,
  taskErrorMessage,
  type CollaborativeTask,
  type TaskMemberOption,
} from '@/services/tasks';
import { KlmPill } from './Primitives';

const MAX_REMINDER_OFFSET_MINUTES = 5 * 366 * 24 * 60;
const offsetPresets = [0, 15, 30, 60, 120, 24 * 60, 2 * 24 * 60, 7 * 24 * 60];

const relativeLabel: Record<TaskReminderRelativeTo, string> = {
  general_due: 'Prazo geral da tarefa',
  personal_due: 'Prazo individual do destinatário',
  review_due: 'Prazo de revisão',
};

const recipientLabel: Record<TaskReminderRecipient, string> = {
  owner: 'Responsável',
  assignees: 'Todos os executores',
  required_assignees: 'Executores obrigatórios',
  reviewer: 'Revisor',
  specific_user: 'Pessoa específica da tarefa',
};

const channelLabel: Record<TaskReminderChannel, string> = {
  in_app: 'Inbox do Meu Executivo',
  email: 'E-mail',
  whatsapp: 'WhatsApp',
};

const statePresentation: Record<
  TaskReminderRuleState,
  { label: string; description: string; tone: 'blue' | 'gold' | 'red' | 'green' | 'muted' }
> = {
  scheduled: {
    label: 'Agendado',
    description: 'O envio interno está persistido e será recuperado automaticamente.',
    tone: 'green',
  },
  pending_due: {
    label: 'Aguardando prazo',
    description: 'A regra existe, mas ao menos um destinatário ainda não tem o prazo escolhido.',
    tone: 'gold',
  },
  pending_recipient: {
    label: 'Aguardando destinatário',
    description: 'A regra será agendada quando houver um participante elegível.',
    tone: 'gold',
  },
  unavailable: {
    label: 'Canal indisponível',
    description: 'Nenhuma entrega externa foi simulada. Troque o canal para o inbox interno.',
    tone: 'red',
  },
  disabled: {
    label: 'Desativado',
    description: 'A regra não agenda novas entregas.',
    tone: 'muted',
  },
};

const participantRoleLabel = {
  owner: 'Responsável',
  assignee: 'Executor',
  reviewer: 'Revisor',
  observer: 'Observador',
} as const;

interface ReminderEditorState {
  mode: 'create' | 'edit';
  ruleId: string | null;
  expectedVersion: number;
  relativeTo: TaskReminderRelativeTo;
  offsetMinutesBefore: number;
  offsetPreset: string;
  channel: TaskReminderChannel;
  recipient: TaskReminderRecipient;
  recipientUserId: string;
  idempotencyKey: string;
}

interface DisableIntent {
  rule: TaskReminderRule;
  idempotencyKey: string;
}

export interface TaskReminderSectionProps {
  task: CollaborativeTask;
  memberOptions: TaskMemberOption[];
  canEdit: boolean;
  onConflict: (message: string) => void;
  onSuccess: (message: string) => void;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return 'Ainda não agendado';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('pt-BR', {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(date);
}

function formatOffset(minutes: number): string {
  if (minutes === 0) return 'No momento do prazo';
  if (minutes % (7 * 24 * 60) === 0) {
    const weeks = minutes / (7 * 24 * 60);
    return `${weeks} ${weeks === 1 ? 'semana' : 'semanas'} antes`;
  }
  if (minutes % (24 * 60) === 0) {
    const days = minutes / (24 * 60);
    return `${days} ${days === 1 ? 'dia' : 'dias'} antes`;
  }
  if (minutes % 60 === 0) {
    const hours = minutes / 60;
    return `${hours} ${hours === 1 ? 'hora' : 'horas'} antes`;
  }
  return `${minutes} minutos antes`;
}

function participantName(
  task: CollaborativeTask,
  options: TaskMemberOption[],
  userId: string | null,
): string {
  if (!userId) return 'Pessoa não definida';
  const participant = task.participants.find((item) => item.active && item.userId === userId);
  if (participant?.labelSnapshot.trim()) return participant.labelSnapshot.trim();
  return options.find((item) => item.id === userId)?.label ?? 'Pessoa vinculada';
}

function editorForCreate(task: CollaborativeTask): ReminderEditorState {
  return {
    mode: 'create',
    ruleId: null,
    expectedVersion: task.version,
    relativeTo: 'general_due',
    offsetMinutesBefore: 24 * 60,
    offsetPreset: String(24 * 60),
    channel: 'in_app',
    recipient: 'owner',
    recipientUserId: '',
    idempotencyKey: newTaskReminderIdempotencyKey('create'),
  };
}

function editorForRule(rule: TaskReminderRule): ReminderEditorState {
  const preset = offsetPresets.includes(rule.offsetMinutesBefore)
    ? String(rule.offsetMinutesBefore)
    : 'custom';
  return {
    mode: 'edit',
    ruleId: rule.id,
    expectedVersion: rule.version,
    relativeTo: rule.relativeTo,
    offsetMinutesBefore: rule.offsetMinutesBefore,
    offsetPreset: preset,
    channel: rule.channel,
    recipient: rule.recipient,
    recipientUserId: rule.recipientUserId ?? '',
    idempotencyKey: newTaskReminderIdempotencyKey('update'),
  };
}

export function TaskReminderSection({
  task,
  memberOptions,
  canEdit,
  onConflict,
  onSuccess,
}: TaskReminderSectionProps) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const { getCapability = () => null } = useAppStore();
  const [editor, setEditor] = useState<ReminderEditorState | null>(null);
  const [disableIntent, setDisableIntent] = useState<DisableIntent | null>(null);

  useEffect(() => {
    setEditor(null);
    setDisableIntent(null);
  }, [task.id]);

  const rulesQuery = useQuery({
    queryKey: ['task-reminder-rules', task.id],
    queryFn: () => listTaskReminderRules(task.id),
    staleTime: 10_000,
    retry: false,
  });
  const rules = rulesQuery.data ?? [];
  const reminderCapability = getCapability('task_reminder_rules');
  const reminderLimit = reminderCapability?.limit ?? null;
  // Missing capability data is not evidence of a contract.  Keep existing
  // rules readable, but fail closed for every new billable reminder intent.
  const contracted = Boolean(
    reminderCapability?.contracted
    && reminderCapability.enabled
    && reminderCapability.permitted,
  );
  const terminal = task.status === 'done' || Boolean(task.cancelledAt);
  const capacityAvailable = reminderLimit == null || rules.length < reminderLimit;
  const canCreate = canEdit && !terminal && contracted && capacityAvailable;

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['task-reminder-rules', task.id] }),
      queryClient.invalidateQueries({ queryKey: ['collaborative-task', task.id] }),
      queryClient.invalidateQueries({ queryKey: ['collaborative-tasks'] }),
    ]);
  };

  const handleError = async (error: unknown) => {
    const kind = classifyTaskError(error);
    const message = taskErrorMessage(error, 'Não foi possível alterar o lembrete.');
    if (kind === 'conflict') {
      setEditor(null);
      setDisableIntent(null);
      onConflict(`${message} Os lembretes foram recarregados; revise a versão atual antes de tentar novamente.`);
      await refresh();
      return;
    }
    if (kind === 'quota') {
      toast({
        variant: 'destructive',
        title: 'Limite de lembretes atingido',
        description: message,
      });
      await refresh();
      return;
    }
    toast({
      variant: 'destructive',
      title: 'Lembrete não alterado',
      description: message,
    });
  };

  const saveMutation = useMutation({
    mutationFn: (draft: ReminderEditorState) => {
      const recipientUserId = draft.recipient === 'specific_user'
        ? draft.recipientUserId
        : null;
      if (draft.mode === 'create') {
        return createTaskReminderRule(
          task.id,
          {
            expectedVersion: draft.expectedVersion,
            relativeTo: draft.relativeTo,
            offsetMinutesBefore: draft.offsetMinutesBefore,
            channel: draft.channel,
            recipient: draft.recipient,
            recipientUserId,
          },
          draft.idempotencyKey,
        );
      }
      if (!draft.ruleId) throw new Error('A regra ainda não foi carregada.');
      return updateTaskReminderRule(
        task.id,
        draft.ruleId,
        {
          expectedVersion: draft.expectedVersion,
          relativeTo: draft.relativeTo,
          offsetMinutesBefore: draft.offsetMinutesBefore,
          channel: draft.channel,
          recipient: draft.recipient,
          recipientUserId,
        },
        draft.idempotencyKey,
      );
    },
    onSuccess: async (_, draft) => {
      setEditor(null);
      onConflict('');
      onSuccess(draft.mode === 'create' ? 'Lembrete criado.' : 'Lembrete atualizado.');
      await refresh();
    },
    onError: (error) => void handleError(error),
  });

  const disableMutation = useMutation({
    mutationFn: (intent: DisableIntent) => disableTaskReminderRule(
      task.id,
      intent.rule.id,
      intent.rule.version,
      intent.idempotencyKey,
    ),
    onSuccess: async () => {
      setDisableIntent(null);
      onConflict('');
      onSuccess('Lembrete desativado e entregas pendentes canceladas.');
      await refresh();
    },
    onError: (error) => void handleError(error),
  });

  const specificParticipants = useMemo(
    () => task.participants.filter((item) => item.active),
    [task.participants],
  );
  const offsetValid = Boolean(
    editor
      && Number.isInteger(editor.offsetMinutesBefore)
      && editor.offsetMinutesBefore >= 0
      && editor.offsetMinutesBefore <= MAX_REMINDER_OFFSET_MINUTES,
  );
  const editorValid = Boolean(
    editor
      && offsetValid
      && editor.channel === 'in_app'
      && (editor.recipient !== 'specific_user' || editor.recipientUserId),
  );

  if (rulesQuery.isPending) {
    return <div className="space-y-3"><Skeleton className="h-20 w-full" /><Skeleton className="h-32 w-full" /></div>;
  }

  if (rulesQuery.isError) {
    return (
      <Alert>
        <AlertCircle className="h-4 w-4" />
        <AlertTitle>Lembretes indisponíveis</AlertTitle>
        <AlertDescription className="space-y-3">
          <p>{taskErrorMessage(rulesQuery.error, 'Não foi possível carregar as regras.')}</p>
          <Button size="sm" variant="outline" onClick={() => void rulesQuery.refetch()}>
            <RefreshCw className="mr-2 h-4 w-4" />Tentar novamente
          </Button>
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="space-y-4">
      <section className="rounded-lg border bg-card p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <BellRing className="h-4 w-4 text-primary" />
              <h3 className="text-sm font-semibold">Regras de lembrete</h3>
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              O prazo real da tarefa é a fonte da agenda. Alterações de prazo reprogramam as entregas pendentes.
            </p>
          </div>
          {canEdit && (
            <Button size="sm" disabled={!canCreate} onClick={() => setEditor(editorForCreate(task))}>
              <Plus className="mr-1.5 h-3.5 w-3.5" />Novo lembrete
            </Button>
          )}
        </div>
        {reminderLimit != null && (
          <p className="mt-3 text-xs text-muted-foreground">
            {rules.length} de {reminderLimit} regras ativas neste plano.
          </p>
        )}
        {terminal && canEdit && (
          <p className="mt-3 text-xs text-amber-700 dark:text-amber-300">
            Reabra a tarefa para criar ou editar regras. Você ainda pode desativar regras existentes.
          </p>
        )}
        {!contracted && canEdit && (
          <p className="mt-3 text-xs text-amber-700 dark:text-amber-300">
            A criação está bloqueada porque a capacidade não foi contratada ou o manifesto está indisponível; regras já existentes continuam visíveis.
          </p>
        )}
      </section>

      <Alert>
        <BellRing className="h-4 w-4" />
        <AlertTitle>Somente o inbox interno está disponível</AlertTitle>
        <AlertDescription>
          E-mail e WhatsApp ficam fora do seletor enquanto não houver adapter e provedor configurados. Nenhum envio externo é tratado como sucesso.
        </AlertDescription>
      </Alert>

      {rules.length === 0 ? (
        <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
          Nenhuma regra de lembrete ativa.
        </p>
      ) : rules.map((rule) => {
        const presentation = statePresentation[rule.state];
        const recipient = rule.recipient === 'specific_user'
          ? participantName(task, memberOptions, rule.recipientUserId)
          : recipientLabel[rule.recipient];
        return (
          <article key={rule.id} className="rounded-lg border bg-card p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm font-semibold">{formatOffset(rule.offsetMinutesBefore)}</p>
                  <KlmPill tone={presentation.tone}>{presentation.label}</KlmPill>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {relativeLabel[rule.relativeTo]} · {recipient} · {channelLabel[rule.channel]}
                </p>
              </div>
              {canEdit && (
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={terminal}
                    aria-label={`Editar lembrete ${formatOffset(rule.offsetMinutesBefore)}`}
                    onClick={() => setEditor(editorForRule(rule))}
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    aria-label={`Desativar lembrete ${formatOffset(rule.offsetMinutesBefore)}`}
                    onClick={() => setDisableIntent({
                      rule,
                      idempotencyKey: newTaskReminderIdempotencyKey('disable'),
                    })}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              )}
            </div>
            <p className="mt-3 text-xs">{presentation.description}</p>
            <div className="mt-3 flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted-foreground">
              <span className="inline-flex items-center gap-1.5">
                <CalendarClock className="h-3.5 w-3.5" />
                Próximo envio: {formatDate(rule.nextScheduledAt)}
              </span>
              <span>{rule.deliveries.length} {rule.deliveries.length === 1 ? 'destinatário resolvido' : 'destinatários resolvidos'}</span>
            </div>
            {!rule.deliveryAvailable && (
              <p className="mt-3 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
                Canal não configurado. Esta regra não produz envio externo.
              </p>
            )}
          </article>
        );
      })}

      <Dialog open={Boolean(editor)} onOpenChange={(next) => { if (!next) setEditor(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editor?.mode === 'create' ? 'Novo lembrete' : 'Editar lembrete'}</DialogTitle>
            <DialogDescription>
              A regra usa a versão atual e nunca inventa uma data quando o prazo de referência está ausente.
            </DialogDescription>
          </DialogHeader>
          {editor && (
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="task-reminder-relative-to">Prazo de referência</Label>
                <Select
                  value={editor.relativeTo}
                  onValueChange={(value: TaskReminderRelativeTo) => setEditor((current) => current && ({ ...current, relativeTo: value }))}
                >
                  <SelectTrigger id="task-reminder-relative-to"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {Object.entries(relativeLabel).map(([value, label]) => (
                      <SelectItem key={value} value={value}>{label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="task-reminder-offset">Antecedência</Label>
                <Select
                  value={editor.offsetPreset}
                  onValueChange={(value) => setEditor((current) => current && ({
                    ...current,
                    offsetPreset: value,
                    offsetMinutesBefore: value === 'custom'
                      ? current.offsetMinutesBefore
                      : Number(value),
                  }))}
                >
                  <SelectTrigger id="task-reminder-offset"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {offsetPresets.map((minutes) => (
                      <SelectItem key={minutes} value={String(minutes)}>{formatOffset(minutes)}</SelectItem>
                    ))}
                    <SelectItem value="custom">Personalizado em minutos</SelectItem>
                  </SelectContent>
                </Select>
                {editor.offsetPreset === 'custom' && (
                  <div className="space-y-1.5">
                    <Label htmlFor="task-reminder-custom-offset">Minutos antes do prazo</Label>
                    <Input
                      id="task-reminder-custom-offset"
                      type="number"
                      min={0}
                      max={MAX_REMINDER_OFFSET_MINUTES}
                      step={1}
                      value={editor.offsetMinutesBefore}
                      onChange={(event) => setEditor((current) => current && ({
                        ...current,
                        offsetMinutesBefore: Number(event.target.value),
                      }))}
                    />
                    {!offsetValid && <p className="text-xs text-destructive">Informe um número inteiro não negativo dentro do limite.</p>}
                  </div>
                )}
              </div>

              <div className="space-y-2">
                <Label htmlFor="task-reminder-channel">Canal</Label>
                <Select
                  value={editor.channel}
                  onValueChange={(value: TaskReminderChannel) => setEditor((current) => current && ({ ...current, channel: value }))}
                >
                  <SelectTrigger id="task-reminder-channel"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {editor.channel !== 'in_app' && (
                      <SelectItem value={editor.channel} disabled>
                        {channelLabel[editor.channel]} (indisponível)
                      </SelectItem>
                    )}
                    <SelectItem value="in_app">{channelLabel.in_app}</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">E-mail e WhatsApp só aparecerão quando houver entrega real configurada.</p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="task-reminder-recipient">Destinatário</Label>
                <Select
                  value={editor.recipient}
                  onValueChange={(value: TaskReminderRecipient) => setEditor((current) => current && ({
                    ...current,
                    recipient: value,
                    recipientUserId: value === 'specific_user' ? current.recipientUserId : '',
                  }))}
                >
                  <SelectTrigger id="task-reminder-recipient"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {Object.entries(recipientLabel).map(([value, label]) => (
                      <SelectItem key={value} value={value}>{label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {editor.recipient === 'specific_user' && (
                <div className="space-y-2">
                  <Label htmlFor="task-reminder-specific-user">Pessoa participante</Label>
                  <Select
                    value={editor.recipientUserId}
                    onValueChange={(value) => setEditor((current) => current && ({ ...current, recipientUserId: value }))}
                  >
                    <SelectTrigger id="task-reminder-specific-user"><SelectValue placeholder="Selecione uma pessoa" /></SelectTrigger>
                    <SelectContent>
                      {specificParticipants.map((participant) => (
                        <SelectItem key={participant.id} value={participant.userId}>
                          {participantName(task, memberOptions, participant.userId)} · {participantRoleLabel[participant.role]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}

              {editor.channel !== 'in_app' && (
                <Alert variant="destructive">
                  <AlertCircle className="h-4 w-4" />
                  <AlertTitle>Canal indisponível</AlertTitle>
                  <AlertDescription>Selecione o inbox interno para salvar esta regra.</AlertDescription>
                </Alert>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditor(null)}>Cancelar</Button>
            <Button
              disabled={!editorValid || saveMutation.isPending}
              onClick={() => editor && saveMutation.mutate(editor)}
            >
              {saveMutation.isPending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}
              Salvar lembrete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(disableIntent)} onOpenChange={(next) => { if (!next) setDisableIntent(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Desativar lembrete?</DialogTitle>
            <DialogDescription>
              Entregas ainda pendentes serão canceladas. O histórico da regra continuará preservado.
            </DialogDescription>
          </DialogHeader>
          {disableIntent && (
            <p className="rounded-md bg-secondary px-3 py-2 text-sm">
              {formatOffset(disableIntent.rule.offsetMinutesBefore)} · {relativeLabel[disableIntent.rule.relativeTo]}
            </p>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDisableIntent(null)}>Voltar</Button>
            <Button
              variant="destructive"
              disabled={disableMutation.isPending}
              onClick={() => disableIntent && disableMutation.mutate(disableIntent)}
            >
              {disableMutation.isPending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}
              Desativar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
