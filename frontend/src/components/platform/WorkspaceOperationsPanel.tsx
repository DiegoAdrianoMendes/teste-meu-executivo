import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarClock, Network, Plus } from 'lucide-react';
import { useEffect, useState } from 'react';

import { PlatformEntityCombobox } from '@/components/platform/PlatformEntityCombobox';
import { PlatformTeamCombobox } from '@/components/platform/PlatformTeamCombobox';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  fetchWorkspaceAssignmentsPage,
  fetchWorkspaceMilestonesPage,
  saveWorkspaceAssignment,
  saveWorkspaceMilestone,
  type PlatformOnboardingMilestone,
  type PlatformWorkspaceAssignment,
} from '@/services/platform-organization';

const PAGE_SIZE = 5;

const ASSIGNMENT_LABELS: Record<PlatformWorkspaceAssignment['assignmentType'], string> = {
  onboarding: 'Onboarding', customer_success: 'Customer Success', billing: 'Cobrança', support: 'Suporte',
};
const MILESTONE_LABELS: Record<PlatformOnboardingMilestone['status'], string> = {
  pending: 'Pendente', in_progress: 'Em andamento', blocked: 'Bloqueado', completed: 'Concluído', cancelled: 'Cancelado',
};

function localDateTime(value: string | null) {
  if (!value) return '';
  const parsed = new Date(value);
  const offset = parsed.getTimezoneOffset() * 60_000;
  return new Date(parsed.getTime() - offset).toISOString().slice(0, 16);
}

function slugify(value: string) {
  return value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 80);
}

export function WorkspaceOperationsPanel({
  workspaceId,
  canManageAssignments,
  canManageMilestones,
}: {
  workspaceId: string;
  canManageAssignments: boolean;
  canManageMilestones: boolean;
}) {
  const queryClient = useQueryClient();
  const [assignment, setAssignment] = useState<PlatformWorkspaceAssignment | 'new' | null>(null);
  const [assignmentType, setAssignmentType] = useState<PlatformWorkspaceAssignment['assignmentType']>('onboarding');
  const [assignedUserId, setAssignedUserId] = useState<string | null>(null);
  const [assignedTeamId, setAssignedTeamId] = useState<string | null>(null);
  const [milestone, setMilestone] = useState<PlatformOnboardingMilestone | 'new' | null>(null);
  const [milestoneLabel, setMilestoneLabel] = useState('');
  const [milestoneStatus, setMilestoneStatus] = useState<PlatformOnboardingMilestone['status']>('pending');
  const [milestoneOwnerId, setMilestoneOwnerId] = useState<string | null>(null);
  const [milestoneDueAt, setMilestoneDueAt] = useState('');
  const [reason, setReason] = useState('');
  const [assignmentPage, setAssignmentPage] = useState(0);
  const [milestonePage, setMilestonePage] = useState(0);

  const assignments = useQuery({
    queryKey: ['platform', 'organization', workspaceId, 'assignments', assignmentPage],
    queryFn: () => fetchWorkspaceAssignmentsPage(workspaceId, { limit: PAGE_SIZE, offset: assignmentPage * PAGE_SIZE }),
  });
  const milestones = useQuery({
    queryKey: ['platform', 'organization', workspaceId, 'milestones', milestonePage],
    queryFn: () => fetchWorkspaceMilestonesPage(workspaceId, { limit: PAGE_SIZE, offset: milestonePage * PAGE_SIZE }),
  });
  useEffect(() => {
    setAssignmentPage(0);
    setMilestonePage(0);
  }, [workspaceId]);
  const refresh = async () => queryClient.invalidateQueries({ queryKey: ['platform', 'organization', workspaceId] });
  const reset = () => { setAssignment(null); setMilestone(null); setReason(''); };
  const assignmentMutation = useMutation({
    mutationFn: () => saveWorkspaceAssignment(workspaceId, {
      assignmentType,
      assignedUserId,
      assignedTeamId,
      expectedVersion: assignment === 'new' ? null : assignment?.version ?? null,
      reason: reason.trim(),
    }),
    onSuccess: async () => { reset(); setAssignmentPage(0); await refresh(); },
  });
  const milestoneMutation = useMutation({
    mutationFn: () => saveWorkspaceMilestone(workspaceId, {
      key: milestone === 'new' ? slugify(milestoneLabel) : milestone?.key ?? '',
      label: milestoneLabel.trim(),
      status: milestoneStatus,
      ownerUserId: milestoneOwnerId,
      dueAt: milestoneDueAt ? new Date(milestoneDueAt).toISOString() : null,
      expectedVersion: milestone === 'new' ? null : milestone?.version ?? null,
      reason: reason.trim(),
    }),
    onSuccess: async () => { reset(); setMilestonePage(0); await refresh(); },
  });
  const editAssignment = (row: PlatformWorkspaceAssignment) => {
    setMilestone(null); setAssignment(row); setAssignmentType(row.assignmentType);
    setAssignedUserId(row.assignedUserId); setAssignedTeamId(row.assignedTeamId); setReason('');
  };
  const editMilestone = (row: PlatformOnboardingMilestone) => {
    setAssignment(null); setMilestone(row); setMilestoneLabel(row.label); setMilestoneStatus(row.status);
    setMilestoneOwnerId(row.ownerUserId); setMilestoneDueAt(localDateTime(row.dueAt)); setReason('');
  };

  return <section className="space-y-4">
    <div><h3 className="text-sm font-semibold">Responsabilidades e onboarding</h3><p className="text-xs text-muted-foreground">Owners e prazos reais desta conta. Equipe não concede permissão.</p></div>
    {(assignments.isError || milestones.isError) ? <Alert variant="destructive"><AlertTitle>Operação da conta indisponível</AlertTitle><AlertDescription>A falha de leitura não foi convertida em lista vazia.</AlertDescription></Alert> : null}
    <div className="space-y-2">
      <div className="flex items-center justify-between"><p className="flex items-center gap-2 text-xs font-medium"><Network className="h-3.5 w-3.5" />Responsáveis</p>{canManageAssignments ? <Button type="button" size="sm" variant="ghost" onClick={() => { setMilestone(null); setAssignment('new'); setAssignmentType('onboarding'); setAssignedUserId(null); setAssignedTeamId(null); setReason(''); }}><Plus className="mr-1 h-3.5 w-3.5" />Definir</Button> : null}</div>
      {assignments.isPending ? <p role="status" className="text-xs text-muted-foreground">Carregando responsáveis…</p> : assignments.data?.items.length === 0 ? <p className="text-xs text-muted-foreground">Nenhuma responsabilidade definida.</p> : assignments.data?.items.map((row) => <button type="button" key={row.id} className="flex w-full items-center justify-between rounded-md border p-2 text-left text-xs" onClick={() => canManageAssignments && editAssignment(row)}><span>{ASSIGNMENT_LABELS[row.assignmentType]}</span><Badge variant="outline">{row.assignedUserId ? 'Pessoa' : 'Equipe'} · v{row.version}</Badge></button>)}
      {assignments.data ? <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground"><span>{assignments.data.total === 0 ? '0 responsáveis' : `${assignments.data.offset + 1}–${assignments.data.offset + assignments.data.items.length} de ${assignments.data.total}`}</span><div className="flex gap-1"><Button type="button" size="sm" variant="outline" disabled={assignmentPage === 0 || assignments.isFetching} onClick={() => setAssignmentPage((page) => Math.max(0, page - 1))}>Anterior</Button><Button type="button" size="sm" variant="outline" disabled={!assignments.data.hasMore || assignments.isFetching} onClick={() => setAssignmentPage((page) => page + 1)}>Próxima</Button></div></div> : null}
    </div>
    <div className="space-y-2">
      <div className="flex items-center justify-between"><p className="flex items-center gap-2 text-xs font-medium"><CalendarClock className="h-3.5 w-3.5" />Marcos</p>{canManageMilestones ? <Button type="button" size="sm" variant="ghost" onClick={() => { setAssignment(null); setMilestone('new'); setMilestoneLabel(''); setMilestoneStatus('pending'); setMilestoneOwnerId(null); setMilestoneDueAt(''); setReason(''); }}><Plus className="mr-1 h-3.5 w-3.5" />Marco</Button> : null}</div>
      {milestones.isPending ? <p role="status" className="text-xs text-muted-foreground">Carregando marcos…</p> : milestones.data?.items.length === 0 ? <p className="text-xs text-muted-foreground">Nenhum marco configurado.</p> : milestones.data?.items.map((row) => <button type="button" key={row.id} className="flex w-full items-center justify-between rounded-md border p-2 text-left text-xs" onClick={() => canManageMilestones && editMilestone(row)}><span><span className="block font-medium">{row.label}</span><span className="text-muted-foreground">{row.dueAt ? new Date(row.dueAt).toLocaleString('pt-BR') : 'Sem prazo'}</span></span><Badge variant={row.status === 'blocked' ? 'destructive' : 'secondary'}>{MILESTONE_LABELS[row.status]}</Badge></button>)}
      {milestones.data ? <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground"><span>{milestones.data.total === 0 ? '0 marcos' : `${milestones.data.offset + 1}–${milestones.data.offset + milestones.data.items.length} de ${milestones.data.total}`}</span><div className="flex gap-1"><Button type="button" size="sm" variant="outline" disabled={milestonePage === 0 || milestones.isFetching} onClick={() => setMilestonePage((page) => Math.max(0, page - 1))}>Anterior</Button><Button type="button" size="sm" variant="outline" disabled={!milestones.data.hasMore || milestones.isFetching} onClick={() => setMilestonePage((page) => page + 1)}>Próxima</Button></div></div> : null}
    </div>
    {assignment ? <div className="space-y-3 rounded-lg border p-3">
      <Label>Tipo de responsabilidade</Label><Select value={assignmentType} onValueChange={(value) => setAssignmentType(value as PlatformWorkspaceAssignment['assignmentType'])}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{Object.entries(ASSIGNMENT_LABELS).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select>
      <div><Label>Pessoa responsável</Label><PlatformEntityCombobox className="mt-1" entityType="staff" purpose="operational_owner" value={assignedUserId} onChange={(value) => { setAssignedUserId(value); if (value) setAssignedTeamId(null); }} label="Selecionar funcionário" placeholder="Buscar funcionário" /></div>
      <div><Label>Ou equipe</Label><PlatformTeamCombobox className="mt-1" value={assignedTeamId} onChange={(value) => { setAssignedTeamId(value); if (value) setAssignedUserId(null); }} label="Selecionar equipe responsável" placeholder="Pesquisar equipe" /></div>
      <div><Label>Motivo</Label><Input value={reason} onChange={(event) => setReason(event.target.value)} /></div>
      {assignmentMutation.isError ? <p role="alert" className="text-xs text-destructive">A responsabilidade mudou ou não pôde ser salva.</p> : null}<div className="flex justify-end gap-2"><Button type="button" size="sm" variant="outline" onClick={reset}>Cancelar</Button><Button type="button" size="sm" disabled={reason.trim().length < 8 || (!assignedUserId && !assignedTeamId) || assignmentMutation.isPending} onClick={() => assignmentMutation.mutate()}>Salvar</Button></div>
    </div> : null}
    {milestone ? <div className="space-y-3 rounded-lg border p-3">
      <div><Label>Marco</Label><Input value={milestoneLabel} onChange={(event) => setMilestoneLabel(event.target.value)} disabled={milestone !== 'new'} /></div>
      <div><Label>Status</Label><Select value={milestoneStatus} onValueChange={(value) => setMilestoneStatus(value as PlatformOnboardingMilestone['status'])}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{Object.entries(MILESTONE_LABELS).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>
      <div><Label>Responsável</Label><PlatformEntityCombobox className="mt-1" entityType="staff" purpose="operational_owner" value={milestoneOwnerId} onChange={(value) => setMilestoneOwnerId(value)} label="Selecionar responsável do marco" /></div>
      <div><Label>Prazo</Label><Input type="datetime-local" value={milestoneDueAt} onChange={(event) => setMilestoneDueAt(event.target.value)} /></div>
      <div><Label>Motivo</Label><Input value={reason} onChange={(event) => setReason(event.target.value)} /></div>
      {milestoneMutation.isError ? <p role="alert" className="text-xs text-destructive">O marco mudou ou não pôde ser salvo.</p> : null}<div className="flex justify-end gap-2"><Button type="button" size="sm" variant="outline" onClick={reset}>Cancelar</Button><Button type="button" size="sm" disabled={milestoneLabel.trim().length < 2 || !slugify(milestoneLabel) || reason.trim().length < 8 || milestoneMutation.isPending} onClick={() => milestoneMutation.mutate()}>Salvar</Button></div>
    </div> : null}
  </section>;
}
