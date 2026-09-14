"""Support-governed commercial token package workflow."""
from __future__ import annotations

import re
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tenant import set_workspace_context
from app.models.ai_token_package import (
    AiTokenPackage,
    AiTokenPackageRequestStatus,
    WorkspaceAiTokenPackageRequest,
)
from app.models.entitlement import EntitlementKey, WorkspaceMonthlyUsage
from app.models.workspace import Workspace
from app.schemas.ai_costs import AiTokenPackageUpsert, WorkspaceAiTokenPackageRequestCreate
from app.services.entitlement_service import EntitlementNotConfiguredError, EntitlementResolver
from app.services.klm_operations_service import KlmOperationsService

_PACKAGE_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,79}$")


class AiTokenPackageNotFoundError(LookupError):
    pass


class AiTokenPackageConflictError(ValueError):
    pass


class AiTokenPackageTransitionError(ValueError):
    pass


def _month_bounds(today: date) -> tuple[date, date]:
    start = date(today.year, today.month, 1)
    end = date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)
    return start, end


class AiTokenPackageService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_catalog(self, *, include_inactive: bool = False) -> list[AiTokenPackage]:
        statement = select(AiTokenPackage)
        if not include_inactive:
            statement = statement.where(AiTokenPackage.is_active.is_(True))
        statement = statement.order_by(
            AiTokenPackage.sort_order.asc(),
            AiTokenPackage.token_quantity.asc(),
            AiTokenPackage.name.asc(),
        )
        return list((await self.db.scalars(statement)).all())

    async def upsert_catalog_item(
        self,
        *,
        key: str,
        payload: AiTokenPackageUpsert,
    ) -> AiTokenPackage:
        normalized_key = key.strip().lower()
        if not _PACKAGE_KEY_PATTERN.fullmatch(normalized_key):
            raise AiTokenPackageConflictError(
                "A chave deve ter de 3 a 80 caracteres e usar apenas letras "
                "minúsculas, números, _ ou -."
            )
        package = await self.db.scalar(
            select(AiTokenPackage).where(AiTokenPackage.key == normalized_key).with_for_update()
        )
        values = payload.model_dump()
        if package is None:
            package = AiTokenPackage(key=normalized_key, **values)
            self.db.add(package)
        else:
            for field_name, value in values.items():
                setattr(package, field_name, value)
        await self.db.flush()
        return package

    async def list_workspace_requests(
        self,
        *,
        workspace_id: UUID,
    ) -> list[WorkspaceAiTokenPackageRequest]:
        await self._require_workspace_context(workspace_id)
        statement = (
            select(WorkspaceAiTokenPackageRequest)
            .where(WorkspaceAiTokenPackageRequest.workspace_id == workspace_id)
            .order_by(WorkspaceAiTokenPackageRequest.created_at.desc())
        )
        return list((await self.db.scalars(statement)).all())

    async def page_workspace_requests(
        self,
        *,
        workspace_id: UUID,
        limit: int,
        offset: int,
        status: AiTokenPackageRequestStatus | None = None,
    ) -> tuple[list[WorkspaceAiTokenPackageRequest], int]:
        await self._require_workspace_context(workspace_id)
        filters = [WorkspaceAiTokenPackageRequest.workspace_id == workspace_id]
        if status is not None:
            filters.append(WorkspaceAiTokenPackageRequest.status == status)
        total = int(
            await self.db.scalar(
                select(func.count(WorkspaceAiTokenPackageRequest.id)).where(*filters)
            )
            or 0
        )
        items = list(
            (
                await self.db.scalars(
                    select(WorkspaceAiTokenPackageRequest)
                    .where(*filters)
                    .order_by(
                        WorkspaceAiTokenPackageRequest.created_at.desc(),
                        WorkspaceAiTokenPackageRequest.id.desc(),
                    )
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        )
        return items, total

    async def request_package(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        payload: WorkspaceAiTokenPackageRequestCreate,
    ) -> WorkspaceAiTokenPackageRequest:
        await self._require_workspace_context(workspace_id, for_update=True)
        idempotency_key = payload.idempotency_key.strip()
        existing = await self.db.scalar(
            select(WorkspaceAiTokenPackageRequest).where(
                WorkspaceAiTokenPackageRequest.workspace_id == workspace_id,
                WorkspaceAiTokenPackageRequest.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.package_id != payload.package_id:
                raise AiTokenPackageConflictError(
                    "A chave de idempotência já foi usada para outro pacote."
                )
            return existing

        package = await self.db.scalar(
            select(AiTokenPackage).where(
                AiTokenPackage.id == payload.package_id,
                AiTokenPackage.is_active.is_(True),
            )
        )
        if package is None:
            raise AiTokenPackageNotFoundError("Pacote ativo não encontrado.")

        period_start, period_end = _month_bounds(datetime.now(UTC).date())
        request = WorkspaceAiTokenPackageRequest(
            workspace_id=workspace_id,
            package_id=package.id,
            requested_by_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            status=AiTokenPackageRequestStatus.requested,
            package_key_snapshot=package.key,
            package_name_snapshot=package.name,
            token_quantity_snapshot=package.token_quantity,
            commercial_price_cents_snapshot=package.commercial_price_cents,
            currency_snapshot=package.currency,
            period_start=period_start,
            period_end=period_end,
            request_note=payload.request_note,
        )
        self.db.add(request)
        await self.db.flush()
        await KlmOperationsService(self.db).record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="human",
            event_type="ai_token_package_requested",
            entity_type="ai_token_package_request",
            entity_id=request.id,
            summary="Pacote extra de tokens solicitado ao suporte.",
            after_data={
                "packageKey": request.package_key_snapshot,
                "tokenQuantity": request.token_quantity_snapshot,
                "status": request.status.value,
                "periodStart": request.period_start.isoformat(),
                "periodEnd": request.period_end.isoformat(),
            },
        )
        await self.db.refresh(request)
        return request

    async def decide_request(
        self,
        *,
        workspace_id: UUID,
        request_id: UUID,
        actor_user_id: UUID,
        decision: AiTokenPackageRequestStatus,
        decision_note: str | None,
    ) -> WorkspaceAiTokenPackageRequest:
        if decision not in {
            AiTokenPackageRequestStatus.approved,
            AiTokenPackageRequestStatus.rejected,
        }:
            raise AiTokenPackageTransitionError("Decisão de suporte inválida.")
        await self._require_workspace_context(workspace_id, for_update=True)
        request = await self.db.scalar(
            select(WorkspaceAiTokenPackageRequest)
            .where(
                WorkspaceAiTokenPackageRequest.id == request_id,
                WorkspaceAiTokenPackageRequest.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if request is None:
            raise AiTokenPackageNotFoundError("Solicitação de pacote não encontrada.")
        if request.status != AiTokenPackageRequestStatus.requested:
            if request.status == decision:
                return request
            raise AiTokenPackageTransitionError(
                "Somente solicitações pendentes podem receber uma decisão."
            )
        if decision == AiTokenPackageRequestStatus.approved:
            if request.period_end <= datetime.now(UTC).date():
                raise AiTokenPackageTransitionError(
                    "A solicitação pertence a um período encerrado. Gere uma nova solicitação."
                )
            try:
                await EntitlementResolver(self.db).resolve(
                    workspace_id,
                    EntitlementKey.tokens,
                )
            except EntitlementNotConfiguredError as error:
                raise AiTokenPackageTransitionError(
                    "O workspace precisa de um plano com limite de tokens antes da liberação."
                ) from error

        before_status = request.status.value
        request.status = decision
        request.decided_by_user_id = actor_user_id
        request.decision_note = decision_note
        request.decided_at = datetime.now(UTC)
        await self.db.flush()
        await self._refresh_current_token_limit(workspace_id)
        await KlmOperationsService(self.db).record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="human",
            event_type=f"ai_token_package_{decision.value}",
            entity_type="ai_token_package_request",
            entity_id=request.id,
            summary=(
                "Pacote extra de tokens liberado pelo suporte."
                if decision == AiTokenPackageRequestStatus.approved
                else "Solicitação de pacote extra de tokens recusada pelo suporte."
            ),
            before_data={"status": before_status},
            after_data={
                "status": request.status.value,
                "tokenQuantity": request.token_quantity_snapshot,
                "periodStart": request.period_start.isoformat(),
                "periodEnd": request.period_end.isoformat(),
            },
        )
        await self.db.refresh(request)
        return request

    async def revoke_request(
        self,
        *,
        workspace_id: UUID,
        request_id: UUID,
        actor_user_id: UUID,
        decision_note: str | None,
    ) -> WorkspaceAiTokenPackageRequest:
        await self._require_workspace_context(workspace_id, for_update=True)
        request = await self.db.scalar(
            select(WorkspaceAiTokenPackageRequest)
            .where(
                WorkspaceAiTokenPackageRequest.id == request_id,
                WorkspaceAiTokenPackageRequest.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if request is None:
            raise AiTokenPackageNotFoundError("Solicitação de pacote não encontrada.")
        if request.status == AiTokenPackageRequestStatus.revoked:
            return request
        if request.status != AiTokenPackageRequestStatus.approved:
            raise AiTokenPackageTransitionError(
                "Somente um pacote previamente aprovado pode ser revogado."
            )
        request.status = AiTokenPackageRequestStatus.revoked
        request.decided_by_user_id = actor_user_id
        request.decision_note = decision_note
        request.decided_at = datetime.now(UTC)
        await self.db.flush()
        await self._refresh_current_token_limit(workspace_id)
        await KlmOperationsService(self.db).record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="human",
            event_type="ai_token_package_revoked",
            entity_type="ai_token_package_request",
            entity_id=request.id,
            summary="Pacote extra de tokens revogado pelo suporte.",
            after_data={"status": request.status.value},
        )
        await self.db.refresh(request)
        return request

    async def _refresh_current_token_limit(self, workspace_id: UUID) -> None:
        period_start = datetime.now(UTC).date().replace(day=1)
        try:
            resolved = await EntitlementResolver(self.db).resolve(
                workspace_id,
                EntitlementKey.tokens,
            )
        except EntitlementNotConfiguredError:
            return
        await self.db.execute(
            update(WorkspaceMonthlyUsage)
            .where(
                WorkspaceMonthlyUsage.workspace_id == workspace_id,
                WorkspaceMonthlyUsage.entitlement == EntitlementKey.tokens,
                WorkspaceMonthlyUsage.period_start == period_start,
            )
            .values(
                limit_quantity=resolved.limit_quantity,
                hard_limit=resolved.hard_limit,
                limit_source=resolved.source.value,
            )
            .execution_options(synchronize_session=False)
        )

    async def _require_workspace_context(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> None:
        statement = select(Workspace.id).where(
            Workspace.id == workspace_id,
            Workspace.deleted_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        if await self.db.scalar(statement) is None:
            raise AiTokenPackageNotFoundError("Workspace não encontrado.")
        await set_workspace_context(self.db, workspace_id)


__all__ = [
    "AiTokenPackageConflictError",
    "AiTokenPackageNotFoundError",
    "AiTokenPackageService",
    "AiTokenPackageTransitionError",
]
