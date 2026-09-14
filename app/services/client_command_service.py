"""Canonical, auditable commands for client mutations.

Endpoints and flows must not mutate ``Client`` directly.  This service keeps
the workspace boundary, optimistic concurrency and audit trail in one place.
It deliberately does not commit: callers can compose the command with an
outbox or another local mutation in one transaction.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client
from app.models.klm_operations import KlmAuditEvent
from app.services.audit_actor_service import resolve_human_actor_label
from app.services.matter_service import MatterService

_EDITABLE_FIELDS = frozenset({"name", "cpf", "email", "phone", "employer", "position"})


def _snapshot(client: Client) -> dict[str, Any]:
    return {field: getattr(client, field) for field in sorted(_EDITABLE_FIELDS)}


def _fingerprint(payload: dict[str, Any]) -> str:
    return sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class ClientCommandService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def update(
        self,
        *,
        workspace_id: UUID,
        client_id: UUID,
        changes: dict[str, Any],
        actor_user_id: UUID,
        expected_updated_at: datetime | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
        source: str = "client_command",
    ) -> Client:
        unsupported = set(changes) - _EDITABLE_FIELDS
        if unsupported:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Campos de cliente não editáveis: {', '.join(sorted(unsupported))}.",
            )
        normalized = {key: value for key, value in changes.items() if key in _EDITABLE_FIELDS}
        if "name" in normalized and (
            normalized["name"] is None or not str(normalized["name"]).strip()
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="O nome do cliente não pode ficar vazio.",
            )

        client = await self.db.scalar(
            select(Client)
            .where(
                Client.id == client_id,
                Client.workspace_id == workspace_id,
                Client.deleted_at.is_(None),
            )
            .with_for_update()
        )
        if client is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cliente não encontrado",
            )

        if expected_updated_at is not None and client.updated_at != expected_updated_at:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "CLIENT_VERSION_CONFLICT",
                    "message": "O cliente foi alterado por outra pessoa.",
                },
            )

        operation_id = None
        payload_hash = _fingerprint(normalized)
        if idempotency_key:
            operation_id = f"client-command:{client.id}:{idempotency_key}"
            existing = await self.db.scalar(
                select(KlmAuditEvent).where(
                    KlmAuditEvent.workspace_id == workspace_id,
                    KlmAuditEvent.entity_type == "client",
                    KlmAuditEvent.entity_id == client.id,
                    KlmAuditEvent.correlation_id == operation_id,
                    KlmAuditEvent.event_type == "client_updated",
                )
            )
            if existing is not None:
                if (existing.after_data or {}).get("payloadHash") != payload_hash:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "code": "IDEMPOTENCY_KEY_REUSED",
                            "message": "A chave foi usada com outra alteração.",
                        },
                    )
                return client

        before = _snapshot(client)
        material_changes: list[tuple[str, Any, Any]] = []
        for field, value in normalized.items():
            old_value = getattr(client, field)
            if old_value != value:
                material_changes.append((field, old_value, value))
                setattr(client, field, value)
        if not material_changes:
            return client

        # Explicit timestamp makes the version advance after flush in all DBs.
        client.updated_at = datetime.now(UTC)
        await MatterService(self.db).record_manual_client_changes(
            workspace_id=workspace_id,
            client_id=client.id,
            changes=material_changes,
            updated_by=actor_user_id,
            change_source=source,
        )
        await self.db.flush()
        after = _snapshot(client)
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human",
                actor_label=await resolve_human_actor_label(self.db, actor_user_id),
                event_type="client_updated",
                entity_type="client",
                entity_id=client.id,
                summary="Cliente atualizado por comando canônico.",
                correlation_id=operation_id or correlation_id,
                before_data=before,
                after_data={**after, "payloadHash": payload_hash, "source": source},
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.flush()
        return client
