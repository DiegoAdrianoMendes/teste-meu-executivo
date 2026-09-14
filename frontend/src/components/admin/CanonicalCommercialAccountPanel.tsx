import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, CreditCard, Loader2, ShieldCheck } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import {
  createCommercialRequest,
  createCommercialBillingPortal,
  createCommercialCheckoutSession,
  fetchCommercialAccount,
  fetchPublicCommercialCatalog,
  previewCommercialRequest,
  type CommercialAccountPreview,
  type CommercialRequestPayload,
  type CommercialRequestType,
  type SaaSBillingProvider,
} from '@/services/commercial-self-service';

const requestLabels: Record<CommercialRequestType, string> = {
  change_base_plan: 'Alterar plano-base',
  activate_legal: 'Contratar Jurídico 360',
  cancel_legal: 'Cancelar Jurídico 360',
  cancel_contract: 'Encerrar contrato',
};

const statusLabels: Record<string, string> = {
  pending: 'Aguardando análise',
  resolved: 'Concluída',
  rejected: 'Rejeitada',
  cancelled: 'Cancelada',
  trialing: 'Período de teste',
  active: 'Ativo',
  past_due: 'Pagamento pendente',
  paused: 'Pausado',
  cancelled_contract: 'Cancelado',
};

const providerLabels: Record<SaaSBillingProvider, string> = {
  stripe: 'Cartão · Stripe',
  asaas: 'Asaas · cartão ou Pix',
  mercado_pago: 'Mercado Pago',
};

function money(cents: number): string {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(cents / 100);
}

export function CanonicalCommercialAccountPanel({ readOnly }: { readOnly: boolean }) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [requestType, setRequestType] = useState<CommercialRequestType>('change_base_plan');
  const [targetSku, setTargetSku] = useState('');
  const [reason, setReason] = useState('');
  const [preview, setPreview] = useState<CommercialAccountPreview | null>(null);
  const [checkoutProvider, setCheckoutProvider] = useState<SaaSBillingProvider>('stripe');

  const accountQuery = useQuery({ queryKey: ['commercial-account'], queryFn: fetchCommercialAccount });
  const catalogQuery = useQuery({ queryKey: ['public-commercial-catalog'], queryFn: fetchPublicCommercialCatalog });
  const currentBaseSku = accountQuery.data?.contract.lines.find((line) => line.sku.startsWith('crm_'))?.sku;
  const hasLegal = Boolean(accountQuery.data?.contract.lines.some((line) => line.sku === 'legal_360'));
  const plans = useMemo(
    () => (catalogQuery.data?.items ?? []).filter((offer) => offer.family === 'crm' && offer.kind === 'base_plan'),
    [catalogQuery.data],
  );

  const payload = (): CommercialRequestPayload => ({
    requestType,
    targetSku: requestType === 'change_base_plan' ? targetSku : undefined,
    expectedContractVersion: accountQuery.data!.contract.version,
    reason: reason.trim(),
  });

  const previewMutation = useMutation({
    mutationFn: () => previewCommercialRequest(payload()),
    onSuccess: setPreview,
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Prévia não gerada',
      description: error instanceof Error ? error.message : 'Não foi possível validar a solicitação.',
    }),
  });
  const submitMutation = useMutation({
    mutationFn: () => createCommercialRequest(payload()),
    onSuccess: async () => {
      setPreview(null);
      setReason('');
      await queryClient.invalidateQueries({ queryKey: ['commercial-account'] });
      toast({ title: 'Solicitação comercial registrada.', description: 'Acompanhe o status nesta mesma tela.' });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Solicitação não registrada',
      description: error instanceof Error ? error.message : 'A operação falhou sem alterar seu contrato.',
    }),
  });
  const checkoutMutation = useMutation({
    mutationFn: () => createCommercialCheckoutSession({
      targetSku,
      expectedContractVersion: accountQuery.data!.contract.version,
      reason: reason.trim(),
      provider: accountQuery.data!.availableProviders.includes(checkoutProvider)
        ? checkoutProvider
        : accountQuery.data!.availableProviders[0],
    }),
    onSuccess: ({ checkoutUrl }) => globalThis.location.assign(checkoutUrl),
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Checkout não iniciado',
      description: error instanceof Error ? error.message : 'Nenhuma cobrança foi criada.',
    }),
  });
  const portalMutation = useMutation({
    mutationFn: createCommercialBillingPortal,
    onSuccess: ({ portalUrl }) => globalThis.location.assign(portalUrl),
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Portal indisponível',
      description: error instanceof Error ? error.message : 'Não foi possível abrir a cobrança.',
    }),
  });

  if (accountQuery.isPending) {
    return <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Carregando contrato canônico…</div>;
  }
  if (accountQuery.isError || !accountQuery.data) {
    return <Alert variant="destructive"><AlertTriangle className="h-4 w-4" /><AlertTitle>Contrato indisponível</AlertTitle><AlertDescription>Nenhum valor foi estimado. Recarregue ou procure o suporte.</AlertDescription></Alert>;
  }
  const {
    contract,
    requests,
    providerMessage,
    automaticCheckoutAvailable,
    billingPortalAvailable,
    providerSubscriptionLinked,
    activeProvider = providerSubscriptionLinked ? 'stripe' : null,
    availableProviders = automaticCheckoutAvailable ? ['stripe'] : [],
  } = accountQuery.data;
  const effectiveProvider = availableProviders.includes(checkoutProvider)
    ? checkoutProvider
    : availableProviders[0];
  const valid = reason.trim().length >= 8 && (requestType !== 'change_base_plan' || Boolean(targetSku));
  const canUseCheckout = requestType === 'change_base_plan'
    && automaticCheckoutAvailable
    && !providerSubscriptionLinked;
  const providerManagedRequest = providerSubscriptionLinked
    && (requestType === 'change_base_plan' || requestType === 'cancel_contract');

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div><CardTitle className="flex items-center gap-2 text-base"><ShieldCheck className="h-4 w-4 text-primary" />Assinatura e produtos</CardTitle><CardDescription>Fonte canônica versionada da conta.</CardDescription></div>
            <Badge variant="secondary">{statusLabels[contract.status] ?? contract.status}</Badge>
          </div>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-3">
          <div className="rounded-md border p-3"><p className="text-xs text-muted-foreground">Recorrência mensal</p><p className="mt-1 font-semibold">{money(contract.price.recurringPriceCents)}</p></div>
          <div className="rounded-md border p-3"><p className="text-xs text-muted-foreground">Valor previsto</p><p className="mt-1 font-semibold">{money(contract.price.amountDueCents)}</p></div>
          <div className="rounded-md border p-3"><p className="text-xs text-muted-foreground">Versão</p><p className="mt-1 font-semibold">{contract.version}</p></div>
          <div className="sm:col-span-3 flex flex-wrap gap-2">{contract.lines.map((line) => <Badge key={line.sku} variant="outline">{line.sku}</Badge>)}</div>
        </CardContent>
      </Card>

      <Alert>
        <CreditCard className="h-4 w-4" />
        <AlertTitle>{billingPortalAvailable ? 'Cobrança segura' : automaticCheckoutAvailable ? 'Checkout seguro disponível' : 'Cobrança assistida'}</AlertTitle>
        <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
          <span>{providerMessage}</span>
          {billingPortalAvailable && !readOnly && (
            <Button size="sm" variant="outline" disabled={portalMutation.isPending} onClick={() => portalMutation.mutate()}>
              {portalMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Gerenciar cobrança
            </Button>
          )}
        </AlertDescription>
      </Alert>

      {!readOnly && (
        <Card>
          <CardHeader><CardTitle className="text-base">Alterar produtos</CardTitle><CardDescription>{billingPortalAvailable ? 'Plano Core, pagamento e encerramento são administrados no portal seguro do provedor. Jurídico 360 continua no fluxo assistido.' : providerSubscriptionLinked ? 'Este provedor não oferece portal hospedado. Alterações e cancelamento seguem pelo fluxo assistido e auditado do Meu Executivo.' : 'A prévia não altera nem cobra. Planos elegíveis seguem para o checkout hospedado; mudanças assistidas vão para análise.'}</CardDescription></CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2"><Label>Tipo</Label><Select value={requestType} onValueChange={(value) => { setRequestType(value as CommercialRequestType); setPreview(null); }}><SelectTrigger aria-label="Tipo de alteração"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="change_base_plan">Alterar plano-base</SelectItem><SelectItem value={hasLegal ? 'cancel_legal' : 'activate_legal'}>{hasLegal ? 'Cancelar Jurídico 360' : 'Contratar Jurídico 360'}</SelectItem><SelectItem value="cancel_contract">Encerrar contrato</SelectItem></SelectContent></Select></div>
              {requestType === 'change_base_plan' && <div className="space-y-2"><Label>Novo plano</Label><Select value={targetSku} onValueChange={(value) => { setTargetSku(value); setPreview(null); }}><SelectTrigger aria-label="Novo plano"><SelectValue placeholder="Selecione" /></SelectTrigger><SelectContent>{plans.filter((offer) => offer.sku !== currentBaseSku).map((offer) => <SelectItem key={offer.sku} value={offer.sku}>{offer.name}{offer.priceMonthlyCents != null ? ` · ${money(offer.priceMonthlyCents)}/mês` : ''}</SelectItem>)}</SelectContent></Select></div>}
            </div>
            <div className="space-y-2"><Label>Motivo</Label><Textarea value={reason} onChange={(event) => { setReason(event.target.value); setPreview(null); }} placeholder="Explique a necessidade da alteração (mínimo 8 caracteres)." maxLength={500} /></div>
            {canUseCheckout && availableProviders.length > 0 && (
              <div className="space-y-2">
                <Label>Meio de pagamento da assinatura</Label>
                <Select value={effectiveProvider} onValueChange={(value) => setCheckoutProvider(value as SaaSBillingProvider)}>
                  <SelectTrigger aria-label="Meio de pagamento da assinatura"><SelectValue /></SelectTrigger>
                  <SelectContent>{availableProviders.map((provider) => (
                    <SelectItem key={provider} value={provider}>{providerLabels[provider]}</SelectItem>
                  ))}</SelectContent>
                </Select>
              </div>
            )}
            {providerManagedRequest && <Alert><CreditCard className="h-4 w-4" /><AlertTitle>Alteração gerenciada pelo provedor</AlertTitle><AlertDescription>A assinatura está vinculada ao {activeProvider ? providerLabels[activeProvider] : 'provedor externo'}. Use a administração segura para manter cobrança e contrato sincronizados.</AlertDescription></Alert>}
            {preview && <div className="rounded-md border p-4 text-sm"><p className="font-medium">Prévia validada</p>{preview.revision && <p className="mt-1 text-muted-foreground">Atual: {money(preview.revision.currentPrice.amountDueCents)} · Após aprovação: {money(preview.revision.price.amountDueCents)}</p>}{preview.warnings.map((warning) => <p className="mt-2 text-amber-700" key={warning}>• {warning}</p>)}</div>}
            <div className="flex flex-wrap gap-2">
              {providerManagedRequest && billingPortalAvailable ? (
                <Button disabled={!billingPortalAvailable || portalMutation.isPending} onClick={() => portalMutation.mutate()}>
                  {portalMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  Abrir portal seguro
                </Button>
              ) : <Button variant="outline" disabled={!valid || previewMutation.isPending} onClick={() => previewMutation.mutate()}>{previewMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Gerar prévia</Button>}
              {(!providerManagedRequest || !billingPortalAvailable) && (canUseCheckout ? (
                <Button disabled={!preview?.eligible || !effectiveProvider || checkoutMutation.isPending} onClick={() => checkoutMutation.mutate()}>
                  {checkoutMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  Ir para pagamento seguro
                </Button>
              ) : (
                <Button disabled={!preview?.eligible || submitMutation.isPending} onClick={() => submitMutation.mutate()}>{submitMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Confirmar solicitação</Button>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader><CardTitle className="text-base">Histórico de solicitações</CardTitle></CardHeader>
        <CardContent className="space-y-2">{requests.length === 0 ? <p className="text-sm text-muted-foreground">Nenhuma solicitação registrada.</p> : requests.map((item) => <div key={item.id} className="flex flex-wrap items-start justify-between gap-2 rounded-md border p-3 text-sm"><div><p className="font-medium">{requestLabels[item.requestType]}</p><p className="text-xs text-muted-foreground">{new Intl.DateTimeFormat('pt-BR', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(item.createdAt))}</p>{item.resolutionNote && <p className="mt-1 text-xs">{item.resolutionNote}</p>}</div><Badge variant={item.status === 'resolved' ? 'default' : 'secondary'}>{statusLabels[item.status] ?? item.status}</Badge></div>)}</CardContent>
      </Card>
    </div>
  );
}
