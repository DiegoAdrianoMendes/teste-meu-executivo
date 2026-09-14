"""
Schemas: Verificação de citações + Revisão adversarial (Onda 6, §9/§3 item 20).
Ver app/models/legal_artifact_verification.py e
app/services/citation_verification_service.py /
app/services/legal_review_service.py.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.schemas.base import CamelCaseSchema


class CitationVerificationRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    legal_demand_id: UUID
    artifact_version_id: UUID
    citation_text: str
    citation_kind: str
    status: str
    source_reference: str | None = None
    trail: dict
    checked_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class CitationVerificationRunRead(CamelCaseSchema):
    artifact_version_id: UUID
    all_verified: bool
    gate_open: bool
    verifications: list[CitationVerificationRead]


class LegalReviewFindingRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    legal_demand_id: UUID
    artifact_version_id: UUID
    severity: str
    kind: str
    description: str
    reference: dict
    created_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class LegalReviewRunRead(CamelCaseSchema):
    artifact_version_id: UUID
    is_protocolable: bool
    journey_advanced: bool
    findings: list[LegalReviewFindingRead]
    # Parecer do revisor sênior nos 3 níveis (crítico/médio/cosmético) e
    # veredito binário. `review_status` = "protocolavel_entregue_ao_humano"
    # significa entrega ao advogado com checklist, NUNCA protocolo realizado.
    parecer: dict[str, list[str]] = {}
    review_status: str | None = None
    methodology_version: str | None = None
