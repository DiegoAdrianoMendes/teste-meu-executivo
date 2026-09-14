import { Fragment, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BarChart3, Check, ChevronDown, ChevronRight, Download, ExternalLink, FileInput, FlaskConical, History, Info, Link2, LoaderCircle, RefreshCw, Search, Send, ShieldAlert, ShieldCheck, Sparkles, Target } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { KlmMetricCard, KlmPanel, KlmPill, KlmProgress, KlmSectionTitle } from '@/klm/components/Primitives';
import { AcquisitionIntegrationsPanel } from '@/klm/components/AcquisitionIntegrationsPanel';
import { OperationalCopilot } from '@/klm/components/OperationalCopilot';
import {
  klmApi,
  type KlmCtwaBackfillReport,
  type KlmOperationalCopilotAction,
  type KlmTrackingSummary,
} from '@/klm/api';
import { formatBRL, formatDecimal } from '@/lib/formatters';
import { useOptionalAppStore } from '@/store/app-context';
import { CustomPeriodButton } from '@/components/common/CustomPeriodButton';
import { PersonalizedFilterBuilder } from '@/components/common/PersonalizedFilterBuilder';
import { PermissionGate } from '@/klm/components/PermissionGate';
import {
  EMPTY_PERSONALIZED_FILTER,
  matchesPersonalizedFilter,
  type PersonalizedFilterDefinition,
  type PersonalizedFilterField,
} from '@/lib/personalized-filters';
import {
  type ContextualPageProps,
  useContextualNavigate,
  useContextualSearchParams,
} from '@/klm/hooks/useContextualRouting';

const EVENT_LABELS: Record<string, string> = {
  lead: 'Lead recebido',
  qualified_lead: 'Lead qualificado',
  schedule: 'Agendamento',
  meeting_scheduled: 'Reunião agendada',
  contract_created: 'Contrato criado',
  payment_confirmed: 'Pagamento confirmado',
  ad_spend: 'Investimento em anúncio',
  client: 'Cliente',
  contact: 'Contato',
};
const eventLabel = (value: string) => EVENT_LABELS[value.trim().toLocaleLowerCase().replaceAll(' ', '_')] ?? value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());

const periodOptions = [
  { value: '1', label: 'Hoje' },
  { value: '7', label: 'Últimos 7 dias' },
  { value: '30', label: 'Últimos 30 dias' },
  { value: '90', label: 'Últimos 90 dias' },
] as const;

const trackingTabs = ['canais', 'atribuicao', 'captacao', 'eventos', 'funil', 'jornadas', 'qualidade', 'feedback', 'remarketing'] as const;
type TrackingTab = (typeof trackingTabs)[number];
const TRACKING_TAB_DETAILS: Record<TrackingTab, { label: string; help: string }> = {
  canais: { label: 'Canais', help: 'Compara investimento, leads, clientes e retorno por origem.' },
  atribuicao: { label: 'Campanhas e criativos', help: 'Mostra campanhas, anúncios, criativos e evidências que originaram as métricas.' },
  captacao: { label: 'Captação e anúncios', help: 'Reúne formulários, integrações e entradas vindas de mídia paga.' },
  eventos: { label: 'Eventos e pixels', help: 'Confere os eventos de rastreamento recebidos e a cobertura técnica.' },
  funil: { label: 'Funil atribuído', help: 'Apresenta a evolução das pessoas entre as etapas comerciais.' },
  jornadas: { label: 'Jornadas', help: 'Mostra, em linguagem simples, o caminho percorrido até a conversão.' },
  qualidade: { label: 'Qualidade comercial', help: 'Aponta falhas de atendimento que afetam a leitura das campanhas.' },
  feedback: { label: 'Retorno aos anúncios', help: 'Exibe conversões preparadas para retorno aos provedores de anúncios.' },
  remarketing: { label: 'Reengajamento', help: 'Identifica públicos que avançaram, mas ainda não converteram.' },
};
const channelBarColors = ['bg-[#031121]', 'bg-[#c8a24d]', 'bg-emerald-600', 'bg-sky-500', 'bg-rose-500', 'bg-violet-500', 'bg-zinc-500'];
type TrackingChannel = KlmTrackingSummary['byChannel'][number] & {
  name: string;
  score: number;
  signal: string;
  tone: 'green' | 'red' | 'gold';
  cpl: number | null;
};
type AttributionRow = KlmTrackingSummary['attributionRows'][number];

function normalizedPeriod(value: string | null) {
  const aliases: Record<string, string> = { today: '1', '1d': '1', '7d': '7', '30d': '30', '90d': '90' };
  const candidate = value ? aliases[value] ?? value : '30';
  const numeric = Number(candidate);
  return Number.isInteger(numeric) && numeric >= 1 && numeric <= 3_650 ? String(numeric) : '30';
}

function normalizedTab(value: string | null): TrackingTab {
  return trackingTabs.includes(value as TrackingTab) ? value as TrackingTab : 'canais';
}

function channelScore(channel: KlmTrackingSummary['byChannel'][number]) {
  if (channel.roas != null) return Math.min(100, Math.round(channel.roas * 20 + (channel.conversionRate ?? 0) / 3));
  if (channel.clients > 0) return 82;
  if (channel.qualifiedLeads > 0) return 64;
  if (channel.leads > 0) return 44;
  return 20;
}

function normalizeTrackingSummary(value: KlmTrackingSummary | undefined): KlmTrackingSummary | undefined {
  if (!value) return undefined;
  const partial = value as Partial<KlmTrackingSummary>;

  return {
    ...value,
    byChannel: partial.byChannel ?? [],
    eventCoverage: partial.eventCoverage ?? [],
    timeTrend: partial.timeTrend ?? [],
    journeys: partial.journeys ?? [],
    remarketing: partial.remarketing ?? [],
    attributionCoverage: {
      conversations: 0,
      trackedConversations: 0,
      untrackedConversations: 0,
      trackingRate: null,
      conversionEvents: 0,
      attributedConversions: 0,
      unattributedConversions: 0,
      attributedConversionRate: null,
      feedbackEligibleConversions: 0,
      feedbackBlockedNoEvidence: 0,
      businessDuplicatesSuppressed: 0,
      fullHierarchyTouches: 0,
      partialHierarchyTouches: 0,
      ...(partial.attributionCoverage ?? {}),
    },
    attributionRows: partial.attributionRows ?? [],
    funnelSteps: partial.funnelSteps ?? [],
    insights: partial.insights ?? [],
    serviceQuality: {
      findings: 0,
      activeFindings: 0,
      criticalFindings: 0,
      unansweredLeads: 0,
      deliveryMismatches: 0,
      duplicateOutbound: 0,
      resolvedFindings: 0,
      auditedThreads: 0,
      qualityScore: 100,
      ...(partial.serviceQuality ?? {}),
    },
    conversionFeedback: {
      prepared: 0,
      blockedExternal: 0,
      awaitingApproval: 0,
      approvedLocal: 0,
      rejected: 0,
      duplicatePrevented: 0,
      payloadReady: 0,
      configurationBlocked: 0,
      preparedProofs: 0,
      providerReceipts: 0,
      failedReceipts: 0,
      providers: {},
      ...(partial.conversionFeedback ?? {}),
    },
    feedbackEvents: partial.feedbackEvents ?? [],
    dailyChannels: partial.dailyChannels ?? [],
    containsLocalFixtureData: partial.containsLocalFixtureData ?? false,
  };
}

export default function TrackingPage({ embedded = false, onNavigate, contextPath }: ContextualPageProps) {
  const appStore = useOptionalAppStore();
  const canManageIntegrations = appStore?.can('integrations', 'manage') ?? false;
  const canBackfillCtwa = appStore?.can('admin', 'manage') ?? false;
  const navigate = useContextualNavigate({ embedded, onNavigate });
  const [searchParams, setSearchParams] = useContextualSearchParams({ embedded, contextPath });
  const queryClient = useQueryClient();
  const period = normalizedPeriod(searchParams.get('period'));
  const activeTab = normalizedTab(searchParams.get('tab'));
  const requestedChannel = searchParams.get('channel')?.trim() ?? '';
  const [query, setQuery] = useState(() => requestedChannel ? eventLabel(requestedChannel) : '');
  const [personalizedFilter, setPersonalizedFilter] = useState<PersonalizedFilterDefinition>(EMPTY_PERSONALIZED_FILTER);
  const [refreshDone, setRefreshDone] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedAttribution, setSelectedAttribution] = useState<AttributionRow | null>(null);
  const [selectedMetric, setSelectedMetric] = useState<string | null>(null);
  const [expandedChannel, setExpandedChannel] = useState<string | null>(null);
  const trackingQuery = useQuery({
    queryKey: ['klm', 'tracking', period],
    queryFn: () => klmApi.tracking(Number(period)),
  });
  const summary = normalizeTrackingSummary(trackingQuery.data);
  const channels = useMemo<TrackingChannel[]>(() => (summary?.byChannel ?? []).map((channel) => {
    const score = channelScore(channel);
    const signal = channel.roas != null && channel.roas >= 3 && channel.clients > 0
      ? 'Escalar'
      : channel.adSpend === 0 && channel.clients > 0
        ? 'Expandir'
        : channel.leads > 0 && channel.clients === 0
          ? 'Corrigir'
          : 'Observar';
    return {
      ...channel,
      name: eventLabel(channel.channel),
      score,
      signal,
      tone: signal === 'Escalar' || signal === 'Expandir' ? 'green' as const : signal === 'Corrigir' ? 'red' as const : 'gold' as const,
      cpl: channel.leads ? channel.adSpend / channel.leads : null,
    };
  }), [summary?.byChannel]);
  const personalizedFields = useMemo<PersonalizedFilterField<TrackingChannel>[]>(() => [
    { key: 'channel', label: 'Canal', type: 'select', options: channels.map((channel) => ({ value: channel.channel, label: channel.name })) },
    { key: 'signal', label: 'Recomendação', type: 'select', options: ['Escalar', 'Expandir', 'Corrigir', 'Observar'].map((value) => ({ value, label: value })) },
    { key: 'adSpend', label: 'Investimento', type: 'number' },
    { key: 'leads', label: 'Leads', type: 'number' },
    { key: 'qualifiedLeads', label: 'Leads qualificados', type: 'number' },
    { key: 'meetings', label: 'Reuniões', type: 'number' },
    { key: 'clients', label: 'Clientes', type: 'number' },
    { key: 'conversionRate', label: 'Conversão (%)', type: 'number' },
    { key: 'cac', label: 'CAC', type: 'number' },
    { key: 'roas', label: 'ROAS', type: 'number' },
    { key: 'attributableRevenue', label: 'Receita atribuída', type: 'number' },
    { key: 'score', label: 'Score do canal', type: 'number' },
  ], [channels]);
  const filteredChannels = useMemo(() => channels.filter((channel) => (
    (!requestedChannel || channel.channel === requestedChannel)
    && channel.name.toLocaleLowerCase('pt-BR').includes(query.toLocaleLowerCase('pt-BR'))
    && matchesPersonalizedFilter(channel, personalizedFilter, personalizedFields)
  )), [channels, personalizedFields, personalizedFilter, query, requestedChannel]);
  const attributionByChannel = useMemo(() => {
    const groups = new Map<string, AttributionRow[]>();
    (summary?.attributionRows ?? []).forEach((row) => groups.set(row.channel, [...(groups.get(row.channel) ?? []), row]));
    return groups;
  }, [summary?.attributionRows]);
  const metricValueForChannel = (channel: TrackingChannel, metric: string) => {
    if (metric === 'Investimento') return { value: channel.adSpend, display: formatBRL(channel.adSpend) };
    if (metric === 'CPL consolidado') return { value: channel.cpl ?? 0, display: channel.cpl == null ? '—' : formatBRL(channel.cpl) };
    if (metric === 'CAC cliente') return { value: channel.cac ?? 0, display: channel.cac == null ? '—' : formatBRL(channel.cac) };
    return { value: channel.roas ?? 0, display: channel.roas == null ? '—' : `${formatDecimal(channel.roas)}x` };
  };
  const bestChannel = [...channels].sort((left, right) => right.score - left.score)[0];
  const weakestChannel = [...channels].sort((left, right) => left.score - right.score)[0];
  const maxTrendEvents = Math.max(1, ...(summary?.timeTrend ?? []).map((item) => item.events));
  const dailyChannelGroups = useMemo(() => {
    const groups = new Map<string, KlmTrackingSummary['dailyChannels']>();
    (summary?.dailyChannels ?? []).forEach((item) => groups.set(item.date, [...(groups.get(item.date) ?? []), item]));
    return [...groups.entries()].sort(([left], [right]) => left.localeCompare(right)).map(([date, items]) => ({
      date,
      items: [...items].sort((left, right) => right.events - left.events),
      total: items.reduce((total, item) => total + item.events, 0),
    }));
  }, [summary?.dailyChannels]);
  const dailyChannelNames = useMemo(() => [...new Set((summary?.dailyChannels ?? []).map((item) => item.channel))].sort(), [summary?.dailyChannels]);
  const maxDailyChannelEvents = Math.max(1, ...dailyChannelGroups.map((item) => item.total));
  const topAttributionRows = useMemo(() => [...(summary?.attributionRows ?? [])].sort((left, right) => (right.attributableRevenue - left.attributableRevenue) || (right.clients - left.clients) || (right.leads - left.leads)).slice(0, 6), [summary?.attributionRows]);
  const maxAttributionRevenue = Math.max(1, ...topAttributionRows.map((item) => item.attributableRevenue));
  const copilotContext = useMemo(() => ({
    period_days: Number(period),
    active_tab: activeTab,
    totals: {
      events: summary?.totalEvents ?? 0,
      identified_journeys: summary?.identifiedJourneys ?? 0,
      tracked_conversations: summary?.attributionCoverage.trackedConversations ?? 0,
      untracked_conversations: summary?.attributionCoverage.untrackedConversations ?? 0,
      tracking_rate: summary?.attributionCoverage.trackingRate ?? null,
    },
    channels: channels.slice(0, 20).map((channel) => ({
      channel: channel.channel,
      signal: channel.signal,
      score: channel.score,
      leads: channel.leads,
      clients: channel.clients,
      conversion_rate: channel.conversionRate,
    })),
  }), [
    activeTab,
    channels,
    period,
    summary?.attributionCoverage.trackedConversations,
    summary?.attributionCoverage.trackingRate,
    summary?.attributionCoverage.untrackedConversations,
    summary?.identifiedJourneys,
    summary?.totalEvents,
  ]);

  useEffect(() => {
    if (requestedChannel) setQuery(eventLabel(requestedChannel));
  }, [requestedChannel]);

  const updateSearchParams = (updates: Record<string, string | null>) => {
    const nextParams = new URLSearchParams(searchParams);
    Object.entries(updates).forEach(([key, value]) => {
      if (value == null) nextParams.delete(key);
      else nextParams.set(key, value);
    });
    setSearchParams(nextParams);
  };

  const intentMutation = useMutation({
    mutationFn: async (variables: { label: string; actionType: string; provider: string; payload: Record<string, unknown> }) => {
      const intent = await klmApi.createExternalIntent({
        actionType: variables.actionType,
        provider: variables.provider,
        idempotencyKey: `tracking:${variables.actionType}:${period}:${JSON.stringify(variables.payload)}`.slice(0, 180),
        payload: { ...variables.payload, periodDays: Number(period), externalActionsExecuted: 0 },
      });
      return klmApi.approveExternalIntentLocally(intent.id);
    },
    onSuccess: (intent, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['klm', 'external-intents'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'activity'] });
      setNotice(`${variables.label} registrada e aprovada localmente (${intent.status}); nenhuma plataforma de anúncios foi chamada.`);
    },
    onError: () => setNotice('Não foi possível registrar esta intenção de marketing no banco local.'),
  });
  const feedbackMutation = useMutation({
    mutationFn: () => klmApi.prepareTrackingConversionFeedback(Number(period)),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ['klm', 'tracking'] });
      setNotice(`${result.prepared} feedback(s) preparado(s), ${result.deduplicated} já protegido(s) contra repetição, ${result.suppressedBusinessDuplicates ?? 0} sinal(is) comercial(is) consolidado(s) e ${result.skippedWithoutEvidence} bloqueado(s) por falta de evidência. Chamadas externas: ${result.externalActionsExecuted}.`);
    },
    onError: (error: Error) => setNotice(`Não foi possível preparar o feedback: ${error.message}`),
  });
  const dispatchFeedbackMutation = useMutation({
    mutationFn: () => klmApi.dispatchTrackingConversionFeedback(),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ['klm', 'tracking'] });
      setNotice(`Despacho: ${result.preparedProcessed} processada(s), ${result.blockedSimulated} bloqueada(s)/simulada(s), ${result.dispatchedReal} enviada(s) de verdade (${result.alreadyDispatched} já despachada(s) antes)${result.externalActionsEnabled ? '' : '. Gate externo fechado: nenhuma chamada real foi feita.'}`);
    },
    onError: (error: Error) => setNotice(`Não foi possível despachar o feedback: ${error.message}`),
  });

  const [ctwaBackfillOpen, setCtwaBackfillOpen] = useState(false);
  const [ctwaDryRunReport, setCtwaDryRunReport] = useState<KlmCtwaBackfillReport | null>(null);
  const [ctwaExecuteReport, setCtwaExecuteReport] = useState<KlmCtwaBackfillReport | null>(null);
  const ctwaDryRunMutation = useMutation({
    mutationFn: () => klmApi.backfillCtwa({ dryRun: true }),
    onSuccess: (report) => setCtwaDryRunReport(report),
  });
  const ctwaExecuteMutation = useMutation({
    mutationFn: () => klmApi.backfillCtwa({ dryRun: false }),
    onSuccess: async (report) => {
      setCtwaExecuteReport(report);
      await queryClient.invalidateQueries({ queryKey: ['klm', 'tracking'] });
    },
  });
  const openCtwaBackfillDialog = () => {
    setCtwaDryRunReport(null);
    setCtwaExecuteReport(null);
    setCtwaBackfillOpen(true);
    ctwaDryRunMutation.mutate();
  };

  const refreshReading = async () => {
    setRefreshDone(false);
    const result = await trackingQuery.refetch();
    setRefreshDone(!result.isError);
  };

  const applyCopilotActions = async (actions: KlmOperationalCopilotAction[]) => {
    for (const action of actions) {
      const actionType = typeof action.payload.type === 'string' ? action.payload.type : '';
      if (actionType === 'tracking.select_tab') {
        const tab = typeof action.payload.tab === 'string'
          ? normalizedTab(action.payload.tab)
          : 'canais';
        updateSearchParams({ tab });
        continue;
      }
      if (actionType === 'tracking.set_period') {
        const days = Number(action.payload.period_days);
        if (!Number.isInteger(days) || days < 1 || days > 3_650) {
          throw new Error('O período sugerido não é válido.');
        }
        updateSearchParams({ period: String(days) });
        continue;
      }
      if (actionType === 'tracking.refresh') {
        await refreshReading();
        continue;
      }
      if (actionType === 'tracking.prepare_feedback') {
        if (!canManageIntegrations) {
          throw new Error('Seu perfil não pode preparar feedback para provedores.');
        }
        await feedbackMutation.mutateAsync();
      }
    }
    return 'A proposta foi aplicada ao painel. Nenhum evento foi enviado a provedores.';
  };

  const applyBudget = () => intentMutation.mutate({
    label: 'Intenção de redistribuição de orçamento',
    actionType: 'marketing_budget_reallocation',
    provider: 'ads_platforms',
    payload: {
      recommendations: channels.map((channel) => ({ channel: channel.channel, signal: channel.signal, score: channel.score, spend: channel.adSpend, roas: channel.roas })),
    },
  });

  const syncAudiences = () => intentMutation.mutate({
    label: 'Intenção de sincronização de audiências',
    actionType: 'remarketing_audiences_sync',
    provider: 'ads_platforms',
    payload: { audiences: summary?.remarketing ?? [] },
  });

  const exportAttributionCsv = () => {
    const rows = summary?.attributionRows ?? [];
    const columns = [
      'provedor', 'canal', 'conta_id', 'conta', 'campanha_id', 'campanha',
      'conjunto_id', 'conjunto', 'anuncio_id', 'anuncio', 'criativo_id', 'criativo',
      'formulario_id', 'placement', 'evidencia', 'completude', 'leads', 'clientes',
      'investimento', 'receita', 'cac', 'roas', 'primeiro_toque', 'ultimo_toque',
    ];
    const escape = (value: unknown) => `"${String(value ?? '').replaceAll('"', '""')}"`;
    const csv = [columns, ...rows.map((row) => [
      row.providerLabel, row.channel, row.accountId, row.accountName, row.campaignId,
      row.campaignName, row.adSetId, row.adSetName, row.adId, row.adName, row.creativeId,
      row.creativeName, row.formId, row.placement, row.evidenceLevel,
      row.hierarchyCompleteness, row.leads, row.clients, row.adSpend,
      row.attributableRevenue, row.cac, row.roas, row.firstTouchAt, row.lastTouchAt,
    ])].map((row) => row.map(escape).join(';')).join('\n');
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' }));
    link.download = `klm-atribuicao-${period}-dias.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  };

  if (trackingQuery.isError) {
    return (
      <div className="mx-auto w-full max-w-[1320px] space-y-5">
        <header>
          <p className="text-2xs font-semibold uppercase text-[#0057d8]">Marketing / Track</p>
          <h2 className="mt-2 text-2xl font-semibold">Rastreamento e aquisição</h2>
        </header>
        <KlmPanel className="border-red-300 bg-red-50 p-6 dark:border-red-900 dark:bg-red-950/30">
          <div className="flex items-start gap-3">
            <ShieldAlert className="mt-0.5 h-5 w-5 text-red-700" />
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold">A leitura de tracking falhou</h2>
              <p className="mt-1 text-2xs leading-5 text-muted-foreground">Os indicadores foram ocultados para não confundir indisponibilidade com zero eventos ou zero receita.</p>
              <Button onClick={() => void trackingQuery.refetch()} disabled={trackingQuery.isFetching} className="mt-4 h-9 text-xs">
                {trackingQuery.isFetching ? <LoaderCircle className="mr-2 h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="mr-2 h-3.5 w-3.5" />}
                Tentar novamente
              </Button>
            </div>
          </div>
        </KlmPanel>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full min-w-0 max-w-[1320px] space-y-5">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div><p className="text-2xs font-semibold uppercase text-[#0057d8]">Marketing / Track</p><h2 className="mt-2 text-2xl font-semibold">Rastreamento e aquisição</h2><p className="mt-1 max-w-3xl text-xs leading-4 text-muted-foreground">Atribuição, eventos, gasto, receita, CAC e oportunidades calculados sobre a base local.</p></div>
        <div className="flex flex-wrap items-center gap-2"><Select value={period} onValueChange={(value) => updateSearchParams({ period: value })}><SelectTrigger className="h-9 w-36 text-xs"><SelectValue /></SelectTrigger><SelectContent>{!periodOptions.some((option) => option.value === period) && <SelectItem value={period}>{period} dias</SelectItem>}{periodOptions.map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}</SelectContent></Select><CustomPeriodButton value={Number(period)} onChange={(days) => updateSearchParams({ period: String(days) })} /><PersonalizedFilterBuilder scope="acquisition-tracking" fields={personalizedFields} value={personalizedFilter} onChange={setPersonalizedFilter} className="h-9" /><Button onClick={() => void refreshReading()} disabled={trackingQuery.isFetching} className="h-9 bg-[#031121] text-xs text-white hover:bg-[#031121]">{trackingQuery.isFetching ? <LoaderCircle className="mr-2 h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="mr-2 h-3.5 w-3.5" />}Atualizar leitura</Button></div>
      </header>

      {summary?.containsLocalFixtureData && (
        <KlmPanel className="flex items-center gap-2 border-amber-300 bg-amber-50 p-3 text-2xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          <FlaskConical className="h-4 w-4 shrink-0" />
          <span>Inclui dados de demonstração (cenário local). Os totais abaixo misturam o cenário local com qualquer dado real já importado.</span>
        </KlmPanel>
      )}

      {(refreshDone || notice) && <KlmPanel className="flex items-center justify-between border-primary/30 bg-primary/10 p-3"><div className="flex items-center gap-2 text-2xs"><Check className="h-4 w-4 text-emerald-600" />{notice ?? `Leitura atualizada: ${summary?.totalEvents ?? 0} eventos retornados para o período.`}</div><Button variant="ghost" size="sm" onClick={() => { setRefreshDone(false); setNotice(null); }} className="h-7 text-2xs">Fechar</Button></KlmPanel>}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
        <KlmMetricCard
          label="Investimento"
          value={trackingQuery.isPending ? '—' : formatBRL(summary?.adSpend ?? 0)}
          delta={trackingQuery.isPending ? 'carregando base local' : `${channels.length} canais medidos`}
          trend="neutral"
          onClick={() => setSelectedMetric('Investimento')}
        />
        <KlmMetricCard
          label="CPL consolidado"
          value={trackingQuery.isPending ? '—' : summary?.leads ? formatBRL((summary.adSpend ?? 0) / summary.leads) : '—'}
          delta={trackingQuery.isPending ? 'carregando base local' : `${summary?.leads ?? 0} leads`}
          trend="neutral"
          onClick={() => setSelectedMetric('CPL consolidado')}
        />
        <KlmMetricCard
          label="CAC cliente"
          value={trackingQuery.isPending ? '—' : summary?.cac != null ? formatBRL(summary.cac) : '—'}
          delta={trackingQuery.isPending ? 'carregando base local' : `${summary?.clients ?? 0} clientes`}
          trend="neutral"
          onClick={() => setSelectedMetric('CAC cliente')}
        />
        <KlmMetricCard
          label="ROAS atribuído"
          value={trackingQuery.isPending ? '—' : summary?.roas != null ? `${formatDecimal(summary.roas)}x` : '—'}
          delta={trackingQuery.isPending ? 'carregando base local' : formatBRL(summary?.attributableRevenue ?? 0)}
          trend={summary?.roas != null && summary.roas >= 1 ? 'up' : 'neutral'}
          onClick={() => setSelectedMetric('ROAS atribuído')}
        />
        <KlmMetricCard
          label="Conversas rastreadas"
          value={summary?.attributionCoverage.trackingRate != null ? `${formatDecimal(summary.attributionCoverage.trackingRate)}%` : '—'}
          delta={`${summary?.attributionCoverage.trackedConversations ?? 0} de ${summary?.attributionCoverage.conversations ?? 0}`}
          trend={(summary?.attributionCoverage.trackingRate ?? 0) >= 90 ? 'up' : 'down'}
          onClick={() => updateSearchParams({ tab: 'atribuicao' })}
        />
        <KlmMetricCard
          label="Vendas atribuídas"
          value={summary?.attributionCoverage.attributedConversionRate != null ? `${formatDecimal(summary.attributionCoverage.attributedConversionRate)}%` : '—'}
          delta={`${summary?.attributionCoverage.attributedConversions ?? 0} de ${summary?.attributionCoverage.conversionEvents ?? 0}`}
          trend={(summary?.attributionCoverage.attributedConversionRate ?? 0) >= 90 ? 'up' : 'down'}
          onClick={() => updateSearchParams({ tab: 'feedback' })}
        />
      </div>

      <Tabs value={activeTab} onValueChange={(value) => updateSearchParams({ tab: normalizedTab(value) })} className="space-y-4"><TabsList className="h-9 w-full justify-start overflow-x-auto bg-secondary p-1">{trackingTabs.map((tab) => <TabsTrigger key={tab} value={tab} className="gap-1.5 text-xs">{TRACKING_TAB_DETAILS[tab].label}<Tooltip><TooltipTrigger asChild><span aria-hidden="true" onClick={(event) => event.stopPropagation()} className="inline-flex text-muted-foreground hover:text-foreground"><Info className="h-3 w-3" /></span></TooltipTrigger><TooltipContent className="max-w-64 text-xs">{TRACKING_TAB_DETAILS[tab].help}</TooltipContent></Tooltip></TabsTrigger>)}</TabsList>

        <TabsContent value="canais"><div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
          <div className="min-w-0 space-y-4"><KlmPanel className="p-4"><KlmSectionTitle title="Canais de aquisição" description="Clique em um canal para abrir campanhas, criativos e a origem das métricas." action={<KlmPill tone="green">base persistida</KlmPill>} /><div className="relative mt-4"><Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" /><Input value={query} onChange={(event) => { setQuery(event.target.value); if (requestedChannel) updateSearchParams({ channel: null }); }} className="h-9 bg-secondary pl-9 text-xs" placeholder="Buscar canal" /></div><div className="mt-4 overflow-x-auto rounded-lg border"><table className="w-full min-w-[920px] text-left text-2xs"><thead><tr className="border-b bg-secondary text-muted-foreground"><th className="p-3">Canal</th><th className="p-3">Invest.</th><th className="p-3">Leads</th><th className="p-3">Qualif.</th><th className="p-3">Reuniões</th><th className="p-3">Clientes</th><th className="p-3">CPL</th><th className="p-3">CAC</th><th className="p-3">Receita</th><th className="p-3">Sinal derivado</th></tr></thead><tbody>{filteredChannels.map((channel) => { const isExpanded = expandedChannel === channel.channel; const campaigns = attributionByChannel.get(channel.channel) ?? []; return <Fragment key={channel.channel}>{/* A linha inteira funciona como controle, sem esconder os dados tabulares. */}<tr tabIndex={0} role="button" aria-expanded={isExpanded} onClick={() => setExpandedChannel(isExpanded ? null : channel.channel)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setExpandedChannel(isExpanded ? null : channel.channel); } }} className="cursor-pointer border-b outline-none hover:bg-secondary focus-visible:bg-primary/10"><td className="p-3 font-semibold"><span className="flex items-center gap-2">{channel.name}<ChevronDown className={`h-3.5 w-3.5 transition-transform ${isExpanded ? 'rotate-180' : ''}`} /></span></td><td className="p-3">{formatBRL(channel.adSpend)}</td><td className="p-3">{channel.leads}</td><td className="p-3">{channel.qualifiedLeads}</td><td className="p-3">{channel.meetings}</td><td className="p-3">{channel.clients}</td><td className="p-3">{channel.cpl != null ? formatBRL(channel.cpl) : '—'}</td><td className="p-3">{channel.cac != null ? formatBRL(channel.cac) : '—'}</td><td className="p-3">{formatBRL(channel.attributableRevenue)}</td><td className="p-3"><KlmPill tone={channel.tone}>{channel.signal}</KlmPill></td></tr>{isExpanded && <tr key={`${channel.channel}-details`} className="border-b bg-secondary/45"><td colSpan={10} className="p-4"><div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_220px]"><div><strong className="text-xs">Campanhas e criativos de {channel.name}</strong><div className="mt-3 divide-y rounded-md border bg-card">{campaigns.map((campaign, index) => <button key={`${campaign.campaignId}-${campaign.adId}-${index}`} type="button" onClick={(event) => { event.stopPropagation(); setSelectedAttribution(campaign); }} className="grid w-full gap-2 p-3 text-left hover:bg-secondary sm:grid-cols-[minmax(0,1fr)_auto]"><span><strong className="block text-2xs">{campaign.campaignName ?? campaign.campaignId ?? 'Campanha não identificada'}</strong><small className="mt-1 block text-muted-foreground">{campaign.creativeName ?? campaign.adName ?? 'Criativo não identificado'} · {campaign.touchCount} toque(s)</small></span><span className="text-right"><strong className="block">{formatBRL(campaign.attributableRevenue)}</strong><small className="text-muted-foreground">{campaign.clients} cliente(s)</small></span></button>)}{!campaigns.length && <p className="p-4 text-2xs text-muted-foreground">Nenhuma campanha individual foi comprovada para este canal.</p>}</div></div><div className="space-y-2 rounded-md border bg-card p-3"><strong className="text-xs">Resumo do canal</strong><p className="text-2xs text-muted-foreground">Conversão: {formatDecimal(channel.conversionRate)}%</p><p className="text-2xs text-muted-foreground">ROAS: {channel.roas == null ? '—' : `${formatDecimal(channel.roas)}x`}</p><p className="text-2xs text-muted-foreground">Eventos: {channel.events}</p><Button variant="outline" size="sm" className="mt-2 h-8 w-full text-2xs" onClick={(event) => { event.stopPropagation(); updateSearchParams({ tab: 'atribuicao', channel: channel.channel }); }}>Ampliar campanhas</Button></div></div></td></tr>}</Fragment>; })}</tbody></table>{!trackingQuery.isPending && !filteredChannels.length && <div className="p-8 text-center text-2xs text-muted-foreground">Nenhum canal corresponde aos filtros do período.</div>}</div></KlmPanel>
          <KlmPanel className="p-4"><KlmSectionTitle title="Origem das conversas por dia" description="Distribuição visual persistida por canal, incluindo tráfego sem atribuição." action={<KlmPill tone="blue">{summary?.attributionCoverage.trackingRate != null ? `${formatDecimal(summary.attributionCoverage.trackingRate)}% rastreado` : 'sem base'}</KlmPill>} /><div className="mt-4 flex flex-wrap gap-x-4 gap-y-2">{dailyChannelNames.map((channel, index) => <span key={channel} className="flex items-center gap-1.5 text-2xs text-muted-foreground"><i className={`h-2.5 w-2.5 ${channelBarColors[index % channelBarColors.length]}`} />{eventLabel(channel)}</span>)}</div><div className="mt-4 grid h-[190px] grid-flow-col auto-cols-[minmax(54px,1fr)] items-end gap-2 overflow-x-auto border-b pb-1">{dailyChannelGroups.map((group) => <div key={group.date} className="grid h-full min-w-[54px] grid-rows-[1fr_30px] gap-1"><div className="flex min-h-0 flex-col-reverse justify-start overflow-hidden border-x border-t border-transparent bg-secondary/40" title={`${group.total} evento(s) em ${new Date(`${group.date}T12:00:00`).toLocaleDateString('pt-BR')}`}>{group.items.map((item) => { const colorIndex = Math.max(0, dailyChannelNames.indexOf(item.channel)); return <span key={`${group.date}-${item.channel}`} className={`${channelBarColors[colorIndex % channelBarColors.length]} min-h-[3px] w-full transition-opacity hover:opacity-75`} style={{ height: `${item.events / maxDailyChannelEvents * 100}%` }} title={`${eventLabel(item.channel)}: ${item.events} eventos, ${item.leads} leads, ${item.clients} clientes`} />; })}</div><span className="text-center text-2xs text-muted-foreground">{new Date(`${group.date}T12:00:00`).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })}</span></div>)}{!dailyChannelGroups.length && <p className="col-span-full self-center text-center text-2xs text-muted-foreground">A série por canal aparecerá quando houver eventos no período.</p>}</div></KlmPanel>
          <KlmPanel className="p-4"><KlmSectionTitle title="Tendência diária" description="Volume de eventos, leads, gasto e receita no período." /><div className="mt-4 space-y-3">{(summary?.timeTrend ?? []).map((point) => <div key={point.date} className="grid grid-cols-[76px_1fr_auto] items-center gap-3 text-2xs"><span>{new Date(`${point.date}T12:00:00`).toLocaleDateString('pt-BR')}</span><KlmProgress value={point.events / maxTrendEvents * 100} tone={point.clients > 0 ? 'green' : 'gold'} /><span className="text-right"><strong>{point.events} eventos</strong><small className="block text-muted-foreground">{point.leads} leads · {formatBRL(point.attributableRevenue)}</small></span></div>)}{!summary?.timeTrend.length && <p className="text-2xs text-muted-foreground">Nenhum ponto temporal no período.</p>}</div></KlmPanel></div>
          <div className="min-w-0 space-y-4"><KlmPanel tone="black" className="p-5"><Sparkles className="h-5 w-5 text-primary" /><h2 className="mt-4 text-lg font-semibold">Recomendação de verba</h2><p className="mt-2 text-2xs leading-5 text-white/60">{bestChannel ? `${bestChannel.name} possui o melhor sinal atual (${bestChannel.signal}). ${weakestChannel && weakestChannel.channel !== bestChannel.channel ? `${weakestChannel.name} exige revisão antes de escalar.` : ''}` : 'Ainda não há dados suficientes para recomendar redistribuição.'}</p><div className="mt-4 space-y-3">{channels.slice(0, 6).map((channel) => <div key={channel.channel}><div className="mb-1 flex justify-between text-2xs"><span className="text-white/55">{channel.name}</span><strong>{channel.signal}</strong></div><KlmProgress value={channel.score} tone={channel.score < 50 ? 'red' : 'gold'} /></div>)}</div><div className="mt-5"><PermissionGate module="integrations" action="manage" actionLabel="registrar intenção de orçamento" className="block w-full"><Button disabled={!channels.length || intentMutation.isPending} onClick={applyBudget} className="w-full text-xs">{intentMutation.isPending ? 'Registrando intenção' : 'Registrar intenção de orçamento'}</Button></PermissionGate></div></KlmPanel><KlmPanel className="p-4"><KlmSectionTitle title="Qualidade por origem" description="Score derivado de conversão e retorno." /><div className="mt-4 space-y-3">{channels.slice(0, 6).map((channel) => <div key={channel.channel}><div className="mb-1.5 flex justify-between text-2xs"><span>{channel.name}</span><strong>{channel.score}</strong></div><KlmProgress value={channel.score} tone={channel.score >= 85 ? 'green' : channel.score < 50 ? 'red' : 'gold'} /></div>)}</div></KlmPanel></div>
        </div></TabsContent>

        <TabsContent value="atribuicao" className="space-y-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <KlmMetricCard label="Cobertura das conversas" value={summary?.attributionCoverage.trackingRate != null ? `${formatDecimal(summary.attributionCoverage.trackingRate)}%` : '—'} delta={`${summary?.attributionCoverage.untrackedConversations ?? 0} sem origem`} trend={(summary?.attributionCoverage.trackingRate ?? 0) >= 90 ? 'up' : 'down'} />
            <KlmMetricCard label="Hierarquia completa" value={String(summary?.attributionCoverage.fullHierarchyTouches ?? 0)} delta={`${summary?.attributionCoverage.partialHierarchyTouches ?? 0} toques parciais`} trend={(summary?.attributionCoverage.fullHierarchyTouches ?? 0) > 0 ? 'up' : 'neutral'} />
            <KlmMetricCard label="Conversões atribuídas" value={String(summary?.attributionCoverage.attributedConversions ?? 0)} delta={`${summary?.attributionCoverage.unattributedConversions ?? 0} sem atribuição`} trend={(summary?.attributionCoverage.unattributedConversions ?? 0) ? 'down' : 'up'} />
            <KlmMetricCard label="Elegíveis para feedback" value={String(summary?.attributionCoverage.feedbackEligibleConversions ?? 0)} delta={`${summary?.attributionCoverage.feedbackBlockedNoEvidence ?? 0} bloqueadas por evidência`} trend={(summary?.attributionCoverage.feedbackBlockedNoEvidence ?? 0) ? 'down' : 'up'} />
          </div>
          <KlmPanel className="p-4">
            <KlmSectionTitle title="Campanha, conjunto, anúncio e criativo" description="A hierarquia é exibida no nível comprovado; nomes não são usados como substitutos silenciosos de IDs." action={<Button variant="outline" size="sm" className="h-8 text-2xs" onClick={exportAttributionCsv} disabled={!summary?.attributionRows.length}><Download className="mr-1.5 h-3 w-3" />Exportar CSV</Button>} />
            <div className="mt-4 grid gap-x-6 gap-y-1 border-y py-3 lg:grid-cols-2">{topAttributionRows.map((row, index) => <div key={`performance-${row.provider}-${row.campaignId}-${row.adId}-${row.creativeId}-${index}`} className="grid grid-cols-[minmax(0,1fr)_110px_auto] items-center gap-3 border-b py-3 last:border-0 lg:[&:nth-last-child(-n+2)]:border-0"><span className="min-w-0"><strong className="block truncate text-2xs" title={row.campaignName ?? undefined}>{row.campaignName ?? row.campaignId ?? eventLabel(row.channel)}</strong><small className="mt-1 block truncate text-2xs text-muted-foreground" title={row.creativeName ?? row.adName ?? undefined}>{row.creativeName ?? row.adName ?? 'Criativo não identificado'} · {row.clients} cliente(s)</small></span><span><strong className="mb-1 block text-right text-2xs">{formatBRL(row.attributableRevenue)}</strong><KlmProgress value={row.attributableRevenue / maxAttributionRevenue * 100} tone={row.clients > 0 ? 'green' : 'gold'} /></span><span className="flex gap-1">{row.sampleThreadId && <Button size="icon" variant="ghost" className="h-7 w-7" onClick={() => navigate(`/app/conversas?thread=${row.sampleThreadId}${row.sampleMatterId ? `&matter=${row.sampleMatterId}` : ''}`)} title="Abrir conversa atribuída"><ExternalLink className="h-3 w-3" /></Button>}{row.providerConsoleUrl && <Button size="icon" variant="ghost" className="h-7 w-7" asChild title="Abrir campanha oficial"><a href={row.providerConsoleUrl} target="_blank" rel="noreferrer"><BarChart3 className="h-3 w-3" /></a></Button>}</span></div>)}{!topAttributionRows.length && <p className="col-span-full py-4 text-center text-2xs text-muted-foreground">O ranking visual aparecerá com campanhas atribuídas.</p>}</div>
            <div className="mt-4 overflow-x-auto rounded-lg border">
              <table className="w-full min-w-[1480px] text-left text-2xs">
                <thead><tr className="border-b bg-secondary text-muted-foreground"><th className="p-3">Origem</th><th className="p-3">Conta</th><th className="p-3">Campanha</th><th className="p-3">Conjunto</th><th className="p-3">Anúncio</th><th className="p-3">Criativo / formulário</th><th className="p-3">Evidência</th><th className="p-3">Resultados</th><th className="p-3">Retorno</th><th className="p-3">Acesso</th></tr></thead>
                <tbody>{(summary?.attributionRows ?? []).map((row, index) => <tr key={`${row.provider}-${row.campaignId}-${row.adSetId}-${row.adId}-${row.creativeId}-${index}`} className="border-b align-top last:border-0 hover:bg-secondary"><td className="p-3"><strong className="block">{row.providerLabel}</strong><small className="mt-1 block text-muted-foreground">{eventLabel(row.channel)} · {row.source ?? 'fonte não nomeada'}{row.placement ? ` · ${row.placement}` : ''}</small></td><td className="p-3"><strong className="block max-w-[150px] truncate">{row.accountName ?? '—'}</strong><code className="mt-1 block max-w-[150px] truncate text-2xs text-muted-foreground">{row.accountId ?? 'sem ID'}</code></td><td className="p-3"><strong className="block max-w-[190px] truncate" title={row.campaignName ?? undefined}>{row.campaignName ?? '—'}</strong><code className="mt-1 block max-w-[190px] truncate text-2xs text-muted-foreground">{row.campaignId ?? 'sem ID'}</code></td><td className="p-3"><strong className="block max-w-[170px] truncate" title={row.adSetName ?? undefined}>{row.adSetName ?? '—'}</strong><code className="mt-1 block max-w-[170px] truncate text-2xs text-muted-foreground">{row.adSetId ?? 'sem ID'}</code></td><td className="p-3"><strong className="block max-w-[170px] truncate" title={row.adName ?? undefined}>{row.adName ?? '—'}</strong><code className="mt-1 block max-w-[170px] truncate text-2xs text-muted-foreground">{row.adId ?? 'sem ID'}</code></td><td className="p-3"><strong className="block max-w-[170px] truncate" title={row.creativeName ?? row.formName ?? undefined}>{row.creativeName ?? row.formName ?? '—'}</strong><code className="mt-1 block max-w-[170px] truncate text-2xs text-muted-foreground">{row.creativeId ?? row.formId ?? 'sem ID'}</code></td><td className="p-3"><div className="flex items-center gap-2"><KlmPill tone={row.evidenceLevel === 'exact' ? 'green' : row.evidenceLevel === 'named' ? 'gold' : 'red'}>{row.evidenceLevel === 'exact' ? 'Exata' : row.evidenceLevel === 'named' ? 'Nominal' : 'Sem atribuição'}</KlmPill><strong>{row.hierarchyCompleteness}%</strong></div><KlmProgress value={row.hierarchyCompleteness} tone={row.hierarchyCompleteness === 100 ? 'green' : row.evidenceLevel === 'unattributed' ? 'red' : 'gold'} /><small className="mt-1 block max-w-[220px] text-muted-foreground">{row.evidenceReason}{row.missingLevels.length ? ` · faltam ${row.missingLevels.join(', ')}` : ''}</small></td><td className="p-3 whitespace-nowrap"><strong>{row.leads} leads</strong><small className="block text-muted-foreground">{row.qualifiedLeads} qualif. · {row.clients} clientes</small></td><td className="p-3 whitespace-nowrap"><strong>{formatBRL(row.attributableRevenue)}</strong><small className="block text-muted-foreground">CAC {row.cac != null ? formatBRL(row.cac) : '—'} · ROAS {row.roas != null ? `${formatDecimal(row.roas)}x` : '—'}</small></td><td className="p-3"><div className="flex flex-wrap gap-1.5"><Button size="icon" variant="outline" className="h-7 w-7" onClick={() => setSelectedAttribution(row)} title="Ver todos os dados de origem"><Search className="h-3 w-3" /></Button>{row.sampleThreadId && <Button size="icon" variant="outline" className="h-7 w-7" onClick={() => navigate(`/app/conversas?thread=${row.sampleThreadId}${row.sampleMatterId ? `&matter=${row.sampleMatterId}` : ''}`)} title="Abrir conversa de evidência"><ExternalLink className="h-3 w-3" /></Button>}{row.providerConsoleUrl && <Button size="icon" variant="outline" className="h-7 w-7" asChild title="Abrir no gerenciador oficial"><a href={row.providerConsoleUrl} target="_blank" rel="noreferrer"><BarChart3 className="h-3 w-3" /></a></Button>}</div></td></tr>)}</tbody>
              </table>
              {!trackingQuery.isPending && !summary?.attributionRows.length && <div className="p-10 text-center text-2xs text-muted-foreground">Ainda não há toques atribuíveis no período.</div>}
            </div>
          </KlmPanel>
        </TabsContent>

        <TabsContent value="captacao"><AcquisitionIntegrationsPanel canManageIntegrations={canManageIntegrations} onNavigate={embedded ? onNavigate : undefined} /></TabsContent>

        <TabsContent value="eventos"><div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_330px]">
          <KlmPanel className="p-4"><KlmSectionTitle title="Saúde dos eventos" description="Cobertura calculada pelos nomes normalizados." action={<KlmPill tone={(summary?.eventCoverage ?? []).every((item) => item.covered) ? 'green' : 'gold'}>{(summary?.eventCoverage ?? []).filter((item) => item.covered).length}/{summary?.eventCoverage.length ?? 0} cobertos</KlmPill>} /><div className="mt-4 divide-y rounded-lg border">{(summary?.eventCoverage ?? []).map((item) => { const maxCount = Math.max(1, ...(summary?.eventCoverage ?? []).map((event) => event.count)); const coverage = item.count / maxCount * 100; return <div key={item.event} className="grid gap-3 p-4 sm:grid-cols-[150px_1fr_120px_auto] sm:items-center"><span><strong className="block text-xs">{eventLabel(item.event)}</strong><span className="mt-1 block text-2xs text-muted-foreground">contrato normalizado</span></span><span className="text-2xs leading-4 text-muted-foreground">{item.count} evento(s) persistido(s) neste período.</span><span><div className="mb-1 flex justify-between text-2xs"><span>Volume relativo</span><strong>{Math.round(coverage)}%</strong></div><KlmProgress value={coverage} tone={item.covered ? 'green' : 'red'} /></span><KlmPill tone={item.covered ? 'green' : 'red'}>{item.covered ? 'Presente' : 'Ausente'}</KlmPill></div>; })}</div></KlmPanel>
          <div className="space-y-4"><KlmPanel tone="black" className="p-5"><FileInput className="h-5 w-5 text-primary" /><h2 className="mt-4 text-lg font-semibold">Conexões de aquisição</h2><p className="mt-2 text-2xs leading-4 text-white/55">Fontes, formulários, recibos e rotas de abordagem são mostrados a partir do registro nativo.</p><Button variant="outline" onClick={() => updateSearchParams({ tab: 'captacao' })} className="mt-4 w-full border-white/20 bg-transparent text-xs text-white hover:bg-white/10 hover:text-white">Abrir central de captação</Button></KlmPanel><KlmPanel className="p-4"><div className="flex items-start gap-3"><ShieldCheck className="mt-0.5 h-5 w-5 text-[#0057d8]" /><div><h3 className="text-sm font-semibold">Deduplicação e identidade</h3><p className="mt-1 text-2xs leading-4 text-muted-foreground">{summary?.identifiedJourneys ?? 0} jornada(s) identificada(s); valores sem semântica financeira foram ignorados em {summary?.ignoredValueEvents ?? 0} evento(s).</p></div></div></KlmPanel><KlmPanel className="p-4"><div className="flex items-start gap-3"><History className="mt-0.5 h-5 w-5 text-[#0057d8]" /><div><h3 className="text-sm font-semibold">Histórico de anúncios (CTWA)</h3><p className="mt-1 text-2xs leading-4 text-muted-foreground">Mensagens de WhatsApp recebidas antes da captura automática de anúncios podem ser recuperadas retroativamente, sem apagar nenhuma evidência.</p></div></div><PermissionGate module="admin" action="manage" actionLabel="recuperar histórico de anúncios (CTWA)" className="mt-4 block w-full"><Button variant="outline" onClick={openCtwaBackfillDialog} className="w-full text-xs">Recuperar histórico de anúncios (CTWA)</Button></PermissionGate></KlmPanel></div>
        </div></TabsContent>

        <TabsContent value="funil">
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
            <KlmPanel className="p-4">
              <KlmSectionTitle title="Funil da jornada atribuída" description="Percentuais calculados por identidade e eventos persistidos, com queda entre etapas consecutivas." />
              <div className="mt-5 space-y-3">{(summary?.funnelSteps ?? []).map((step, index) => <div key={step.stage} className="rounded-lg border p-4"><div className="flex flex-wrap items-start justify-between gap-3"><span><strong className="block text-xs">{eventLabel(step.stage)}</strong><small className="mt-1 block text-2xs text-muted-foreground">{step.journeys} jornada(s) · {step.shareOfStart != null ? `${formatDecimal(step.shareOfStart)}% da entrada` : 'base inicial'}</small></span><span className="flex gap-2"><KlmPill tone={step.dropOffFromPrevious > 0 ? 'gold' : 'green'}>{index === 0 ? 'Entrada' : `${step.conversionFromPrevious != null ? formatDecimal(step.conversionFromPrevious) : '—'}% avançou`}</KlmPill>{step.dropOffFromPrevious > 0 && <KlmPill tone="red">-{step.dropOffFromPrevious} · {formatDecimal(step.dropOffRate ?? 0)}%</KlmPill>}</span></div><div className="mt-3"><KlmProgress value={step.shareOfStart ?? 100} tone={step.dropOffRate != null && step.dropOffRate > 50 ? 'red' : step.dropOffRate != null && step.dropOffRate > 20 ? 'gold' : 'green'} /></div></div>)}{!summary?.funnelSteps.length && <div className="p-10 text-center text-2xs text-muted-foreground">O funil aparecerá quando houver jornadas identificadas.</div>}</div>
            </KlmPanel>
            <KlmPanel tone="black" className="p-5"><Target className="h-5 w-5 text-primary" /><h2 className="mt-4 text-lg font-semibold">Leitura do gargalo</h2><p className="mt-2 text-2xs leading-5 text-white/60">O maior abandono é calculado pela progressão real da jornada. Use os links de campanha e conversa para comparar origem, atendimento, objeções e prazo de resposta.</p><div className="mt-5 space-y-3">{(summary?.insights ?? []).filter((insight) => insight.key === 'journey_bottleneck').map((insight) => <div key={insight.key} className="rounded-md border border-white/10 p-3 text-2xs"><strong className="block text-white">{insight.title}</strong><p className="mt-1 leading-4 text-white/55">{insight.detail}</p><p className="mt-2 text-primary">{insight.recommendedAction}</p></div>)}</div><Button variant="outline" onClick={() => updateSearchParams({ tab: 'atribuicao' })} className="mt-5 w-full border-white/20 bg-transparent text-xs text-white hover:bg-white/10 hover:text-white">Cruzar com campanhas</Button></KlmPanel>
          </div>
        </TabsContent>

        <TabsContent value="jornadas"><KlmPanel className="p-4"><KlmSectionTitle title="Jornadas atribuídas" description="Caminhos anônimos derivados de click IDs e vínculos persistidos." action={<KlmPill tone="green">{summary?.identifiedJourneys ?? 0} identificadas</KlmPill>} /><div className="mt-4 space-y-3">{(summary?.journeys ?? []).map((journey) => <div key={journey.journeyId} className="rounded-lg border p-4"><div className="flex flex-wrap items-center justify-between gap-2"><div><strong className="text-xs">Jornada {journey.journeyId.slice(0, 10)}</strong><span className="ml-2 text-2xs text-muted-foreground">identidade {eventLabel(journey.identityType)}</span></div><div className="flex gap-2"><KlmPill tone={journey.converted ? 'green' : 'gold'}>{journey.converted ? 'Convertida' : 'Em aberto'}</KlmPill><KlmPill tone="green">{formatBRL(journey.attributableRevenue)}</KlmPill></div></div><div className="mt-4 flex items-center gap-1.5 overflow-x-auto pb-1">{journey.eventPath.map((step, index) => <div key={`${step}-${index}`} className="flex shrink-0 items-center gap-1.5"><span className="rounded-md border bg-secondary px-3 py-2 text-2xs font-semibold">{eventLabel(step)}</span>{index < journey.eventPath.length - 1 && <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />}</div>)}</div><p className="mt-3 text-2xs text-muted-foreground">{journey.channels.map(eventLabel).join(' · ')} · última atividade {new Date(journey.lastActivityAt).toLocaleString('pt-BR')}</p></div>)}{!summary?.journeys.length && <div className="p-8 text-center text-2xs text-muted-foreground">Nenhuma jornada identificável no período.</div>}</div></KlmPanel></TabsContent>

        <TabsContent value="qualidade" className="space-y-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-7">
            <KlmMetricCard label="Score de qualidade" value={`${summary?.serviceQuality.qualityScore ?? 100}%`} delta={`${summary?.serviceQuality.auditedThreads ?? 0} conversas auditadas`} trend={(summary?.serviceQuality.qualityScore ?? 100) >= 90 ? 'up' : 'down'} />
            <KlmMetricCard label="Achados ativos" value={String(summary?.serviceQuality.activeFindings ?? 0)} delta={`${summary?.serviceQuality.criticalFindings ?? 0} críticos`} trend={(summary?.serviceQuality.activeFindings ?? 0) ? 'down' : 'up'} />
            <KlmMetricCard label="Sem resposta" value={String(summary?.serviceQuality.unansweredLeads ?? 0)} delta="leads aguardando" trend={(summary?.serviceQuality.unansweredLeads ?? 0) ? 'down' : 'up'} />
            <KlmMetricCard label="Entrega divergente" value={String(summary?.serviceQuality.deliveryMismatches ?? 0)} delta="tela x provedor" trend={(summary?.serviceQuality.deliveryMismatches ?? 0) ? 'down' : 'up'} />
            <KlmMetricCard label="Duplicidade" value={String(summary?.serviceQuality.duplicateOutbound ?? 0)} delta="saídas repetidas" trend={(summary?.serviceQuality.duplicateOutbound ?? 0) ? 'down' : 'up'} />
            <KlmMetricCard label="Resolvidos" value={String(summary?.serviceQuality.resolvedFindings ?? 0)} delta="achados encerrados" trend="up" />
            <KlmMetricCard label="Cobertura track" value={summary?.attributionCoverage.trackingRate != null ? `${formatDecimal(summary.attributionCoverage.trackingRate)}%` : '—'} delta="origem comprovada" trend={(summary?.attributionCoverage.trackingRate ?? 0) >= 90 ? 'up' : 'down'} />
          </div>
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
            <KlmPanel className="p-4"><KlmSectionTitle title="Insights acionáveis" description="Somente alertas derivados de métricas e evidências desta base." /><div className="mt-4 divide-y rounded-lg border">{(summary?.insights ?? []).map((insight) => <div key={insight.key} className="grid gap-3 p-4 sm:grid-cols-[32px_1fr_auto]"><span className={`grid h-8 w-8 place-items-center rounded-full ${insight.severity === 'critical' ? 'bg-red-100 text-red-700 dark:bg-red-950' : insight.severity === 'warning' ? 'bg-amber-100 text-amber-800 dark:bg-amber-950' : 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950'}`}>{insight.severity === 'critical' ? <ShieldAlert className="h-4 w-4" /> : <Sparkles className="h-4 w-4" />}</span><span><strong className="block text-xs">{insight.title}</strong><p className="mt-1 text-2xs leading-4 text-muted-foreground">{insight.detail}</p><p className="mt-2 text-2xs font-semibold text-[#0057d8]">{insight.recommendedAction}</p></span><KlmPill tone={insight.severity === 'critical' ? 'red' : insight.severity === 'warning' ? 'gold' : 'green'}>{insight.severity === 'critical' ? 'Crítico' : insight.severity === 'warning' ? 'Atenção' : 'Oportunidade'}</KlmPill></div>)}{!summary?.insights.length && <div className="p-10 text-center text-2xs text-muted-foreground">Nenhum alerta derivado no período.</div>}</div></KlmPanel>
            <KlmPanel tone="black" className="p-5"><ShieldCheck className="h-5 w-5 text-primary" /><h2 className="mt-4 text-lg font-semibold">Auditor independente</h2><p className="mt-2 text-2xs leading-5 text-white/60">Confere lead sem resposta, falha de entrega, divergência entre projeção e provedor e duplicidade de saída. O rastreamento não premia uma campanha quando o atendimento falhou.</p><Button onClick={() => navigate('/app/qualidade')} className="mt-5 w-full text-xs">Abrir central de qualidade</Button></KlmPanel>
          </div>
        </TabsContent>

        <TabsContent value="feedback" className="space-y-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-6">
            <KlmMetricCard label="Conversões lidas" value={String(summary?.attributionCoverage.conversionEvents ?? 0)} delta="eventos comerciais" />
            <KlmMetricCard label="Preparadas" value={String(summary?.conversionFeedback.prepared ?? 0)} delta="com event ID estável" trend="up" />
            <KlmMetricCard label="Bloqueadas localmente" value={String(summary?.conversionFeedback.blockedExternal ?? 0)} delta="nenhuma API chamada" />
            <KlmMetricCard label="Aguardando aprovação" value={String(summary?.conversionFeedback.awaitingApproval ?? 0)} delta="gate externo" />
            <KlmMetricCard label="Sem evidência" value={String(summary?.attributionCoverage.feedbackBlockedNoEvidence ?? 0)} delta="não serão enviadas" trend={(summary?.attributionCoverage.feedbackBlockedNoEvidence ?? 0) ? 'down' : 'up'} />
            <KlmMetricCard label="Ações externas" value={String((summary?.feedbackEvents ?? []).reduce((total, event) => total + event.externalActionsExecuted, 0))} delta="deve ficar zero localmente" trend="neutral" />
            <KlmMetricCard label="Payloads prontos" value={String(summary?.conversionFeedback.payloadReady ?? 0)} delta="contrato + destino completos" trend={(summary?.conversionFeedback.payloadReady ?? 0) ? 'up' : 'neutral'} />
            <KlmMetricCard label="Provas imutáveis" value={String(summary?.conversionFeedback.preparedProofs ?? 0)} delta="hash SHA-256 persistido" trend={(summary?.conversionFeedback.preparedProofs ?? 0) === (summary?.conversionFeedback.prepared ?? 0) ? 'up' : 'down'} />
            <KlmMetricCard label="Repetições bloqueadas" value={String(summary?.conversionFeedback.duplicatePrevented ?? 0)} delta="nenhum reenvio criado" trend="up" />
            <KlmMetricCard label="Vendas consolidadas" value={String(summary?.attributionCoverage.businessDuplicatesSuppressed ?? 0)} delta="purchase + pagamento sem duplicar" trend="up" />
            <KlmMetricCard label="Recibos oficiais" value={String(summary?.conversionFeedback.providerReceipts ?? 0)} delta={`${summary?.conversionFeedback.failedReceipts ?? 0} com falha`} trend={(summary?.conversionFeedback.failedReceipts ?? 0) ? 'down' : 'neutral'} />
          </div>
          <KlmPanel className="p-4">
            <KlmSectionTitle title="Fila de feedback aos provedores" description="Cada provedor recebe contrato próprio; preparação, tentativa e recibo são estados separados e auditáveis." action={<div className="flex flex-wrap gap-2"><Button className="h-8 bg-[#031121] text-2xs text-white hover:bg-[#031121]" onClick={() => feedbackMutation.mutate()} disabled={!canManageIntegrations || feedbackMutation.isPending}>{feedbackMutation.isPending ? <LoaderCircle className="mr-1.5 h-3 w-3 animate-spin" /> : <Send className="mr-1.5 h-3 w-3" />}Preparar feedback local</Button><Button variant="outline" className="h-8 text-2xs" onClick={() => dispatchFeedbackMutation.mutate()} disabled={!canManageIntegrations || dispatchFeedbackMutation.isPending} title="Consome as conversões preparadas e grava recibo (simulado enquanto o gate externo estiver fechado)">{dispatchFeedbackMutation.isPending ? <LoaderCircle className="mr-1.5 h-3 w-3 animate-spin" /> : <Send className="mr-1.5 h-3 w-3" />}Despachar conversões preparadas</Button></div>} />
            <div className="mt-4 flex flex-wrap items-center gap-2 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-2xs text-emerald-950 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100"><ShieldCheck className="h-4 w-4" /><strong>Limite de dados:</strong><span>somente IDs opacos validados, contato em SHA-256, evento e valor, com consentimento individual comprovado. CPF, caso, saúde, documentos, conversa e notas internas não são mapeados.</span></div>
            <div className="mt-4 overflow-x-auto rounded-lg border"><table className="w-full min-w-[1480px] text-left text-2xs"><thead><tr className="border-b bg-secondary text-muted-foreground"><th className="p-3">Conversão</th><th className="p-3">Provedor</th><th className="p-3">Valor</th><th className="p-3">Correspondência</th><th className="p-3">Event ID / order ID</th><th className="p-3">Configuração</th><th className="p-3">Prova e recibo</th><th className="p-3">Estado local</th><th className="p-3">Deduplicação</th></tr></thead><tbody>{(summary?.feedbackEvents ?? []).map((event) => <tr key={event.intentId} className="border-b last:border-0 hover:bg-secondary"><td className="p-3"><strong className="block">{eventLabel(event.conversionEvent)}</strong><small className="text-muted-foreground">{new Date(event.happenedAt).toLocaleString('pt-BR')}</small></td><td className="p-3"><strong>{eventLabel(event.provider)}</strong><small className="mt-1 block text-muted-foreground">{event.payloadSchemaVersion || 'contrato legado'}</small></td><td className="p-3">{event.value != null ? formatBRL(event.value) : '—'}</td><td className="p-3"><div className="flex max-w-[240px] flex-wrap gap-1">{event.matchKeyNames.map((key) => <KlmPill key={key} tone="blue">{key}</KlmPill>)}{!event.matchKeyNames.length && <KlmPill tone="red">sem match key</KlmPill>}</div><small className="mt-1 block text-muted-foreground">{eventLabel(event.matchingMode)}</small></td><td className="p-3"><code>{event.providerEventId}</code></td><td className="p-3"><KlmPill tone={event.readyForDispatch ? 'green' : 'gold'}>{event.readyForDispatch ? 'Contrato completo' : 'Configuração pendente'}</KlmPill><small className="mt-1 block max-w-[220px] text-muted-foreground">{event.missingConfiguration.length ? event.missingConfiguration.map(eventLabel).join(' · ') : 'sem pendências locais'}</small></td><td className="p-3"><KlmPill tone={event.proofState === 'prepared_local' ? 'green' : 'red'}>{eventLabel(event.proofState)}</KlmPill><small className="mt-1 block text-muted-foreground">{event.receiptState ? `recibo: ${eventLabel(event.receiptState)}` : 'sem recibo oficial'}</small><code className="mt-1 block max-w-[180px] truncate text-2xs" title={event.payloadSha256}>{event.payloadSha256 || 'sem hash'}</code></td><td className="p-3"><KlmPill tone={event.status === 'blocked_external' ? 'muted' : event.status === 'awaiting_approval' ? 'gold' : 'green'}>{eventLabel(event.status)}</KlmPill><small className="mt-1 block text-muted-foreground">{event.externalActionsExecuted} chamada(s)</small></td><td className="p-3"><code className="block max-w-[210px] truncate" title={event.dedupeKey}>{event.dedupeKey}</code></td></tr>)}</tbody></table>{!summary?.feedbackEvents.length && <div className="p-10 text-center text-2xs text-muted-foreground">Nenhum feedback preparado. Conversões sem evidência permanecem bloqueadas.</div>}</div>
          </KlmPanel>
        </TabsContent>

        <TabsContent value="remarketing"><div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_320px]"><KlmPanel className="p-4"><KlmSectionTitle title="Oportunidades de remarketing" description="Pessoas identificadas que avançaram sem converter." action={<KlmPill tone="gold">{summary?.remarketing.length ?? 0} segmentos</KlmPill>} /><div className="mt-4 divide-y rounded-lg border">{(summary?.remarketing ?? []).map((item) => <div key={`${item.stage}-${item.channel}`} className="grid gap-3 p-4 sm:grid-cols-[1fr_90px_130px_1fr_auto] sm:items-center"><span><strong className="block text-2xs">Parou em {eventLabel(item.stage)}</strong><span className="mt-1 block text-2xs text-muted-foreground">atividade {new Date(item.lastActivityAt).toLocaleDateString('pt-BR')}</span></span><strong className="text-sm">{item.audienceSize}</strong><KlmPill tone="blue">{eventLabel(item.channel)}</KlmPill><span className="text-2xs leading-4 text-muted-foreground">Reengajar no canal de origem sem misturar identidades.</span><Button size="icon" variant="ghost" disabled={!canManageIntegrations || intentMutation.isPending} onClick={() => intentMutation.mutate({ label: `Intenção de audiência ${eventLabel(item.stage)}`, actionType: 'remarketing_audience_create', provider: item.channel, payload: item })} className="h-8 w-8" title={!canManageIntegrations ? 'Permissão integrations.manage necessária' : 'Registrar intenção local de audiência'}><ChevronRight className="h-4 w-4" /></Button></div>)}{!summary?.remarketing.length && <div className="p-8 text-center text-2xs text-muted-foreground">Nenhuma audiência elegível foi derivada dos eventos atuais.</div>}</div></KlmPanel><KlmPanel tone="black" className="p-5"><Target className="h-5 w-5 text-primary" /><h2 className="mt-4 text-lg font-semibold">Estratégia cruzada</h2><p className="mt-2 text-2xs leading-5 text-white/60">As audiências abaixo são calculadas localmente. A sincronização externa permanece sob aprovação e credenciais.</p><div className="mt-5 space-y-2">{(summary?.remarketing ?? []).map((item) => <div key={`${item.channel}-${item.stage}`} className="flex items-center gap-2 rounded-md border border-white/10 p-2.5 text-2xs"><Link2 className="h-3.5 w-3.5 text-primary" />{eventLabel(item.channel)} · {eventLabel(item.stage)} · {item.audienceSize}</div>)}</div><Button disabled={!canManageIntegrations || !summary?.remarketing.length || intentMutation.isPending} title={!canManageIntegrations ? 'Permissão integrations.manage necessária' : 'Registrar intenção local de sincronização'} onClick={syncAudiences} className="mt-5 w-full text-xs">{intentMutation.isPending ? 'Registrando intenção' : 'Registrar intenção de sincronização'}</Button></KlmPanel></div></TabsContent>
      </Tabs>
      <OperationalCopilot
        domain="tracking"
        title="Copiloto de Rastreamento"
        subtitle="Atribuição, qualidade e feedback"
        welcome="Peça uma leitura por período, canal, campanha ou gargalo. Eu organizo a visão e preparo ações locais para sua aprovação."
        placeholder="Ex.: compare os últimos 30 dias e prepare o feedback das vendas atribuídas."
        contextKey={`tracking:${period}:${activeTab}`}
        contextSnapshot={copilotContext}
        workspaceId={appStore?.state?.activeWorkspace?.id}
        userId={appStore?.state?.currentUser?.id}
        canApply={appStore?.can('relatorios', 'view') ?? true}
        onApply={applyCopilotActions}
      />
      <Dialog open={Boolean(selectedMetric)} onOpenChange={(open) => { if (!open) setSelectedMetric(null); }}>
        <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
          <DialogHeader><DialogTitle>{selectedMetric} por canal e campanha</DialogTitle><DialogDescription>Composição do indicador usando somente canais e atribuições persistidos no período selecionado.</DialogDescription></DialogHeader>
          {selectedMetric && <div className="space-y-5">
            <div className="space-y-3">
              {channels.map((channel) => {
                const metric = metricValueForChannel(channel, selectedMetric);
                const maxValue = Math.max(1, ...channels.map((item) => metricValueForChannel(item, selectedMetric).value));
                return <button key={channel.channel} type="button" onClick={() => { setSelectedMetric(null); setExpandedChannel(channel.channel); updateSearchParams({ tab: 'canais', channel: null }); }} className="grid w-full grid-cols-[110px_minmax(0,1fr)_90px] items-center gap-3 text-left"><strong className="truncate text-2xs">{channel.name}</strong><span className="h-2 overflow-hidden rounded bg-secondary"><i className="block h-full bg-primary" style={{ width: `${metric.value / maxValue * 100}%` }} /></span><span className="text-right text-2xs font-semibold">{metric.display}</span></button>;
              })}
            </div>
            <div className="border-t pt-4"><div className="flex items-center justify-between gap-3"><strong className="text-xs">Campanhas que sustentam a atribuição</strong><KlmPill tone="blue">{summary?.attributionRows.length ?? 0} registros</KlmPill></div><div className="mt-3 divide-y rounded-md border">{(summary?.attributionRows ?? []).slice(0, 12).map((campaign, index) => <button key={`${campaign.channel}-${campaign.campaignId}-${index}`} type="button" onClick={() => { setSelectedMetric(null); setSelectedAttribution(campaign); }} className="grid w-full gap-2 p-3 text-left hover:bg-secondary sm:grid-cols-[minmax(0,1fr)_auto]"><span><strong className="block text-2xs">{campaign.campaignName ?? campaign.campaignId ?? eventLabel(campaign.channel)}</strong><small className="mt-1 block text-muted-foreground">{campaign.providerLabel} · {campaign.creativeName ?? campaign.adName ?? 'criativo não identificado'}</small></span><span className="text-right"><strong className="block text-2xs">{formatBRL(campaign.attributableRevenue)}</strong><small className="text-muted-foreground">{campaign.clients} cliente(s)</small></span></button>)}{!summary?.attributionRows.length && <p className="p-5 text-center text-2xs text-muted-foreground">Nenhuma campanha individual possui evidência suficiente neste período.</p>}</div></div>
          </div>}
        </DialogContent>
      </Dialog>
      <Dialog open={Boolean(selectedAttribution)} onOpenChange={(open) => { if (!open) setSelectedAttribution(null); }}>
        <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Detalhes completos da origem</DialogTitle>
            <DialogDescription>Registro local usado para atribuir a conversa. IDs e evidências permanecem visíveis mesmo quando o nome está incompleto.</DialogDescription>
          </DialogHeader>
          {selectedAttribution && <div className="space-y-4 text-xs">
            <div className="grid gap-3 sm:grid-cols-2">
              {[
                ['Provedor', selectedAttribution.providerLabel], ['Canal', eventLabel(selectedAttribution.channel)],
                ['Conta', selectedAttribution.accountName ?? '—'], ['ID da conta', selectedAttribution.accountId ?? '—'],
                ['Campanha', selectedAttribution.campaignName ?? '—'], ['ID da campanha', selectedAttribution.campaignId ?? '—'],
                ['Conjunto', selectedAttribution.adSetName ?? '—'], ['ID do conjunto', selectedAttribution.adSetId ?? '—'],
                ['Anúncio', selectedAttribution.adName ?? '—'], ['ID do anúncio', selectedAttribution.adId ?? '—'],
                ['Criativo', selectedAttribution.creativeName ?? '—'], ['ID do criativo', selectedAttribution.creativeId ?? '—'],
                ['Formulário', selectedAttribution.formName ?? '—'], ['ID do formulário', selectedAttribution.formId ?? '—'],
                ['Posicionamento', selectedAttribution.placement ?? '—'], ['Modelo', eventLabel(selectedAttribution.attributionModel)],
              ].map(([label, value]) => <div key={label} className="min-w-0 rounded-md border bg-secondary/40 p-3"><span className="block text-2xs text-muted-foreground">{label}</span><strong className="mt-1 block break-words font-mono text-xs">{value}</strong></div>)}
            </div>
            <div className="rounded-md border p-3"><div className="flex items-center justify-between gap-2"><strong>Evidência da atribuição</strong><KlmPill tone={selectedAttribution.evidenceLevel === 'exact' ? 'green' : selectedAttribution.evidenceLevel === 'named' ? 'gold' : 'red'}>{selectedAttribution.hierarchyCompleteness}% · {selectedAttribution.evidenceLevel === 'exact' ? 'Exata' : selectedAttribution.evidenceLevel === 'named' ? 'Nominal' : 'Sem atribuição'}</KlmPill></div><p className="mt-2 text-muted-foreground">{selectedAttribution.evidenceReason}</p>{selectedAttribution.missingLevels.length > 0 && <p className="mt-1 text-amber-700">Níveis faltantes: {selectedAttribution.missingLevels.join(', ')}</p>}<div className="mt-3 flex flex-wrap gap-1.5">{selectedAttribution.clickIdKeys.map((key) => <KlmPill key={key} tone="blue">{key}</KlmPill>)}{!selectedAttribution.clickIdKeys.length && <KlmPill tone="muted">Nenhum click ID registrado</KlmPill>}</div></div>
            <div className="grid gap-3 sm:grid-cols-2"><div className="rounded-md border p-3"><span className="text-2xs text-muted-foreground">URLs registradas</span><a className="mt-2 block break-all text-[#0057d8] underline" href={selectedAttribution.landingPageUrl ?? '#'} target="_blank" rel="noreferrer">{selectedAttribution.landingPageUrl ?? 'Landing page não registrada'}</a><a className="mt-2 block break-all text-[#0057d8] underline" href={selectedAttribution.sourceUrl ?? '#'} target="_blank" rel="noreferrer">{selectedAttribution.sourceUrl ?? 'Fonte não registrada'}</a></div><div className="rounded-md border p-3"><span className="text-2xs text-muted-foreground">Jornada persistida</span><p className="mt-2">{selectedAttribution.touchCount} toque(s) · {selectedAttribution.journeyCount} jornada(s)</p><p className="mt-1 text-muted-foreground">Primeiro toque: {new Date(selectedAttribution.firstTouchAt).toLocaleString('pt-BR')}</p><p className="mt-1 text-muted-foreground">Último toque: {new Date(selectedAttribution.lastTouchAt).toLocaleString('pt-BR')}</p></div></div>
          </div>}
        </DialogContent>
      </Dialog>
      <Dialog open={ctwaBackfillOpen} onOpenChange={(open) => { setCtwaBackfillOpen(open); if (!open) { setCtwaDryRunReport(null); setCtwaExecuteReport(null); } }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Recuperar histórico de anúncios (CTWA)</DialogTitle>
            <DialogDescription>Varre mensagens de WhatsApp já recebidas em busca de conversas que vieram de um clique em anúncio, sem apagar ou alterar nenhuma evidência.</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 text-xs">
            {ctwaDryRunMutation.isPending && (
              <div className="flex items-center gap-2 rounded-md border p-4 text-2xs text-muted-foreground"><LoaderCircle className="h-4 w-4 animate-spin" />Analisando o histórico local, sem gravar nada ainda…</div>
            )}
            {ctwaDryRunMutation.isError && (
              <div className="flex items-start gap-2 rounded-md border border-red-300 bg-red-50 p-4 text-2xs text-red-900 dark:border-red-900 dark:bg-red-950/30 dark:text-red-100">
                <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
                <div>
                  <strong className="block">Não foi possível analisar o histórico.</strong>
                  <span>{(ctwaDryRunMutation.error as Error)?.message ?? 'Tente novamente em instantes.'}</span>
                  <Button variant="outline" size="sm" className="mt-3 h-7 text-2xs" onClick={() => ctwaDryRunMutation.mutate()}>Tentar novamente</Button>
                </div>
              </div>
            )}
            {ctwaDryRunReport && !ctwaExecuteReport && (
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-2">
                  <div className="rounded-md border p-3"><span className="block text-xs text-muted-foreground">Mensagens varridas</span><strong className="mt-1 block text-2xl font-bold tabular-nums">{ctwaDryRunReport.scanned}</strong></div>
                  <div className="rounded-md border p-3"><span className="block text-xs text-muted-foreground">Vieram de anúncio</span><strong className="mt-1 block text-2xl font-bold tabular-nums">{ctwaDryRunReport.withReferral}</strong></div>
                  <div className="rounded-md border border-emerald-200 bg-emerald-50 p-3 dark:border-emerald-900 dark:bg-emerald-950/30"><span className="block text-xs text-muted-foreground">Recuperáveis agora</span><strong className="mt-1 block text-2xl font-bold tabular-nums text-emerald-700 dark:text-emerald-300">{ctwaDryRunReport.eventsCreated}</strong></div>
                  <div className="rounded-md border p-3"><span className="block text-xs text-muted-foreground">Já recuperados / sem thread</span><strong className="mt-1 block text-2xl font-bold tabular-nums">{ctwaDryRunReport.skippedExisting + ctwaDryRunReport.unresolved}</strong></div>
                </div>
                {ctwaDryRunReport.eventsCreated === 0 ? (
                  <div className="rounded-md border p-3 text-2xs text-muted-foreground">Nada para recuperar: ou não há anúncios no histórico, ou já foram todos recuperados anteriormente.</div>
                ) : (
                  <div className="flex items-center justify-between gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-2xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
                    <span>Confirme para gravar {ctwaDryRunReport.eventsCreated} evento(s) de aquisição. Rodar de novo depois não duplica nada.</span>
                  </div>
                )}
              </div>
            )}
            {ctwaExecuteMutation.isPending && (
              <div className="flex items-center gap-2 rounded-md border p-4 text-2xs text-muted-foreground"><LoaderCircle className="h-4 w-4 animate-spin" />Gravando os eventos recuperados…</div>
            )}
            {ctwaExecuteMutation.isError && (
              <div className="flex items-start gap-2 rounded-md border border-red-300 bg-red-50 p-4 text-2xs text-red-900 dark:border-red-900 dark:bg-red-950/30 dark:text-red-100">
                <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
                <div>
                  <strong className="block">Não foi possível gravar o histórico recuperado.</strong>
                  <span>{(ctwaExecuteMutation.error as Error)?.message ?? 'Tente novamente em instantes.'}</span>
                </div>
              </div>
            )}
            {ctwaExecuteReport && (
              <div className="flex items-start gap-2 rounded-md border border-emerald-200 bg-emerald-50 p-4 text-2xs text-emerald-950 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100">
                <Check className="mt-0.5 h-4 w-4 shrink-0" />
                <span><strong>{ctwaExecuteReport.eventsCreated}</strong> evento(s) de aquisição recuperado(s) e vinculado(s) às conversas. {ctwaExecuteReport.unresolved > 0 && `${ctwaExecuteReport.unresolved} mensagem(ns) com anúncio não puderam ser vinculadas a uma conversa.`}</span>
              </div>
            )}
          </div>
          <div className="mt-2 flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setCtwaBackfillOpen(false)}>Fechar</Button>
            {ctwaDryRunReport && !ctwaExecuteReport && ctwaDryRunReport.eventsCreated > 0 && (
              <Button size="sm" onClick={() => ctwaExecuteMutation.mutate()} disabled={ctwaExecuteMutation.isPending}>
                {ctwaExecuteMutation.isPending ? <LoaderCircle className="mr-1.5 h-3 w-3 animate-spin" /> : null}
                Confirmar e recuperar {ctwaDryRunReport.eventsCreated} evento(s)
              </Button>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
