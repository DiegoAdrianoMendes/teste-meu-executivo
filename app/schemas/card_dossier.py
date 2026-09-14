"""
Schemas do dossiê vivo do card.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelCaseSchema


class CampaignAttributionRead(CamelCaseSchema):
    id: UUID
    source: Optional[str] = None
    medium: Optional[str] = None
    campaign: Optional[str] = None
    content: Optional[str] = None
    term: Optional[str] = None
    channel: Optional[str] = None
    first_touch_at: Optional[str] = None
    last_touch_at: Optional[str] = None
    notes: Optional[str] = None


class RevenueProjectionRead(CamelCaseSchema):
    id: UUID
    estimated_claim_value: Optional[float] = None
    projected_revenue: Optional[float] = None
    success_probability: Optional[float] = None
    confidence_level: Optional[float] = None
    payback_days: Optional[int] = None
    currency: str = "BRL"
    notes: Optional[str] = None
    projection_premises: Dict[str, Any] = Field(default_factory=dict)
    projection_version: int = 0


class HonorariumEstimateRead(CamelCaseSchema):
    id: UUID
    contract_type: Optional[str] = None
    fixed_fee: Optional[float] = None
    quota_litis_percentage: Optional[float] = None
    expected_expenses: Optional[float] = None
    total_estimated_value: Optional[float] = None
    notes: Optional[str] = None


class CardScoreRead(CamelCaseSchema):
    id: UUID
    viability_score: Optional[float] = None
    urgency_score: Optional[float] = None
    evidence_score: Optional[float] = None
    conversion_score: Optional[float] = None
    relationship_score: Optional[float] = None
    overall_score: Optional[float] = None
    explanation: Optional[str] = None


class CardFieldChangeRead(CamelCaseSchema):
    id: UUID
    field_name: str
    old_value: Optional[Dict[str, Any]] = None
    new_value: Optional[Dict[str, Any]] = None
    summary: Optional[str] = None
    change_source: Optional[str] = None
    actor_type: str
    changed_by_user_id: Optional[UUID] = None
    created_at: datetime


class AuditLogRead(CamelCaseSchema):
    id: UUID
    action: str
    entity_type: str = "matter"
    entity_id: UUID
    agent_id: Optional[UUID] = None
    timestamp: datetime
    description: Optional[str] = None
    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None


class ConversationInsightRead(CamelCaseSchema):
    id: UUID
    source_channel: str
    summary: Optional[str] = None
    funnel_signal: Optional[str] = None
    close_intent: Optional[str] = None
    document_readiness: Optional[str] = None
    urgency_level: Optional[str] = None
    sentiment: Optional[str] = None
    trust_level: Optional[str] = None
    no_show_risk: Optional[str] = None
    objections: list[str] = Field(default_factory=list)
    requested_documents: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    recommended_action: Optional[str] = None
    next_best_message: Optional[str] = None
    estimated_conversion_value: Optional[float] = None
    closed_signal: bool = False
    conversion_platforms: list[str] = Field(default_factory=list)
    raw_payload: Dict[str, Any] = Field(default_factory=dict)
    last_message_at: Optional[str] = None
    created_at: datetime
    updated_at: datetime
