import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CreditCard, Loader2 } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import {
  configurePlatformCommercialProviderPriceBinding,
  fetchPlatformCommercialProviderPriceBindings,
  fetchPublicCommercialCatalog,
  type SaaSBillingProvider,
} from '@/services/commercial-self-service';

const providerLabels: Record<SaaSBillingProvider, string> = {
  stripe: 'Stripe', asaas: 'Asaas', mercado_pago: 'Mercado Pago',
};

export function PlatformCommercialProviderPanel({ isPlatformRoot }: { isPlatformRoot: boolean }) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [sku, setSku] = useState('');
  const [provider, setProvider] = useState<SaaSBillingProvider>('stripe');
  const [providerPriceId, setProviderPriceId] = useState('');
  const [reason, setReason] = useState('');
  const bindingsQuery = useQuery({
    queryKey: ['platform', 'commercial', 'provider-price-bindings'],
    queryFn: fetchPlatformCommercialProviderPriceBindings,
  });
  const catalogQuery = useQuery({
    queryKey: ['public-commercial-catalog'],
    queryFn: fetchPublicCommercialCatalog,
  });
  const offers = useMemo(
    () => (catalogQuery.data?.items ?? []).filter(
      (offer) => offer.family === 'crm' && !offer.isQuoteOnly && offer.priceMonthlyCents != null,
    ),
    [catalogQuery.data],
  );
  const mutation = useMutation({
    mutationFn: () => configurePlatformCommercialProviderPriceBinding({
      sku,
      provider,
      ...(provider === 'stripe' ? { providerPriceId: providerPriceId.trim() } : {}),
      reason: reason.trim(),
    }),
    onSuccess: async () => {
      setProviderPriceId('');
      setReason('');
      await queryClient.invalidateQueries({
        queryKey: ['platform', 'commercial', 'provider-price-bindings'],
      });
      toast({ title: `Referência ${providerLabels[provider]} vinculada à versão publicada.` });
    },
    onError: (error) => toast({
      variant: 'destructive',
      title: 'Vínculo não alterado',
      description: error instanceof Error ? error.message : 'A operação falhou sem alterar o catálogo.',
    }),
  });
  const valid = isPlatformRoot
    && Boolean(sku)
    && (provider !== 'stripe' || providerPriceId.trim().length >= 3)
    && reason.trim().length >= 8;

  return (
    <div className="space-y-4">
      <Alert>
        <CreditCard className="h-4 w-4" />
        <AlertTitle>Checkouts hospedados da assinatura</AlertTitle>
        <AlertDescription>
          Stripe, Asaas e Mercado Pago cobram o SaaS com credenciais globais isoladas. A referência externa não é segredo; chaves e webhooks permanecem somente no cofre.
        </AlertDescription>
      </Alert>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Preços vinculados</CardTitle>
          <CardDescription>Somente o Super Admin publica ou substitui um vínculo.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {bindingsQuery.isPending && <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Carregando vínculos…</p>}
          {bindingsQuery.isError && <p className="text-sm text-destructive">Não foi possível carregar os vínculos. Nenhuma ausência foi convertida em zero.</p>}
          {bindingsQuery.data?.items.length === 0 && <p className="text-sm text-muted-foreground">Nenhum provedor de assinatura configurado.</p>}
          {bindingsQuery.data?.items.map((item) => (
            <div key={item.id} className="flex flex-wrap items-center justify-between gap-3 rounded-md border p-3 text-sm">
              <div><p className="font-medium">{item.sku} · versão {item.offerVersion}</p><p className="text-xs text-muted-foreground">{providerLabels[item.provider]}{item.provider === 'stripe' ? ` · ${item.providerPriceId}` : ' · preço do catálogo canônico'}</p></div>
              <Badge variant={item.status === 'active' ? 'default' : 'secondary'}>{item.status === 'active' ? 'Ativo' : 'Inativo'}</Badge>
            </div>
          ))}
        </CardContent>
      </Card>
      {isPlatformRoot && (
        <Card>
          <CardHeader><CardTitle className="text-base">Vincular preço mensal</CardTitle><CardDescription>Substituir o vínculo não reprifica contratos existentes.</CardDescription></CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 md:grid-cols-3">
              <div className="space-y-2"><Label htmlFor="commercial-provider">Provedor</Label><Select value={provider} onValueChange={(value) => setProvider(value as SaaSBillingProvider)}><SelectTrigger id="commercial-provider"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="stripe">Stripe</SelectItem><SelectItem value="asaas">Asaas</SelectItem><SelectItem value="mercado_pago">Mercado Pago</SelectItem></SelectContent></Select></div>
              <div className="space-y-2"><Label htmlFor="commercial-offer-sku">Oferta publicada</Label><Select value={sku} onValueChange={setSku}><SelectTrigger id="commercial-offer-sku"><SelectValue placeholder="Selecione" /></SelectTrigger><SelectContent>{offers.map((offer) => <SelectItem key={offer.sku} value={offer.sku}>{offer.name} · {offer.sku}</SelectItem>)}</SelectContent></Select></div>
              {provider === 'stripe' ? (
                <div className="space-y-2"><Label htmlFor="provider-price-id">Price ID publicado no Stripe</Label><Input id="provider-price-id" value={providerPriceId} onChange={(event) => setProviderPriceId(event.target.value)} placeholder="price_..." autoComplete="off" /></div>
              ) : (
                <div className="rounded-md border bg-muted/30 p-3 text-sm text-muted-foreground">
                  {providerLabels[provider]} receberá preço, moeda e recorrência diretamente da versão publicada da oferta. Nenhum ID externo é necessário.
                </div>
              )}
            </div>
            <div className="space-y-2"><Label htmlFor="stripe-binding-reason">Motivo</Label><Textarea id="stripe-binding-reason" value={reason} onChange={(event) => setReason(event.target.value)} maxLength={500} placeholder="Informe o motivo operacional (mínimo 8 caracteres)." /></div>
            <Button disabled={!valid || mutation.isPending} onClick={() => mutation.mutate()}>{mutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Salvar vínculo</Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
