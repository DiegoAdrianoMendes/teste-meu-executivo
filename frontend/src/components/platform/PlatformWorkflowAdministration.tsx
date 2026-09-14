import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ClipboardCheck, Plus, UserRoundCog } from 'lucide-react';
import { useState } from 'react';

import { useBlackboxManifest } from '@/blackbox/session-manifest';
import { PlatformEntityCombobox } from '@/components/platform/PlatformEntityCombobox';
import { PlatformTeamCombobox } from '@/components/platform/PlatformTeamCombobox';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { canPlatform } from '@/lib/platform-access';
import {
  fetchOperationalAssignmentsPage,
  fetchPlatformReviewsPage,
  saveOperationalAssignment,
  savePlatformReview,
  type PlatformOperationalAssignment,
  type PlatformReviewRequest,
} from '@/services/platform-organization';
import type { PlatformSearchEntityType } from '@/services/platform-search';

const ASSIGNMENT_MODULE: Record<PlatformOperationalAssignment['sourceType'], string> = {
  billing: 'billing', lead: 'sales', ticket: 'support', publication: 'announcements', configuration: 'workspace_configuration',
};
const SOURCE_LABEL: Record<PlatformOperationalAssignment['sourceType'], string> = {
  billing: 'Cobrança', lead: 'Lead', ticket: 'Ticket', publication: 'Publicação', configuration: 'Configuração',
};
const SOURCE_LOOKUP: Partial<Record<PlatformOperationalAssignment['sourceType'], PlatformSearchEntityType>> = {
  billing: 'subscriptions', lead: 'leads', ticket: 'tickets',
};
const ASSIGNMENT_STATES: PlatformOperationalAssignment['state'][] = ['open', 'in_progress', 'waiting_external', 'waiting_customer', 'blocked', 'done', 'cancelled'];
const REVIEW_STATES: PlatformReviewRequest['state'][] = ['draft', 'in_review', 'changes_requested', 'approved', 'published', 'publish_failed', 'cancelled'];

function toLocal(value: string | null) {
  if (!value) return '';
  const parsed = new Date(value);
  return new Date(parsed.getTime() - parsed.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

export function PlatformWorkflowAdministration() {
  const { effectiveUser } = useBlackboxManifest();
  const queryClient = useQueryClient();
  const [assignment, setAssignment] = useState<PlatformOperationalAssignment | 'new' | null>(null);
  const [sourceType, setSourceType] = useState<PlatformOperationalAssignment['sourceType']>('lead');
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [ownerUserId, setOwnerUserId] = useState<string | null>(null);
  const [teamId, setTeamId] = useState<string | null>(null);
  const [nextAction, setNextAction] = useState('');
  const [nextActionAt, setNextActionAt] = useState('');
  const [dueAt, setDueAt] = useState('');
  const [assignmentState, setAssignmentState] = useState<PlatformOperationalAssignment['state']>('open');
  const [review, setReview] = useState<PlatformReviewRequest | null>(null);
  const [reviewState, setReviewState] = useState<PlatformReviewRequest['state']>('draft');
  const [reviewerUserId, setReviewerUserId] = useState<string | null>(null);
  const [reviewDueAt, setReviewDueAt] = useState('');
  const [reason, setReason] = useState('');
  const [assignmentPage, setAssignmentPage] = useState(0);
  const [reviewPage, setReviewPage] = useState(0);
  const pageSize = 10;

  const assignments = useQuery({ queryKey: ['platform', 'organization', 'operational-assignments', assignmentPage], queryFn: () => fetchOperationalAssignmentsPage({ limit: pageSize, offset: assignmentPage * pageSize }) });
  const reviews = useQuery({ queryKey: ['platform', 'organization', 'reviews', reviewPage], queryFn: () => fetchPlatformReviewsPage({ limit: pageSize, offset: reviewPage * pageSize }) });
  const canCreateAny = (['billing', 'lead', 'ticket'] as const).some((type) => canPlatform(effectiveUser, ASSIGNMENT_MODULE[type], 'manage'));
  const canManageAssignment = (type: PlatformOperationalAssignment['sourceType']) => canPlatform(effectiveUser, ASSIGNMENT_MODULE[type], 'manage');
  const canManageReview = (type: PlatformReviewRequest['sourceType']) => canPlatform(effectiveUser, type === 'publication' ? 'announcements' : 'workspace_configuration', 'manage');
  const reset = () => { setAssignment(null); setReview(null); setReason(''); };
  const refresh = async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ['platform', 'organization', 'operational-assignments'] }), queryClient.invalidateQueries({ queryKey: ['platform', 'organization', 'reviews'] }), queryClient.invalidateQueries({ queryKey: ['blackbox', 'work-items'] })]); };
  const beginNew = () => {
    const allowed = (['lead', 'ticket', 'billing'] as const).find((type) => canManageAssignment(type)) ?? 'lead';
    setReview(null); setAssignment('new'); setSourceType(allowed); setSourceId(null); setOwnerUserId(null); setTeamId(null); setNextAction(''); setNextActionAt(''); setDueAt(''); setAssignmentState('open'); setReason('');
  };
  const beginAssignment = (row: PlatformOperationalAssignment) => {
    setReview(null); setAssignment(row); setSourceType(row.sourceType); setSourceId(row.sourceId); setOwnerUserId(row.ownerUserId); setTeamId(row.teamId);
    setNextAction(row.nextAction ?? ''); setNextActionAt(toLocal(row.nextActionAt)); setDueAt(toLocal(row.dueAt)); setAssignmentState(row.state); setReason('');
  };
  const beginReview = (row: PlatformReviewRequest) => {
    setAssignment(null); setReview(row); setReviewState(row.state); setReviewerUserId(row.reviewerUserId); setReviewDueAt(toLocal(row.reviewDueAt)); setReason('');
  };
  const assignmentMutation = useMutation({
    mutationFn: () => saveOperationalAssignment({ sourceType, sourceId: sourceId!, ownerUserId, teamId, nextAction: nextAction.trim() || null, nextActionAt: nextActionAt ? new Date(nextActionAt).toISOString() : null, dueAt: dueAt ? new Date(dueAt).toISOString() : null, state: assignmentState, expectedVersion: assignment === 'new' ? null : assignment?.version ?? null, reason: reason.trim() }),
    onSuccess: async () => { reset(); await refresh(); },
  });
  const reviewMutation = useMutation({
    mutationFn: () => savePlatformReview({ sourceType: review!.sourceType, sourceId: review!.sourceId, state: reviewState, reviewerUserId, reviewDueAt: reviewDueAt ? new Date(reviewDueAt).toISOString() : null, expectedVersion: review!.version, reason: reason.trim() }),
    onSuccess: async () => { reset(); await refresh(); },
  });
  const selectableTypes = (['lead', 'ticket', 'billing'] as const).filter((type) => canManageAssignment(type));

  return <Card>
    <CardHeader><div className="flex flex-wrap items-start justify-between gap-3"><div><CardTitle>Administração das filas</CardTitle><CardDescription>Responsáveis e revisões persistidos na fonte; a projeção da fila é reconciliada depois da mutação.</CardDescription></div>{canCreateAny ? <Button size="sm" onClick={beginNew}><Plus className="mr-1 h-4 w-4" />Atribuição</Button> : null}</div></CardHeader>
    <CardContent className="space-y-5">
      {(assignments.isError || reviews.isError) ? <Alert variant="destructive"><AlertTitle>Administração parcialmente indisponível</AlertTitle><AlertDescription>A fila principal continua visível, mas responsáveis ou revisões não puderam ser lidos.</AlertDescription></Alert> : null}
      <div className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold"><UserRoundCog className="h-4 w-4" />Responsabilidades operacionais</h3>
        {assignments.isPending ? <p role="status" className="text-sm text-muted-foreground">Carregando atribuições…</p> : assignments.data?.items.length === 0 ? <p className="text-sm text-muted-foreground">Nenhuma atribuição operacional configurada.</p> : <div className="grid gap-2 md:grid-cols-2">{assignments.data?.items.map((row) => <button key={row.id} type="button" disabled={!canManageAssignment(row.sourceType)} className="flex items-center justify-between rounded-lg border p-3 text-left disabled:cursor-default" onClick={() => beginAssignment(row)}><span><span className="block text-sm font-medium">{SOURCE_LABEL[row.sourceType]}</span><span className="text-xs text-muted-foreground">{row.nextAction ?? 'Sem próxima ação'} · {row.dueAt ? new Date(row.dueAt).toLocaleString('pt-BR') : 'sem prazo'}</span></span><Badge variant={row.state === 'blocked' ? 'destructive' : 'secondary'}>{row.state}</Badge></button>)}</div>}
        {assignments.data && assignments.data.total > pageSize ? <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground"><span>{assignments.data.offset + 1}–{Math.min(assignments.data.offset + assignments.data.items.length, assignments.data.total)} de {assignments.data.total} atribuições</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={assignmentPage === 0 || assignments.isFetching} onClick={() => setAssignmentPage((page) => Math.max(0, page - 1))}>Anterior</Button><Button size="sm" variant="outline" disabled={!assignments.data.hasMore || assignments.isFetching} onClick={() => setAssignmentPage((page) => page + 1)}>Próxima</Button></div></div> : null}
      </div>
      <div className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold"><ClipboardCheck className="h-4 w-4" />Revisões editoriais e de configuração</h3>
        {reviews.isPending ? <p role="status" className="text-sm text-muted-foreground">Carregando revisões…</p> : reviews.data?.items.length === 0 ? <p className="text-sm text-muted-foreground">Nenhuma revisão configurada.</p> : <div className="grid gap-2 md:grid-cols-2">{reviews.data?.items.map((row) => <button key={row.id} type="button" disabled={!canManageReview(row.sourceType)} className="flex items-center justify-between rounded-lg border p-3 text-left disabled:cursor-default" onClick={() => beginReview(row)}><span><span className="block text-sm font-medium">{row.sourceType === 'publication' ? 'Publicação vinculada' : 'Configuração vinculada'}</span><span className="text-xs text-muted-foreground">{row.reviewDueAt ? `revisão até ${new Date(row.reviewDueAt).toLocaleString('pt-BR')}` : 'sem prazo de revisão'}</span></span><Badge variant="outline">{row.state}</Badge></button>)}</div>}
        {reviews.data && reviews.data.total > pageSize ? <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground"><span>{reviews.data.offset + 1}–{Math.min(reviews.data.offset + reviews.data.items.length, reviews.data.total)} de {reviews.data.total} revisões</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={reviewPage === 0 || reviews.isFetching} onClick={() => setReviewPage((page) => Math.max(0, page - 1))}>Anterior</Button><Button size="sm" variant="outline" disabled={!reviews.data.hasMore || reviews.isFetching} onClick={() => setReviewPage((page) => page + 1)}>Próxima</Button></div></div> : null}
      </div>
      {assignment ? <div role="dialog" aria-modal="true" aria-labelledby="assignment-title" className="space-y-3 rounded-lg border border-primary/30 p-4"><h3 id="assignment-title" className="font-semibold">{assignment === 'new' ? 'Nova atribuição' : `Editar ${SOURCE_LABEL[assignment.sourceType]}`}</h3>{assignment === 'new' ? <><div><Label>Fonte</Label><Select value={sourceType} onValueChange={(value) => { setSourceType(value as PlatformOperationalAssignment['sourceType']); setSourceId(null); }}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{selectableTypes.map((type) => <SelectItem key={type} value={type}>{SOURCE_LABEL[type]}</SelectItem>)}</SelectContent></Select></div><div><Label>Registro</Label><PlatformEntityCombobox className="mt-1" entityType={SOURCE_LOOKUP[sourceType]!} purpose="operational_assignment" value={sourceId} onChange={(value) => setSourceId(value)} label={`Selecionar ${SOURCE_LABEL[sourceType]}`} /></div></> : null}<div className="grid gap-3 md:grid-cols-2"><div><Label>Responsável</Label><PlatformEntityCombobox className="mt-1" entityType="staff" purpose="operational_owner" value={ownerUserId} onChange={(value) => { setOwnerUserId(value); if (value) setTeamId(null); }} label="Selecionar responsável" /></div><div><Label>Equipe</Label><PlatformTeamCombobox className="mt-1" value={teamId} onChange={(value) => { setTeamId(value); if (value) setOwnerUserId(null); }} label="Selecionar equipe da atribuição" placeholder="Pesquisar equipe" /></div><div><Label>Estado</Label><Select value={assignmentState} onValueChange={(value) => setAssignmentState(value as PlatformOperationalAssignment['state'])}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{ASSIGNMENT_STATES.map((state) => <SelectItem key={state} value={state}>{state}</SelectItem>)}</SelectContent></Select></div><div><Label>Próxima ação</Label><Input value={nextAction} onChange={(event) => setNextAction(event.target.value)} /></div><div><Label>Próxima ação em</Label><Input type="datetime-local" value={nextActionAt} onChange={(event) => setNextActionAt(event.target.value)} /></div><div><Label>Prazo</Label><Input type="datetime-local" value={dueAt} onChange={(event) => setDueAt(event.target.value)} /></div></div><div><Label>Motivo</Label><Input value={reason} onChange={(event) => setReason(event.target.value)} /></div>{assignmentMutation.isError ? <p role="alert" className="text-sm text-destructive">A fonte mudou, o vínculo é inválido ou a operação foi bloqueada.</p> : null}<div className="flex justify-end gap-2"><Button variant="outline" onClick={reset}>Cancelar</Button><Button disabled={!sourceId || (!ownerUserId && !teamId) || reason.trim().length < 8 || assignmentMutation.isPending} onClick={() => assignmentMutation.mutate()}>Salvar atribuição</Button></div></div> : null}
      {review ? <div role="dialog" aria-modal="true" aria-labelledby="review-title" className="space-y-3 rounded-lg border border-primary/30 p-4"><h3 id="review-title" className="font-semibold">Atualizar revisão de {review.sourceType === 'publication' ? 'publicação' : 'configuração'}</h3><div className="grid gap-3 md:grid-cols-3"><div><Label>Estado</Label><Select value={reviewState} onValueChange={(value) => setReviewState(value as PlatformReviewRequest['state'])}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{REVIEW_STATES.map((state) => <SelectItem key={state} value={state}>{state}</SelectItem>)}</SelectContent></Select></div><div><Label>Revisor</Label><PlatformEntityCombobox className="mt-1" entityType="staff" purpose="reviewer" value={reviewerUserId} onChange={(value) => setReviewerUserId(value)} label="Selecionar revisor" /></div><div><Label>Prazo</Label><Input type="datetime-local" value={reviewDueAt} onChange={(event) => setReviewDueAt(event.target.value)} /></div></div><div><Label>Motivo</Label><Input value={reason} onChange={(event) => setReason(event.target.value)} /></div>{reviewMutation.isError ? <p role="alert" className="text-sm text-destructive">A revisão mudou ou não pôde ser atualizada.</p> : null}<div className="flex justify-end gap-2"><Button variant="outline" onClick={reset}>Cancelar</Button><Button disabled={reason.trim().length < 8 || reviewMutation.isPending} onClick={() => reviewMutation.mutate()}>Salvar revisão</Button></div></div> : null}
    </CardContent>
  </Card>;
}
