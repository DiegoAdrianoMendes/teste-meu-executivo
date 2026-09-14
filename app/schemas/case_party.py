"""
Schemas: CaseParty — Onda 5 (Caso 360 canônico).
Ver app/models/case_party.py e app/services/case_360_service.py.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import Field

from app.models.case_party import CasePartyPole
from app.schemas.base import CamelCaseSchema, WriteSchema


class CasePartyCreate(WriteSchema):
    name: str
    pole: CasePartyPole
    role: Optional[str] = None
    qualification: dict = Field(default_factory=dict)
    notes: Optional[str] = None


class CasePartyRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    legal_demand_id: UUID
    name: str
    pole: CasePartyPole
    role: Optional[str] = None
    qualification: dict
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class CasePartyListRead(CamelCaseSchema):
    items: list[CasePartyRead]
