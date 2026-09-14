"""Platform-only CRUD for per-account entitlement overrides.

The plan/package defines the default entitlement catalog for every
workspace. This service lets a platform-root operator grant, revoke, or
re-limit a single entitlement for one workspace without touching the plan
catalog — an override always wins over the plan at resolution time (see
``EntitlementResolver.resolve``). Every write is paired with an immutable
``KlmAuditEvent`` (who, what, why); reads never leak across workspaces
because every query is scoped by ``workspace_id``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entitlement import EntitlementKey, OverrideMode, WorkspaceEntitlementOverride
from app.models.klm_operations import KlmAuditEvent
from app.models.workspace import Workspace
from app.services.audit_actor_service import resolve_human_actor_label
from app.services.entitlement_service import (
    ENTITLEMENT_CATALOG,
    QUANTITATIVE_ENTITLEMENTS,
)

AUDIT_ENTITY_TYPE = "workspace_entitlement_override"
# Quantities cross the JSON/TypeScript boundary as numbers. Staying within
# IEEE-754's safe integer range prevents silent rounding in the Blackbox.
MAX_SAFE_ENTITLEMENT_QUANTITY = (1 << 53) - 1


class WorkspaceEntitlementOverrideError(RuntimeError):
    """Base class for override CRUD failures."""


class WorkspaceEntitlementOverrideValidationError(WorkspaceEntitlementOverrideError):
    pass


class WorkspaceEntitlementOverrideWorkspaceNotFoundError(WorkspaceEntitlementOverrideError):
    pass


class WorkspaceEntitlementOverrideNotFoundError(WorkspaceEntitlementOverrideError):
    pass


@dataclass(frozen=True, slots=True)
class OverrideWriteRequest:
    entitlement_key: str
    action: str  # "upsert" | "delete"
    mode: str | None
    quantity: int | None
    reason: str


def _validate_reason(reason: str) -> str:
    normalized = reason.strip() if isinstance(reason, str) else ""
    if not normalized:
        raise WorkspaceEntitlementOverrideValidationError(
            "O motivo (reason) é obrigatório para toda mudança de override."
        )
    return normalized


def _validate_entitlement_key(value: str) -> EntitlementKey:
    try:
        key = EntitlementKey(value)
    except ValueError as error:
        raise WorkspaceEntitlementOverrideValidationError(
            f"entitlement_key inválida: {value!r}."
        ) from error
    if key not in ENTITLEMENT_CATALOG:
        raise WorkspaceEntitlementOverrideValidationError(
            f"entitlement_key não está no catálogo de entitlements: {value!r}."
        )
    return key


def _before_snapshot(override: WorkspaceEntitlementOverride | None) -> dict | None:
    if override is None:
        return None
    return {
        "mode": override.mode.value,
        "quantity": override.quantity,
        "reason": override.reason,
    }


def _after_snapshot(override: WorkspaceEntitlementOverride) -> dict:
    return {
        "mode": override.mode.value,
        "quantity": override.quantity,
        "reason": override.reason,
    }


class WorkspaceEntitlementOverrideService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_overrides(
        self, workspace_id: UUID
    ) -> list[WorkspaceEntitlementOverride]:
        result = await self.db.scalars(
            select(WorkspaceEntitlementOverride)
            .where(WorkspaceEntitlementOverride.workspace_id == workspace_id)
            .order_by(WorkspaceEntitlementOverride.entitlement)
        )
        return list(result.all())

    async def upsert_override(
        self,
        *,
        workspace_id: UUID,
        entitlement_key: str,
        mode: str,
        quantity: int | None,
        reason: str,
        actor_user_id: UUID,
    ) -> WorkspaceEntitlementOverride:
        await self._require_workspace(workspace_id)
        key = _validate_entitlement_key(entitlement_key)
        normalized_reason = _validate_reason(reason)
        try:
            normalized_mode = OverrideMode(mode)
        except ValueError as error:
            raise WorkspaceEntitlementOverrideValidationError(
                f"mode inválido: {mode!r}. Use grant, revoke ou limit."
            ) from error

        if normalized_mode == OverrideMode.limit and quantity is None:
            raise WorkspaceEntitlementOverrideValidationError(
                "mode=limit exige quantity."
            )
        if normalized_mode == OverrideMode.revoke and quantity is not None:
            raise WorkspaceEntitlementOverrideValidationError(
                "mode=revoke não aceita quantity."
            )
        if (
            key in QUANTITATIVE_ENTITLEMENTS
            and normalized_mode == OverrideMode.grant
            and quantity is None
        ):
            raise WorkspaceEntitlementOverrideValidationError(
                f"{key.value} exige quantity finita em mode=grant."
            )
        if key not in QUANTITATIVE_ENTITLEMENTS and quantity is not None:
            raise WorkspaceEntitlementOverrideValidationError(
                f"{key.value} é booleano e não aceita quantity."
            )
        if quantity is not None:
            if (
                isinstance(quantity, bool)
                or not isinstance(quantity, int)
                or quantity < 0
                or quantity > MAX_SAFE_ENTITLEMENT_QUANTITY
            ):
                raise WorkspaceEntitlementOverrideValidationError(
                    "quantity deve ser um inteiro não negativo representável com segurança."
                )

        existing = await self.db.scalar(
            select(WorkspaceEntitlementOverride).where(
                WorkspaceEntitlementOverride.workspace_id == workspace_id,
                WorkspaceEntitlementOverride.entitlement == key,
            )
        )
        before_data = _before_snapshot(existing)

        if existing is None:
            override = WorkspaceEntitlementOverride(
                workspace_id=workspace_id,
                entitlement=key,
                mode=normalized_mode,
                quantity=quantity,
                reason=normalized_reason,
                created_by_user_id=actor_user_id,
            )
            self.db.add(override)
        else:
            override = existing
            override.mode = normalized_mode
            override.quantity = quantity
            override.reason = normalized_reason
            override.created_by_user_id = actor_user_id
        await self.db.flush()

        actor_label = await resolve_human_actor_label(self.db, actor_user_id)
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human",
                actor_label=actor_label,
                event_type="workspace_entitlement_override_upserted",
                entity_type=AUDIT_ENTITY_TYPE,
                entity_id=override.id,
                summary=(
                    f"Override de entitlement '{key.value}' definido como "
                    f"{normalized_mode.value} pela Central Blackbox."
                ),
                before_data=before_data,
                after_data=_after_snapshot(override),
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.flush()
        return override

    async def delete_override(
        self,
        *,
        workspace_id: UUID,
        entitlement_key: str,
        reason: str,
        actor_user_id: UUID,
    ) -> None:
        await self._require_workspace(workspace_id)
        key = _validate_entitlement_key(entitlement_key)
        normalized_reason = _validate_reason(reason)

        existing = await self.db.scalar(
            select(WorkspaceEntitlementOverride).where(
                WorkspaceEntitlementOverride.workspace_id == workspace_id,
                WorkspaceEntitlementOverride.entitlement == key,
            )
        )
        if existing is None:
            raise WorkspaceEntitlementOverrideNotFoundError(
                f"Não existe override de {key.value} para o workspace {workspace_id}."
            )

        before_data = _before_snapshot(existing)
        entity_id = existing.id
        await self.db.delete(existing)
        await self.db.flush()

        actor_label = await resolve_human_actor_label(self.db, actor_user_id)
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human",
                actor_label=actor_label,
                event_type="workspace_entitlement_override_deleted",
                entity_type=AUDIT_ENTITY_TYPE,
                entity_id=entity_id,
                summary=(
                    f"Override de entitlement '{key.value}' removido pela "
                    f"Central Blackbox — motivo: {normalized_reason}."
                ),
                before_data=before_data,
                after_data=None,
                occurred_at=datetime.now(UTC),
            )
        )
        await self.db.flush()

    async def _require_workspace(self, workspace_id: UUID) -> None:
        exists = await self.db.scalar(
            select(Workspace.id).where(
                Workspace.id == workspace_id, Workspace.deleted_at.is_(None)
            )
        )
        if exists is None:
            raise WorkspaceEntitlementOverrideWorkspaceNotFoundError(
                f"Workspace {workspace_id} não encontrado."
            )
