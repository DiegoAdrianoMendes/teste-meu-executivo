import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Ban, CheckCircle2, Download, FileClock, LoaderCircle, RefreshCw } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { useBlackboxManifest } from '@/blackbox/session-manifest';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { canPlatform } from '@/lib/platform-access';
import {
  cancelPlatformExport,
  createPlatformExport,
  downloadPlatformExport,
  fetchPlatformExports,
  type PlatformExportDataset,
  type PlatformExportFormat,
  type PlatformExportJob,
} from '@/services/platform-exports';

const DATASETS: Array<{
  value: PlatformExportDataset;
  label: string;
  description: string;
  permissions: Array<[string, string]>;
}> = [
  { value: 'accounts', label: 'Contas', description: 'Contas, estado e onboarding.', permissions: [['customers', 'export']] },
  { value: 'users', label: 'Usuários', description: 'Usuários das contas com mascaramento por papel.', permissions: [['customers', 'export']] },
  { value: 'kpis', label: 'KPIs', description: 'Indicadores autorizados e qualidade da fonte.', permissions: [['reports', 'export']] },
  { value: 'billing', label: 'Cobrança', description: 'Assinaturas e estado comercial; valores são root-only.', permissions: [['billing', 'export'], ['finance', 'export']] },
  { value: 'tickets', label: 'Tickets', description: 'SLA, fila e status sem conteúdo privado.', permissions: [['support', 'export']] },
  { value: 'leads', label: 'Leads', description: 'Pipeline comercial com dados mascarados por papel.', permissions: [['sales', 'export']] },
  { value: 'audit', label: 'Auditoria', description: 'Eventos canônicos sanitizados.', permissions: [['audit', 'export']] },
  { value: 'bulk_result', label: 'Resultado de lote', description: 'Disponível a partir da Central de Jobs.', permissions: [['bulk_operations', 'export']] },
];

const STATUS_LABELS: Record<PlatformExportJob['status'], string> = {
  queued: 'Na fila', running: 'Gerando', cancelling: 'Cancelando', completed: 'Pronto',
  failed: 'Falhou', cancelled: 'Cancelado', expired: 'Expirado',
};

const TERMINAL = new Set<PlatformExportJob['status']>(['completed', 'failed', 'cancelled', 'expired']);

function dateLabel(value: string | null) {
  return value ? new Date(value).toLocaleString('pt-BR') : 'Ainda não definido';
}

function sizeLabel(value: number | null) {
  if (value === null) return 'Indisponível';
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 ** 2).toFixed(1)} MB`;
}

export default function BlackboxExports() {
  const { effectiveUser } = useBlackboxManifest();
  const queryClient = useQueryClient();
  const allowedDatasets = useMemo(
    () => DATASETS.filter((dataset) => dataset.permissions.some(([module, action]) => canPlatform(effectiveUser, module, action))),
    [effectiveUser],
  );
  const [dataset, setDataset] = useState<PlatformExportDataset>(allowedDatasets[0]?.value ?? 'accounts');
  const [format, setFormat] = useState<PlatformExportFormat>('csv');
  const [page, setPage] = useState(0);
  const pageSize = 50;

  useEffect(() => {
    if (allowedDatasets.length > 0 && !allowedDatasets.some((item) => item.value === dataset)) {
      setDataset(allowedDatasets[0].value);
    }
  }, [allowedDatasets, dataset]);

  const jobsQuery = useQuery({
    queryKey: ['blackbox', 'exports', page],
    queryFn: () => fetchPlatformExports(pageSize, page * pageSize),
    refetchInterval: (query) => query.state.data?.items.some((job) => !TERMINAL.has(job.status)) ? 2_000 : false,
    retry: false,
  });
  const createMutation = useMutation({
    mutationFn: () => createPlatformExport({
      dataset,
      format,
      idempotencyKey: `export:${dataset}:${format}:${crypto.randomUUID()}`,
    }),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ['blackbox', 'exports'] }),
  });
  const cancelMutation = useMutation({
    mutationFn: cancelPlatformExport,
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ['blackbox', 'exports'] }),
  });
  const downloadMutation = useMutation({ mutationFn: downloadPlatformExport });
  const selected = allowedDatasets.find((item) => item.value === dataset);

  return (
    <div className="mx-auto w-full max-w-[1500px] space-y-6 p-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">Dados administrativos</p>
          <h1 className="mt-1 text-2xl font-semibold">Central de Downloads</h1>
          <p className="mt-1 text-sm text-muted-foreground">Arquivos privados, assíncronos, auditados e disponíveis por 24 horas.</p>
        </div>
        <Button variant="outline" onClick={() => void jobsQuery.refetch()} disabled={jobsQuery.isFetching}>
          <RefreshCw className={`mr-2 h-4 w-4 ${jobsQuery.isFetching ? 'animate-spin' : ''}`} />Atualizar
        </Button>
      </header>

      {allowedDatasets.length === 0 ? (
        <Alert><Ban className="h-4 w-4" /><AlertTitle>Nenhum dataset autorizado</AlertTitle><AlertDescription>O painel permanece acessível, mas o backend não concedeu permissão de exportação a esta função.</AlertDescription></Alert>
      ) : (
        <Card>
          <CardHeader><CardTitle>Nova exportação</CardTitle><CardDescription>{selected?.description}</CardDescription></CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-[minmax(0,1fr)_220px_auto] md:items-end">
            <div><Label>Dataset</Label><Select value={dataset} onValueChange={(value) => setDataset(value as PlatformExportDataset)}><SelectTrigger aria-label="Dataset"><SelectValue /></SelectTrigger><SelectContent>{allowedDatasets.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectContent></Select></div>
            <div><Label>Formato</Label><Select value={format} onValueChange={(value) => setFormat(value as PlatformExportFormat)}><SelectTrigger aria-label="Formato"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="csv">CSV</SelectItem><SelectItem value="xlsx">XLSX</SelectItem><SelectItem value="ndjson">NDJSON</SelectItem></SelectContent></Select></div>
            <Button onClick={() => createMutation.mutate()} disabled={createMutation.isPending || !selected || dataset === 'bulk_result'}>{createMutation.isPending ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <FileClock className="mr-2 h-4 w-4" />}Gerar arquivo</Button>
            {dataset === 'bulk_result' ? <p className="text-xs text-muted-foreground md:col-span-3">Resultados de lote são exportados pelo próprio detalhe do job, preservando o vínculo auditável.</p> : null}
          </CardContent>
        </Card>
      )}

      {createMutation.isError || jobsQuery.isError || cancelMutation.isError || downloadMutation.isError ? (
        <Alert variant="destructive"><AlertTriangle className="h-4 w-4" /><AlertTitle>Operação não concluída</AlertTitle><AlertDescription>A permissão pode ter sido revogada, o arquivo pode ter expirado ou ocorreu uma falha recuperável. Atualize a central antes de repetir.</AlertDescription></Alert>
      ) : null}

      <Card>
        <CardHeader><CardTitle>Histórico de exportações</CardTitle><CardDescription>{jobsQuery.data ? `${jobsQuery.data.total} jobs visíveis` : 'Consultando a fonte autoritativa…'}</CardDescription></CardHeader>
        <CardContent className="space-y-3">
          {jobsQuery.isPending ? <p className="py-12 text-center text-sm text-muted-foreground" role="status" aria-live="polite">Carregando exportações…</p> : null}
          {!jobsQuery.isPending && jobsQuery.data?.items.length === 0 ? <div className="grid place-items-center rounded-lg border border-dashed py-12 text-center"><CheckCircle2 className="h-8 w-8 text-emerald-600" /><p className="mt-3 font-medium">Nenhuma exportação solicitada</p><p className="text-sm text-muted-foreground">A consulta foi concluída sem resultados.</p></div> : null}
          {jobsQuery.data?.items.map((job) => (
            <article key={job.id} className="flex flex-wrap items-start justify-between gap-4 rounded-lg border p-4">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap gap-2"><Badge variant={job.status === 'failed' ? 'destructive' : 'secondary'}>{STATUS_LABELS[job.status]}</Badge><Badge variant="outline">{DATASETS.find((item) => item.value === job.dataset)?.label ?? job.dataset}</Badge><Badge variant="outline">{job.format.toUpperCase()}</Badge></div>
                <p className="mt-2 text-sm">{job.processedRows.toLocaleString('pt-BR')} linhas processadas{job.rowCount !== null ? ` · ${job.rowCount.toLocaleString('pt-BR')} concluídas` : ''} · {sizeLabel(job.byteSize)}</p>
                <p className="mt-1 text-xs text-muted-foreground">Solicitado em {dateLabel(job.createdAt)} · expira em {dateLabel(job.expiresAt)}</p>
                {job.checksumSha256 ? <p className="mt-1 truncate font-mono text-[10px] text-muted-foreground" title={job.checksumSha256}>SHA-256 {job.checksumSha256}</p> : null}
                {job.errorCode ? <p className="mt-2 text-xs text-destructive">{job.errorCode}: {job.errorDetail ?? 'Falha sanitizada sem detalhe adicional.'}</p> : null}
              </div>
              <div className="flex gap-2">
                {!TERMINAL.has(job.status) ? <Button variant="outline" size="sm" onClick={() => cancelMutation.mutate(job.id)} disabled={cancelMutation.isPending}><Ban className="mr-2 h-4 w-4" />Cancelar</Button> : null}
                {job.status === 'completed' ? <Button size="sm" onClick={() => downloadMutation.mutate(job)} disabled={downloadMutation.isPending}><Download className="mr-2 h-4 w-4" />Baixar</Button> : null}
              </div>
            </article>
          ))}
          {jobsQuery.data && jobsQuery.data.total > pageSize ? <div className="flex justify-between"><Button variant="outline" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>Anterior</Button><span className="self-center text-xs text-muted-foreground">Página {page + 1} de {Math.ceil(jobsQuery.data.total / pageSize)}</span><Button variant="outline" disabled={(page + 1) * pageSize >= jobsQuery.data.total} onClick={() => setPage((value) => value + 1)}>Próxima</Button></div> : null}
        </CardContent>
      </Card>
    </div>
  );
}
