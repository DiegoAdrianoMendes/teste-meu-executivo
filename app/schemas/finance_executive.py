from datetime import date, datetime
from typing import Any

from app.schemas.base import CamelCaseSchema


class ExecutiveMetric(CamelCaseSchema):
    value: Any = None
    quality: str
    source: str
    definition: str
    unit: str = "BRL"


class ExecutiveAnalyticsRead(CamelCaseSchema):
    generated_at: datetime
    period_start: date
    period_end: date
    arr: ExecutiveMetric
    contracted_revenue: ExecutiveMetric
    gross_margin: ExecutiveMetric
    contribution_margin: ExecutiveMetric
    ebitda: ExecutiveMetric
    burn: ExecutiveMetric
    runway: ExecutiveMetric
    break_even: ExecutiveMetric
    concentration: ExecutiveMetric
    dimensional_profitability: ExecutiveMetric
    cac: ExecutiveMetric
    ltv: ExecutiveMetric
    payback: ExecutiveMetric
