import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Archive,
  CirclePause,
  History,
  LoaderCircle,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  ShieldCheck,
  TicketPercent,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
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
  type BenefitUnit,
  type Coupon,
  type CouponStatus,
  type CouponWritePayload,
} from '@/services/saas-benefits';
import { useAppStore } from '@/store/app-context';
import { PlatformEntityCombobox } from '@/components/platform/PlatformEntityCombobox';

interface CouponFormState {
  code: string;
  name: string;
  description: string;
  benefitType: string;
  unit: BenefitUnit;
  amount: string;
  allowedPlanCodes: string;
  totalLimit: string;
  perWorkspaceLimit: string;
  autoApply: boolean;
  startsAt: string;
  endsAt: string;
  reason: string;
  ticketId: string;
}

const emptyForm: CouponFormState = {
  code: '',
  name: '',
  description: '',
  benefitType: 'commercial_coupon',
  unit: 'days',
  amount: '14',
  allowedPlanCodes: '',
  totalLimit: '',
  perWorkspaceLimit: '1',
  autoApply: true,
  startsAt: '',
  endsAt: '',
  reason: '',
  ticketId: '',
};

const statusLabels: Record<CouponStatus, string> = {
  draft: 'Rascunho',
  active: 'Ativo',
  paused: 'Pausado',
  expired: 'Expirado',
  archived: 'Arquivado',
};

const unitLabels: Record<BenefitUnit, string> = {
  days: 'Dias grátis',
  months: 'Meses grátis',
  tokens: 'Tokens',
  percentage: 'Desconto percentual',
  cents: 'Crédito em centavos',
};

function dateTime(value?: string | null) {
  if (!value) return 'Sem limite';
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value));
}

function localDateTime(value?: string | null) {
  if (!value) return '';
  const date = new Date(value);
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return shifted.toISOString().slice(0, 16);
}

function toIso(value: string) {
  return value ? new Date(value).toISOString() : null;
}

function amountLabel(coupon: Coupon) {
  const amount = Number(coupon.amount);
  if (coupon.unit === 'cents') return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(amount / 100);
  if (coupon.unit === 'percentage') return `${amount}%`;
  return `${new Intl.NumberFormat('pt-BR').format(amount)} ${unitLabels[coupon.unit].toLowerCase()}`;
}

function formFromCoupon(coupon: Coupon): CouponFormState {
  return {
    code: coupon.code,
    name: coupon.name,
    description: coupon.description,
    benefitType: coupon.benefitType,
    unit: coupon.unit,
    amount: coupon.amount,
    allowedPlanCodes: coupon.allowedPlanCodes.join(', '),
    totalLimit: coupon.totalLimit == null ? '' : String(coupon.totalLimit),
    perWorkspaceLimit: String(coupon.perWorkspaceLimit),
    autoApply: coupon.autoApply,
    startsAt: localDateTime(coupon.startsAt),
    endsAt: localDateTime(coupon.endsAt),
    reason: '',
    ticketId: '',
  };
}

function payloadFromForm(form: CouponFormState): CouponWritePayload {
  return {
    code: form.code.trim().toUpperCase(),
    name: form.name.trim(),
    description: form.description.trim(),
    benefitType: form.benefitType.trim(),
    unit: form.unit,
    amount: Number(form.amount),
    allowedPlanCodes: form.allowedPlanCodes.split(',').map((item) => item.trim()).filter(Boolean),
    totalLimit: form.totalLimit.trim() ? Number(form.totalLimit) : null,
    perWorkspaceLimit: Number(form.perWorkspaceLimit),
    autoApply: form.autoApply,
    startsAt: toIso(form.startsAt),
    endsAt: toIso(form.endsAt),
    reason: form.reason.trim(),
    ticketId: form.ticketId.trim() || null,
  };
}

export default function BlackboxCouponStudio() {
  const { state } = useAppStore();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const canView = canPlatform(state.currentUser, 'coupons', 'view');
  const canManage = canPlatform(state.currentUser, 'coupons', 'manage');
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingCoupon, setEditingCoupon] = useState<Coupon | null>(null);
  const [form, setForm] = useState<CouponFormState>(emptyForm);
  const [selectedCouponId, setSelectedCouponId] = useState<string | null>(null);
  const [statusTarget, setStatusTarget] = useState<{ coupon: Coupon; status: CouponStatus } | null>(null);
  const [statusReason, setStatusReason] = useState('');
  const [statusTicketId, setStatusTicketId] = useState('');
  const [couponPage, setCouponPage] = useState(0);
  const [redemptionPage, setRedemptionPage] = useState(0);
  const [auditPage, setAuditPage] = useState(0);
  const pageSize = 20;

  const overview = useQuery({ queryKey: ['blackbox', 'benefits', 'overview'], queryFn: saasBenefitsApi.platformOverview, enabled: canView });
  const coupons = useQuery({ queryKey: ['blackbox', 'coupons', 'catalog', couponPage], queryFn: () => saasBenefitsApi.couponsPage({ limit: pageSize, offset: couponPage * pageSize }), enabled: canView });
  const redemptions = useQuery({ queryKey: ['blackbox', 'coupons', 'redemptions', redemptionPage], queryFn: () => saasBenefitsApi.couponRedemptionsPage({ limit: pageSize, offset: redemptionPage * pageSize }), enabled: canView });
  const audit = useQuery({
    queryKey: ['blackbox', 'coupons', 'audit', selectedCouponId, auditPage],
    queryFn: () => saasBenefitsApi.couponAuditPage(selectedCouponId as string, { limit: pageSize, offset: auditPage * pageSize }),
    enabled: canView && Boolean(selectedCouponId),
  });
  const selectedCoupon = useMemo(
    () => (coupons.data?.items ?? []).find((item) => item.id === selectedCouponId) ?? null,
    [coupons.data, selectedCouponId],
  );
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['blackbox', 'coupons'] });

  const saveCoupon = useMutation({
    mutationFn: () => {
      const payload = payloadFromForm(form);
      return editingCoupon
        ? saasBenefitsApi.updateCoupon(editingCoupon.id, payload, editingCoupon.version)
        : saasBenefitsApi.createCoupon(payload);
    },
    onSuccess: async (coupon) => {
      setEditorOpen(false);
      setEditingCoupon(null);
      setForm(emptyForm);
      setSelectedCouponId(coupon.id);
      await refresh();
      toast({ title: editingCoupon ? 'Cupom atualizado' : 'Cupom criado', description: 'A alteração foi versionada e registrada na auditoria.' });
    },
    onError: (error) => toast({ title: 'Não foi possível salvar o cupom', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' }),
  });

  const changeStatus = useMutation({
    mutationFn: () => {
      if (!statusTarget) throw new Error('Selecione o cupom.');
      return saasBenefitsApi.changeCouponStatus(
        statusTarget.coupon,
        statusTarget.status,
        statusReason,
        statusTicketId.trim() || null,
      );
    },
    onSuccess: async (coupon) => {
      setStatusTarget(null);
      setStatusReason('');
      setStatusTicketId('');
      setSelectedCouponId(coupon.id);
      await refresh();
      toast({ title: 'Status do cupom atualizado', description: 'A decisão permanece disponível na auditoria.' });
    },
    onError: (error) => toast({ title: 'Não foi possível alterar o status', description: error instanceof Error ? error.message : 'Tente novamente.', variant: 'destructive' }),
  });

  const openCreate = () => {
    setEditingCoupon(null);
    setForm(emptyForm);
    setEditorOpen(true);
  };
  const openEdit = (coupon: Coupon) => {
    setEditingCoupon(coupon);
    setForm(formFromCoupon(coupon));
    setEditorOpen(true);
  };
  const validForm = form.code.trim().length >= 3
    && form.name.trim().length >= 2
    && Number(form.amount) > 0
    && Number(form.perWorkspaceLimit) > 0
    && form.reason.trim().length >= 3;

  if (!canView) {
    return (
      <div className="grid min-h-[60vh] place-items-center p-6">
        <section className="max-w-lg rounded-lg border bg-white p-7 text-center">
          <ShieldCheck className="mx-auto h-7 w-7 text-blue-600" />
          <h1 className="mt-3 text-lg font-semibold">Studio não autorizado</h1>
          <p className="mt-2 text-sm text-muted-foreground">Seu pacote Blackbox não permite visualizar cupons comerciais.</p>
        </section>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-[1500px] space-y-5 p-6" data-testid="blackbox-coupon-studio">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2"><TicketPercent className="h-5 w-5 text-blue-600" /><h1 className="text-xl font-semibold">Studio de cupons</h1></div>
          <p className="mt-1 text-sm text-muted-foreground">Crie regras comerciais com vigência, limites, concorrência otimista e trilha completa.</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => void refresh()}><RefreshCw className="mr-2 h-4 w-4" />Atualizar</Button>
          <Button onClick={openCreate} disabled={!canManage}><Plus className="mr-2 h-4 w-4" />Novo cupom</Button>
        </div>
      </header>

      <section className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-lg border bg-white p-4"><p className="text-xs text-muted-foreground">Cupons cadastrados</p><p className="mt-2 text-lg font-semibold">{coupons.data?.total ?? '—'}</p></div>
        <div className="rounded-lg border bg-white p-4"><p className="text-xs text-muted-foreground">Ativos</p><p className="mt-2 text-lg font-semibold">{overview.data?.activeCoupons ?? '—'}</p></div>
        <div className="rounded-lg border bg-white p-4"><p className="text-xs text-muted-foreground">Resgates</p><p className="mt-2 text-lg font-semibold">{overview.data?.couponRedemptions ?? '—'}</p></div>
      </section>

      <Tabs defaultValue="catalogo" className="space-y-4">
        <TabsList><TabsTrigger value="catalogo">Catálogo</TabsTrigger><TabsTrigger value="resgates">Resgates</TabsTrigger><TabsTrigger value="auditoria">Auditoria</TabsTrigger></TabsList>
        <TabsContent value="catalogo" className="space-y-3">
          {coupons.isLoading ? <div className="grid min-h-48 place-items-center"><LoaderCircle className="h-5 w-5 animate-spin text-blue-600" /></div> : coupons.isError ? <div className="rounded-lg border border-destructive/30 p-10 text-center text-sm text-destructive">Catálogo indisponível. Nenhum total foi presumido.</div> : (coupons.data?.items ?? []).length === 0 ? (
            <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">Nenhum cupom cadastrado. Crie o primeiro sem sair do Blackbox.</div>
          ) : (coupons.data?.items ?? []).map((coupon) => (
            <div key={coupon.id} className="grid gap-4 rounded-lg border bg-white p-4 xl:grid-cols-[1fr_auto] xl:items-center">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2"><span className="font-mono text-sm font-semibold">{coupon.code}</span><Badge variant="outline">{statusLabels[coupon.status]}</Badge><Badge variant="outline">v{coupon.version}</Badge></div>
                <p className="mt-2 text-sm font-semibold">{coupon.name} · {amountLabel(coupon)}</p>
                <p className="mt-1 text-xs text-muted-foreground">{coupon.description || 'Sem descrição'} · {coupon.redeemedCount}/{coupon.totalLimit ?? 'sem limite'} resgates · até {coupon.perWorkspaceLimit} por conta</p>
                <p className="mt-1 text-[11px] text-muted-foreground">Planos: {coupon.allowedPlanCodes.length ? coupon.allowedPlanCodes.join(', ') : 'todos'} · vigência {dateTime(coupon.startsAt)} a {dateTime(coupon.endsAt)}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="outline" aria-label={`Auditoria de ${coupon.code}`} onClick={() => { setSelectedCouponId(coupon.id); setAuditPage(0); }}><History className="mr-2 h-4 w-4" />Auditoria</Button>
                <Button size="sm" variant="outline" disabled={!canManage} onClick={() => openEdit(coupon)}><Pencil className="mr-2 h-4 w-4" />Editar</Button>
                {coupon.status !== 'active' && coupon.status !== 'archived' && <Button size="sm" disabled={!canManage} onClick={() => setStatusTarget({ coupon, status: 'active' })}><Play className="mr-2 h-4 w-4" />Ativar</Button>}
                {coupon.status === 'active' && <Button size="sm" variant="outline" disabled={!canManage} onClick={() => setStatusTarget({ coupon, status: 'paused' })}><CirclePause className="mr-2 h-4 w-4" />Pausar</Button>}
                {coupon.status !== 'archived' && <Button size="sm" variant="destructive" disabled={!canManage} onClick={() => setStatusTarget({ coupon, status: 'archived' })}><Archive className="mr-2 h-4 w-4" />Arquivar</Button>}
              </div>
            </div>
          ))}
          {coupons.data ? <div className="flex items-center justify-between gap-3"><span className="text-xs text-muted-foreground">{coupons.data.total === 0 ? 'Sem cupons.' : `${coupons.data.offset + 1}–${Math.min(coupons.data.offset + coupons.data.items.length, coupons.data.total)} de ${coupons.data.total}`}</span><span className="flex gap-2"><Button size="sm" variant="outline" disabled={couponPage === 0 || coupons.isFetching} onClick={() => setCouponPage((value) => Math.max(0, value - 1))}>Anterior</Button><Button size="sm" variant="outline" disabled={!coupons.data.hasMore || coupons.isFetching} onClick={() => setCouponPage((value) => value + 1)}>Próxima</Button></span></div> : null}
        </TabsContent>

        <TabsContent value="resgates" className="space-y-3">
          {redemptions.isError ? <div className="rounded-lg border border-destructive/30 p-10 text-center text-sm text-destructive">Resgates indisponíveis. Nenhum total foi presumido.</div> : (redemptions.data?.items ?? []).length === 0 ? <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">Nenhum cupom foi resgatado.</div> : (redemptions.data?.items ?? []).map((item) => (
            <div key={item.id} className="grid gap-2 rounded-lg border bg-white p-4 md:grid-cols-[180px_1fr_auto] md:items-center">
              <span className="font-mono text-sm font-semibold">{item.couponCode}</span>
              <div><p className="text-sm font-medium">{item.workspaceName}</p><p className="mt-1 text-xs text-muted-foreground">Conta {item.workspaceId}</p></div>
              <div className="text-xs text-muted-foreground md:text-right"><Badge variant="outline">{item.status}</Badge><p className="mt-2">{dateTime(item.redeemedAt)}</p></div>
            </div>
          ))}
          {redemptions.data ? <div className="flex items-center justify-between gap-3"><span className="text-xs text-muted-foreground">{redemptions.data.total === 0 ? 'Sem resgates.' : `${redemptions.data.offset + 1}–${Math.min(redemptions.data.offset + redemptions.data.items.length, redemptions.data.total)} de ${redemptions.data.total}`}</span><span className="flex gap-2"><Button size="sm" variant="outline" disabled={redemptionPage === 0 || redemptions.isFetching} onClick={() => setRedemptionPage((value) => Math.max(0, value - 1))}>Anterior</Button><Button size="sm" variant="outline" disabled={!redemptions.data.hasMore || redemptions.isFetching} onClick={() => setRedemptionPage((value) => value + 1)}>Próxima</Button></span></div> : null}
        </TabsContent>

        <TabsContent value="auditoria" className="grid gap-4 xl:grid-cols-[320px_1fr]">
          <section className="rounded-lg border bg-white p-4">
            <Label>Selecionar cupom</Label>
            <Select value={selectedCouponId ?? ''} onValueChange={(value) => { setSelectedCouponId(value); setAuditPage(0); }}><SelectTrigger className="mt-2"><SelectValue placeholder="Escolha um cupom desta página" /></SelectTrigger><SelectContent>{(coupons.data?.items ?? []).map((item) => <SelectItem key={item.id} value={item.id}>{item.code}</SelectItem>)}</SelectContent></Select>
            {selectedCoupon && <div className="mt-4 rounded-md border bg-slate-50 p-3"><p className="font-mono text-sm font-semibold">{selectedCoupon.code}</p><p className="mt-1 text-xs text-muted-foreground">{selectedCoupon.name} · versão {selectedCoupon.version}</p></div>}
          </section>
          <section className="space-y-3">
            {!selectedCouponId ? <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">Selecione um cupom para consultar sua história.</div> : audit.isLoading ? <div className="grid min-h-40 place-items-center"><LoaderCircle className="h-5 w-5 animate-spin text-blue-600" /></div> : audit.isError ? <div className="rounded-lg border border-destructive/30 p-10 text-center text-sm text-destructive">Auditoria do cupom indisponível.</div> : (audit.data?.items ?? []).map((entry) => (
              <div key={entry.id} className="rounded-lg border bg-white p-4">
                <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-sm font-semibold">{entry.action}</p><p className="mt-1 text-xs text-muted-foreground">{entry.reason}</p></div><time className="text-xs text-muted-foreground">{dateTime(entry.createdAt)}</time></div>
                <div className="mt-3 grid gap-3 md:grid-cols-2"><pre className="max-h-44 overflow-auto rounded-md bg-slate-950 p-3 text-[10px] text-slate-200">{JSON.stringify(entry.before, null, 2)}</pre><pre className="max-h-44 overflow-auto rounded-md bg-slate-950 p-3 text-[10px] text-slate-200">{JSON.stringify(entry.after, null, 2)}</pre></div>
                {entry.ticketId && <p className="mt-2 text-[11px] text-muted-foreground">Ticket vinculado: {entry.ticketId}</p>}
              </div>
            ))}
            {audit.data ? <div className="flex items-center justify-between gap-3"><span className="text-xs text-muted-foreground">{audit.data.total === 0 ? 'Sem eventos.' : `${audit.data.offset + 1}–${Math.min(audit.data.offset + audit.data.items.length, audit.data.total)} de ${audit.data.total}`}</span><span className="flex gap-2"><Button size="sm" variant="outline" disabled={auditPage === 0 || audit.isFetching} onClick={() => setAuditPage((value) => Math.max(0, value - 1))}>Anterior</Button><Button size="sm" variant="outline" disabled={!audit.data.hasMore || audit.isFetching} onClick={() => setAuditPage((value) => value + 1)}>Próxima</Button></span></div> : null}
          </section>
        </TabsContent>
      </Tabs>

      <Dialog open={editorOpen} onOpenChange={setEditorOpen}>
        <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
          <DialogHeader><DialogTitle>{editingCoupon ? 'Editar cupom' : 'Criar cupom'}</DialogTitle><DialogDescription>Campos comerciais, limites e vigência são validados no backend. Salvar não ativa automaticamente um rascunho.</DialogDescription></DialogHeader>
          <div className="grid gap-4 sm:grid-cols-2">
            <div><Label>Código</Label><Input className="mt-2 font-mono" value={form.code} onChange={(event) => setForm((current) => ({ ...current, code: event.target.value.toUpperCase() }))} placeholder="PARCEIRO-2026" /></div>
            <div><Label>Nome interno</Label><Input className="mt-2" value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} /></div>
            <div className="sm:col-span-2"><Label>Descrição</Label><Textarea className="mt-2" value={form.description} onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))} /></div>
            <div><Label>Unidade</Label><Select value={form.unit} onValueChange={(value: BenefitUnit) => setForm((current) => ({ ...current, unit: value }))}><SelectTrigger className="mt-2"><SelectValue /></SelectTrigger><SelectContent>{Object.entries(unitLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>
            <div><Label>Quantidade</Label><Input className="mt-2" type="number" min={1} value={form.amount} onChange={(event) => setForm((current) => ({ ...current, amount: event.target.value }))} /></div>
            <div><Label>Planos permitidos</Label><Input className="mt-2" value={form.allowedPlanCodes} onChange={(event) => setForm((current) => ({ ...current, allowedPlanCodes: event.target.value }))} placeholder="essencial, escritorio" /><p className="mt-1 text-[11px] text-muted-foreground">Vazio permite todos os planos.</p></div>
            <div><Label>Tipo técnico</Label><Input className="mt-2" value={form.benefitType} onChange={(event) => setForm((current) => ({ ...current, benefitType: event.target.value }))} /></div>
            <div><Label>Limite total</Label><Input className="mt-2" type="number" min={1} value={form.totalLimit} onChange={(event) => setForm((current) => ({ ...current, totalLimit: event.target.value }))} placeholder="Sem limite" /></div>
            <div><Label>Limite por conta</Label><Input className="mt-2" type="number" min={1} max={10} value={form.perWorkspaceLimit} onChange={(event) => setForm((current) => ({ ...current, perWorkspaceLimit: event.target.value }))} /></div>
            <div><Label>Início da vigência</Label><Input className="mt-2" type="datetime-local" value={form.startsAt} onChange={(event) => setForm((current) => ({ ...current, startsAt: event.target.value }))} /></div>
            <div><Label>Fim da vigência</Label><Input className="mt-2" type="datetime-local" value={form.endsAt} onChange={(event) => setForm((current) => ({ ...current, endsAt: event.target.value }))} /></div>
            <label className="flex items-center gap-2 rounded-md border p-3 text-sm sm:col-span-2"><Checkbox checked={form.autoApply} onCheckedChange={(checked) => setForm((current) => ({ ...current, autoApply: checked === true }))} />Aplicar o benefício automaticamente após um resgate válido</label>
            <div className="sm:col-span-2"><Label>Motivo da criação ou alteração</Label><Textarea className="mt-2" value={form.reason} onChange={(event) => setForm((current) => ({ ...current, reason: event.target.value }))} placeholder="Obrigatório para a auditoria." /></div>
            <div className="sm:col-span-2"><Label>Ticket vinculado</Label><PlatformEntityCombobox className="mt-2" entityType="tickets" purpose="coupon_write" value={form.ticketId || null} onChange={(value) => setForm((current) => ({ ...current, ticketId: value ?? '' }))} label="Selecionar ticket do cupom" placeholder="Pesquisar ticket" /></div>
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setEditorOpen(false)}>Cancelar</Button><Button disabled={!canManage || !validForm || saveCoupon.isPending} onClick={() => saveCoupon.mutate()}>{saveCoupon.isPending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}Salvar cupom</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(statusTarget)} onOpenChange={(open) => { if (!open) setStatusTarget(null); }}>
        <DialogContent>
          <DialogHeader><DialogTitle>Alterar status do cupom</DialogTitle><DialogDescription>{statusTarget ? `${statusTarget.coupon.code} será alterado para ${statusLabels[statusTarget.status]}.` : ''}</DialogDescription></DialogHeader>
          <div className="space-y-4"><div><Label>Motivo</Label><Textarea className="mt-2" value={statusReason} onChange={(event) => setStatusReason(event.target.value)} placeholder="Explique a decisão." /></div><div><Label>Ticket vinculado</Label><PlatformEntityCombobox className="mt-2" entityType="tickets" purpose="coupon_status" value={statusTicketId || null} onChange={(value) => setStatusTicketId(value ?? '')} label="Selecionar ticket da alteração" placeholder="Pesquisar ticket" /></div></div>
          <DialogFooter><Button variant="outline" onClick={() => setStatusTarget(null)}>Cancelar</Button><Button disabled={statusReason.trim().length < 3 || changeStatus.isPending} onClick={() => changeStatus.mutate()}>{changeStatus.isPending && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}Confirmar</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
