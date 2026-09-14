/**
 * Painel "Saúde Meta & Funil" — Sprint "Super Admin enxerga tudo" (diretriz 7
 * do PLANO_META_MAXIMO_2026-07-23.md).
 *
 * Dá vida aos 3 endpoints de plataforma órfãos (mrr, token-usage, aws-costs,
 * antes só usados via /overview) e expõe o novo GET /platform/meta-health:
 * campanhas pausadas por política com motivo legível, top erros Meta por
 * código, funil de aquisição (24h/7d) e opt-outs por motivo.
 */
import { useQuery } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import {
  fetchPlatformAwsCosts,
  fetchPlatformMetaHealth,
  fetchPlatformMrr,
  fetchPlatformTokenUsage,
} from '@/services/platform';
import {
  AlertTriangle,
  Ban,
  Cloud,
  Coins,
  DollarSign,
  Filter,
  PauseCircle,
  ShieldOff,
  TrendingUp,
} from 'lucide-react';

function fmtCurrency(value?: number | null, currency = 'BRL') {
  if (value == null) return '—';
  return new Intl.NumberFormat(currency === 'BRL' ? 'pt-BR' : 'en-US', {
    style: 'currency',
    currency,
    maximumFractionDigits: 2,
  }).format(value);
}

function fmtDateTime(value?: string | null) {
  if (!value) return '—';
  try {
    return new Date(value).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
  } catch {
    return value;
  }
}

function EmptyState({
  icon: Icon,
  title,
  description,
}: {
  icon: React.ElementType;
  title: string;
  description: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-8 text-center">
      <Icon className="h-8 w-8 text-muted-foreground/60" />
      <p className="text-sm font-medium">{title}</p>
      <p className="text-xs text-muted-foreground max-w-sm">{description}</p>
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
      <AlertTriangle className="h-4 w-4 shrink-0" />
      <span>{message}</span>
    </div>
  );
}

function MiniKpi({
  title,
  value,
  sub,
  icon: Icon,
  loading,
  error,
}: {
  title: string;
  value: string;
  sub?: string;
  icon: React.ElementType;
  loading?: boolean;
  error?: boolean;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-xs font-medium text-muted-foreground">{title}</CardTitle>
        <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-8 w-32" />
        ) : error ? (
          <p className="text-sm text-destructive">Métrica indisponível.</p>
        ) : (
          <>
            <p className="text-2xl font-bold tabular-nums">{value}</p>
            {sub && <p className="text-xs text-muted-foreground mt-1">{sub}</p>}
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function PlatformMetaHealthPanel({ enabled }: { enabled: boolean }) {
  const mrrQuery = useQuery({
    queryKey: ['platform', 'mrr'],
    queryFn: fetchPlatformMrr,
    staleTime: 30_000,
    enabled,
    retry: false,
  });
  const tokenUsageQuery = useQuery({
    queryKey: ['platform', 'token-usage', 30],
    queryFn: () => fetchPlatformTokenUsage(30),
    staleTime: 30_000,
    enabled,
    retry: false,
  });
  const awsCostsQuery = useQuery({
    queryKey: ['platform', 'aws-costs'],
    queryFn: fetchPlatformAwsCosts,
    staleTime: 30_000,
    enabled,
    retry: false,
  });
  const metaHealthQuery = useQuery({
    queryKey: ['platform', 'meta-health', 7],
    queryFn: () => fetchPlatformMetaHealth(7),
    staleTime: 15_000,
    enabled,
    retry: false,
  });

  const mrr = mrrQuery.data ?? {};
  const tokenUsage = tokenUsageQuery.data ?? {};
  const aws = awsCostsQuery.data ?? {};
  const metaHealth = metaHealthQuery.data;

  const pausedCampaigns = metaHealth?.paused_campaigns ?? [];
  const topErrors = metaHealth?.top_meta_errors ?? [];
  const funnel24h = metaHealth?.funnel_last_24h ?? [];
  const funnel7d = metaHealth?.funnel_last_7d ?? [];
  const suppressions = metaHealth?.suppressions_by_reason ?? [];
  const dataNotes: string[] = metaHealth?.data_notes ?? [];

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">Saúde Meta &amp; Funil</h2>
        <p className="text-sm text-muted-foreground">
          Tudo que o motor de campanhas e o funil de aquisição fazem, visível sem acessar o banco.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <MiniKpi
          title="MRR da plataforma"
          value={mrrQuery.isLoading ? '—' : fmtCurrency((mrr.total_mrr_cents ?? 0) / 100)}
          sub={`ARR: ${fmtCurrency((mrr.total_arr_cents ?? (mrr.total_mrr_cents ?? 0) * 12) / 100)}`}
          icon={DollarSign}
          loading={mrrQuery.isLoading}
          error={mrrQuery.isError}
        />
        <MiniKpi
          title="Consumo de tokens IA (30d)"
          value={tokenUsageQuery.isLoading ? '—' : fmtCurrency(tokenUsage.total_cost_usd ?? 0, 'USD')}
          sub={`${tokenUsage.total_runs ?? 0} execuções · ${(tokenUsage.total_tokens ?? 0).toLocaleString('pt-BR')} tokens`}
          icon={Coins}
          loading={tokenUsageQuery.isLoading}
          error={tokenUsageQuery.isError}
        />
        <MiniKpi
          title="Custos AWS (30d)"
          value={awsCostsQuery.isLoading ? '—' : aws.source === 'estimate' ? 'Estimado' : fmtCurrency(aws.total_usd, 'USD')}
          sub={aws.source === 'estimate' ? 'Cost Explorer não conectado' : 'Fonte: AWS Cost Explorer'}
          icon={Cloud}
          loading={awsCostsQuery.isLoading}
          error={awsCostsQuery.isError}
        />
      </div>

      {metaHealthQuery.isError && (
        <ErrorState message="Não foi possível carregar a saúde Meta agora. Tente novamente em instantes." />
      )}

      {dataNotes.length > 0 && (
        <div className="rounded-lg border border-amber-300/40 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-500/30 dark:bg-amber-950/30 dark:text-amber-200">
          {dataNotes.map((note) => (
            <p key={note}>{note}</p>
          ))}
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <PauseCircle className="h-4 w-4 text-muted-foreground" />
              Campanhas pausadas por política
            </CardTitle>
            <CardDescription>Kill switch automático e política de erro Meta, com motivo legível.</CardDescription>
          </CardHeader>
          <CardContent>
            {metaHealthQuery.isLoading ? (
              <div className="space-y-2">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </div>
            ) : pausedCampaigns.length === 0 ? (
              <EmptyState
                icon={PauseCircle}
                title="Nenhuma campanha pausada"
                description="Nenhum kill switch ou política de erro Meta pausou campanhas no momento."
              />
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Campanha</TableHead>
                      <TableHead>Motivo</TableHead>
                      <TableHead>Código</TableHead>
                      <TableHead>Pausada em</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {pausedCampaigns.map((item: any) => (
                      <TableRow key={item.campaign_id}>
                        <TableCell className="max-w-[180px] truncate font-medium">{item.title}</TableCell>
                        <TableCell className="max-w-[280px] text-xs text-muted-foreground">
                          {item.pause_reason ?? 'Motivo não registrado.'}
                        </TableCell>
                        <TableCell>
                          {item.provider_code != null ? (
                            <Badge variant="outline">{item.provider_code}</Badge>
                          ) : (
                            '—'
                          )}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">{fmtDateTime(item.paused_at)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-muted-foreground" />
              Top erros Meta
            </CardTitle>
            <CardDescription>Códigos de provedor mais frequentes entre destinatários com falha (7 dias).</CardDescription>
          </CardHeader>
          <CardContent>
            {metaHealthQuery.isLoading ? (
              <div className="space-y-2">
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-full" />
              </div>
            ) : topErrors.length === 0 ? (
              <EmptyState
                icon={ShieldOff}
                title="Nenhum erro Meta recente"
                description="Nenhum destinatário falhou com código de erro classificado nos últimos 7 dias."
              />
            ) : (
              <div className="space-y-2">
                {topErrors.map((row: any) => (
                  <div key={row.provider_code} className="flex items-center justify-between rounded-lg border p-3">
                    <div className="flex items-center gap-2">
                      <Badge variant="destructive">{row.provider_code}</Badge>
                      <span className="text-xs text-muted-foreground max-w-[260px] truncate">{row.sample_message}</span>
                    </div>
                    <span className="text-sm font-semibold tabular-nums">{row.count}</span>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <TrendingUp className="h-4 w-4 text-muted-foreground" />
              Funil de eventos de aquisição
            </CardTitle>
            <CardDescription>Marcos de funil emitidos pelo tracking, últimas 24h e 7 dias.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2">
            <div>
              <p className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">Últimas 24h</p>
              {metaHealthQuery.isLoading ? (
                <Skeleton className="h-16 w-full" />
              ) : funnel24h.length === 0 ? (
                <EmptyState
                  icon={Filter}
                  title="Sem eventos nas últimas 24h"
                  description="Nenhum marco de funil (AcquisitionEvent) emitido no período."
                />
              ) : (
                <div className="space-y-1.5">
                  {funnel24h.map((row: any) => (
                    <div key={row.event_name} className="flex items-center justify-between text-sm">
                      <span>{row.event_name}</span>
                      <Badge variant="secondary">{row.count}</Badge>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div>
              <p className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">Últimos 7 dias</p>
              {metaHealthQuery.isLoading ? (
                <Skeleton className="h-16 w-full" />
              ) : funnel7d.length === 0 ? (
                <EmptyState
                  icon={Filter}
                  title="Sem eventos nos últimos 7 dias"
                  description="Nenhum marco de funil (AcquisitionEvent) emitido no período."
                />
              ) : (
                <div className="space-y-1.5">
                  {funnel7d.map((row: any) => (
                    <div key={row.event_name} className="flex items-center justify-between text-sm">
                      <span>{row.event_name}</span>
                      <Badge variant="secondary">{row.count}</Badge>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <Ban className="h-4 w-4 text-muted-foreground" />
              Opt-outs e supressões
            </CardTitle>
            <CardDescription>Contatos suprimidos de disparo, agrupados por motivo.</CardDescription>
          </CardHeader>
          <CardContent>
            {metaHealthQuery.isLoading ? (
              <Skeleton className="h-16 w-full" />
            ) : suppressions.length === 0 ? (
              <EmptyState
                icon={Ban}
                title="Nenhuma supressão ativa"
                description="Nenhum contato foi suprimido por opt-out ou política até agora."
              />
            ) : (
              <div className="space-y-1.5">
                {suppressions.map((row: any) => (
                  <div key={row.reason} className="flex items-center justify-between text-sm">
                    <span>{row.reason}</span>
                    <Badge variant="outline">{row.count}</Badge>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
