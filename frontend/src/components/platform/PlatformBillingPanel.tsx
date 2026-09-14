/**
 * Painel Financeiro do Centro de Comando (Sprint B1).
 *
 * MRR/ARR/churn/LTV + série, tabela de assinaturas com ações auditadas
 * (mudar plano, desconto/cortesia, estender trial, suspender/reativar) e
 * P&L por conta (drawer). Local-first: nenhuma chamada a provedor externo.
 */
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import {
  applySubscriptionDiscount,
  changeSubscriptionPlan,
  extendSubscriptionTrial,
  fetchPlatformBillingOverview,
  fetchPlatformPeople,
  fetchPlatformSubscriptionEventsPage,
  fetchPlatformSubscriptions,
  fetchWorkspace360,
  reactivateSubscription,
  suspendSubscription,
} from '@/services/platform';

function fmtBRL(value?: number | null) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 2 }).format(value ?? 0);
}

const PLAN_LABEL: Record<string, string> = {
  crm_start: 'Start',
  crm_advanced: 'Advanced',
  crm_evolution: 'Evolution',
  legal_360: 'Jurídico 360',
  legacy_custom: 'Contrato legado',
  essencial: 'Start',
  escritorio: 'Advanced',
  performance: 'Evolution',
  custom: 'Contrato legado',
};

const PLAN_FILTER_LABEL: Record<string, string> = {
  crm_start: 'Start',
  crm_advanced: 'Advanced',
  crm_evolution: 'Evolution',
  legal_360: 'Jurídico 360',
  legacy_custom: 'Contrato legado',
};

const LEGACY_MUTATION_PLAN_LABEL: Record<string, string> = {
  essencial: 'Start',
  escritorio: 'Advanced',
  performance: 'Evolution',
};

const STATUS_LABEL: Record<string, string> = {
  trial: 'Trial',
  ativa: 'Ativa',
  inadimplente: 'Inadimplente',
  pausada: 'Pausada',
  cancelada: 'Cancelada',
};

const STATUS_VARIANT: Record<string, 'default' | 'secondary' | 'destructive' | 'outline'> = {
  trial: 'outline',
  ativa: 'secondary',
  inadimplente: 'destructive',
  pausada: 'outline',
  cancelada: 'destructive',
};

const SUBSCRIPTION_FIELD_LABEL: Record<string, string> = {
  plan: 'Plano',
  plano: 'Plano',
  status: 'Status',
  amountCents: 'Valor mensal',
  valor_mensal_cents: 'Valor mensal',
  discountPct: 'Desconto',
  desconto_pct: 'Desconto',
  courtesy: 'Cortesia',
  cortesia: 'Cortesia',
  interval: 'Ciclo',
  ciclo: 'Ciclo',
  trialEndsAt: 'Fim do trial',
  trial_ends_at: 'Fim do trial',
};

type ActionKind = 'plan' | 'discount' | 'trial' | 'suspend' | 'reactivate' | null;
const SUBSCRIPTION_PAGE_SIZE = 50;
const SUBSCRIPTION_EVENT_PAGE_SIZE = 10;
const WORKSPACE_PEOPLE_PAGE_SIZE = 8;

export function PlatformBillingPanel({ enabled }: { enabled: boolean }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [planFilter, setPlanFilter] = useState<string>('all');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [actionTarget, setActionTarget] = useState<{ kind: ActionKind; workspaceId: string; workspaceName?: string } | null>(null);
  const [drawerWorkspaceId, setDrawerWorkspaceId] = useState<string | null>(null);

  const overviewQuery = useQuery({
    queryKey: ['platform', 'billing', 'overview'],
    queryFn: () => fetchPlatformBillingOverview(30),
    staleTime: 30_000,
    enabled,
  });

  const subscriptionsQuery = useQuery({
    queryKey: ['platform', 'subscriptions', planFilter, statusFilter, search, page],
    queryFn: () =>
      fetchPlatformSubscriptions({
        plano: planFilter === 'all' ? undefined : planFilter,
        status: statusFilter === 'all' ? undefined : statusFilter,
        search: search || undefined,
        limit: SUBSCRIPTION_PAGE_SIZE,
        offset: page * SUBSCRIPTION_PAGE_SIZE,
      }),
    staleTime: 15_000,
    enabled,
  });

  useEffect(() => {
    setPage(0);
  }, [planFilter, search, statusFilter]);

  const workspace360Query = useQuery({
    queryKey: ['platform', 'workspace-360', drawerWorkspaceId],
    queryFn: () => fetchWorkspace360(drawerWorkspaceId as string),
    enabled: Boolean(drawerWorkspaceId),
  });

  const invalidateBilling = () => {
    void queryClient.invalidateQueries({ queryKey: ['platform', 'billing', 'overview'] });
    void queryClient.invalidateQueries({ queryKey: ['platform', 'subscriptions'] });
    void queryClient.invalidateQueries({ queryKey: ['platform', 'workspace-360'] });
  };

  const overview = overviewQuery.data ?? {};
  const series = (overview.series ?? []) as Array<{ month: string; mrr_brl: number }>;
  const subscriptionTotal = subscriptionsQuery.data?.total ?? subscriptionsQuery.data?.count ?? 0;
  const subscriptionHasMore = subscriptionsQuery.data?.hasMore
    ?? Boolean(
      subscriptionsQuery.data
      && subscriptionsQuery.data.offset + subscriptionsQuery.data.items.length < subscriptionTotal
    );

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <KpiTile title="MRR" loading={overviewQuery.isLoading} value={fmtBRL(overview.mrr_brl)} sub={`ARR: ${fmtBRL(overview.arr_brl)}`} />
        <KpiTile title="Churn (30d)" loading={overviewQuery.isLoading} value={`${overview.churn_rate_pct ?? 0}%`} sub={`${overview.active_count ?? 0} contas ativas`} />
        <KpiTile title="LTV estimado" loading={overviewQuery.isLoading} value={fmtBRL(overview.ltv_estimate_brl)} sub={`${overview.trial_count ?? 0} em trial`} />
        <KpiTile title="Contas em risco" loading={overviewQuery.isLoading} value={`${overview.at_risk_count ?? 0}`} sub="inadimplentes" />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Evolução do MRR</CardTitle>
          <CardDescription>Últimos 6 meses.</CardDescription>
        </CardHeader>
        <CardContent className="h-64">
          {overviewQuery.isLoading ? (
            <Skeleton className="h-full w-full" />
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={series}>
                <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => fmtBRL(v)} width={90} />
                <Tooltip formatter={(value: number) => fmtBRL(value)} />
                <Line type="monotone" dataKey="mrr_brl" stroke="hsl(var(--primary))" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <CardTitle className="text-base">Assinaturas por workspace</CardTitle>
            <CardDescription>Contratos canônicos e assinaturas legadas não reconciliadas, sem dupla contagem.</CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            <Input placeholder="Buscar workspace..." value={search} onChange={(e) => setSearch(e.target.value)} className="w-48" />
            <Select value={planFilter} onValueChange={setPlanFilter}>
              <SelectTrigger className="w-36"><SelectValue placeholder="Plano" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Todos os planos</SelectItem>
                {Object.entries(PLAN_FILTER_LABEL).map(([key, label]) => <SelectItem key={key} value={key}>{label}</SelectItem>)}
              </SelectContent>
            </Select>
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="w-36"><SelectValue placeholder="Status" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Todos os status</SelectItem>
                {Object.entries(STATUS_LABEL).map(([key, label]) => <SelectItem key={key} value={key}>{label}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        </CardHeader>
        <CardContent>
          {subscriptionsQuery.isLoading ? (
            <Skeleton className="h-48 w-full" />
          ) : subscriptionsQuery.isError ? (
            <div className="rounded-lg border border-destructive/30 p-8 text-center text-sm text-destructive">
              Assinaturas indisponíveis. Nenhum total foi presumido.
              <div className="mt-3"><Button size="sm" variant="outline" onClick={() => void subscriptionsQuery.refetch()}>Tentar novamente</Button></div>
            </div>
          ) : (subscriptionsQuery.data?.items ?? []).length === 0 ? (
            <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
              Nenhuma assinatura encontrada com esses filtros.
            </div>
          ) : (
            <div className="space-y-3">
              <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Workspace</TableHead>
                    <TableHead>Plano</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>MRR</TableHead>
                    <TableHead>Trial até</TableHead>
                    <TableHead className="text-right">Ações</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(subscriptionsQuery.data?.items ?? []).map((sub) => (
                    <TableRow key={sub.id}>
                      <TableCell>
                        <button
                          type="button"
                          className="text-sm font-medium underline-offset-2 hover:underline"
                          onClick={() => setDrawerWorkspaceId(sub.workspace_id)}
                        >
                          {sub.workspace_name ?? sub.workspace_id}
                        </button>
                      </TableCell>
                      <TableCell>{PLAN_LABEL[sub.plano] ?? sub.plano}</TableCell>
                      <TableCell>
                        <Badge variant={STATUS_VARIANT[sub.status] ?? 'outline'}>{STATUS_LABEL[sub.status] ?? sub.status}</Badge>
                        {sub.cortesia && <Badge variant="outline" className="ml-1">cortesia</Badge>}
                        {sub.canonical_managed && <Badge variant="outline" className="ml-1">contrato canônico</Badge>}
                      </TableCell>
                      <TableCell className="tabular-nums">{fmtBRL((sub.mrr_cents ?? 0) / 100)}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {sub.trial_ends_at ? new Date(sub.trial_ends_at).toLocaleDateString('pt-BR') : '—'}
                      </TableCell>
                      <TableCell className="text-right">
                        {sub.canonical_managed ? (
                          <Button size="sm" variant="outline" onClick={() => setDrawerWorkspaceId(sub.workspace_id)}>
                            Gerir na Conta 360
                          </Button>
                        ) : (
                          <div className="flex flex-wrap justify-end gap-1">
                            <Button size="sm" variant="outline" onClick={() => setActionTarget({ kind: 'plan', workspaceId: sub.workspace_id, workspaceName: sub.workspace_name })}>Mudar plano</Button>
                            <Button size="sm" variant="outline" onClick={() => setActionTarget({ kind: 'discount', workspaceId: sub.workspace_id, workspaceName: sub.workspace_name })}>Desconto</Button>
                            {sub.status === 'trial' && (
                              <Button size="sm" variant="outline" onClick={() => setActionTarget({ kind: 'trial', workspaceId: sub.workspace_id, workspaceName: sub.workspace_name })}>Estender trial</Button>
                            )}
                            {sub.status === 'pausada' ? (
                              <Button size="sm" variant="outline" onClick={() => setActionTarget({ kind: 'reactivate', workspaceId: sub.workspace_id, workspaceName: sub.workspace_name })}>Reativar</Button>
                            ) : (
                              <Button size="sm" variant="destructive" onClick={() => setActionTarget({ kind: 'suspend', workspaceId: sub.workspace_id, workspaceName: sub.workspace_name })}>Suspender</Button>
                            )}
                          </div>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              </div>
              {subscriptionsQuery.data && (
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs text-muted-foreground">
                    {`${subscriptionsQuery.data.offset + 1}–${Math.min(subscriptionsQuery.data.offset + subscriptionsQuery.data.items.length, subscriptionTotal)} de ${subscriptionTotal}`}
                  </span>
                  <span className="flex gap-2">
                    <Button size="sm" variant="outline" disabled={page === 0 || subscriptionsQuery.isFetching} onClick={() => setPage((value) => Math.max(0, value - 1))}>Anterior</Button>
                    <Button size="sm" variant="outline" disabled={!subscriptionHasMore || subscriptionsQuery.isFetching} onClick={() => setPage((value) => value + 1)}>Próxima</Button>
                  </span>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <SubscriptionActionDialog
        target={actionTarget}
        onClose={() => setActionTarget(null)}
        onSuccess={() => {
          invalidateBilling();
          toast({ title: 'Assinatura atualizada.' });
        }}
        onError={(message) => toast({ variant: 'destructive', title: 'Não foi possível concluir a ação', description: message })}
      />

      <Dialog open={Boolean(drawerWorkspaceId)} onOpenChange={(open) => !open && setDrawerWorkspaceId(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{workspace360Query.data?.workspace_name ?? 'Workspace 360'}</DialogTitle>
            <DialogDescription>Assinatura, consumo, saúde Meta e usuários — tudo num só lugar.</DialogDescription>
          </DialogHeader>
          {workspace360Query.isLoading ? (
            <Skeleton className="h-64 w-full" />
          ) : workspace360Query.isError ? (
            <div className="rounded-lg border border-destructive/30 p-6 text-sm text-destructive">
              Conta 360 indisponível. Nenhum dado foi presumido.
              <Button className="ml-2" size="sm" variant="outline" onClick={() => void workspace360Query.refetch()}>
                Tentar novamente
              </Button>
            </div>
          ) : workspace360Query.data ? (
            <Workspace360Body data={workspace360Query.data} workspaceId={drawerWorkspaceId as string} />
          ) : (
            <p className="text-sm text-muted-foreground">Sem dados para este workspace.</p>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function KpiTile({ title, value, sub, loading }: { title: string; value: string; sub?: string; loading?: boolean }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-xs font-medium text-muted-foreground">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? <Skeleton className="h-8 w-32" /> : (
          <>
            <p className="text-2xl font-bold tabular-nums">{value}</p>
            {sub && <p className="mt-1 text-xs text-muted-foreground">{sub}</p>}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function subscriptionEventChangedKeys(event: {
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}) {
  const before = event.before ?? {};
  const after = event.after ?? {};
  return Array.from(new Set([...Object.keys(before), ...Object.keys(after)]))
    .filter((key) => JSON.stringify(before[key]) !== JSON.stringify(after[key]));
}

function humanizeEventType(value: string) {
  return value.replaceAll('_', ' ');
}

function Workspace360Body({ data, workspaceId }: { data: Record<string, any>; workspaceId: string }) {
  const pnl = data.pnl ?? {};
  const sub = data.subscription ?? {};
  const [eventPage, setEventPage] = useState(0);
  const [peoplePage, setPeoplePage] = useState(0);
  const eventsQuery = useQuery({
    queryKey: ['platform', 'subscription-events', workspaceId, eventPage],
    queryFn: () => fetchPlatformSubscriptionEventsPage(
      workspaceId,
      SUBSCRIPTION_EVENT_PAGE_SIZE,
      eventPage * SUBSCRIPTION_EVENT_PAGE_SIZE,
    ),
    staleTime: 15_000,
  });
  const events = eventsQuery.data?.items ?? [];
  const peopleQuery = useQuery({
    queryKey: ['platform', 'workspace-people', workspaceId, peoplePage],
    queryFn: () => fetchPlatformPeople({
      workspaceId,
      limit: WORKSPACE_PEOPLE_PAGE_SIZE,
      offset: peoplePage * WORKSPACE_PEOPLE_PAGE_SIZE,
    }),
    staleTime: 15_000,
  });
  const people = peopleQuery.data?.items ?? [];
  return (
    <div className="max-h-[65vh] space-y-4 overflow-y-auto pr-1">
      <div className="grid gap-3 md:grid-cols-3">
        <div className="rounded-lg border p-3">
          <p className="text-xs uppercase text-muted-foreground">Plano</p>
          <p className="mt-1 text-sm font-semibold">{PLAN_LABEL[sub.plano] ?? sub.plano ?? '—'} · {STATUS_LABEL[sub.status] ?? sub.status ?? '—'}</p>
        </div>
        <div className="rounded-lg border p-3">
          <p className="text-xs uppercase text-muted-foreground">Receita mensal</p>
          <p className="mt-1 text-sm font-semibold tabular-nums">{fmtBRL(pnl.receita_mensal_brl)}</p>
        </div>
        <div className="rounded-lg border p-3">
          <p className="text-xs uppercase text-muted-foreground">Margem (30d)</p>
          <p className="mt-1 text-sm font-semibold tabular-nums">{fmtBRL(pnl.margem_brl)} {pnl.margem_pct != null ? `(${pnl.margem_pct}%)` : ''}</p>
        </div>
      </div>
      <div className="rounded-lg border p-3 text-sm">
        <p className="font-medium">Custo estimado (30d)</p>
        <p className="text-xs text-muted-foreground">IA: {fmtBRL(pnl.custo_ia_brl)} · WhatsApp: {fmtBRL(pnl.custo_whatsapp_estimado_brl)}</p>
      </div>
      <div className="rounded-lg border p-3">
        <p className="mb-2 text-sm font-medium">Histórico da assinatura</p>
        {eventsQuery.isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : eventsQuery.isError ? (
          <div className="rounded-md border border-destructive/30 p-3 text-xs text-destructive">
            Histórico indisponível. Nenhum evento foi presumido.
            <Button className="ml-2" size="sm" variant="outline" onClick={() => void eventsQuery.refetch()}>
              Tentar novamente
            </Button>
          </div>
        ) : events.length === 0 ? (
          <p className="text-xs text-muted-foreground">Sem eventos registrados ainda.</p>
        ) : (
          <div className="space-y-3">
            <ul className="space-y-2 text-xs text-muted-foreground">
              {events.map((event) => {
                const changedKeys = subscriptionEventChangedKeys(event);
                return (
                  <li key={event.id} className="rounded-md bg-muted/40 p-2">
                    <div className="font-medium text-foreground">
                      {humanizeEventType(event.eventType)}
                    </div>
                    <div>
                      {event.occurredAt ? new Date(event.occurredAt).toLocaleString('pt-BR') : 'Horário indisponível'}
                      {event.actorLabel ? ` · ${event.actorLabel}` : ''}
                    </div>
                    {event.motivo && <div className="mt-1">Motivo: {event.motivo}</div>}
                    <div className="mt-1">
                      {changedKeys.length > 0
                        ? `${changedKeys.length} campo(s) alterado(s): ${changedKeys.map((key) => SUBSCRIPTION_FIELD_LABEL[key] ?? key).join(', ')}`
                        : 'Sem alteração de campos no snapshot.'}
                    </div>
                  </li>
                );
              })}
            </ul>
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-muted-foreground">
                {`${eventsQuery.data!.offset + 1}–${Math.min(eventsQuery.data!.offset + events.length, eventsQuery.data!.total)} de ${eventsQuery.data!.total}`}
              </span>
              <span className="flex gap-2">
                <Button size="sm" variant="outline" disabled={eventPage === 0 || eventsQuery.isFetching} onClick={() => setEventPage((value) => Math.max(0, value - 1))}>Anterior</Button>
                <Button size="sm" variant="outline" disabled={!eventsQuery.data!.hasMore || eventsQuery.isFetching} onClick={() => setEventPage((value) => value + 1)}>Próxima</Button>
              </span>
            </div>
          </div>
        )}
      </div>
      <div className="rounded-lg border p-3">
        <p className="mb-2 text-sm font-medium">Usuários da conta</p>
        {peopleQuery.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : peopleQuery.isError ? (
          <div className="rounded-md border border-destructive/30 p-3 text-xs text-destructive">
            Usuários indisponíveis. Nenhum total foi presumido.
            <Button className="ml-2" size="sm" variant="outline" onClick={() => void peopleQuery.refetch()}>
              Tentar novamente
            </Button>
          </div>
        ) : people.length === 0 ? (
          <p className="text-xs text-muted-foreground">Nenhum usuário vinculado a esta conta.</p>
        ) : (
          <div className="space-y-3">
            <ul className="space-y-1 text-xs text-muted-foreground">
              {people.map((person) => (
                <li key={`${person.workspace_id}:${person.user_id}`}>
                  {person.name} · {person.role} · {person.membership_status}
                </li>
              ))}
            </ul>
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-muted-foreground">
                {`${peopleQuery.data!.offset + 1}–${Math.min(peopleQuery.data!.offset + people.length, peopleQuery.data!.total)} de ${peopleQuery.data!.total}`}
              </span>
              <span className="flex gap-2">
                <Button size="sm" variant="outline" disabled={peoplePage === 0 || peopleQuery.isFetching} onClick={() => setPeoplePage((value) => Math.max(0, value - 1))}>Anterior</Button>
                <Button size="sm" variant="outline" disabled={!peopleQuery.data!.hasMore || peopleQuery.isFetching} onClick={() => setPeoplePage((value) => value + 1)}>Próxima</Button>
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function SubscriptionActionDialog({
  target,
  onClose,
  onSuccess,
  onError,
}: {
  target: { kind: ActionKind; workspaceId: string; workspaceName?: string } | null;
  onClose: () => void;
  onSuccess: () => void;
  onError: (message: string) => void;
}) {
  const [novoPlano, setNovoPlano] = useState('essencial');
  const [descontoPct, setDescontoPct] = useState('10');
  const [cortesia, setCortesia] = useState(false);
  const [dias, setDias] = useState('14');
  const [motivo, setMotivo] = useState('');

  const mutation = useMutation({
    mutationFn: async () => {
      if (!target) return;
      const { kind, workspaceId } = target;
      if (kind === 'plan') return changeSubscriptionPlan(workspaceId, { novoPlano, motivo });
      if (kind === 'discount') return applySubscriptionDiscount(workspaceId, { descontoPct: Number(descontoPct), cortesia, motivo });
      if (kind === 'trial') return extendSubscriptionTrial(workspaceId, { dias: Number(dias), motivo });
      if (kind === 'suspend') return suspendSubscription(workspaceId, { motivo });
      if (kind === 'reactivate') return reactivateSubscription(workspaceId, { motivo });
      return undefined;
    },
    onSuccess: () => {
      onSuccess();
      setMotivo('');
      onClose();
    },
    onError: (error: Error) => onError(error.message),
  });

  const title = useMemo(() => {
    switch (target?.kind) {
      case 'plan': return 'Mudar plano';
      case 'discount': return 'Aplicar desconto ou cortesia';
      case 'trial': return 'Estender trial';
      case 'suspend': return 'Suspender assinatura';
      case 'reactivate': return 'Reativar assinatura';
      default: return '';
    }
  }, [target?.kind]);

  return (
    <Dialog open={Boolean(target)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{target?.workspaceName ?? target?.workspaceId} — a ação é auditada e fica visível no histórico do workspace.</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          {target?.kind === 'plan' && (
            <div className="space-y-1.5">
              <Label>Novo plano</Label>
              <Select value={novoPlano} onValueChange={setNovoPlano}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(LEGACY_MUTATION_PLAN_LABEL).map(([key, label]) => <SelectItem key={key} value={key}>{label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          )}
          {target?.kind === 'discount' && (
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Desconto (%)</Label>
                <Input type="number" min={0} max={100} value={descontoPct} onChange={(e) => setDescontoPct(e.target.value)} />
              </div>
              <div className="flex items-end gap-2">
                <input id="cortesia" type="checkbox" checked={cortesia} onChange={(e) => setCortesia(e.target.checked)} />
                <Label htmlFor="cortesia">Marcar como cortesia</Label>
              </div>
            </div>
          )}
          {target?.kind === 'trial' && (
            <div className="space-y-1.5">
              <Label>Dias adicionais</Label>
              <Input type="number" min={1} value={dias} onChange={(e) => setDias(e.target.value)} />
            </div>
          )}
          <div className="space-y-1.5">
            <Label>Motivo (obrigatório, vai para a auditoria)</Label>
            <Textarea value={motivo} onChange={(e) => setMotivo(e.target.value)} rows={3} placeholder="Ex.: upgrade solicitado pelo cliente em 23/07." />
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
          <Button
            disabled={!motivo.trim() || mutation.isPending}
            variant={target?.kind === 'suspend' ? 'destructive' : 'default'}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? 'Confirmando...' : 'Confirmar'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
