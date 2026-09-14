import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowRight,
  BarChart3,
  CheckCircle2,
  Clock3,
  DatabaseZap,
  RefreshCw,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { useAppStore } from '@/store/app-context';
import {
  blackboxRoleProfile,
  blackboxRoleWorkstreams,
  visibleBlackboxSections,
} from '@/blackbox/access';
import { isPlatformRoot } from '@/lib/platform-access';
import {
  fetchPlatformDashboardHome,
  savePlatformDashboardPreference,
  type PlatformDashboardMetric,
  type PlatformDashboardScope,
} from '@/services/platform-dashboard';

const QUALITY_LABELS: Record<string, string> = {
  updated: 'Atualizado',
  partial: 'Parcial',
  estimated: 'Estimado',
  stale: 'Desatualizado',
  unavailable: 'Indisponível',
  insufficient_data: 'Dados insuficientes',
};

function formatMetric(metric: PlatformDashboardMetric): string {
  if (metric.value === null) return '—';
  if (metric.unit === 'currency_brl') {
    return new Intl.NumberFormat('pt-BR', {
      style: 'currency',
      currency: 'BRL',
      maximumFractionDigits: 0,
    }).format(metric.value);
  }
  if (metric.unit === 'percentage') return `${metric.value.toLocaleString('pt-BR')}%`;
  return metric.value.toLocaleString('pt-BR');
}

function DashboardSkeleton() {
  return (
    <div className="mx-auto max-w-[1480px] space-y-6 p-6 lg:p-8" aria-label="Carregando painel">
      <Skeleton className="h-48 w-full rounded-xl" />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <Skeleton key={index} className="h-36 rounded-xl" />
        ))}
      </div>
      <div className="grid gap-5 xl:grid-cols-2">
        <Skeleton className="h-72 rounded-xl" />
        <Skeleton className="h-72 rounded-xl" />
      </div>
    </div>
  );
}

export default function BlackboxRoleHome() {
  const { state } = useAppStore();
  const currentUser = state.currentUser;
  const root = isPlatformRoot(currentUser);
  const operationalAdmin = currentUser?.platformRolePackage === 'platform_admin';
  const [scope, setScope] = useState<PlatformDashboardScope>(
    root || operationalAdmin ? 'platform' : 'mine',
  );
  const [customizing, setCustomizing] = useState(false);
  const queryClient = useQueryClient();
  const profile = blackboxRoleProfile(currentUser);
  const workstreams = blackboxRoleWorkstreams(currentUser);
  const sections = visibleBlackboxSections(currentUser).filter((item) => item.path !== '/blackbox');
  const query = useQuery({
    queryKey: ['platform-dashboard-home', scope, 30, currentUser?.id],
    queryFn: () => fetchPlatformDashboardHome(scope, 30),
    enabled: Boolean(currentUser),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
  const preferenceMutation = useMutation({
    mutationFn: (hiddenWidgets: string[]) => {
      if (!query.data) throw new Error('Painel ainda não carregado.');
      return savePlatformDashboardPreference({
        templateKey: query.data.layout.templateKey,
        templateVersion: query.data.layout.templateVersion,
        hiddenWidgets,
        widgetOrder: query.data.layout.widgetOrder,
        etag: query.data.layout.preferenceEtag,
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['platform-dashboard-home'] });
    },
  });

  if (query.isLoading) return <DashboardSkeleton />;

  if (query.isError || !query.data) {
    return (
      <div className="mx-auto max-w-[920px] p-6 lg:p-8">
        <Card className="border-destructive/30" role="alert">
          <CardContent className="flex flex-col items-start gap-4 p-6">
            <span className="grid h-11 w-11 place-items-center rounded-xl bg-destructive/10 text-destructive">
              <AlertTriangle className="h-5 w-5" />
            </span>
            <div>
              <h1 className="text-lg font-semibold">Não foi possível carregar o painel operacional</h1>
              <p className="mt-1 text-sm text-muted-foreground">
                A falha não foi convertida em zeros. Tente atualizar a leitura das fontes.
              </p>
            </div>
            <Button variant="outline" onClick={() => query.refetch()}>
              <RefreshCw className="mr-2 h-4 w-4" /> Atualizar painel
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  const dashboard = query.data;
  const hiddenWidgets = new Set(dashboard.layout.hiddenWidgets);
  const partial = dashboard.metrics.some((metric) => metric.value === null)
    || dashboard.sourceHealth.some((source) => source.status !== 'available');

  return (
    <div className="mx-auto max-w-[1480px] space-y-6 p-6 lg:p-8">
      <section className="relative overflow-hidden rounded-xl border border-primary/20 bg-card px-6 py-7 shadow-[var(--shadow-card)] lg:px-8">
        <div className="absolute -right-20 -top-24 h-64 w-64 rounded-full bg-primary/10 blur-3xl" />
        <div className="relative flex flex-wrap items-start justify-between gap-6">
          <div className="max-w-3xl">
            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-primary">
              Painel operacional · últimos {dashboard.periodDays} dias
            </p>
            <h1 className="mt-2 text-2xl font-semibold tracking-tight lg:text-3xl">
              {dashboard.roleLabel}
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
              {profile.mission}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" size="sm" variant="outline" onClick={() => setCustomizing((value) => !value)}>
              <SlidersHorizontal className="mr-2 h-3.5 w-3.5" /> Personalizar
            </Button>
            {dashboard.availableScopes.length > 1 && (
              <div className="flex rounded-lg border bg-muted/30 p-1" aria-label="Escopo do painel">
                {dashboard.availableScopes.map((availableScope) => (
                  <Button
                    key={availableScope}
                    type="button"
                    size="sm"
                    variant={scope === availableScope ? 'secondary' : 'ghost'}
                    onClick={() => setScope(availableScope)}
                  >
                    {availableScope === 'mine'
                      ? 'Meu escopo'
                      : availableScope === 'team'
                        ? dashboard.teamName ?? 'Minha equipe'
                        : 'Plataforma'}
                  </Button>
                ))}
              </div>
            )}
            <Badge variant="outline" className="gap-1.5 border-primary/20 bg-primary/5 text-primary">
              <ShieldCheck className="h-3.5 w-3.5" />
              {dashboard.isPlatformRoot ? 'Super Admin · visão integral' : 'Acesso por função'}
            </Badge>
          </div>
        </div>
        <div className="relative mt-6 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1.5">
            <Clock3 className="h-3.5 w-3.5" />
            Atualizado {new Date(dashboard.generatedAt).toLocaleString('pt-BR')}
          </span>
          {partial ? (
            <Badge variant="outline" className="border-amber-300 bg-amber-50 text-amber-800">
              Leitura parcial · sem zeros fictícios
            </Badge>
          ) : (
            <Badge variant="outline" className="border-emerald-300 bg-emerald-50 text-emerald-800">
              <CheckCircle2 className="mr-1 h-3.5 w-3.5" /> Fontes consultadas
            </Badge>
          )}
        </div>
      </section>

      {customizing ? (
        <Card className="border-primary/20">
          <CardContent className="flex flex-wrap items-center gap-2 p-4">
            <span className="mr-2 text-xs font-semibold">Widgets opcionais</span>
            {dashboard.layout.optionalWidgets.map((widget) => {
              const hidden = hiddenWidgets.has(widget);
              const label = {
                quick_actions: 'Ações rápidas',
                source_health: 'Saúde das fontes',
                authorized_modules: 'Módulos liberados',
              }[widget] ?? widget;
              return (
                <Button
                  key={widget}
                  type="button"
                  size="sm"
                  variant={hidden ? 'outline' : 'secondary'}
                  disabled={preferenceMutation.isPending}
                  onClick={() => preferenceMutation.mutate(
                    hidden
                      ? dashboard.layout.hiddenWidgets.filter((key) => key !== widget)
                      : [...dashboard.layout.hiddenWidgets, widget],
                  )}
                >
                  {hidden ? `Mostrar ${label}` : `Ocultar ${label}`}
                </Button>
              );
            })}
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={preferenceMutation.isPending || dashboard.layout.hiddenWidgets.length === 0}
              onClick={() => preferenceMutation.mutate([])}
            >
              Restaurar padrão
            </Button>
          </CardContent>
        </Card>
      ) : null}

      <section aria-labelledby="dashboard-metrics-heading">
        <div className="mb-3 flex items-end justify-between gap-4">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-primary">Indicadores reais</p>
            <h2 id="dashboard-metrics-heading" className="mt-1 text-lg font-semibold">Prioridades da função</h2>
          </div>
          <BarChart3 className="h-5 w-5 text-muted-foreground" />
        </div>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {dashboard.metrics.map((metric) => (
            <Card key={metric.key} className={metric.value === null ? 'border-amber-200' : ''}>
              <CardContent className="p-5">
                <div className="flex items-start justify-between gap-3">
                  <p className="text-xs font-semibold text-muted-foreground">{metric.label}</p>
                  <Badge variant="outline" className="text-[9px]">
                    {QUALITY_LABELS[metric.quality] ?? metric.quality}
                  </Badge>
                </div>
                <p className="mt-3 text-2xl font-semibold tabular-nums">{formatMetric(metric)}</p>
                <p className="mt-2 min-h-10 text-[11px] leading-5 text-muted-foreground">
                  {metric.description}
                </p>
                <div className="mt-3 flex items-center justify-between border-t pt-3 text-[10px] text-muted-foreground">
                  <span>Fonte: {metric.source}</span>
                  {metric.href && (
                    <Link to={metric.href} className="inline-flex items-center gap-1 font-semibold text-primary">
                      Detalhar <ArrowRight className="h-3 w-3" />
                    </Link>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.3fr)_minmax(360px,0.7fr)]">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-sm">
              <Sparkles className="h-4 w-4 text-primary" /> Fila prioritária
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {dashboard.workItems.length === 0 ? (
              <div className="rounded-lg border border-dashed p-5 text-center">
                <CheckCircle2 className="mx-auto h-5 w-5 text-emerald-600" />
                <p className="mt-2 text-sm font-medium">Nenhum item disponível nesta fila</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Isso não substitui uma fonte indisponível; consulte a saúde abaixo.
                </p>
              </div>
            ) : dashboard.workItems.map((item) => (
              <Link key={`${item.sourceType}:${item.id}`} to={item.href} className="group flex items-center gap-3 rounded-lg border p-3 transition-colors hover:bg-muted/50">
                <Badge variant="outline" className={item.priority === 'P0' ? 'border-red-300 bg-red-50 text-red-700' : ''}>
                  {item.priority}
                </Badge>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-semibold">{item.safeTitle}</span>
                  <span className="mt-0.5 block truncate text-[10px] text-muted-foreground">{item.safeSummary}</span>
                </span>
                <ArrowRight className="h-3.5 w-3.5 text-muted-foreground group-hover:text-primary" />
              </Link>
            ))}
          </CardContent>
        </Card>

        <div className="space-y-5">
          {!hiddenWidgets.has('quick_actions') ? <Card>
            <CardHeader className="pb-3"><CardTitle className="text-sm">Alertas</CardTitle></CardHeader>
            <CardContent className="space-y-2">
              {dashboard.alerts.length === 0 ? (
                <p className="rounded-lg bg-emerald-50 p-3 text-xs text-emerald-800">Sem alertas confirmados pelas fontes consultadas.</p>
              ) : dashboard.alerts.map((alert) => {
                const content = (
                  <div className="rounded-lg border p-3">
                    <p className="text-xs font-semibold">{alert.title}</p>
                    <p className="mt-1 text-[11px] leading-5 text-muted-foreground">{alert.description}</p>
                  </div>
                );
                return alert.href ? <Link key={alert.id} to={alert.href}>{content}</Link> : <div key={alert.id}>{content}</div>;
              })}
            </CardContent>
          </Card> : null}

          <Card>
            <CardHeader className="pb-3"><CardTitle className="text-sm">Ações autorizadas</CardTitle></CardHeader>
            <CardContent className="grid gap-2">
              {dashboard.actions.map((action) => (
                <Button key={action.key} asChild variant="outline" className="justify-between">
                  <Link to={action.href}>{action.label}<ArrowRight className="h-3.5 w-3.5" /></Link>
                </Button>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        {!hiddenWidgets.has('source_health') ? <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-sm"><DatabaseZap className="h-4 w-4 text-primary" /> Saúde das fontes</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {dashboard.sourceHealth.map((source) => (
              <div key={source.key} className="flex items-start justify-between gap-4 rounded-lg border px-3 py-2.5">
                <span>
                  <span className="block text-xs font-semibold">{source.label}</span>
                  {source.detail && <span className="mt-0.5 block text-[10px] text-muted-foreground">{source.detail}</span>}
                </span>
                <Badge variant="outline" className={source.status === 'unavailable' ? 'border-amber-300 text-amber-800' : 'border-emerald-300 text-emerald-800'}>
                  {source.status === 'available' ? 'Disponível' : source.status === 'partial' ? 'Parcial' : 'Indisponível'}
                </Badge>
              </div>
            ))}
          </CardContent>
        </Card> : null}

        {!hiddenWidgets.has('authorized_modules') ? <Card>
          <CardHeader className="pb-3"><CardTitle className="text-sm">Frentes e módulos liberados</CardTitle></CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              {workstreams.map((stream) => (
                <Button key={`${stream.capability}:${stream.path}`} asChild variant="secondary" size="sm">
                  <Link to={stream.path}>{stream.title}</Link>
                </Button>
              ))}
            </div>
            <p className="mt-4 text-xs leading-5 text-muted-foreground">
              {sections.length} módulos visíveis. {profile.guardrail}
            </p>
          </CardContent>
        </Card> : null}
      </div>
    </div>
  );
}
