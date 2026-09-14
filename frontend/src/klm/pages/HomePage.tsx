import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, CalendarClock, CalendarDays, CheckCircle2, Clock3, Download, FileText, Lightbulb, LoaderCircle, MapPin, Megaphone, MessageSquareText, PauseCircle, ShieldAlert, TrendingDown, TrendingUp, Video, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { KlmMetricCard, KlmPanel, KlmPill, KlmProgress, KlmSectionTitle } from '@/klm/components/Primitives';
import { buildExecutiveReportHtml, openExecutiveReport } from '@/klm/components/executive-report';
import { klmApi, type KlmTrackingSummary } from '@/klm/api';
import { cn } from '@/lib/utils';
import { formatBRL, formatDecimal } from '@/lib/formatters';
import { useAppStore } from '@/store/app-context';
import { useOptionalWorkspaceBrand } from '@/providers/workspace-brand-provider';
import { CustomPeriodButton } from '@/components/common/CustomPeriodButton';

const periods = [
  { key: 'today', label: 'Hoje', days: 1 },
  { key: '7d', label: '7 dias', days: 7 },
  { key: '30d', label: '30 dias', days: 30 },
] as const;

/** Home adaptativa: ordem dos blocos por pacote de acesso (session.activeRole).
 * Papel desconhecido/ausente mantém a ordem original (não muda nada). */
const HOME_BLOCK_ORDER: Record<string, ReadonlyArray<'pipeline' | 'channels' | 'revenue' | 'decisions' | 'reports'>> = {
  office_owner: ['decisions', 'revenue', 'pipeline', 'channels', 'reports'],
  office_admin: ['decisions', 'revenue', 'pipeline', 'channels', 'reports'],
  managing_partner: ['decisions', 'revenue', 'pipeline', 'channels', 'reports'],
  legal_manager: ['decisions', 'pipeline', 'channels', 'revenue', 'reports'],
  senior_lawyer: ['decisions', 'pipeline', 'channels', 'revenue', 'reports'],
  lawyer: ['decisions', 'pipeline', 'channels', 'revenue', 'reports'],
  paralegal: ['decisions', 'pipeline', 'channels', 'revenue', 'reports'],
  secretary: ['decisions', 'reports', 'pipeline', 'channels', 'revenue'],
  intake_sdr: ['decisions', 'reports', 'pipeline', 'channels', 'revenue'],
  marketing: ['channels', 'pipeline', 'revenue', 'decisions', 'reports'],
};
const DEFAULT_HOME_BLOCK_ORDER = ['pipeline', 'channels', 'revenue', 'decisions', 'reports'] as const;

type AhaState = { whatsappVisited: boolean; testMessageVisited: boolean; roiVisited: boolean; dismissed: boolean };
const AHA_DEFAULT_STATE: AhaState = { whatsappVisited: false, testMessageVisited: false, roiVisited: false, dismissed: false };

function ahaStorageKey(workspaceId: string | null) {
  return `klm-aha-onboarding:${workspaceId ?? 'no-workspace'}`;
}

function readAhaState(workspaceId: string | null): AhaState {
  try {
    const raw = window.localStorage.getItem(ahaStorageKey(workspaceId));
    if (!raw) return AHA_DEFAULT_STATE;
    return { ...AHA_DEFAULT_STATE, ...JSON.parse(raw) };
  } catch {
    return AHA_DEFAULT_STATE;
  }
}

function writeAhaState(workspaceId: string | null, next: AhaState) {
  try {
    window.localStorage.setItem(ahaStorageKey(workspaceId), JSON.stringify(next));
  } catch {
    // localStorage indisponível (modo privado etc.): o card volta a aparecer na próxima sessão.
  }
}

function greetingForHour(hour: number) {
  if (hour < 5) return 'Boa noite';
  if (hour < 12) return 'Bom dia';
  if (hour < 18) return 'Boa tarde';
  return 'Boa noite';
}

type ExportFormat = 'csv' | 'json' | 'executive';

type Recommendation = {
  label: string;
  actionType: string;
  channel: KlmTrackingSummary['byChannel'][number];
};

function csvCell(value: unknown) {
  const text = value == null ? '' : String(value);
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function downloadBrowserFile(filename: string, content: string, mimeType: string) {
  const url = URL.createObjectURL(new Blob([content], { type: mimeType }));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.hidden = true;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function channelName(value: string) {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function isoDate(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function startOfLocalDay(date: Date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function meetingStatusLabel(status: 'scheduled' | 'completed' | 'cancelled' | 'no_show') {
  if (status === 'completed') return 'Concluída';
  if (status === 'cancelled') return 'Cancelada';
  if (status === 'no_show') return 'Não compareceu';
  return 'Agendada';
}

export default function HomePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { can, state } = useAppStore();
  const workspaceBrand = useOptionalWorkspaceBrand();
  const canViewIntegrations = can('integrations', 'view');
  const canManageIntegrations = can('integrations', 'manage');
  const [periodDays, setPeriodDays] = useState(1);
  const [reportOpen, setReportOpen] = useState(false);
  const [reportNotice, setReportNotice] = useState<string | null>(null);
  const [recommendationNotice, setRecommendationNotice] = useState<string | null>(null);
  const period = periods.find((item) => item.days === periodDays) ?? { key: `custom-${periodDays}d`, label: `${periodDays} dias`, days: periodDays };

  const workspaceId = state?.activeWorkspace?.id ?? null;
  const activeRole = state?.session?.activeRole ?? state?.currentUser?.role ?? null;
  const userName = state?.currentUser?.name?.split(' ')[0] ?? null;
  const blockOrder = (activeRole && HOME_BLOCK_ORDER[activeRole]) || DEFAULT_HOME_BLOCK_ORDER;

  const [ahaState, setAhaState] = useState<AhaState>(() => readAhaState(workspaceId));
  useEffect(() => setAhaState(readAhaState(workspaceId)), [workspaceId]);
  useEffect(() => {
    const reopen = () => {
      const next = { ...AHA_DEFAULT_STATE };
      writeAhaState(workspaceId, next);
      setAhaState(next);
    };
    window.addEventListener('klm:aha:reopen', reopen);
    return () => window.removeEventListener('klm:aha:reopen', reopen);
  }, [workspaceId]);
  const updateAhaState = (patch: Partial<AhaState>) => {
    setAhaState((previous) => {
      const next = { ...previous, ...patch };
      writeAhaState(workspaceId, next);
      return next;
    });
  };

  const dashboardQuery = useQuery({ queryKey: ['klm', 'dashboard'], queryFn: klmApi.dashboard });
  const kpiQuery = useQuery({
    queryKey: ['klm', 'kpis', period.days, null],
    queryFn: () => klmApi.kpis(period.days, null),
  });
  const funnelQuery = useQuery({ queryKey: ['klm', 'funnel'], queryFn: klmApi.funnel });
  const financeQuery = useQuery({ queryKey: ['klm', 'finance'], queryFn: klmApi.financeOverview });
  const qualityQuery = useQuery({ queryKey: ['klm', 'quality'], queryFn: klmApi.quality });
  const trackingQuery = useQuery({
    queryKey: ['klm', 'tracking', period.days],
    queryFn: () => klmApi.tracking(period.days),
  });
  const meetingsQuery = useQuery({ queryKey: ['klm', 'meetings'], queryFn: klmApi.meetings });
  const adsDateRanges = useMemo(() => {
    const currentEnd = new Date();
    const currentStart = new Date(currentEnd);
    currentStart.setDate(currentStart.getDate() - Math.max(0, period.days - 1));
    const previousEnd = new Date(currentStart);
    previousEnd.setDate(previousEnd.getDate() - 1);
    const previousStart = new Date(previousEnd);
    previousStart.setDate(previousStart.getDate() - Math.max(0, period.days - 1));
    return {
      current: { dateFrom: isoDate(currentStart), dateTo: isoDate(currentEnd) },
      previous: { dateFrom: isoDate(previousStart), dateTo: isoDate(previousEnd) },
    };
  }, [period.days]);
  const adsCurrentQuery = useQuery({
    queryKey: ['klm', 'ads-manager', 'summary', adsDateRanges.current],
    queryFn: () => klmApi.adsManagerSummary(adsDateRanges.current),
    enabled: canViewIntegrations,
    retry: false,
  });
  const adsPreviousQuery = useQuery({
    queryKey: ['klm', 'ads-manager', 'summary', adsDateRanges.previous],
    queryFn: () => klmApi.adsManagerSummary(adsDateRanges.previous),
    enabled: canViewIntegrations,
    retry: false,
  });
  const connectionsQuery = useQuery({
    queryKey: ['klm', 'omnichannel', 'connections', 'whatsapp'],
    queryFn: () => klmApi.omnichannelConnections('whatsapp'),
  });
  const hasActiveWhatsapp = connectionsQuery.data?.some((connection) => connection.status === 'connected') ?? false;
  const ahaSteps = [
    { key: 'whatsappVisited' as const, label: 'Conectar WhatsApp', done: hasActiveWhatsapp || ahaState.whatsappVisited, path: '/app/conexoes' },
    { key: 'testMessageVisited' as const, label: 'Enviar mensagem-teste', done: ahaState.testMessageVisited, path: '/app/conversas' },
    { key: 'roiVisited' as const, label: 'Ver ROI de demonstração', done: ahaState.roiVisited, path: '/app/rastreamento' },
  ];
  const ahaCompletedCount = ahaSteps.filter((step) => step.done).length;
  const showAhaCard = !hasActiveWhatsapp && !ahaState.dismissed && connectionsQuery.isSuccess;

  const kpiByKey = useMemo(
    () => Object.fromEntries((kpiQuery.data?.topMetrics ?? []).map((item) => [item.key, item])),
    [kpiQuery.data?.topMetrics],
  );
  const metricState = (pending: boolean, error: boolean, scope: string) => pending ? 'Carregando' : error ? 'Indisponível' : scope;
  const leadsTrend = kpiQuery.data?.trend?.leads?.map((point) => point.value);
  const revenueTrend = kpiQuery.data?.trend?.revenue?.map((point) => point.value);
  const acquisitionTrend = trackingQuery.data?.timeTrend?.slice(-14) ?? [];
  const acquisitionTrendMax = Math.max(1, ...acquisitionTrend.map((point) => point.leads));
  const projectedLeads = trackingQuery.isSuccess && trackingQuery.data.leads > 0
    ? Math.round((trackingQuery.data.leads / Math.max(1, period.days)) * 30)
    : null;
  const metrics = [
    {
      label: 'Leads novos',
      value: trackingQuery.isSuccess ? String(trackingQuery.data.leads) : '—',
      delta: metricState(trackingQuery.isPending, trackingQuery.isError, period.label),
      trend: 'neutral' as const,
      path: '/app/leads',
      loading: trackingQuery.isPending,
      sparkline: leadsTrend,
    },
    {
      label: 'CAC médio',
      value: trackingQuery.isSuccess && trackingQuery.data.cac != null ? formatBRL(trackingQuery.data.cac) : '—',
      delta: metricState(trackingQuery.isPending, trackingQuery.isError, period.label),
      trend: 'neutral' as const,
      path: `/app/rastreamento?period=${period.days}&tab=canais`,
      loading: trackingQuery.isPending,
    },
    {
      label: 'Reuniões online',
      value: trackingQuery.isSuccess ? String(trackingQuery.data.meetings) : '—',
      delta: metricState(trackingQuery.isPending, trackingQuery.isError, period.label),
      trend: 'neutral' as const,
      path: '/app/agenda',
      loading: trackingQuery.isPending,
    },
    {
      label: 'Contratos enviados',
      value: kpiQuery.isSuccess && kpiByKey.contracts ? String(kpiByKey.contracts.value) : '—',
      delta: metricState(kpiQuery.isPending, kpiQuery.isError, 'Snapshot atual'),
      trend: 'neutral' as const,
      path: '/app/contratos',
      loading: kpiQuery.isPending,
    },
    {
      label: 'Receita prevista',
      value: kpiQuery.isSuccess && kpiByKey.pipeline ? formatBRL(Number(kpiByKey.pipeline.value)) : '—',
      delta: metricState(kpiQuery.isPending, kpiQuery.isError, 'Snapshot atual'),
      trend: 'neutral' as const,
      path: '/app/financeiro',
      loading: kpiQuery.isPending,
      sparkline: revenueTrend,
    },
  ];

  const funnel = useMemo(() => {
    if (!funnelQuery.data) return [];
    const visibleStages = funnelQuery.data.stages.slice(0, 6);
    const base = visibleStages[0]?.count ?? 0;
    return visibleStages.map((item, index) => ({
      id: item.id,
      label: item.label,
      value: item.count,
      progress: base > 0 ? Math.round(item.count / base * 100) : index === 0 && item.count > 0 ? 100 : 0,
    }));
  }, [funnelQuery.data]);
  const funnelConcentration = [...funnel].sort((left, right) => right.value - left.value)[0];

  const channels = useMemo(() => {
    const rows = trackingQuery.data?.byChannel ?? [];
    const maxEvents = Math.max(1, ...rows.map((channel) => channel.events));
    return rows.slice(0, 4).map((channel) => ({
      raw: channel,
      label: channelName(channel.channel),
      leads: channel.leads,
      cac: channel.cac,
      progress: Math.round(channel.events / maxEvents * 100),
    }));
  }, [trackingQuery.data?.byChannel]);
  const googleChannel = trackingQuery.data?.byChannel.find((channel) => /google|search/i.test(channel.channel));
  const tiktokChannel = trackingQuery.data?.byChannel.find((channel) => /tiktok/i.test(channel.channel));
  const marketingIntelligence = useMemo(() => {
    const summary = trackingQuery.data;
    if (!summary) return null;
    const activeChannels = summary.byChannel.filter((channel) => channel.leads > 0 || channel.clients > 0 || channel.attributableRevenue > 0);
    const bestChannel = [...activeChannels].sort((left, right) => (
      (right.clients - left.clients)
      || (right.attributableRevenue - left.attributableRevenue)
      || (right.qualifiedLeads - left.qualifiedLeads)
      || (right.leads - left.leads)
    ))[0] ?? null;
    const attributableCampaigns = summary.attributionRows.filter((row) => (
      row.campaignName
      && row.evidenceLevel !== 'unattributed'
      && (row.leads > 0 || row.clients > 0 || row.attributableRevenue > 0)
    ));
    const bestCampaign = [...attributableCampaigns].sort((left, right) => (
      (right.clients - left.clients)
      || (right.attributableRevenue - left.attributableRevenue)
      || (right.qualifiedLeads - left.qualifiedLeads)
      || (right.leads - left.leads)
    ))[0] ?? null;
    const roi = summary.adSpend > 0
      ? ((summary.attributableRevenue - summary.adSpend) / summary.adSpend) * 100
      : null;
    const averageCampaignSpend = attributableCampaigns.length
      ? attributableCampaigns.reduce((total, row) => total + row.adSpend, 0) / attributableCampaigns.length
      : 0;
    const increaseCandidate = attributableCampaigns.find((row) => (
      row.clients > 0
      && row.adSpend > 0
      && row.roas != null
      && row.roas >= Math.max(1.2, summary.roas ?? 0)
    )) ?? null;
    const reviewCandidate = attributableCampaigns.find((row) => (
      row.clients === 0
      && row.leads >= 3
      && row.adSpend >= Math.max(1, averageCampaignSpend)
    )) ?? null;
    const totalChannelLeads = activeChannels.reduce((total, channel) => total + channel.leads, 0);
    const topChannelShare = bestChannel && totalChannelLeads > 0 ? bestChannel.leads / totalChannelLeads : null;
    const exploreNewChannels = period.days >= 7 && totalChannelLeads >= 10 && topChannelShare != null && topChannelShare >= 0.7;

    const previousCampaigns = new Map((adsPreviousQuery.data?.campaigns ?? []).map((campaign) => [campaign.externalId, campaign]));
    const fatigueCandidate = (adsCurrentQuery.data?.campaigns ?? []).find((campaign) => {
      const previous = previousCampaigns.get(campaign.externalId);
      const currentCtr = campaign.ctr == null ? null : Number(campaign.ctr);
      const previousCtr = previous?.ctr == null ? null : Number(previous.ctr);
      return campaign.impressions >= 1_000
        && currentCtr != null
        && previousCtr != null
        && previousCtr > 0
        && currentCtr <= previousCtr * 0.8
        && campaign.contracts <= (previous?.contracts ?? 0);
    }) ?? null;

    return {
      bestChannel,
      bestCampaign,
      roi,
      increaseCandidate,
      reviewCandidate,
      exploreNewChannels,
      topChannelShare,
      fatigueCandidate,
    };
  }, [adsCurrentQuery.data?.campaigns, adsPreviousQuery.data?.campaigns, period.days, trackingQuery.data]);

  const agendaOverview = useMemo(() => {
    const now = new Date();
    const todayStart = startOfLocalDay(now);
    const tomorrowStart = new Date(todayStart);
    tomorrowStart.setDate(tomorrowStart.getDate() + 1);
    const weekday = todayStart.getDay();
    const mondayOffset = weekday === 0 ? -6 : 1 - weekday;
    const weekStart = new Date(todayStart);
    weekStart.setDate(weekStart.getDate() + mondayOffset);
    const nextWeekStart = new Date(weekStart);
    nextWeekStart.setDate(nextWeekStart.getDate() + 7);
    const active = (meetingsQuery.data ?? [])
      .filter((meeting) => meeting.status !== 'cancelled')
      .map((meeting) => ({ ...meeting, start: new Date(meeting.startsAt) }))
      .filter((meeting) => !Number.isNaN(meeting.start.getTime()))
      .sort((left, right) => left.start.getTime() - right.start.getTime());
    const today = active.filter((meeting) => meeting.start >= todayStart && meeting.start < tomorrowStart);
    const remainderOfWeek = active.filter((meeting) => meeting.start >= tomorrowStart && meeting.start < nextWeekStart);
    const scheduledThisWeek = active.filter((meeting) => meeting.start >= weekStart && meeting.start < nextWeekStart);
    return {
      today,
      remainderOfWeek,
      scheduledThisWeek,
      orphanCount: scheduledThisWeek.filter((meeting) => !meeting.matterId).length,
      missingRoomCount: scheduledThisWeek.filter((meeting) => !meeting.meetingUrl && !meeting.location).length,
    };
  }, [meetingsQuery.data]);
  const mattersById = useMemo(
    () => new Map((funnelQuery.data?.cards ?? []).map((matter) => [matter.id, matter.name])),
    [funnelQuery.data?.cards],
  );

  const decisions = dashboardQuery.data?.decisions.map((decision) => decision.type === 'approval'
    ? { priority: 'Alta', title: `${decision.count} ${decision.count === 1 ? 'aprovação' : 'aprovações'} aguardando decisão`, meta: 'Revisão humana obrigatória', path: '/app/aprovacoes', risk: true }
    : { priority: 'Alta', title: `${decision.count} follow-up${decision.count === 1 ? '' : 's'} vencido${decision.count === 1 ? '' : 's'}`, meta: 'Política: lead nunca fica sem resposta', path: '/app/conversas', risk: true }) ?? [];

  const dueFollowUps = dashboardQuery.data?.metrics.dueFollowUps ?? 0;
  const pendingApprovals = dashboardQuery.data?.metrics.pendingApprovals ?? 0;
  const greetingSummary = dashboardQuery.isPending
    ? 'Carregando o resumo do dia...'
    : dashboardQuery.isError
      ? 'Não foi possível carregar o resumo do dia agora.'
      : dueFollowUps === 0 && pendingApprovals === 0
        ? 'Nenhum follow-up vencido nem decisão pendente agora.'
        : [
          dueFollowUps > 0 ? `${dueFollowUps} follow-up${dueFollowUps === 1 ? '' : 's'} vencido${dueFollowUps === 1 ? '' : 's'}` : null,
          pendingApprovals > 0 ? `${pendingApprovals} ${pendingApprovals === 1 ? 'decisão pendente' : 'decisões pendentes'}` : null,
        ].filter(Boolean).join(' · ');

  const allQueries = [dashboardQuery, kpiQuery, funnelQuery, financeQuery, qualityQuery, trackingQuery];
  const reportReady = allQueries.every((query) => query.isSuccess);
  const hasQueryError = allQueries.some((query) => query.isError);
  const localDataState = reportReady
    ? 'Dados locais carregados.'
    : hasQueryError
      ? 'Parte da base local está indisponível; nenhum valor substituto foi usado.'
      : 'Carregando dados locais confirmados.';

  const recommendationMutation = useMutation({
    mutationFn: async ({ label, actionType, channel }: Recommendation) => {
      const intent = await klmApi.createExternalIntent({
        actionType,
        provider: channel.channel,
        idempotencyKey: `home:${actionType}:${channel.channel}:${period.days}:${trackingQuery.data?.periodStart ?? 'current'}`.slice(0, 180),
        payload: {
          label,
          periodDays: period.days,
          channelSnapshot: channel,
          externalActionsExecuted: 0,
        },
      });
      return klmApi.approveExternalIntentLocally(intent.id);
    },
    onSuccess: (intent, variables) => {
      setRecommendationNotice(`${variables.label} registrada (${intent.status}). Nenhuma campanha externa foi alterada.`);
      void queryClient.invalidateQueries({ queryKey: ['klm', 'external-intents'] });
      void queryClient.invalidateQueries({ queryKey: ['klm', 'activity'] });
    },
    onError: () => setRecommendationNotice('Não foi possível salvar a recomendação agora.'),
  });

  const exportReport = (format: ExportFormat) => {
    if (!reportReady) {
      setReportNotice(hasQueryError ? 'Corrija as consultas indisponíveis antes de exportar.' : 'Aguarde o carregamento das consultas locais.');
      return;
    }
    const generatedAt = new Date().toISOString();
    const snapshot = {
      generatedAt,
      source: 'local_api_snapshot',
      period: { label: period.label, days: period.days },
      dashboard: dashboardQuery.data,
      kpis: kpiQuery.data,
      funnel: funnelQuery.data,
      finance: financeQuery.data,
      quality: qualityQuery.data,
      tracking: trackingQuery.data,
    };
    try {
      if (format === 'json') {
        downloadBrowserFile(`klm-home-${period.key}.json`, JSON.stringify(snapshot, null, 2), 'application/json;charset=utf-8');
        setReportNotice('Arquivo JSON gerado com os dados atuais do sistema.');
      } else if (format === 'csv') {
        const rows: unknown[][] = [['seção', 'chave', 'rótulo', 'valor', 'unidade']];
        kpiQuery.data.topMetrics.forEach((item) => rows.push(['kpi', item.key, item.label, item.value, item.unit]));
        funnelQuery.data.stages.forEach((item) => rows.push(['funil', item.id, item.label, item.count, 'count']));
        Object.entries(dashboardQuery.data.metrics).forEach(([key, value]) => rows.push(['dashboard', key, key, value, 'count']));
        trackingQuery.data.byChannel.forEach((channel) => {
          rows.push(['canal', channel.channel, 'leads', channel.leads, 'count']);
          rows.push(['canal', channel.channel, 'CAC', channel.cac, 'BRL']);
          rows.push(['canal', channel.channel, 'ROAS', channel.roas, 'ratio']);
        });
        Object.entries(financeQuery.data).filter(([, value]) => typeof value === 'number').forEach(([key, value]) => rows.push(['financeiro', key, key, value, 'BRL']));
        qualityQuery.data.dimensions.forEach((item) => rows.push(['qualidade', item.key, item.label, item.score, 'score']));
        downloadBrowserFile(`klm-home-${period.key}.csv`, `\uFEFF${rows.map((row) => row.map(csvCell).join(',')).join('\n')}`, 'text/csv;charset=utf-8');
        setReportNotice('Arquivo CSV gerado com os dados atuais do sistema.');
      } else {
        const html = buildExecutiveReportHtml({
          brand: workspaceBrand?.brand ?? null,
          periodLabel: period.label,
          generatedAt: new Date(),
          metrics: metrics.map((metric) => ({ label: metric.label, value: metric.value })),
          funnel: funnel.map((stage) => ({ label: stage.label, value: stage.value, progress: stage.progress })),
          channels: trackingQuery.data.byChannel.map((channel) => ({
            label: channelName(channel.channel),
            leads: channel.leads,
            cac: channel.cac == null ? '—' : formatBRL(channel.cac),
            roas: channel.roas == null ? '—' : `${formatDecimal(channel.roas, 2)}x`,
          })),
          revenue: {
            paid: formatBRL(financeQuery.data.paidAmount),
            overdue: formatBRL(financeQuery.data.overdueAmount),
            scheduled: formatBRL(financeQuery.data.scheduledAmount),
            receivables: formatBRL(financeQuery.data.totalOpenReceivables),
          },
          revenueTrend: kpiQuery.data.trend?.revenue?.map((point) => point.value) ?? [],
        });
        const opened = openExecutiveReport(html);
        setReportNotice(opened ? 'Relatório executivo aberto em nova aba: use "Salvar como PDF" no diálogo de impressão.' : 'O navegador bloqueou a nova aba. Permita pop-ups para este site e tente novamente.');
      }
    } catch {
      setReportNotice('O navegador não conseguiu iniciar o download local.');
    }
  };

  const reportCards = [
    ['Diário', 'Conversas ativas', dashboardQuery.isSuccess ? dashboardQuery.data.metrics.activeThreads == null ? 'Não agregado pela API' : `${dashboardQuery.data.metrics.activeThreads} agora` : metricState(dashboardQuery.isPending, dashboardQuery.isError, 'Sem dados')],
    ['Aquisição', 'Leads e clientes', trackingQuery.isSuccess ? `${trackingQuery.data.leads} / ${trackingQuery.data.clients}` : metricState(trackingQuery.isPending, trackingQuery.isError, 'Sem dados')],
    ['Financeiro', 'Cobranças pendentes', financeQuery.isSuccess ? String(financeQuery.data.pendingReminders) : metricState(financeQuery.isPending, financeQuery.isError, 'Sem dados')],
    ['Operação', 'Integridade operacional', qualityQuery.isSuccess ? `${qualityQuery.data.score}/100` : metricState(qualityQuery.isPending, qualityQuery.isError, 'Sem dados')],
  ];
  const qualityRows = useMemo(() => qualityQuery.data?.dimensions ?? [], [qualityQuery.data?.dimensions]);
  const attentionSignals = useMemo(() => {
    const signals: Array<{ title: string; detail: string; path: string; level: 'high' | 'medium' }> = [];
    if (trackingQuery.isSuccess) {
      const coverage = trackingQuery.data.attributionCoverage.trackingRate;
      if (coverage != null && coverage < 80) signals.push({ title: 'Origem dos leads incompleta', detail: `${formatDecimal(coverage, 1)}% das conversas estão rastreadas. Campanhas sem origem reduzem a precisão do CAC.`, path: '/app/rastreamento', level: coverage < 60 ? 'high' : 'medium' });
      if (trackingQuery.data.leads > 0 && trackingQuery.data.clients === 0) signals.push({ title: 'Leads sem conversão no período', detail: `${trackingQuery.data.leads} lead${trackingQuery.data.leads === 1 ? '' : 's'} entrou${trackingQuery.data.leads === 1 ? '' : 'aram'}, mas nenhum novo cliente foi atribuído.`, path: '/app/funil', level: 'high' });
    }
    if (financeQuery.isSuccess && financeQuery.data.overdueAmount > 0) signals.push({ title: 'Receita em atraso', detail: `${formatBRL(financeQuery.data.overdueAmount)} exige acompanhamento de cobrança.`, path: '/app/financeiro', level: 'high' });
    qualityRows.filter((row) => row.score < 70).slice(0, 2).forEach((row) => signals.push({ title: `${row.label} abaixo do esperado`, detail: `Indicador operacional em ${row.score}/100. Abra a análise para ver as evidências e ações recomendadas.`, path: '/app/qualidade', level: row.score < 50 ? 'high' : 'medium' }));
    return signals.slice(0, 4);
  }, [financeQuery.data, financeQuery.isSuccess, qualityRows, trackingQuery.data, trackingQuery.isSuccess]);
  const pendingOnboarding = ahaSteps.filter((step) => !step.done);
  const openSupport = () => window.dispatchEvent(new CustomEvent('meu-executivo:support:open'));

  return (
    <div className="mx-auto max-w-[1220px] space-y-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-2xs uppercase tracking-wide text-muted-foreground">Meu Executivo / Operação jurídica</p>
          <h2 className="mt-2 text-2xl font-semibold">
            {greetingForHour(new Date().getHours())}{userName ? `, ${userName}` : ''}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">{greetingSummary}</p>
          <p className="mt-1 text-xs text-muted-foreground">Tudo que exige decisão hoje: aquisição, atendimento, documentos, funil, receita e agentes. {localDataState}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-lg border border-border bg-card p-1">
            {periods.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => setPeriodDays(item.days)}
                className={cn('h-7 rounded-md px-3 text-sm font-medium transition-colors', period.key === item.key ? 'bg-primary text-[#131720]' : 'text-muted-foreground hover:bg-secondary')}
              >
                {item.label}
              </button>
            ))}
          </div>
          <CustomPeriodButton value={period.days} onChange={setPeriodDays} presets={periods.map((item) => item.days)} />
          <Button className="h-9 shrink-0 bg-[#031121] px-4 text-sm text-white hover:bg-[#031121]" onClick={() => { setReportNotice(null); setReportOpen(true); }}>
            <FileText className="mr-2 h-3.5 w-3.5" />
            Gerar relatório
          </Button>
        </div>
      </div>

      {showAhaCard && (
        <KlmPanel className="border-primary/30 p-4" data-testid="aha-onboarding-card">
          <div className="flex items-start justify-between gap-4">
            <KlmSectionTitle
              title="Comece por aqui"
              description="3 passos para ver o WhatsApp e o ROI de anúncios funcionando de ponta a ponta."
            />
            <button
              type="button"
              className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              onClick={() => updateAhaState({ dismissed: true })}
            >
              <X className="h-3.5 w-3.5" />Dispensar
            </button>
          </div>
          <div className="mt-3">
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>{ahaCompletedCount} de {ahaSteps.length} concluídos</span>
            </div>
            <KlmProgress value={(ahaCompletedCount / ahaSteps.length) * 100} tone="green" />
          </div>
          <div className="mt-4 grid gap-2 sm:grid-cols-3">
            {ahaSteps.map((step, index) => (
              <button
                key={step.key}
                type="button"
                className={cn(
                  'flex items-center gap-2 rounded-lg border p-3 text-left transition hover:-translate-y-px',
                  step.done ? 'border-emerald-600/30 bg-emerald-500/[0.08]' : 'border-border bg-card hover:border-primary',
                )}
                onClick={() => { updateAhaState({ [step.key]: true } as Partial<AhaState>); navigate(step.path); }}
              >
                <span className={cn('flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-2xs font-semibold', step.done ? 'bg-[#1a9948] text-white' : 'bg-secondary text-muted-foreground')}>
                  {step.done ? <CheckCircle2 className="h-3.5 w-3.5" /> : index + 1}
                </span>
                <span className="min-w-0 truncate text-xs font-medium">{step.label}</span>
              </button>
            ))}
          </div>
        </KlmPanel>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        {metrics.map((metric) => <KlmMetricCard key={metric.label} {...metric} onClick={() => navigate(metric.path)} />)}
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.35fr_1fr]">
        <KlmPanel className="p-4">
          <KlmSectionTitle title="Entrada de leads e projeção" description={`Ritmo de aquisição · ${period.label}`} action={<Button variant="outline" size="sm" className="h-8 text-xs" onClick={() => navigate(`/app/rastreamento?period=${period.days}`)}>Ver aquisição</Button>} />
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            <div className="rounded-lg border border-border bg-card p-3"><span className="text-xs text-muted-foreground">Leads no período</span><strong className="mt-1 block text-xl tabular-nums">{trackingQuery.isSuccess ? trackingQuery.data.leads : '—'}</strong></div>
            <div className="rounded-lg border border-border bg-card p-3"><span className="text-xs text-muted-foreground">Novos clientes</span><strong className="mt-1 block text-xl tabular-nums">{trackingQuery.isSuccess ? trackingQuery.data.clients : '—'}</strong></div>
            <div className="rounded-lg border border-primary/30 bg-primary/[0.06] p-3"><span className="flex items-center gap-1.5 text-xs text-muted-foreground"><TrendingUp className="h-3.5 w-3.5 text-primary" />Ritmo projetado em 30 dias</span><strong className="mt-1 block text-xl tabular-nums">{projectedLeads == null ? '—' : `${projectedLeads} leads`}</strong><span className="mt-1 block text-[10px] text-muted-foreground">Projeção linear pelo ritmo do período selecionado.</span></div>
          </div>
          <div className="mt-4 flex h-24 items-end gap-1 rounded-lg border border-border bg-secondary/40 px-3 pb-3 pt-5" aria-label="Evolução diária de leads">
            {acquisitionTrend.length ? acquisitionTrend.map((point) => (
              <button key={point.date} type="button" className="group relative flex h-full min-w-0 flex-1 items-end" title={`${new Date(`${point.date}T12:00:00`).toLocaleDateString('pt-BR')}: ${point.leads} leads`} onClick={() => navigate(`/app/rastreamento?period=${period.days}`)}>
                <span className="w-full rounded-t-sm bg-primary/75 transition-colors group-hover:bg-primary" style={{ height: `${Math.max(point.leads > 0 ? 8 : 2, point.leads / acquisitionTrendMax * 100)}%` }} />
              </button>
            )) : <p className="m-auto text-xs text-muted-foreground">A evolução aparecerá quando houver eventos de aquisição no período.</p>}
          </div>
        </KlmPanel>

        <KlmPanel className="p-4">
          <KlmSectionTitle title="Atenção do gestor" description="Indicadores que merecem decisão ou investigação" />
          <div className="mt-4 space-y-2">
            {(trackingQuery.isPending || financeQuery.isPending || qualityQuery.isPending) && Array.from({ length: 2 }).map((_, index) => <Skeleton key={index} className="h-16 w-full rounded-lg" />)}
            {!(trackingQuery.isPending || financeQuery.isPending || qualityQuery.isPending) && attentionSignals.length ? attentionSignals.map((signal) => (
              <button key={signal.title} type="button" className={cn('flex w-full items-start gap-3 rounded-lg border p-3 text-left', signal.level === 'high' ? 'border-[#b94a48]/35 bg-[#b94a48]/[0.06]' : 'border-primary/25 bg-primary/[0.04]')} onClick={() => navigate(signal.path)}>
                <ShieldAlert className={cn('mt-0.5 h-4 w-4 shrink-0', signal.level === 'high' ? 'text-[#b94a48]' : 'text-primary')} />
                <span className="min-w-0 flex-1"><strong className="block text-xs">{signal.title}</strong><span className="mt-1 block text-[11px] leading-4 text-muted-foreground">{signal.detail}</span></span><ArrowRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              </button>
            )) : !(trackingQuery.isPending || financeQuery.isPending || qualityQuery.isPending) && <div className="flex items-center gap-3 rounded-lg border border-emerald-600/25 bg-emerald-500/[0.06] p-3"><CheckCircle2 className="h-4 w-4 text-[#0b7435]" /><p className="text-xs text-muted-foreground">Nenhum alerta executivo foi identificado nos dados carregados.</p></div>}
          </div>
        </KlmPanel>
      </div>

      <KlmPanel className="p-4">
        <KlmSectionTitle
          title="Agenda executiva"
          description="Compromissos de hoje e visão da semana"
          action={<Button variant="outline" size="sm" className="h-8 text-xs" onClick={() => navigate('/app/agenda')}>Abrir agenda</Button>}
        />
        {meetingsQuery.isPending && <div className="mt-4 grid gap-3 lg:grid-cols-2">{Array.from({ length: 2 }).map((_, index) => <Skeleton key={index} className="h-40 rounded-lg" />)}</div>}
        {meetingsQuery.isError && (
          <div className="mt-4 flex items-center justify-between gap-3 rounded-lg border border-[#b94a48]/30 bg-[#b94a48]/[0.06] p-3">
            <p className="text-xs text-[#a53e3c]">Não foi possível carregar os compromissos. Nenhuma agenda substituta foi exibida.</p>
            <Button variant="outline" size="sm" className="h-8 shrink-0 text-xs" onClick={() => meetingsQuery.refetch()}>Tentar novamente</Button>
          </div>
        )}
        {meetingsQuery.isSuccess && (
          <>
            <div className="mt-4 grid gap-2 sm:grid-cols-3">
              <button type="button" className="rounded-lg border border-primary/30 bg-primary/[0.06] p-3 text-left hover:border-primary" onClick={() => navigate('/app/agenda')}>
                <span className="flex items-center gap-1.5 text-xs text-muted-foreground"><CalendarClock className="h-3.5 w-3.5 text-primary" />Hoje</span>
                <strong className="mt-1 block text-xl tabular-nums">{agendaOverview.today.length}</strong>
              </button>
              <button type="button" className="rounded-lg border border-border p-3 text-left hover:border-primary" onClick={() => navigate('/app/agenda')}>
                <span className="flex items-center gap-1.5 text-xs text-muted-foreground"><CalendarDays className="h-3.5 w-3.5 text-primary" />Esta semana</span>
                <strong className="mt-1 block text-xl tabular-nums">{agendaOverview.scheduledThisWeek.length}</strong>
              </button>
              <div className="rounded-lg border border-border p-3">
                <span className="text-xs text-muted-foreground">Atenção de preparo</span>
                <strong className="mt-1 block text-sm">{agendaOverview.orphanCount + agendaOverview.missingRoomCount === 0 ? 'Agenda organizada' : `${agendaOverview.orphanCount + agendaOverview.missingRoomCount} pendência${agendaOverview.orphanCount + agendaOverview.missingRoomCount === 1 ? '' : 's'}`}</strong>
                <span className="mt-1 block text-[11px] text-muted-foreground">{agendaOverview.orphanCount} sem caso · {agendaOverview.missingRoomCount} sem sala ou link</span>
              </div>
            </div>
            <div className="mt-3 grid gap-3 lg:grid-cols-2">
              <div className="rounded-lg border border-border p-3">
                <h3 className="flex items-center gap-2 text-xs font-semibold"><Clock3 className="h-4 w-4 text-primary" />Hoje</h3>
                <div className="mt-2 space-y-2">
                  {agendaOverview.today.length ? agendaOverview.today.slice(0, 5).map((meeting) => (
                    <button key={meeting.id} type="button" className="flex w-full items-center gap-3 rounded-md border border-border px-3 py-2 text-left hover:border-primary" onClick={() => navigate(`/app/agenda?meeting=${meeting.id}`)}>
                      <span className="w-11 shrink-0 text-xs font-semibold tabular-nums">{meeting.start.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}</span>
                      <span className="min-w-0 flex-1"><strong className="block truncate text-xs">{meeting.title.replace(/^Reunião online:\s*/i, '')}</strong><span className="mt-0.5 block truncate text-[11px] text-muted-foreground">{meeting.matterId ? mattersById.get(meeting.matterId) ?? 'Caso vinculado' : 'Sem caso vinculado'} · {meetingStatusLabel(meeting.status)}</span></span>
                      {meeting.meetingUrl ? <Video className="h-3.5 w-3.5 shrink-0 text-[#0b7435]" /> : meeting.location ? <MapPin className="h-3.5 w-3.5 shrink-0 text-primary" /> : <ShieldAlert className="h-3.5 w-3.5 shrink-0 text-[#b94a48]" />}
                    </button>
                  )) : <p className="rounded-md bg-secondary p-3 text-xs text-muted-foreground">Nenhum compromisso registrado para hoje.</p>}
                </div>
              </div>
              <div className="rounded-lg border border-border p-3">
                <h3 className="flex items-center gap-2 text-xs font-semibold"><CalendarDays className="h-4 w-4 text-primary" />Próximos compromissos da semana</h3>
                <div className="mt-2 space-y-2">
                  {agendaOverview.remainderOfWeek.length ? agendaOverview.remainderOfWeek.slice(0, 5).map((meeting) => (
                    <button key={meeting.id} type="button" className="flex w-full items-center gap-3 rounded-md border border-border px-3 py-2 text-left hover:border-primary" onClick={() => navigate(`/app/agenda?meeting=${meeting.id}`)}>
                      <span className="w-20 shrink-0 text-[11px] font-semibold capitalize">{meeting.start.toLocaleDateString('pt-BR', { weekday: 'short', day: '2-digit' })}<span className="mt-0.5 block font-normal tabular-nums text-muted-foreground">{meeting.start.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}</span></span>
                      <span className="min-w-0 flex-1"><strong className="block truncate text-xs">{meeting.title.replace(/^Reunião online:\s*/i, '')}</strong><span className="mt-0.5 block truncate text-[11px] text-muted-foreground">{meeting.matterId ? mattersById.get(meeting.matterId) ?? 'Caso vinculado' : 'Sem caso vinculado'}</span></span>
                      <ArrowRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    </button>
                  )) : <p className="rounded-md bg-secondary p-3 text-xs text-muted-foreground">Nenhum outro compromisso registrado até o fim da semana.</p>}
                </div>
              </div>
            </div>
          </>
        )}
      </KlmPanel>

      <KlmPanel className="p-4">
        <KlmSectionTitle
          title="Inteligência de marketing"
          description={`Campanhas, canais e retorno com evidência do período · ${period.label}`}
          action={<Button variant="outline" size="sm" className="h-8 text-xs" onClick={() => navigate(`/app/rastreamento?period=${period.days}&tab=atribuicao`)}>Abrir análise completa</Button>}
        />
        {trackingQuery.isPending && <div className="mt-4 grid gap-3 md:grid-cols-4">{Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-24 rounded-lg" />)}</div>}
        {trackingQuery.isError && <p className="mt-4 rounded-lg border border-[#b94a48]/30 bg-[#b94a48]/[0.06] p-3 text-xs text-[#a53e3c]">Não foi possível consultar a atribuição. Nenhuma recomendação foi gerada.</p>}
        {marketingIntelligence && (
          <>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <button type="button" className="rounded-lg border border-border p-3 text-left hover:border-primary" onClick={() => navigate(`/app/rastreamento?${marketingIntelligence.bestChannel ? `channel=${encodeURIComponent(marketingIntelligence.bestChannel.channel)}&` : ''}period=${period.days}&tab=canais`)}>
                <span className="text-xs text-muted-foreground">Canal com mais resultado</span>
                <strong className="mt-1 block truncate text-base">{marketingIntelligence.bestChannel ? channelName(marketingIntelligence.bestChannel.channel) : 'Dados insuficientes'}</strong>
                <span className="mt-1 block text-[11px] text-muted-foreground">{marketingIntelligence.bestChannel ? `${marketingIntelligence.bestChannel.leads} leads · ${marketingIntelligence.bestChannel.clients} clientes` : 'Nenhum canal com aquisição atribuída.'}</span>
              </button>
              <button type="button" className="rounded-lg border border-border p-3 text-left hover:border-primary" onClick={() => navigate(`/app/rastreamento?period=${period.days}&tab=atribuicao`)}>
                <span className="text-xs text-muted-foreground">Campanha com mais resultado</span>
                <strong className="mt-1 block truncate text-base">{marketingIntelligence.bestCampaign?.campaignName ?? 'Dados insuficientes'}</strong>
                <span className="mt-1 block text-[11px] text-muted-foreground">{marketingIntelligence.bestCampaign ? `${marketingIntelligence.bestCampaign.clients} clientes · ${formatBRL(marketingIntelligence.bestCampaign.attributableRevenue)}` : 'Falta campanha identificada com resultado.'}</span>
              </button>
              <div className="rounded-lg border border-border p-3">
                <span className="text-xs text-muted-foreground">ROI médio atribuído</span>
                <strong className="mt-1 block text-base tabular-nums">{marketingIntelligence.roi == null ? 'Não calculável' : `${formatDecimal(marketingIntelligence.roi, 1)}%`}</strong>
                <span className="mt-1 block text-[11px] text-muted-foreground">{marketingIntelligence.roi == null ? 'Investimento atribuído igual a zero.' : `(${formatBRL(trackingQuery.data.attributableRevenue)} − ${formatBRL(trackingQuery.data.adSpend)}) ÷ investimento`}</span>
              </div>
              <div className="rounded-lg border border-border p-3">
                <span className="text-xs text-muted-foreground">Confiabilidade da atribuição</span>
                <strong className="mt-1 block text-base tabular-nums">{trackingQuery.data.attributionCoverage.trackingRate == null ? 'Não calculável' : `${formatDecimal(trackingQuery.data.attributionCoverage.trackingRate, 1)}%`}</strong>
                <span className="mt-1 block text-[11px] text-muted-foreground">{trackingQuery.data.attributionCoverage.trackedConversations} de {trackingQuery.data.attributionCoverage.conversations} conversas rastreadas</span>
              </div>
            </div>
            <div className="mt-3 grid gap-3 lg:grid-cols-2">
              <div className="space-y-2 rounded-lg border border-border p-3">
                <h3 className="flex items-center gap-2 text-xs font-semibold"><Megaphone className="h-4 w-4 text-primary" />Decisões sugeridas</h3>
                <div className="flex gap-2 text-[11px]"><TrendingUp className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#0b7435]" /><p><strong>Aumentar:</strong> {marketingIntelligence.increaseCandidate ? `${marketingIntelligence.increaseCandidate.campaignName}, após validar capacidade de atendimento. Evidência: ${marketingIntelligence.increaseCandidate.clients} clientes e ROAS ${formatDecimal(marketingIntelligence.increaseCandidate.roas ?? 0, 2)}x.` : 'nenhuma campanha atingiu amostra e retorno mínimos para recomendar aumento.'}</p></div>
                <div className="flex gap-2 text-[11px]"><TrendingDown className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#b94a48]" /><p><strong>Revisar ou reduzir:</strong> {marketingIntelligence.reviewCandidate ? `${marketingIntelligence.reviewCandidate.campaignName}. Evidência: ${marketingIntelligence.reviewCandidate.leads} leads, ${marketingIntelligence.reviewCandidate.clients} clientes e ${formatBRL(marketingIntelligence.reviewCandidate.adSpend)} investidos.` : 'nenhuma campanha atingiu gasto e amostra mínimos sem conversão.'}</p></div>
                <div className="flex gap-2 text-[11px]"><Lightbulb className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" /><p><strong>Explorar outros canais:</strong> {marketingIntelligence.exploreNewChannels ? `sim. ${formatDecimal((marketingIntelligence.topChannelShare ?? 0) * 100, 1)}% dos leads rastreados estão concentrados no principal canal.` : `ainda não há base suficiente ou concentração acima de 70% com ao menos 10 leads em 7 dias.`}</p></div>
              </div>
              <div className="space-y-2 rounded-lg border border-border p-3">
                <h3 className="text-xs font-semibold">Saturação e desgaste</h3>
                {marketingIntelligence.fatigueCandidate ? <p className="text-[11px]"><strong>Sinal de desgaste em campanha:</strong> {marketingIntelligence.fatigueCandidate.displayName}. O CTR caiu pelo menos 20% contra o período anterior, com mil ou mais impressões e sem crescimento de contratos.</p> : <p className="text-[11px] text-muted-foreground">Nenhuma campanha atingiu os critérios combinados de queda de CTR, volume de impressões e conversão para sinalizar desgaste.</p>}
                <p className="rounded-md bg-secondary p-2 text-[11px] text-muted-foreground"><strong>Criativos:</strong> saturação não pode ser afirmada com a base atual. É necessária série temporal por criativo com frequência, impressões, CTR e conversão. O sistema não usa idade do anúncio como substituto dessa prova.</p>
                {!canViewIntegrations && <p className="text-[11px] text-muted-foreground">Seu perfil não possui acesso aos dados do Gestor de Anúncios; a comparação de desgaste não foi executada.</p>}
                {(adsCurrentQuery.isError || adsPreviousQuery.isError) && <p className="text-[11px] text-[#a53e3c]">Comparação de mídia indisponível; a análise de desgaste ficou suspensa.</p>}
              </div>
            </div>
          </>
        )}
      </KlmPanel>

      <div className="grid gap-4 xl:grid-cols-3">
        <KlmPanel className="p-4">
          <KlmSectionTitle title="Implantação da conta" description="O que falta para aproveitar a operação completa" />
          <div className="mt-4 space-y-2">
            {pendingOnboarding.length ? pendingOnboarding.map((step) => <button key={step.key} type="button" className="flex w-full items-center justify-between gap-3 rounded-lg border border-border p-3 text-left hover:border-primary" onClick={() => navigate(step.path)}><span className="flex min-w-0 items-center gap-2 text-xs font-medium"><span className="h-2 w-2 shrink-0 rounded-full bg-primary" />{step.label}</span><ArrowRight className="h-3.5 w-3.5 shrink-0" /></button>) : <div className="flex items-center gap-2 rounded-lg border border-emerald-600/25 bg-emerald-500/[0.06] p-3 text-xs"><CheckCircle2 className="h-4 w-4 text-[#0b7435]" />Etapas essenciais concluídas.</div>}
          </div>
        </KlmPanel>
        <KlmPanel className="p-4">
          <KlmSectionTitle title="Intervenção humana" description="Decisões e tarefas que não devem ficar paradas" />
          <div className="mt-4 grid grid-cols-2 gap-2">
            <button type="button" className="rounded-lg border border-border p-3 text-left hover:border-primary" onClick={() => navigate('/app/aprovacoes')}><span className="text-xs text-muted-foreground">Aprovações</span><strong className="mt-1 block text-xl tabular-nums">{pendingApprovals}</strong></button>
            <button type="button" className="rounded-lg border border-border p-3 text-left hover:border-primary" onClick={() => navigate('/app/conversas')}><span className="text-xs text-muted-foreground">Follow-ups vencidos</span><strong className="mt-1 block text-xl tabular-nums">{dueFollowUps}</strong></button>
          </div>
          <Button variant="outline" className="mt-3 h-8 w-full text-xs" onClick={() => navigate('/app/atividade')}>Ver todas as tarefas pendentes</Button>
        </KlmPanel>
        <KlmPanel className="p-4">
          <KlmSectionTitle title="Suporte e evolução" description="Ajuda operacional e canal direto para melhorar o produto" />
          <div className="mt-4 space-y-2">
            <Button variant="outline" className="h-9 w-full justify-start text-xs" onClick={openSupport}><CalendarClock className="mr-2 h-4 w-4 text-primary" />Agendar suporte</Button>
            <Button variant="outline" className="h-9 w-full justify-start text-xs" onClick={openSupport}><MessageSquareText className="mr-2 h-4 w-4 text-primary" />Dar feedback</Button>
            <Button variant="outline" className="h-9 w-full justify-start text-xs" onClick={() => navigate('/app/conhecimento')}><Lightbulb className="mr-2 h-4 w-4 text-primary" />Consultar orientações</Button>
          </div>
        </KlmPanel>
      </div>

      {(() => {
        const pipelineBlock = (
        <KlmPanel key="pipeline" className="p-4">
          <KlmSectionTitle title="Funil de conversão" description="Snapshot atual · etapas atualizadas" />
          {funnelQuery.isError && <p className="mt-4 text-xs text-[#a53e3c]">Não foi possível carregar o funil local.</p>}
          {funnelQuery.isSuccess && funnelConcentration && <p className="mt-3 text-xs font-medium text-[#0057d8]">Maior concentração atual: {funnelConcentration.label}, com {funnelConcentration.value} caso{funnelConcentration.value === 1 ? '' : 's'}.</p>}
          <div className="mt-3 space-y-2.5">
            {funnelQuery.isPending && Array.from({ length: 4 }).map((_, index) => (
              <div key={index} className="space-y-1.5">
                <div className="flex items-center justify-between"><Skeleton className="h-3 w-24" /><Skeleton className="h-3 w-6" /></div>
                <Skeleton className="h-1.5 w-full rounded-full" />
              </div>
            ))}
            {funnel.map((item, index) => (
              <button key={item.id} className="block w-full text-left" onClick={() => navigate(`/app/funil?${new URLSearchParams({ stage: item.id }).toString()}`)}>
                <div className="mb-1 flex items-center justify-between gap-2 text-xs"><span className="min-w-0 truncate font-semibold">{item.label}</span><span className="shrink-0 font-semibold tabular-nums">{item.value}</span></div>
                <KlmProgress value={item.progress} tone={index === 3 || index === 5 ? 'green' : 'gold'} />
              </button>
            ))}
            {funnelQuery.isSuccess && !funnel.length && (
              <div className="flex flex-col items-center gap-2 py-6 text-center">
                <FileText className="h-6 w-6 text-muted-foreground" />
                <p className="text-xs text-muted-foreground">Nenhum caso está no funil ainda.</p>
                <Button size="sm" variant="outline" className="h-8 text-xs" onClick={() => navigate('/app/funil')}>Criar primeiro caso</Button>
              </div>
            )}
          </div>
        </KlmPanel>
        );

        const channelsBlock = (
        <KlmPanel key="channels" className="p-4">
          <KlmSectionTitle title="Canais de aquisição" description={`Leads e CAC · ${period.label}`} />
          <div className="mt-4 space-y-3">
            {trackingQuery.isPending && Array.from({ length: 3 }).map((_, index) => (
              <div key={index} className="space-y-1.5">
                <div className="flex items-center justify-between gap-2"><Skeleton className="h-3 w-28" /><Skeleton className="h-3 w-20" /></div>
                <Skeleton className="h-1.5 w-full rounded-full" />
              </div>
            ))}
            {channels.map((channel) => (
              <button
                key={channel.raw.channel}
                className="block w-full text-left"
                onClick={() => navigate(`/app/rastreamento?${new URLSearchParams({ channel: channel.raw.channel, period: String(period.days), tab: 'canais' }).toString()}`)}
              >
                <div className="mb-1 flex items-center justify-between gap-2 text-xs"><span className="min-w-0 truncate font-semibold">{channel.label}</span><span className="shrink-0 text-right tabular-nums text-[#0057d8]">{channel.leads} lead{channel.leads === 1 ? '' : 's'} · CAC {channel.cac == null ? '—' : formatBRL(channel.cac)}</span></div>
                <KlmProgress value={channel.progress} tone={channel.raw.clients > 0 ? 'green' : 'gold'} />
              </button>
            ))}
            {trackingQuery.isError && <p className="text-xs text-[#a53e3c]">Não foi possível carregar os canais.</p>}
            {trackingQuery.isSuccess && !channels.length && (
              <div className="flex flex-col items-center gap-2 py-4 text-center">
                <p className="text-xs text-muted-foreground">Nenhum canal possui eventos neste período.</p>
                <Button size="sm" variant="outline" className="h-8 text-xs" onClick={() => navigate('/app/rastreamento')}>Conectar um canal</Button>
              </div>
            )}
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button size="sm" className="h-8 bg-[#1a9948] text-xs text-white hover:bg-[#0b7435]" disabled={!canManageIntegrations || !googleChannel || recommendationMutation.isPending} title={!canManageIntegrations ? 'Permissão integrations.manage necessária' : 'Registrar recomendação local'} onClick={() => googleChannel && recommendationMutation.mutate({ label: 'Sugestão de aumento em Search', actionType: 'marketing_channel_budget_increase', channel: googleChannel })}>
              {recommendationMutation.isPending ? <LoaderCircle className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" />} Sugerir aumento em Search
            </Button>
            <Button size="sm" variant="outline" className="h-8 text-xs" disabled={!canManageIntegrations || !tiktokChannel || recommendationMutation.isPending} title={!canManageIntegrations ? 'Permissão integrations.manage necessária' : 'Registrar recomendação local'} onClick={() => tiktokChannel && recommendationMutation.mutate({ label: 'Revisão sugerida para TikTok', actionType: 'marketing_channel_review', channel: tiktokChannel })}>
              <PauseCircle className="mr-1.5 h-3.5 w-3.5" /> Sugerir revisão do TikTok
            </Button>
          </div>
          {recommendationNotice && <p className="mt-3 rounded-md bg-secondary px-3 py-2 text-xs text-muted-foreground">{recommendationNotice}</p>}
        </KlmPanel>
        );

        const revenueBlock = (
        <KlmPanel key="revenue" className="p-4">
          <KlmSectionTitle title="Receita e previsão" description="Caixa, assinaturas e honorários" />
          <div className="mt-5 text-2xl font-bold tabular-nums text-[#0057d8]">{financeQuery.isSuccess ? formatBRL(financeQuery.data.paidAmount) : '—'}</div>
          <p className="text-xs tabular-nums text-[#0b7435]">{financeQuery.isSuccess ? `recebidos localmente · ${formatBRL(financeQuery.data.totalOpenReceivables)} a receber` : metricState(financeQuery.isPending, financeQuery.isError, 'Sem lançamentos')}</p>
          <div className="mt-5 grid grid-cols-2 gap-2 border-y border-border py-3 text-xs">
            <span className="text-muted-foreground">Em atraso<strong className="mt-1 block truncate text-sm font-semibold tabular-nums text-foreground">{financeQuery.isSuccess ? formatBRL(financeQuery.data.overdueAmount) : '—'}</strong></span>
            <span className="text-muted-foreground">Agendado<strong className="mt-1 block truncate text-sm font-semibold tabular-nums text-foreground">{financeQuery.isSuccess ? formatBRL(financeQuery.data.scheduledAmount) : '—'}</strong></span>
          </div>
          <button className="mt-4 flex w-full items-center justify-between gap-2 border-t border-border pt-3 text-left text-xs text-muted-foreground" onClick={() => navigate('/app/financeiro')}>
            <span className="min-w-0 truncate">{financeQuery.isSuccess ? `${financeQuery.data.pendingReminders} lembrete${financeQuery.data.pendingReminders === 1 ? '' : 's'} de cobrança pendente${financeQuery.data.pendingReminders === 1 ? '' : 's'}` : metricState(financeQuery.isPending, financeQuery.isError, 'Sem dados')}</span>
            <ArrowRight className="h-3.5 w-3.5 shrink-0" />
          </button>
        </KlmPanel>
        );

        const decisionsBlock = (
        <KlmPanel key="decisions" className="p-4">
          <KlmSectionTitle title="Fila de decisões" description="O que precisa de você ou de ajuste humano" action={<Button variant="outline" size="sm" className="h-8 shrink-0 text-xs" onClick={() => navigate('/app/atividade')}>Abrir fila</Button>} />
          <div className="mt-4 space-y-2">
            {dashboardQuery.isPending && Array.from({ length: 2 }).map((_, index) => (
              <Skeleton key={index} className="h-16 w-full rounded-lg" />
            ))}
            {decisions.map((decision) => (
              <button key={decision.title} className={cn('grid w-full grid-cols-[56px_1fr_auto] items-center gap-3 rounded-lg border p-3 text-left transition hover:-translate-y-px', decision.risk ? 'border-[#b94a48]/35 bg-[#b94a48] text-white' : 'border-border bg-card hover:border-primary')} onClick={() => navigate(decision.path)}>
                <span className="text-xs font-semibold">{decision.priority}</span>
                <span className="min-w-0"><span className="block truncate text-sm font-semibold">{decision.title}</span><span className={cn('mt-0.5 block truncate text-xs', decision.risk ? 'text-white/75' : 'text-muted-foreground')}>{decision.meta}</span></span>
                {decision.risk ? <ShieldAlert className="h-4 w-4" /> : <ArrowRight className="h-4 w-4 text-muted-foreground" />}
              </button>
            ))}
            {dashboardQuery.isError && <p className="py-5 text-center text-xs text-[#a53e3c]">Fila de decisões indisponível.</p>}
            {dashboardQuery.isSuccess && !decisions.length && (
              <div className="flex flex-col items-center gap-2 py-5 text-center">
                <CheckCircle2 className="h-6 w-6 text-[#0b7435]" />
                <p className="text-xs text-muted-foreground">Nenhuma decisão pendente. Tudo em dia.</p>
              </div>
            )}
          </div>
        </KlmPanel>
        );

        const reportsBlock = (
        <KlmPanel key="reports" tone="soft" className="p-4">
          <KlmSectionTitle title="Relatórios executivos" description="Leitura consolidada sem abrir cada módulo" />
          <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {reportCards.map(([label, title, meta]) => (
              <button key={label} className="rounded-lg border border-border bg-card p-3 text-left hover:border-primary" onClick={() => navigate(label === 'Aquisição' ? `/app/rastreamento?period=${period.days}&tab=canais` : label === 'Financeiro' ? '/app/financeiro' : '/app/kpis')}>
                <span className="block truncate text-xs text-muted-foreground">{label}</span><span className="mt-1 block truncate text-sm font-semibold">{title}</span><span className="mt-2 block truncate text-xs tabular-nums text-muted-foreground">{meta}</span>
              </button>
            ))}
          </div>
        </KlmPanel>
        );

        const blocksById: Record<(typeof DEFAULT_HOME_BLOCK_ORDER)[number], JSX.Element> = {
          pipeline: pipelineBlock,
          channels: channelsBlock,
          revenue: revenueBlock,
          decisions: decisionsBlock,
          reports: reportsBlock,
        };
        const orderedBlocks = blockOrder.map((id) => blocksById[id]);
        const [firstRow, secondRow] = [orderedBlocks.slice(0, 3), orderedBlocks.slice(3)];

        return (
          <>
            <div className="grid gap-4 xl:grid-cols-3">{firstRow}</div>
            <div className="grid gap-4 xl:grid-cols-2">{secondRow}</div>
          </>
        );
      })()}

      <div className="flex flex-col gap-3 border-t border-border pt-4 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
        <span className="min-w-0">Etapas e canais abrem as respectivas visões com os filtros exibidos; os demais atalhos abrem o módulo correspondente.</span>
        <div className="flex shrink-0 gap-2"><Button variant="outline" size="sm" className="h-8 text-xs" onClick={() => navigate('/app/kpis')}>Ver relatórios</Button><Button size="sm" className="h-8 bg-[#031121] text-xs text-white hover:bg-[#031121]" onClick={() => navigate('/app/funil')}>Ver funil</Button></div>
      </div>

      <Dialog open={reportOpen} onOpenChange={setReportOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader><DialogTitle>Relatório executivo</DialogTitle><DialogDescription>Baixe um snapshot com os dados atuais do sistema.</DialogDescription></DialogHeader>
          <div className="rounded-lg border border-border bg-secondary p-4 text-xs">
            <div className="flex items-center justify-between"><span>Período de aquisição</span><KlmPill>{period.label}</KlmPill></div>
            <div className="mt-3 flex items-center justify-between"><span>Fonte</span><KlmPill tone={reportReady ? 'green' : hasQueryError ? 'red' : 'gold'}>{reportReady ? 'Dados atualizados' : hasQueryError ? 'Consulta indisponível' : 'Carregando'}</KlmPill></div>
            <div className="mt-3 flex items-center justify-between"><span>Envio externo</span><KlmPill tone="red">Bloqueado</KlmPill></div>
          </div>
          {reportNotice && <p aria-live="polite" className="rounded-md border border-primary/30 bg-primary/10 px-3 py-2 text-xs">{reportNotice}</p>}
          <DialogFooter className="flex-col gap-2 sm:flex-col sm:items-stretch">
            <Button className="w-full bg-[#031121] text-white hover:bg-[#031121]" disabled={!reportReady} onClick={() => exportReport('executive')}>
              <FileText className="mr-2 h-4 w-4" />Relatório executivo (PDF)
            </Button>
            <div className="flex items-center justify-between gap-2">
              <Button variant="outline" onClick={() => setReportOpen(false)}>Cancelar</Button>
              <div className="flex gap-2"><Button variant="outline" disabled={!reportReady} onClick={() => exportReport('json')}><Download className="mr-2 h-4 w-4" />Baixar JSON</Button><Button variant="outline" disabled={!reportReady} onClick={() => exportReport('csv')}><Download className="mr-2 h-4 w-4" />Baixar CSV</Button></div>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
