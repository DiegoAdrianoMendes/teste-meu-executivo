import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Archive,
  ArrowDownRight,
  ArrowUpRight,
  Ban,
  Check,
  CircleDollarSign,
  Clock3,
  Download,
  Loader2,
  PauseCircle,
  PlayCircle,
  ReceiptText,
  ShieldCheck,
} from 'lucide-react';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { useToast } from '@/hooks/use-toast';
import {
  createLocalBillingClosureIntent,
  createLocalBillingExportIntent,
  executeLocalBillingCommand,
  fetchLocalBillingIntents,
  fetchLocalBillingPortal,
  fetchLocalPlanCatalog,
  type LocalBillingCommand,
  type LocalBillingInterval,
  type LocalPlanTier,
} from '@/services/local-billing';
import { CanonicalCommercialAccountPanel } from '@/components/admin/CanonicalCommercialAccountPanel';

const tierRank: Record<LocalPlanTier, number> = {
  free: 0,
  starter: 1,
  pro: 2,
  business: 3,
  enterprise: 4,
};

const entitlementLabels: Record<string, string> = {
  tokens: 'Tokens',
  ai_executions: 'Execuções de IA',
  ai_conversations: 'Conversas com IA',
  audio_minutes: 'Áudio com IA',
  storage_bytes: 'Armazenamento',
  files: 'Arquivos',
  users: 'Usuários',
  whatsapp_numbers: 'Números de WhatsApp',
  agents: 'Agentes',
  automations: 'Automações',
  digital_signature: 'Assinatura digital',
  calendar: 'Sincronização externa de calendário',
  email: 'E-mail',
  webhooks: 'Webhooks',
  advanced_audit: 'Auditoria avançada',
  success_manager: 'Gerente de sucesso',
};

const featureEntitlements = new Set([
  'digital_signature',
  'calendar',
  'email',
  'webhooks',
  'advanced_audit',
  'success_manager',
]);

const statusLabels: Record<string, string> = {
  trialing: 'Período de teste',
  active: 'Ativa',
  past_due: 'Em carência',
  cancelled: 'Cancelada',
  unpaid: 'Suspensa',
  paused: 'Pausada',
};

function formatMoney(cents: number, currency = 'BRL'): string {
  return new Intl.NumberFormat('pt-BR', {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
  }).format(cents / 100);
}

function formatQuantity(key: string, value: number | null, enabled = true): string {
  if (featureEntitlements.has(key)) return enabled ? 'Incluído' : 'Não incluído';
  if (!enabled) return 'Não incluído';
  if (value == null) return 'Ilimitado';
  if (key === 'storage_bytes') {
    return new Intl.NumberFormat('pt-BR', {
      style: 'unit',
      unit: 'gigabyte',
      maximumFractionDigits: 2,
    }).format(value / 1_073_741_824);
  }
  if (key === 'audio_minutes') {
    return `${new Intl.NumberFormat('pt-BR').format(value)} min`;
  }
  return new Intl.NumberFormat('pt-BR').format(value);
}

function formatDate(value?: string | null): string {
  if (!value) return 'Não definido';
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'medium' }).format(new Date(value));
}

function actionLabel(value: string): string {
  const label = value.replace(/^local_billing\./, '').replaceAll('_', ' ');
  return label.charAt(0).toUpperCase() + label.slice(1);
}

export function SaasBillingPanel({
  readOnly = false,
  canonicalManaged = true,
}: {
  readOnly?: boolean;
  canonicalManaged?: boolean;
}) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [interval, setInterval] = useState<LocalBillingInterval>('monthly');
  const [closureReason, setClosureReason] = useState('');

  const catalogQuery = useQuery({
    queryKey: ['local-billing-catalog'],
    queryFn: fetchLocalPlanCatalog,
    enabled: !canonicalManaged,
  });
  const portalQuery = useQuery({ queryKey: ['local-billing-portal'], queryFn: fetchLocalBillingPortal });
  const intentsQuery = useQuery({
    queryKey: ['local-billing-intents'],
    queryFn: () => fetchLocalBillingIntents(50),
  });

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['local-billing-portal'] }),
      queryClient.invalidateQueries({ queryKey: ['local-billing-intents'] }),
      queryClient.invalidateQueries({ queryKey: ['workspace-plan-summary'] }),
    ]);
  };

  const commandMutation = useMutation({
    mutationFn: ({ command, payload }: { command: LocalBillingCommand; payload?: Record<string, unknown> }) => (
      executeLocalBillingCommand(command, payload)
    ),
    onSuccess: async (result) => {
      await refresh();
      toast({ title: result.replayed ? 'Comando já aplicado.' : 'Assinatura atualizada no simulador local.' });
    },
    onError: (error) => {
      toast({
        variant: 'destructive',
        title: 'Operação não concluída',
        description: error instanceof Error ? error.message : 'Falha no simulador local.',
      });
    },
  });

  const exportMutation = useMutation({
    mutationFn: () => createLocalBillingExportIntent({
      exportFormat: 'zip',
      scopes: ['workspace', 'users', 'cases', 'documents', 'audit'],
      reason: 'Exportação solicitada pelo administrador do workspace',
    }),
    onSuccess: async () => {
      await refresh();
      toast({ title: 'Intenção de exportação registrada.' });
    },
  });

  const closureMutation = useMutation({
    mutationFn: () => createLocalBillingClosureIntent(closureReason.trim()),
    onSuccess: async () => {
      setClosureReason('');
      await refresh();
      toast({ title: 'Solicitação de encerramento registrada para revisão.' });
    },
  });

  const subscription = portalQuery.data?.subscription ?? null;
  const access = portalQuery.data?.access;
  const busy = commandMutation.isPending || exportMutation.isPending || closureMutation.isPending;

  const choosePlan = (tier: LocalPlanTier) => {
    if (!subscription) {
      commandMutation.mutate({ command: 'checkout', payload: { planTier: tier, interval } });
      return;
    }
    const command: LocalBillingCommand = tierRank[tier] > tierRank[subscription.planTier]
      ? 'upgrade'
      : 'downgrade';
    commandMutation.mutate({ command, payload: { planTier: tier, interval } });
  };

  if (canonicalManaged) {
    return <CanonicalCommercialAccountPanel readOnly={readOnly} />;
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle className="flex items-center gap-2 text-base">
                <CircleDollarSign className="h-4 w-4 text-primary" />
                Assinatura do Meu Executivo
              </CardTitle>
              <CardDescription>Plano, acesso, limites e ciclo de vida do workspace.</CardDescription>
            </div>
            <div className="flex flex-wrap gap-2">
              <Badge variant="outline">simulador local</Badge>
              <Badge variant={portalQuery.data?.externalEffects === 0 ? 'secondary' : 'destructive'}>
                {portalQuery.data?.externalEffects ?? 0} efeitos externos
              </Badge>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {portalQuery.isLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />Carregando assinatura...
            </div>
          ) : (
            <div className="grid gap-3 md:grid-cols-4">
              <div className="rounded-md border p-3">
                <p className="text-xs text-muted-foreground">Plano atual</p>
                <p className="mt-1 truncate text-2xl font-bold">{subscription?.planName ?? 'Sem assinatura'}</p>
                <Badge className="mt-2" variant={access?.writeAllowed ? 'default' : 'secondary'}>
                  {subscription ? statusLabels[subscription.status] ?? subscription.status : 'Não iniciado'}
                </Badge>
              </div>
              <div className="rounded-md border p-3">
                <p className="text-xs text-muted-foreground">Valor</p>
                <p className="mt-1 text-2xl font-bold tabular-nums">
                  {subscription ? formatMoney(subscription.amountCents, subscription.currency) : formatMoney(0)}
                </p>
                <p className="mt-2 text-xs text-muted-foreground">
                  {subscription?.interval === 'annually' ? 'por ano' : 'por mês'}
                </p>
              </div>
              <div className="rounded-md border p-3">
                <p className="text-xs text-muted-foreground">Próximo marco</p>
                <p className="mt-1 text-sm font-semibold tabular-nums">
                  {formatDate(subscription?.trialEnd ?? subscription?.currentPeriodEnd)}
                </p>
                <p className="mt-2 text-xs text-muted-foreground">
                  {subscription?.trialEnd ? 'Fim do período de teste' : 'Fim do período atual'}
                </p>
              </div>
              <div className="rounded-md border p-3">
                <p className="text-xs text-muted-foreground">Acesso</p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <Badge variant={access?.readAllowed ? 'default' : 'destructive'}>Leitura</Badge>
                  <Badge variant={access?.writeAllowed ? 'default' : 'destructive'}>Escrita</Badge>
                </div>
                <p className="mt-2 truncate text-xs text-muted-foreground">{access?.reasonCode ?? 'Sem política'}</p>
              </div>
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            {!subscription && (
              <Button
                type="button"
                variant="outline"
                disabled={readOnly || busy}
                onClick={() => commandMutation.mutate({ command: 'trial', payload: { planTier: 'pro', trialDays: 14 } })}
              >
                <Clock3 className="mr-1.5 h-4 w-4" />Iniciar teste de 14 dias
              </Button>
            )}
            {subscription && ['active', 'trialing'].includes(subscription.status) && (
              <Button
                type="button"
                variant="outline"
                disabled={readOnly || busy}
                onClick={() => commandMutation.mutate({ command: 'dunning', payload: { graceDays: 7 } })}
              >
                <ReceiptText className="mr-1.5 h-4 w-4" />Simular atraso
              </Button>
            )}
            {subscription && !['unpaid', 'cancelled'].includes(subscription.status) && (
              <Button
                type="button"
                variant="outline"
                disabled={readOnly || busy}
                onClick={() => commandMutation.mutate({ command: 'suspend', payload: { reason: 'Suspensão local pelo administrador' } })}
              >
                <PauseCircle className="mr-1.5 h-4 w-4" />Suspender
              </Button>
            )}
            {subscription && ['unpaid', 'past_due', 'cancelled', 'paused'].includes(subscription.status) && (
              <Button
                type="button"
                variant="outline"
                disabled={readOnly || busy}
                onClick={() => commandMutation.mutate({ command: 'reactivate', payload: { reason: 'Reativação local pelo administrador' } })}
              >
                <PlayCircle className="mr-1.5 h-4 w-4" />Reativar
              </Button>
            )}
            <Button type="button" variant="outline" disabled={readOnly || busy} onClick={() => exportMutation.mutate()}>
              <Download className="mr-1.5 h-4 w-4" />Preparar exportação
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <CardTitle className="text-base">Planos</CardTitle>
              <CardDescription>Catálogo local e limites tipados por recurso.</CardDescription>
            </div>
            <Select value={interval} onValueChange={(value) => setInterval(value as LocalBillingInterval)}>
              <SelectTrigger className="w-[150px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="monthly">Mensal</SelectItem>
                <SelectItem value="annually">Anual</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 lg:grid-cols-3">
            {(catalogQuery.data ?? []).map((plan) => {
              const current = subscription?.planTier === plan.tier;
              const price = interval === 'annually' ? plan.priceAnnuallyCents : plan.priceMonthlyCents;
              const upgrade = subscription ? tierRank[plan.tier] > tierRank[subscription.planTier] : false;
              return (
                <div key={plan.id} className={`flex min-h-[520px] flex-col rounded-md border p-4 ${current ? 'border-primary bg-primary/5' : ''}`}>
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="text-base font-medium">{plan.name}</p>
                      <p className="mt-1 text-xs text-muted-foreground">{plan.description || 'Plano Meu Executivo'}</p>
                    </div>
                    {current && <Badge>Atual</Badge>}
                  </div>
                  <p className="mt-4 text-2xl font-bold tabular-nums">{formatMoney(price, plan.currency)}</p>
                  <p className="text-xs text-muted-foreground">{interval === 'annually' ? 'por ano' : 'por mês'}</p>
                  <div className="mt-4 flex-1 space-y-2">
                    {plan.entitlements.map((item) => (
                      <div key={item.entitlement} className="flex items-center justify-between gap-3 text-xs">
                        <span className="flex min-w-0 items-center gap-1.5">
                          {item.enabled
                            ? <Check className="h-3.5 w-3.5 shrink-0 text-emerald-600" />
                            : <Ban className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
                          <span className="truncate">{entitlementLabels[item.entitlement] ?? item.entitlement}</span>
                        </span>
                        <span className="shrink-0 font-medium tabular-nums">{formatQuantity(item.entitlement, item.limitQuantity, item.enabled)}</span>
                      </div>
                    ))}
                  </div>
                  <Button
                    type="button"
                    className="mt-4 w-full"
                    variant={current ? 'secondary' : 'default'}
                    disabled={readOnly || busy || current || !plan.isActive}
                    onClick={() => choosePlan(plan.tier)}
                  >
                    {upgrade ? <ArrowUpRight className="mr-1.5 h-4 w-4" /> : <ArrowDownRight className="mr-1.5 h-4 w-4" />}
                    {!subscription ? 'Ativar plano' : upgrade ? 'Fazer upgrade' : current ? 'Plano atual' : 'Fazer downgrade'}
                  </Button>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <ShieldCheck className="h-4 w-4 text-primary" />Histórico do ciclo de vida
          </CardTitle>
          <CardDescription>Comandos idempotentes e intenções administrativas do workspace.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="divide-y rounded-md border">
            {(intentsQuery.data ?? portalQuery.data?.recentIntents ?? []).map((intent) => (
              <div key={intent.id} className="flex flex-col gap-2 p-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{actionLabel(intent.actionType)}</p>
                  <p className="truncate text-xs text-muted-foreground">{intent.reason}</p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge variant="outline">{intent.status}</Badge>
                  <span className="text-xs text-muted-foreground">{formatDate(intent.createdAt)}</span>
                </div>
              </div>
            ))}
            {!intentsQuery.isLoading && !(intentsQuery.data?.length || portalQuery.data?.recentIntents.length) && (
              <p className="p-3 text-xs text-muted-foreground">Nenhum comando registrado.</p>
            )}
          </div>

          <Separator />

          <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
            <div className="max-w-xl space-y-1.5">
              <Label>Motivo do encerramento</Label>
              <Input
                value={closureReason}
                onChange={(event) => setClosureReason(event.target.value)}
                placeholder="Informe o motivo para registrar a solicitação"
                maxLength={240}
                readOnly={readOnly}
              />
            </div>
            <div className="flex flex-wrap gap-2">
              {subscription && subscription.status !== 'cancelled' && (
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button type="button" variant="outline" disabled={readOnly || busy}>
                      <Ban className="mr-1.5 h-4 w-4" />Cancelar assinatura
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>Cancelar a assinatura local?</AlertDialogTitle>
                      <AlertDialogDescription>
                        O workspace passa para acesso somente leitura conforme a política do ciclo de vida.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Voltar</AlertDialogCancel>
                      <AlertDialogAction onClick={() => commandMutation.mutate({ command: 'cancel', payload: { reason: 'Cancelamento local pelo administrador' } })}>
                        Confirmar cancelamento
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              )}

              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button type="button" variant="destructive" disabled={readOnly || busy || closureReason.trim().length < 3}>
                    <Archive className="mr-1.5 h-4 w-4" />Solicitar encerramento
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Registrar encerramento do workspace?</AlertDialogTitle>
                    <AlertDialogDescription>
                      A solicitação ficará pendente para exportação e revisão. Nenhum dado será removido agora.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Voltar</AlertDialogCancel>
                    <AlertDialogAction onClick={() => closureMutation.mutate()}>
                      Registrar solicitação
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
