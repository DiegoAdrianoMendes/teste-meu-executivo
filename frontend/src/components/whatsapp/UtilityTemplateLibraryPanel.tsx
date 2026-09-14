import { useEffect, useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, Clock3, RefreshCw, ShieldCheck, XCircle } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useToast } from '@/hooks/use-toast';
import {
  fetchUtilityTemplateCatalog,
  retryUtilityTemplateCatalogItem,
} from '@/services/campaigns';
import {
  fetchPlatformWorkspaceUtilityTemplateCatalog,
  retryPlatformWorkspaceUtilityTemplate,
} from '@/services/platform';
import type { UtilityTemplateCatalogItem } from '@/types';

const STATUS: Record<string, { label: string; className: string; icon: typeof Clock3 }> = {
  approved: { label: 'Aprovado pela Meta', className: 'border-emerald-300 text-emerald-800', icon: CheckCircle2 },
  pending: { label: 'Em análise na Meta', className: 'border-amber-300 text-amber-800', icon: Clock3 },
  rejected: { label: 'Rejeitado pela Meta', className: 'border-red-300 text-red-800', icon: XCircle },
  catalog_queued: { label: 'Enfileirado', className: 'border-blue-300 text-blue-800', icon: Clock3 },
  catalog_pending_gate: { label: 'Pendente: ambiente bloqueado', className: 'border-zinc-300 text-zinc-700', icon: AlertTriangle },
  catalog_failed: { label: 'Falha no provisionamento', className: 'border-red-300 text-red-800', icon: XCircle },
  catalog_name_reserved: { label: 'Nome reservado pela Meta', className: 'border-red-300 text-red-800', icon: XCircle },
  name_in_use: { label: 'Nome já utilizado nesta WABA', className: 'border-red-300 text-red-800', icon: XCircle },
  not_prepared: { label: 'Ainda não preparado', className: 'border-zinc-300 text-zinc-700', icon: Clock3 },
};

function statusMeta(item: UtilityTemplateCatalogItem) {
  return STATUS[item.provisioningStatus] ?? {
    label: item.provisioningStatus,
    className: 'border-zinc-300 text-zinc-700',
    icon: Clock3,
  };
}

function humanError(error: unknown): string {
  const data = (error as { data?: { message?: string } })?.data;
  return data?.message || (error instanceof Error ? error.message : 'Falha não identificada.');
}

interface UtilityTemplateLibraryPanelProps {
  surface?: 'workspace' | 'blackbox';
  workspaceId?: string | null;
  canRetry?: boolean;
}

export function UtilityTemplateLibraryPanel({
  surface = 'workspace',
  workspaceId = null,
  canRetry = true,
}: UtilityTemplateLibraryPanelProps) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const enabled = surface === 'workspace' || Boolean(workspaceId);
  const queryKey = useMemo(
    () => surface === 'blackbox'
      ? ['platform-workspace-utility-template-library', workspaceId]
      : ['workspace-utility-template-library'],
    [surface, workspaceId],
  );
  const catalogQuery = useQuery({
    queryKey,
    enabled,
    queryFn: () => surface === 'blackbox'
      ? fetchPlatformWorkspaceUtilityTemplateCatalog(workspaceId as string)
      : fetchUtilityTemplateCatalog(),
    refetchInterval: (query) => query.state.data?.items.some((item) =>
      ['pending', 'catalog_queued'].includes(item.provisioningStatus),
    ) ? 5_000 : false,
  });
  useEffect(() => {
    if (surface !== 'workspace') return undefined;
    const refresh = (event: Event) => {
      const detail = (event as CustomEvent<{ event?: string }>).detail;
      if (detail?.event === 'WHATSAPP_TEMPLATE_STATUS_UPDATED') {
        void queryClient.invalidateQueries({ queryKey });
      }
    };
    window.addEventListener('klm:realtime', refresh);
    return () => window.removeEventListener('klm:realtime', refresh);
  }, [queryClient, queryKey, surface]);
  const retryMutation = useMutation({
    mutationFn: (catalogKey: string) => surface === 'blackbox'
      ? retryPlatformWorkspaceUtilityTemplate(workspaceId as string, catalogKey)
      : retryUtilityTemplateCatalogItem(catalogKey),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKey, data);
      toast({
        title: 'Reenvio enfileirado',
        description: 'O worker fará a submissão idempotente e o webhook atualizará o estado real.',
      });
    },
    onError: (error) => {
      toast({ title: 'Não foi possível reenviar', description: humanError(error), variant: 'destructive' });
    },
  });

  const catalog = catalogQuery.data;
  return (
    <Card data-testid={`utility-template-library-${surface}`}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <ShieldCheck className="h-4 w-4 text-primary" />Biblioteca de utilidade
        </CardTitle>
        <CardDescription>
          Modelos transacionais do Meu Executivo, submetidos separadamente à WABA deste escritório.
          A aprovação é sempre da Meta por conta — não existe aprovação global.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Alert>
          <ShieldCheck className="h-4 w-4" />
          <AlertTitle>Uso restrito a clientes e atendimentos existentes</AlertTitle>
          <AlertDescription>
            O catálogo padrão contém somente UTILITY. Não inclui captação, boas-vindas promocionais
            ou marketing automático; o escritório continua responsável pelo uso em conformidade.
          </AlertDescription>
        </Alert>

        {catalogQuery.isLoading ? (
          <p className="text-sm text-muted-foreground">Carregando estados reais da WABA…</p>
        ) : catalogQuery.isError ? (
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Biblioteca indisponível</AlertTitle>
            <AlertDescription>{humanError(catalogQuery.error)}</AlertDescription>
            <Button className="mt-3" size="sm" variant="outline" onClick={() => catalogQuery.refetch()}>
              <RefreshCw className="mr-2 h-3.5 w-3.5" />Tentar novamente
            </Button>
          </Alert>
        ) : !catalog?.connectionId ? (
          <Alert>
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Nenhuma WABA oficial conectada</AlertTitle>
            <AlertDescription>
              Conclua a conexão oficial do WhatsApp. A biblioteca será preparada automaticamente
              depois que a conexão for validada.
            </AlertDescription>
          </Alert>
        ) : (
          <>
            {!catalog.gateOpen && (
              <Alert>
                <AlertTriangle className="h-4 w-4" />
                <AlertTitle>Entregas externas desabilitadas neste ambiente</AlertTitle>
                <AlertDescription>
                  Os itens permanecem pendentes e não são marcados como enviados. Nenhuma chamada
                  à Meta será feita enquanto o gate estiver fechado.
                </AlertDescription>
              </Alert>
            )}
            <div className="grid gap-3 md:grid-cols-2">
              {catalog.items.map((item) => {
                const meta = statusMeta(item);
                const Icon = meta.icon;
                return (
                  <div key={item.key} className="space-y-2 rounded-lg border p-3">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div>
                        <p className="text-sm font-semibold">{item.key.replaceAll('_', ' ')}</p>
                        <p className="text-2xs text-muted-foreground">{item.metaName} · pt_BR · tentativa {item.attempt || '—'}</p>
                      </div>
                      <Badge variant="outline" className={meta.className}>
                        <Icon className="mr-1 h-3 w-3" />{meta.label}
                      </Badge>
                    </div>
                    <p className="line-clamp-3 text-xs text-muted-foreground">{item.body}</p>
                    <p className="text-2xs text-muted-foreground">
                      {item.variables.length} variável(is), todas com exemplo · {item.practiceAreas.join(', ')}
                    </p>
                    {item.externalTemplateId && (
                      <p className="break-all text-2xs text-muted-foreground">Meta ID: {item.externalTemplateId}</p>
                    )}
                    {(item.rejectionReason || item.errorMessage) && (
                      <Alert variant="destructive" className="py-2">
                        <AlertDescription className="text-xs">
                          {item.rejectionReason || item.errorMessage}
                          {item.errorCode ? ` (${item.errorCode})` : ''}
                        </AlertDescription>
                      </Alert>
                    )}
                    {canRetry && item.canRetry && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={retryMutation.isPending}
                        onClick={() => retryMutation.mutate(item.key)}
                      >
                        <RefreshCw className="mr-2 h-3.5 w-3.5" />
                        {item.provisioningStatus === 'rejected' ? 'Criar nova tentativa' : 'Reenfileirar'}
                      </Button>
                    )}
                  </div>
                );
              })}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
