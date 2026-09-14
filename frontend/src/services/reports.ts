import type { AdPlatformConversion, ReportsSummaryData, ReportMetric, ReportChartPoint, ReportInsight, ReportValuePoint } from '@/types';
import { apiFetch } from '@/services/http';

type ApiSummary = Record<string, any>;

function mapMetric(item: any): ReportMetric {
  return {
    id: item.id,
    label: item.label,
    value: item.value,
    previousValue: item.previousValue ?? item.previous_value,
    unit: item.unit,
    trend: item.trend,
  };
}

function mapChart(items: any[] = []): ReportChartPoint[] {
  return items.map((item) => ({
    name: item.name,
    count: item.count,
    color: item.color,
  }));
}

function mapValueChart(items: any[] = []): ReportValuePoint[] {
  return items.map((item) => ({
    name: item.name,
    value: item.value,
    color: item.color,
  }));
}

function mapInsight(items: any[] = []): ReportInsight[] {
  return items.map((item) => ({
    id: item.id,
    title: item.title,
    summary: item.summary,
    priority: item.priority,
    recommendedAction: item.recommendedAction ?? item.recommended_action,
    source: item.source,
    metricRefs: item.metricRefs ?? item.metric_refs ?? [],
  }));
}

function mapConversions(items: any[] = []): AdPlatformConversion[] {
  return items.map((item) => ({
    platform: item.platform,
    source: item.source,
    conversions: item.conversions,
    conversionValue: item.conversionValue ?? item.conversion_value ?? 0,
    currency: item.currency ?? 'BRL',
    eventName: item.eventName ?? item.event_name ?? 'qualified_onboarding_closed',
  }));
}

export async function fetchReportsSummary(): Promise<ReportsSummaryData> {
  const data = await apiFetch<ApiSummary>('/api/v1/reports/metrics');
  return {
    kpis: (data.kpis ?? []).map(mapMetric),
    casesByPhase: mapChart(data.casesByPhase ?? data.cases_by_phase),
    casesByClassification: mapChart(data.casesByClassification ?? data.cases_by_classification),
    casesByResponsible: mapChart(data.casesByResponsible ?? data.cases_by_responsible),
    casesByDemand: mapChart(data.casesByDemand ?? data.cases_by_demand),
    casesBySource: mapChart(data.casesBySource ?? data.cases_by_source),
    conversationSignals: mapChart(data.conversationSignals ?? data.conversation_signals),
    documentsByStatus: mapChart(data.documentsByStatus ?? data.documents_by_status),
    funnelOverview: mapChart(data.funnelOverview ?? data.funnel_overview),
    revenueBySource: mapValueChart(data.revenueBySource ?? data.revenue_by_source),
    tasksHumanVsAi: mapChart(data.tasksHumanVsAi ?? data.tasks_human_vs_ai),
    adPlatformConversions: mapConversions(data.adPlatformConversions ?? data.ad_platform_conversions),
    insights: mapInsight(data.insights),
  };
}
