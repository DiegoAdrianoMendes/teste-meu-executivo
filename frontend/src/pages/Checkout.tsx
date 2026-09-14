/**
 * Checkout público legado do Meu Executivo.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { DesbLogo } from '@/components/ui/DesbLogo';
import { CheckCircle2, ArrowRight, Sparkles } from 'lucide-react';
import {
  createIdempotencyKey,
  passwordPolicyError,
  requestSignup,
} from '@/services/identity';
import {
  fetchPublicCommercialCatalog,
  type PublicCommercialOffer,
} from '@/services/commercial-self-service';

const PLAN_ALIASES: Record<string, string> = {
  basic: 'crm_start',
  starter: 'crm_start',
  start: 'crm_start',
  pro: 'crm_advanced',
  advanced: 'crm_advanced',
  business: 'crm_evolution',
  scale: 'crm_evolution',
  evolution: 'crm_evolution',
};

function formatMonthlyPrice(offer: PublicCommercialOffer): string {
  if (offer.isQuoteOnly || offer.priceMonthlyCents == null) return 'Sob consulta';
  return `${new Intl.NumberFormat('pt-BR', { style: 'currency', currency: offer.currency }).format(offer.priceMonthlyCents / 100)}/mês`;
}

export default function Checkout() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const requestedPlan = params.get('plan') ?? 'crm_advanced';
  const requestedSku = PLAN_ALIASES[requestedPlan] ?? requestedPlan;
  const [offers, setOffers] = useState<PublicCommercialOffer[]>([]);
  const [catalogState, setCatalogState] = useState<'loading' | 'available' | 'not_published' | 'error'>('loading');
  const plan = useMemo(
    () => offers.find((offer) => offer.sku === requestedSku) ?? null,
    [offers, requestedSku],
  );

  const [step, setStep] = useState<'info' | 'submitting'>('info');
  const [form, setForm] = useState({ name: '', email: '', workspace: '', password: '' });
  const [error, setError] = useState<string | null>(null);
  const idempotencyKey = useRef(createIdempotencyKey());

  useEffect(() => {
    let active = true;
    fetchPublicCommercialCatalog()
      .then((catalog) => {
        if (!active) return;
        const basePlans = catalog.items.filter((offer) => offer.family === 'crm' && offer.kind === 'base_plan');
        setOffers(basePlans);
        setCatalogState(basePlans.length ? 'available' : 'not_published');
      })
      .catch(() => active && setCatalogState('error'));
    return () => { active = false; };
  }, []);

  const set = (field: keyof typeof form) => (event: React.ChangeEvent<HTMLInputElement>) => {
    idempotencyKey.current = createIdempotencyKey();
    setForm((previous) => ({ ...previous, [field]: event.target.value }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!plan) {
      setError('Nenhum plano está publicado para contratação. Solicite uma demonstração.');
      return;
    }
    if (!form.name || !form.email || !form.workspace || !form.password) {
      setError('Preencha todos os campos.');
      return;
    }
    const passwordError = passwordPolicyError(form.password);
    if (passwordError) {
      setError(passwordError);
      return;
    }
    setStep('submitting');
    try {
      const accepted = await requestSignup({
        name: form.name,
        email: form.email,
        workspaceName: form.workspace,
        password: form.password,
        plan: plan.sku,
      }, idempotencyKey.current);
      const verificationQuery = new URLSearchParams({
        delivery: accepted.deliveryId,
        mode: accepted.deliveryMode,
      });
      navigate(`/verify-account?${verificationQuery.toString()}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Não foi possível solicitar a conta.');
      setStep('info');
    }
  };

  return (
    <div className="min-h-screen bg-background flex flex-col">
      {/* Header */}
      <header className="border-b border-border bg-card/50 backdrop-blur-sm">
        <div className="max-w-5xl mx-auto px-4 h-14 flex items-center justify-between">
          <Link to="/">
            <DesbLogo variant="full" size={48} />
          </Link>
          <div className="text-xs text-muted-foreground flex items-center gap-1">
            <CheckCircle2 className="h-3.5 w-3.5 text-primary" />
            Contratação segura · Condições no contrato
          </div>
        </div>
      </header>

      <main className="flex-1 flex items-center justify-center p-6">
        <div className="w-full max-w-4xl grid grid-cols-1 md:grid-cols-2 gap-8">

          {/* Left — Resumo do plano */}
          <div className={`rounded-lg p-8 border ${plan?.isRecommended ? 'border-primary/30 bg-primary/5 ring-1 ring-primary/10' : 'border-border bg-card'}`}>
            {plan?.isRecommended && (
              <div className="badge-ai mb-4 w-fit">
                <Sparkles className="h-3 w-3" /> Mais escolhido
              </div>
            )}
            <h2 className="text-lg font-semibold mb-1">{plan ? `Plano ${plan.name}` : 'Planos Meu Executivo'}</h2>
            <p className="text-2xl font-bold tabular-nums text-primary mb-6">
              {catalogState === 'loading' ? 'Carregando catálogo…' : plan ? formatMonthlyPrice(plan) : 'Contratação indisponível'}
            </p>

            <div className="space-y-3 mb-8">
              {(plan?.highlights ?? []).map(f => (
                <div key={f} className="flex items-center gap-2 text-sm">
                  <CheckCircle2 className="h-4 w-4 text-primary shrink-0" />
                  <span>{f}</span>
                </div>
              ))}
            </div>

            <div className="rounded-xl bg-primary/10 p-4 text-sm">
              <p className="font-semibold text-primary mb-1">Ativação após verificação do e-mail</p>
              <p className="text-muted-foreground text-xs">
                O período de teste e as condições comerciais são registrados no contrato canônico da conta.
              </p>
            </div>

            <div className="mt-6 space-y-2 text-xs text-muted-foreground">
              <div className="flex items-center gap-1.5"><CheckCircle2 className="h-3 w-3 text-success" /> Controles de privacidade e auditoria</div>
              <div className="flex items-center gap-1.5"><CheckCircle2 className="h-3 w-3 text-success" /> Suporte em português</div>
              <div className="flex items-center gap-1.5"><CheckCircle2 className="h-3 w-3 text-success" /> Configuração guiada do workspace</div>
            </div>

            <div className="mt-6 pt-4 border-t border-border/50">
              <p className="text-xs text-muted-foreground">
                Quer outro plano?{' '}
                <Link to="/landing#pricing" className="text-primary underline-offset-2 hover:underline">Ver todos</Link>
              </p>
            </div>
          </div>

          {/* Right — Formulário */}
          <div className="bg-card rounded-lg border border-border p-8">
            <h3 className="text-lg font-semibold mb-1">Criar sua conta</h3>
            <p className="text-sm text-muted-foreground mb-6">O workspace e o trial são ativados após a verificação do e-mail.</p>

            <form onSubmit={handleSubmit} className="space-y-4">
              {error && (
                <div className="text-sm text-destructive bg-destructive/10 rounded-lg p-3">
                  {error}
                </div>
              )}
              {catalogState === 'not_published' && (
                <div className="text-sm text-muted-foreground bg-muted rounded-lg p-3">
                  O catálogo ainda não foi publicado para contratação. Fale com nosso time para receber uma proposta.
                </div>
              )}
              {catalogState === 'error' && (
                <div className="text-sm text-destructive bg-destructive/10 rounded-lg p-3">
                  O catálogo comercial está indisponível. Nenhum preço foi estimado ou substituído por zero.
                </div>
              )}

              <div className="space-y-1.5">
                <label className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Seu nome</label>
                <Input placeholder="Dra. Ana Souza" value={form.name} onChange={set('name')} disabled={step === 'submitting'} />
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-medium uppercase tracking-wider text-muted-foreground">E-mail profissional</label>
                <Input type="email" placeholder="ana@escritorio.com.br" value={form.email} onChange={set('email')} disabled={step === 'submitting'} />
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Nome do escritório</label>
                <Input placeholder="Souza Advogados" value={form.workspace} onChange={set('workspace')} disabled={step === 'submitting'} />
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Senha</label>
                <Input type="password" placeholder="10 a 72 bytes em UTF-8" value={form.password} onChange={set('password')} disabled={step === 'submitting'} />
              </div>

              <Button
                type="submit"
                className="w-full h-12 bg-brand-gradient text-white hover:opacity-90 shadow-glow text-sm"
                disabled={step === 'submitting' || catalogState !== 'available' || !plan}
              >
                {step === 'submitting' ? (
                  <span className="flex items-center gap-2">
                    <span className="h-4 w-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    Registrando solicitação...
                  </span>
                ) : (
                  <>Continuar para verificação <ArrowRight className="ml-1 h-4 w-4" /></>
                )}
              </Button>
            </form>

            <p className="text-2xs text-muted-foreground text-center mt-4">
              Ao criar conta você concorda com os{' '}
              <Link to="/termos" className="underline underline-offset-2">Termos de Uso</Link>
              {' '}e{' '}
              <Link to="/privacidade" className="underline underline-offset-2">Política de Privacidade</Link>.
            </p>

            <div className="text-center mt-4">
              <Link to="/login" className="text-xs text-muted-foreground hover:text-foreground transition-colors">
                Já tem conta? <span className="text-primary">Entrar</span>
              </Link>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
