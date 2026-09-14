"""Canonical mutation boundary for draft contract billing proposals."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contract_billing import ContractBillingProposal, ContractBillingProposalStatus
from app.models.klm_operations import KlmAuditEvent
from app.services.audit_actor_service import resolve_human_actor_label

_EDITABLE_FIELDS = frozenset({"source_document_name", "items", "notes"})


def _snapshot(proposal: ContractBillingProposal) -> dict[str, Any]:
    return {field: getattr(proposal, field) for field in sorted(_EDITABLE_FIELDS)}


def _fingerprint(payload: dict[str, Any]) -> str:
    return sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class ContractCommandService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def update(
        self,
        *,
        workspace_id: UUID,
        proposal_id: UUID,
        changes: dict[str, Any],
        actor_user_id: UUID,
        expected_updated_at: datetime | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
        source: str = "contract_command",
    ) -> ContractBillingProposal:
        unsupported = set(changes) - _EDITABLE_FIELDS
        if unsupported:
            raise HTTPException(status_code=422, detail="Campos de contrato não editáveis.")
        proposal = await self.db.scalar(
            select(ContractBillingProposal)
            .where(
                ContractBillingProposal.id == proposal_id,
                ContractBillingProposal.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if proposal is None:
            raise HTTPException(status_code=404, detail="Proposta de cobrança não encontrada.")
        if proposal.status != ContractBillingProposalStatus.draft:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "CONTRACT_NOT_DRAFT",
                    "message": "Somente propostas em rascunho podem ser alteradas.",
                },
            )
        if expected_updated_at is not None and proposal.updated_at != expected_updated_at:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "CONTRACT_VERSION_CONFLICT",
                    "message": "A proposta foi alterada por outra pessoa.",
                },
            )
        normalized = {key: value for key, value in changes.items() if key in _EDITABLE_FIELDS}
        payload_hash = _fingerprint(normalized)
        operation_id = None
        if idempotency_key:
            operation_id = f"contract-command:{proposal.id}:{idempotency_key}"
            existing = await self.db.scalar(
                select(KlmAuditEvent).where(
                    KlmAuditEvent.workspace_id == workspace_id,
                    KlmAuditEvent.entity_type == "contract_billing_proposal",
                    KlmAuditEvent.entity_id == proposal.id,
                    KlmAuditEvent.correlation_id == operation_id,
                    KlmAuditEvent.event_type == "contract_billing_proposal_updated",
                )
            )
            if existing is not None:
                if (existing.after_data or {}).get("payloadHash") != payload_hash:
                    raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED"})
                return proposal
        before = _snapshot(proposal)
        changed = False
        for field, value in normalized.items():
            if getattr(proposal, field) != value:
                setattr(proposal, field, value)
                changed = True
        if not changed:
            return proposal
        proposal.updated_at = datetime.now(UTC)
        await self.db.flush()
        after = _snapshot(proposal)
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human",
                actor_label=await resolve_human_actor_label(self.db, actor_user_id),
                event_type="contract_billing_proposal_updated",
                entity_type="contract_billing_proposal",
                entity_id=proposal.id,
                summary="Proposta de cobrança atualizada por comando canônico.",
                correlation_id=operation_id or correlation_id,
                before_data=before,
                after_data={**after, "payloadHash": payload_hash, "source": source},
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.flush()
        return proposal
