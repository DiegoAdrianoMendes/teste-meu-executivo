import { useCallback, useEffect, useState, type ComponentType } from 'react';
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  DatabaseZap,
  FileSignature,
  MessageCircleMore,
  RefreshCw,
  ServerCog,
  Timer,
} from 'lucide-react';
import { ApiError, apiFetch } from '@/services/http';

const STATUS_LABELS = {
  operational: { label: 'Operacional', dot: 'bg-emerald-600', text: 'text-emerald-800', bg: 'bg-emerald-50', border: 'border-emerald-200' },
  degraded: { label: 'Degradado', dot: 'bg-red-600', text: 'text-red-800', bg: 'bg-red-50', border: 'border-red-200' },
  partial: { label: 'Parcial', dot: 'bg-amber-600', text: 'text-amber-800', bg: 'bg-amber-50', border: 'border-amber-200' },
  prepared: { label: 'Preparado, sem conexão', dot: 'bg-amber-500', text: 'text-amber-800', bg: 'bg-amber-50', border: 'border-amber-200' },
  connected_unverified: { label: 'Cadastrado, não verificado', dot: 'bg-amber-500', text: 'text-amber-800', bg: 'bg-amber-50', border: 'border-amber-200' },
  configured_unverified: { label: 'Credencial não verificada', dot: 'bg-amber-500', text: 'text-amber-800', bg: 'bg-amber-50', border: 'border-amber-200' },
  not_configured: { label: 'Não configurado', dot: 'bg-zinc-400', text: 'text-zinc-700', bg: 'bg-zinc-100', border: 'border-zinc-200' },
  unknown: { label: 'Indeterminado', dot: 'bg-zinc-500', text: 'text-zinc-700', bg: 'bg-zinc-100', border: 'border-zinc-200' },
} as const;

const COMPONENTS: Record<string, { label: string; icon: ComponentType<{ className?: string }> }> = {
  database: { label: 'Banco de dados', icon: Database },
  cache: { label: 'Cache e filas', icon: DatabaseZap },
  workers: { label: 'Workers de automação', icon: ServerCog },
  whatsapp_api: { label: 'WhatsApp Business API', icon: MessageCircleMore },
  zapsign_api: { label: 'ZapSign', icon: FileSignature },
};

interface ComponentStatus {
  status: string;
  latency_ms?: number | null;
  workers?: number;
  note?: string;
  error?: string;
}

interface SystemStatus {
  status: string;
  timestamp: string;
  version: string;
  components: Record<string, ComponentStatus>;
  message: string;
  historyRecorded: boolean;
}

interface StatusDay {
  date: string;
  status: string;
  observations: number;
  uptimePercent: number | null;
}

interface StatusIncident {
  startedAt: string;
  endedAt: string | null;
  ongoing: boolean;
  status: string;
  observations: number;
  components: string[];
}

interface StatusHistory {
  state: 'ready' | 'collecting' | 'unavailable';
  periodDays: number;
  observations: number;
  uptimePercent: number | null;
  firstObservedAt: string | null;
  lastObservedAt: string | null;
  daysObserved: number;
  latestStatus: string | null;
  daily: StatusDay[];
  incidents: StatusIncident[];
}

// Dormente #9 — estado por task individual do Celery Beat. Aditivo: nada
// da visão geral acima muda; ganha-se uma aba com a tabela por task.
interface PeriodicTask {
  scheduleName: string;
  taskName: string;
  scheduleDescription: string;
  status: 'success' | 'error' | 'running' | 'never_run' | string;
  lastStartedAt: string | null;
  lastFinishedAt: string | null;
  lastSuccessAt: string | null;
  lastErrorAt: string | null;
  lastDurationMs: number | null;
  errorType: string | null;
  successCount: number;
  errorCount: number;
  isLate: boolean | null;
  expectedNextRunAt: string | null;
}

interface PeriodicTaskOverview {
  generatedAt: string;
  graceSeconds: number;
  available?: boolean;
  summary: { total: number; failing: number; late: number; neverRun: number; healthy: number } | null;
  tasks: PeriodicTask[];
}

const TASK_STATUS_LABELS: Record<string, { label: string; bg: string; text: string }> = {
  success: { label: 'Sucesso', bg: 'bg-emerald-50', text: 'text-emerald-800' },
  error: { label: 'Erro', bg: 'bg-red-50', text: 'text-red-800' },
  running: { label: 'Em execução', bg: 'bg-blue-50', text: 'text-blue-800' },
  never_run: { label: 'Nunca executada', bg: 'bg-zinc-100', text: 'text-zinc-700' },
};

function taskStatusInfo(status: string) {
  return TASK_STATUS_LABELS[status] ?? { label: status, bg: 'bg-zinc-100', text: 'text-zinc-700' };
}

function taskNeedsAttention(task: PeriodicTask) {
  return task.status === 'error' || task.isLate === true;
}

function statusInfo(status: string) {
  return STATUS_LABELS[status as keyof typeof STATUS_LABELS] ?? STATUS_LABELS.unknown;
}

function formatDateTime(value: string | null) {
  if (!value) return '—';
  return new Date(value).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
}

function dayBarClass(status: string) {
  if (status === 'operational') return 'bg-emerald-500';
  if (status === 'partial') return 'bg-[#c99a25]';
  if (['prepared', 'connected_unverified', 'configured_unverified'].includes(status)) return 'bg-[#c99a25]';
  if (status === 'degraded') return 'bg-red-500';
  if (status === 'unknown') return 'bg-zinc-500';
  return 'bg-zinc-200';
}

export default function StatusPage() {
  const [data, setData] = useState<SystemStatus | null>(null);
  const [history, setHistory] = useState<StatusHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  const [tab, setTab] = useState<'overview' | 'workers'>('overview');
  const [tasks, setTasks] = useState<PeriodicTaskOverview | null>(null);
  const [tasksDenial, setTasksDenial] = useState<'restricted' | 'unavailable' | null>(null);

  const fetchStatus = useCallback(async () => {
    setRefreshing(true);
    try {
      const current = await apiFetch<SystemStatus>('/api/v1/status');
      setData(current);
      setLastChecked(new Date(current.timestamp));
      setError(null);

      setHistory(await apiFetch<StatusHistory>('/api/v1/status/history'));
      // O inventário por task é restrito a root: esta página é pública e
      // enumerar as rotinas internas do deploy não é sinal de
      // disponibilidade. Visitante anônimo cai em 401/403 e precisa ver
      // "restrito", não o mesmo vazio de uma falha de leitura.
      try {
        const overview = await apiFetch<PeriodicTaskOverview>('/api/v1/status/tasks');
        setTasks(overview);
        setTasksDenial(overview.available === false ? 'unavailable' : null);
      } catch (taskError) {
        setTasks(null);
        const status = taskError instanceof ApiError ? taskError.status : 0;
        setTasksDenial(status === 401 || status === 403 ? 'restricted' : 'unavailable');
      }
    } catch {
      setError('Não foi possível consultar a telemetria local agora.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void fetchStatus();
    const interval = window.setInterval(() => void fetchStatus(), 30_000);
    return () => window.clearInterval(interval);
  }, [fetchStatus]);

  const overall = statusInfo(data?.status ?? 'unknown');
  const OverallIcon = data?.status === 'degraded' ? AlertTriangle : CheckCircle2;

  return (
    <main className="min-h-screen bg-[#f4f4f5] text-[#111111]">
      <header className="border-b border-white/10 bg-[#050505] text-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-4 py-5 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <img src="/logo-icon.png" alt="Meu Executivo" className="h-9 w-9 object-contain" />
            <div className="min-w-0">
              <p className="text-[10px] font-semibold uppercase text-primary">Meu Executivo</p>
              <h1 className="truncate text-lg font-semibold">Status operacional</h1>
            </div>
          </div>
          <button
            type="button"
            onClick={() => void fetchStatus()}
            disabled={refreshing}
            title="Atualizar telemetria"
            aria-label="Atualizar telemetria"
            className="grid h-9 w-9 shrink-0 place-items-center rounded-md border border-white/20 text-white transition hover:border-primary hover:text-primary disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </header>

      <div className="mx-auto max-w-5xl space-y-4 px-4 py-6 sm:px-6 sm:py-8">
        {error && (
          <div role="alert" className="flex items-center gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
            <AlertTriangle className="h-5 w-5 shrink-0" />
            {error}
          </div>
        )}

        <div role="tablist" aria-label="Seções do status" className="flex gap-2 border-b">
          {([
            { id: 'overview', label: 'Visão geral' },
            { id: 'workers', label: 'Workers' },
          ] as const).map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              id={`status-tab-${item.id}`}
              aria-selected={tab === item.id}
              aria-controls={`status-panel-${item.id}`}
              onClick={() => setTab(item.id)}
              className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition ${
                tab === item.id
                  ? 'border-[#a77d12] text-[#111111]'
                  : 'border-transparent text-zinc-500 hover:text-zinc-800'
              }`}
            >
              {item.label}
              {item.id === 'workers' && tasks?.summary && tasks.summary.failing + tasks.summary.late > 0 && (
                <span className="ml-2 rounded-full bg-red-50 px-2 py-0.5 text-[10px] font-semibold text-red-800">
                  {tasks.summary.failing + tasks.summary.late}
                </span>
              )}
            </button>
          ))}
        </div>

        {tab === 'workers' && (
          <section
            role="tabpanel"
            id="status-panel-workers"
            aria-labelledby="status-tab-workers"
            className="overflow-hidden rounded-lg border bg-white"
            aria-label="Tasks periódicas"
          >
            <div className="flex items-center justify-between border-b px-5 py-4">
              <div>
                <h2 className="font-semibold">Tasks periódicas</h2>
                <p className="mt-1 text-xs text-zinc-500">
                  Última execução de cada task do agendador. Uma task pode falhar sozinha mesmo com todos os workers de pé.
                </p>
              </div>
              <Timer className="h-5 w-5 text-[#a77d12]" />
            </div>

            {tasks?.summary ? (
              <>
                <div className="grid gap-3 border-b bg-zinc-50 px-5 py-3 text-xs text-zinc-600 sm:grid-cols-4">
                  <span><strong className="block text-zinc-900">{tasks.summary.total}</strong>task(s) agendada(s)</span>
                  <span><strong className="block text-red-800">{tasks.summary.failing}</strong>com erro</span>
                  <span><strong className="block text-[#a77d12]">{tasks.summary.late}</strong>atrasada(s)</span>
                  <span><strong className="block text-zinc-900">{tasks.summary.neverRun}</strong>nunca executada(s)</span>
                </div>
                <table className="w-full text-left text-xs">
                  <thead className="bg-white text-[10px] uppercase text-zinc-500">
                    <tr>
                      <th scope="col" className="px-5 py-3 font-semibold">Task</th>
                      <th scope="col" className="px-5 py-3 font-semibold">Última execução</th>
                      <th scope="col" className="px-5 py-3 font-semibold">Situação</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tasks.tasks.map((task) => {
                      const info = taskStatusInfo(task.status);
                      const attention = taskNeedsAttention(task);
                      return (
                        <tr
                          key={task.scheduleName}
                          data-testid={`task-row-${task.scheduleName}`}
                          data-attention={attention ? 'true' : 'false'}
                          className={`border-t align-top ${attention ? 'bg-red-50/60' : ''}`}
                        >
                          <td className="px-5 py-3">
                            <span className="block font-medium text-zinc-900">{task.scheduleName}</span>
                            <span className="block font-mono text-[10px] text-zinc-500">{task.taskName}</span>
                            <span className="block text-[10px] text-zinc-400">{task.scheduleDescription}</span>
                          </td>
                          <td className="px-5 py-3 text-zinc-600">
                            <span className="block">{formatDateTime(task.lastFinishedAt)}</span>
                            {task.lastDurationMs != null && (
                              <span className="block font-mono text-[10px] text-zinc-400">{task.lastDurationMs} ms</span>
                            )}
                            <span className="block text-[10px] text-zinc-400">
                              {task.successCount} sucesso(s) · {task.errorCount} erro(s)
                            </span>
                          </td>
                          <td className="px-5 py-3">
                            <span className={`inline-block rounded-full px-2.5 py-1 text-[10px] font-semibold ${info.bg} ${info.text}`}>
                              {info.label}
                            </span>
                            {task.isLate === true && (
                              <span className="ml-2 inline-block rounded-full bg-[#fdf3dc] px-2.5 py-1 text-[10px] font-semibold text-[#7a5c0d]">
                                Atrasada
                              </span>
                            )}
                            {task.errorType && (
                              <span className="mt-1 block font-mono text-[10px] text-red-800">{task.errorType}</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </>
            ) : (
              <div className="p-6 text-center text-sm text-zinc-500" data-testid="workers-empty-state">
                {tasksDenial === 'restricted'
                  ? 'O estado por task é restrito à administração da plataforma. Entre como root para visualizar.'
                  : 'O estado por task não pôde ser lido agora. Isso não significa que as tasks estejam saudáveis.'}
              </div>
            )}
          </section>
        )}

        {tab === 'overview' && (
        <div
          role="tabpanel"
          id="status-panel-overview"
          aria-labelledby="status-tab-overview"
          className="space-y-4"
        >
        {loading ? (
          <div className="h-32 animate-pulse rounded-lg border bg-white" />
        ) : (
          <section className={`rounded-lg border p-5 ${overall.bg} ${overall.border}`} aria-labelledby="overall-status">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-start gap-3">
                <span className={`mt-1 grid h-9 w-9 shrink-0 place-items-center rounded-full ${overall.dot} text-white`}>
                  <OverallIcon className="h-5 w-5" />
                </span>
                <div>
                  <p className="text-[10px] font-semibold uppercase text-zinc-500">Estado atual</p>
                  <h2 id="overall-status" className={`mt-1 text-xl font-semibold ${overall.text}`}>{overall.label}</h2>
                  <p className="mt-1 max-w-2xl text-sm text-zinc-600">{data?.message}</p>
                </div>
              </div>
              <div className="shrink-0 text-left text-xs text-zinc-500 sm:text-right">
                <strong className="block text-zinc-800">Última leitura</strong>
                {lastChecked ? lastChecked.toLocaleString('pt-BR') : 'Sem leitura'}
              </div>
            </div>
          </section>
        )}

        <section className="overflow-hidden rounded-lg border bg-white" aria-labelledby="components-heading">
          <div className="flex items-center justify-between border-b px-5 py-4">
            <div>
              <h2 id="components-heading" className="font-semibold">Componentes verificados</h2>
              <p className="mt-1 text-xs text-zinc-500">Leitura direta do runtime, sem chamadas externas de teste.</p>
            </div>
            <Activity className="h-5 w-5 text-[#a77d12]" />
          </div>
          {loading ? (
            Array.from({ length: 5 }).map((_, index) => <div key={index} className="h-16 animate-pulse border-b bg-zinc-50 last:border-0" />)
          ) : (
            Object.entries(data?.components ?? {}).map(([key, component]) => {
              const info = statusInfo(component.status);
              const definition = COMPONENTS[key] ?? { label: key, icon: ServerCog };
              const Icon = definition.icon;
              return (
                <article key={key} className="flex flex-col gap-3 border-b px-5 py-4 last:border-0 sm:flex-row sm:items-center sm:justify-between">
                  <div className="flex min-w-0 items-start gap-3">
                    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-md bg-zinc-100 text-zinc-700"><Icon className="h-4 w-4" /></span>
                    <div className="min-w-0">
                      <h3 className="text-sm font-medium">{definition.label}</h3>
                      {(component.note || component.error) && <p className="mt-1 text-xs text-zinc-500">{component.note ?? component.error}</p>}
                      {component.workers !== undefined && <p className="mt-1 text-xs text-zinc-500">{component.workers} worker(s) respondendo</p>}
                    </div>
                  </div>
                  <div className="flex items-center justify-between gap-3 sm:justify-end">
                    {component.latency_ms != null && <span className="font-mono text-xs text-zinc-500">{component.latency_ms} ms</span>}
                    <span className={`rounded-full px-2.5 py-1 text-[10px] font-semibold ${info.bg} ${info.text}`}>{info.label}</span>
                  </div>
                </article>
              );
            })
          )}
        </section>

        <section className="rounded-lg border bg-white p-5" aria-labelledby="history-heading">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h2 id="history-heading" className="font-semibold">Disponibilidade observada</h2>
              <p className="mt-1 text-xs text-zinc-500">Janela móvel de 30 dias, calculada somente sobre amostras persistidas.</p>
            </div>
            <div className="text-left sm:text-right">
              <strong className="text-lg">{history?.uptimePercent != null ? `${history.uptimePercent.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%` : 'Coletando'}</strong>
              <span className="block text-[10px] text-zinc-500">{history?.observations ?? 0} observação(ões) real(is)</span>
            </div>
          </div>
          <div className="mt-5 flex h-9 items-stretch gap-1" aria-label="Disponibilidade diária observada">
            {(history?.daily ?? Array.from({ length: 30 }, (_, index) => ({ date: String(index), status: 'no_data', observations: 0, uptimePercent: null }))).map((day) => (
              <span
                key={day.date}
                className={`min-w-0 flex-1 rounded-sm ${dayBarClass(day.status)}`}
                title={`${day.date}: ${day.observations ? `${statusInfo(day.status).label}, ${day.observations} observação(ões)` : 'sem observações'}`}
              />
            ))}
          </div>
          <div className="mt-2 flex justify-between text-[10px] text-zinc-400"><span>30 dias atrás</span><span>Hoje</span></div>
          <div className="mt-4 grid gap-3 border-t pt-4 text-xs text-zinc-600 sm:grid-cols-3">
            <span><strong className="block text-zinc-900">Cobertura</strong>{history?.daysObserved ?? 0} dia(s) com amostra</span>
            <span><strong className="block text-zinc-900">Primeira amostra</strong>{formatDateTime(history?.firstObservedAt ?? null)}</span>
            <span><strong className="block text-zinc-900">Amostra mais recente</strong>{formatDateTime(history?.lastObservedAt ?? null)}</span>
          </div>
        </section>

        <section className="rounded-lg border bg-white p-5" aria-labelledby="incidents-heading">
          <div className="flex items-center justify-between">
            <div>
              <h2 id="incidents-heading" className="font-semibold">Incidentes observados</h2>
              <p className="mt-1 text-xs text-zinc-500">Agrupados a partir de estados degradados ou indeterminados persistidos.</p>
            </div>
            <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-[10px] font-semibold">{history?.incidents.length ?? 0}</span>
          </div>
          {history?.incidents.length ? (
            <div className="mt-4 divide-y rounded-lg border">
              {history.incidents.map((incident) => (
                <article key={`${incident.startedAt}-${incident.status}`} className="grid gap-2 p-4 text-xs sm:grid-cols-[1fr_auto]">
                  <div>
                    <strong className="text-sm text-red-800">{incident.ongoing ? 'Em observação' : 'Recuperado'}</strong>
                    <p className="mt-1 text-zinc-500">{formatDateTime(incident.startedAt)} até {incident.ongoing ? 'agora' : formatDateTime(incident.endedAt)}</p>
                    <p className="mt-1 text-zinc-500">Componentes: {incident.components.map((key) => COMPONENTS[key]?.label ?? key).join(', ') || 'não identificado'}</p>
                  </div>
                  <span className="self-start rounded-full bg-red-50 px-2.5 py-1 text-[10px] font-semibold text-red-800">{incident.observations} amostra(s)</span>
                </article>
              ))}
            </div>
          ) : (
            <div className="mt-4 rounded-lg border border-dashed p-6 text-center text-sm text-zinc-500">
              {history?.state === 'ready' ? `Nenhum incidente nas ${history.observations} observações disponíveis.` : 'O histórico será exibido após as primeiras observações reais.'}
            </div>
          )}
        </section>
        </div>
        )}

        <footer className="flex flex-col gap-1 pb-2 text-center text-[10px] text-zinc-400">
          <span>Atualização automática a cada 30 segundos</span>
          <span>Meu Executivo {data?.version ? `v${data.version}` : ''}</span>
        </footer>
      </div>
    </main>
  );
}
