import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, CalendarClock, Link2, UsersRound } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { PlatformEntityCombobox } from '@/components/platform/PlatformEntityCombobox';
import { PlatformEntityMultiSelect } from '@/components/platform/PlatformEntityMultiSelect';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { resolvePlatformEntity } from '@/services/platform-search';
import {
  classifyPlatformTaskError,
  createPlatformTask,
  platformTaskErrorMessage,
  replacePlatformTaskAssignments,
  updatePlatformTask,
  type PlatformTask,
  type PlatformTaskAssignmentsInput,
  type PlatformTaskCreateInput,
  type PlatformTaskParticipantInput,
  type PlatformTaskPriority,
  type PlatformTaskUpdateInput,
} from '@/services/platform-tasks';
import type { AuthUser } from '@/types';

function toLocalInput(value: string | null | undefined): string {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  return new Date(parsed.getTime() - parsed.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

function toApiDate(value: string): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

function afterBoundary(value: string, boundary: string): boolean {
  if (!value || !boundary) return false;
  return new Date(value).getTime() > new Date(boundary).getTime();
}

interface PeopleDraft {
  ownerId: string;
  ownerDueAt: string;
  executorIds: string[];
  observerIds: string[];
  reviewerId: string;
  reviewDueAt: string;
  personalDueByUser: Record<string, string>;
  requiredByUser: Record<string, boolean>;
}

interface BaseDraft {
  title: string;
  description: string;
  priority: PlatformTaskPriority;
  dueAt: string;
  workspaceId: string | null;
  linkedTicketId: string | null;
  linkedIncidentId: string | null;
  linkedLeadId: string | null;
  linkedReleaseId: string | null;
  reason: string;
}

function blankBase(): BaseDraft {
  return {
    title: '', description: '', priority: 'P2', dueAt: '', workspaceId: null,
    linkedTicketId: null, linkedIncidentId: null, linkedLeadId: null,
    linkedReleaseId: null, reason: '',
  };
}

function blankPeople(currentUser: AuthUser): PeopleDraft {
  return {
    ownerId: currentUser.id,
    ownerDueAt: '',
    executorIds: [],
    observerIds: [],
    reviewerId: '',
    reviewDueAt: '',
    personalDueByUser: {},
    requiredByUser: {},
  };
}

function peopleForTask(task: PlatformTask): PeopleDraft {
  const active = task.participants.filter((item) => item.active);
  return {
    ownerId: task.ownerUserId,
    ownerDueAt: toLocalInput(task.ownerDueAt),
    executorIds: active.filter((item) => item.role === 'assignee').map((item) => item.userId),
    observerIds: active.filter((item) => item.role === 'observer').map((item) => item.userId),
    reviewerId: task.reviewerUserId ?? '',
    reviewDueAt: toLocalInput(task.reviewDueAt),
    personalDueByUser: Object.fromEntries(
      active
        .filter((item) => item.role === 'assignee')
        .map((item) => [item.userId, toLocalInput(item.personalDueAt)]),
    ),
    requiredByUser: Object.fromEntries(
      active
        .filter((item) => item.role === 'assignee')
        .map((item) => [item.userId, item.requiredForCompletion]),
    ),
  };
}

function participantsFrom(people: PeopleDraft): PlatformTaskParticipantInput[] {
  return [
    ...people.executorIds.map((userId) => ({
      userId,
      role: 'assignee' as const,
      personalDueAt: toApiDate(people.personalDueByUser[userId] ?? ''),
      requiredForCompletion: people.requiredByUser[userId] !== false,
    })),
    ...people.observerIds.map((userId) => ({
      userId,
      role: 'observer' as const,
      personalDueAt: null,
      requiredForCompletion: false,
    })),
  ];
}

function useStaffLabels(ids: string[]) {
  const stableIds = useMemo(() => Array.from(new Set(ids.filter(Boolean))).sort(), [ids]);
  const query = useQuery({
    queryKey: ['platform-task', 'staff-labels', stableIds],
    queryFn: () => resolvePlatformEntity({
      entityType: 'staff', purpose: 'platform_task_participant', ids: stableIds,
    }),
    enabled: stableIds.length > 0,
    retry: false,
  });
  return new Map((query.data?.items ?? []).map((item) => [item.id, item.label]));
}

function PeopleFields({
  value,
  onChange,
  dueAt,
  canAssign,
  currentUser,
}: {
  value: PeopleDraft;
  onChange: (value: PeopleDraft) => void;
  dueAt: string;
  canAssign: boolean;
  currentUser: AuthUser;
}) {
  const selectedIds = [value.ownerId, value.reviewerId, ...value.executorIds, ...value.observerIds];
  const labels = useStaffLabels(selectedIds);
  const excludedFromExecutors = [value.ownerId, value.reviewerId, ...value.observerIds].filter(Boolean);
  const excludedFromObservers = [value.ownerId, value.reviewerId, ...value.executorIds].filter(Boolean);
  const deadlineConflict = [value.ownerDueAt, value.reviewDueAt, ...Object.values(value.personalDueByUser)]
    .some((candidate) => afterBoundary(candidate, dueAt));

  return (
    <fieldset className="space-y-4 rounded-lg border p-4">
      <legend className="px-1 text-sm font-semibold">Equipe e prazos distintos</legend>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-1.5">
          <Label>Responsável principal</Label>
          {canAssign ? (
            <PlatformEntityCombobox
              entityType="staff"
              purpose="platform_task_participant"
              value={value.ownerId}
              onChange={(ownerId) => onChange({ ...value, ownerId: ownerId ?? currentUser.id })}
              label="Responsável principal"
              allowClear={false}
            />
          ) : <p className="rounded-md border bg-muted/30 px-3 py-2 text-sm">{currentUser.name}</p>}
        </div>
        <div className="space-y-1.5"><Label htmlFor="platform-task-owner-due">Prazo do responsável</Label><Input id="platform-task-owner-due" type="datetime-local" value={value.ownerDueAt} onChange={(event) => onChange({ ...value, ownerDueAt: event.target.value })} /></div>
      </div>
      {canAssign ? (
        <>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-1.5"><Label>Executores</Label><PlatformEntityMultiSelect entityType="staff" purpose="platform_task_participant" value={value.executorIds} onChange={(executorIds) => onChange({ ...value, executorIds })} excludedIds={excludedFromExecutors} label="Executores da tarefa" /></div>
            <div className="space-y-1.5"><Label>Observadores</Label><PlatformEntityMultiSelect entityType="staff" purpose="platform_task_participant" value={value.observerIds} onChange={(observerIds) => onChange({ ...value, observerIds })} excludedIds={excludedFromObservers} label="Observadores da tarefa" /></div>
          </div>
          {value.executorIds.length > 0 && (
            <div className="space-y-2 rounded-md bg-muted/30 p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Prazos individuais dos executores</p>
              {value.executorIds.map((userId, index) => (
                <div key={userId} className="grid items-center gap-2 sm:grid-cols-[minmax(0,1fr)_220px_auto]">
                  <span className="truncate text-sm">{labels.get(userId) ?? `Executor ${index + 1}`}</span>
                  <Input aria-label={`Prazo individual de ${labels.get(userId) ?? `executor ${index + 1}`}`} type="datetime-local" value={value.personalDueByUser[userId] ?? ''} onChange={(event) => onChange({ ...value, personalDueByUser: { ...value.personalDueByUser, [userId]: event.target.value } })} />
                  <label className="flex items-center gap-2 text-xs"><Checkbox checked={value.requiredByUser[userId] !== false} onCheckedChange={(checked) => onChange({ ...value, requiredByUser: { ...value.requiredByUser, [userId]: checked === true } })} />Obrigatório</label>
                </div>
              ))}
            </div>
          )}
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-1.5"><Label>Revisor</Label><PlatformEntityCombobox entityType="staff" purpose="platform_task_participant" value={value.reviewerId || null} onChange={(reviewerId) => onChange({ ...value, reviewerId: reviewerId ?? '', reviewDueAt: reviewerId ? value.reviewDueAt : '' })} workspaceId={null} label="Revisor da tarefa" placeholder="Sem revisão obrigatória" /></div>
            <div className="space-y-1.5"><Label htmlFor="platform-task-review-due">Prazo da revisão</Label><Input id="platform-task-review-due" type="datetime-local" disabled={!value.reviewerId} value={value.reviewDueAt} onChange={(event) => onChange({ ...value, reviewDueAt: event.target.value })} /></div>
          </div>
        </>
      ) : <p className="text-xs text-muted-foreground">Sua função pode criar uma tarefa própria, mas não distribuir trabalho ou nomear revisor.</p>}
      {deadlineConflict ? <Alert variant="destructive"><CalendarClock className="h-4 w-4" /><AlertTitle>Prazo incompatível</AlertTitle><AlertDescription>Nenhum prazo individual ou de revisão pode ultrapassar o prazo geral.</AlertDescription></Alert> : null}
    </fieldset>
  );
}

function BaseFields({ value, onChange }: { value: BaseDraft; onChange: (value: BaseDraft) => void }) {
  return (
    <div className="space-y-4">
      <div className="space-y-1.5"><Label htmlFor="platform-task-title">Título</Label><Input id="platform-task-title" value={value.title} maxLength={500} onChange={(event) => onChange({ ...value, title: event.target.value })} /></div>
      <div className="space-y-1.5"><Label htmlFor="platform-task-description">Descrição interna</Label><Textarea id="platform-task-description" value={value.description} maxLength={20_000} rows={4} onChange={(event) => onChange({ ...value, description: event.target.value })} /></div>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-1.5"><Label>Prioridade</Label><Select value={value.priority} onValueChange={(priority) => onChange({ ...value, priority: priority as PlatformTaskPriority })}><SelectTrigger aria-label="Prioridade da tarefa"><SelectValue /></SelectTrigger><SelectContent>{['P0', 'P1', 'P2', 'P3'].map((priority) => <SelectItem key={priority} value={priority}>{priority}</SelectItem>)}</SelectContent></Select></div>
        <div className="space-y-1.5"><Label htmlFor="platform-task-due">Prazo geral</Label><Input id="platform-task-due" type="datetime-local" value={value.dueAt} onChange={(event) => onChange({ ...value, dueAt: event.target.value })} /></div>
      </div>
      <fieldset className="space-y-4 rounded-lg border p-4"><legend className="px-1 text-sm font-semibold">Vínculos pesquisáveis</legend><p className="text-xs text-muted-foreground">Os vínculos guardam apenas referências seguras; conteúdo privado da conta não é copiado para a tarefa interna.</p>
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-1.5"><Label>Conta</Label><PlatformEntityCombobox entityType="accounts" purpose="platform_task_relation" value={value.workspaceId} onChange={(workspaceId) => onChange({ ...value, workspaceId, linkedTicketId: null, linkedLeadId: null })} label="Conta vinculada" placeholder="Sem conta vinculada" /></div>
          <div className="space-y-1.5"><Label>Ticket</Label><PlatformEntityCombobox entityType="tickets" purpose="platform_task_relation" workspaceId={value.workspaceId} value={value.linkedTicketId} onChange={(linkedTicketId) => onChange({ ...value, linkedTicketId })} label="Ticket vinculado" placeholder="Sem ticket vinculado" /></div>
          <div className="space-y-1.5"><Label>Lead</Label><PlatformEntityCombobox entityType="leads" purpose="platform_task_relation" workspaceId={value.workspaceId} value={value.linkedLeadId} onChange={(linkedLeadId) => onChange({ ...value, linkedLeadId })} label="Lead vinculado" placeholder="Sem lead vinculado" /></div>
          <div className="space-y-1.5"><Label>Incidente</Label><PlatformEntityCombobox entityType="incidents" purpose="platform_task_relation" value={value.linkedIncidentId} onChange={(linkedIncidentId) => onChange({ ...value, linkedIncidentId })} label="Incidente vinculado" placeholder="Sem incidente vinculado" /></div>
          <div className="space-y-1.5"><Label>Release</Label><PlatformEntityCombobox entityType="releases" purpose="platform_task_relation" value={value.linkedReleaseId} onChange={(linkedReleaseId) => onChange({ ...value, linkedReleaseId })} label="Release vinculada" placeholder="Sem release vinculada" /></div>
        </div>
      </fieldset>
      <div className="space-y-1.5"><Label htmlFor="platform-task-reason">Motivo auditável</Label><Input id="platform-task-reason" value={value.reason} minLength={8} maxLength={500} onChange={(event) => onChange({ ...value, reason: event.target.value })} placeholder="Explique por que esta tarefa ou alteração é necessária" /></div>
    </div>
  );
}

function mutationFeedback(error: unknown) {
  const kind = classifyPlatformTaskError(error);
  const title = kind === 'conflict' ? 'Conflito de versão' : kind === 'forbidden' ? 'Operação não autorizada' : 'Tarefa não salva';
  return <Alert variant="destructive"><AlertCircle className="h-4 w-4" /><AlertTitle>{title}</AlertTitle><AlertDescription>{platformTaskErrorMessage(error, 'Não foi possível concluir a operação.')}</AlertDescription></Alert>;
}

export function PlatformTaskCreateDialog({ open, onOpenChange, currentUser, canAssign, onSaved }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  currentUser: AuthUser;
  canAssign: boolean;
  onSaved: (task: PlatformTask) => void;
}) {
  const queryClient = useQueryClient();
  const [base, setBase] = useState<BaseDraft>(blankBase);
  const [people, setPeople] = useState<PeopleDraft>(() => blankPeople(currentUser));
  useEffect(() => { if (open) { setBase(blankBase()); setPeople(blankPeople(currentUser)); } }, [currentUser, open]);
  const invalidDeadline = [people.ownerDueAt, people.reviewDueAt, ...Object.values(people.personalDueByUser)].some((value) => afterBoundary(value, base.dueAt));
  const mutation = useMutation({
    mutationFn: () => {
      const input: PlatformTaskCreateInput = {
        title: base.title.trim(), description: base.description.trim() || null,
        priority: base.priority, dueAt: toApiDate(base.dueAt),
        ownerUserId: people.ownerId, ownerDueAt: toApiDate(people.ownerDueAt),
        reviewerUserId: people.reviewerId || null, reviewDueAt: toApiDate(people.reviewDueAt),
        reviewRequired: Boolean(people.reviewerId), participants: participantsFrom(people),
        workspaceId: base.workspaceId, linkedTicketId: base.linkedTicketId,
        linkedIncidentId: base.linkedIncidentId, linkedLeadId: base.linkedLeadId,
        linkedReleaseId: base.linkedReleaseId, reason: base.reason.trim(),
      };
      return createPlatformTask(input);
    },
    onSuccess: async (task) => {
      await queryClient.invalidateQueries({ queryKey: ['blackbox', 'platform-tasks'] });
      onOpenChange(false); onSaved(task);
    },
  });
  const valid = base.title.trim().length > 0 && base.reason.trim().length >= 8 && people.ownerId && !invalidDeadline;
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="max-h-[92vh] max-w-4xl overflow-y-auto"><DialogHeader><DialogTitle className="flex items-center gap-2"><UsersRound className="h-5 w-5" />Nova tarefa interna</DialogTitle><DialogDescription>Tarefa autoritativa da equipe do SaaS. Ela não é um work item projetado nem uma cópia de tarefa do cliente.</DialogDescription></DialogHeader><BaseFields value={base} onChange={setBase} /><PeopleFields value={people} onChange={setPeople} dueAt={base.dueAt} canAssign={canAssign} currentUser={currentUser} />{mutation.isError ? mutationFeedback(mutation.error) : null}<DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>Cancelar</Button><Button disabled={!valid || mutation.isPending} onClick={() => mutation.mutate()}>{mutation.isPending ? 'Criando…' : 'Criar tarefa'}</Button></DialogFooter></DialogContent></Dialog>;
}

export function PlatformTaskEditDialog({ task, open, onOpenChange, onSaved }: {
  task: PlatformTask;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: (task: PlatformTask) => void;
}) {
  const queryClient = useQueryClient();
  const [base, setBase] = useState<BaseDraft>(blankBase);
  useEffect(() => { if (open) setBase({ title: task.title, description: task.description ?? '', priority: task.priority, dueAt: toLocalInput(task.dueAt), workspaceId: task.workspaceId, linkedTicketId: task.linkedTicketId, linkedIncidentId: task.linkedIncidentId, linkedLeadId: task.linkedLeadId, linkedReleaseId: task.linkedReleaseId, reason: '' }); }, [open, task]);
  const mutation = useMutation({
    mutationFn: () => updatePlatformTask(task.id, {
      expectedVersion: task.version, title: base.title.trim(), description: base.description.trim() || null,
      priority: base.priority, dueAt: toApiDate(base.dueAt), workspaceId: base.workspaceId,
      linkedTicketId: base.linkedTicketId, linkedIncidentId: base.linkedIncidentId,
      linkedLeadId: base.linkedLeadId, linkedReleaseId: base.linkedReleaseId,
      reason: base.reason.trim(),
    } satisfies PlatformTaskUpdateInput),
    onSuccess: async (updated) => { await queryClient.invalidateQueries({ queryKey: ['blackbox', 'platform-tasks'] }); onOpenChange(false); onSaved(updated); },
  });
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="max-h-[92vh] max-w-4xl overflow-y-auto"><DialogHeader><DialogTitle className="flex items-center gap-2"><Link2 className="h-5 w-5" />Editar tarefa interna</DialogTitle><DialogDescription>A versão {task.version} será comparada no servidor antes da alteração.</DialogDescription></DialogHeader><BaseFields value={base} onChange={setBase} />{mutation.isError ? mutationFeedback(mutation.error) : null}<DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>Cancelar</Button><Button disabled={!base.title.trim() || base.reason.trim().length < 8 || mutation.isPending} onClick={() => mutation.mutate()}>Salvar versão</Button></DialogFooter></DialogContent></Dialog>;
}

export function PlatformTaskAssignmentsDialog({ task, open, onOpenChange, currentUser, onSaved }: {
  task: PlatformTask;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  currentUser: AuthUser;
  onSaved: (task: PlatformTask) => void;
}) {
  const queryClient = useQueryClient();
  const [people, setPeople] = useState<PeopleDraft>(() => peopleForTask(task));
  const [reason, setReason] = useState('');
  useEffect(() => { if (open) { setPeople(peopleForTask(task)); setReason(''); } }, [open, task]);
  const invalidDeadline = [people.ownerDueAt, people.reviewDueAt, ...Object.values(people.personalDueByUser)].some((value) => afterBoundary(value, toLocalInput(task.dueAt)));
  const mutation = useMutation({
    mutationFn: () => replacePlatformTaskAssignments(task.id, {
      expectedVersion: task.version, ownerUserId: people.ownerId,
      ownerDueAt: toApiDate(people.ownerDueAt), reviewerUserId: people.reviewerId || null,
      reviewDueAt: toApiDate(people.reviewDueAt), participants: participantsFrom(people),
      reason: reason.trim(),
    } satisfies PlatformTaskAssignmentsInput),
    onSuccess: async (updated) => { await queryClient.invalidateQueries({ queryKey: ['blackbox', 'platform-tasks'] }); onOpenChange(false); onSaved(updated); },
  });
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="max-h-[92vh] max-w-4xl overflow-y-auto"><DialogHeader><DialogTitle>Equipe, revisor e prazos</DialogTitle><DialogDescription>Substituição lógica e auditada dos participantes ativos, protegida pela versão {task.version}.</DialogDescription></DialogHeader><PeopleFields value={people} onChange={setPeople} dueAt={toLocalInput(task.dueAt)} canAssign currentUser={currentUser} /><div className="space-y-1.5"><Label htmlFor="platform-task-assignment-reason">Motivo auditável</Label><Input id="platform-task-assignment-reason" value={reason} onChange={(event) => setReason(event.target.value)} /></div>{mutation.isError ? mutationFeedback(mutation.error) : null}<DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>Cancelar</Button><Button disabled={!people.ownerId || reason.trim().length < 8 || invalidDeadline || mutation.isPending} onClick={() => mutation.mutate()}>Atualizar equipe</Button></DialogFooter></DialogContent></Dialog>;
}
