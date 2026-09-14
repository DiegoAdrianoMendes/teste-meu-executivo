import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { GitCommitHorizontal, Plus, RefreshCw } from 'lucide-react';
import { useEffect, useState } from 'react';

import { PlatformEntityCombobox } from '@/components/platform/PlatformEntityCombobox';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import {
  createPlatformReleaseRecord,
  fetchPlatformReleaseRecords,
  updatePlatformReleaseRecord,
  type PlatformReleaseEnvironment,
  type PlatformReleaseRecord,
  type PlatformReleaseStatus,
} from '@/services/platform-releases';

const STATUS_LABELS: Record<PlatformReleaseStatus, string> = {
  planned: 'Planejada', deploying: 'Em implantação', active: 'Ativa', failed: 'Falhou', rolled_back: 'Revertida',
};

function toLocal(value: string | null) {
  if (!value) return '';
  const parsed = new Date(value);
  return new Date(parsed.getTime() - parsed.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

export function PlatformReleasesPanel({ canManage }: { canManage: boolean }) {
  const queryClient = useQueryClient();
  const [environmentFilter, setEnvironmentFilter] = useState<'all' | PlatformReleaseEnvironment>('all');
  const [editing, setEditing] = useState<PlatformReleaseRecord | 'new' | null>(null);
  const [version, setVersion] = useState('');
  const [environment, setEnvironment] = useState<PlatformReleaseEnvironment>('local');
  const [status, setStatus] = useState<PlatformReleaseStatus>('planned');
  const [commitSha, setCommitSha] = useState('');
  const [source, setSource] = useState('manual_blackbox');
  const [deployedAt, setDeployedAt] = useState('');
  const [previousReleaseId, setPreviousReleaseId] = useState<string | null>(null);
  const [rollbackTargetId, setRollbackTargetId] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [page, setPage] = useState(0);
  const pageSize = 50;
  const records = useQuery({
    queryKey: ['platform-governance', 'release-records', environmentFilter, page],
    queryFn: () => fetchPlatformReleaseRecords({
      environment: environmentFilter === 'all' ? undefined : environmentFilter,
      limit: pageSize,
      offset: page * pageSize,
    }),
    retry: false,
  });
  useEffect(() => setPage(0), [environmentFilter]);
  const reset = () => { setEditing(null); setReason(''); };
  const beginNew = () => {
    setEditing('new'); setVersion(''); setEnvironment('local'); setStatus('planned'); setCommitSha('');
    setSource('manual_blackbox'); setDeployedAt(''); setPreviousReleaseId(null); setRollbackTargetId(null); setReason('');
  };
  const beginEdit = (row: PlatformReleaseRecord) => {
    setEditing(row); setVersion(row.version); setEnvironment(row.environment); setStatus(row.status); setCommitSha(row.commitSha ?? '');
    setSource(row.source); setDeployedAt(toLocal(row.deployedAt)); setPreviousReleaseId(row.previousReleaseId); setRollbackTargetId(row.rollbackTargetId); setReason('');
  };
  const mutation = useMutation({
    mutationFn: () => editing === 'new'
      ? createPlatformReleaseRecord({ version: version.trim(), environment, status, commitSha: commitSha.trim() || null, source: source.trim(), deployedAt: deployedAt ? new Date(deployedAt).toISOString() : null, previousReleaseId, rollbackTargetId, reason: reason.trim() })
      : updatePlatformReleaseRecord(editing as PlatformReleaseRecord, { status, deployedAt: deployedAt ? new Date(deployedAt).toISOString() : null, rollbackTargetId, reason: reason.trim() }),
    onSuccess: async () => { reset(); await queryClient.invalidateQueries({ queryKey: ['platform-governance', 'release-records'] }); },
  });

  return <Card className="xl:col-span-2">
    <CardHeader><div className="flex flex-wrap items-start justify-between gap-3"><div><CardTitle className="flex items-center gap-2"><GitCommitHorizontal className="h-5 w-5" />Registro canônico de releases</CardTitle><CardDescription>Deploys observados, revisão otimista e alvos de rollback. Registrar não prova publicação externa.</CardDescription></div><div className="flex gap-2"><Button variant="outline" size="sm" onClick={() => void records.refetch()} disabled={records.isFetching}><RefreshCw className={`mr-1 h-4 w-4 ${records.isFetching ? 'animate-spin' : ''}`} />Atualizar</Button>{canManage ? <Button size="sm" onClick={beginNew}><Plus className="mr-1 h-4 w-4" />Release</Button> : null}</div></div></CardHeader>
    <CardContent className="space-y-4">
      <Select value={environmentFilter} onValueChange={(value) => setEnvironmentFilter(value as typeof environmentFilter)}><SelectTrigger className="w-56" aria-label="Filtrar ambiente"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Todos os ambientes</SelectItem><SelectItem value="local">Local</SelectItem><SelectItem value="staging">Homologação</SelectItem><SelectItem value="production">Produção</SelectItem></SelectContent></Select>
      {records.isPending ? <p role="status" className="text-sm text-muted-foreground">Carregando releases…</p> : records.isError ? <Alert variant="destructive"><AlertTitle>Releases indisponíveis</AlertTitle><AlertDescription>A política permanece acessível, mas os registros canônicos não puderam ser consultados.</AlertDescription></Alert> : records.data?.items.length === 0 ? <p className="text-sm text-muted-foreground">Nenhuma release observada neste ambiente.</p> : <div className="overflow-x-auto rounded-lg border"><table className="w-full min-w-[760px] text-left text-sm"><thead className="bg-muted/60 text-xs text-muted-foreground"><tr><th className="p-3">Versão</th><th className="p-3">Ambiente</th><th className="p-3">Estado</th><th className="p-3">Commit</th><th className="p-3">Implantação</th><th className="p-3">Ação</th></tr></thead><tbody>{records.data?.items.map((row) => <tr key={row.id} className="border-t"><td className="p-3 font-medium">{row.version}<span className="ml-2 text-xs text-muted-foreground">v{row.revision}</span></td><td className="p-3"><Badge variant="outline">{row.environment}</Badge></td><td className="p-3"><Badge variant={row.status === 'failed' ? 'destructive' : 'secondary'}>{STATUS_LABELS[row.status]}</Badge></td><td className="p-3 font-mono text-xs">{row.commitSha?.slice(0, 12) ?? 'Não informado'}</td><td className="p-3 text-xs">{row.deployedAt ? new Date(row.deployedAt).toLocaleString('pt-BR') : 'Ainda não implantada'}</td><td className="p-3">{canManage ? <Button size="sm" variant="outline" onClick={() => beginEdit(row)}>Atualizar</Button> : null}</td></tr>)}</tbody></table></div>}
      {records.data && records.data.total > 0 ? <div className="flex items-center justify-between gap-3"><span className="text-xs text-muted-foreground">{records.data.offset + 1}–{Math.min(records.data.offset + records.data.items.length, records.data.total)} de {records.data.total}</span><span className="flex gap-2"><Button size="sm" variant="outline" disabled={page === 0 || records.isFetching} onClick={() => setPage((value) => Math.max(0, value - 1))}>Anterior</Button><Button size="sm" variant="outline" disabled={!records.data.hasMore || records.isFetching} onClick={() => setPage((value) => value + 1)}>Próxima</Button></span></div> : null}
      {editing ? <div role="dialog" aria-modal="true" aria-labelledby="release-form-title" className="space-y-4 rounded-lg border border-primary/30 p-4"><h3 id="release-form-title" className="font-semibold">{editing === 'new' ? 'Registrar release' : `Atualizar ${editing.version}`}</h3><div className="grid gap-3 md:grid-cols-2">
        <div><Label>Versão</Label><Input value={version} onChange={(event) => setVersion(event.target.value)} disabled={editing !== 'new'} /></div>
        <div><Label>Ambiente</Label><Select value={environment} disabled={editing !== 'new'} onValueChange={(value) => setEnvironment(value as PlatformReleaseEnvironment)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="local">Local</SelectItem><SelectItem value="staging">Homologação</SelectItem><SelectItem value="production">Produção</SelectItem></SelectContent></Select></div>
        <div><Label>Estado</Label><Select value={status} onValueChange={(value) => setStatus(value as PlatformReleaseStatus)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{Object.entries(STATUS_LABELS).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>
        <div><Label>Implantada em</Label><Input type="datetime-local" value={deployedAt} onChange={(event) => setDeployedAt(event.target.value)} /></div>
        {editing === 'new' ? <><div><Label>Commit SHA</Label><Input value={commitSha} onChange={(event) => setCommitSha(event.target.value)} /></div><div><Label>Origem</Label><Input value={source} onChange={(event) => setSource(event.target.value)} /></div><div><Label>Release anterior</Label><PlatformEntityCombobox className="mt-1" entityType="releases" purpose="release_relation" value={previousReleaseId} onChange={(value) => setPreviousReleaseId(value)} label="Selecionar release anterior" /></div></> : null}
        <div><Label>Alvo de rollback</Label><PlatformEntityCombobox className="mt-1" entityType="releases" purpose="release_relation" value={rollbackTargetId} onChange={(value) => setRollbackTargetId(value)} label="Selecionar alvo de rollback" /></div>
      </div><div><Label>Motivo</Label><Textarea value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Contexto da mudança, validação ou rollback" /></div>{mutation.isError ? <Alert variant="destructive"><AlertTitle>Release não salva</AlertTitle><AlertDescription>O registro mudou, o vínculo é inválido ou a operação não foi autorizada.</AlertDescription></Alert> : null}<div className="flex justify-end gap-2"><Button variant="outline" onClick={reset}>Cancelar</Button><Button disabled={reason.trim().length < 8 || (editing === 'new' && (version.trim().length < 1 || source.trim().length < 2)) || mutation.isPending} onClick={() => mutation.mutate()}>Salvar release</Button></div></div> : null}
    </CardContent>
  </Card>;
}
