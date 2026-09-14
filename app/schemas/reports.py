"""
Schemas Pydantic para Reports e Analytics.

Mapeamento de campos (backend → frontend ReportMetric):
  previous_value   → previousValue
  cases_by_phase   → casesByPhase
  etc.
"""
from typing import List, Optional
from pydantic import Field
from app.schemas.base import CamelCaseSchema


class KPIMetric(CamelCaseSchema):
    """
    Compatível com a interface ReportMetric do frontend:
      id, label, value, previousValue, unit, trend
    """
    id: str
    label: str
    value: float
    previous_value: Optional[float] = None
    unit: Optional[str] = None
    trend: Optional[str] = None  # up | down | stable


class ChartDataPoint(CamelCaseSchema):
    name: str
    count: int
    color: Optional[str] = None


class ValueDataPoint(CamelCaseSchema):
    name: str
    value: float
    color: Optional[str] = None


class ReportInsightRead(CamelCaseSchema):
    id: str
    title: str
    summary: str
    priority: str
    recommended_action: Optional[str] = None
    source: str = "ai"
    metric_refs: List[str] = Field(default_factory=list)


class AdPlatformConversionRead(CamelCaseSchema):
    platform: str
    source: str
    conversions: int
    conversion_value: float
    currency: str = "BRL"
    event_name: str = "qualified_onboarding_closed"


class ReportsSummary(CamelCaseSchema):
    kpis: List[KPIMetric]
    cases_by_phase: List[ChartDataPoint]
    cases_by_classification: List[ChartDataPoint]
    cases_by_responsible: List[ChartDataPoint]
    cases_by_demand: List[ChartDataPoint]
    cases_by_source: List[ChartDataPoint]
    conversation_signals: List[ChartDataPoint]
    documents_by_status: List[ChartDataPoint]
    funnel_overview: List[ChartDataPoint]
    revenue_by_source: List[ValueDataPoint]
    tasks_human_vs_ai: List[ChartDataPoint]
    ad_platform_conversions: List[AdPlatformConversionRead]
    insights: List[ReportInsightRead]


class KPIAggregateBucketRead(CamelCaseSchema):
    key: str
    label: str
    value: float
    unit: str
    emphasis: str = "neutral"


class KPIAggregateRead(CamelCaseSchema):
    workspace_id: str
    generated_at: str
    pipeline: List[KPIAggregateBucketRead] = Field(default_factory=list)
    financial: List[KPIAggregateBucketRead] = Field(default_factory=list)
    automation: List[KPIAggregateBucketRead] = Field(default_factory=list)
    documents: List[KPIAggregateBucketRead] = Field(default_factory=list)
    conversation: List[KPIAggregateBucketRead] = Field(default_factory=list)
