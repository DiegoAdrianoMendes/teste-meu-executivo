"""
Service: FinanceService
Controle financeiro tenant-aware com lembretes e integrações nativas.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import logger
from app.models.entitlement import EntitlementKey
from app.models.finance import (
    FinanceEntry,
    FinanceEntryStatus,
    FinanceEntryType,
    FinanceReminder,
    FinanceReminderStatus,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
)
from app.models.matter import Matter
from app.models.workspace import Workspace
from app.schemas.finance import (
    FinanceEntryCreate,
    FinanceEntryRead,
    FinanceEntryUpdate,
    FinanceOverviewRead,
    FinanceReminderRead,
    FinanceReminderUpdate,
    FinanceSettingsRead,
    FinanceSettingsUpdate,
    IntegrationConnectionCreate,
    IntegrationConnectionRead,
    IntegrationConnectionUpdate,
)
from app.services.entitlement_service import EntitlementEnforcementService
from app.services.financial_upgrade_service import (
    enforce_financial_plan_only,
    is_paused_financial_integration,
)
from app.services.integration_secrets import (
    WEBHOOK_SECRET_DIGEST_KEY,
    protect_integration_settings,
    reveal_integration_secret,
)
from app.services.integration_service import IntegrationService

FINANCE_DEFAULT_SETTINGS = {
    "remindersEnabled": True,
    "defaultReminderDaysBefore": 3,
    "defaultReminderChannel": "whatsapp",
    "allowClientNotification": True,
    "autoCreateReceivableOnClose": True,
    "openApiEnabled": True,
    "externalSyncMode": "manual",
}


class FinanceService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_entries(
        self,
        workspace_id: UUID,
        *,
        status: Optional[FinanceEntryStatus] = None,
        provider: Optional[str] = None,
        matter_id: UUID | None = None,
        upcoming_only: bool = False,
        limit: int = 100,
    ) -> list[FinanceEntry]:
        query = (
            select(FinanceEntry)
            .options(selectinload(FinanceEntry.reminders))
            .where(FinanceEntry.workspace_id == workspace_id, FinanceEntry.deleted_at.is_(None))
            .order_by(FinanceEntry.due_date.asc().nullslast(), FinanceEntry.created_at.desc())
            .limit(limit)
        )
        if status:
            query = query.where(FinanceEntry.status == status)
        if provider:
            query = query.where(FinanceEntry.provider == provider)
        if matter_id is not None:
            query = query.where(FinanceEntry.matter_id == matter_id)
        if upcoming_only:
            now = self._utc_now()
            query = query.where(
                FinanceEntry.due_date.is_not(None),
                FinanceEntry.due_date >= now,
                FinanceEntry.due_date <= now + timedelta(days=14),
            )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_entry(self, workspace_id: UUID, entry_id: UUID) -> FinanceEntry:
        result = await self.db.execute(
            select(FinanceEntry)
            .options(selectinload(FinanceEntry.reminders))
            .where(
                FinanceEntry.workspace_id == workspace_id,
                FinanceEntry.id == entry_id,
                FinanceEntry.deleted_at.is_(None),
            )
        )
        entry = result.scalar_one_or_none()
        if not entry:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "FIN-001", "message": "Lançamento financeiro não encontrado."},
            )
        return entry

    async def create_entry(self, workspace_id: UUID, payload: FinanceEntryCreate) -> FinanceEntry:
        entry = FinanceEntry(
            workspace_id=workspace_id,
            matter_id=payload.matter_id,
            title=payload.title,
            description=payload.description,
            entry_type=payload.entry_type,
            status=payload.status,
            provider=payload.provider,
            external_ref=payload.external_ref,
            amount=payload.amount,
            currency=payload.currency,
            due_date=payload.due_date,
            paid_at=payload.paid_at,
            reminder_days_before=payload.reminder_days_before,
            notify_client=payload.notify_client,
            tags=payload.tags,
            entry_metadata=payload.entry_metadata,
        )
        self.db.add(entry)
        await self.db.flush()
        await self._sync_reminders(entry, payload.reminders)
        await self.db.flush()
        if entry.status == FinanceEntryStatus.paid:
            await self._record_payment_confirmed(entry)
        return await self.get_entry(workspace_id, entry.id)

    async def update_entry(self, workspace_id: UUID, entry_id: UUID, payload: FinanceEntryUpdate) -> FinanceEntry:
        entry = await self.get_entry(workspace_id, entry_id)
        previous_status = entry.status
        update_data = payload.model_dump(exclude_unset=True)
        reminders_payload = update_data.pop("reminders", None)

        for field, value in update_data.items():
            setattr(entry, field, value)

        if reminders_payload is not None:
            await self._replace_reminders(entry, reminders_payload)
        elif entry.due_date and not entry.reminders:
            await self._create_default_reminder(entry)

        await self.db.flush()
        if (
            entry.status == FinanceEntryStatus.paid
            and previous_status != FinanceEntryStatus.paid
        ):
            await self._record_payment_confirmed(entry)
        return await self.get_entry(workspace_id, entry.id)

    async def _record_payment_confirmed(self, entry: FinanceEntry) -> None:
        """Marco de funil: honorário de caso pago vira AcquisitionEvent idempotente."""
        if entry.matter_id is None or entry.entry_type != FinanceEntryType.receivable:
            return
        from app.schemas.klm_operations import AcquisitionEventCreate
        from app.services.klm_operations_service import KlmOperationsService

        client_id = await self.db.scalar(
            select(Matter.client_id).where(
                Matter.id == entry.matter_id,
                Matter.workspace_id == entry.workspace_id,
            )
        )
        try:
            await KlmOperationsService(self.db).record_acquisition_event(
                entry.workspace_id,
                None,
                AcquisitionEventCreate(
                    matter_id=entry.matter_id,
                    client_id=client_id,
                    event_name="payment_confirmed",
                    channel="finance",
                    source=entry.provider,
                    event_id=str(entry.id),
                    dedupe_key=f"finance:{entry.id}",
                    value=float(entry.amount) if entry.amount is not None else None,
                    currency=(entry.currency or "BRL").strip().upper(),
                    event_metadata={"paymentId": str(entry.id)},
                ),
            )
        except ValueError:
            # Re-transição legítima (pago → estornado → pago com valor editado):
            # o marco de funil não pode derrubar a operação contábil.
            logger.warning(
                "finance.acquisition_event_replay_mismatch",
                entry_id=str(entry.id),
            )

    async def soft_delete_entry(self, workspace_id: UUID, entry_id: UUID) -> None:
        entry = await self.get_entry(workspace_id, entry_id)
        entry.soft_delete()
        await self.db.flush()

    async def hard_delete_entry(self, workspace_id: UUID, entry_id: UUID) -> None:
        entry = await self.get_entry(workspace_id, entry_id)
        await self.db.delete(entry)
        await self.db.flush()

    async def list_reminders(
        self,
        workspace_id: UUID,
        *,
        status: Optional[FinanceReminderStatus] = None,
        due_only: bool = False,
        limit: int = 100,
    ) -> list[FinanceReminder]:
        query = (
            select(FinanceReminder)
            .options(selectinload(FinanceReminder.entry))
            .where(FinanceReminder.workspace_id == workspace_id)
            .order_by(FinanceReminder.remind_at.asc())
            .limit(limit)
        )
        if status:
            query = query.where(FinanceReminder.status == status)
        if due_only:
            query = query.where(
                FinanceReminder.status == FinanceReminderStatus.pending,
                FinanceReminder.remind_at <= self._utc_now(),
            )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def update_reminder(
        self,
        workspace_id: UUID,
        reminder_id: UUID,
        payload: FinanceReminderUpdate,
    ) -> FinanceReminder:
        result = await self.db.execute(
            select(FinanceReminder).where(
                FinanceReminder.workspace_id == workspace_id,
                FinanceReminder.id == reminder_id,
            )
        )
        reminder = result.scalar_one_or_none()
        if not reminder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "FIN-002", "message": "Lembrete financeiro não encontrado."},
            )
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(reminder, field, value)
        await self.db.flush()
        return reminder

    async def list_integrations(self, workspace_id: UUID) -> list[IntegrationConnection]:
        result = await self.db.execute(
            select(IntegrationConnection)
            .where(IntegrationConnection.workspace_id == workspace_id)
            .order_by(IntegrationConnection.provider.asc(), IntegrationConnection.label.asc())
        )
        connections = list(result.scalars().all())
        if any(self._backfill_webhook_secret_digest(connection) for connection in connections):
            await self.db.flush()
        return connections

    @staticmethod
    def _backfill_webhook_secret_digest(connection: IntegrationConnection) -> bool:
        """S7: conexões criadas antes do digest não são localizáveis pelo resolver
        do webhook (401 até recadastrar o segredo). Quem abrir a tela de
        integrações do workspace cura a conexão, sob o contexto do tenant, sem
        varredura cross-tenant."""
        current = connection.settings or {}
        if not current.get("webhookSecretEncrypted") or current.get(WEBHOOK_SECRET_DIGEST_KEY):
            return False
        secret = reveal_integration_secret(current, "webhookSecret")
        if not secret:
            return False
        connection.settings = protect_integration_settings({**current, "webhookSecret": secret})
        return True

    async def create_integration(
        self,
        workspace_id: UUID,
        payload: IntegrationConnectionCreate,
    ) -> IntegrationConnection:
        enforce_financial_plan_only(
            provider=payload.provider,
            connection_status=payload.status,
            label=payload.label,
            base_url=payload.base_url,
            external_account_id=payload.external_account_id,
            webhook_url=payload.webhook_url,
            capabilities=payload.capabilities,
            settings=payload.settings,
            notes=payload.notes,
        )
        await self._enforce_integration_entitlements(
            workspace_id=workspace_id,
            provider=payload.provider,
            connection_status=payload.status,
            webhook_url=payload.webhook_url,
            capabilities=payload.capabilities,
        )
        integration_service = IntegrationService(self.db)
        integration_service.validate_connection_payload(
            provider=payload.provider,
            status=payload.status,
            settings_payload=payload.settings,
        )
        connection_defaults = await integration_service.ensure_default_connection_metadata(
            workspace_id,
            payload.provider,
            payload.label,
            payload.base_url,
            payload.webhook_url,
            payload.capabilities,
        )
        connection = IntegrationConnection(
            workspace_id=workspace_id,
            provider=payload.provider,
            status=payload.status,
            label=payload.label,
            base_url=connection_defaults["baseUrl"],
            external_account_id=payload.external_account_id,
            webhook_url=connection_defaults["webhookUrl"],
            api_version=payload.api_version or connection_defaults.get("apiVersion"),
            capabilities=connection_defaults["capabilities"],
            settings=protect_integration_settings(payload.settings),
            notes=payload.notes,
        )
        self.db.add(connection)
        await self.db.flush()
        await self.db.refresh(connection)
        return connection

    async def update_integration(
        self,
        workspace_id: UUID,
        connection_id: UUID,
        payload: IntegrationConnectionUpdate,
    ) -> IntegrationConnection:
        result = await self.db.execute(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == workspace_id,
                IntegrationConnection.id == connection_id,
            )
        )
        connection = result.scalar_one_or_none()
        if not connection:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "FIN-003", "message": "Integração financeira não encontrada."},
            )

        next_status = payload.status or connection.status
        next_webhook_url = (
            payload.webhook_url
            if "webhook_url" in payload.model_fields_set
            else connection.webhook_url
        )
        next_capabilities = (
            payload.capabilities
            if "capabilities" in payload.model_fields_set
            else connection.capabilities
        )
        next_settings = connection.settings.copy() if connection.settings else {}
        if payload.settings is not None:
            next_settings = payload.settings
        enforce_financial_plan_only(
            provider=connection.provider,
            connection_status=next_status,
            label=payload.label if "label" in payload.model_fields_set else connection.label,
            base_url=(
                payload.base_url
                if "base_url" in payload.model_fields_set
                else connection.base_url
            ),
            external_account_id=(
                payload.external_account_id
                if "external_account_id" in payload.model_fields_set
                else connection.external_account_id
            ),
            webhook_url=next_webhook_url,
            capabilities=next_capabilities,
            settings=next_settings,
            notes=payload.notes if "notes" in payload.model_fields_set else connection.notes,
            last_sync_at=(
                payload.last_sync_at
                if "last_sync_at" in payload.model_fields_set
                else connection.last_sync_at
            ),
        )
        await self._enforce_integration_entitlements(
            workspace_id=workspace_id,
            provider=connection.provider,
            connection_status=next_status,
            webhook_url=next_webhook_url,
            capabilities=next_capabilities,
        )
        IntegrationService(self.db).validate_connection_payload(
            provider=connection.provider,
            status=next_status,
            settings_payload=next_settings,
        )

        changes = payload.model_dump(exclude_unset=True)
        protected_settings = None
        if "settings" in changes:
            protected_settings = protect_integration_settings(changes.pop("settings"))
        for field, value in changes.items():
            setattr(connection, field, value)
        if protected_settings is not None:
            connection.settings = protected_settings
        if connection.webhook_url is None:
            defaults = await IntegrationService(self.db).ensure_default_connection_metadata(
                workspace_id,
                connection.provider,
                connection.label,
                connection.base_url,
                connection.webhook_url,
                connection.capabilities,
            )
            connection.webhook_url = defaults["webhookUrl"]
            connection.capabilities = defaults["capabilities"]
            if connection.api_version is None:
                connection.api_version = defaults.get("apiVersion")
        await self.db.flush()
        await self.db.refresh(connection)
        return connection

    async def _enforce_integration_entitlements(
        self,
        *,
        workspace_id: UUID,
        provider: IntegrationProvider,
        connection_status: IntegrationStatus,
        webhook_url: str | None,
        capabilities: list[str] | None,
    ) -> None:
        # Desconectar nunca depende de um recurso pago: o cliente precisa poder
        # revogar uma integracao mesmo depois de trocar de plano.
        if connection_status == IntegrationStatus.disconnected:
            return

        enforcement = EntitlementEnforcementService(self.db)
        if provider in {IntegrationProvider.zapsign, IntegrationProvider.docusign}:
            await enforcement.enforce_feature(
                workspace_id=workspace_id,
                entitlement=EntitlementKey.digital_signature,
            )

        normalized_capabilities = {
            capability.strip().lower()
            for capability in (capabilities or [])
            if capability.strip()
        }
        custom_uses_webhook = provider == IntegrationProvider.custom and (
            bool(webhook_url)
            or any("webhook" in capability for capability in normalized_capabilities)
        )
        if custom_uses_webhook:
            await enforcement.enforce_feature(
                workspace_id=workspace_id,
                entitlement=EntitlementKey.webhooks,
            )

    async def get_settings(self, workspace_id: UUID) -> FinanceSettingsRead:
        workspace = await self._get_workspace(workspace_id)
        finance_settings = self._merge_finance_settings(workspace.settings or {})
        return FinanceSettingsRead.model_validate(finance_settings)

    async def update_settings(self, workspace_id: UUID, payload: FinanceSettingsUpdate) -> FinanceSettingsRead:
        workspace = await self._get_workspace(workspace_id)
        settings = workspace.settings or {}
        current = self._merge_finance_settings(settings)
        current.update(payload.model_dump(exclude_unset=True, by_alias=False))
        settings["finance"] = {
            "remindersEnabled": current["reminders_enabled"],
            "defaultReminderDaysBefore": current["default_reminder_days_before"],
            "defaultReminderChannel": current["default_reminder_channel"],
            "allowClientNotification": current["allow_client_notification"],
            "autoCreateReceivableOnClose": current["auto_create_receivable_on_close"],
            "openApiEnabled": current["open_api_enabled"],
            "externalSyncMode": current["external_sync_mode"],
        }
        workspace.settings = settings
        await self.db.flush()
        return FinanceSettingsRead.model_validate(current)

    async def get_overview(self, workspace_id: UUID) -> FinanceOverviewRead:
        now = self._utc_now()
        entries = await self.list_entries(workspace_id, limit=250)
        integrations = await self.list_integrations(workspace_id)
        reminders = await self.list_reminders(
            workspace_id,
            status=FinanceReminderStatus.pending,
            limit=20,
        )

        open_receivables = [
            entry for entry in entries
            if entry.entry_type.value == "receivable" and entry.status in {
                FinanceEntryStatus.pending,
                FinanceEntryStatus.scheduled,
                FinanceEntryStatus.overdue,
            }
        ]
        overdue_entries = [
            entry for entry in open_receivables
            if entry.status == FinanceEntryStatus.overdue
            or (
                entry.due_date
                and self._as_utc(entry.due_date) < now
                and entry.status != FinanceEntryStatus.paid
            )
        ]
        scheduled_entries = [entry for entry in open_receivables if entry.status == FinanceEntryStatus.scheduled]
        paid_entries = [entry for entry in entries if entry.status == FinanceEntryStatus.paid]
        due_soon_entries = [
            entry for entry in open_receivables
            if entry.due_date
            and now <= self._as_utc(entry.due_date) <= now + timedelta(days=7)
        ]

        highlights: list[str] = []
        if overdue_entries:
            highlights.append(
                f"Existem {len(overdue_entries)} cobrança(s) em atraso exigindo ação imediata."
            )
        if reminders:
            highlights.append(
                f"Há {len(reminders)} lembrete(s) pendente(s) de cobrança ou acompanhamento."
            )
        connected_integrations = [
            item
            for item in integrations
            if item.status.value == "connected"
            and not is_paused_financial_integration(
                provider=item.provider,
                label=item.label,
                base_url=item.base_url,
                webhook_url=item.webhook_url,
                capabilities=item.capabilities,
                settings=item.settings,
                notes=item.notes,
            )
        ]
        if connected_integrations:
            providers = ", ".join(sorted({item.provider.value for item in connected_integrations}))
            highlights.append(f"Integrações financeiras conectadas: {providers}.")
        else:
            highlights.append("Nenhuma integração financeira está conectada ainda.")

        return FinanceOverviewRead(
            total_open_receivables=self._sum_amount(open_receivables),
            overdue_amount=self._sum_amount(overdue_entries),
            scheduled_amount=self._sum_amount(scheduled_entries),
            paid_amount=self._sum_amount(paid_entries),
            pending_reminders=len(reminders),
            entries_due_soon=len(due_soon_entries),
            upcoming_entries=[FinanceEntryRead.model_validate(entry) for entry in due_soon_entries[:8]],
            reminders=[FinanceReminderRead.model_validate(reminder) for reminder in reminders],
            integrations=[IntegrationConnectionRead.model_validate(connection) for connection in integrations],
            highlights=highlights,
        )

    async def _get_workspace(self, workspace_id: UUID) -> Workspace:
        workspace = await self.db.get(Workspace, workspace_id)
        if not workspace:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WKS-001", "message": "Workspace não encontrado."},
            )
        return workspace

    def _merge_finance_settings(self, settings: dict) -> dict:
        finance_settings = settings.get("finance", {}) if isinstance(settings, dict) else {}
        merged = {
            "reminders_enabled": finance_settings.get("remindersEnabled", FINANCE_DEFAULT_SETTINGS["remindersEnabled"]),
            "default_reminder_days_before": finance_settings.get("defaultReminderDaysBefore", FINANCE_DEFAULT_SETTINGS["defaultReminderDaysBefore"]),
            "default_reminder_channel": finance_settings.get("defaultReminderChannel", FINANCE_DEFAULT_SETTINGS["defaultReminderChannel"]),
            "allow_client_notification": finance_settings.get("allowClientNotification", FINANCE_DEFAULT_SETTINGS["allowClientNotification"]),
            "auto_create_receivable_on_close": finance_settings.get("autoCreateReceivableOnClose", FINANCE_DEFAULT_SETTINGS["autoCreateReceivableOnClose"]),
            "open_api_enabled": finance_settings.get("openApiEnabled", FINANCE_DEFAULT_SETTINGS["openApiEnabled"]),
            "external_sync_mode": finance_settings.get("externalSyncMode", FINANCE_DEFAULT_SETTINGS["externalSyncMode"]),
        }
        return merged

    async def _sync_reminders(self, entry: FinanceEntry, reminders_payload) -> None:
        if reminders_payload:
            await self._replace_reminders(entry, reminders_payload)
            return
        if entry.due_date:
            await self._create_default_reminder(entry)

    async def _replace_reminders(self, entry: FinanceEntry, reminders_payload) -> None:
        await self.db.execute(
            delete(FinanceReminder).where(
                FinanceReminder.workspace_id == entry.workspace_id,
                FinanceReminder.finance_entry_id == entry.id,
            )
        )
        for reminder_payload in reminders_payload:
            payload = (
                reminder_payload
                if isinstance(reminder_payload, dict)
                else reminder_payload.model_dump()
            )
            self.db.add(
                FinanceReminder(
                    workspace_id=entry.workspace_id,
                    finance_entry_id=entry.id,
                    remind_at=payload["remind_at"],
                    channel=payload.get("channel", "email"),
                    message=payload.get("message"),
                )
            )

    async def _create_default_reminder(self, entry: FinanceEntry) -> None:
        if not entry.due_date:
            return
        remind_at = entry.due_date - timedelta(days=entry.reminder_days_before or 3)
        self.db.add(
            FinanceReminder(
                workspace_id=entry.workspace_id,
                finance_entry_id=entry.id,
                remind_at=remind_at,
                channel="whatsapp" if entry.notify_client else "email",
                message=f"Lembrete automático para {entry.title}",
            )
        )

    @staticmethod
    def _sum_amount(entries: list[FinanceEntry]) -> float:
        total = Decimal("0")
        for entry in entries:
            total += Decimal(str(entry.amount or 0))
        return float(total.quantize(Decimal("0.01")))

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
