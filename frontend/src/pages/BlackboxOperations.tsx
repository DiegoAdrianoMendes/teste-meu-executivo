import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Ban, CheckCircle2, Download, LoaderCircle, Play, RefreshCw, RotateCcw } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { useBlackboxManifest } from '@/blackbox/session-manifest';
import { PlatformEntityCombobox } from '@/components/platform/PlatformEntityCombobox';
import { PlatformEntityMultiSelect } from '@/components/platform/PlatformEntityMultiSelect';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { canPlatform } from '@/lib/platform-access';
import {
  cancelPlatformBulkJob,
  createPlatformBulkJob,
  createPlatformBulkPreview,
  createPlatformBulkRetryPreview,
  createPlatformBulkRestorationPreview,
  createPlatformSelection,
  fetchPlatformBulkJob,
  fetchPlatformBulkJobs,
  fetchPlatformBulkPreview,
  restorePlatformBulkJob,
  type PlatformBulkJob,
  type PlatformBulkPreview,
  type PlatformBulkResourceType,
} from '@/services/platform';
import type { PlatformSearchEntityType } from '@/services/platform-search';
import { createPlatformExport, type PlatformExportJob } from '@/services/platform-exports';

type ResourceDefinition = {
  label: string;
  module: string;
  entityType: PlatformSearchEntityType;
  actions: Array<{ value: string; label: string }>;
};

const RESOURCES: Record<PlatformBulkResourceType, ResourceDefinition> = {
  workspace: { label: 'Contas', module: 'customers', entityType: 'accounts', actions: [{ value: 'suspend', label: 'Suspender' }, { value: 'reactivate', label: 'Reativar' }, { value: 'complete_onboarding', label: 'Concluir onboarding' }, { value: 'reopen_onboarding', label: 'Reabrir onboarding' }, { value: 'set_plan', label: 'Alterar plano' }] },
  customer_user: { label: 'Usuários de contas', module: 'customers', entityType: 'users', actions: [{ value: 'block', label: 'Bloquear' }, { value: 'unblock', label: 'Desbloquear' }, { value: 'revoke_sessions', label: 'Encerrar sessões' }] },
  staff: { label: 'Funcionários', module: 'staff', entityType: 'staff', actions: [{ value: 'suspend', label: 'Suspender' }, { value: 'reactivate', label: 'Reativar' }, { value: 'archive', label: 'Arquivar' }, { value: 'set_role', label: 'Alterar função' }, { value: 'revoke_sessions', label: 'Encerrar sessões' }] },
  lead: { label: 'Leads', module: 'sales', entityType: 'leads', actions: [{ value: 'assign_owner', label: 'Atribuir responsável' }, { value: 'set_stage', label: 'Alterar etapa' }, { value: 'apply_tag', label: 'Aplicar tag' }, { value: 'set_next_action', label: 'Definir próxima ação' }] },
  ticket: { label: 'Tickets', module: 'support', entityType: 'tickets', actions: [{ value: 'assign', label: 'Atribuir responsável' }, { value: 'set_queue', label: 'Alterar fila' }, { value: 'set_priority', label: 'Alterar prioridade' }, { value: 'set_status', label: 'Alterar status' }] },
  subscription: { label: 'Assinaturas', module: 'billing', entityType: 'subscriptions', actions: [{ value: 'set_plan', label: 'Alterar plano' }, { value: 'extend_trial', label: 'Estender trial' }, { value: 'suspend', label: 'Suspender' }, { value: 'reactivate', label: 'Reativar' }] },
  workspace_invitation: { label: 'Convites de conta', module: 'customers', entityType: 'workspace_invitations', actions: [{ value: 'revoke', label: 'Revogar' }] },
  staff_invitation: { label: 'Convites de funcionário', module: 'staff', entityType: 'staff_invitations', actions: [{ value: 'revoke', label: 'Revogar' }] },
};

const TERMINAL = new Set<PlatformBulkJob['status']>(['succeeded', 'partial', 'failed', 'cancelled']);
const ROLE_OPTIONS = ['platform_admin', 'sales_sdr', 'commercial_manager', 'onboarding', 'customer_success', 'support', 'technical_support', 'finance_billing', 'marketing_content', 'community_manager', 'compliance_security', 'product_operations', 'engineering'];

function buildParameters(action: string, values: Record<string, string | null>) {
  if (action === 'set_plan') return values.resourceType === 'workspace' ? { planKey: values.plan } : { plan: values.plan };
  if (action === 'set_role') return { rolePackage: values.rolePackage };
  if (action === 'assign_owner') return { ownerUserId: values.assigneeId };
  if (action === 'assign') return { assignedUserId: values.assigneeId };
  if (action === 'set_stage') return { stage: values.stage };
  if (action === 'apply_tag') return { tag: values.tag };
  if (action === 'set_next_action') return { nextAction: values.nextAction, nextActionAt: values.nextActionAt ? new Date(values.nextActionAt).toISOString() : null };
  if (action === 'set_queue') return { queue: values.queue };
  if (action === 'set_priority') return { priority: values.priority };
  if (action === 'set_status') return { status: values.status };
  if (action === 'extend_trial') return { days: Number(values.days) };
  return {};
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'A operação não pôde ser concluída.';
}

export default function BlackboxOperations() {
  const { effectiveUser } = useBlackboxManifest();
  const queryClient = useQueryClient();
  const allowedResources = useMemo(() => (Object.entries(RESOURCES) as Array<[PlatformBulkResourceType, ResourceDefinition]>).filter(([, definition]) => canPlatform(effectiveUser, definition.module, 'manage')), [effectiveUser]);
  const [resourceType, setResourceType] = useState<PlatformBulkResourceType>(allowedResources[0]?.[0] ?? 'workspace');
  const definition = RESOURCES[resourceType];
  const [action, setAction] = useState(definition.actions[0].value);
  const [ids, setIds] = useState<string[]>([]);
  const [parameters, setParameters] = useState<Record<string, string | null>>({ resourceType, plan: 'escritorio', days: '7' });
  const [reason, setReason] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [preview, setPreview] = useState<PlatformBulkPreview | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [resultExport, setResultExport] = useState<PlatformExportJob | null>(null);
  const [restorePreview, setRestorePreview] = useState<PlatformBulkPreview | null>(null);
  const [restoreReason, setRestoreReason] = useState('');
  const [restoreConfirmation, setRestoreConfirmation] = useState('');
  const [jobsPage, setJobsPage] = useState(0);
  const jobsPageSize = 50;

  const jobsQuery = useQuery({
    queryKey: ['blackbox', 'bulk-jobs', jobsPage],
    queryFn: () => fetchPlatformBulkJobs(jobsPageSize, jobsPage * jobsPageSize),
    refetchInterval: (query) => query.state.data?.items.some((job) => !TERMINAL.has(job.status)) ? 2_000 : false,
    retry: false,
  });
  const previewQuery = useQuery({
    queryKey: ['blackbox', 'bulk-preview', preview?.id],
    queryFn: () => fetchPlatformBulkPreview(preview!.id),
    enabled: preview?.status === 'building',
    refetchInterval: preview?.status === 'building' ? 2_000 : false,
    retry: false,
  });
  useEffect(() => {
    if (previewQuery.data) {
      setPreview(previewQuery.data);
    }
  }, [previewQuery.data]);
  const activeJobQuery = useQuery({
    queryKey: ['blackbox', 'bulk-job', activeJobId],
    queryFn: () => fetchPlatformBulkJob(activeJobId!),
    enabled: Boolean(activeJobId),
    refetchInterval: (query) => query.state.data && !TERMINAL.has(query.state.data.status) ? 2_000 : false,
    retry: false,
  });
  const previewMutation = useMutation({
    mutationFn: async () => {
      const selection = await createPlatformSelection({ resourceType, mode: 'explicit', ids });
      return createPlatformBulkPreview({ selectionSetId: selection.id, action, parameters: buildParameters(action, parameters) });
    },
    onSuccess: (result) => { setPreview(result); setConfirmation(''); },
  });
  const executeMutation = useMutation({
    mutationFn: () => createPlatformBulkJob({ previewId: preview!.id, reason: reason.trim(), confirmationPhrase: preview!.confirmationPhrase ? confirmation : null, idempotencyKey: `bulk:${crypto.randomUUID()}` }),
    onSuccess: async (job) => { setActiveJobId(job.id); setPreview(null); setIds([]); setReason(''); await queryClient.invalidateQueries({ queryKey: ['blackbox', 'bulk-jobs'] }); },
  });
  const cancelMutation = useMutation({
    mutationFn: cancelPlatformBulkJob,
    onSuccess: async (job) => { queryClient.setQueryData(['blackbox', 'bulk-job', job.id], job); await queryClient.invalidateQueries({ queryKey: ['blackbox', 'bulk-jobs'] }); },
  });
  const exportMutation = useMutation({
    mutationFn: (jobId: string) => createPlatformExport({
      dataset: 'bulk_result',
      format: 'csv',
      filters: { bulkJobId: jobId },
      idempotencyKey: `bulk-result:${jobId}:${crypto.randomUUID()}`,
    }),
    onSuccess: setResultExport,
  });
  const retryPreviewMutation = useMutation({
    mutationFn: (job: PlatformBulkJob) => createPlatformBulkRetryPreview(job.id),
    onSuccess: (result, job) => {
      setResourceType(job.resourceType);
      setAction(job.action);
      setPreview(result);
      setReason('');
      setConfirmation('');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },
  });
  const restorePreviewMutation = useMutation({
    mutationFn: createPlatformBulkRestorationPreview,
    onSuccess: (result) => { setRestorePreview(result); setRestoreReason(''); setRestoreConfirmation(''); },
  });
  const restoreMutation = useMutation({
    mutationFn: () => restorePlatformBulkJob({
      jobId: activeJob!.id,
      previewId: restorePreview!.id,
      reason: restoreReason.trim(),
      confirmationPhrase: restoreConfirmation,
      idempotencyKey: `bulk-restore:${activeJob!.id}:${crypto.randomUUID()}`,
    }),
    onSuccess: async (job) => { setRestorePreview(null); setActiveJobId(job.id); await queryClient.invalidateQueries({ queryKey: ['blackbox', 'bulk-jobs'] }); },
  });
  const activeJob = activeJobQuery.data;
  const resetForResource = (next: PlatformBulkResourceType) => {
    const nextDefinition = RESOURCES[next];
    setResourceType(next); setAction(nextDefinition.actions[0].value); setIds([]); setPreview(null); setConfirmation(''); setParameters({ resourceType: next, plan: next === 'workspace' ? 'pro' : 'escritorio', days: '7' });
  };
  const setParameter = (key: string, value: string | null) => {
    setParameters((current) => ({ ...current, [key]: value }));
    setPreview(null);
    setConfirmation('');
  };

  return <div className="mx-auto w-full max-w-[1500px] space-y-6 p-6">
    <header className="flex flex-wrap items-start justify-between gap-4"><div><p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">Operação controlada</p><h1 className="mt-1 text-2xl font-semibold">Central de Operações em Massa</h1><p className="mt-1 text-sm text-muted-foreground">Seleção congelada, prévia de impacto, job persistido e resultado por item.</p></div><Button variant="outline" onClick={() => void jobsQuery.refetch()} disabled={jobsQuery.isFetching}><RefreshCw className={`mr-2 h-4 w-4 ${jobsQuery.isFetching ? 'animate-spin' : ''}`} />Atualizar jobs</Button></header>
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(380px,0.7fr)]">
      <Card><CardHeader><CardTitle>Novo lote</CardTitle><CardDescription>Nenhuma mutação é executada antes da prévia.</CardDescription></CardHeader><CardContent className="space-y-4">
        <div className="grid gap-4 md:grid-cols-2"><div className="space-y-2"><Label>Tipo de registro</Label><Select value={resourceType} onValueChange={(value) => resetForResource(value as PlatformBulkResourceType)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{allowedResources.map(([value, item]) => <SelectItem key={value} value={value}>{item.label}</SelectItem>)}</SelectContent></Select></div><div className="space-y-2"><Label>Ação</Label><Select value={action} onValueChange={(value) => { setAction(value); setPreview(null); }}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{definition.actions.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectContent></Select></div></div>
        <div><Label className="mb-2 block">Registros afetados</Label><PlatformEntityMultiSelect entityType={definition.entityType} purpose="bulk_operation" label={`Selecionar ${definition.label}`} value={ids} onChange={(value) => { setIds(value); setPreview(null); }} /></div>
        {(action === 'assign' || action === 'assign_owner') && <div><Label className="mb-2 block">Responsável</Label><PlatformEntityCombobox entityType="staff" purpose="bulk_assignee" label="Selecionar responsável" value={parameters.assigneeId ?? null} onChange={(value) => setParameter('assigneeId', value)} /></div>}
        {(action === 'set_plan') && <div className="space-y-2"><Label>Plano de destino</Label><Select value={parameters.plan ?? ''} onValueChange={(value) => setParameter('plan', value)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{(resourceType === 'workspace' ? ['basic', 'pro', 'scale'] : ['essencial', 'escritorio', 'performance', 'custom']).map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select></div>}
        {action === 'set_role' && <div className="space-y-2"><Label>Função de destino</Label><Select value={parameters.rolePackage ?? ''} onValueChange={(value) => setParameter('rolePackage', value)}><SelectTrigger><SelectValue placeholder="Selecione" /></SelectTrigger><SelectContent>{ROLE_OPTIONS.map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select></div>}
        {action === 'set_stage' && <div className="space-y-2"><Label>Etapa</Label><Select value={parameters.stage ?? ''} onValueChange={(value) => setParameter('stage', value)}><SelectTrigger><SelectValue placeholder="Selecione" /></SelectTrigger><SelectContent>{['prospect', 'contatado', 'demo_agendada', 'trial', 'proposta', 'pagante', 'perdido'].map((value) => <SelectItem key={value} value={value}>{value.replaceAll('_', ' ')}</SelectItem>)}</SelectContent></Select></div>}
        {action === 'apply_tag' && <div className="space-y-2"><Label>Tag</Label><Input value={parameters.tag ?? ''} onChange={(event) => setParameter('tag', event.target.value)} maxLength={80} /></div>}
        {action === 'set_next_action' && <div className="grid gap-4 md:grid-cols-2"><div className="space-y-2"><Label>Próxima ação</Label><Input value={parameters.nextAction ?? ''} onChange={(event) => setParameter('nextAction', event.target.value)} /></div><div className="space-y-2"><Label>Prazo</Label><Input type="datetime-local" value={parameters.nextActionAt ?? ''} onChange={(event) => setParameter('nextActionAt', event.target.value)} /></div></div>}
        {action === 'set_queue' && <div className="space-y-2"><Label>Fila</Label><Select value={parameters.queue ?? ''} onValueChange={(value) => setParameter('queue', value)}><SelectTrigger><SelectValue placeholder="Selecione" /></SelectTrigger><SelectContent><SelectItem value="support">Suporte</SelectItem><SelectItem value="technical">Suporte técnico</SelectItem></SelectContent></Select></div>}
        {action === 'set_priority' && <div className="space-y-2"><Label>Prioridade</Label><Select value={parameters.priority ?? ''} onValueChange={(value) => setParameter('priority', value)}><SelectTrigger><SelectValue placeholder="Selecione" /></SelectTrigger><SelectContent>{['low', 'normal', 'high', 'urgent'].map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select></div>}
        {action === 'set_status' && <div className="space-y-2"><Label>Status</Label><Select value={parameters.status ?? ''} onValueChange={(value) => setParameter('status', value)}><SelectTrigger><SelectValue placeholder="Selecione" /></SelectTrigger><SelectContent>{['new', 'triage', 'in_progress', 'waiting_customer', 'waiting_internal', 'validating', 'resolved', 'closed', 'cancelled'].map((value) => <SelectItem key={value} value={value}>{value.replaceAll('_', ' ')}</SelectItem>)}</SelectContent></Select></div>}
        {action === 'extend_trial' && <div className="space-y-2"><Label>Dias adicionais</Label><Input type="number" min={1} max={90} value={parameters.days ?? '7'} onChange={(event) => setParameter('days', event.target.value)} /></div>}
        <div className="space-y-2"><Label htmlFor="bulk-operation-reason">Motivo operacional</Label><Textarea id="bulk-operation-reason" rows={3} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Explique o motivo (8 a 500 caracteres)." /></div>
        <Button disabled={!ids.length || previewMutation.isPending} onClick={() => previewMutation.mutate()}><Play className="mr-2 h-4 w-4" />{previewMutation.isPending ? 'Calculando impacto…' : 'Gerar prévia'}</Button>
        {previewMutation.isError && <Alert variant="destructive"><AlertTriangle className="h-4 w-4" /><AlertTitle>Prévia indisponível</AlertTitle><AlertDescription>{errorMessage(previewMutation.error)}</AlertDescription></Alert>}
        {preview && <div className="space-y-3 rounded-lg border p-4" data-testid="bulk-preview">{preview.status === 'building' ? <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><LoaderCircle className="h-4 w-4 animate-spin" />Calculando o impacto do lote em segundo plano…</p> : <><div className="grid grid-cols-4 gap-2 text-center"><div><p className="text-xs text-muted-foreground">Alvos</p><strong>{preview.targetCount}</strong></div><div><p className="text-xs text-muted-foreground">Elegíveis</p><strong className="text-emerald-600">{preview.eligibleCount}</strong></div><div><p className="text-xs text-muted-foreground">Sem mudança</p><strong>{preview.noOpCount}</strong></div><div><p className="text-xs text-muted-foreground">Bloqueados</p><strong className="text-destructive">{preview.blockedCount}</strong></div></div>{preview.warnings.map((warning, index) => <Alert key={`${warning.code ?? 'warning'}-${index}`}><AlertTriangle className="h-4 w-4" /><AlertDescription>{warning.message ?? warning.code}</AlertDescription></Alert>)}{preview.status === 'invalid' && <Alert variant="destructive"><Ban className="h-4 w-4" /><AlertTitle>Prévia inválida</AlertTitle><AlertDescription>{preview.blockers[0]?.reason ?? 'Recrie a seleção e tente novamente.'}</AlertDescription></Alert>}{preview.confirmationPhrase && <div className="space-y-2"><Label>Digite: <span className="font-mono">{preview.confirmationPhrase}</span></Label><Input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></div>}<Button variant={preview.risk === 'critical' ? 'destructive' : 'default'} disabled={preview.status !== 'ready' || preview.eligibleCount === 0 || reason.trim().length < 8 || executeMutation.isPending || Boolean(preview.confirmationPhrase && confirmation !== preview.confirmationPhrase)} onClick={() => executeMutation.mutate()}>{executeMutation.isPending ? 'Enfileirando…' : `Executar ${preview.eligibleCount} alteração(ões)`}</Button></>}</div>}
        {executeMutation.isError && <Alert variant="destructive"><Ban className="h-4 w-4" /><AlertTitle>Job não criado</AlertTitle><AlertDescription>{errorMessage(executeMutation.error)}</AlertDescription></Alert>}
      </CardContent></Card>
      <div className="space-y-6">{activeJob && <Card><CardHeader><CardTitle>Job em acompanhamento</CardTitle><CardDescription>{RESOURCES[activeJob.resourceType].label} · {activeJob.action}</CardDescription></CardHeader><CardContent className="space-y-3" aria-live="polite"><div className="flex items-center justify-between"><Badge>{activeJob.status}</Badge><span className="text-xs text-muted-foreground">{activeJob.processedCount}/{activeJob.eligibleCount}</span></div><Progress value={activeJob.progressPercent} /><p className="text-sm">{activeJob.succeededCount} sucesso(s), {activeJob.failedCount} falha(s), {activeJob.conflictedCount} conflito(s), {activeJob.cancelledCount} cancelado(s).</p><div className="flex flex-wrap gap-2">{!TERMINAL.has(activeJob.status) && <Button variant="outline" onClick={() => cancelMutation.mutate(activeJob.id)} disabled={cancelMutation.isPending}>Cancelar com segurança</Button>}{TERMINAL.has(activeJob.status) && activeJob.action !== 'restore' && activeJob.failedCount + activeJob.conflictedCount + activeJob.skippedCount > 0 ? <Button variant="outline" onClick={() => retryPreviewMutation.mutate(activeJob)} disabled={retryPreviewMutation.isPending}><RefreshCw className="mr-2 h-4 w-4" />{retryPreviewMutation.isPending ? 'Revalidando…' : 'Tentar falhas novamente'}</Button> : null}{TERMINAL.has(activeJob.status) && activeJob.succeededCount > 0 && activeJob.action !== 'restore' ? <Button variant="outline" onClick={() => restorePreviewMutation.mutate(activeJob.id)} disabled={restorePreviewMutation.isPending}><RotateCcw className="mr-2 h-4 w-4" />{restorePreviewMutation.isPending ? 'Validando…' : 'Preparar restauração'}</Button> : null}{TERMINAL.has(activeJob.status) && canPlatform(effectiveUser, 'bulk_operations', 'export') ? <Button variant="outline" onClick={() => exportMutation.mutate(activeJob.id)} disabled={exportMutation.isPending}><Download className="mr-2 h-4 w-4" />{exportMutation.isPending ? 'Enfileirando…' : 'Exportar resultados'}</Button> : null}</div>{retryPreviewMutation.isError ? <Alert variant="destructive"><AlertTitle>Nova tentativa indisponível</AlertTitle><AlertDescription>{errorMessage(retryPreviewMutation.error)}</AlertDescription></Alert> : null}{restorePreviewMutation.isError ? <Alert variant="destructive"><AlertTitle>Restauração indisponível</AlertTitle><AlertDescription>{errorMessage(restorePreviewMutation.error)}</AlertDescription></Alert> : null}{restorePreview ? <div className="space-y-3 rounded-lg border border-primary/30 p-3" role="dialog" aria-modal="true" aria-labelledby="bulk-restore-title"><h3 id="bulk-restore-title" className="font-semibold">Prévia de restauração</h3><p className="text-sm">{restorePreview.eligibleCount} compatível(is) · {restorePreview.blockedCount} bloqueado(s) por alteração posterior ou snapshot incompleto.</p>{restorePreview.warnings.map((warning) => <Alert key={warning.code}><AlertDescription>{warning.message}</AlertDescription></Alert>)}<div><Label>Motivo</Label><Textarea rows={2} value={restoreReason} onChange={(event) => setRestoreReason(event.target.value)} /></div><div><Label>Digite: <span className="font-mono">{restorePreview.confirmationPhrase}</span></Label><Input value={restoreConfirmation} onChange={(event) => setRestoreConfirmation(event.target.value)} /></div>{restoreMutation.isError ? <p role="alert" className="text-sm text-destructive">{errorMessage(restoreMutation.error)}</p> : null}<div className="flex justify-end gap-2"><Button variant="outline" onClick={() => setRestorePreview(null)}>Cancelar</Button><Button variant="destructive" disabled={restorePreview.eligibleCount === 0 || restoreReason.trim().length < 8 || restoreConfirmation !== restorePreview.confirmationPhrase || restoreMutation.isPending} onClick={() => restoreMutation.mutate()}>Restaurar compatíveis</Button></div></div> : null}{resultExport ? <Alert><AlertTitle>Exportação criada</AlertTitle><AlertDescription>O arquivo é processado em segundo plano e expira. <Button asChild variant="link" className="h-auto p-0"><Link to="/blackbox/exports">Abrir Central de Downloads</Link></Button></AlertDescription></Alert> : null}{exportMutation.isError ? <Alert variant="destructive"><AlertTitle>Exportação não criada</AlertTitle><AlertDescription>{errorMessage(exportMutation.error)}</AlertDescription></Alert> : null}</CardContent></Card>}
      <Card><CardHeader><CardTitle>Jobs recentes</CardTitle><CardDescription>{jobsQuery.data?.total ?? 0} job(s) visíveis para suas permissões.</CardDescription></CardHeader><CardContent>{jobsQuery.isPending ? <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><LoaderCircle className="h-4 w-4 animate-spin" />Carregando jobs…</p> : jobsQuery.isError ? <Alert variant="destructive"><AlertTriangle className="h-4 w-4" /><AlertTitle>Histórico indisponível</AlertTitle><AlertDescription>{errorMessage(jobsQuery.error)}</AlertDescription></Alert> : jobsQuery.data?.items.length ? <div className="space-y-3"><Table><TableHeader><TableRow><TableHead>Recurso</TableHead><TableHead>Ação</TableHead><TableHead>Status</TableHead><TableHead>Resultado</TableHead></TableRow></TableHeader><TableBody>{jobsQuery.data.items.map((job) => <TableRow key={job.id} className="cursor-pointer" onClick={() => setActiveJobId(job.id)}><TableCell>{RESOURCES[job.resourceType].label}</TableCell><TableCell>{job.action}</TableCell><TableCell><Badge variant={job.status === 'failed' ? 'destructive' : 'secondary'}>{job.status}</Badge></TableCell><TableCell>{job.succeededCount}/{job.eligibleCount}</TableCell></TableRow>)}</TableBody></Table>{jobsQuery.data.total > jobsPageSize ? <div className="flex items-center justify-between gap-2"><span className="text-xs text-muted-foreground">{jobsQuery.data.offset + 1}–{Math.min(jobsQuery.data.offset + jobsQuery.data.items.length, jobsQuery.data.total)} de {jobsQuery.data.total}</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={jobsPage === 0 || jobsQuery.isFetching} onClick={() => setJobsPage((value) => Math.max(0, value - 1))}>Anterior</Button><Button size="sm" variant="outline" disabled={(jobsPage + 1) * jobsPageSize >= jobsQuery.data.total || jobsQuery.isFetching} onClick={() => setJobsPage((value) => value + 1)}>Próxima</Button></div></div> : null}</div> : <div className="py-8 text-center text-sm text-muted-foreground"><CheckCircle2 className="mx-auto mb-2 h-5 w-5" />Nenhum lote executado.</div>}</CardContent></Card></div>
    </div>
  </div>;
}
