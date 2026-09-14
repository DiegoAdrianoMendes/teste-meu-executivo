/**
 * Página de obrigado após o checkout público.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { BRAND } from '@/lib/brand';
import { DesbLogo } from '@/components/ui/DesbLogo';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { CheckCircle2, ArrowRight, MessageSquare, BarChart3, Bot, CreditCard, Loader2 } from 'lucide-react';
import {
  createCommercialCheckoutSession,
  fetchCommercialAccount,
  type CommercialAccountSummary,
  type SaaSBillingProvider,
} from '@/services/commercial-self-service';

const PROVIDER_LABELS: Record<SaaSBillingProvider, string> = {
  stripe: 'Cartão · Stripe',
  asaas: 'Asaas · cartão ou Pix',
  mercado_pago: 'Mercado Pago',
};

function newCheckoutIdempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.()
    ?? `verified-checkout-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

const NEXT_STEPS = [
  {
    icon: MessageSquare,
    title: "Conecte seu WhatsApp",
    description: "Conecte a API oficial da Meta pelo fluxo disponível para sua conta. Nenhum QR code é necessário.",
    cta: "Conectar agora",
    link: "/app/conexoes",
  },
  {
    icon: Bot,
    title: "Ative suas áreas da biblioteca",
    description: "Escolha as áreas do escritório, defina follow-up e intervenção humana sem mexer em configurações técnicas.",
    cta: "Ver automações do workspace",
    link: "/app/areas",
  },
  {
    icon: BarChart3,
    title: "Importe seus casos",
    description: "Adicione os primeiros casos no Kanban ou importe de planilha CSV.",
    cta: "Ir para o Kanban",
    link: "/app/funil",
  },
];

export default function ThankYou() {
  const [searchParams] = useSearchParams();
  const checkoutCompleted = searchParams.get('checkout') === 'success';
  const [account, setAccount] = useState<CommercialAccountSummary | null>(null);
  const [accountState, setAccountState] = useState<'loading' | 'ready' | 'unavailable'>('loading');
  const [provider, setProvider] = useState<SaaSBillingProvider | null>(null);
  const [checkoutState, setCheckoutState] = useState<'idle' | 'creating' | 'ready' | 'failed'>('idle');
  const [checkoutUrl, setCheckoutUrl] = useState<string | null>(null);
  const [checkoutError, setCheckoutError] = useState<string | null>(null);
  const checkoutIdempotencyKey = useRef(newCheckoutIdempotencyKey());

  useEffect(() => {
    fetchCommercialAccount()
      .then((summary) => {
        setAccount(summary);
        setProvider(summary.availableProviders?.[0] ?? null);
        setAccountState('ready');
      })
      .catch(() => setAccountState('unavailable'));
  }, []);

  const basePlan = account?.contract.lines.find((line) => line.sku.startsWith('crm_'))?.sku;
  const planLabel = basePlan
    ? basePlan.replace('crm_', '').replace(/^./, (value) => value.toUpperCase())
    : null;
  const availableProviders = useMemo(() => account?.availableProviders ?? [], [account]);
  const checkoutRequired = !checkoutCompleted
    && accountState === 'ready'
    && account?.contract.status === 'trialing'
    && !account.providerSubscriptionLinked;

  const prepareCheckout = async () => {
    if (!account || !basePlan || !provider) return;
    setCheckoutState('creating');
    setCheckoutError(null);
    setCheckoutUrl(null);
    try {
      const session = await createCommercialCheckoutSession({
        targetSku: basePlan,
        expectedContractVersion: account.contract.version,
        reason: 'Contratação iniciada após verificação da conta',
        provider,
      }, checkoutIdempotencyKey.current);
      setCheckoutUrl(session.checkoutUrl);
      setCheckoutState('ready');
    } catch (error) {
      setCheckoutState('failed');
      setCheckoutError(error instanceof Error ? error.message : 'Nenhuma cobrança foi criada.');
    }
  };

  return (
    <div className="min-h-screen bg-background flex flex-col">
      {/* Header */}
      <header className="border-b border-border">
        <div className="max-w-5xl mx-auto px-4 h-14 flex items-center">
          <Link to="/">
            <DesbLogo variant="full" size={48} />
          </Link>
        </div>
      </header>

      <main className="flex-1 flex flex-col items-center justify-center p-6 text-center">
        <div className="max-w-2xl">
          {/* Ícone de sucesso */}
          <div className="w-20 h-20 rounded-full bg-success/10 flex items-center justify-center mx-auto mb-6">
            <CheckCircle2 className="h-10 w-10 text-success" />
          </div>

          <h1 className="text-4xl font-bold tracking-tight mb-3">
            {checkoutCompleted
              ? 'Checkout concluído'
              : `Bem-vindo ao ${BRAND.name} ${BRAND.tagline}!`}
          </h1>
          <p className="text-lg text-muted-foreground mb-2">
            {checkoutCompleted
              ? accountState === 'ready' && account?.contract.status === 'active'
                ? 'Sua assinatura está ativa. Agora conclua o onboarding antes de operar os primeiros fluxos.'
                : 'Recebemos o retorno do checkout. A confirmação da assinatura será refletida aqui assim que o provedor concluir o processamento.'
              : 'Sua identidade foi verificada e o workspace foi criado. Agora conclua o onboarding antes de operar os primeiros fluxos.'}
          </p>
          <div className="badge-ai mx-auto mb-10 w-fit">
            {accountState === 'loading' && 'Confirmando contrato…'}
            {accountState === 'ready' && `${planLabel ? `Plano ${planLabel}` : 'Contrato ativo'} · ${account?.contract.status ?? ''}`}
            {accountState === 'unavailable' && 'Contrato indisponível para consulta — tente novamente na Assinatura'}
          </div>

          {checkoutRequired && (
            <div className="mb-8 rounded-xl border border-primary/20 bg-card p-5 text-left">
              <div className="mb-4 flex items-start gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                  <CreditCard className="h-5 w-5 text-primary" />
                </div>
                <div>
                  <h2 className="font-semibold">Concluir contratação</h2>
                  <p className="mt-1 text-sm text-muted-foreground">
                    Sua conta está verificada e o trial foi criado. Escolha o meio de pagamento para ativar a cobrança recorrente do plano {planLabel ?? 'selecionado'}.
                  </p>
                </div>
              </div>

              {account.automaticCheckoutAvailable && availableProviders.length > 0 ? (
                <div className="space-y-3">
                  <div className="space-y-1.5">
                    <Label htmlFor="signup-billing-provider">Meio de pagamento</Label>
                    <Select value={provider ?? undefined} onValueChange={(value) => {
                      setProvider(value as SaaSBillingProvider);
                      checkoutIdempotencyKey.current = newCheckoutIdempotencyKey();
                      setCheckoutState('idle');
                      setCheckoutUrl(null);
                      setCheckoutError(null);
                    }}>
                      <SelectTrigger id="signup-billing-provider" aria-label="Meio de pagamento da contratação">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {availableProviders.map((item) => (
                          <SelectItem key={item} value={item}>{PROVIDER_LABELS[item]}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  {checkoutState === 'ready' && checkoutUrl ? (
                    <Button className="w-full" asChild>
                      <a href={checkoutUrl}>Abrir pagamento seguro<ArrowRight className="ml-2 h-4 w-4" /></a>
                    </Button>
                  ) : (
                    <Button className="w-full" disabled={!provider || checkoutState === 'creating'} onClick={prepareCheckout}>
                      {checkoutState === 'creating' && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                      Preparar pagamento seguro
                    </Button>
                  )}
                  {checkoutError && (
                    <Alert variant="destructive">
                      <AlertTitle>Checkout não iniciado</AlertTitle>
                      <AlertDescription>{checkoutError}</AlertDescription>
                    </Alert>
                  )}
                  <p className="text-xs text-muted-foreground">Os dados de pagamento são coletados pelo provedor escolhido, não pelo Meu Executivo.</p>
                </div>
              ) : (
                <Alert>
                  <CreditCard className="h-4 w-4" />
                  <AlertTitle>Ativação assistida</AlertTitle>
                  <AlertDescription>{account.providerMessage}</AlertDescription>
                </Alert>
              )}
            </div>
          )}

          {/* Próximos passos */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-left mb-10">
            {NEXT_STEPS.map((step, i) => (
              <div key={i} className="bg-card rounded-xl border border-border p-5">
                <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center mb-3">
                  <step.icon className="h-5 w-5 text-primary" />
                </div>
                <h3 className="font-semibold text-sm mb-1">{step.title}</h3>
                <p className="text-xs text-muted-foreground mb-3 leading-relaxed">{step.description}</p>
                <Link
                  to={step.link}
                  className="text-xs font-medium text-primary hover:underline underline-offset-2 flex items-center gap-1"
                >
                  {step.cta} <ArrowRight className="h-3 w-3" />
                </Link>
              </div>
            ))}
          </div>

          <Button size="lg" variant={checkoutRequired ? 'outline' : 'default'} className={checkoutRequired ? 'h-12 px-8' : 'bg-brand-gradient text-white hover:opacity-90 shadow-glow h-12 px-8'} asChild>
            <Link to="/onboarding">
              Iniciar configuração do workspace
              <ArrowRight className="ml-2 h-4 w-4" />
            </Link>
          </Button>

          <p className="text-xs text-muted-foreground mt-4">
            Dúvidas? <a href={`mailto:${BRAND.supportEmail}`} className="text-primary underline-offset-2 hover:underline">{BRAND.supportEmail}</a>
          </p>
        </div>
      </main>
    </div>
  );
}
