/**
 * Growth CRM da plataforma — funil de aquisição B2B dos escritórios-clientes.
 *
 * "Casa de ferreiro, espeto de pau": o Meu Executivo vende CRM para escritórios de
 * advocacia mas não tinha CRM próprio para captar esses escritórios. Este
 * painel vive no domínio Negócio do Super Admin e cobre:
 *   - Kanban do funil (prospect -> contatado -> demo_agendada -> trial ->
 *     proposta -> pagante | perdido) com botões de mover estágio;
 *   - Métricas do funil (conversão, win rate, pipeline estimado, origem);
 *   - Formulário de novo prospect;
 *   - Drawer com histórico de atividades e registro de novo toque.
 */
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Badge } from '@/components/ui/badge';
import { PlatformEntityCombobox } from '@/components/platform/PlatformEntityCombobox';
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
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';
import {
  createGrowthActivity,
  createGrowthProspect,
  convertGrowthProspectToWorkspace,
  fetchGrowthFunnelMetrics,
  fetchGrowthProspect,
  fetchGrowthProspects,
  GROWTH_FUNNEL_STAGES,
  type GrowthProspect,
  moveGrowthProspectStage,
  updateGrowthProspect,
} from '@/services/platform';
import { Building2, Phone, Plus, Search, Target, TrendingUp } from 'lucide-react';

const STAGE_LABEL: Record<string, string> = {
  prospect: 'Prospect',
  contatado: 'Contatado',
  demo_agendada: 'Demo agendada',
  trial: 'Trial',
  proposta: 'Proposta',
  pagante: 'Pagante',
  perdido: 'Perdido',
};

const BOARD_STAGES = GROWTH_FUNNEL_STAGES.filter((s) => s !== 'perdido');
const PROSPECT_PAGE_SIZE = 25;

const ORIGEM_LABEL: Record<string, string> = {
  indicacao: 'Indicação',
  outbound: 'Outbound',
  inbound_site: 'Inbound (site)',
  evento: 'Evento',
  rede_propria: 'Rede própria',
  parceria: 'Parceria',
  anuncio: 'Anúncio',
  backfill: 'Backfill (base existente)',
  outro: 'Outro',
};

const ACTIVITY_LABEL: Record<string, string> = {
  ligacao: 'Ligação',
  email: 'E-mail',
  whatsapp: 'WhatsApp',
  demo: 'Demo',
  nota: 'Nota',
};

const CHART_COLORS = ['#2563eb', '#059669', '#d97706', '#7c3aed', '#dc2626', '#0891b2', '#4b5563'];

function fmtBRL(value?: number | null) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(
    value ?? 0,
  );
}

function fmtDate(value?: string | null) {
  if (!value) return '—';
  return new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric' }).format(
    new Date(value),
  );
}

export function PlatformGrowthCrmPanel({ enabled }: { enabled: boolean }) {
  const { toast } = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [stagePages, setStagePages] = useState<Record<string, number>>(() => Object.fromEntries(
    BOARD_STAGES.map((stage) => [stage, 0]),
  ));
  const [createOpen, setCreateOpen] = useState(false);
  const [drawerProspectId, setDrawerProspectId] = useState<string | null>(null);
  const [lossProspect, setLossProspect] = useState<GrowthProspect | null>(null);
  const [lossReason, setLossReason] = useState('');
  const [editProspect, setEditProspect] = useState<GrowthProspect | null>(null);
  const [convertProspect, setConvertProspect] = useState<GrowthProspect | null>(null);
  const [convertDraft, setConvertDraft] = useState({ workspaceName: '', slug: '', ownerName: '', ownerEmail: '', planKey: 'pro', setupMode: 'assisted' });
  const [editDraft, setEditDraft] = useState({
    nomeEscritorio: '', contatoNome: '', contatoEmail: '', contatoTelefone: '', cidade: '', uf: '', origem: 'outbound', valorEstimado: '', notas: '', ownerUserId: '',
    score: '', nextAction: '', nextActionAt: '', campaign: '', utmSource: '', utmMedium: '', utmCampaign: '', proposalStatus: '', proposalReference: '', proposalValue: '',
  });
  const [newProspect, setNewProspect] = useState({
    nomeEscritorio: '',
    contatoNome: '',
    contatoEmail: '',
    contatoTelefone: '',
    cidade: '',
    uf: '',
    origem: 'outbound',
    valorEstimadoCents: '',
    notas: '',
  });

  const stageQueries = useQueries({
    queries: BOARD_STAGES.map((stage) => ({
      queryKey: ['platform', 'growth', 'prospects', stage, search, stagePages[stage] ?? 0],
      queryFn: () => fetchGrowthProspects({
        estagio: stage,
        search: search || undefined,
        limit: PROSPECT_PAGE_SIZE,
        offset: (stagePages[stage] ?? 0) * PROSPECT_PAGE_SIZE,
      }),
      staleTime: 15_000,
      enabled,
    })),
  });

  useEffect(() => {
    setStagePages(Object.fromEntries(BOARD_STAGES.map((stage) => [stage, 0])));
  }, [search]);

  const metricsQuery = useQuery({
    queryKey: ['platform', 'growth', 'funnel-metrics'],
    queryFn: () => fetchGrowthFunnelMetrics(90),
    staleTime: 30_000,
    enabled,
  });
  const drawerQuery = useQuery({
    queryKey: ['platform', 'growth', 'prospect', drawerProspectId],
    queryFn: () => fetchGrowthProspect(drawerProspectId as string),
    enabled: Boolean(drawerProspectId),
  });

  const invalidateAll = () => {
    void queryClient.invalidateQueries({ queryKey: ['platform', 'growth'] });
  };

  const createMutation = useMutation({
    mutationFn: () =>
      createGrowthProspect({
        nomeEscritorio: newProspect.nomeEscritorio.trim(),
        contatoNome: newProspect.contatoNome || undefined,
        contatoEmail: newProspect.contatoEmail || undefined,
        contatoTelefone: newProspect.contatoTelefone || undefined,
        cidade: newProspect.cidade || undefined,
        uf: newProspect.uf || undefined,
        origem: newProspect.origem,
        valorEstimadoCents: newProspect.valorEstimadoCents
          ? Math.round(Number(newProspect.valorEstimadoCents) * 100)
          : undefined,
        notas: newProspect.notas || undefined,
      }),
    onSuccess: () => {
      toast({ title: 'Prospect criado', description: 'Escritório adicionado ao funil.' });
      setCreateOpen(false);
      setNewProspect({
        nomeEscritorio: '',
        contatoNome: '',
        contatoEmail: '',
        contatoTelefone: '',
        cidade: '',
        uf: '',
        origem: 'outbound',
        valorEstimadoCents: '',
        notas: '',
      });
      invalidateAll();
    },
    onError: (error: any) => {
      toast({
        title: 'Não foi possível criar o prospect',
        description: error?.message ?? 'Erro inesperado.',
        variant: 'destructive',
      });
    },
  });

  const moveStageMutation = useMutation({
    mutationFn: ({ prospectId, toEstagio, motivo }: { prospectId: string; toEstagio: string; motivo?: string }) =>
      moveGrowthProspectStage(prospectId, { toEstagio, motivo: motivo ?? `Movido para ${STAGE_LABEL[toEstagio] ?? toEstagio} pelo painel comercial.` }),
    onSuccess: () => {
      toast({ title: 'Estágio atualizado' });
      setLossProspect(null);
      setLossReason('');
      invalidateAll();
    },
    onError: (error: any) => {
      toast({
        title: 'Não foi possível mover o estágio',
        description: error?.message ?? 'Erro inesperado.',
        variant: 'destructive',
      });
    },
  });

  const [activityDraft, setActivityDraft] = useState({ tipo: 'ligacao', descricao: '' });
  const activityMutation = useMutation({
    mutationFn: () => {
      if (!drawerProspectId) throw new Error('Nenhum prospect selecionado.');
      return createGrowthActivity(drawerProspectId, activityDraft);
    },
    onSuccess: () => {
      toast({ title: 'Toque registrado' });
      setActivityDraft({ tipo: 'ligacao', descricao: '' });
      void queryClient.invalidateQueries({ queryKey: ['platform', 'growth', 'prospect', drawerProspectId] });
    },
    onError: (error: any) => {
      toast({
        title: 'Não foi possível registrar a atividade',
        description: error?.message ?? 'Erro inesperado.',
        variant: 'destructive',
      });
    },
  });
  const editMutation = useMutation({
    mutationFn: () => {
      if (!editProspect) throw new Error('Nenhum prospect selecionado.');
      return updateGrowthProspect(editProspect.id, {
        nomeEscritorio: editDraft.nomeEscritorio.trim(),
        contatoNome: editDraft.contatoNome.trim(),
        contatoEmail: editDraft.contatoEmail.trim(),
        contatoTelefone: editDraft.contatoTelefone.trim(),
        cidade: editDraft.cidade.trim(),
        uf: editDraft.uf.trim().toUpperCase(),
        origem: editDraft.origem,
        valorEstimadoCents: Math.max(0, Math.round(Number(editDraft.valorEstimado || 0) * 100)),
        notas: editDraft.notas.trim(),
        ownerUserId: editDraft.ownerUserId || null,
        score: editDraft.score === '' ? null : Number(editDraft.score),
        nextAction: editDraft.nextAction.trim() || null,
        nextActionAt: editDraft.nextActionAt || null,
        campaign: editDraft.campaign.trim() || null,
        utmSource: editDraft.utmSource.trim() || null,
        utmMedium: editDraft.utmMedium.trim() || null,
        utmCampaign: editDraft.utmCampaign.trim() || null,
        proposalStatus: editDraft.proposalStatus || null,
        proposalReference: editDraft.proposalReference.trim() || null,
        proposalValueCents: editDraft.proposalValue === '' ? null : Math.round(Number(editDraft.proposalValue) * 100),
      });
    },
    onSuccess: () => {
      toast({ title: 'Prospect atualizado' });
      setEditProspect(null);
      invalidateAll();
      void queryClient.invalidateQueries({ queryKey: ['platform', 'growth', 'prospect', drawerProspectId] });
    },
    onError: (error: any) => toast({ title: 'Não foi possível atualizar o prospect', description: error?.message ?? 'Erro inesperado.', variant: 'destructive' }),
  });

  function beginProspectEdit(prospect: GrowthProspect) {
    setEditProspect(prospect);
    setEditDraft({
      nomeEscritorio: prospect.nome_escritorio,
      contatoNome: prospect.contato_nome ?? '',
      contatoEmail: prospect.contato_email ?? '',
      contatoTelefone: prospect.contato_telefone ?? '',
      cidade: prospect.cidade ?? '',
      uf: prospect.uf ?? '',
      origem: prospect.origem,
      valorEstimado: String(prospect.valor_estimado_brl ?? 0),
      notas: prospect.notas ?? '',
      ownerUserId: prospect.owner_user_id ?? '',
      score: prospect.score == null ? '' : String(prospect.score),
      nextAction: prospect.next_action ?? '',
      nextActionAt: prospect.next_action_at ? prospect.next_action_at.slice(0, 16) : '',
      campaign: prospect.campaign ?? '',
      utmSource: prospect.utm_source ?? '',
      utmMedium: prospect.utm_medium ?? '',
      utmCampaign: prospect.utm_campaign ?? '',
      proposalStatus: prospect.proposal_status ?? '',
      proposalReference: prospect.proposal_reference ?? '',
      proposalValue: prospect.proposal_value_cents == null ? '' : String(prospect.proposal_value_cents / 100),
    });
  }

  function beginProspectConversion(prospect: GrowthProspect) {
    setConvertProspect(prospect);
    setConvertDraft({
      workspaceName: prospect.nome_escritorio,
      slug: '',
      ownerName: prospect.contato_nome ?? '',
      ownerEmail: prospect.contato_email ?? '',
      planKey: 'pro',
      setupMode: 'assisted',
    });
  }

  const conversionMutation = useMutation({
    mutationFn: () => {
      if (!convertProspect) throw new Error('Nenhum prospect selecionado.');
      return convertGrowthProspectToWorkspace(convertProspect.id, {
        workspaceName: convertDraft.workspaceName.trim(),
        slug: convertDraft.slug.trim() || undefined,
        ownerName: convertDraft.ownerName.trim(),
        ownerEmail: convertDraft.ownerEmail.trim(),
        planKey: convertDraft.planKey,
        setupMode: convertDraft.setupMode,
      });
    },
    onSuccess: (result) => {
      toast({ title: result.already_converted ? 'Prospect já convertido' : 'Conta criada e convite do titular emitido.' });
      setConvertProspect(null);
      setDrawerProspectId(null);
      invalidateAll();
      void queryClient.invalidateQueries({ queryKey: ['platform', 'workspaces'] });
      navigate('/blackbox/workspaces', { state: { workspaceId: result.workspace_id } });
    },
    onError: (error: any) => toast({ title: 'Não foi possível converter o prospect', description: error?.message ?? 'Erro inesperado.', variant: 'destructive' }),
  });

  const board = useMemo(() => Object.fromEntries(
    BOARD_STAGES.map((stage, index) => [stage, stageQueries[index]?.data?.items ?? []]),
  ) as Record<string, GrowthProspect[]>, [stageQueries]);
  const metrics = metricsQuery.data;

  const origemChartData = useMemo(
    () =>
      Object.entries(metrics?.by_origem ?? {}).map(([origem, count]) => ({
        name: ORIGEM_LABEL[origem] ?? origem,
        value: count,
      })),
    [metrics],
  );

  const stageChartData = useMemo(
    () =>
      BOARD_STAGES.map((stage) => ({
        name: STAGE_LABEL[stage],
        count: metrics?.by_stage?.[stage]?.count ?? 0,
      })),
    [metrics],
  );

  return (
    <div className="space-y-6">
      {/* Métricas do funil */}
      {metricsQuery.isError && (
        <div role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          KPIs comerciais indisponíveis. Nenhum zero substitutivo foi exibido; tente novamente após verificar a fonte.
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="text-xs">Prospects no funil</CardDescription>
            <CardTitle className="text-2xl font-bold tabular-nums">
              {metricsQuery.isLoading ? <Skeleton className="h-8 w-16" /> : metricsQuery.isError || !metrics ? '—' : metrics.total_prospects}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="text-xs">Win rate</CardDescription>
            <CardTitle className="text-2xl font-bold tabular-nums">
              {metricsQuery.isLoading ? <Skeleton className="h-8 w-16" /> : metricsQuery.isError || !metrics ? '—' : `${metrics.win_rate_pct}%`}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="text-xs">Conversão prospect → pagante</CardDescription>
            <CardTitle className="text-2xl font-bold tabular-nums">
              {metricsQuery.isLoading ? (
                <Skeleton className="h-8 w-16" />
              ) : metricsQuery.isError || !metrics ? '—' : `${metrics.conversion_prospect_to_pagante_pct}%`}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="text-xs">Pipeline estimado (aberto)</CardDescription>
            <CardTitle className="text-2xl font-bold tabular-nums">
              {metricsQuery.isLoading ? (
                <Skeleton className="h-8 w-24" />
              ) : metricsQuery.isError || !metrics ? '—' : fmtBRL(metrics.pipeline_estimado_brl)}
            </CardTitle>
          </CardHeader>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <TrendingUp className="h-4 w-4" /> Prospects por estágio
            </CardTitle>
          </CardHeader>
          <CardContent>
            {metricsQuery.isLoading ? (
              <Skeleton className="h-48 w-full" />
            ) : metricsQuery.isError ? (
              <p className="py-12 text-center text-sm text-muted-foreground">Indicador indisponível.</p>
            ) : (
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={stageChartData}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                  <XAxis dataKey="name" tick={{ fontSize: 11 }} interval={0} angle={-20} textAnchor="end" height={60} />
                  <YAxis allowDecimals={false} />
                  <Tooltip />
                  <Bar dataKey="count" fill="#2563eb" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Target className="h-4 w-4" /> Origem dos prospects
            </CardTitle>
          </CardHeader>
          <CardContent>
            {metricsQuery.isLoading ? (
              <Skeleton className="h-48 w-full" />
            ) : metricsQuery.isError ? (
              <p className="py-12 text-center text-sm text-muted-foreground">Indicador indisponível.</p>
            ) : origemChartData.length === 0 ? (
              <p className="py-12 text-center text-sm text-muted-foreground">Sem prospects ainda.</p>
            ) : (
              <ResponsiveContainer width="100%" height={220}>
                <PieChart>
                  <Pie data={origemChartData} dataKey="value" nameKey="name" innerRadius={50} outerRadius={80}>
                    {origemChartData.map((_, index) => (
                      <Cell key={index} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip />
                </PieChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="relative w-full max-w-sm">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Buscar por nome, contato, cidade..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8"
          />
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="mr-2 h-4 w-4" /> Novo prospect
        </Button>
      </div>

      {/* Kanban */}
      <div className="grid gap-3 overflow-x-auto pb-2 lg:grid-cols-6">
        {BOARD_STAGES.map((stage) => {
          const stageIndex = BOARD_STAGES.indexOf(stage);
          const stageQuery = stageQueries[stageIndex];
          const items = board[stage] ?? [];
          const total = stageQuery.data?.total ?? stageQuery.data?.count ?? 0;
          const hasMore = stageQuery.data?.hasMore
            ?? Boolean(stageQuery.data && stageQuery.data.offset + stageQuery.data.items.length < total);
          const currentPage = stagePages[stage] ?? 0;
          return (
            <div key={stage} className="min-w-[220px] rounded-lg border bg-muted/30 p-2">
              <div className="mb-2 flex items-center justify-between px-1">
                <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {STAGE_LABEL[stage]}
                </span>
                <Badge variant="secondary">{stageQuery.isError ? '—' : total}</Badge>
              </div>
              <div className="space-y-2">
                {stageQuery.isLoading &&
                  Array.from({ length: 2 }).map((_, i) => <Skeleton key={i} className="h-24 w-full rounded-md" />)}
                {stageQuery.isError && (
                  <div className="rounded-md border border-destructive/30 p-3 text-center text-xs text-destructive">
                    Coluna indisponível.
                    <Button size="sm" variant="ghost" className="mt-1 w-full" onClick={() => void stageQuery.refetch()}>Tentar novamente</Button>
                  </div>
                )}
                {!stageQuery.isLoading && !stageQuery.isError && items.length === 0 && (
                  <p className="px-1 py-6 text-center text-xs text-muted-foreground">Nenhum prospect aqui.</p>
                )}
                {items.map((prospect) => {
                  const nextStageIndex = BOARD_STAGES.indexOf(stage) + 1;
                  const nextStage = BOARD_STAGES[nextStageIndex];
                  return (
                    <Card
                      key={prospect.id}
                      className="cursor-pointer border-border/60 shadow-sm transition hover:shadow-md"
                      onClick={() => setDrawerProspectId(prospect.id)}
                    >
                      <CardContent className="space-y-2 p-3">
                        <div className="flex items-start gap-2">
                          <Building2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                          <p className="min-w-0 text-sm font-semibold leading-tight">{prospect.nome_escritorio}</p>
                        </div>
                        {prospect.contato_nome && (
                          <p className="flex items-center gap-1 text-xs text-muted-foreground">
                            <Phone className="h-3 w-3 shrink-0" /> <span className="truncate">{prospect.contato_nome}</span>
                          </p>
                        )}
                        <div className="flex items-center justify-between gap-2">
                          <span className="shrink-0 text-xs font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">
                            {fmtBRL(prospect.valor_estimado_brl)}
                          </span>
                          <Badge variant="outline" className="min-w-0 text-2xs">
                            <span className="truncate">{ORIGEM_LABEL[prospect.origem] ?? prospect.origem}</span>
                          </Badge>
                        </div>
                        <div className="flex items-center justify-between gap-2 text-2xs text-muted-foreground">
                          <span>{prospect.score == null ? 'Score não informado' : `Score ${prospect.score}/100`}</span>
                          <span className="truncate">{prospect.next_action ?? 'Sem próxima ação'}</span>
                        </div>
                        {nextStage && (
                          <Button
                            size="sm"
                            variant="secondary"
                            className="w-full text-xs"
                            disabled={moveStageMutation.isPending}
                            onClick={(e) => {
                              e.stopPropagation();
                              moveStageMutation.mutate({ prospectId: prospect.id, toEstagio: nextStage });
                            }}
                          >
                            Mover para {STAGE_LABEL[nextStage]} →
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          className="w-full text-xs text-destructive hover:text-destructive"
                          disabled={moveStageMutation.isPending}
                          onClick={(e) => {
                            e.stopPropagation();
                            setLossProspect(prospect);
                          }}
                        >
                          Marcar como perdido
                        </Button>
                      </CardContent>
                    </Card>
                  );
                })}
                {stageQuery.data && total > 0 && (
                  <div className="space-y-1 border-t pt-2">
                    <p className="text-center text-2xs text-muted-foreground">
                      {stageQuery.data.offset + 1}–{Math.min(stageQuery.data.offset + items.length, total)} de {total}
                    </p>
                    <div className="grid grid-cols-2 gap-1">
                      <Button size="sm" variant="outline" disabled={currentPage === 0 || stageQuery.isFetching} onClick={() => setStagePages((current) => ({ ...current, [stage]: Math.max(0, currentPage - 1) }))}>Anterior</Button>
                      <Button size="sm" variant="outline" disabled={!hasMore || stageQuery.isFetching} onClick={() => setStagePages((current) => ({ ...current, [stage]: currentPage + 1 }))}>Próxima</Button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Dialog: novo prospect */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Novo prospect</DialogTitle>
            <DialogDescription>Adicionar um escritório-alvo ao funil de aquisição.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label htmlFor="gp-nome">Nome do escritório *</Label>
              <Input
                id="gp-nome"
                value={newProspect.nomeEscritorio}
                onChange={(e) => setNewProspect((s) => ({ ...s, nomeEscritorio: e.target.value }))}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label htmlFor="gp-contato">Contato</Label>
                <Input
                  id="gp-contato"
                  value={newProspect.contatoNome}
                  onChange={(e) => setNewProspect((s) => ({ ...s, contatoNome: e.target.value }))}
                />
              </div>
              <div>
                <Label htmlFor="gp-telefone">Telefone</Label>
                <Input
                  id="gp-telefone"
                  value={newProspect.contatoTelefone}
                  onChange={(e) => setNewProspect((s) => ({ ...s, contatoTelefone: e.target.value }))}
                />
              </div>
            </div>
            <div>
              <Label htmlFor="gp-email">E-mail</Label>
              <Input
                id="gp-email"
                type="email"
                value={newProspect.contatoEmail}
                onChange={(e) => setNewProspect((s) => ({ ...s, contatoEmail: e.target.value }))}
              />
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div className="col-span-2">
                <Label htmlFor="gp-cidade">Cidade</Label>
                <Input
                  id="gp-cidade"
                  value={newProspect.cidade}
                  onChange={(e) => setNewProspect((s) => ({ ...s, cidade: e.target.value }))}
                />
              </div>
              <div>
                <Label htmlFor="gp-uf">UF</Label>
                <Input
                  id="gp-uf"
                  maxLength={2}
                  value={newProspect.uf}
                  onChange={(e) => setNewProspect((s) => ({ ...s, uf: e.target.value.toUpperCase() }))}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Origem</Label>
                <Select
                  value={newProspect.origem}
                  onValueChange={(v) => setNewProspect((s) => ({ ...s, origem: v }))}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(ORIGEM_LABEL)
                      .filter(([k]) => k !== 'backfill')
                      .map(([value, label]) => (
                        <SelectItem key={value} value={value}>
                          {label}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label htmlFor="gp-valor">Valor estimado (R$/mês)</Label>
                <Input
                  id="gp-valor"
                  type="number"
                  min={0}
                  value={newProspect.valorEstimadoCents}
                  onChange={(e) => setNewProspect((s) => ({ ...s, valorEstimadoCents: e.target.value }))}
                />
              </div>
            </div>
            <div>
              <Label htmlFor="gp-notas">Notas</Label>
              <Textarea
                id="gp-notas"
                rows={3}
                value={newProspect.notas}
                onChange={(e) => setNewProspect((s) => ({ ...s, notas: e.target.value }))}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              Cancelar
            </Button>
            <Button
              disabled={!newProspect.nomeEscritorio.trim() || createMutation.isPending}
              onClick={() => createMutation.mutate()}
            >
              Criar prospect
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(lossProspect)} onOpenChange={(open) => { if (!open) { setLossProspect(null); setLossReason(''); } }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Marcar oportunidade como perdida</DialogTitle>
            <DialogDescription>
              Registre o motivo real para melhorar origem, qualificação e abordagem comercial.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="growth-loss-reason">Motivo da perda</Label>
            <Textarea id="growth-loss-reason" rows={3} value={lossReason} onChange={(event) => setLossReason(event.target.value)} />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setLossProspect(null); setLossReason(''); }}>Cancelar</Button>
            <Button
              variant="destructive"
              disabled={!lossProspect || lossReason.trim().length < 3 || moveStageMutation.isPending}
              onClick={() => lossProspect && moveStageMutation.mutate({ prospectId: lossProspect.id, toEstagio: 'perdido', motivo: lossReason.trim() })}
            >Confirmar perda</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(editProspect)} onOpenChange={(open) => { if (!open) setEditProspect(null); }}>
        <DialogContent className="max-h-[90vh] overflow-y-auto">
          <DialogHeader><DialogTitle>Editar prospect</DialogTitle><DialogDescription>Atualize os dados usados pelo pipeline e pelo forecast.</DialogDescription></DialogHeader>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1 sm:col-span-2"><Label htmlFor="growth-edit-name">Escritório</Label><Input id="growth-edit-name" value={editDraft.nomeEscritorio} onChange={(event) => setEditDraft((current) => ({ ...current, nomeEscritorio: event.target.value }))} /></div>
            <div className="space-y-1"><Label htmlFor="growth-edit-contact">Contato</Label><Input id="growth-edit-contact" value={editDraft.contatoNome} onChange={(event) => setEditDraft((current) => ({ ...current, contatoNome: event.target.value }))} /></div>
            <div className="space-y-1"><Label htmlFor="growth-edit-phone">Telefone</Label><Input id="growth-edit-phone" value={editDraft.contatoTelefone} onChange={(event) => setEditDraft((current) => ({ ...current, contatoTelefone: event.target.value }))} /></div>
            <div className="space-y-1 sm:col-span-2"><Label htmlFor="growth-edit-email">E-mail</Label><Input id="growth-edit-email" type="email" value={editDraft.contatoEmail} onChange={(event) => setEditDraft((current) => ({ ...current, contatoEmail: event.target.value }))} /></div>
            <div className="space-y-1"><Label htmlFor="growth-edit-city">Cidade</Label><Input id="growth-edit-city" value={editDraft.cidade} onChange={(event) => setEditDraft((current) => ({ ...current, cidade: event.target.value }))} /></div>
            <div className="space-y-1"><Label htmlFor="growth-edit-state">UF</Label><Input id="growth-edit-state" maxLength={2} value={editDraft.uf} onChange={(event) => setEditDraft((current) => ({ ...current, uf: event.target.value.toUpperCase() }))} /></div>
            <div className="space-y-1"><Label>Origem</Label><Select value={editDraft.origem} onValueChange={(origem) => setEditDraft((current) => ({ ...current, origem }))}><SelectTrigger aria-label="Origem do prospect"><SelectValue /></SelectTrigger><SelectContent>{Object.entries(ORIGEM_LABEL).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>
            <div className="space-y-1"><Label htmlFor="growth-edit-value">Valor estimado (R$/mês)</Label><Input id="growth-edit-value" type="number" min={0} value={editDraft.valorEstimado} onChange={(event) => setEditDraft((current) => ({ ...current, valorEstimado: event.target.value }))} /></div>
            <div className="space-y-1 sm:col-span-2"><Label>Responsável comercial</Label><PlatformEntityCombobox entityType="staff" purpose="growth_assignee" label="Responsável comercial" placeholder="Sem responsável" value={editDraft.ownerUserId || null} onChange={(ownerUserId) => setEditDraft((current) => ({ ...current, ownerUserId: ownerUserId ?? '' }))} allowClear /></div>
            <div className="space-y-1"><Label htmlFor="growth-edit-score">Score (0–100)</Label><Input id="growth-edit-score" type="number" min={0} max={100} value={editDraft.score} onChange={(event) => setEditDraft((current) => ({ ...current, score: event.target.value }))} /></div>
            <div className="space-y-1"><Label htmlFor="growth-edit-next-at">Prazo da próxima ação</Label><Input id="growth-edit-next-at" type="datetime-local" value={editDraft.nextActionAt} onChange={(event) => setEditDraft((current) => ({ ...current, nextActionAt: event.target.value }))} /></div>
            <div className="space-y-1 sm:col-span-2"><Label htmlFor="growth-edit-next">Próxima ação</Label><Input id="growth-edit-next" value={editDraft.nextAction} onChange={(event) => setEditDraft((current) => ({ ...current, nextAction: event.target.value }))} /></div>
            <div className="space-y-1"><Label htmlFor="growth-edit-campaign">Campanha</Label><Input id="growth-edit-campaign" value={editDraft.campaign} onChange={(event) => setEditDraft((current) => ({ ...current, campaign: event.target.value }))} /></div>
            <div className="space-y-1"><Label htmlFor="growth-edit-utm-campaign">UTM campaign</Label><Input id="growth-edit-utm-campaign" value={editDraft.utmCampaign} onChange={(event) => setEditDraft((current) => ({ ...current, utmCampaign: event.target.value }))} /></div>
            <div className="space-y-1"><Label htmlFor="growth-edit-utm-source">UTM source</Label><Input id="growth-edit-utm-source" value={editDraft.utmSource} onChange={(event) => setEditDraft((current) => ({ ...current, utmSource: event.target.value }))} /></div>
            <div className="space-y-1"><Label htmlFor="growth-edit-utm-medium">UTM medium</Label><Input id="growth-edit-utm-medium" value={editDraft.utmMedium} onChange={(event) => setEditDraft((current) => ({ ...current, utmMedium: event.target.value }))} /></div>
            <div className="space-y-1"><Label>Status da proposta</Label><Select value={editDraft.proposalStatus || 'none'} onValueChange={(proposalStatus) => setEditDraft((current) => ({ ...current, proposalStatus: proposalStatus === 'none' ? '' : proposalStatus }))}><SelectTrigger aria-label="Status da proposta"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="none">Sem proposta</SelectItem><SelectItem value="draft">Rascunho</SelectItem><SelectItem value="sent">Enviada</SelectItem><SelectItem value="accepted">Aceita</SelectItem><SelectItem value="rejected">Recusada</SelectItem><SelectItem value="expired">Expirada</SelectItem></SelectContent></Select></div>
            <div className="space-y-1"><Label htmlFor="growth-edit-proposal-value">Valor da proposta (R$/mês)</Label><Input id="growth-edit-proposal-value" type="number" min={0} value={editDraft.proposalValue} onChange={(event) => setEditDraft((current) => ({ ...current, proposalValue: event.target.value }))} /></div>
            <div className="space-y-1 sm:col-span-2"><Label htmlFor="growth-edit-proposal-ref">Referência da proposta</Label><Input id="growth-edit-proposal-ref" value={editDraft.proposalReference} onChange={(event) => setEditDraft((current) => ({ ...current, proposalReference: event.target.value }))} /></div>
            <div className="space-y-1 sm:col-span-2"><Label htmlFor="growth-edit-notes">Notas</Label><Textarea id="growth-edit-notes" rows={3} value={editDraft.notas} onChange={(event) => setEditDraft((current) => ({ ...current, notas: event.target.value }))} /></div>
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setEditProspect(null)}>Cancelar</Button><Button disabled={!editDraft.nomeEscritorio.trim() || editMutation.isPending} onClick={() => editMutation.mutate()}>Salvar alterações</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(convertProspect)} onOpenChange={(open) => { if (!open) setConvertProspect(null); }}>
        <DialogContent>
          <DialogHeader><DialogTitle>Converter prospect em conta</DialogTitle><DialogDescription>A conta será criada em provisionamento e o titular receberá um convite. A operação é transacional.</DialogDescription></DialogHeader>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1 sm:col-span-2"><Label htmlFor="growth-convert-workspace">Nome da conta</Label><Input id="growth-convert-workspace" value={convertDraft.workspaceName} onChange={(event) => setConvertDraft((current) => ({ ...current, workspaceName: event.target.value }))} /></div>
            <div className="space-y-1 sm:col-span-2"><Label htmlFor="growth-convert-slug">Slug opcional</Label><Input id="growth-convert-slug" value={convertDraft.slug} onChange={(event) => setConvertDraft((current) => ({ ...current, slug: event.target.value }))} /></div>
            <div className="space-y-1"><Label htmlFor="growth-convert-owner">Titular</Label><Input id="growth-convert-owner" value={convertDraft.ownerName} onChange={(event) => setConvertDraft((current) => ({ ...current, ownerName: event.target.value }))} /></div>
            <div className="space-y-1"><Label htmlFor="growth-convert-email">E-mail do titular</Label><Input id="growth-convert-email" type="email" value={convertDraft.ownerEmail} onChange={(event) => setConvertDraft((current) => ({ ...current, ownerEmail: event.target.value }))} /></div>
            <div className="space-y-1"><Label>Plano inicial</Label><Select value={convertDraft.planKey} onValueChange={(planKey) => setConvertDraft((current) => ({ ...current, planKey }))}><SelectTrigger aria-label="Plano inicial da conversão"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="basic">Basic</SelectItem><SelectItem value="pro">Pro</SelectItem><SelectItem value="scale">Scale</SelectItem></SelectContent></Select></div>
            <div className="space-y-1"><Label>Onboarding</Label><Select value={convertDraft.setupMode} onValueChange={(setupMode) => setConvertDraft((current) => ({ ...current, setupMode }))}><SelectTrigger aria-label="Modo de onboarding"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="assisted">Assistido</SelectItem><SelectItem value="self_service">Self-service</SelectItem></SelectContent></Select></div>
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setConvertProspect(null)}>Cancelar</Button><Button disabled={convertDraft.workspaceName.trim().length < 2 || convertDraft.ownerName.trim().length < 2 || !convertDraft.ownerEmail.includes('@') || conversionMutation.isPending} onClick={() => conversionMutation.mutate()}>{conversionMutation.isPending ? 'Convertendo...' : 'Criar conta e convidar titular'}</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Sheet: drawer do prospect */}
      <Sheet open={Boolean(drawerProspectId)} onOpenChange={(open) => !open && setDrawerProspectId(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
          {drawerQuery.isLoading || !drawerQuery.data ? (
            <div className="space-y-3 pt-6">
              <Skeleton className="h-6 w-2/3" />
              <Skeleton className="h-4 w-1/3" />
              <Skeleton className="h-32 w-full" />
            </div>
          ) : (
            <>
              <SheetHeader>
                <SheetTitle>{drawerQuery.data.nome_escritorio}</SheetTitle>
                <SheetDescription>
                  {STAGE_LABEL[drawerQuery.data.estagio] ?? drawerQuery.data.estagio} ·{' '}
                  {ORIGEM_LABEL[drawerQuery.data.origem] ?? drawerQuery.data.origem}
                </SheetDescription>
              </SheetHeader>

              <div className="mt-4 space-y-4">
                <div className="flex justify-end gap-2">
                  <Button size="sm" variant="outline" onClick={() => beginProspectEdit(drawerQuery.data)}>Editar cadastro</Button>
                  {drawerQuery.data.workspace_id
                    ? <Button size="sm" onClick={() => navigate('/blackbox/workspaces', { state: { workspaceId: drawerQuery.data.workspace_id } })}>Abrir conta</Button>
                    : <Button size="sm" onClick={() => beginProspectConversion(drawerQuery.data)}>Converter em conta</Button>}
                </div>
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div>
                    <p className="text-xs text-muted-foreground">Contato</p>
                    <p>{drawerQuery.data.contato_nome ?? '—'}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">Telefone</p>
                    <p>{drawerQuery.data.contato_telefone ?? '—'}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">E-mail</p>
                    <p className="truncate">{drawerQuery.data.contato_email ?? '—'}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">Cidade/UF</p>
                    <p>
                      {drawerQuery.data.cidade ?? '—'}
                      {drawerQuery.data.uf ? `/${drawerQuery.data.uf}` : ''}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">Valor estimado</p>
                    <p>{fmtBRL(drawerQuery.data.valor_estimado_brl)}/mês</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">No estágio desde</p>
                    <p>{fmtDate(drawerQuery.data.stage_entered_at)}</p>
                  </div>
                </div>

                {drawerQuery.data.notas && (
                  <div>
                    <p className="text-xs text-muted-foreground">Notas</p>
                    <p className="text-sm">{drawerQuery.data.notas}</p>
                  </div>
                )}

                <div className="rounded-md border p-3 text-sm">
                  <p className="font-medium">Qualificação e proposta</p>
                  <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                    <div><p className="text-muted-foreground">Score</p><p>{drawerQuery.data.score == null ? 'Não informado' : `${drawerQuery.data.score}/100`}</p></div>
                    <div><p className="text-muted-foreground">Responsável</p><p>{drawerQuery.data.owner_name ?? 'Não atribuído'}</p></div>
                    <div><p className="text-muted-foreground">Próxima ação</p><p>{drawerQuery.data.next_action ?? 'Não definida'}</p></div>
                    <div><p className="text-muted-foreground">Prazo</p><p>{fmtDate(drawerQuery.data.next_action_at)}</p></div>
                    <div><p className="text-muted-foreground">Campanha</p><p>{drawerQuery.data.campaign ?? drawerQuery.data.utm_campaign ?? 'Não identificada'}</p></div>
                    <div><p className="text-muted-foreground">Proposta</p><p>{drawerQuery.data.proposal_status ?? 'Não criada'}{drawerQuery.data.proposal_value_cents != null ? ` · ${fmtBRL(drawerQuery.data.proposal_value_cents / 100)}` : ''}</p></div>
                  </div>
                </div>

                <div className="rounded-md border p-3">
                  <p className="mb-2 text-sm font-medium">Registrar toque</p>
                  <div className="flex flex-col gap-2 sm:flex-row">
                    <Select
                      value={activityDraft.tipo}
                      onValueChange={(v) => setActivityDraft((s) => ({ ...s, tipo: v }))}
                    >
                      <SelectTrigger className="sm:w-40">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {Object.entries(ACTIVITY_LABEL).map(([value, label]) => (
                          <SelectItem key={value} value={value}>
                            {label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Input
                      placeholder="Descrição breve"
                      value={activityDraft.descricao}
                      onChange={(e) => setActivityDraft((s) => ({ ...s, descricao: e.target.value }))}
                    />
                    <Button
                      disabled={activityMutation.isPending}
                      onClick={() => activityMutation.mutate()}
                    >
                      Registrar
                    </Button>
                  </div>
                </div>

                <div>
                  <p className="mb-2 text-sm font-medium">Histórico de atividades</p>
                  {(drawerQuery.data.activities ?? []).length === 0 ? (
                    <p className="text-xs text-muted-foreground">Nenhuma atividade registrada ainda.</p>
                  ) : (
                    <ul className="space-y-2">
                      {(drawerQuery.data.activities ?? []).map((activity) => (
                        <li key={activity.id} className="rounded-md border p-2 text-xs">
                          <div className="flex items-center justify-between">
                            <Badge variant="outline">{ACTIVITY_LABEL[activity.tipo] ?? activity.tipo}</Badge>
                            <span className="text-muted-foreground">{fmtDate(activity.occurred_at)}</span>
                          </div>
                          {activity.descricao && <p className="mt-1">{activity.descricao}</p>}
                          <p className="mt-1 text-muted-foreground">{activity.actor_label}</p>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                <div>
                  <p className="mb-2 text-sm font-medium">Histórico de estágios</p>
                  <ul className="space-y-1">
                    {(drawerQuery.data.stage_events ?? []).map((event) => (
                      <li key={event.id} className="text-xs text-muted-foreground">
                        {event.from_estagio ? `${STAGE_LABEL[event.from_estagio]} → ` : 'Criado em '}
                        {STAGE_LABEL[event.to_estagio] ?? event.to_estagio} · {fmtDate(event.occurred_at)}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
