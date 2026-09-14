import { useQuery } from '@tanstack/react-query';
import { Activity, AlertTriangle, CheckCircle2, Clock3, Database, RefreshCw, ServerCog } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  fetchPlatformPeriodicTasks,
  fetchPlatformStatusHistory,
  fetchPlatformSystemStatus,
} from '@/services/platform';

const statusLabels: Record<string, string> = {
  operational: 'Operacional', partial: 'Parcial', degraded: 'Degradado', unknown: 'Desconhecido',
  not_configured: 'Não configurado', success: 'Sucesso', error: 'Erro', running: 'Em execução', never_run: 'Nunca executada',
  prepared: 'Preparado, sem conexão', connected_unverified: 'Cadastrado, não verificado', configured_unverified: 'Credencial não verificada',
};

function statusVariant(value: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (value === 'degraded' || value === 'error') return 'destructive';
  if (value === 'operational' || value === 'success') return 'default';
  if (value === 'unknown' || value === 'not_configured' || value === 'never_run') return 'outline';
  return 'secondary';
}

function dateTime(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString('pt-BR') : 'Sem observação';
}

export default function BlackboxInfrastructure() {
  const statusQuery = useQuery({ queryKey: ['blackbox', 'infrastructure', 'status'], queryFn: fetchPlatformSystemStatus, refetchInterval: 30_000 });
  const historyQuery = useQuery({ queryKey: ['blackbox', 'infrastructure', 'history'], queryFn: fetchPlatformStatusHistory, refetchInterval: 60_000 });
  const tasksQuery = useQuery({ queryKey: ['blackbox', 'infrastructure', 'tasks'], queryFn: fetchPlatformPeriodicTasks, refetchInterval: 30_000, retry: false });
  const refresh = () => void Promise.all([statusQuery.refetch(), historyQuery.refetch(), tasksQuery.refetch()]);
  const refreshing = statusQuery.isFetching || historyQuery.isFetching || tasksQuery.isFetching;
  const summary = tasksQuery.data?.summary;

  return (
    <div className="mx-auto w-full max-w-[1500px] space-y-6 p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div><h1 className="text-2xl font-semibold">Infraestrutura e workers</h1><p className="mt-1 text-sm text-muted-foreground">Telemetria real persistida, componentes, incidentes e execução das rotinas internas.</p></div>
        <Button variant="outline" onClick={refresh} disabled={refreshing}><RefreshCw className={`mr-2 h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />Atualizar</Button>
      </div>

      {statusQuery.isError || historyQuery.isError || tasksQuery.isError ? <Alert variant="destructive"><AlertTriangle className="h-4 w-4" /><AlertTitle>Telemetria parcialmente indisponível</AlertTitle><AlertDescription>Cada bloco abaixo distingue falha de leitura de ausência de eventos; nenhum zero é presumido.</AlertDescription></Alert> : null}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card><CardHeader className="pb-2"><CardDescription>Estado geral</CardDescription></CardHeader><CardContent><div className="flex items-center gap-2"><Activity className="h-5 w-5" />{statusQuery.data ? <Badge variant={statusVariant(statusQuery.data.status)}>{statusLabels[statusQuery.data.status] ?? statusQuery.data.status}</Badge> : <span className="text-sm text-muted-foreground">Indisponível</span>}</div><p className="mt-2 text-xs text-muted-foreground">{dateTime(statusQuery.data?.timestamp)}</p></CardContent></Card>
        <Card><CardHeader className="pb-2"><CardDescription>Uptime observado</CardDescription></CardHeader><CardContent><p className="text-2xl font-semibold">{historyQuery.data?.uptimePercent == null ? 'Não calculável' : `${historyQuery.data.uptimePercent.toFixed(2)}%`}</p><p className="mt-1 text-xs text-muted-foreground">{historyQuery.data ? `${historyQuery.data.observations} observação(ões) em ${historyQuery.data.daysObserved} dia(s)` : 'Histórico indisponível'}</p></CardContent></Card>
        <Card><CardHeader className="pb-2"><CardDescription>Rotinas com erro</CardDescription></CardHeader><CardContent><p className="text-2xl font-semibold">{summary ? summary.failing : 'Indisponível'}</p><p className="mt-1 text-xs text-muted-foreground">{summary ? `${summary.total} rotina(s) catalogada(s)` : 'Leitura não concluída'}</p></CardContent></Card>
        <Card><CardHeader className="pb-2"><CardDescription>Rotinas atrasadas</CardDescription></CardHeader><CardContent><p className="text-2xl font-semibold">{summary ? summary.late : 'Indisponível'}</p><p className="mt-1 text-xs text-muted-foreground">{summary ? `${summary.neverRun} nunca executada(s)` : 'Leitura não concluída'}</p></CardContent></Card>
      </div>

      <Card><CardHeader><CardTitle className="flex items-center gap-2"><Database className="h-5 w-5" />Componentes</CardTitle><CardDescription>O estado “configurado” não é prova de chamada externa bem-sucedida.</CardDescription></CardHeader><CardContent>{statusQuery.isPending ? <p role="status" className="text-sm text-muted-foreground">Consultando componentes…</p> : statusQuery.data ? <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{Object.entries(statusQuery.data.components).map(([key, component]) => <div key={key} className="rounded-lg border p-4"><div className="flex items-center justify-between gap-2"><p className="font-mono text-xs font-semibold">{key}</p><Badge variant={statusVariant(component.status)}>{statusLabels[component.status] ?? component.status}</Badge></div><div className="mt-2 space-y-1 text-xs text-muted-foreground">{component.latency_ms != null ? <p>Latência: {component.latency_ms} ms</p> : null}{component.workers != null ? <p>Workers: {component.workers}</p> : null}{component.note ? <p>{component.note}</p> : null}{component.error ? <p>{component.error}</p> : null}</div></div>)}</div> : <p className="text-sm text-muted-foreground">Componentes indisponíveis.</p>}</CardContent></Card>

      <Card><CardHeader><CardTitle className="flex items-center gap-2"><ServerCog className="h-5 w-5" />Tarefas periódicas</CardTitle><CardDescription>Falha de uma rotina não é mascarada pelo estado geral dos workers.</CardDescription></CardHeader><CardContent>{tasksQuery.isPending ? <p role="status" className="text-sm text-muted-foreground">Consultando rotinas…</p> : tasksQuery.data?.available === false ? <Alert><AlertTitle>Leitura indisponível</AlertTitle><AlertDescription>O backend não conseguiu consultar a saúde das tarefas. Isso não equivale a zero falhas.</AlertDescription></Alert> : tasksQuery.data ? <div className="overflow-x-auto rounded-lg border"><table className="w-full min-w-[900px] text-left text-sm"><thead className="bg-muted/60 text-xs text-muted-foreground"><tr><th className="p-3">Rotina</th><th className="p-3">Última execução</th><th className="p-3">Contadores</th><th className="p-3">Situação</th></tr></thead><tbody>{tasksQuery.data.tasks.map((task) => <tr key={task.scheduleName} className="border-t"><td className="p-3"><p className="font-medium">{task.scheduleName}</p><p className="font-mono text-xs text-muted-foreground">{task.taskName}</p><p className="text-xs text-muted-foreground">{task.scheduleDescription}</p></td><td className="p-3 text-xs">{dateTime(task.lastFinishedAt)}{task.lastDurationMs != null ? <p className="text-muted-foreground">{task.lastDurationMs} ms</p> : null}</td><td className="p-3 text-xs">{task.successCount} sucesso(s)<br />{task.errorCount} erro(s)</td><td className="p-3"><div className="flex flex-wrap gap-1"><Badge variant={statusVariant(task.status)}>{statusLabels[task.status] ?? task.status}</Badge>{task.isLate ? <Badge variant="destructive">Atrasada</Badge> : null}</div>{task.errorType ? <p className="mt-1 font-mono text-xs text-destructive">{task.errorType}</p> : null}</td></tr>)}</tbody></table></div> : <p className="text-sm text-muted-foreground">Tarefas indisponíveis.</p>}</CardContent></Card>

      <Card><CardHeader><CardTitle className="flex items-center gap-2"><Clock3 className="h-5 w-5" />Incidentes observados</CardTitle><CardDescription>Histórico dos últimos {historyQuery.data?.periodDays ?? 30} dias, derivado apenas de observações persistidas.</CardDescription></CardHeader><CardContent>{historyQuery.data?.state === 'collecting' ? <Alert><AlertTitle>Dados insuficientes</AlertTitle><AlertDescription>A coleta começou, mas ainda não há janela suficiente para inferir disponibilidade.</AlertDescription></Alert> : historyQuery.data?.incidents.length ? <div className="space-y-2">{historyQuery.data.incidents.map((incident) => <div key={`${incident.startedAt}-${incident.status}`} className="flex flex-wrap items-start justify-between gap-3 rounded-lg border p-3"><div><p className="text-sm font-medium">{dateTime(incident.startedAt)} — {incident.ongoing ? 'em andamento' : dateTime(incident.endedAt)}</p><p className="text-xs text-muted-foreground">{incident.components.join(', ') || 'Componentes não identificados'} · {incident.observations} observação(ões)</p></div><Badge variant={statusVariant(incident.status)}>{statusLabels[incident.status] ?? incident.status}</Badge></div>)}</div> : historyQuery.data?.state === 'ready' ? <div className="flex items-center gap-2 text-sm text-muted-foreground"><CheckCircle2 className="h-4 w-4 text-emerald-600" />Nenhum incidente nas observações disponíveis.</div> : <p className="text-sm text-muted-foreground">Histórico indisponível.</p>}</CardContent></Card>
    </div>
  );
}
