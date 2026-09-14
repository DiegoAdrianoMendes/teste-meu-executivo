"""Serviço da régua de cobrança (dunning) — F4.

Ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 F4. Config por
workspace vive em `Workspace.settings["finance"]["dunning"]`, no mesmo
padrão de `FinanceService.get_settings`/`update_settings` (§4-F1), mas
mantido em módulo próprio para não reformatar `finance_service.py`.
"""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.finance import FinanceEntry
from app.models.finance_charge import FinanceCharge
from app.models.finance_dunning import FinanceDunningLog, FinanceDunningLogStatus
from app.models.workspace import Workspace
from app.schemas.finance_dunning import (
    DUNNING_DEFAULT_STEP_CONFIG,
    FinanceDunningLogRead,
    FinanceDunningSettingsRead,
    FinanceDunningSettingsUpdate,
    FinanceDunningStepConfig,
)


class FinanceDunningService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _get_workspace(self, workspace_id: UUID) -> Workspace:
        workspace = await self.db.get(Workspace, workspace_id)
        if not workspace:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WKS-001", "message": "Workspace não encontrado."},
            )
        return workspace

    def _merge_dunning_settings(self, settings: dict) -> dict:
        finance_settings = settings.get("finance", {}) if isinstance(settings, dict) else {}
        dunning_settings = (
            finance_settings.get("dunning", {}) if isinstance(finance_settings, dict) else {}
        )
        stored_steps = dunning_settings.get("steps", {}) if isinstance(dunning_settings, dict) else {}
        merged_steps: dict[str, dict] = {}
        for step_key, defaults in DUNNING_DEFAULT_STEP_CONFIG.items():
            stored = stored_steps.get(step_key, {}) if isinstance(stored_steps, dict) else {}
            merged_steps[step_key] = {
                "enabled": stored.get("enabled", defaults["enabled"]),
                "offsetDays": stored.get("offsetDays", defaults["offsetDays"]),
                "requiresApproval": stored.get("requiresApproval", defaults["requiresApproval"]),
                "messageTemplate": stored.get("messageTemplate", defaults["messageTemplate"]),
            }
        return merged_steps

    async def get_config(self, workspace_id: UUID) -> FinanceDunningSettingsRead:
        workspace = await self._get_workspace(workspace_id)
        merged_steps = self._merge_dunning_settings(workspace.settings or {})
        return FinanceDunningSettingsRead(
            steps={
                key: FinanceDunningStepConfig(
                    enabled=value["enabled"],
                    offset_days=value["offsetDays"],
                    requires_approval=value["requiresApproval"],
                    message_template=value["messageTemplate"],
                )
                for key, value in merged_steps.items()
            }
        )

    async def update_config(
        self, workspace_id: UUID, payload: FinanceDunningSettingsUpdate
    ) -> FinanceDunningSettingsRead:
        workspace = await self._get_workspace(workspace_id)
        settings = workspace.settings or {}
        merged_steps = self._merge_dunning_settings(settings)

        for step_key, step_update in (payload.steps or {}).items():
            if step_key not in merged_steps:
                continue
            update_data = step_update.model_dump(exclude_unset=True, by_alias=False)
            if "enabled" in update_data:
                merged_steps[step_key]["enabled"] = update_data["enabled"]
            if "offset_days" in update_data:
                merged_steps[step_key]["offsetDays"] = update_data["offset_days"]
            if "requires_approval" in update_data:
                merged_steps[step_key]["requiresApproval"] = update_data["requires_approval"]
            if "message_template" in update_data:
                merged_steps[step_key]["messageTemplate"] = update_data["message_template"]

        finance_settings = settings.get("finance", {}) if isinstance(settings, dict) else {}
        finance_settings = dict(finance_settings)
        finance_settings["dunning"] = {"steps": merged_steps}
        settings = {**settings, "finance": finance_settings}
        workspace.settings = settings
        await self.db.flush()

        return FinanceDunningSettingsRead(
            steps={
                key: FinanceDunningStepConfig(
                    enabled=value["enabled"],
                    offset_days=value["offsetDays"],
                    requires_approval=value["requiresApproval"],
                    message_template=value["messageTemplate"],
                )
                for key, value in merged_steps.items()
            }
        )

    async def list_log_for_charge(
        self, workspace_id: UUID, charge_id: UUID
    ) -> List[FinanceDunningLog]:
        result = await self.db.execute(
            select(FinanceDunningLog)
            .where(
                FinanceDunningLog.workspace_id == workspace_id,
                FinanceDunningLog.charge_id == charge_id,
            )
            .order_by(FinanceDunningLog.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_log_entry(
        self, workspace_id: UUID, log_id: UUID
    ) -> Optional[FinanceDunningLog]:
        return await self.db.scalar(
            select(FinanceDunningLog).where(
                FinanceDunningLog.workspace_id == workspace_id,
                FinanceDunningLog.id == log_id,
            )
        )

    async def list_recent_logs(
        self,
        workspace_id: UUID,
        status_filter: Optional[FinanceDunningLogStatus] = None,
        limit: int = 50,
    ) -> List[FinanceDunningLogRead]:
        """Disparos recentes da régua no workspace inteiro, com resumo do
        lançamento de origem (título, valor, vencimento) — alimenta a lista
        de "últimos disparos" e a fila de aprovação humana do degrau final
        (status=pending_approval) sem uma segunda chamada por linha.
        """
        stmt = (
            select(FinanceDunningLog, FinanceEntry.title, FinanceEntry.amount, FinanceEntry.due_date)
            .outerjoin(FinanceCharge, FinanceCharge.id == FinanceDunningLog.charge_id)
            .outerjoin(FinanceEntry, FinanceEntry.id == FinanceCharge.entry_id)
            .where(FinanceDunningLog.workspace_id == workspace_id)
        )
        if status_filter is not None:
            stmt = stmt.where(FinanceDunningLog.status == status_filter)
        stmt = stmt.order_by(FinanceDunningLog.created_at.desc()).limit(limit)

        result = await self.db.execute(stmt)
        enriched: List[FinanceDunningLogRead] = []
        for log_entry, entry_title, amount, due_date in result.all():
            base = FinanceDunningLogRead.model_validate(log_entry)
            enriched.append(
                base.model_copy(
                    update={
                        "entry_title": entry_title,
                        "amount": float(amount) if amount is not None else None,
                        "due_date": due_date,
                    }
                )
            )
        return enriched
