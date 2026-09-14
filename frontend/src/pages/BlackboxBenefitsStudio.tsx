import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Award,
  CalendarClock,
  CheckCircle2,
  Gift,
  History,
  LoaderCircle,
  Play,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  UsersRound,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import { canPlatform } from '@/lib/platform-access';
import {
  saasBenefitsApi,
  type BenefitGrant,
  type BenefitUnit,
  type PlatformReferral,
  type ReferralPolicyRules,
} from '@/services/saas-benefits';
import { resolvePlatformEntity } from '@/services/platform-search';
import { useAppStore } from '@/store/app-context';
import { PlatformEntityCombobox } from '@/components/platform/PlatformEntityCombobox';

const defaultRules: ReferralPolicyRules = {
  rewardMonthsByPlan: { essencial: 1, escritorio: 2, performance: 3, custom: 3 },
  welcomeBenefit: 'days',
  welcomeAmount: 14,
  holdDaysAfterFirstPayment: 30,
  partnerMinActiveReferrals: 3,
  partnerMinReferredMrrCents: 0,
  partnerReviewMonths: 3,
};

const unitLabels: Record<BenefitUnit, string> = {
  days: 'Dias grátis',
  months: 'Meses grátis',
  tokens: 'Tokens',
  percentage: 'Desconto percentual',
  cents: 'Crédito em centavos',
};

const statusLabels: Record<string, string> = {
  registered: 'Registrada',
  qualified: 'Qualificada',
  payment_hold: 'Em confirmação',
  eligible: 'Elegível',
  rewarded: 'Recompensada',
  reversed: 'Revertida',
  pending: 'Pendente',
  available: 'Aprovado',
  applied: 'Aplicado',
  expired: 'Expirado',
  candidate: 'Candidato',
  active: 'Ativo',
  suspended: 'Suspenso',
};

function dateTime(value?: string | null) {
  if (!value) return '—';
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value));
}

function currency(cents: number) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(cents / 100);
}

function statusBadge(value: string) {
  return <Badge variant="outline">{statusLabels[value] ?? value}</Badge>;
}

function FieldHelp({ children }: { children: React.ReactNode }) {
  return <p className="mt-1 text-[11px] leading-4 text-muted-foreground">{children}</p>;
}

export default function BlackboxBenefitsStudio() {
  const { state } = useAppStore();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const canView = canPlatform(state.currentUser, 'benefits', 'view');
  const canGrant = canPlatform(state.currentUser, 'benefits', 'grant');
  const canApprove = canPlatform(state.currentUser, 'benefits', 'approve');
  const canReverse = canPlatform(state.currentUser, 'benefits', 'reverse');
  const canManagePolicy = canPlatform(state.currentUser, 'benefits', 'manage_policy');
  const canManageReferrals = canPlatform(state.currentUser, 'referrals', 'manage');
  const canManagePartners = canPlatform(state.currentUser, 'partners', 'manage');

  const [rules, setRules] = useState(defaultRules);
  const [policyReason, setPolicyReason] = useState('');
  const [newPlanCode, setNewPlanCode] = useState('');
  const [grantWorkspaceId, setGrantWorkspaceId] = useState('');
  const [grantUnit, setGrantUnit] = useState<BenefitUnit>('days');
  const [grantAmount, setGrantAmount] = useState('14');
  const [grantReason, setGrantReason] = useState('');
  const [grantTicketId, setGrantTicketId] = useState('');
  const [decisionReason, setDecisionReason] = useState('');
  const [decisionTicketId, setDecisionTicketId] = useState('');
  const [paymentReferral, setPaymentReferral] = useState<PlatformReferral | null>(null);
  const [paymentPlan, setPaymentPlan] = useState('');
  const [paymentReference, setPaymentReference] = useState('');
  const [paymentDate, setPaymentDate] = useState(() => new Date().toISOString().slice(0, 16));
  const [grantPage, setGrantPage] = useState(0);
  const [ledgerPage, setLedgerPage] = useState(0);
  const [policyHistoryPage, setPolicyHistoryPage] = useState(0);
  const [referralPage, setReferralPage] = useState(0);
  const [partnerPage, setPartnerPage] = useState(0);
  const pageSize = 20;
  const grantCommand = useRef({ fingerprint: '', key: '' });

  const overview = useQuery({ queryKey: ['blackbox', 'benefits', 'overview'], queryFn: saasBenefitsApi.platformOverview, enabled: canView });
  const policy = useQuery({ queryKey: ['blackbox', 'benefits', 'policy'], queryFn: saasBenefitsApi.policy, enabled: canView });
  const history = useQuery({ queryKey: ['blackbox', 'benefits', 'policy-history', policyHistoryPage], queryFn: () => saasBenefitsApi.policyHistoryPage({ limit: pageSize, offset: policyHistoryPage * pageSize }), enabled: canView });
  const referrals = useQuery({ queryKey: ['blackbox', 'benefits', 'referrals', referralPage], queryFn: () => saasBenefitsApi.referralsPage({ limit: pageSize, offset: referralPage * pageSize }), enabled: canManageReferrals });
  const grants = useQuery({ queryKey: ['blackbox', 'benefits', 'grants', grantPage], queryFn: () => saasBenefitsApi.grantsPage({ limit: pageSize, offset: grantPage * pageSize }), enabled: canView });
  const ledger = useQuery({ queryKey: ['blackbox', 'benefits', 'ledger', ledgerPage], queryFn: () => saasBenefitsApi.ledgerPage({ limit: pageSize, offset: ledgerPage * pageSize }), enabled: canView });
  const partners = useQuery({ queryKey: ['blackbox', 'benefits', 'partners', partnerPage], queryFn: () => saasBenefitsApi.partnersPage({ limit: pageSize, offset: partnerPage * pageSize }), enabled: canManagePartners });

  useEffect(() => {
    if (policy.data?.rules) setRules(policy.data.rules);
  }, [policy.data?.rules]);

  const workspaceIds = useMemo(() => Array.from(new Set([
    ...(grants.data?.items ?? []).map((item) => item.workspaceId),
    ...(ledger.data?.items ?? []).map((item) => item.workspaceId).filter((value): value is string => Boolean(value)),
  ])), [grants.data, ledger.data]);
  const workspaceLabels = useQuery({
    queryKey: ['blackbox', 'benefits', 'workspace-labels', workspaceIds],
    queryFn: () => resolvePlatformEntity({
      entityType: 'accounts',
      purpose: 'benefit_grant',
      ids: workspaceIds,
    }),
    enabled: canView && workspaceIds.length > 0,
    retry: false,
  });
  const workspaceNames = useMemo(
    () => new Map((workspaceLabels.data?.items ?? []).map((item) => [item.id, item.label])),
    [workspaceLabels.data],
  );
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['blackbox', 'benefits'] });

  const publishPolicy = useMutation({
    mutationFn: () => saasBenefitsApi.publishPolicy(rules, policyReason),
    onSuccess: async () => {
      setPolicyReason('');
      await refresh();
      toast({ title: 'Política publicada', description: 'A nova versão já está registrada no histórico imutável.' });
    },
    onError: showError('Não foi possível publicar a política'),
  });

  const createGrant = useMutation({
    mutationFn: async (applyNow: boolean) => {
      const payload = {
        workspaceId: grantWorkspaceId,
        benefitType: grantUnit === 'tokens' ? 'bonus_tokens' : 'free_access',
        unit: grantUnit,
        amount: Number(grantAmount),
        reason: grantReason,
        idempotencyKey: '',
        ticketId: grantTicketId.trim() || null,
        sourceType: 'manual_blackbox',
      };
      const fingerprint = JSON.stringify({ ...payload, applyNow });
      if (grantCommand.current.fingerprint !== fingerprint || !grantCommand.current.key) {
        grantCommand.current = { fingerprint, key: crypto.randomUUID() };
      }
      payload.idempotencyKey = grantCommand.current.key;
      return applyNow
        ? saasBenefitsApi.createAndApplyGrant(payload)
        : saasBenefitsApi.createGrant(payload);
    },
    onSuccess: async (_, applyNow) => {
      grantCommand.current = { fingerprint: '', key: '' };
      setGrantReason('');
      setGrantTicketId('');
      await refresh();
      toast({
        title: applyNow ? 'Benefício concedido e aplicado' : 'Benefício criado para aprovação',
        description: 'Todas as etapas e o responsável foram registrados.',
      });
    },
    onError: showError('Não foi possível registrar o benefício'),
  });

  const decideGrant = useMutation({
    mutationFn: ({ grant, action }: { grant: BenefitGrant; action: 'approve' | 'apply' | 'reverse' }) => (
      saasBenefitsApi.decideGrant(grant.id, action, decisionReason, decisionTicketId.trim() || null)
    ),
    onSuccess: async () => {
      await refresh();
      toast({ title: 'Benefício atualizado', description: 'A decisão foi registrada no histórico.' });
    },
    onError: showError('Não foi possível alterar o benefício'),
  });

  const processEligible = useMutation({
    mutationFn: saasBenefitsApi.processEligible,
    onSuccess: async (result) => {
      await refresh();
      toast({ title: 'Elegibilidade processada', description: `${result.processed} indicação(ões) liberada(s).` });
    },
    onError: showError('Não foi possível processar as indicações'),
  });

  const recordPayment = useMutation({
    mutationFn: () => {
      if (!paymentReferral) throw new Error('Selecione uma indicação.');
      return saasBenefitsApi.recordFirstPayment(paymentReferral.id, {
        paidAt: new Date(paymentDate).toISOString(),
        planCode: paymentPlan,
        paymentReference,
      });
    },
    onSuccess: async () => {
      setPaymentReferral(null);
      setPaymentPlan('');
      setPaymentReference('');
      await refresh();
      toast({ title: 'Primeiro pagamento registrado', description: 'O período de confirmação já está em andamento.' });
    },
    onError: showError('Não foi possível registrar o pagamento'),
  });

  const evaluatePartner = useMutation({
    mutationFn: saasBenefitsApi.evaluatePartner,
    onSuccess: async () => {
      await refresh();
      toast({ title: 'Partner reavaliado', description: 'Elegibilidade, MRR e período foram recalculados.' });
    },
    onError: showError('Não foi possível reavaliar o Partner'),
  });

  function showError(title: string) {
    return (error: unknown) => toast({
      title,
      description: error instanceof Error ? error.message : 'Tente novamente.',
      variant: 'destructive',
    });
  }

  if (!canView) {
    return (
      <div className="grid min-h-[60vh] place-items-center p-6">
        <section className="max-w-lg rounded-lg border bg-white p-7 text-center">
          <ShieldCheck className="mx-auto h-7 w-7 text-blue-600" />
          <h1 className="mt-3 text-lg font-semibold">Módulo não autorizado</h1>
          <p className="mt-2 text-sm text-muted-foreground">Seu pacote de acesso Blackbox não permite visualizar benefícios comerciais.</p>
        </section>
      </div>
    );
  }

  const totalReferrals = Object.values(overview.data?.referrals ?? {}).reduce((sum, value) => sum + value, 0);
  const pendingBenefits = Number(overview.data?.benefits.pending ?? 0);

  return (
    <div className="mx-auto w-full max-w-[1500px] space-y-5 p-6" data-testid="blackbox-benefits-studio">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2"><Gift className="h-5 w-5 text-blue-600" /><h1 className="text-xl font-semibold">Indicações e benefícios</h1></div>
          <p className="mt-1 text-sm text-muted-foreground">Políticas versionadas, recompensas, gratuidades e Partner com lastro de auditoria.</p>
        </div>
        <Button variant="outline" onClick={() => void refresh()}><RefreshCw className="mr-2 h-4 w-4" />Atualizar</Button>
      </header>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {[
          { label: 'Indicações', value: totalReferrals, icon: UsersRound },
          { label: 'Benefícios pendentes', value: pendingBenefits, icon: CalendarClock },
          { label: 'Partners ativos', value: overview.data?.activePartners ?? 0, icon: Award },
          { label: 'Cupons ativos', value: overview.data?.activeCoupons ?? 0, icon: Gift },
          { label: 'Cupons resgatados', value: overview.data?.couponRedemptions ?? 0, icon: CheckCircle2 },
        ].map((item) => (
          <div key={item.label} className="rounded-lg border bg-white p-4">
            <div className="flex items-center justify-between"><span className="text-xs text-muted-foreground">{item.label}</span><item.icon className="h-4 w-4 text-blue-600" /></div>
            <p className="mt-2 text-lg font-semibold">{item.value}</p>
          </div>
        ))}
      </section>

      <Tabs defaultValue="politica" className="space-y-4">
        <TabsList className="h-auto flex-wrap justify-start">
          <TabsTrigger value="politica">Política do programa</TabsTrigger>
          <TabsTrigger value="indicacoes">Indicações</TabsTrigger>
          <TabsTrigger value="beneficios">Conceder benefício</TabsTrigger>
          <TabsTrigger value="historico">Histórico</TabsTrigger>
          <TabsTrigger value="partner">Partner</TabsTrigger>
        </TabsList>

        <TabsContent value="politica" className="space-y-4">
          <section className="rounded-lg border bg-white p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div><h2 className="text-sm font-semibold">Regras vigentes</h2><p className="mt-1 text-xs text-muted-foreground">Versão {policy.data?.version ?? 0} · alterações valem após uma nova publicação.</p></div>
              {statusBadge(policy.data?.status ?? 'default')}
            </div>
            <div className="mt-5 grid gap-5 xl:grid-cols-2">
              <div className="space-y-3">
                <Label>Meses grátis por plano</Label>
                {Object.entries(rules.rewardMonthsByPlan).map(([plan, months]) => (
                  <div key={plan} className="grid grid-cols-[1fr_100px_36px] gap-2">
                    <Input value={plan} onChange={(event) => setRules((current) => {
                      const next = { ...current.rewardMonthsByPlan };
                      delete next[plan];
                      next[event.target.value.trim().toLowerCase()] = months;
                      return { ...current, rewardMonthsByPlan: next };
                    })} />
                    <Input type="number" min={1} max={3} value={months} onChange={(event) => setRules((current) => ({ ...current, rewardMonthsByPlan: { ...current.rewardMonthsByPlan, [plan]: Number(event.target.value) } }))} />
                    <Button type="button" size="icon" variant="ghost" title="Remover plano" onClick={() => setRules((current) => {
                      const next = { ...current.rewardMonthsByPlan };
                      delete next[plan];
                      return { ...current, rewardMonthsByPlan: next };
                    })}><Trash2 className="h-4 w-4" /></Button>
                  </div>
                ))}
                <div className="flex gap-2">
                  <Input placeholder="Código de outro plano" value={newPlanCode} onChange={(event) => setNewPlanCode(event.target.value)} />
                  <Button type="button" variant="outline" disabled={!newPlanCode.trim()} onClick={() => {
                    setRules((current) => ({ ...current, rewardMonthsByPlan: { ...current.rewardMonthsByPlan, [newPlanCode.trim().toLowerCase()]: 1 } }));
                    setNewPlanCode('');
                  }}><Plus className="mr-2 h-4 w-4" />Adicionar</Button>
                </div>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div><Label>Boas-vindas</Label><Select value={rules.welcomeBenefit} onValueChange={(value: 'days' | 'tokens') => setRules((current) => ({ ...current, welcomeBenefit: value }))}><SelectTrigger className="mt-2"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="days">Dias grátis</SelectItem><SelectItem value="tokens">Tokens</SelectItem></SelectContent></Select></div>
                <div><Label>Quantidade</Label><Input className="mt-2" type="number" min={1} value={rules.welcomeAmount} onChange={(event) => setRules((current) => ({ ...current, welcomeAmount: Number(event.target.value) }))} /></div>
                <div><Label>Confirmação após pagamento</Label><Input className="mt-2" type="number" min={1} value={rules.holdDaysAfterFirstPayment} onChange={(event) => setRules((current) => ({ ...current, holdDaysAfterFirstPayment: Number(event.target.value) }))} /><FieldHelp>Dias após o primeiro pagamento.</FieldHelp></div>
                <div><Label>Revisão Partner</Label><Input className="mt-2" type="number" min={1} max={12} value={rules.partnerReviewMonths} onChange={(event) => setRules((current) => ({ ...current, partnerReviewMonths: Number(event.target.value) }))} /><FieldHelp>Intervalo em meses.</FieldHelp></div>
                <div><Label>Indicações ativas para Partner</Label><Input className="mt-2" type="number" min={1} value={rules.partnerMinActiveReferrals} onChange={(event) => setRules((current) => ({ ...current, partnerMinActiveReferrals: Number(event.target.value) }))} /></div>
                <div><Label>MRR indicado mínimo</Label><Input className="mt-2" type="number" min={0} value={rules.partnerMinReferredMrrCents / 100} onChange={(event) => setRules((current) => ({ ...current, partnerMinReferredMrrCents: Math.round(Number(event.target.value) * 100) }))} /><FieldHelp>Valor em reais.</FieldHelp></div>
              </div>
            </div>
            <div className="mt-5 border-t pt-5">
              <Label htmlFor="policy-reason">Motivo da nova versão</Label>
              <Textarea id="policy-reason" className="mt-2" value={policyReason} onChange={(event) => setPolicyReason(event.target.value)} placeholder="Explique a mudança para a trilha de auditoria." />
              <Button className="mt-3" disabled={!canManagePolicy || policyReason.trim().length < 3 || publishPolicy.isPending} onClick={() => publishPolicy.mutate()}>{publishPolicy.isPending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}Publicar nova versão</Button>
            </div>
          </section>
          <section className="space-y-2">
            <h2 className="text-sm font-semibold">Versões anteriores</h2>
            {history.isError ? <div className="rounded-lg border border-destructive/30 p-6 text-sm text-destructive">Histórico de políticas indisponível.</div> : (history.data?.items ?? []).map((item) => (
              <div key={item.id ?? item.version} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-white p-4">
                <div><p className="text-sm font-semibold">Versão {item.version}</p><p className="mt-1 text-xs text-muted-foreground">{item.reason ?? 'Sem motivo registrado'} · {dateTime(item.effectiveAt)}</p></div>
                {statusBadge(item.status)}
              </div>
            ))}
            {history.data ? <div className="flex items-center justify-between gap-3"><span className="text-xs text-muted-foreground">{history.data.total === 0 ? 'Sem versões anteriores.' : `${history.data.offset + 1}–${Math.min(history.data.offset + history.data.items.length, history.data.total)} de ${history.data.total}`}</span><span className="flex gap-2"><Button size="sm" variant="outline" disabled={policyHistoryPage === 0 || history.isFetching} onClick={() => setPolicyHistoryPage((value) => Math.max(0, value - 1))}>Anterior</Button><Button size="sm" variant="outline" disabled={!history.data.hasMore || history.isFetching} onClick={() => setPolicyHistoryPage((value) => value + 1)}>Próxima</Button></span></div> : null}
          </section>
        </TabsContent>

        <TabsContent value="indicacoes" className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><h2 className="text-sm font-semibold">Indicações atribuídas</h2><p className="mt-1 text-xs text-muted-foreground">Pagamentos normalmente chegam pela integração financeira; o registro manual existe para recuperação auditada.</p></div>
            <Button disabled={!canManageReferrals || processEligible.isPending} onClick={() => processEligible.mutate()}><Play className="mr-2 h-4 w-4" />Processar elegíveis</Button>
          </div>
          {referrals.isError ? <div className="rounded-lg border border-destructive/30 p-8 text-center text-sm text-destructive">Indicações indisponíveis. Nenhum total foi presumido.</div> : (referrals.data?.items ?? []).length === 0 ? <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">Nenhuma indicação registrada.</div> : (referrals.data?.items ?? []).map((item) => (
            <div key={item.id} className="grid gap-3 rounded-lg border bg-white p-4 lg:grid-cols-[1fr_auto_auto] lg:items-center">
              <div><p className="text-sm font-semibold">{item.referredWorkspaceName}</p><p className="mt-1 text-xs text-muted-foreground">Plano {item.planCode ?? 'não informado'} · confirmação {dateTime(item.holdUntil)}</p></div>
              {statusBadge(item.status)}
              {!item.firstPaymentAt && <Button variant="outline" onClick={() => { setPaymentReferral(item); setPaymentPlan(item.planCode ?? ''); }}>Registrar 1º pagamento</Button>}
            </div>
          ))}
          {referrals.data ? <div className="flex items-center justify-between gap-3"><span className="text-xs text-muted-foreground">{referrals.data.total === 0 ? 'Sem indicações.' : `${referrals.data.offset + 1}–${Math.min(referrals.data.offset + referrals.data.items.length, referrals.data.total)} de ${referrals.data.total}`}</span><span className="flex gap-2"><Button size="sm" variant="outline" disabled={referralPage === 0 || referrals.isFetching} onClick={() => setReferralPage((value) => Math.max(0, value - 1))}>Anterior</Button><Button size="sm" variant="outline" disabled={!referrals.data.hasMore || referrals.isFetching} onClick={() => setReferralPage((value) => value + 1)}>Próxima</Button></span></div> : null}
        </TabsContent>

        <TabsContent value="beneficios" className="grid gap-4 xl:grid-cols-[420px_1fr]">
          <section className="rounded-lg border bg-white p-5">
            <h2 className="text-sm font-semibold">Conceder dias, meses ou créditos</h2>
            <p className="mt-1 text-xs text-muted-foreground">A operação gera histórico com responsável, motivo e ticket relacionado.</p>
            <div className="mt-5 space-y-4">
              <div><Label>Conta</Label><PlatformEntityCombobox className="mt-2" entityType="accounts" purpose="benefit_grant" value={grantWorkspaceId || null} onChange={(value) => { setGrantWorkspaceId(value ?? ''); setGrantTicketId(''); }} label="Selecionar conta do benefício" placeholder="Pesquisar conta" /></div>
              <div><Label>Tipo do benefício</Label><Select value={grantUnit} onValueChange={(value: BenefitUnit) => setGrantUnit(value)}><SelectTrigger className="mt-2"><SelectValue /></SelectTrigger><SelectContent>{Object.entries(unitLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>
              <div><Label>Quantidade</Label><Input className="mt-2" type="number" min={1} value={grantAmount} onChange={(event) => setGrantAmount(event.target.value)} /></div>
              <div><Label>Motivo</Label><Textarea className="mt-2" value={grantReason} onChange={(event) => setGrantReason(event.target.value)} placeholder="Ex.: compensação aprovada no ticket de suporte." /></div>
              <div><Label>Ticket vinculado</Label><PlatformEntityCombobox className="mt-2" entityType="tickets" purpose="benefit_grant" workspaceId={grantWorkspaceId || null} value={grantTicketId || null} onChange={(value) => setGrantTicketId(value ?? '')} label="Selecionar ticket do benefício" placeholder={grantWorkspaceId ? 'Pesquisar ticket da conta' : 'Selecione primeiro a conta'} disabled={!grantWorkspaceId} /></div>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" disabled={!canGrant || !grantWorkspaceId || grantReason.trim().length < 3 || createGrant.isPending} onClick={() => createGrant.mutate(false)}>Criar para aprovação</Button>
                <Button disabled={!canGrant || !canApprove || !grantWorkspaceId || grantReason.trim().length < 3 || createGrant.isPending} onClick={() => createGrant.mutate(true)}>Conceder e aplicar</Button>
              </div>
            </div>
          </section>
          <section className="space-y-3">
            <div className="grid gap-3 md:grid-cols-2">
              <div><Label>Motivo da decisão</Label><Input className="mt-2" value={decisionReason} onChange={(event) => setDecisionReason(event.target.value)} placeholder="Obrigatório para aprovar, aplicar ou reverter" /></div>
              <div><Label>Ticket da decisão</Label><PlatformEntityCombobox className="mt-2" entityType="tickets" purpose="benefit_decision" value={decisionTicketId || null} onChange={(value) => setDecisionTicketId(value ?? '')} label="Selecionar ticket da decisão" placeholder="Pesquisar ticket" /></div>
            </div>
            {grants.isError ? <div className="rounded-lg border border-destructive/30 p-8 text-center text-sm text-destructive">Benefícios indisponíveis. Nenhum total foi presumido.</div> : (grants.data?.items ?? []).map((grant) => (
              <div key={grant.id} className="rounded-lg border bg-white p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div><p className="text-sm font-semibold">{workspaceNames.get(grant.workspaceId) ?? 'Conta removida ou indisponível'}</p><p className="mt-1 text-xs text-muted-foreground">{grant.amount} {unitLabels[grant.unit].toLowerCase()} · {grant.reason}</p></div>
                  {statusBadge(grant.status)}
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {grant.status === 'pending' && <Button size="sm" variant="outline" disabled={!canApprove || decisionReason.trim().length < 3} onClick={() => decideGrant.mutate({ grant, action: 'approve' })}>Aprovar</Button>}
                  {grant.status === 'available' && <Button size="sm" disabled={!canApprove || decisionReason.trim().length < 3} onClick={() => decideGrant.mutate({ grant, action: 'apply' })}>Aplicar</Button>}
                  {['available', 'applied'].includes(grant.status) && <Button size="sm" variant="destructive" disabled={!canReverse || decisionReason.trim().length < 3} onClick={() => decideGrant.mutate({ grant, action: 'reverse' })}>Reverter</Button>}
                  {grant.ticketId && <Badge variant="outline">Ticket {grant.ticketId}</Badge>}
                </div>
              </div>
            ))}
            {grants.data ? <div className="flex items-center justify-between gap-3 pt-2"><span className="text-xs text-muted-foreground">{grants.data.total === 0 ? 'Nenhum benefício encontrado.' : `${grants.data.offset + 1}–${Math.min(grants.data.offset + grants.data.items.length, grants.data.total)} de ${grants.data.total}`}</span><span className="flex gap-2"><Button size="sm" variant="outline" disabled={grantPage === 0 || grants.isFetching} onClick={() => setGrantPage((value) => Math.max(0, value - 1))}>Anterior</Button><Button size="sm" variant="outline" disabled={!grants.data.hasMore || grants.isFetching} onClick={() => setGrantPage((value) => value + 1)}>Próxima</Button></span></div> : null}
          </section>
        </TabsContent>

        <TabsContent value="historico" className="space-y-3">
          <div className="flex items-center gap-2"><History className="h-4 w-4 text-blue-600" /><h2 className="text-sm font-semibold">Razão imutável de benefícios</h2></div>
          {ledger.isError ? <div className="rounded-lg border border-destructive/30 p-8 text-center text-sm text-destructive">Razão de benefícios indisponível. Nenhum total foi presumido.</div> : (ledger.data?.items ?? []).length === 0 ? <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">Nenhum evento registrado.</div> : (ledger.data?.items ?? []).map((entry) => (
            <div key={entry.id} className="grid gap-2 rounded-lg border bg-white p-4 lg:grid-cols-[200px_1fr_auto] lg:items-center">
              <div><p className="text-xs font-semibold">{workspaceNames.get(entry.workspaceId ?? '') ?? 'Conta removida ou indisponível'}</p><p className="mt-1 text-[11px] text-muted-foreground">{entry.eventType}</p></div>
              <p className="text-xs text-muted-foreground">{entry.reason}{entry.ticketId ? ` · ticket ${entry.ticketId}` : ''}</p>
              <time className="text-xs text-muted-foreground">{dateTime(entry.createdAt)}</time>
            </div>
          ))}
          {ledger.data ? <div className="flex items-center justify-between gap-3 pt-2"><span className="text-xs text-muted-foreground">{ledger.data.total === 0 ? 'Sem lançamentos.' : `${ledger.data.offset + 1}–${Math.min(ledger.data.offset + ledger.data.items.length, ledger.data.total)} de ${ledger.data.total}`}</span><span className="flex gap-2"><Button size="sm" variant="outline" disabled={ledgerPage === 0 || ledger.isFetching} onClick={() => setLedgerPage((value) => Math.max(0, value - 1))}>Anterior</Button><Button size="sm" variant="outline" disabled={!ledger.data.hasMore || ledger.isFetching} onClick={() => setLedgerPage((value) => value + 1)}>Próxima</Button></span></div> : null}
        </TabsContent>

        <TabsContent value="partner" className="space-y-3">
          <div><h2 className="text-sm font-semibold">Reavaliação trimestral Partner</h2><p className="mt-1 text-xs text-muted-foreground">Somente contas com os mínimos de indicações ativas e MRR permanecem isentas.</p></div>
          {partners.isError ? <div className="rounded-lg border border-destructive/30 p-8 text-center text-sm text-destructive">Partners indisponíveis. Nenhum total foi presumido.</div> : (partners.data?.items ?? []).length === 0 ? <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">Nenhum candidato Partner calculado.</div> : (partners.data?.items ?? []).map((item) => (
            <div key={item.workspaceId} className="grid gap-3 rounded-lg border bg-white p-4 lg:grid-cols-[1fr_auto_auto] lg:items-center">
              <div><p className="text-sm font-semibold">{item.workspaceName}</p><p className="mt-1 text-xs text-muted-foreground">{item.activeReferrals} indicações ativas · {currency(item.referredMrrCents)} de MRR · próxima revisão {dateTime(item.nextReviewAt)}</p></div>
              {statusBadge(item.status)}
              <Button variant="outline" disabled={!canManagePartners || evaluatePartner.isPending} onClick={() => evaluatePartner.mutate(item.workspaceId)}>Reavaliar agora</Button>
            </div>
          ))}
          {partners.data ? <div className="flex items-center justify-between gap-3"><span className="text-xs text-muted-foreground">{partners.data.total === 0 ? 'Sem Partners.' : `${partners.data.offset + 1}–${Math.min(partners.data.offset + partners.data.items.length, partners.data.total)} de ${partners.data.total}`}</span><span className="flex gap-2"><Button size="sm" variant="outline" disabled={partnerPage === 0 || partners.isFetching} onClick={() => setPartnerPage((value) => Math.max(0, value - 1))}>Anterior</Button><Button size="sm" variant="outline" disabled={!partners.data.hasMore || partners.isFetching} onClick={() => setPartnerPage((value) => value + 1)}>Próxima</Button></span></div> : null}
        </TabsContent>
      </Tabs>

      <Dialog open={Boolean(paymentReferral)} onOpenChange={(open) => { if (!open) setPaymentReferral(null); }}>
        <DialogContent>
          <DialogHeader><DialogTitle>Registrar primeiro pagamento</DialogTitle><DialogDescription>Use apenas como recuperação auditada quando o evento financeiro não tiver sido conciliado automaticamente.</DialogDescription></DialogHeader>
          <div className="space-y-4">
            <div><Label>Plano contratado</Label><Input className="mt-2" value={paymentPlan} onChange={(event) => setPaymentPlan(event.target.value)} /></div>
            <div><Label>Referência do pagamento</Label><Input className="mt-2" value={paymentReference} onChange={(event) => setPaymentReference(event.target.value)} placeholder="Identificador do provedor ou conciliação" /></div>
            <div><Label>Data e hora</Label><Input className="mt-2" type="datetime-local" value={paymentDate} onChange={(event) => setPaymentDate(event.target.value)} /></div>
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setPaymentReferral(null)}>Cancelar</Button><Button disabled={paymentPlan.trim().length < 2 || paymentReference.trim().length < 3 || recordPayment.isPending} onClick={() => recordPayment.mutate()}>{recordPayment.isPending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}Registrar</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
