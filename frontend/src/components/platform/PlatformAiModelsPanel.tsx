import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Cpu, KeyRound, Loader2, RotateCw } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  AI_PROVIDER_LABELS,
  AI_TASK_CLASS_LABELS,
  AI_TASK_CLASS_ORDER,
} from '@/components/admin/AiModelRoutingPanel';
import { fetchPlatformAiModels } from '@/services/platform';

function providerLabel(provider: string): string {
  return AI_PROVIDER_LABELS[provider] ?? provider;
}

function formatUsdPerMillion(value: number): string {
  return new Intl.NumberFormat('pt-BR', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value || 0);
}

export function PlatformAiModelsPanel({ enabled = true }: { enabled?: boolean }) {
  const overviewQuery = useQuery({
    queryKey: ['platform-ai-models'],
    queryFn: fetchPlatformAiModels,
    staleTime: 30_000,
    enabled,
    retry: false,
  });
  const overview = overviewQuery.data ?? null;
  const orderedDefaults = [...(overview?.defaults ?? [])].sort(
    (a, b) => AI_TASK_CLASS_ORDER.indexOf(a.taskClass) - AI_TASK_CLASS_ORDER.indexOf(b.taskClass),
  );

  return (
    <Card data-testid="platform-ai-models-panel">
      <CardHeader className="gap-1">
        <CardTitle className="flex items-center gap-2 text-base">
          <Cpu className="h-4 w-4 text-primary" />Modelos e chaves por workspace
        </CardTitle>
        <CardDescription>
          Defaults globais do catálogo por classe de tarefa e contas que usam chave própria (BYOK).
          As chaves nunca aparecem — somente a máscara com os últimos 4 caracteres.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {overviewQuery.isLoading ? (
          <div className="flex min-h-28 items-center justify-center text-sm text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />Carregando defaults e BYOK
          </div>
        ) : overviewQuery.isError ? (
          <div className="flex min-h-28 flex-col items-center justify-center gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-center">
            <AlertTriangle className="h-5 w-5 text-destructive" />
            <p className="text-sm font-semibold">Não foi possível carregar a visão de modelos</p>
            <Button type="button" variant="outline" size="sm" onClick={() => { void overviewQuery.refetch(); }}>
              <RotateCw className="mr-2 h-3.5 w-3.5" />Tentar novamente
            </Button>
          </div>
        ) : !overview ? (
          <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
            Nenhuma visão de modelos disponível.
          </div>
        ) : (
          <>
            <div className="rounded-lg border p-4">
              <p className="text-sm font-semibold">Defaults globais por classe</p>
              <p className="mt-0.5 text-xs text-muted-foreground">Catálogo {overview.catalogVersion}</p>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {orderedDefaults.map((item) => (
                  <div key={item.taskClass} className="flex items-start justify-between gap-3 rounded-lg bg-muted px-3 py-2">
                    <div className="min-w-0">
                      <p className="text-sm font-medium">{AI_TASK_CLASS_LABELS[item.taskClass] ?? item.taskClass}</p>
                      <p className="truncate text-xs text-muted-foreground">
                        {item.model ? `${providerLabel(item.provider ?? '')} · ${item.model}` : 'Sem rota publicada no catálogo'}
                      </p>
                    </div>
                    <span className="shrink-0 text-right text-xs text-muted-foreground tabular-nums">
                      {item.price
                        ? `${formatUsdPerMillion(item.price.inputPerMillionUsd)} / ${formatUsdPerMillion(item.price.outputPerMillionUsd)} por 1M`
                        : '—'}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-lg border p-4">
              <div className="flex items-center gap-2">
                <KeyRound className="h-4 w-4 text-primary" />
                <p className="text-sm font-semibold">Workspaces com chave própria ou rota customizada</p>
              </div>
              {overview.byokWorkspaces.length ? (
                <div className="mt-3 space-y-2">
                  {overview.byokWorkspaces.map((workspace) => (
                    <div key={workspace.workspaceId} className="rounded-lg border p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="text-sm font-medium">{workspace.workspaceName}</p>
                        <div className="flex flex-wrap gap-1.5">
                          {workspace.providers.map((provider) => (
                            <Badge key={provider.provider} variant="outline" className="text-2xs font-medium">
                              {providerLabel(provider.provider)} · {provider.apiKeyMasked}
                            </Badge>
                          ))}
                        </div>
                      </div>
                      {workspace.customRouteClasses.length > 0 && (
                        <p className="mt-1.5 text-xs text-muted-foreground">
                          Rotas customizadas: {workspace.customRouteClasses
                            .map((taskClass) => AI_TASK_CLASS_LABELS[taskClass] ?? taskClass)
                            .join(', ')}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="mt-3 rounded-lg border border-dashed p-3 text-xs text-muted-foreground">
                  Nenhum workspace usa chave própria — todas as contas operam com a chave da plataforma.
                </p>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
