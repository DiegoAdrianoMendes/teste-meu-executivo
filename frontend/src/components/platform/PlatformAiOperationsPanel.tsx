import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Activity, AlertTriangle, Loader2, RotateCw, Zap } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  fetchAiOperationsEvents,
  fetchAiOperationsSummary,
  type AiOperationsSummary,
} from '@/services/platform';

function usd(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return 'Indisponível';
  const n = typeof value === 'string' ? Number(value) : value;
  if (!Number.isFinite(n)) return 'Indisponível';
  return new Intl.NumberFormat('pt-BR', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(n as number);
}

function Kpi({
  label,
  value,
  alert = false,
}: {
  label: string;
  value: string;
  alert?: boolean;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
      <p
        className={`mt-1 text-2xl font-bold tabular-nums ${alert ? 'text-[#a53e3c] dark:text-red-400' : 'text-foreground'}`}
      >
        {value}
      </p>
    </div>
  );
}

export function PlatformAiOperationsPanel({ enabled = true }: { enabled?: boolean }) {
  const [onlyFallbacks, setOnlyFallbacks] = useState(true);
  const [eventPage, setEventPage] = useState(0);
  const eventPageSize = 12;

  const summaryQuery = useQuery({
    queryKey: ['platform-ai-operations', 'summary'],
    queryFn: () => fetchAiOperationsSummary(7),
    enabled,
    retry: false,
    refetchInterval: enabled ? 30_000 : false,
  });
  const eventsQuery = useQuery({
    queryKey: ['platform-ai-operations', 'events', onlyFallbacks, eventPage],
    queryFn: () => fetchAiOperationsEvents({
      days: 7,
      limit: eventPageSize,
      offset: eventPage * eventPageSize,
      onlyFallbacks,
    }),
    enabled,
    retry: false,
    refetchInterval: enabled ? 30_000 : false,
  });

  const summary: AiOperationsSummary | null = summaryQuery.data ?? null;
  const alerts = summary?.route_alerts ?? [];

  return (
    <Card data-testid="platform-ai-operations-panel">
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2 text-lg font-semibold">
            <Activity className="h-4 w-4 shrink-0" />
            Operação de IA
          </CardTitle>
          <CardDescription className="text-xs">
            Custo, rotas e fallback dos últimos 7 dias. Atualiza a cada 30s.
          </CardDescription>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="h-8 shrink-0 text-xs"
          onClick={() => {
            void summaryQuery.refetch();
            void eventsQuery.refetch();
          }}
        >
          <RotateCw className="mr-1.5 h-3.5 w-3.5" />
          Atualizar
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {summaryQuery.isPending ? (
          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Carregando operação de IA…
          </div>
        ) : summaryQuery.isError ? (
          <div className="rounded-lg border border-destructive/30 bg-card p-4 text-sm text-destructive">
            Não foi possível carregar a operação de IA. Nenhum dado alternativo será exibido.
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Kpi label="Custo (7d)" value={usd(summary?.cost_usd)} />
              <Kpi label="Execuções (7d)" value={(summary?.executions ?? 0).toLocaleString('pt-BR')} />
              <Kpi
                label="Taxa de fallback"
                value={`${Math.round((summary?.fallback_rate ?? 0) * 100)}%`}
                alert={(summary?.fallback_rate ?? 0) > 0.25}
              />
              <Kpi
                label="Rotas com falha (24h)"
                value={String(alerts.length)}
                alert={alerts.length > 0}
              />
            </div>

            {alerts.length > 0 && (
              <div className="rounded-lg border border-[#a53e3c]/30 bg-[#a53e3c]/5 p-3">
                <p className="flex items-center gap-1.5 text-xs font-semibold text-[#a53e3c] dark:text-red-400">
                  <AlertTriangle className="h-3.5 w-3.5" /> Rotas com falha recente
                </p>
                <div className="mt-2 space-y-1">
                  {alerts.slice(0, 6).map((a) => (
                    <div
                      key={a.route_key}
                      className="flex items-center justify-between gap-2 text-xs"
                    >
                      <span className="min-w-0 truncate font-medium">{a.route_key}</span>
                      <span className="shrink-0 tabular-nums text-muted-foreground">
                        {a.failures} falha(s)
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Custo por classe × provedor
              </p>
              <div className="overflow-x-auto rounded-lg border border-border">
                <table className="w-full text-sm">
                  <thead className="bg-secondary/40 text-2xs uppercase tracking-wide text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2 text-left">Classe</th>
                      <th className="px-3 py-2 text-left">Provedor</th>
                      <th className="px-3 py-2 text-right">Execuções</th>
                      <th className="px-3 py-2 text-right">Fallback</th>
                      <th className="px-3 py-2 text-right">Custo</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(summary?.by_class_provider ?? []).map((r) => (
                      <tr key={`${r.task_class}:${r.provider}`} className="border-t border-border">
                        <td className="px-3 py-2">{r.task_class}</td>
                        <td className="px-3 py-2">{r.provider}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{r.executions}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{r.fallback_count}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{usd(r.cost_usd)}</td>
                      </tr>
                    ))}
                    {(summary?.by_class_provider ?? []).length === 0 && (
                      <tr>
                        <td colSpan={5} className="px-3 py-4 text-center text-xs text-muted-foreground">
                          Nenhuma execução de IA registrada neste período.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-2xs text-muted-foreground">
                Para trocar o modelo ou a ordem de fallback de uma classe, use o painel
                "Modelos por tarefa" acima.
              </p>
            </div>

            <div>
              <div className="mb-2 flex items-center justify-between gap-2">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Eventos do gateway
                </p>
                <Button
                  variant={onlyFallbacks ? 'default' : 'outline'}
                  size="sm"
                  className="h-7 text-2xs"
                  onClick={() => {
                    setEventPage(0);
                    setOnlyFallbacks((v) => !v);
                  }}
                >
                  <Zap className="mr-1 h-3 w-3" />
                  {onlyFallbacks ? 'Só fallbacks' : 'Todos'}
                </Button>
              </div>
              <div className="space-y-1">
                {(eventsQuery.data?.items ?? []).map((e) => (
                  <div
                    key={e.id}
                    className="flex items-center justify-between gap-2 rounded-md border border-border px-3 py-1.5 text-xs"
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      {e.fallback_used && (
                        <Badge variant="outline" className="shrink-0 text-2xs">
                          fallback
                        </Badge>
                      )}
                      <span className="min-w-0 truncate font-medium">{e.route_key}</span>
                    </div>
                    <div className="flex shrink-0 items-center gap-3 tabular-nums text-muted-foreground">
                      <span className="hidden sm:inline">{e.agent_name}</span>
                      <span>{usd(e.cost_usd ?? e.estimated_cost_usd)}</span>
                    </div>
                  </div>
                ))}
                {(eventsQuery.data?.items ?? []).length === 0 && !eventsQuery.isPending && (
                  <p className="py-3 text-center text-xs text-muted-foreground">
                    Nenhum evento{onlyFallbacks ? ' de fallback' : ''} neste período.
                  </p>
                )}
              </div>
              {eventsQuery.data && (
                <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-2xs text-muted-foreground">
                  <span>
                    {eventsQuery.data.total === 0
                      ? '0 eventos'
                      : `${eventsQuery.data.offset + 1}–${eventsQuery.data.offset + eventsQuery.data.items.length} de ${eventsQuery.data.total}`}
                  </span>
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-7 text-2xs"
                      disabled={eventPage === 0 || eventsQuery.isFetching}
                      onClick={() => setEventPage((page) => Math.max(0, page - 1))}
                    >
                      Anterior
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-7 text-2xs"
                      disabled={!eventsQuery.data.hasMore || eventsQuery.isFetching}
                      onClick={() => setEventPage((page) => page + 1)}
                    >
                      Próxima
                    </Button>
                  </div>
                </div>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
