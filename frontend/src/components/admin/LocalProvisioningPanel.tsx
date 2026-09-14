import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CheckCircle2,
  CircleDashed,
  Loader2,
  Play,
  RefreshCw,
  ServerCog,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { useToast } from '@/hooks/use-toast';
import {
  executeLocalProvisioning,
  fetchLocalProvisioningPlan,
  fetchLocalProvisioningState,
  type LocalProvisioningResource,
} from '@/services/local-provisioning';

function resourceModuleLabel(resource: LocalProvisioningResource): string {
  if (resource.provider === 'klm') return 'Núcleo Meu Executivo';
  if (['account', 'inbox'].includes(resource.resourceType)) return 'Comunicações';
  if (['agent_group', 'agent', 'workflow'].includes(resource.resourceType)) return 'Automação';
  if (['pipeline', 'funnel', 'matter', 'finance'].includes(resource.resourceType)) return 'CRM jurídico';
  if (resource.resourceType === 'workspace') return 'Colaboração';
  return 'Operações locais';
}

function resourceTypeLabel(resourceType: string): string {
  const labels: Record<string, string> = {
    workspace: 'Espaço de trabalho',
    storage: 'Documentos locais',
    account: 'Conta de atendimento',
    inbox: 'Caixa de entrada',
    organization: 'Organização',
    agent_group: 'Equipe automatizada',
    pipeline: 'Funil jurídico',
    component: 'Componente de conversa',
  };
  return labels[resourceType] ?? 'Recurso local';
}

const actionLabels = {
  create: 'Criar',
  update: 'Reconciliar',
  noop: 'Pronto',
} as const;

function formatDate(value?: string | null): string {
  if (!value) return 'Ainda não executado';
  return new Intl.DateTimeFormat('pt-BR', {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(new Date(value));
}

export function LocalProvisioningPanel({ readOnly = false }: { readOnly?: boolean }) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const planQuery = useQuery({
    queryKey: ['local-provisioning-plan'],
    queryFn: fetchLocalProvisioningPlan,
  });
  const stateQuery = useQuery({
    queryKey: ['local-provisioning-state'],
    queryFn: fetchLocalProvisioningState,
  });
  const mutation = useMutation({
    mutationFn: (action: 'apply' | 'reconcile') => executeLocalProvisioning(action),
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['local-provisioning-plan'] }),
        queryClient.invalidateQueries({ queryKey: ['local-provisioning-state'] }),
        queryClient.invalidateQueries({ queryKey: ['runtime-status'] }),
      ]);
      toast({
        title: result.replayed ? 'Comando local já aplicado.' : 'Recursos locais reconciliados.',
        description: `Versão ${result.state.documentVersion ?? 1}, sem efeito externo.`,
      });
    },
    onError: (error) => {
      toast({
        variant: 'destructive',
        title: 'Provisionamento não concluído',
        description: error instanceof Error ? error.message : 'Falha na reconciliação local.',
      });
    },
  });

  const plan = planQuery.data;
  const state = stateQuery.data;
  const total = plan?.resources.length ?? 0;
  const ready = plan?.noops ?? 0;
  const progress = total ? Math.round((ready / total) * 100) : 0;
  const modules = Array.from(new Set((plan?.resources ?? []).map(resourceModuleLabel)));

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <ServerCog className="h-4 w-4 text-primary" />
              Provisionamento local
            </CardTitle>
            <CardDescription>Plano declarativo dos recursos nativos e seus controles locais.</CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline">simulação local</Badge>
            <Badge variant={(plan?.externalEffects ?? 0) === 0 ? 'secondary' : 'destructive'}>
              {plan?.externalEffects ?? 0} efeitos externos
            </Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        {(planQuery.isLoading || stateQuery.isLoading) && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />Compilando plano local...
          </div>
        )}

        {plan && (
          <>
            <div className="grid gap-3 md:grid-cols-4">
              <div className="rounded-md border p-3">
                <p className="text-xs text-muted-foreground">Recursos</p>
                <p className="mt-1 text-2xl font-bold tabular-nums">{total}</p>
                <p className="mt-1 text-xs text-muted-foreground tabular-nums">{modules.length} módulos locais</p>
              </div>
              <div className="rounded-md border p-3">
                <p className="text-xs text-muted-foreground">A criar</p>
                <p className="mt-1 text-2xl font-bold tabular-nums">{plan.creates}</p>
                <p className="mt-1 text-xs text-muted-foreground">Recursos ainda ausentes</p>
              </div>
              <div className="rounded-md border p-3">
                <p className="text-xs text-muted-foreground">Divergências</p>
                <p className="mt-1 text-2xl font-bold tabular-nums">{plan.updates}</p>
                <p className="mt-1 text-xs text-muted-foreground">Estados a reconciliar</p>
              </div>
              <div className="rounded-md border p-3">
                <p className="text-xs text-muted-foreground">Versão</p>
                <p className="mt-1 text-2xl font-bold tabular-nums">{state?.documentVersion ?? 0}</p>
                <p className="mt-1 text-xs text-muted-foreground">{formatDate(state?.lastRunAt)}</p>
              </div>
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between gap-3 text-xs">
                <span className="font-medium">Convergência local</span>
                <span className="text-muted-foreground">{ready}/{total}</span>
              </div>
              <Progress value={progress} className="h-2" />
            </div>

            <div className="flex flex-wrap gap-2">
              {modules.map((module) => (
                <Badge key={module} variant="outline">{module}</Badge>
              ))}
            </div>

            <div className="overflow-hidden rounded-md border">
              <div className="hidden grid-cols-[1.2fr_1fr_1fr_auto] gap-3 border-b bg-muted/40 px-3 py-2 text-xs font-medium text-muted-foreground md:grid">
                <span>Módulo</span>
                <span>Recurso</span>
                <span>Disponibilidade</span>
                <span>Estado</span>
              </div>
              <div className="divide-y">
                {plan.resources.map((resource) => (
                  <div key={resource.key} className="grid gap-2 px-3 py-3 text-xs md:grid-cols-[1.2fr_1fr_1fr_auto] md:items-center md:gap-3">
                    <span className="font-medium">{resourceModuleLabel(resource)}</span>
                    <span className="text-muted-foreground">{resourceTypeLabel(resource.resourceType)}</span>
                    <span className="text-muted-foreground">{resource.status === 'ready' ? 'Disponível' : resource.status === 'drift' ? 'Reconciliação necessária' : 'Preparação pendente'}</span>
                    <Badge
                      variant={resource.action === 'noop' ? 'secondary' : resource.action === 'update' ? 'destructive' : 'outline'}
                      className="w-fit"
                    >
                      {resource.action === 'noop' && <CheckCircle2 className="mr-1 h-3 w-3" />}
                      {resource.action === 'update' && <TriangleAlert className="mr-1 h-3 w-3" />}
                      {resource.action === 'create' && <CircleDashed className="mr-1 h-3 w-3" />}
                      {actionLabels[resource.action]}
                    </Badge>
                  </div>
                ))}
              </div>
            </div>

            <div className="flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
                <ShieldCheck className="h-4 w-4 shrink-0 text-emerald-600" />
                <span>Plano local versionado e auditável</span>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="outline"
                  disabled={readOnly || mutation.isPending || !state?.configured}
                  onClick={() => mutation.mutate('reconcile')}
                >
                  {mutation.isPending
                    ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                    : <RefreshCw className="mr-1.5 h-4 w-4" />}
                  Reconciliar
                </Button>
                <Button
                  type="button"
                  disabled={readOnly || mutation.isPending || plan.ready}
                  onClick={() => mutation.mutate('apply')}
                >
                  {mutation.isPending
                    ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                    : <Play className="mr-1.5 h-4 w-4" />}
                  Aplicar plano
                </Button>
              </div>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
