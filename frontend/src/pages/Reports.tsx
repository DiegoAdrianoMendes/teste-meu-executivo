import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertCircle, Lightbulb, TrendingUp } from 'lucide-react';
import { SectionHeader } from '@/components/common/SectionHeader';
import { KPIStatCard } from '@/components/common/KPIStatCard';
import { useAppStore } from '@/store/app-context';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts';
import { fetchReportsSummary } from '@/services/reports';
import { fetchCampaigns } from '@/services/campaigns';
import type { MatterCard, ReportChartPoint, ReportInsight, ReportMetric, ReportsSummaryData, ClientClassification, KanbanColumnId } from '@/types';
import { formatBRL } from '@/lib/formatters';

const classColors: Record<ClientClassification, string> = {
  Premium: 'hsl(38 92% 50%)',
  Forte: 'hsl(217 50% 45%)',
  Bom: 'hsl(142 50% 40%)',
  Frio: 'hsl(220 9% 46%)',
};

const platformLabels: Record<string, string> = {
  meta_ads: 'Meta Ads',
  google_ads: 'Google Ads',
  tiktok_ads: 'TikTok Ads',
  instagram: 'Instagram',
  messenger: 'Messenger',
  site: 'Site',
};

const phaseLabels: Record<string, string> = {
  entrada: 'Entrada',
  qualificacao: 'Qualificação',
  proposta_fechamento: 'Proposta',
  coleta_dados: 'Coleta',
};

const phaseColors: Record<string, string> = {
  entrada: 'hsl(220 9% 46%)',
  qualificacao: 'hsl(199 89% 48%)',
  proposta_fechamento: 'hsl(262 50% 45%)',
  coleta_dados: 'hsl(38 92% 50%)',
};

const docStatusLabels: Record<string, string> = {
  aguardando: 'Aguardando',
  parcial: 'Parcial',
  invalido: 'Inválido',
  completo: 'Completo',
};

function buildFallbackSummary(cards: MatterCard[]): ReportsSummaryData {
  const now = new Date();
  const in7d = new Date(now.getTime() + 7 * 86400000);
  const threeDaysAgo = new Date(now.getTime() - 3 * 86400000);

  const total = cards.length;
  const qualified = cards.filter((card) =>
    ['qualificacao', 'proposta_fechamento', 'coleta_dados'].includes(card.columnId),
  ).length;
  const proposals = cards.filter((card) => card.columnId === 'proposta_fechamento').length;
  const collection = cards.filter((card) => card.columnId === 'coleta_dados').length;
  const completed = cards.filter((card) =>
    ['docs_completos', 'checklist_final'].includes(card.microStatus),
  ).length;
  const aiTasks = cards.reduce((sum, card) => sum + card.tasks.filter((task) => task.isAiGenerated).length, 0);
  const totalTasks = cards.reduce((sum, card) => sum + card.tasks.length, 0);
  const closeSignalCount = cards.filter((card) => ['proposta_fechamento', 'coleta_dados'].includes(card.columnId)).length;
  const closedRevenue = cards.reduce((sum, card) => {
    const amount = card.honorariumEstimate?.totalEstimatedValue ?? card.revenueProjection?.projectedRevenue ?? 0;
    return card.columnId === 'coleta_dados' ? sum + amount : sum;
  }, 0);

  const kpis: ReportMetric[] = [
    { id: 'total_matters', label: 'Total de Leads', value: total, unit: 'leads', trend: 'stable' },
    {
      id: 'lead_to_qualification',
      label: 'Qualificação',
      value: total > 0 ? Number(((qualified / total) * 100).toFixed(1)) : 0,
      unit: '%',
      trend: 'up',
    },
    {
      id: 'qualification_to_proposal',
      label: 'Avanço p/ Proposta',
      value: qualified > 0 ? Number(((proposals / qualified) * 100).toFixed(1)) : 0,
      unit: '%',
      trend: 'stable',
    },
    {
      id: 'proposal_to_collection',
      label: 'Fechou e Entrou na Coleta',
      value: proposals > 0 ? Number(((collection / proposals) * 100).toFixed(1)) : 0,
      unit: '%',
      trend: 'stable',
    },
    {
      id: 'collection_completion',
      label: 'Checklist Completo',
      value: collection > 0 ? Number(((completed / collection) * 100).toFixed(1)) : 0,
      unit: '%',
      trend: 'stable',
    },
    {
      id: 'open_tasks',
      label: 'Pendências Abertas',
      value: cards.reduce((sum, card) => sum + card.tasks.filter((task) => task.status !== 'done').length, 0),
      unit: 'tarefas',
    },
    { id: 'pending_approvals', label: 'Aprovações Pendentes', value: cards.filter((card) => card.needsApproval).length, unit: 'itens' },
    {
      id: 'avg_doc_score',
      label: 'Score Médio de Docs',
      value: total > 0 ? Math.round(cards.reduce((sum, card) => sum + card.docScore, 0) / total) : 0,
      unit: '%',
    },
    {
      id: 'ai_automation_rate',
      label: 'Taxa de Automação IA',
      value: totalTasks > 0 ? Number(((aiTasks / totalTasks) * 100).toFixed(1)) : 0,
      unit: '%',
      trend: 'up',
    },
    {
      id: 'close_signal_rate',
      label: 'Sinal de Fechamento',
      value: total > 0 ? Number(((closeSignalCount / total) * 100).toFixed(1)) : 0,
      unit: '%',
      trend: 'up',
    },
    {
      id: 'closed_revenue',
      label: 'Ganho Fechado',
      value: closedRevenue,
      unit: 'BRL',
      trend: 'up',
    },
    { id: 'blocked_matters', label: 'Leads Bloqueados', value: cards.filter((card) => card.isBlocked).length, unit: 'leads', trend: 'down' },
  ];

  const casesByPhase = Object.entries(phaseLabels)
    .map(([columnId, label]) => ({
      name: label,
      count: cards.filter((card) => card.columnId === columnId).length,
      color: phaseColors[columnId] ?? 'hsl(220 9% 46%)',
    }))
    .filter((item) => item.count > 0);

  const casesByClassification = Object.entries(
    cards.reduce<Record<string, number>>((acc, card) => {
      acc[card.classification] = (acc[card.classification] ?? 0) + 1;
      return acc;
    }, {}),
  ).map(([name, count]) => ({
    name,
    count,
    color: classColors[name as ClientClassification] ?? 'hsl(220 9% 46%)',
  }));

  const casesByResponsible = Object.entries(
    cards.reduce<Record<string, number>>((acc, card) => {
      const name = card.responsibleHuman || 'Não definido';
      acc[name] = (acc[name] ?? 0) + 1;
      return acc;
    }, {}),
  ).map(([name, count]) => ({ name, count }));

  const casesByDemand = Object.entries(
    cards.reduce<Record<string, number>>((acc, card) => {
      const key = card.demandType.split('+')[0].trim().substring(0, 18) || 'Não informado';
      acc[key] = (acc[key] ?? 0) + 1;
      return acc;
    }, {}),
  )
    .map(([name, count]) => ({ name, count }))
    .sort((left, right) => right.count - left.count)
    .slice(0, 8);

  const casesBySource = Object.entries(
    cards.reduce<Record<string, number>>((acc, card) => {
      const key = card.leadSource || 'Não informado';
      acc[key] = (acc[key] ?? 0) + 1;
      return acc;
    }, {}),
  ).map(([name, count]) => ({ name, count }));

  const documentsByStatus = Object.entries(
    cards.reduce<Record<string, number>>((acc, card) => {
      card.documents.forEach((document) => {
        acc[document.status] = (acc[document.status] ?? 0) + 1;
      });
      return acc;
    }, {}),
  ).map(([name, count]) => ({ name, count }));

  const funnelOverview: ReportChartPoint[] = [
    { name: 'Entrada', count: total },
    { name: 'Qualificados', count: qualified },
    { name: 'Proposta', count: proposals },
    { name: 'Coleta', count: collection },
    { name: 'Checklist OK', count: completed },
  ];

  const tasksHumanVsAi: ReportChartPoint[] = [
    { name: 'Humano', count: Math.max(totalTasks - aiTasks, 0), color: '#3B82F6' },
    { name: 'IA', count: aiTasks, color: '#8B5CF6' },
  ];

  const conversationSignals: ReportChartPoint[] = [
    { name: 'Fechamento alto', count: cards.filter((card) => card.columnId === 'coleta_dados').length },
    { name: 'Fechamento médio', count: cards.filter((card) => card.columnId === 'proposta_fechamento').length },
    { name: 'Fechamento baixo', count: cards.filter((card) => ['entrada', 'qualificacao'].includes(card.columnId)).length },
  ];

  const revenueBySource = Object.entries(
    cards.reduce<Record<string, number>>((acc, card) => {
      if (card.columnId !== 'coleta_dados') return acc;
      const source = card.leadSource || 'Não informado';
      const revenue = card.honorariumEstimate?.totalEstimatedValue ?? card.revenueProjection?.projectedRevenue ?? 0;
      if (!revenue) return acc;
      acc[source] = (acc[source] ?? 0) + revenue;
      return acc;
    }, {}),
  ).map(([name, value]) => ({ name, value }));

  const adPlatformConversions = Object.entries(
    cards.reduce<Record<string, { conversions: number; conversionValue: number }>>((acc, card) => {
      if (card.columnId !== 'coleta_dados') return acc;
      const source = card.leadSource || 'Não informado';
      const haystack = source.toLowerCase();
      const platforms = [
        ...(haystack.includes('meta') || haystack.includes('instagram') || haystack.includes('facebook') ? ['meta'] : []),
        ...(haystack.includes('google') || haystack.includes('ads') ? ['google'] : []),
      ];
      const revenue = card.honorariumEstimate?.totalEstimatedValue ?? card.revenueProjection?.projectedRevenue ?? 0;
      platforms.forEach((platform) => {
        const key = `${platform}:${source}`;
        if (!acc[key]) acc[key] = { conversions: 0, conversionValue: 0 };
        acc[key].conversions += 1;
        acc[key].conversionValue += revenue;
      });
      return acc;
    }, {}),
  ).map(([key, payload]) => {
    const [platform, source] = key.split(':');
    return {
      platform,
      source,
      conversions: payload.conversions,
      conversionValue: payload.conversionValue,
      currency: 'BRL',
      eventName: 'qualified_onboarding_closed',
    };
  });

  const insights: ReportInsight[] = [
    {
      id: 'fallback-response-gap',
      title: 'Há leads esfriando por falta de resposta rápida',
      summary: `${cards.filter((card) => new Date(card.lastInteraction) < threeDaysAgo).length} cards estão sem interação recente. Isso tende a derrubar avanço para proposta e coleta.`,
      priority: 'medium',
      recommendedAction: 'Ativar follow-up automático para cards sem resposta acima de 72 horas.',
      source: 'rule_based',
      metricRefs: ['lead_to_qualification'],
    },
    {
      id: 'fallback-deadline-focus',
      title: 'Existem cards com urgência operacional no curto prazo',
      summary: `${cards.filter((card) => card.nextDeadline && new Date(card.nextDeadline) <= in7d).length} cards têm prazo próximo. Vale priorizar cobrança documental e organização interna.`,
      priority: 'medium',
      recommendedAction: 'Criar fila operacional por prazo e microstatus para secretaria e administrativo.',
      source: 'rule_based',
      metricRefs: ['open_tasks', 'collection_completion'],
    },
  ];

  return {
    kpis,
    casesByPhase,
    casesByClassification,
    casesByResponsible,
    casesByDemand,
    casesBySource,
    conversationSignals,
    documentsByStatus,
    funnelOverview,
    revenueBySource,
    tasksHumanVsAi,
    adPlatformConversions,
    insights,
  };
}

function metricValue(summary: ReportsSummaryData, metricId: string): number {
  return summary.kpis.find((metric) => metric.id === metricId)?.value ?? 0;
}

function phaseFilterValue(pointName: string): KanbanColumnId | null {
  const entry = Object.entries(phaseLabels).find(([, label]) => label === pointName);
  return (entry?.[0] as KanbanColumnId | undefined) ?? null;
}

function priorityBadgeVariant(priority: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (priority === 'high') return 'destructive';
  if (priority === 'medium') return 'secondary';
  return 'outline';
}

export default function Reports() {
  const { state, navigateWithFilter } = useAppStore();

  const fallbackSummary = useMemo(() => buildFallbackSummary(state.cards), [state.cards]);

  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: ['reports-summary', state.activeWorkspace?.id],
    queryFn: fetchReportsSummary,
    enabled: state.isAuthenticated && Boolean(state.activeWorkspace),
    staleTime: 60_000,
    retry: 1,
  });

  const { data: campaigns = [] } = useQuery({
    queryKey: ['reports-whatsapp-campaigns', state.activeWorkspace?.id],
    queryFn: fetchCampaigns,
    enabled: state.isAuthenticated && Boolean(state.activeWorkspace),
    staleTime: 60_000,
    retry: 1,
  });

  const summary = data ?? fallbackSummary;
  const campaignMetrics = useMemo(
    () =>
      campaigns.reduce(
        (acc, campaign) => {
          acc.total += 1;
          acc.sent += campaign.sentCount;
          acc.delivered += campaign.deliveredCount;
          acc.read += campaign.readCount;
          acc.failed += campaign.failedCount;
          return acc;
        },
        { total: 0, sent: 0, delivered: 0, read: 0, failed: 0 },
      ),
    [campaigns],
  );
  const qualificationRate = metricValue(summary, 'lead_to_qualification');
  const collectionCompletion = metricValue(summary, 'collection_completion');
  const closedRevenue = metricValue(summary, 'closed_revenue');

  const classificationChart = summary.casesByClassification.map((item) => ({
    ...item,
    value: item.count,
    color: item.color ?? classColors[item.name as ClientClassification] ?? 'hsl(220 9% 46%)',
  }));

  return (
    <div className="space-y-6 max-w-7xl">
      <SectionHeader
        title="Relatórios"
        description="KPIs operacionais, gargalos do onboarding e insights acionáveis por workspace"
        action={isFetching ? <Badge variant="outline">Atualizando</Badge> : undefined}
      />

      {error && (
        <Card className="border-amber-200 bg-amber-50/70">
          <CardContent className="flex items-start gap-3 py-4 text-sm text-amber-900">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <div>
              <p className="font-medium">Usando fallback local para os relatórios.</p>
              <p className="text-amber-800/90">
                A API de métricas não respondeu agora, então a tela foi mantida com base no estado atual da SPA.
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2 sm:gap-3">
        {summary.kpis.map((metric) => (
          <KPIStatCard key={metric.id} metric={metric} />
        ))}
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base font-medium">Campanhas oficiais no WhatsApp</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-3 md:grid-cols-5">
          {[
            { label: 'Campanhas', value: campaignMetrics.total },
            { label: 'Enviadas', value: campaignMetrics.sent },
            { label: 'Entregues', value: campaignMetrics.delivered },
            { label: 'Lidas', value: campaignMetrics.read },
            { label: 'Falhas', value: campaignMetrics.failed },
          ].map((item) => (
            <div key={item.label} className="rounded-xl border p-4">
              <p className="text-xs uppercase tracking-wide text-muted-foreground">{item.label}</p>
              <p className="mt-2 text-2xl font-bold tabular-nums">{item.value}</p>
            </div>
          ))}
        </CardContent>
      </Card>

      {isLoading && !data && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <Card><CardContent className="py-10 text-sm text-muted-foreground">Carregando funil e distribuição operacional...</CardContent></Card>
          <Card><CardContent className="py-10 text-sm text-muted-foreground">Carregando indicadores de documentos e origem...</CardContent></Card>
          <Card><CardContent className="py-10 text-sm text-muted-foreground">Carregando insights do workspace...</CardContent></Card>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2" style={{ boxShadow: 'var(--shadow-card)' }}>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base font-medium">
              <TrendingUp className="h-4 w-4" />
              Funil de onboarding
            </CardTitle>
          </CardHeader>
          <CardContent className="px-2 sm:px-6">
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={summary.funnelOverview}>
                <XAxis dataKey="name" tick={{ fontSize: 10 }} interval={0} angle={-15} textAnchor="end" height={48} />
                <YAxis tick={{ fontSize: 10 }} allowDecimals={false} width={28} />
                <Tooltip contentStyle={{ fontSize: 11 }} />
                <Bar dataKey="count" fill="hsl(217 50% 22%)" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
            <div className="grid gap-2 pt-3 text-xs text-muted-foreground tabular-nums sm:grid-cols-2">
              <div className="rounded-md border p-3">
                <p className="font-medium text-foreground">Qualificação atual</p>
                <p>{qualificationRate}% dos leads estão passando da entrada para qualificação.</p>
              </div>
              <div className="rounded-md border p-3">
                <p className="font-medium text-foreground">Checklist completo</p>
                <p>{collectionCompletion}% dos cards em coleta já chegaram com checklist fechado.</p>
              </div>
              <div className="rounded-md border p-3">
                <p className="font-medium text-foreground">Ganho fechado do recorte</p>
                <p>{formatBRL(closedRevenue)} em cards já convertidos para onboarding.</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card style={{ boxShadow: 'var(--shadow-card)' }}>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base font-medium">
              <Lightbulb className="h-4 w-4" />
              Insights operacionais
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {summary.insights.length === 0 && (
              <div className="rounded-md border p-3 text-sm text-muted-foreground">
                Nenhum insight gerado neste momento.
              </div>
            )}
            {summary.insights.map((insight) => (
              <div key={insight.id} className="rounded-lg border p-3 space-y-2">
                <div className="flex items-start justify-between gap-3">
                  <p className="text-sm font-semibold leading-5">{insight.title}</p>
                  <Badge variant={priorityBadgeVariant(insight.priority)}>
                    {insight.priority === 'high' ? 'Alta' : insight.priority === 'medium' ? 'Média' : 'Baixa'}
                  </Badge>
                </div>
                <p className="text-sm text-muted-foreground">{insight.summary}</p>
                {insight.recommendedAction && (
                  <div className="rounded-md bg-muted/50 px-3 py-2 text-xs text-foreground">
                    <span className="font-medium">Ação sugerida:</span> {insight.recommendedAction}
                  </div>
                )}
                <div className="flex items-center justify-between text-2xs text-muted-foreground">
                  <span>Fonte: {insight.source === 'ai' ? 'IA' : 'Regra operacional'}</span>
                  {insight.metricRefs.length > 0 && <span>{insight.metricRefs.length} métricas relacionadas</span>}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card style={{ boxShadow: 'var(--shadow-card)' }}>
          <CardHeader className="pb-2">
            <CardTitle className="text-base font-medium">Cards por fase</CardTitle>
          </CardHeader>
          <CardContent className="px-2 sm:px-6">
            <ResponsiveContainer width="100%" height={220}>
              <BarChart
                data={summary.casesByPhase}
                onClick={(dataPoint) => {
                  const pointName = dataPoint?.activePayload?.[0]?.payload?.name;
                  const columnId = typeof pointName === 'string' ? phaseFilterValue(pointName) : null;
                  if (columnId) {
                    navigateWithFilter({ columnId: [columnId] });
                  }
                }}
              >
                <XAxis dataKey="name" tick={{ fontSize: 10 }} interval={0} angle={-20} textAnchor="end" height={45} />
                <YAxis tick={{ fontSize: 10 }} allowDecimals={false} width={28} />
                <Tooltip contentStyle={{ fontSize: 11 }} />
                <Bar dataKey="count" fill="hsl(217 50% 22%)" radius={[4, 4, 0, 0]} cursor="pointer" />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card style={{ boxShadow: 'var(--shadow-card)' }}>
          <CardHeader className="pb-2">
            <CardTitle className="text-base font-medium">Cards por classificação</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie
                  data={classificationChart}
                  cx="50%"
                  cy="50%"
                  innerRadius={42}
                  outerRadius={74}
                  paddingAngle={4}
                  dataKey="value"
                  label={({ name, value }) => `${name}: ${value}`}
                  style={{ fontSize: 10, cursor: 'pointer' }}
                  onClick={(_, idx) => {
                    const item = classificationChart[idx];
                    if (item) {
                      navigateWithFilter({ classification: [item.name as ClientClassification] });
                    }
                  }}
                >
                  {classificationChart.map((entry, index) => (
                    <Cell key={`${entry.name}-${index}`} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip contentStyle={{ fontSize: 11 }} />
              </PieChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card style={{ boxShadow: 'var(--shadow-card)' }}>
          <CardHeader className="pb-2">
            <CardTitle className="text-base font-medium">Origem dos leads</CardTitle>
          </CardHeader>
          <CardContent className="px-2 sm:px-6">
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={summary.casesBySource}>
                <XAxis dataKey="name" tick={{ fontSize: 10 }} interval={0} angle={-15} textAnchor="end" height={44} />
                <YAxis tick={{ fontSize: 10 }} allowDecimals={false} width={28} />
                <Tooltip contentStyle={{ fontSize: 11 }} />
                <Bar dataKey="count" fill="hsl(142 50% 35%)" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card style={{ boxShadow: 'var(--shadow-card)' }}>
          <CardHeader className="pb-2">
            <CardTitle className="text-base font-medium">Sinais da conversa</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie
                  data={summary.conversationSignals.map((item) => ({ ...item, value: item.count }))}
                  cx="50%"
                  cy="50%"
                  innerRadius={42}
                  outerRadius={74}
                  paddingAngle={4}
                  dataKey="value"
                  label={({ name, value }) => `${name}: ${value}`}
                  style={{ fontSize: 10 }}
                >
                  {summary.conversationSignals.map((entry, index) => (
                    <Cell
                      key={`${entry.name}-${index}`}
                      fill={
                        entry.name.includes('alto')
                          ? 'hsl(142 50% 40%)'
                          : entry.name.includes('médio')
                            ? 'hsl(38 92% 50%)'
                            : 'hsl(220 9% 46%)'
                      }
                    />
                  ))}
                </Pie>
                <Tooltip contentStyle={{ fontSize: 11 }} />
              </PieChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card style={{ boxShadow: 'var(--shadow-card)' }}>
          <CardHeader className="pb-2">
            <CardTitle className="text-base font-medium">Documentos por status</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie
                  data={summary.documentsByStatus.map((item) => ({
                    ...item,
                    name: docStatusLabels[item.name] ?? item.name,
                    value: item.count,
                  }))}
                  cx="50%"
                  cy="50%"
                  innerRadius={42}
                  outerRadius={74}
                  paddingAngle={4}
                  dataKey="value"
                  label={({ name, value }) => `${name}: ${value}`}
                  style={{ fontSize: 10 }}
                >
                  {summary.documentsByStatus.map((entry, index) => (
                    <Cell
                      key={`${entry.name}-${index}`}
                      fill={entry.name === 'completo' ? 'hsl(142 50% 40%)' : entry.name === 'parcial' ? 'hsl(38 92% 50%)' : 'hsl(220 9% 46%)'}
                    />
                  ))}
                </Pie>
                <Tooltip contentStyle={{ fontSize: 11 }} />
              </PieChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card style={{ boxShadow: 'var(--shadow-card)' }}>
          <CardHeader className="pb-2">
            <CardTitle className="text-base font-medium">Cards por responsável</CardTitle>
          </CardHeader>
          <CardContent className="px-2 sm:px-6">
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={summary.casesByResponsible} layout="vertical">
                <XAxis type="number" tick={{ fontSize: 10 }} allowDecimals={false} />
                <YAxis type="category" dataKey="name" tick={{ fontSize: 10 }} width={92} />
                <Tooltip contentStyle={{ fontSize: 11 }} />
                <Bar dataKey="count" fill="hsl(262 50% 45%)" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card style={{ boxShadow: 'var(--shadow-card)' }}>
          <CardHeader className="pb-2">
            <CardTitle className="text-base font-medium">Tarefas humanas vs IA</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie
                  data={summary.tasksHumanVsAi.map((item) => ({ ...item, value: item.count }))}
                  cx="50%"
                  cy="50%"
                  innerRadius={42}
                  outerRadius={74}
                  paddingAngle={4}
                  dataKey="value"
                  label={({ name, value }) => `${name}: ${value}`}
                  style={{ fontSize: 10 }}
                >
                  {summary.tasksHumanVsAi.map((entry, index) => (
                    <Cell key={`${entry.name}-${index}`} fill={entry.color ?? (entry.name === 'IA' ? '#8B5CF6' : '#3B82F6')} />
                  ))}
                </Pie>
                <Tooltip contentStyle={{ fontSize: 11 }} />
              </PieChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card style={{ boxShadow: 'var(--shadow-card)' }} className="lg:col-span-2">
          <CardHeader className="pb-2">
            <CardTitle className="text-base font-medium">Ganho fechado por origem</CardTitle>
          </CardHeader>
          <CardContent className="px-2 sm:px-6">
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={summary.revenueBySource}>
                <XAxis dataKey="name" tick={{ fontSize: 10 }} interval={0} angle={-20} textAnchor="end" height={46} />
                <YAxis tick={{ fontSize: 10 }} width={42} />
                <Tooltip
                  contentStyle={{ fontSize: 11 }}
                  formatter={(value: number) => [formatBRL(Number(value)), 'Ganho']}
                />
                <Bar dataKey="value" fill="hsl(160 60% 35%)" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card style={{ boxShadow: 'var(--shadow-card)' }} className="lg:col-span-2">
          <CardHeader className="pb-2">
            <CardTitle className="text-base font-medium">Volume por tipo de demanda</CardTitle>
          </CardHeader>
          <CardContent className="px-2 sm:px-6">
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={summary.casesByDemand}>
                <XAxis dataKey="name" tick={{ fontSize: 10 }} interval={0} angle={-20} textAnchor="end" height={46} />
                <YAxis tick={{ fontSize: 10 }} allowDecimals={false} width={28} />
                <Tooltip contentStyle={{ fontSize: 11 }} />
                <Bar dataKey="count" fill="hsl(142 50% 35%)" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card style={{ boxShadow: 'var(--shadow-card)' }} className="lg:col-span-2">
          <CardHeader className="pb-2">
            <CardTitle className="text-base font-medium">Retorno de conversão pronto para Meta / Google</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {summary.adPlatformConversions.length === 0 && (
              <div className="rounded-md border p-3 text-sm text-muted-foreground">
                Ainda não há conversões fechadas com origem mapeável para Meta ou Google.
              </div>
            )}
            {summary.adPlatformConversions.map((item) => (
              <div key={`${item.platform}-${item.source}`} className="grid gap-2 rounded-md border p-3 text-sm md:grid-cols-5">
                <div>
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">Plataforma</p>
                  <p className="font-medium">{platformLabels[item.platform] ?? item.platform}</p>
                </div>
                <div>
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">Origem</p>
                  <p className="font-medium">{item.source}</p>
                </div>
                <div>
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">Conversões</p>
                  <p className="font-medium tabular-nums">{item.conversions}</p>
                </div>
                <div>
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">Valor</p>
                  <p className="font-medium tabular-nums">{formatBRL(item.conversionValue)}</p>
                </div>
                <div>
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">Evento</p>
                  <p className="font-medium">{item.eventName}</p>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
