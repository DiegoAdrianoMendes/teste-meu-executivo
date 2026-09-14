import { useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Award,
  Check,
  Clock3,
  Copy,
  Gift,
  Link2,
  LoaderCircle,
  ShieldCheck,
  Sparkles,
  TicketPercent,
  UsersRound,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useToast } from '@/hooks/use-toast';
import {
  saasBenefitsApi,
  type BenefitGrant,
  type BenefitUnit,
} from '@/services/saas-benefits';
import { useAppStore } from '@/store/app-context';

const statusLabels: Record<string, string> = {
  registered: 'Indicação registrada',
  qualified: 'Qualificada',
  payment_hold: 'Aguardando 30 dias',
  eligible: 'Benefício disponível',
  rewarded: 'Recompensa liberada',
  reversed: 'Revertida',
  pending: 'Aguardando aprovação',
  available: 'Disponível',
  applied: 'Aplicado',
  expired: 'Expirado',
  candidate: 'Em avaliação',
  active: 'Partner ativo',
  suspended: 'Partner suspenso',
};

const unitLabels: Record<BenefitUnit, string> = {
  days: 'dias',
  months: 'meses',
  tokens: 'tokens',
  percentage: '%',
  cents: 'centavos',
};

function formatDate(value?: string | null) {
  if (!value) return 'Ainda não definido';
  return new Intl.DateTimeFormat('pt-BR', {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(new Date(value));
}

function amountLabel(benefit: Pick<BenefitGrant, 'amount' | 'unit'>) {
  const amount = Number(benefit.amount);
  if (benefit.unit === 'cents') {
    return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(amount / 100);
  }
  if (benefit.unit === 'percentage') return `${amount}%`;
  return `${new Intl.NumberFormat('pt-BR').format(amount)} ${unitLabels[benefit.unit]}`;
}

function StatusBadge({ value }: { value: string }) {
  return <Badge variant="outline">{statusLabels[value] ?? value}</Badge>;
}

function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
      {children}
    </div>
  );
}

export default function BenefitsPage() {
  const { state } = useAppStore();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const isOwner = state.session?.activeRole === 'workspace_owner';
  const [referralCode, setReferralCode] = useState('');
  const [couponCode, setCouponCode] = useState('');
  const referralCommand = useRef({ value: '', key: '' });
  const couponCommand = useRef({ value: '', key: '' });

  const stableCommandKey = (
    command: React.MutableRefObject<{ value: string; key: string }>,
    value: string,
  ) => {
    if (command.current.value !== value || !command.current.key) {
      command.current = { value, key: crypto.randomUUID() };
    }
    return command.current.key;
  };

  const overview = useQuery({
    queryKey: ['saas-benefits', 'workspace'],
    queryFn: saasBenefitsApi.workspaceOverview,
    enabled: isOwner,
    retry: false,
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['saas-benefits'] });

  const createProfile = useMutation({
    mutationFn: saasBenefitsApi.createProfile,
    onSuccess: async () => {
      await refresh();
      toast({ title: 'Link de indicação criado', description: 'O link pertence ao seu escritório.' });
    },
    onError: (error) => toast({
      title: 'Não foi possível criar o link',
      description: error instanceof Error ? error.message : 'Tente novamente.',
      variant: 'destructive',
    }),
  });

  const claimReferral = useMutation({
    mutationFn: (code: string) => {
      const normalized = code.trim().toUpperCase();
      return saasBenefitsApi.claimReferral(normalized, stableCommandKey(referralCommand, normalized));
    },
    onSuccess: async () => {
      referralCommand.current = { value: '', key: '' };
      setReferralCode('');
      await refresh();
      toast({ title: 'Indicação vinculada', description: 'A validação seguirá a regra comercial vigente.' });
    },
    onError: (error) => toast({
      title: 'Não foi possível vincular a indicação',
      description: error instanceof Error ? error.message : 'Confira o código informado.',
      variant: 'destructive',
    }),
  });

  const redeemCoupon = useMutation({
    mutationFn: (code: string) => {
      const normalized = code.trim().toUpperCase();
      return saasBenefitsApi.redeemCoupon(normalized, stableCommandKey(couponCommand, normalized));
    },
    onSuccess: async () => {
      couponCommand.current = { value: '', key: '' };
      setCouponCode('');
      await refresh();
      toast({ title: 'Cupom resgatado', description: 'O benefício e sua trilha já foram registrados.' });
    },
    onError: (error) => toast({
      title: 'Não foi possível resgatar o cupom',
      description: error instanceof Error ? error.message : 'Confira o código e tente novamente.',
      variant: 'destructive',
    }),
  });

  const data = overview.data;
  const referralLink = data?.profile
    ? `${window.location.origin}${data.profile.link}`
    : '';
  const rewardedCount = data?.referrals.filter((item) => item.status === 'rewarded').length ?? 0;
  const appliedBenefits = data?.benefits.filter((item) => item.status === 'applied').length ?? 0;
  const partnerReferralTarget = data?.policy.partnerMinActiveReferrals ?? 1;
  const partnerMrrTarget = data?.policy.partnerMinReferredMrrCents ?? 0;
  const partnerReferralProgress = Math.min(
    100,
    ((data?.partner?.activeReferrals ?? 0) / Math.max(1, partnerReferralTarget)) * 100,
  );
  const partnerMrrProgress = partnerMrrTarget === 0
    ? 100
    : Math.min(100, ((data?.partner?.referredMrrCents ?? 0) / partnerMrrTarget) * 100);
  const rewardSummary = useMemo(() => {
    if (!data) return '';
    return Object.entries(data.policy.rewardMonthsByPlan)
      .map(([plan, months]) => `${plan}: ${months} ${months === 1 ? 'mês' : 'meses'}`)
      .join(' · ');
  }, [data]);

  if (!isOwner) {
    return (
      <div className="mx-auto grid min-h-[55vh] max-w-2xl place-items-center">
        <section className="w-full rounded-lg border bg-card p-8 text-center">
          <ShieldCheck className="mx-auto h-8 w-8 text-primary" />
          <h2 className="mt-4 text-lg font-semibold">Acesso do titular do escritório</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            O link, as recompensas e o histórico financeiro pertencem ao escritório e são visíveis somente ao titular.
          </p>
        </section>
      </div>
    );
  }

  if (overview.isLoading) {
    return <div className="grid min-h-[55vh] place-items-center"><LoaderCircle className="h-6 w-6 animate-spin text-primary" /></div>;
  }

  if (overview.error || !data) {
    return (
      <div className="mx-auto max-w-2xl rounded-lg border bg-card p-6 text-center">
        <h2 className="text-lg font-semibold">Não foi possível carregar o programa</h2>
        <p className="mt-2 text-sm text-muted-foreground">{overview.error instanceof Error ? overview.error.message : 'Tente novamente.'}</p>
        <Button className="mt-4" onClick={() => void overview.refetch()}>Tentar novamente</Button>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-[1420px] space-y-5" data-testid="workspace-benefits-page">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Gift className="h-5 w-5 text-primary" />
            <h2 className="text-xl font-semibold">Indique e ganhe</h2>
          </div>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Benefícios para o escritório após o primeiro pagamento da indicação e o período de confirmação de {data.policy.holdDaysAfterFirstPayment} dias.
          </p>
        </div>
        <Badge variant="outline">Exclusivo do titular</Badge>
      </header>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          { label: 'Escritórios indicados', value: data.referrals.length, icon: UsersRound },
          { label: 'Recompensas liberadas', value: rewardedCount, icon: Award },
          { label: 'Benefícios aplicados', value: appliedBenefits, icon: Check },
          { label: 'Status Partner', value: statusLabels[data.partner?.status ?? 'candidate'] ?? 'Em avaliação', icon: Sparkles },
        ].map((item) => (
          <div key={item.label} className="rounded-lg border bg-card p-4">
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs text-muted-foreground">{item.label}</p>
              <item.icon className="h-4 w-4 text-primary" />
            </div>
            <p className="mt-2 text-lg font-semibold">{item.value}</p>
          </div>
        ))}
      </section>

      <section className="rounded-lg border bg-card p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <Link2 className="h-4 w-4 text-primary" />
              <h2 className="text-sm font-semibold">Link do seu escritório</h2>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              Somente o titular acessa este link. A atribuição é registrada de forma idempotente no cadastro do novo escritório.
            </p>
            {data.profile ? (
              <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                <Input readOnly value={referralLink} className="font-mono text-xs" />
                <Button
                  type="button"
                  variant="outline"
                  onClick={async () => {
                    await navigator.clipboard.writeText(referralLink);
                    toast({ title: 'Link copiado' });
                  }}
                >
                  <Copy className="mr-2 h-4 w-4" />Copiar
                </Button>
              </div>
            ) : (
              <Button className="mt-3" onClick={() => createProfile.mutate()} disabled={createProfile.isPending}>
                {createProfile.isPending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}
                Criar link do escritório
              </Button>
            )}
          </div>
          <div className="max-w-xl rounded-md border bg-secondary/40 px-4 py-3 text-xs leading-5">
            <strong>Regra atual:</strong> {rewardSummary}. O novo escritório recebe {data.policy.welcomeAmount} {data.policy.welcomeBenefit === 'days' ? 'dias' : 'tokens'} de boas-vindas.
          </div>
        </div>
      </section>

      <Tabs defaultValue="indicacoes" className="space-y-4">
        <TabsList className="h-auto flex-wrap justify-start">
          <TabsTrigger value="indicacoes">Indicações</TabsTrigger>
          <TabsTrigger value="beneficios">Benefícios e histórico</TabsTrigger>
          <TabsTrigger value="partner">Partner</TabsTrigger>
          <TabsTrigger value="cupons">Cupons</TabsTrigger>
        </TabsList>

        <TabsContent value="indicacoes" className="space-y-3">
          {data.referrals.length === 0 ? (
            <EmptyState>Nenhum escritório foi atribuído ao seu link até agora.</EmptyState>
          ) : data.referrals.map((item) => (
            <div key={item.id} className="grid gap-3 rounded-lg border bg-card p-4 md:grid-cols-[1fr_auto_auto] md:items-center">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold">{item.referredWorkspaceName}</p>
                <p className="mt-1 text-xs text-muted-foreground">Plano {item.planCode ?? 'a definir'} · indicada em {formatDate(item.createdAt)}</p>
              </div>
              <StatusBadge value={item.status} />
              <div className="text-xs text-muted-foreground md:text-right">
                {item.rewardedAt ? `Liberada em ${formatDate(item.rewardedAt)}` : item.holdUntil ? `Confirmação em ${formatDate(item.holdUntil)}` : 'Aguardando primeiro pagamento'}
              </div>
            </div>
          ))}
        </TabsContent>

        <TabsContent value="beneficios" className="grid gap-4 xl:grid-cols-2">
          <section className="space-y-3">
            <h2 className="text-sm font-semibold">Benefícios do escritório</h2>
            {data.benefits.length === 0 ? <EmptyState>Nenhum benefício registrado.</EmptyState> : data.benefits.map((item) => (
              <div key={item.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-4">
                <div>
                  <p className="text-sm font-semibold">{amountLabel(item)}</p>
                  <p className="mt-1 text-xs text-muted-foreground">{item.reason}</p>
                </div>
                <StatusBadge value={item.status} />
              </div>
            ))}
          </section>
          <section className="space-y-3">
            <h2 className="text-sm font-semibold">Histórico imutável</h2>
            {data.ledger.length === 0 ? <EmptyState>Nenhum evento registrado.</EmptyState> : data.ledger.map((entry) => (
              <div key={entry.id} className="rounded-lg border bg-card p-4">
                <div className="flex items-center justify-between gap-3">
                  <p className="text-xs font-semibold">{entry.eventType}</p>
                  <time className="text-xs text-muted-foreground">{formatDate(entry.createdAt)}</time>
                </div>
                <p className="mt-2 text-xs text-muted-foreground">{entry.reason}</p>
                {entry.ticketId && <p className="mt-1 text-[11px] text-muted-foreground">Ticket vinculado: {entry.ticketId}</p>}
              </div>
            ))}
          </section>
        </TabsContent>

        <TabsContent value="partner" className="space-y-4">
          <section className="rounded-lg border bg-card p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold">Caminho para Partner</h2>
                <p className="mt-1 text-xs text-muted-foreground">A isenção é condicional e reavaliada a cada {data.policy.partnerReviewMonths} meses.</p>
              </div>
              <StatusBadge value={data.partner?.status ?? 'candidate'} />
            </div>
            <div className="mt-5 grid gap-5 md:grid-cols-2">
              <div>
                <div className="flex justify-between text-xs"><span>Indicações ativas</span><strong>{data.partner?.activeReferrals ?? 0}/{partnerReferralTarget}</strong></div>
                <Progress value={partnerReferralProgress} className="mt-2 h-2" />
              </div>
              <div>
                <div className="flex justify-between text-xs"><span>MRR indicado</span><strong>{new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format((data.partner?.referredMrrCents ?? 0) / 100)}</strong></div>
                <Progress value={partnerMrrProgress} className="mt-2 h-2" />
              </div>
            </div>
            <div className="mt-5 flex items-start gap-2 rounded-md border bg-secondary/40 p-3 text-xs text-muted-foreground">
              <Clock3 className="mt-0.5 h-4 w-4 shrink-0" />
              <span>Próxima revisão: {formatDate(data.partner?.nextReviewAt)}. Isenção atual até: {formatDate(data.partner?.exemptionEndsAt)}.</span>
            </div>
          </section>
        </TabsContent>

        <TabsContent value="cupons" className="grid gap-4 xl:grid-cols-2">
          <section className="rounded-lg border bg-card p-5">
            <div className="flex items-center gap-2"><TicketPercent className="h-4 w-4 text-primary" /><h2 className="text-sm font-semibold">Resgatar cupom</h2></div>
            <p className="mt-1 text-xs text-muted-foreground">O resgate respeita plano, período e limites definidos no Studio de Cupons.</p>
            <div className="mt-4 space-y-2">
              <Label htmlFor="benefit-coupon">Código</Label>
              <div className="flex gap-2">
                <Input id="benefit-coupon" value={couponCode} onChange={(event) => setCouponCode(event.target.value.toUpperCase())} placeholder="SEU-CUPOM" />
                <Button disabled={couponCode.trim().length < 3 || redeemCoupon.isPending} onClick={() => redeemCoupon.mutate(couponCode.trim())}>
                  {redeemCoupon.isPending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}Resgatar
                </Button>
              </div>
            </div>
            <div className="mt-6 border-t pt-5">
              <Label htmlFor="referral-code">Recebeu um código de indicação?</Label>
              <div className="mt-2 flex gap-2">
                <Input id="referral-code" value={referralCode} onChange={(event) => setReferralCode(event.target.value.toUpperCase())} placeholder="CÓDIGO DO ESCRITÓRIO" />
                <Button variant="outline" disabled={referralCode.trim().length < 4 || claimReferral.isPending} onClick={() => claimReferral.mutate(referralCode.trim())}>Vincular</Button>
              </div>
            </div>
          </section>
          <section className="space-y-3">
            <h2 className="text-sm font-semibold">Cupons já utilizados</h2>
            {data.couponRedemptions.length === 0 ? <EmptyState>Nenhum cupom resgatado por este escritório.</EmptyState> : data.couponRedemptions.map((item) => (
              <div key={item.id} className="flex items-center justify-between gap-3 rounded-lg border bg-card p-4">
                <div><p className="font-mono text-sm font-semibold">{item.code}</p><p className="mt-1 text-xs text-muted-foreground">{item.name} · {formatDate(item.redeemedAt)}</p></div>
                <StatusBadge value={item.status} />
              </div>
            ))}
          </section>
        </TabsContent>
      </Tabs>
    </div>
  );
}
