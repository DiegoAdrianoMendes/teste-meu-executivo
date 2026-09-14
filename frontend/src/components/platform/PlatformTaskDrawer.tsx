import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, CalendarClock, CheckCircle2, History, MessageSquareText, Pencil, RefreshCw, ShieldCheck, UsersRound } from 'lucide-react';
import { useMemo, useState } from 'react';

import { PlatformTaskAssignmentsDialog, PlatformTaskEditDialog } from '@/components/platform/PlatformTaskEditor';
import { availablePlatformTaskCommands } from '@/components/platform/platform-task-commands';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { canPlatform } from '@/lib/platform-access';
import { resolvePlatformEntity, type PlatformSearchEntityType } from '@/services/platform-search';
import {
  addPlatformTaskComment,
  classifyPlatformTaskError,
  executePlatformTaskCommand,
  getPlatformTask,
  listPlatformTaskEvents,
  platformTaskErrorMessage,
  updatePlatformTaskParticipantState,
  type PlatformTask,
  type PlatformTaskCommand,
} from '@/services/platform-tasks';
import type { AuthUser } from '@/types';

const STATUS_LABEL: Record<string, string> = {
  pending: 'Pendente', in_progress: 'Em andamento', blocked: 'Bloqueada', done: 'Concluída', cancelled: 'Cancelada',
};
const REVIEW_LABEL: Record<string, string> = {
  not_required: 'Sem revisão', draft: 'Revisão configurada', pending: 'Aguardando revisão', approved: 'Aprovada', changes_requested: 'Ajustes solicitados', rejected: 'Rejeitada', cancelled: 'Cancelada',
};
const EVENT_LABEL: Record<string, string> = {
  created: 'Tarefa criada', updated: 'Tarefa atualizada', participants_changed: 'Equipe alterada',
  started: 'Tarefa iniciada', blocked: 'Tarefa bloqueada', review_submitted: 'Enviada para revisão',
  review_approved: 'Revisão aprovada', review_changes_requested: 'Ajustes solicitados',
  review_rejected: 'Revisão rejeitada', completed: 'Tarefa concluída', reopened: 'Tarefa reaberta',
  cancelled: 'Tarefa cancelada', comment_added: 'Comentário adicionado',
};
const COMMAND_LABEL: Record<PlatformTaskCommand, string> = {
  start: 'Iniciar', block: 'Bloquear', 'submit-for-review': 'Enviar para revisão', approve: 'Aprovar',
  'request-changes': 'Solicitar ajustes', reject: 'Rejeitar', complete: 'Concluir', reopen: 'Reabrir', cancel: 'Cancelar',
};

function formatDate(value: string | null | undefined): string {
  if (!value) return 'Não definido';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'Não definido' : parsed.toLocaleString('pt-BR');
}

function useResolvedLabels(task: PlatformTask | undefined) {
  const specifications = useMemo(() => {
    if (!task) return [] as Array<{ type: PlatformSearchEntityType; id: string }>;
    return [
      { type: 'staff' as const, id: task.ownerUserId },
      ...(task.reviewerUserId ? [{ type: 'staff' as const, id: task.reviewerUserId }] : []),
      ...(task.workspaceId ? [{ type: 'accounts' as const, id: task.workspaceId }] : []),
      ...(task.linkedTicketId ? [{ type: 'tickets' as const, id: task.linkedTicketId }] : []),
      ...(task.linkedIncidentId ? [{ type: 'incidents' as const, id: task.linkedIncidentId }] : []),
      ...(task.linkedLeadId ? [{ type: 'leads' as const, id: task.linkedLeadId }] : []),
      ...(task.linkedReleaseId ? [{ type: 'releases' as const, id: task.linkedReleaseId }] : []),
    ];
  }, [task]);
  const query = useQuery({
    queryKey: ['platform-task', 'resolved-labels', specifications],
    queryFn: async () => {
      const groups = new Map<PlatformSearchEntityType, string[]>();
      specifications.forEach(({ type, id }) => groups.set(type, [...(groups.get(type) ?? []), id]));
      const responses = await Promise.all(Array.from(groups.entries()).map(([type, ids]) => resolvePlatformEntity({
        entityType: type,
        purpose: type === 'staff' ? 'platform_task_reference' : 'platform_task_relation',
        ids,
        workspaceId: type === 'tickets' || type === 'leads' ? task?.workspaceId : null,
      })));
      return new Map(responses.flatMap((response) => response.items).map((item) => [item.id, item.label]));
    },
    enabled: specifications.length > 0,
    retry: false,
  });
  return query.data ?? new Map<string, string>();
}

export function PlatformTaskDrawer({ taskId, summary, open, onOpenChange, currentUser }: {
  taskId: string | null;
  summary?: PlatformTask | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  currentUser: AuthUser;
}) {
  const queryClient = useQueryClient();
  const [editOpen, setEditOpen] = useState(false);
  const [assignmentsOpen, setAssignmentsOpen] = useState(false);
  const [command, setCommand] = useState<PlatformTaskCommand | null>(null);
  const [reason, setReason] = useState('');
  const [decisionComment, setDecisionComment] = useState('');
  const [comment, setComment] = useState('');
  const [participantIntent, setParticipantIntent] = useState<{ userId: string; label: string; state: 'assigned' | 'in_progress' | 'completed' } | null>(null);
  const [participantReason, setParticipantReason] = useState('');
  const [conflict, setConflict] = useState<string | null>(null);
  const taskQuery = useQuery({ queryKey: ['blackbox', 'platform-tasks', taskId], queryFn: () => getPlatformTask(taskId!), enabled: open && Boolean(taskId), initialData: summary?.id === taskId ? summary : undefined, retry: false });
  const eventsQuery = useQuery({ queryKey: ['blackbox', 'platform-tasks', taskId, 'events'], queryFn: () => listPlatformTaskEvents(taskId!, 0, 200), enabled: open && Boolean(taskId), retry: false });
  const task = taskQuery.data;
  const labels = useResolvedLabels(task);
  const canEdit = canPlatform(currentUser, 'platform_tasks', 'edit');
  const canAssign = canPlatform(currentUser, 'platform_tasks', 'assign');
  const canComment = canPlatform(currentUser, 'platform_tasks', 'comment');

  const refresh = async () => {
    if (!taskId) return;
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['blackbox', 'platform-tasks'] }),
      queryClient.invalidateQueries({ queryKey: ['blackbox', 'platform-tasks', taskId] }),
    ]);
  };
  const handleError = (error: unknown) => {
    if (classifyPlatformTaskError(error) === 'conflict') {
      setConflict(`${platformTaskErrorMessage(error, 'A tarefa mudou em outra sessão.')} Os dados atuais foram recarregados.`);
      void refresh();
    }
  };
  const commandMutation = useMutation({
    mutationFn: () => {
      if (!task || !command) throw new Error('A tarefa ainda não foi carregada.');
      return executePlatformTaskCommand(task.id, command, task.version, { reason: reason.trim(), comment: decisionComment.trim() || undefined });
    },
    onSuccess: async () => { setCommand(null); setReason(''); setDecisionComment(''); setConflict(null); await refresh(); },
    onError: handleError,
  });
  const commentMutation = useMutation({
    mutationFn: () => {
      if (!task) throw new Error('A tarefa ainda não foi carregada.');
      return addPlatformTaskComment(task.id, task.version, comment.trim());
    },
    onSuccess: async () => { setComment(''); await refresh(); },
    onError: handleError,
  });
  const participantMutation = useMutation({
    mutationFn: () => {
      if (!task || !participantIntent) throw new Error('O executor ainda não foi carregado.');
      return updatePlatformTaskParticipantState(task.id, participantIntent.userId, task.version, participantIntent.state, participantReason.trim());
    },
    onSuccess: async () => { setParticipantIntent(null); setParticipantReason(''); await refresh(); },
    onError: handleError,
  });
  const events = eventsQuery.data?.items ?? [];

  return <>
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex h-dvh w-[98vw] max-w-none flex-col gap-0 overflow-hidden p-0 sm:max-w-[820px]">
        <SheetHeader className="border-b px-5 py-4 pr-12 text-left">
          <div className="flex flex-wrap gap-2">{task ? <><Badge variant={task.priority === 'P0' || task.priority === 'P1' ? 'destructive' : 'secondary'}>{task.priority}</Badge><Badge variant="outline">{STATUS_LABEL[task.status]}</Badge><Badge variant="secondary">{REVIEW_LABEL[task.reviewState]}</Badge></> : null}</div>
          <SheetTitle>{task?.title ?? summary?.title ?? 'Carregando tarefa interna…'}</SheetTitle>
          <SheetDescription>Registro autoritativo da equipe do SaaS · alterações protegidas por versão e auditoria.</SheetDescription>
        </SheetHeader>
        {taskQuery.isPending && !task ? <div className="space-y-3 p-6"><Skeleton className="h-8 w-2/3" /><Skeleton className="h-36 w-full" /></div> : null}
        {taskQuery.isError && !task ? <div className="p-6"><Alert variant="destructive"><AlertCircle className="h-4 w-4" /><AlertTitle>Tarefa indisponível</AlertTitle><AlertDescription>{platformTaskErrorMessage(taskQuery.error, 'Não foi possível carregar a tarefa.')}</AlertDescription></Alert></div> : null}
        {task ? <>
          <div className="space-y-3 border-b px-5 py-3">
            {conflict ? <Alert variant="destructive"><RefreshCw className="h-4 w-4" /><AlertTitle>Conflito de versão</AlertTitle><AlertDescription>{conflict}</AlertDescription></Alert> : null}
            <div className="flex flex-wrap gap-2">
              {canEdit && !['done', 'cancelled'].includes(task.status) ? <Button size="sm" variant="outline" onClick={() => setEditOpen(true)}><Pencil className="mr-1.5 h-3.5 w-3.5" />Editar</Button> : null}
              {canAssign && !['done', 'cancelled'].includes(task.status) ? <Button size="sm" variant="outline" onClick={() => setAssignmentsOpen(true)}><UsersRound className="mr-1.5 h-3.5 w-3.5" />Equipe e prazos</Button> : null}
              {availablePlatformTaskCommands(task, currentUser).map((item) => <Button key={item} size="sm" variant={item === 'cancel' || item === 'reject' ? 'destructive' : item === 'block' || item === 'request-changes' ? 'outline' : 'default'} onClick={() => { setCommand(item); setReason(''); setDecisionComment(''); }}>{COMMAND_LABEL[item]}</Button>)}
            </div>
          </div>
          <Tabs defaultValue="summary" className="flex min-h-0 flex-1 flex-col">
            <div className="overflow-x-auto border-b px-5"><TabsList className="h-11 min-w-max bg-transparent p-0"><TabsTrigger value="summary">Resumo</TabsTrigger><TabsTrigger value="team">Equipe</TabsTrigger><TabsTrigger value="review">Revisão</TabsTrigger><TabsTrigger value="comments">Comentários</TabsTrigger><TabsTrigger value="history">Histórico</TabsTrigger></TabsList></div>
            <ScrollArea className="min-h-0 flex-1">
              <TabsContent value="summary" className="m-0 space-y-5 p-5">
                <div className="grid gap-3 sm:grid-cols-3"><div className="rounded-lg border p-4"><p className="text-xs text-muted-foreground">Prazo geral</p><p className="mt-1 text-sm font-medium">{formatDate(task.dueAt)}</p></div><div className="rounded-lg border p-4"><p className="text-xs text-muted-foreground">Prazo do responsável</p><p className="mt-1 text-sm font-medium">{formatDate(task.ownerDueAt)}</p></div><div className="rounded-lg border p-4"><p className="text-xs text-muted-foreground">Prazo da revisão</p><p className="mt-1 text-sm font-medium">{formatDate(task.reviewDueAt)}</p></div></div>
                <section><h3 className="text-sm font-semibold">Descrição</h3><p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">{task.description || 'Sem descrição.'}</p></section>
                <section className="space-y-2"><h3 className="text-sm font-semibold">Vínculos seguros</h3><div className="grid gap-2 sm:grid-cols-2">{[[task.workspaceId, 'Conta'], [task.linkedTicketId, 'Ticket'], [task.linkedLeadId, 'Lead'], [task.linkedIncidentId, 'Incidente'], [task.linkedReleaseId, 'Release']].filter(([id]) => id).map(([id, label]) => <div key={String(id)} className="rounded-md border px-3 py-2 text-sm"><span className="text-xs text-muted-foreground">{label}</span><p className="truncate font-medium">{labels.get(String(id)) ?? 'Registro vinculado indisponível'}</p></div>)}</div>{!task.workspaceId && !task.linkedTicketId && !task.linkedLeadId && !task.linkedIncidentId && !task.linkedReleaseId ? <p className="text-sm text-muted-foreground">Nenhum vínculo externo.</p> : null}</section>
                {task.blockedReason ? <Alert><AlertCircle className="h-4 w-4" /><AlertTitle>Bloqueio atual</AlertTitle><AlertDescription>{task.blockedReason}</AlertDescription></Alert> : null}
              </TabsContent>
              <TabsContent value="team" className="m-0 space-y-3 p-5">
                <article className="rounded-lg border p-4"><p className="text-xs text-muted-foreground">Responsável principal</p><p className="mt-1 font-medium">{labels.get(task.ownerUserId) ?? 'Funcionário vinculado'}</p><p className="mt-1 text-xs text-muted-foreground">Prazo: {formatDate(task.ownerDueAt)}</p></article>
                {task.participants.length === 0 ? <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">Sem executores ou observadores adicionais.</p> : task.participants.map((participant) => <article key={participant.id} className="rounded-lg border p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="font-medium">{participant.labelSnapshot}</p><p className="text-xs text-muted-foreground">{participant.role === 'assignee' ? 'Executor' : 'Observador'} · {participant.state}</p>{participant.requiredForCompletion ? <Badge className="mt-2" variant="outline">Obrigatório para concluir</Badge> : null}</div><div className="text-right text-xs"><p className="text-muted-foreground">Prazo individual</p><p>{formatDate(participant.personalDueAt)}</p></div></div>{participant.role === 'assignee' && canEdit && (participant.userId === currentUser.id || canAssign) && task.reviewState !== 'pending' && !['done', 'cancelled'].includes(task.status) ? <div className="mt-3 flex justify-end gap-2">{(['assigned', 'in_progress', 'completed'] as const).filter((state) => state !== participant.state).map((state) => <Button key={state} size="sm" variant="outline" onClick={() => { setParticipantIntent({ userId: participant.userId, label: participant.labelSnapshot, state }); setParticipantReason(''); }}>{state === 'assigned' ? 'Reatribuir' : state === 'in_progress' ? 'Marcar em execução' : 'Concluir parte'}</Button>)}</div> : null}</article>)}
              </TabsContent>
              <TabsContent value="review" className="m-0 space-y-3 p-5">
                {!task.reviewRequired ? <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">Esta tarefa não exige revisão.</p> : <><article className="rounded-lg border p-4"><p className="text-xs text-muted-foreground">Revisor</p><p className="mt-1 font-medium">{task.reviewerUserId ? labels.get(task.reviewerUserId) ?? 'Funcionário vinculado' : 'Não definido'}</p><p className="mt-1 text-xs text-muted-foreground">Estado: {REVIEW_LABEL[task.reviewState]} · prazo {formatDate(task.reviewDueAt)}</p></article>{[...task.reviewRounds].reverse().map((round) => <article key={round.id} className="rounded-lg border p-4"><div className="flex justify-between gap-2"><p className="font-medium">Rodada {round.roundNumber}</p><Badge variant="outline">{REVIEW_LABEL[round.state]}</Badge></div><p className="mt-2 text-xs text-muted-foreground">Enviada em {formatDate(round.submittedAt)} · versão {round.taskVersionReviewed}</p>{round.decisionReason ? <p className="mt-2 rounded-md bg-muted p-2 text-sm">{round.decisionReason}</p> : null}</article>)}</>}
              </TabsContent>
              <TabsContent value="comments" className="m-0 space-y-4 p-5">
                {canComment ? <div className="space-y-2"><Label htmlFor="platform-task-comment">Novo comentário interno</Label><Textarea id="platform-task-comment" value={comment} onChange={(event) => setComment(event.target.value)} rows={3} /><Button disabled={!comment.trim() || commentMutation.isPending} onClick={() => commentMutation.mutate()}><MessageSquareText className="mr-2 h-4 w-4" />Registrar no histórico</Button>{commentMutation.isError ? <p className="text-sm text-destructive">{platformTaskErrorMessage(commentMutation.error, 'Comentário não registrado.')}</p> : null}</div> : <p className="text-sm text-muted-foreground">Sua função não pode comentar nesta tarefa.</p>}
                <div className="space-y-2">{events.filter((event) => event.comment).map((event) => <article key={event.id} className="rounded-lg border p-3"><div className="flex justify-between gap-2 text-xs text-muted-foreground"><span>{event.actorLabelSnapshot}</span><span>{formatDate(event.occurredAt)}</span></div><p className="mt-2 whitespace-pre-wrap text-sm">{event.comment}</p></article>)}</div>
              </TabsContent>
              <TabsContent value="history" className="m-0 space-y-3 p-5">
                {eventsQuery.isPending ? <p role="status" className="text-sm text-muted-foreground">Carregando histórico…</p> : null}{eventsQuery.isError ? <Alert variant="destructive"><History className="h-4 w-4" /><AlertTitle>Histórico indisponível</AlertTitle><AlertDescription>A falha não foi convertida em histórico vazio.</AlertDescription></Alert> : null}{[...events].reverse().map((event) => <article key={event.id} className="rounded-lg border p-4"><div className="flex flex-wrap items-center justify-between gap-2"><p className="font-medium">{EVENT_LABEL[event.eventType] ?? event.eventType}</p><span className="text-xs text-muted-foreground">{formatDate(event.occurredAt)}</span></div><p className="mt-1 text-xs text-muted-foreground">{event.actorLabelSnapshot} · {event.actorRoleSnapshot} · {event.sessionType}</p><p className="mt-2 text-sm">{event.reason}</p>{event.changedPaths.length ? <div className="mt-2 flex flex-wrap gap-1">{event.changedPaths.map((path) => <Badge key={path} variant="outline">{path}</Badge>)}</div> : null}</article>)}
              </TabsContent>
            </ScrollArea>
          </Tabs>
        </> : null}
      </SheetContent>
    </Sheet>
    {task ? <><PlatformTaskEditDialog task={task} open={editOpen} onOpenChange={setEditOpen} onSaved={() => void refresh()} /><PlatformTaskAssignmentsDialog task={task} open={assignmentsOpen} onOpenChange={setAssignmentsOpen} currentUser={currentUser} onSaved={() => void refresh()} /></> : null}
    <Dialog open={Boolean(command)} onOpenChange={(next) => { if (!next) setCommand(null); }}><DialogContent><DialogHeader><DialogTitle>{command ? COMMAND_LABEL[command] : 'Executar ação'}</DialogTitle><DialogDescription>A ação usa a versão {task?.version ?? 'atual'} e falha com conflito se outra sessão já tiver alterado a tarefa.</DialogDescription></DialogHeader><div className="space-y-3"><div className="space-y-1.5"><Label htmlFor="platform-task-command-reason">Motivo auditável</Label><Input id="platform-task-command-reason" value={reason} onChange={(event) => setReason(event.target.value)} /></div><div className="space-y-1.5"><Label htmlFor="platform-task-command-comment">Comentário da decisão (opcional)</Label><Textarea id="platform-task-command-comment" value={decisionComment} onChange={(event) => setDecisionComment(event.target.value)} /></div>{commandMutation.isError ? <Alert variant="destructive"><AlertCircle className="h-4 w-4" /><AlertTitle>Ação não concluída</AlertTitle><AlertDescription>{platformTaskErrorMessage(commandMutation.error, 'Recarregue a tarefa e tente novamente.')}</AlertDescription></Alert> : null}</div><DialogFooter><Button variant="outline" onClick={() => setCommand(null)}>Cancelar</Button><Button disabled={reason.trim().length < 8 || commandMutation.isPending} onClick={() => commandMutation.mutate()}>{commandMutation.isPending ? 'Executando…' : 'Confirmar ação'}</Button></DialogFooter></DialogContent></Dialog>
    <Dialog open={Boolean(participantIntent)} onOpenChange={(next) => { if (!next) setParticipantIntent(null); }}><DialogContent><DialogHeader><DialogTitle>Atualizar progresso individual</DialogTitle><DialogDescription>{participantIntent?.label} · novo estado {participantIntent?.state}. A versão será revalidada.</DialogDescription></DialogHeader><div className="space-y-1.5"><Label htmlFor="platform-task-participant-reason">Motivo</Label><Input id="platform-task-participant-reason" value={participantReason} onChange={(event) => setParticipantReason(event.target.value)} /></div>{participantMutation.isError ? <Alert variant="destructive"><AlertCircle className="h-4 w-4" /><AlertTitle>Progresso não alterado</AlertTitle><AlertDescription>{platformTaskErrorMessage(participantMutation.error, 'Não foi possível atualizar o executor.')}</AlertDescription></Alert> : null}<DialogFooter><Button variant="outline" onClick={() => setParticipantIntent(null)}>Cancelar</Button><Button disabled={participantReason.trim().length < 8 || participantMutation.isPending} onClick={() => participantMutation.mutate()}><CheckCircle2 className="mr-2 h-4 w-4" />Confirmar</Button></DialogFooter></DialogContent></Dialog>
  </>;
}
