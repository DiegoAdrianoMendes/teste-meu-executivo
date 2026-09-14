from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import re
import unicodedata
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, case, delete, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.klm_runtime import external_delivery_allowed
from app.core.logging_privacy import safe_error_type
from app.db.tenant import get_workspace_context
from app.models.billing import Plan, Subscription
from app.models.entitlement import EntitlementKey
from app.models.flow import Flow
from app.models.klm_operations import MessageDeliveryStatus
from app.models.matter import Matter
from app.models.whatsapp import (
    ConnectionType,
    WaConnectionStatus,
    WaMessageDirection,
    WaMessageStatus,
    WhatsAppConnection,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    CampaignSendJobStatus,
    CampaignStatus,
    MetaTemplateStatus,
    OptInStatus,
    WhatsAppAudienceList,
    WhatsAppAudienceListMember,
    WhatsAppCampaign,
    WhatsAppCampaignAudience,
    WhatsAppCampaignDeliveryEvent,
    WhatsAppCampaignRecipient,
    WhatsAppCampaignSendJob,
    WhatsAppCampaignTemplateSnapshot,
    WhatsAppMetaTemplate,
    WhatsAppOptInRecord,
    WhatsAppSuppressionEntry,
)
from app.models.workspace import Workspace
from app.schemas.omnichannel_v6 import InternalMessageDeliveryReceiptIngest
from app.schemas.whatsapp_campaign import (
    AudienceListCreate,
    AudienceListImportResult,
    AudienceListMemberCreate,
    AudienceListMemberRead,
    AudienceListRead,
    AudienceListUpdate,
    CampaignAudienceFilter,
    CampaignAudienceRead,
    CampaignCreate,
    CampaignLaunchRequest,
    CampaignRead,
    CampaignRecipientRead,
    CampaignTemplateSnapshotRead,
    CampaignUpdate,
    MetaTemplateRead,
    OptInRecordCreate,
    OptInRecordRead,
    SuppressionEntryCreate,
    SuppressionEntryRead,
)
from app.services.commercial_catalog_service import (
    CommercialCatalogService,
    CommercialContractNotFoundError,
)
from app.services.entitlement_service import (
    CurrentStockLimitExceededError,
    EntitlementEnforcementService,
    EntitlementError,
)
from app.services.flow_service import FlowService
from app.services.omnichannel_legacy_bridge_service import (
    OmnichannelLegacyBridgeService,
)
from app.services.omnichannel_v6_service import OmnichannelV6Service
from app.services.plan_policy_service import PlanPolicyService
from app.services.whatsapp.connection_health_service import (
    WhatsAppConnectionHealthService,
)
from app.services.whatsapp.connection_service import WhatsAppConnectionService
from app.services.whatsapp.contact_capacity_service import WhatsAppContactCapacityService
from app.services.whatsapp.errors import (
    MetaErrorDisposition,
    MetaErrorPolicy,
    WhatsAppProviderError,
    classify_meta_error,
)
from app.services.whatsapp.helpers import (
    normalize_phone_number,
    phone_number_lookup_variants,
    phone_number_variants,
)
from app.services.whatsapp.meta_cloud_client import MetaCloudClient

CAMPAIGN_PROCESS_BATCH_SIZE = 8
CAMPAIGN_JOB_BATCH_SIZE = 50
# Retry padrão por job (erros não mapeados): nº máximo de tentativas.
CAMPAIGN_JOB_MAX_ATTEMPTS = 3
# Backoff do LOTE inteiro quando a Meta devolve 130429 (throughput).
CAMPAIGN_BATCH_BACKOFF_SECONDS = 60


def _expand_phone_variants(phones: Iterable[str]) -> set[str]:
    return {variant for phone in phones for variant in phone_number_lookup_variants(phone)}


# Kill switch automático: pausa a campanha quando failed/(sent+failed)
# excede o limiar, com um mínimo de tentativas para evitar falso positivo.
CAMPAIGN_FAILURE_RATE_KILL_SWITCH = 0.20
CAMPAIGN_KILL_SWITCH_MIN_ATTEMPTS = 20
# Prefixo usado para marcar jobs com entrega incerta (reconciliação via webhook).
DELIVERY_UNCERTAIN_ERROR_PREFIX = "WA-DELIVERY-UNCERTAIN"

# Reagendamento por falta de folga na janela deslizante de 24h (tier/rampa,
# W1.5). Diferente do backoff de throughput (130429, CAMPAIGN_BATCH_BACKOFF_
# SECONDS): aqui não é um erro momentâneo da Meta, é quota diária real —
# o intervalo é maior para não martelar a query em vão, mas curto o
# bastante para não perder capacidade assim que a janela liberar folga
# (ela desliza a cada segundo; qualquer reprocessamento periódico do
# worker recalcula o valor exato).
TIER_QUOTA_DEFER_SECONDS = 900  # 15 min
MONTHLY_QUOTA_ERROR_CODE = "WA-CAM-013"
CAMPAIGN_CAPABILITY_ERROR_CODE = "WA-CAM-014"
# V4 (backend): estados em que "lançar" não faz sentido e o endpoint explícito recusa.
_LAUNCH_REJECTED_STATUSES = frozenset(
    {
        CampaignStatus.paused,
        CampaignStatus.completed,
        CampaignStatus.stopped,
        CampaignStatus.failed,
    }
)
_LAUNCH_STATUS_LABELS = {
    CampaignStatus.paused: "pausada",
    CampaignStatus.completed: "concluída",
    CampaignStatus.stopped: "interrompida",
    CampaignStatus.failed: "com falha",
}
_TEMPLATE_NO_REJECTION_REASONS = frozenset({"", "NONE", "NULL"})


def _real_rejected_reason(value: object) -> str | None:
    """Motivo real de rejeição, ou None para ausente/vazio/"NONE" (case-insensitive)."""
    text = str(value or "").strip()
    if text.upper() in _TEMPLATE_NO_REJECTION_REASONS:
        return None
    return text


def _merge_synced_raw_payload(previous: dict | None, item: dict) -> dict:
    """V29: o `GET message_templates` da Meta devolve `rejected_reason: "NONE"` para um
    template já rejeitado, e o sync sobrescrevia o dicionário inteiro, apagando o motivo
    que o webhook `message_template_status_update` tinha gravado e as chaves locais
    (catálogo de utilidade, bindings, timestamps de submissão, análise da OAB). Parte do
    que já existia, aplica o item da Meta por cima e mantém o motivo real quando a Meta
    manda vazio. `source` vira sempre `meta_sync`: um `local_draft` que sobrevivesse
    voltaria a ser rascunho na tela e sumiria da detecção de remoção."""
    merged = {key: value for key, value in dict(previous or {}).items() if key != "source"}
    previous_reason = _real_rejected_reason(merged.get("rejected_reason"))
    merged.update(item)
    item_reason = _real_rejected_reason(item.get("rejected_reason"))
    item_status = str(item.get("status") or "").strip().upper()
    if item_reason is None:
        if previous_reason is not None and item_status == "REJECTED":
            # A Meta manda "NONE" no GET de um template ainda rejeitado: vale o webhook.
            merged["rejected_reason"] = previous_reason
        else:
            # Aprovado, pendente ou pausado: motivo antigo não pode sobreviver.
            merged.pop("rejected_reason", None)
    merged["source"] = "meta_sync"
    return merged


class CampaignCapabilityDeniedError(RuntimeError):
    """Canonical contract does not currently permit campaign recipients."""

    def __init__(self, denial_reason: str) -> None:
        self.denial_reason = denial_reason
        super().__init__(denial_reason)


# Rampa de aquecimento (W1.5): teto ADICIONAL e conservador aplicado por
# cima do limite do tier enquanto `ramp_up_stage` estiver neste mapa.
# Não é uma tabela oficial da Meta (a Meta não publica tetos por estágio
# de rampa) — é política interna do KLM para reduzir o risco de queda de
# qualidade em números novos/recém-aquecidos, com incrementos graduais
# até o número ser considerado "aquecido" (estágio > RAMP_UP_MAX_STAGE),
# quando só o teto do tier passa a valer. Ajustável conforme dados reais
# de quality rating por estágio.
RAMP_UP_STAGE_CAPS: dict[int, int] = {
    0: 50,
    1: 150,
    2: 500,
    3: 1_000,
}
RAMP_UP_MAX_STAGE = max(RAMP_UP_STAGE_CAPS)

logger = logging.getLogger(__name__)


def _canonical_delivery_status_event(
    wamid: str,
    provider_status: str,
    payload: dict[str, Any],
) -> str:
    return json.dumps(
        {
            "schemaVersion": 1,
            "wamid": str(wamid),
            "providerStatus": str(provider_status).strip().lower(),
            "payload": payload,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def whatsapp_delivery_status_event_key(
    wamid: str,
    provider_status: str,
    payload: dict[str, Any],
) -> str:
    """Stable key for one exact provider status receipt."""

    return hashlib.sha256(
        _canonical_delivery_status_event(wamid, provider_status, payload).encode("utf-8")
    ).hexdigest()


def whatsapp_delivery_status_occurred_at(
    payload: dict[str, Any],
    *,
    fallback: datetime | None = None,
) -> datetime:
    """Use the provider timestamp when present so replay cannot change event time."""

    raw_timestamp = payload.get("timestamp")
    try:
        timestamp = float(str(raw_timestamp))
    except (TypeError, ValueError):
        timestamp = 0
    if timestamp > 0:
        try:
            return datetime.fromtimestamp(timestamp, tz=UTC)
        except (OverflowError, OSError, ValueError):
            pass
    return fallback or datetime.now(UTC)


def _delivery_status_event_matches(
    event: WhatsAppCampaignDeliveryEvent,
    *,
    wamid: str,
    provider_status: str,
    mapped_status: CampaignRecipientStatus,
    payload: dict[str, Any],
) -> bool:
    return (
        event.wamid == wamid
        and event.status == mapped_status
        and _canonical_delivery_status_event(wamid, provider_status, event.payload)
        == _canonical_delivery_status_event(wamid, provider_status, payload)
    )


def _require_external_delivery(action: str) -> None:
    if not external_delivery_allowed():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "KLM-EXT-001",
                "message": f"{action} bloqueado enquanto entregas externas estiverem desabilitadas.",
            },
        )


class WhatsAppCampaignService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        connection_service: WhatsAppConnectionService,
        meta_client: MetaCloudClient,
    ) -> None:
        self.db = db
        self.connection_service = connection_service
        self.meta_client = meta_client

    async def list_meta_templates(
        self,
        workspace_id: UUID,
        *,
        connection_id: UUID | None = None,
    ) -> list[MetaTemplateRead]:
        stmt = select(WhatsAppMetaTemplate).where(
            WhatsAppMetaTemplate.workspace_id == workspace_id,
            WhatsAppMetaTemplate.removed_at_source_at.is_(None),
        )
        if connection_id:
            stmt = stmt.where(WhatsAppMetaTemplate.whatsapp_connection_id == connection_id)
        stmt = stmt.order_by(
            WhatsAppMetaTemplate.updated_at.desc(), WhatsAppMetaTemplate.name.asc()
        )
        result = await self.db.execute(stmt)
        return [MetaTemplateRead.model_validate(item) for item in result.scalars().all()]

    async def sync_meta_templates(
        self,
        workspace_id: UUID,
        *,
        connection_id: UUID | None = None,
    ) -> list[MetaTemplateRead]:
        _require_external_delivery("Sincronização de templates da Meta")
        connection = await self._resolve_official_connection(workspace_id, connection_id)
        access_token = await self.connection_service.resolve_meta_access_token(connection)
        waba_id = connection.waba_id or connection.business_account_id
        if not waba_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-CAM-001",
                    "message": "A conexão oficial não possui WABA vinculada.",
                },
            )
        payload = await self.meta_client.get_message_templates(
            waba_id=waba_id, access_token=access_token
        )
        items = payload.get("data", [])
        observed_external_ids: set[str] = set()
        observed_keys: set[tuple[str, str]] = set()
        synced_at = self._utc_now()
        for item in items:
            name = item.get("name")
            language = item.get("language")
            if not name or not language:
                continue
            external_id = str(item.get("id") or "").strip()
            if external_id:
                observed_external_ids.add(external_id)
            observed_keys.add((str(name), str(language)))
            result = await self.db.execute(
                select(WhatsAppMetaTemplate).where(
                    WhatsAppMetaTemplate.workspace_id == workspace_id,
                    WhatsAppMetaTemplate.whatsapp_connection_id == connection.id,
                    WhatsAppMetaTemplate.name == name,
                    WhatsAppMetaTemplate.language == language,
                )
            )
            template = result.scalars().first() or WhatsAppMetaTemplate(
                workspace_id=workspace_id,
                whatsapp_connection_id=connection.id,
                name=name,
                language=language,
            )
            template.external_template_id = external_id or template.external_template_id
            template.category = item.get("category")
            template.status = self._normalize_meta_template_status(item.get("status"))
            template.quality_rating = (
                item.get("quality_rating")
                or item.get("quality_score", {}).get("score")
                or item.get("quality_score", {}).get("quality_rating")
            )
            template.components = item.get("components") or []
            template.raw_payload = _merge_synced_raw_payload(template.raw_payload, item)
            template.last_synced_at = synced_at
            template.removed_at_source_at = None
            self.db.add(template)

        local_result = await self.db.execute(
            select(WhatsAppMetaTemplate).where(
                WhatsAppMetaTemplate.workspace_id == workspace_id,
                WhatsAppMetaTemplate.whatsapp_connection_id == connection.id,
                WhatsAppMetaTemplate.external_template_id.is_not(None),
            )
        )
        for local_template in local_result.scalars().all():
            raw = local_template.raw_payload or {}
            provider_presence_was_observed = raw.get("source") != "local_draft" or bool(
                raw.get("last_status_event")
            )
            if not provider_presence_was_observed:
                # A criação 200 pode ainda não aparecer imediatamente na listagem
                # da WABA. Sem sync/webhook anterior, ausência não prova remoção.
                continue
            still_exists = (
                str(local_template.external_template_id) in observed_external_ids
                or (local_template.name, local_template.language) in observed_keys
            )
            if not still_exists and local_template.removed_at_source_at is None:
                local_template.removed_at_source_at = synced_at
                local_template.last_synced_at = synced_at
                self.db.add(local_template)
        await self.db.commit()
        return await self.list_meta_templates(workspace_id, connection_id=connection.id)

    async def list_opt_ins(self, workspace_id: UUID) -> list[OptInRecordRead]:
        result = await self.db.execute(
            select(WhatsAppOptInRecord)
            .where(WhatsAppOptInRecord.workspace_id == workspace_id)
            .order_by(WhatsAppOptInRecord.updated_at.desc())
        )
        return [OptInRecordRead.model_validate(item) for item in result.scalars().all()]

    async def upsert_opt_in(
        self, workspace_id: UUID, payload: OptInRecordCreate
    ) -> OptInRecordRead:
        phone = normalize_phone_number(payload.phone)
        if not phone:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "WA-CAM-002", "message": "Telefone inválido para opt-in."},
            )
        result = await self.db.execute(
            select(WhatsAppOptInRecord).where(
                WhatsAppOptInRecord.workspace_id == workspace_id,
                WhatsAppOptInRecord.phone.in_(phone_number_variants(phone)),
                WhatsAppOptInRecord.channel == payload.channel,
            )
        )
        record = result.scalars().first() or WhatsAppOptInRecord(
            workspace_id=workspace_id,
            phone=phone,
            channel=payload.channel,
        )
        record.phone = phone
        record.contact_id = payload.contact_id
        record.status = payload.status
        record.source = payload.source
        record.details = payload.details
        if payload.status == OptInStatus.opted_in:
            record.granted_at = self._utc_now()
            record.revoked_at = None
        else:
            record.revoked_at = self._utc_now()
        self.db.add(record)
        await self.db.commit()
        await self.db.refresh(record)
        return OptInRecordRead.model_validate(record)

    async def list_suppressions(self, workspace_id: UUID) -> list[SuppressionEntryRead]:
        result = await self.db.execute(
            select(WhatsAppSuppressionEntry)
            .where(WhatsAppSuppressionEntry.workspace_id == workspace_id)
            .order_by(WhatsAppSuppressionEntry.updated_at.desc())
        )
        return [SuppressionEntryRead.model_validate(item) for item in result.scalars().all()]

    async def upsert_suppression(
        self, workspace_id: UUID, payload: SuppressionEntryCreate
    ) -> SuppressionEntryRead:
        phone = normalize_phone_number(payload.phone)
        if not phone:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "WA-CAM-003", "message": "Telefone inválido para supressão."},
            )
        result = await self.db.execute(
            select(WhatsAppSuppressionEntry).where(
                WhatsAppSuppressionEntry.workspace_id == workspace_id,
                WhatsAppSuppressionEntry.phone.in_(phone_number_variants(phone)),
            )
        )
        entry = result.scalars().first() or WhatsAppSuppressionEntry(
            workspace_id=workspace_id,
            phone=phone,
        )
        entry.phone = phone
        entry.contact_id = payload.contact_id
        entry.reason = payload.reason
        entry.details = payload.details
        entry.is_active = payload.is_active
        self.db.add(entry)
        await self.db.commit()
        await self.db.refresh(entry)
        return SuppressionEntryRead.model_validate(entry)

    async def list_audience_lists(
        self,
        workspace_id: UUID,
        *,
        include_archived: bool = False,
        mask_sensitive: bool = False,
    ) -> list[AudienceListRead]:
        stmt = select(WhatsAppAudienceList).where(WhatsAppAudienceList.workspace_id == workspace_id)
        if not include_archived:
            stmt = stmt.where(WhatsAppAudienceList.status == "active")
        result = await self.db.execute(stmt.order_by(WhatsAppAudienceList.updated_at.desc()))
        return [
            self._to_audience_list_read(item, include_members=False, mask_sensitive=mask_sensitive)
            for item in result.scalars().all()
        ]

    async def get_audience_list(
        self,
        workspace_id: UUID,
        audience_list_id: UUID,
        *,
        mask_sensitive: bool = False,
    ) -> AudienceListRead:
        item = await self._get_audience_list(workspace_id, audience_list_id)
        return self._to_audience_list_read(
            item, include_members=True, mask_sensitive=mask_sensitive
        )

    async def create_audience_list(
        self,
        workspace_id: UUID,
        payload: AudienceListCreate,
        *,
        acting_user_id: UUID,
    ) -> AudienceListRead:
        await self._ensure_audience_list_name_available(workspace_id, payload.name)
        item = WhatsAppAudienceList(
            workspace_id=workspace_id,
            created_by_id=acting_user_id,
            name=payload.name.strip(),
            description=payload.description,
            source=payload.source,
            status="active",
        )
        self.db.add(item)
        await self.db.commit()
        return await self.get_audience_list(workspace_id, item.id)

    async def update_audience_list(
        self,
        workspace_id: UUID,
        audience_list_id: UUID,
        payload: AudienceListUpdate,
    ) -> AudienceListRead:
        item = await self._get_audience_list(workspace_id, audience_list_id)
        if payload.name is not None and payload.name.strip() != item.name:
            await self._ensure_audience_list_name_available(
                workspace_id,
                payload.name,
                exclude_id=item.id,
            )
            item.name = payload.name.strip()
        if "description" in payload.model_fields_set:
            item.description = payload.description
        if payload.status is not None:
            item.status = payload.status
        await self.db.commit()
        return await self.get_audience_list(workspace_id, item.id)

    async def add_audience_list_member(
        self,
        workspace_id: UUID,
        audience_list_id: UUID,
        payload: AudienceListMemberCreate,
    ) -> AudienceListRead:
        audience_list = await self._get_audience_list(workspace_id, audience_list_id)
        phone = normalize_phone_number(payload.phone)
        if not phone:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "WA-LIST-001", "message": "Telefone inválido para a lista."},
            )
        contact, matter = await self._resolve_member_links(
            workspace_id,
            contact_id=payload.contact_id,
            matter_id=payload.matter_id,
            phone=phone,
        )
        existing = (
            (
                await self.db.execute(
                    select(WhatsAppAudienceListMember).where(
                        WhatsAppAudienceListMember.workspace_id == workspace_id,
                        WhatsAppAudienceListMember.audience_list_id == audience_list_id,
                        WhatsAppAudienceListMember.phone.in_(phone_number_variants(phone)),
                    )
                )
            )
            .scalars()
            .first()
        )
        member = existing or WhatsAppAudienceListMember(
            workspace_id=workspace_id,
            audience_list_id=audience_list_id,
            phone=phone,
        )
        member.phone = phone
        member.contact_id = contact.id if contact else payload.contact_id
        member.matter_id = matter.id if matter else payload.matter_id
        member.name = payload.name or (contact.name if contact else None)
        member.email = payload.email
        member.status = "valid"
        member.member_metadata = payload.member_metadata
        self.db.add(member)
        await self.db.flush()
        await self._refresh_audience_list_counts(
            audience_list, invalid=0, duplicates=1 if existing else 0
        )
        await self.db.commit()
        return await self.get_audience_list(workspace_id, audience_list_id)

    async def import_audience_list_csv(
        self,
        workspace_id: UUID,
        audience_list_id: UUID,
        csv_text: str,
        *,
        filename: str | None = None,
    ) -> AudienceListImportResult:
        audience_list = await self._get_audience_list(workspace_id, audience_list_id)
        reader, delimiter = self._csv_reader(csv_text)
        rows = list(reader)
        if len(rows) > 25_000:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={
                    "code": "WA-LIST-002",
                    "message": "A importação aceita no máximo 25.000 linhas por arquivo.",
                },
            )

        aliases = {
            "phone": {"phone", "telefone", "celular", "whatsapp", "numero", "numero_whatsapp"},
            "name": {"name", "nome", "cliente", "contato"},
            "email": {"email", "e_mail"},
            "source": {"source", "origem"},
            "campaign": {"campaign", "campanha"},
            "opt_in": {"opt_in", "optin", "consentimento", "autorizado"},
        }
        headers = {
            canonical: next(
                (
                    raw
                    for raw in (reader.fieldnames or [])
                    if self._normalize_csv_header(raw) in choices
                ),
                None,
            )
            for canonical, choices in aliases.items()
        }
        if headers["phone"] is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "WA-LIST-003",
                    "message": "O CSV precisa de uma coluna telefone, celular ou whatsapp.",
                },
            )

        unique_rows: dict[str, tuple[int, dict[str, str]]] = {}
        invalid = 0
        duplicates = 0
        normalized = 0
        for row_number, raw_row in enumerate(rows, start=2):
            row = {
                str(key): str(value or "").strip()
                for key, value in raw_row.items()
                if key is not None
            }
            raw_phone = row.get(headers["phone"] or "", "")
            phone = normalize_phone_number(raw_phone)
            if not phone:
                invalid += 1
                continue
            if raw_phone.strip() != phone:
                normalized += 1
            if phone in unique_rows:
                duplicates += 1
            unique_rows[phone] = (row_number, row)

        contact_rows = await self._contacts_for_phones(workspace_id, set(unique_rows))
        existing_members = await self._audience_members_for_phones(
            workspace_id,
            audience_list_id,
            set(unique_rows),
        )
        explicit_opt_in_phones = {
            phone
            for phone, (_, row) in unique_rows.items()
            if self._csv_truthy(row.get(headers["opt_in"] or ""))
        }
        opt_in_records = await self._opt_in_records_for_phones(
            workspace_id,
            explicit_opt_in_phones,
        )
        imported = 0
        updated = 0
        opt_ins_recorded = 0
        for phone, (row_number, row) in unique_rows.items():
            contact, matter = contact_rows.get(phone, (None, None))
            member = existing_members.get(phone)
            if member is None:
                member = WhatsAppAudienceListMember(
                    workspace_id=workspace_id,
                    audience_list_id=audience_list_id,
                    phone=phone,
                )
                imported += 1
            else:
                updated += 1
            member.contact_id = contact.id if contact else None
            member.matter_id = matter.id if matter else None
            member.name = row.get(headers["name"] or "") or (contact.name if contact else None)
            member.email = row.get(headers["email"] or "") or None
            member.status = "valid"
            member.source_row = row_number
            member.member_metadata = {
                "source": row.get(headers["source"] or "") or "csv",
                "campaign": row.get(headers["campaign"] or "") or None,
                "filename": filename,
            }
            self.db.add(member)
            if phone in explicit_opt_in_phones:
                record = opt_in_records.get(phone) or WhatsAppOptInRecord(
                    workspace_id=workspace_id,
                    phone=phone,
                    channel="whatsapp",
                )
                record.contact_id = contact.id if contact else None
                record.status = OptInStatus.opted_in
                record.source = "csv_explicit"
                record.details = {"filename": filename, "explicitColumn": True}
                record.granted_at = self._utc_now()
                record.revoked_at = None
                self.db.add(record)
                opt_ins_recorded += 1

        audience_list.source = "csv"
        audience_list.import_metadata = {
            "filename": filename,
            "delimiter": delimiter,
            "headers": [self._normalize_csv_header(value) for value in (reader.fieldnames or [])],
            "totalRows": len(rows),
            "imported": imported,
            "updated": updated,
            "invalid": invalid,
            "duplicates": duplicates,
            "optInsRecorded": opt_ins_recorded,
            "normalized": normalized,
            "importedAt": self._utc_now().isoformat(),
        }
        await self.db.flush()
        await self._refresh_audience_list_counts(
            audience_list, invalid=invalid, duplicates=duplicates
        )
        await self.db.commit()
        current = await self.get_audience_list(workspace_id, audience_list_id)
        return AudienceListImportResult(
            audience_list=current,
            imported=imported,
            updated=updated,
            invalid=invalid,
            duplicates=duplicates,
            total_rows=len(rows),
            opt_ins_recorded=opt_ins_recorded,
            normalized=normalized,
        )

    async def export_audience_list_csv(self, workspace_id: UUID, audience_list_id: UUID) -> str:
        audience_list = await self._get_audience_list(workspace_id, audience_list_id)
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";", lineterminator="\n")
        writer.writerow(
            ["nome", "telefone", "email", "status", "contact_id", "matter_id", "origem"]
        )
        for member in audience_list.members:
            writer.writerow(
                [
                    member.name or "",
                    member.phone,
                    member.email or "",
                    member.status,
                    str(member.contact_id or ""),
                    str(member.matter_id or ""),
                    str((member.member_metadata or {}).get("source") or audience_list.source),
                ]
            )
        return "\ufeff" + output.getvalue()

    async def list_campaigns(self, workspace_id: UUID) -> list[CampaignRead]:
        # S35: a listagem carregava TODOS os destinatários de todas as campanhas
        # só para recontar e tirar uma amostra de 8. Agora: contadores por
        # agregação e amostra por janela, sem tocar a relação `recipients`.
        result = await self.db.execute(
            select(WhatsAppCampaign)
            .execution_options(populate_existing=True)
            .options(
                selectinload(WhatsAppCampaign.audience),
                selectinload(WhatsAppCampaign.template_snapshot),
            )
            .where(WhatsAppCampaign.workspace_id == workspace_id)
            .order_by(WhatsAppCampaign.created_at.desc())
        )
        campaigns = list(result.scalars().all())
        campaign_ids = [item.id for item in campaigns]
        counts = await self._recipient_status_counts(workspace_id, campaign_ids)
        samples = await self._recipient_samples(workspace_id, campaign_ids)
        return [
            self._to_campaign_read(
                item, projection=(counts.get(item.id, {}), samples.get(item.id, []))
            )
            for item in campaigns
        ]

    async def _recipient_status_counts(
        self, workspace_id: UUID, campaign_ids: list[UUID]
    ) -> dict[UUID, dict[CampaignRecipientStatus, int]]:
        if not campaign_ids:
            return {}
        rows = await self.db.execute(
            select(
                WhatsAppCampaignRecipient.campaign_id,
                WhatsAppCampaignRecipient.status,
                func.count(WhatsAppCampaignRecipient.id),
            )
            .where(
                WhatsAppCampaignRecipient.workspace_id == workspace_id,
                WhatsAppCampaignRecipient.campaign_id.in_(campaign_ids),
            )
            .group_by(WhatsAppCampaignRecipient.campaign_id, WhatsAppCampaignRecipient.status)
        )
        counts: dict[UUID, dict[CampaignRecipientStatus, int]] = {}
        for campaign_id, status_value, total in rows.all():
            counts.setdefault(campaign_id, {})[status_value] = int(total)
        return counts

    async def _recipient_samples(
        self, workspace_id: UUID, campaign_ids: list[UUID], *, limit: int = 8
    ) -> dict[UUID, list[WhatsAppCampaignRecipient]]:
        """Os `limit` destinatários mais recentes de cada campanha (a tela de lista
        mostra o último erro e os motivos de falha a partir deles)."""
        if not campaign_ids:
            return {}
        ranked = (
            select(
                WhatsAppCampaignRecipient.id.label("id"),
                func.row_number()
                .over(
                    partition_by=WhatsAppCampaignRecipient.campaign_id,
                    order_by=(
                        WhatsAppCampaignRecipient.created_at.desc(),
                        WhatsAppCampaignRecipient.id.desc(),
                    ),
                )
                .label("rn"),
            )
            .where(
                WhatsAppCampaignRecipient.workspace_id == workspace_id,
                WhatsAppCampaignRecipient.campaign_id.in_(campaign_ids),
            )
            .subquery()
        )
        rows = await self.db.execute(
            select(WhatsAppCampaignRecipient)
            .join(ranked, ranked.c.id == WhatsAppCampaignRecipient.id)
            .where(ranked.c.rn <= limit)
            .order_by(
                WhatsAppCampaignRecipient.campaign_id,
                WhatsAppCampaignRecipient.created_at.desc(),
                WhatsAppCampaignRecipient.id.desc(),
            )
        )
        samples: dict[UUID, list[WhatsAppCampaignRecipient]] = {}
        for recipient in rows.scalars().all():
            samples.setdefault(recipient.campaign_id, []).append(recipient)
        return samples

    async def get_campaign(self, workspace_id: UUID, campaign_id: UUID) -> CampaignRead:
        campaign = await self._get_campaign(workspace_id, campaign_id)
        return self._to_campaign_read(campaign)

    async def create_campaign(
        self,
        workspace_id: UUID,
        payload: CampaignCreate,
        *,
        acting_user_id: UUID,
        idempotency_key: str | None = None,
        idempotency_fingerprint_override: str | None = None,
    ) -> CampaignRead:
        idempotency_fingerprint = None
        if idempotency_key:
            if idempotency_fingerprint_override:
                idempotency_fingerprint = idempotency_fingerprint_override
            else:
                canonical_payload = payload.model_dump(mode="json", exclude_none=True)
                idempotency_fingerprint = hashlib.sha256(
                    json.dumps(canonical_payload, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
        if idempotency_key:
            existing = await self.db.scalar(
                select(WhatsAppCampaign).where(
                    WhatsAppCampaign.workspace_id == workspace_id,
                    WhatsAppCampaign.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if (
                    existing.idempotency_fingerprint
                    and existing.idempotency_fingerprint != idempotency_fingerprint
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "code": "WA-CAM-IDEMPOTENCY-MISMATCH",
                            "message": "A chave de idempotência já foi usada para outro envio.",
                        },
                    )
                return await self.get_campaign(workspace_id, existing.id)
        connection = await self._resolve_official_connection(
            workspace_id, payload.whatsapp_connection_id
        )
        template = await self._get_template(workspace_id, payload.meta_template_id)
        self._validate_template_connection(template, connection.id)
        self._validate_template_parameters(
            template,
            header_parameters=payload.header_parameters,
            body_parameters=payload.body_parameters,
            button_parameters=payload.button_parameters,
        )
        await self._validate_follow_up_flow(workspace_id, payload.follow_up_flow_id)
        scheduled_at = self._normalize_datetime(payload.scheduled_at)
        campaign = WhatsAppCampaign(
            workspace_id=workspace_id,
            whatsapp_connection_id=connection.id,
            created_by_id=acting_user_id,
            idempotency_key=idempotency_key,
            idempotency_fingerprint=idempotency_fingerprint,
            title=payload.title,
            description=payload.description,
            follow_up_flow_id=payload.follow_up_flow_id,
            flow_start_mode=payload.flow_start_mode,
            scheduled_at=scheduled_at,
            # Agendar configura o momento; lançar materializa público e jobs.
            status=CampaignStatus.draft,
        )
        audience_preview = await self._build_audience_preview(workspace_id, payload.audience_filter)
        campaign.total_recipients = audience_preview["total"]
        campaign.eligible_recipients = audience_preview["eligible"]
        campaign.blocked_recipients = audience_preview["blocked"]
        campaign.audience = WhatsAppCampaignAudience(
            filters=payload.audience_filter.model_dump(mode="json", exclude_none=True),
            estimated_recipients=audience_preview["eligible"],
            sample_recipients=audience_preview["sample"],
        )
        campaign.template_snapshot = self._build_template_snapshot(
            template,
            header_parameters=payload.header_parameters,
            body_parameters=payload.body_parameters,
            button_parameters=payload.button_parameters,
        )
        self.db.add(campaign)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            if idempotency_key:
                existing = await self.db.scalar(
                    select(WhatsAppCampaign).where(
                        WhatsAppCampaign.workspace_id == workspace_id,
                        WhatsAppCampaign.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    if (
                        existing.idempotency_fingerprint
                        and existing.idempotency_fingerprint != idempotency_fingerprint
                    ):
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail={
                                "code": "WA-CAM-IDEMPOTENCY-MISMATCH",
                                "message": "A chave de idempotência já foi usada para outro envio.",
                            },
                        )
                    return await self.get_campaign(workspace_id, existing.id)
            raise
        return await self.get_campaign(workspace_id, campaign.id)

    async def update_campaign(
        self,
        workspace_id: UUID,
        campaign_id: UUID,
        payload: CampaignUpdate,
    ) -> CampaignRead:
        campaign = await self._get_campaign(workspace_id, campaign_id)
        if campaign.status not in {
            CampaignStatus.draft,
            CampaignStatus.scheduled,
            CampaignStatus.paused,
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-CAM-004",
                    "message": "Só é possível editar campanhas em rascunho, agendadas ou pausadas.",
                },
            )
        if payload.title is not None:
            campaign.title = payload.title
        if payload.description is not None:
            campaign.description = payload.description
        if payload.scheduled_at is not None:
            campaign.scheduled_at = self._normalize_datetime(payload.scheduled_at)
            campaign.status = (
                CampaignStatus.scheduled
                if self._is_future(campaign.scheduled_at)
                else CampaignStatus.draft
            )
        if "follow_up_flow_id" in payload.model_fields_set:
            await self._validate_follow_up_flow(workspace_id, payload.follow_up_flow_id)
            campaign.follow_up_flow_id = payload.follow_up_flow_id
            if payload.follow_up_flow_id is None:
                campaign.flow_start_mode = "manual"
        if payload.flow_start_mode is not None:
            campaign.flow_start_mode = payload.flow_start_mode
        if payload.audience_filter is not None:
            preview = await self._build_audience_preview(workspace_id, payload.audience_filter)
            if campaign.audience is None:
                campaign.audience = WhatsAppCampaignAudience(filters={})
            campaign.audience.filters = payload.audience_filter.model_dump(
                mode="json", exclude_none=True
            )
            campaign.audience.estimated_recipients = preview["eligible"]
            campaign.audience.sample_recipients = preview["sample"]
            campaign.total_recipients = preview["total"]
            campaign.eligible_recipients = preview["eligible"]
            campaign.blocked_recipients = preview["blocked"]
        if payload.meta_template_id is not None:
            template = await self._get_template(workspace_id, payload.meta_template_id)
            self._validate_template_connection(template, campaign.whatsapp_connection_id)
            header_parameters = payload.header_parameters or (
                campaign.template_snapshot.header_parameters if campaign.template_snapshot else []
            )
            body_parameters = payload.body_parameters or (
                campaign.template_snapshot.body_parameters if campaign.template_snapshot else []
            )
            button_parameters = payload.button_parameters or (
                campaign.template_snapshot.button_parameters if campaign.template_snapshot else []
            )
            self._validate_template_parameters(
                template,
                header_parameters=header_parameters,
                body_parameters=body_parameters,
                button_parameters=button_parameters,
            )
            campaign.template_snapshot = self._build_template_snapshot(
                template,
                header_parameters=header_parameters,
                body_parameters=body_parameters,
                button_parameters=button_parameters,
            )
        else:
            if campaign.template_snapshot is not None and payload.header_parameters is not None:
                campaign.template_snapshot.header_parameters = payload.header_parameters
            if campaign.template_snapshot is not None and payload.body_parameters is not None:
                campaign.template_snapshot.body_parameters = payload.body_parameters
            if campaign.template_snapshot is not None and payload.button_parameters is not None:
                campaign.template_snapshot.button_parameters = payload.button_parameters
        await self.db.commit()
        return await self.get_campaign(workspace_id, campaign.id)

    async def launch_campaign(
        self,
        workspace_id: UUID,
        campaign_id: UUID,
        payload: CampaignLaunchRequest,
        *,
        reject_terminal_status: bool = False,
    ) -> CampaignRead:
        if not payload.dry_run:
            _require_external_delivery("Lançamento de campanha")
        campaign = await self._get_campaign(
            workspace_id,
            campaign_id,
            lock=not payload.dry_run,
        )
        if not payload.dry_run and campaign.status != CampaignStatus.draft:
            if reject_terminal_status and campaign.status in _LAUNCH_REJECTED_STATUSES:
                # V4 (backend): o endpoint explícito responde 409 em vez de devolver a
                # campanha em silêncio. `scheduled`/`running` seguem silenciosos: é o
                # replay idempotente de um lançamento que já aconteceu (envio rápido,
                # duplo clique, flows e API pública dependem disso).
                label = _LAUNCH_STATUS_LABELS[campaign.status]
                hint = (
                    "Campanha pausada se retoma, não se relança."
                    if campaign.status == CampaignStatus.paused
                    else "Para enviar de novo, duplique a campanha."
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "WA-CAM-015",
                        "message": (
                            f"A campanha está {label}; o lançamento só vale em rascunho. {hint}"
                        ),
                        "status": campaign.status.value,
                    },
                )
            return await self.get_campaign(workspace_id, campaign.id)
        connection = await self._resolve_official_connection(
            workspace_id, campaign.whatsapp_connection_id
        )
        if campaign.template_snapshot is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-CAM-005",
                    "message": "A campanha não possui template oficial configurado.",
                },
            )
        if campaign.template_snapshot.status not in {
            MetaTemplateStatus.approved,
            MetaTemplateStatus.pending,
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-CAM-006",
                    "message": "O template selecionado não está apto para disparo oficial.",
                },
            )
        if not payload.dry_run and campaign.template_snapshot.status != MetaTemplateStatus.approved:
            # Disparo real exige template aprovado; pending só é aceito em dry-run.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-CAM-012",
                    "message": (
                        "O disparo real exige template aprovado pela Meta. "
                        "O template selecionado ainda está pendente; use o dry-run "
                        "para testar a audiência enquanto aguarda a aprovação."
                    ),
                },
            )

        audience_filter = CampaignAudienceFilter.model_validate(
            campaign.audience.filters if campaign.audience else {}
        )
        preview = await self._build_audience_preview(workspace_id, audience_filter)
        if payload.dry_run:
            self._apply_campaign_audience_preview(
                campaign=campaign,
                audience_filter=audience_filter,
                preview=preview,
            )
            await self.db.commit()
            return await self.get_campaign(workspace_id, campaign.id)

        now = self._utc_now()
        async with self.db.begin_nested():
            quota_moment = self._normalize_datetime(campaign.scheduled_at) or now
            quota_period, quota_reserved_at = await self._reserve_launch_quota(
                workspace_id=workspace_id,
                requested=preview["eligible"],
                at=quota_moment,
            )
            contacts_by_phone = await self._materialize_launch_contacts(
                workspace_id=workspace_id,
                connection=connection,
                eligible_rows=preview["eligible_rows"],
            )
            self._apply_campaign_audience_preview(
                campaign=campaign,
                audience_filter=audience_filter,
                preview=preview,
            )
            await self._reset_campaign_runtime(campaign.id)
            recipients = []
            for item in preview["eligible_rows"]:
                canonical_phone = normalize_phone_number(item["phone"])
                contact = contacts_by_phone.get(canonical_phone or str(item["phone"]))
                matter = item.get("matter")
                recipient = WhatsAppCampaignRecipient(
                    campaign_id=campaign.id,
                    workspace_id=workspace_id,
                    contact_id=contact.id if contact else item.get("contact_id"),
                    matter_id=matter.id if matter else item.get("matter_id"),
                    template_id=(
                        campaign.template_snapshot.source_template_id
                        if campaign.template_snapshot
                        else None
                    ),
                    phone=canonical_phone or str(item["phone"]),
                    name=item.get("name") or (contact.name if contact else None),
                    status=CampaignRecipientStatus.scheduled
                    if self._is_future(campaign.scheduled_at, now)
                    else CampaignRecipientStatus.queued,
                    send_context={
                        **item["context"],
                        "follow_up_flow_id": str(campaign.follow_up_flow_id or ""),
                        "flow_start_mode": campaign.flow_start_mode,
                        "sequenceInitiatorUserId": (
                            str(campaign.created_by_id) if campaign.created_by_id else None
                        ),
                    },
                    quota_period_start=quota_period,
                    quota_reserved_at=quota_reserved_at,
                )
                self.db.add(recipient)
                recipients.append(recipient)
            await self.db.flush()

            for recipient in recipients:
                self.db.add(
                    WhatsAppCampaignSendJob(
                        campaign_id=campaign.id,
                        recipient_id=recipient.id,
                        workspace_id=workspace_id,
                        whatsapp_connection_id=connection.id,
                        status=CampaignSendJobStatus.scheduled
                        if self._is_future(campaign.scheduled_at, now)
                        else CampaignSendJobStatus.pending,
                        scheduled_for=self._normalize_datetime(campaign.scheduled_at),
                    )
                )
            campaign.status = (
                CampaignStatus.scheduled
                if self._is_future(campaign.scheduled_at, now)
                else CampaignStatus.running
            )
            campaign.launched_at = now if campaign.status == CampaignStatus.running else None
            await self.db.flush()
        await self.db.commit()

        if campaign.status == CampaignStatus.running:
            await self._process_ready_jobs(
                campaign.id,
                connection,
                workspace_id=workspace_id,
            )
        return await self.get_campaign(workspace_id, campaign.id)

    async def pause_campaign(self, workspace_id: UUID, campaign_id: UUID) -> CampaignRead:
        campaign = await self._get_campaign(workspace_id, campaign_id)
        campaign.status = CampaignStatus.paused
        campaign.paused_at = self._utc_now()
        await self.db.commit()
        return await self.get_campaign(workspace_id, campaign.id)

    async def resume_campaign(self, workspace_id: UUID, campaign_id: UUID) -> CampaignRead:
        _require_external_delivery("Retomada de campanha")
        campaign = await self._get_campaign(workspace_id, campaign_id)
        connection = await self._resolve_official_connection(
            workspace_id, campaign.whatsapp_connection_id
        )
        campaign.status = (
            CampaignStatus.scheduled
            if self._is_future(campaign.scheduled_at)
            else CampaignStatus.running
        )
        campaign.paused_at = None
        campaign.pause_reason = None
        campaign.paused_by_policy = False
        await self.db.commit()
        if campaign.status == CampaignStatus.running:
            await self._process_ready_jobs(
                campaign.id,
                connection,
                workspace_id=workspace_id,
            )
        return await self.get_campaign(workspace_id, campaign.id)

    async def stop_campaign(self, workspace_id: UUID, campaign_id: UUID) -> CampaignRead:
        campaign = await self._get_campaign(workspace_id, campaign_id)
        campaign.status = CampaignStatus.stopped
        campaign.completed_at = self._utc_now()
        releasable_recipient_ids = select(WhatsAppCampaignSendJob.recipient_id).where(
            WhatsAppCampaignSendJob.campaign_id == campaign.id,
            WhatsAppCampaignSendJob.status.in_(
                [CampaignSendJobStatus.pending, CampaignSendJobStatus.scheduled]
            ),
        )
        await self.db.execute(
            update(WhatsAppCampaignRecipient)
            .where(WhatsAppCampaignRecipient.id.in_(releasable_recipient_ids))
            .values(quota_period_start=None, quota_reserved_at=None)
        )
        await self.db.execute(
            delete(WhatsAppCampaignSendJob).where(
                WhatsAppCampaignSendJob.campaign_id == campaign.id,
                WhatsAppCampaignSendJob.status.in_(
                    [CampaignSendJobStatus.pending, CampaignSendJobStatus.scheduled]
                ),
            )
        )
        await self.db.commit()
        return await self.get_campaign(workspace_id, campaign.id)

    async def sync_delivery_status(
        self, wamid: str, provider_status: str, payload: dict[str, Any]
    ) -> None:
        mapped_status = self._map_delivery_status(provider_status)
        provider_event_key = whatsapp_delivery_status_event_key(
            wamid,
            provider_status,
            payload,
        )
        result = await self.db.execute(
            select(WhatsAppCampaignRecipient)
            .options(selectinload(WhatsAppCampaignRecipient.campaign))
            .where(WhatsAppCampaignRecipient.wamid == wamid)
            .with_for_update()
        )
        recipient = result.scalars().first()
        if recipient is None:
            recipient = await self._match_delivery_uncertain_recipient(wamid, payload)
        if recipient is None:
            existing_event = await self.db.scalar(
                select(WhatsAppCampaignDeliveryEvent).where(
                    WhatsAppCampaignDeliveryEvent.provider_event_key == provider_event_key
                )
            )
            if existing_event is not None and not _delivery_status_event_matches(
                existing_event,
                wamid=wamid,
                provider_status=provider_status,
                mapped_status=mapped_status,
                payload=payload,
            ):
                raise RuntimeError("WhatsApp delivery status receipt key collision")
            return

        # The recipient lock serializes exact concurrent redeliveries. Recheck
        # after acquiring it because another transaction may have committed
        # the receipt while this transaction was waiting for the lock.
        existing_event = await self.db.scalar(
            select(WhatsAppCampaignDeliveryEvent).where(
                WhatsAppCampaignDeliveryEvent.provider_event_key == provider_event_key
            )
        )
        if existing_event is not None:
            if not _delivery_status_event_matches(
                existing_event,
                wamid=wamid,
                provider_status=provider_status,
                mapped_status=mapped_status,
                payload=payload,
            ):
                raise RuntimeError("WhatsApp delivery status receipt key collision")
            await self._sync_canonical_delivery_status(
                recipient=recipient,
                provider_status=provider_status,
                mapped_status=mapped_status,
                payload=payload,
                occurred_at=whatsapp_delivery_status_occurred_at(payload),
            )
            # The receipt is durable even when starting the follow-up flow
            # failed transiently.  A provider replay must therefore retry the
            # missing launch, while the recipient row remains the idempotent
            # guard that prevents a second FlowRun after success.
            flow_run = None
            if (
                mapped_status
                in {CampaignRecipientStatus.delivered, CampaignRecipientStatus.read}
                and recipient.flow_run_id is None
                and recipient.campaign.flow_start_mode == "on_delivery"
            ):
                flow_run = await self._start_recipient_follow_up_flow(
                    recipient,
                    trigger_event="on_delivery",
                )
            await self.db.commit()
            if flow_run is not None:
                await self.db.refresh(flow_run)
                FlowService.publish_initial_flow_run(flow_run, recipient.workspace_id)
            return

        now = whatsapp_delivery_status_occurred_at(payload)
        recipient.status = mapped_status
        if mapped_status == CampaignRecipientStatus.delivered:
            recipient.delivered_at = now
        elif mapped_status == CampaignRecipientStatus.read:
            recipient.read_at = now
        elif mapped_status == CampaignRecipientStatus.failed:
            recipient.last_error = payload.get("errors", [{}])[0].get("title") or payload.get(
                "status"
            )
        self.db.add(
            WhatsAppCampaignDeliveryEvent(
                campaign_id=recipient.campaign_id,
                recipient_id=recipient.id,
                wamid=wamid,
                provider_event_key=provider_event_key,
                status=mapped_status,
                payload=payload,
                occurred_at=now,
            )
        )
        await self.db.flush()
        flow_run = None
        if mapped_status in {
            CampaignRecipientStatus.delivered,
            CampaignRecipientStatus.read,
        }:
            flow_run = await self._start_recipient_follow_up_flow(
                recipient,
                trigger_event="on_delivery",
            )
        await self._sync_canonical_delivery_status(
            recipient=recipient,
            provider_status=provider_status,
            mapped_status=mapped_status,
            payload=payload,
            occurred_at=now,
        )
        await self._refresh_campaign_counters(recipient.campaign_id)
        await self.db.commit()
        if flow_run is not None:
            await self.db.refresh(flow_run)
            FlowService.publish_initial_flow_run(flow_run, recipient.workspace_id)

    async def _sync_canonical_delivery_status(
        self,
        *,
        recipient: WhatsAppCampaignRecipient,
        provider_status: str,
        mapped_status: CampaignRecipientStatus,
        payload: dict[str, Any],
        occurred_at: datetime,
    ) -> None:
        if not recipient.wamid:
            return
        status_mapping = {
            CampaignRecipientStatus.sent: MessageDeliveryStatus.sent,
            CampaignRecipientStatus.delivered: MessageDeliveryStatus.delivered,
            CampaignRecipientStatus.read: MessageDeliveryStatus.read,
            CampaignRecipientStatus.failed: MessageDeliveryStatus.failed,
        }
        canonical_status = status_mapping.get(mapped_status)
        if canonical_status is None:
            return
        campaign = recipient.campaign
        connection = await self.db.scalar(
            select(WhatsAppConnection).where(
                WhatsAppConnection.id == campaign.whatsapp_connection_id,
                WhatsAppConnection.workspace_id == recipient.workspace_id,
            )
        )
        if connection is None:
            return
        provider_event_key = whatsapp_delivery_status_event_key(
            recipient.wamid,
            provider_status,
            payload,
        )
        try:
            async with self.db.begin_nested():
                await WhatsAppConnectionService(self.db).ensure_omnichannel_projection(connection)
                await OmnichannelV6Service(self.db).ingest_delivery_receipt(
                    recipient.workspace_id,
                    InternalMessageDeliveryReceiptIngest(
                        provider="meta_cloud",
                        connection_key=f"whatsapp:{connection.id}",
                        external_event_id=f"campaign-status:{provider_event_key}",
                        external_message_id=recipient.wamid,
                        status=canonical_status,
                        provider_status=provider_status,
                        occurred_at=occurred_at,
                        payload=payload,
                        metadata={
                            "source": "whatsapp_campaign_status",
                            "campaignId": str(campaign.id),
                            "campaignRecipientId": str(recipient.id),
                        },
                    ),
                )
        except Exception as exc:
            context = dict(recipient.send_context or {})
            context["canonicalReceiptProjection"] = {
                "state": "failed_known",
                "status": canonical_status.value,
                "errorType": safe_error_type(exc),
                "checkedAt": self._utc_now().isoformat(),
            }
            recipient.send_context = context
            logger.error(
                "whatsapp_campaign.canonical_receipt_projection_failed "
                "campaign_id=%s recipient_id=%s error_type=%s",
                campaign.id,
                recipient.id,
                safe_error_type(exc),
            )

    async def trigger_follow_up_flow_on_reply(
        self,
        *,
        workspace_id: UUID,
        phone: str,
        matter_id: UUID | None,
        thread_id: UUID | None = None,
    ) -> UUID | None:
        """Start the most recent matching campaign flow exactly once after a reply."""
        normalized_phone = normalize_phone_number(phone)
        if not normalized_phone:
            return None
        result = await self.db.execute(
            select(WhatsAppCampaignRecipient)
            .join(
                WhatsAppCampaign,
                WhatsAppCampaign.id == WhatsAppCampaignRecipient.campaign_id,
            )
            .options(selectinload(WhatsAppCampaignRecipient.campaign))
            .where(
                WhatsAppCampaignRecipient.workspace_id == workspace_id,
                WhatsAppCampaignRecipient.phone.in_(phone_number_variants(normalized_phone)),
                WhatsAppCampaignRecipient.flow_run_id.is_(None),
                WhatsAppCampaignRecipient.status.in_(
                    [
                        CampaignRecipientStatus.sent,
                        CampaignRecipientStatus.delivered,
                        CampaignRecipientStatus.read,
                    ]
                ),
                WhatsAppCampaign.follow_up_flow_id.is_not(None),
                WhatsAppCampaign.flow_start_mode == "on_reply",
            )
            .order_by(
                WhatsAppCampaignRecipient.sent_at.desc(),
                WhatsAppCampaignRecipient.created_at.desc(),
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        recipient = result.scalar_one_or_none()
        if recipient is None:
            return None

        if recipient.matter_id is None and matter_id is not None:
            linked_matter = await self.db.scalar(
                select(Matter).where(
                    Matter.id == matter_id,
                    Matter.workspace_id == workspace_id,
                    Matter.deleted_at.is_(None),
                )
            )
            if linked_matter is not None:
                recipient.matter_id = linked_matter.id

        flow_run = await self._start_recipient_follow_up_flow(
            recipient,
            trigger_event="on_reply",
            thread_id=thread_id,
        )
        await self.db.commit()
        if flow_run is None:
            return None
        await self.db.refresh(flow_run)
        FlowService.publish_initial_flow_run(flow_run, workspace_id)
        return flow_run.id

    async def _start_recipient_follow_up_flow(
        self,
        recipient: WhatsAppCampaignRecipient,
        *,
        trigger_event: str,
        thread_id: UUID | None = None,
    ):
        campaign = recipient.campaign
        if (
            recipient.flow_run_id is not None
            or campaign.follow_up_flow_id is None
            or campaign.flow_start_mode != trigger_event
        ):
            return None

        context = dict(recipient.send_context or {})
        follow_up_context = dict(context.get("followUpFlow") or {})
        if recipient.matter_id is None:
            context["followUpFlow"] = {
                **follow_up_context,
                "state": "blocked_missing_matter",
                "triggerEvent": trigger_event,
                "checkedAt": self._utc_now().isoformat(),
            }
            recipient.send_context = context
            return None

        try:
            async with self.db.begin_nested():
                # Preserve the audience context (including canonical picker
                # field paths) when creating the FlowRun.  The previous
                # implementation replaced it with only campaign metadata,
                # causing delayed sequence bindings to lose their values.
                flow_context = {
                    **context,
                    "source": "whatsapp_campaign",
                    "campaignId": str(campaign.id),
                    "campaignRecipientId": str(recipient.id),
                    "contactId": str(recipient.contact_id) if recipient.contact_id else None,
                    "phone": recipient.phone,
                    "triggerEvent": trigger_event,
                }
                flow_run = await FlowService(self.db).start_flow_run(
                    campaign.follow_up_flow_id,
                    recipient.matter_id,
                    recipient.workspace_id,
                    context=flow_context,
                    thread_id=thread_id,
                    commit=False,
                    publish=False,
                )
                started_at = self._utc_now()
                recipient.flow_run_id = flow_run.id
                recipient.flow_trigger_event = trigger_event
                recipient.flow_started_at = started_at
                context["followUpFlow"] = {
                    **follow_up_context,
                    "state": "started",
                    "flowId": str(campaign.follow_up_flow_id),
                    "flowRunId": str(flow_run.id),
                    "triggerEvent": trigger_event,
                    "startedAt": started_at.isoformat(),
                }
                recipient.send_context = context
                await self.db.flush()
            return flow_run
        except Exception as exc:
            context["followUpFlow"] = {
                **follow_up_context,
                "state": "failed_known",
                "triggerEvent": trigger_event,
                "errorType": safe_error_type(exc),
                "checkedAt": self._utc_now().isoformat(),
            }
            recipient.send_context = context
            logger.error(
                "whatsapp_campaign.follow_up_flow_failed campaign_id=%s recipient_id=%s error_type=%s",
                campaign.id,
                recipient.id,
                safe_error_type(exc),
            )
            return None

    async def get_campaign_metrics(self, workspace_id: UUID) -> dict[str, Any]:
        result = await self.db.execute(
            select(
                func.count(WhatsAppCampaign.id),
                func.coalesce(func.sum(WhatsAppCampaign.sent_count), 0),
                func.coalesce(func.sum(WhatsAppCampaign.delivered_count), 0),
                func.coalesce(func.sum(WhatsAppCampaign.read_count), 0),
                func.coalesce(func.sum(WhatsAppCampaign.failed_count), 0),
            ).where(WhatsAppCampaign.workspace_id == workspace_id)
        )
        total_campaigns, sent_count, delivered_count, read_count, failed_count = result.one()
        return {
            "totalCampaigns": total_campaigns or 0,
            "sentCount": int(sent_count or 0),
            "deliveredCount": int(delivered_count or 0),
            "readCount": int(read_count or 0),
            "failedCount": int(failed_count or 0),
        }

    async def process_due_campaigns(
        self,
        *,
        workspace_id: UUID | None = None,
        campaign_batch_size: int = CAMPAIGN_PROCESS_BATCH_SIZE,
        job_batch_size: int = CAMPAIGN_JOB_BATCH_SIZE,
    ) -> int:
        if not external_delivery_allowed():
            return 0
        scoped_workspace_id = workspace_id or get_workspace_context(self.db)
        if scoped_workspace_id is None:
            raise RuntimeError("process_due_campaigns exige contexto explícito de workspace")
        scoped_workspace_id = UUID(str(scoped_workspace_id))
        campaign_limit = max(1, min(int(campaign_batch_size), 50))
        job_limit = max(1, min(int(job_batch_size), 200))
        now = self._utc_now()
        result = await self.db.execute(
            select(WhatsAppCampaign)
            .where(
                WhatsAppCampaign.workspace_id == scoped_workspace_id,
                WhatsAppCampaign.status.in_([CampaignStatus.scheduled, CampaignStatus.running]),
                or_(
                    WhatsAppCampaign.status == CampaignStatus.running,
                    WhatsAppCampaign.scheduled_at.is_(None),
                    WhatsAppCampaign.scheduled_at <= now,
                ),
                exists(
                    select(WhatsAppCampaignSendJob.id).where(
                        WhatsAppCampaignSendJob.campaign_id == WhatsAppCampaign.id,
                        WhatsAppCampaignSendJob.workspace_id == scoped_workspace_id,
                        WhatsAppCampaignSendJob.status.in_(
                            [
                                CampaignSendJobStatus.pending,
                                CampaignSendJobStatus.scheduled,
                            ]
                        ),
                        or_(
                            WhatsAppCampaignSendJob.scheduled_for.is_(None),
                            WhatsAppCampaignSendJob.scheduled_for <= now,
                        ),
                    )
                ),
            )
            .order_by(WhatsAppCampaign.created_at.asc())
            .with_for_update(skip_locked=True, of=WhatsAppCampaign)
            .limit(campaign_limit)
        )
        campaigns = result.scalars().all()
        processed = 0
        for campaign in campaigns:
            if not external_delivery_allowed():
                break
            if campaign.status == CampaignStatus.scheduled and self._is_future(
                campaign.scheduled_at, now
            ):
                continue
            connection = await self._resolve_official_connection(
                scoped_workspace_id,
                campaign.whatsapp_connection_id,
            )
            if campaign.status == CampaignStatus.scheduled:
                campaign.status = CampaignStatus.running
                campaign.launched_at = campaign.launched_at or now
                await self.db.commit()
            await self._process_ready_jobs(
                campaign.id,
                connection,
                workspace_id=scoped_workspace_id,
                batch_size=job_limit,
            )
            processed += 1
        return processed

    async def _get_audience_list(
        self,
        workspace_id: UUID,
        audience_list_id: UUID,
    ) -> WhatsAppAudienceList:
        result = await self.db.execute(
            select(WhatsAppAudienceList)
            .execution_options(populate_existing=True)
            .options(selectinload(WhatsAppAudienceList.members))
            .where(
                WhatsAppAudienceList.id == audience_list_id,
                WhatsAppAudienceList.workspace_id == workspace_id,
            )
        )
        audience_list = result.scalars().first()
        if audience_list is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WA-LIST-404", "message": "Lista de disparo não encontrada."},
            )
        return audience_list

    async def _ensure_audience_list_name_available(
        self,
        workspace_id: UUID,
        name: str,
        *,
        exclude_id: UUID | None = None,
    ) -> None:
        stmt = select(WhatsAppAudienceList.id).where(
            WhatsAppAudienceList.workspace_id == workspace_id,
            func.lower(WhatsAppAudienceList.name) == name.strip().lower(),
        )
        if exclude_id is not None:
            stmt = stmt.where(WhatsAppAudienceList.id != exclude_id)
        if (await self.db.scalar(stmt)) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-LIST-004",
                    "message": "Já existe uma lista com este nome no workspace.",
                },
            )

    async def _resolve_member_links(
        self,
        workspace_id: UUID,
        *,
        contact_id: UUID | None,
        matter_id: UUID | None,
        phone: str,
    ) -> tuple[WhatsAppContact | None, Matter | None]:
        contact = None
        if contact_id is not None:
            contact = await self.db.scalar(
                select(WhatsAppContact).where(
                    WhatsAppContact.id == contact_id,
                    WhatsAppContact.workspace_id == workspace_id,
                )
            )
            if contact is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "WA-LIST-005",
                        "message": "Contato não encontrado neste workspace.",
                    },
                )
        else:
            contact = await self.db.scalar(
                select(WhatsAppContact)
                .where(
                    WhatsAppContact.workspace_id == workspace_id,
                    WhatsAppContact.phone.in_(phone_number_lookup_variants(phone)),
                )
                .order_by(WhatsAppContact.updated_at.desc())
                .limit(1)
            )

        resolved_matter_id = matter_id or (contact.matter_id if contact else None)
        matter = None
        if resolved_matter_id is not None:
            matter = await self.db.scalar(
                select(Matter).where(
                    Matter.id == resolved_matter_id,
                    Matter.workspace_id == workspace_id,
                )
            )
            if matter is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "WA-LIST-006",
                        "message": "Card não encontrado neste workspace.",
                    },
                )
        return contact, matter

    async def _contacts_for_phones(
        self,
        workspace_id: UUID,
        phones: set[str],
    ) -> dict[str, tuple[WhatsAppContact | None, Matter | None]]:
        mapping: dict[str, tuple[WhatsAppContact | None, Matter | None]] = {}
        ordered = sorted(_expand_phone_variants(phones))
        for offset in range(0, len(ordered), 500):
            chunk = ordered[offset : offset + 500]
            rows = (
                await self.db.execute(
                    select(WhatsAppContact, Matter)
                    .outerjoin(
                        Matter,
                        and_(
                            Matter.id == WhatsAppContact.matter_id,
                            Matter.workspace_id == workspace_id,
                        ),
                    )
                    .where(
                        WhatsAppContact.workspace_id == workspace_id,
                        WhatsAppContact.phone.in_(chunk),
                    )
                    .order_by(WhatsAppContact.updated_at.desc())
                )
            ).all()
            for contact, matter in rows:
                phone = normalize_phone_number(contact.phone)
                if phone and phone not in mapping:
                    mapping[phone] = (contact, matter)
        return mapping

    async def _audience_members_for_phones(
        self,
        workspace_id: UUID,
        audience_list_id: UUID,
        phones: set[str],
    ) -> dict[str, WhatsAppAudienceListMember]:
        mapping: dict[str, WhatsAppAudienceListMember] = {}
        ordered = sorted(_expand_phone_variants(phones))
        for offset in range(0, len(ordered), 500):
            chunk = ordered[offset : offset + 500]
            items = (
                await self.db.scalars(
                    select(WhatsAppAudienceListMember).where(
                        WhatsAppAudienceListMember.workspace_id == workspace_id,
                        WhatsAppAudienceListMember.audience_list_id == audience_list_id,
                        WhatsAppAudienceListMember.phone.in_(chunk),
                    )
                )
            ).all()
            for item in items:
                canonical = normalize_phone_number(item.phone)
                if canonical:
                    mapping[canonical] = item
        return mapping

    async def _opt_in_records_for_phones(
        self,
        workspace_id: UUID,
        phones: Iterable[str],
    ) -> dict[str, WhatsAppOptInRecord]:
        mapping: dict[str, WhatsAppOptInRecord] = {}
        ordered = sorted(_expand_phone_variants(phones))
        for offset in range(0, len(ordered), 500):
            chunk = ordered[offset : offset + 500]
            items = (
                await self.db.scalars(
                    select(WhatsAppOptInRecord).where(
                        WhatsAppOptInRecord.workspace_id == workspace_id,
                        WhatsAppOptInRecord.phone.in_(chunk),
                        WhatsAppOptInRecord.channel == "whatsapp",
                    )
                )
            ).all()
            for item in items:
                canonical = normalize_phone_number(item.phone)
                if canonical:
                    mapping[canonical] = item
        return mapping

    async def _refresh_audience_list_counts(
        self,
        audience_list: WhatsAppAudienceList,
        *,
        invalid: int,
        duplicates: int,
    ) -> None:
        total, valid = (
            await self.db.execute(
                select(
                    func.count(WhatsAppAudienceListMember.id),
                    func.coalesce(
                        func.sum(case((WhatsAppAudienceListMember.status == "valid", 1), else_=0)),
                        0,
                    ),
                ).where(
                    WhatsAppAudienceListMember.workspace_id == audience_list.workspace_id,
                    WhatsAppAudienceListMember.audience_list_id == audience_list.id,
                )
            )
        ).one()
        audience_list.member_count = int(total or 0)
        audience_list.valid_count = int(valid or 0)
        audience_list.invalid_count = int(invalid)
        audience_list.duplicate_count = int(duplicates)
        self.db.add(audience_list)

    async def _validate_follow_up_flow(self, workspace_id: UUID, flow_id: UUID | None) -> None:
        if flow_id is None:
            return
        flow = await self.db.scalar(
            select(Flow).where(
                Flow.id == flow_id,
                Flow.workspace_id == workspace_id,
                Flow.is_active.is_(True),
                Flow.deleted_at.is_(None),
            )
        )
        if flow is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "WA-CAM-011",
                    "message": "Fluxo publicado não encontrado neste workspace.",
                },
            )

    def _csv_reader(self, csv_text: str) -> tuple[csv.DictReader, str]:
        if not csv_text.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "WA-LIST-007", "message": "O arquivo CSV está vazio."},
            )
        sample = csv_text[:8192]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")), delimiter=delimiter)
        if not reader.fieldnames:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "WA-LIST-008", "message": "O CSV não possui cabeçalho válido."},
            )
        return reader, delimiter

    def _normalize_csv_header(self, value: str) -> str:
        decomposed = unicodedata.normalize("NFKD", str(value or "").strip().lower())
        ascii_value = "".join(char for char in decomposed if not unicodedata.combining(char))
        normalized = "".join(char if char.isalnum() else "_" for char in ascii_value)
        while "__" in normalized:
            normalized = normalized.replace("__", "_")
        return normalized.strip("_")

    def _csv_truthy(self, value: str | None) -> bool:
        normalized = self._normalize_csv_header(value or "")
        return normalized in {"1", "sim", "yes", "true", "autorizado", "aceito"}

    def _to_audience_list_read(
        self,
        audience_list: WhatsAppAudienceList,
        *,
        include_members: bool,
        mask_sensitive: bool,
    ) -> AudienceListRead:
        members: list[AudienceListMemberRead] = []
        if include_members:
            for member in audience_list.members:
                members.append(
                    AudienceListMemberRead(
                        id=member.id,
                        contact_id=None if mask_sensitive else member.contact_id,
                        matter_id=None if mask_sensitive else member.matter_id,
                        phone=f"***{member.phone[-4:]}" if mask_sensitive else member.phone,
                        name="Lead protegido" if mask_sensitive else member.name,
                        email=None if mask_sensitive else member.email,
                        status=member.status,
                        source_row=member.source_row,
                        member_metadata={} if mask_sensitive else member.member_metadata,
                        created_at=member.created_at,
                    )
                )
        return AudienceListRead(
            id=audience_list.id,
            created_by_id=audience_list.created_by_id,
            name=audience_list.name,
            description=audience_list.description,
            source=audience_list.source,
            status=audience_list.status,
            member_count=audience_list.member_count,
            valid_count=audience_list.valid_count,
            invalid_count=audience_list.invalid_count,
            duplicate_count=audience_list.duplicate_count,
            import_metadata=audience_list.import_metadata,
            members=members,
            sensitive_data_hidden=mask_sensitive,
            created_at=audience_list.created_at,
            updated_at=audience_list.updated_at,
        )

    @staticmethod
    def _quota_period_start(at: datetime) -> date:
        normalized = at if at.tzinfo is not None else at.replace(tzinfo=UTC)
        normalized = normalized.astimezone(UTC)
        return date(normalized.year, normalized.month, 1)

    @staticmethod
    def _quota_period_end(period_start: date) -> date:
        if period_start.month == 12:
            return date(period_start.year + 1, 1, 1)
        return date(period_start.year, period_start.month + 1, 1)

    async def _campaign_recipient_limit(
        self,
        *,
        workspace_id: UUID,
        effective_at: datetime,
    ) -> int | None:
        try:
            manifest = await CommercialCatalogService(self.db).resolve_effective_manifest(
                workspace_id
            )
        except CommercialContractNotFoundError:
            manifest = None
        if manifest is not None:
            capability = manifest.capabilities.get(EntitlementKey.campaign_recipients)
            # A canonical contract is authoritative. Missing or denied
            # capability data must not reopen the legacy unlimited path.
            if capability is None:
                raise CampaignCapabilityDeniedError("not_configured")
            if not capability.enabled or not capability.permitted:
                raise CampaignCapabilityDeniedError(capability.denial_reason or "permission_denied")
            if capability.limit is None:
                raise CampaignCapabilityDeniedError("not_configured")
            return max(int(capability.limit), 0)

        # Compatibility is intentionally one-way: only a workspace without a
        # canonical contract may consult its immutable PlanPolicy/Plan state.
        subscription_row = (
            await self.db.execute(
                select(Subscription, Plan)
                .join(Plan, Plan.id == Subscription.plan_id)
                .where(Subscription.workspace_id == workspace_id)
            )
        ).first()
        if subscription_row is None:
            raise CampaignCapabilityDeniedError("not_contracted")
        _subscription, plan = subscription_row
        versioned = await PlanPolicyService(self.db).resolve_versioned_workspace(
            workspace_id=workspace_id,
            effective_at=effective_at,
        )
        configured = (
            versioned.parameters.max_campaign_recipients_per_month
            if versioned is not None
            else (plan.features or {}).get("maxCampaignRecipientsPerMonth")
        )
        if configured is None or isinstance(configured, bool):
            raise CampaignCapabilityDeniedError("not_configured")
        try:
            normalized_limit = int(configured)
        except (OverflowError, TypeError, ValueError) as error:
            raise CampaignCapabilityDeniedError("not_configured") from error
        if normalized_limit < 0 or (isinstance(configured, float) and not configured.is_integer()):
            raise CampaignCapabilityDeniedError("not_configured")
        return normalized_limit

    async def _lock_quota_workspace(self, workspace_id: UUID) -> None:
        locked_workspace_id = await self.db.scalar(
            select(Workspace.id)
            .where(
                Workspace.id == workspace_id,
                Workspace.deleted_at.is_(None),
            )
            .with_for_update()
        )
        if locked_workspace_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WA-CAM-404", "message": "Workspace não encontrado."},
            )

    async def _monthly_quota_usage(
        self,
        *,
        workspace_id: UUID,
        period_start: date,
    ) -> int:
        period_end = self._quota_period_end(period_start)
        period_start_at = datetime(
            period_start.year,
            period_start.month,
            1,
            tzinfo=UTC,
        )
        period_end_at = datetime(period_end.year, period_end.month, 1, tzinfo=UTC)
        return int(
            await self.db.scalar(
                select(func.count(WhatsAppCampaignRecipient.id)).where(
                    WhatsAppCampaignRecipient.workspace_id == workspace_id,
                    or_(
                        WhatsAppCampaignRecipient.quota_period_start == period_start,
                        and_(
                            WhatsAppCampaignRecipient.quota_period_start.is_(None),
                            WhatsAppCampaignRecipient.sent_at >= period_start_at,
                            WhatsAppCampaignRecipient.sent_at < period_end_at,
                        ),
                    ),
                )
            )
            or 0
        )

    async def _reserve_launch_quota(
        self,
        *,
        workspace_id: UUID,
        requested: int,
        at: datetime,
    ) -> tuple[date, datetime]:
        await self._lock_quota_workspace(workspace_id)
        period_start = self._quota_period_start(at)
        reserved_at = self._utc_now()
        try:
            limit = await self._campaign_recipient_limit(
                workspace_id=workspace_id,
                effective_at=reserved_at,
            )
        except CampaignCapabilityDeniedError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": CAMPAIGN_CAPABILITY_ERROR_CODE,
                    "message": (
                        "O contrato comercial não permite destinatários de campanha neste momento."
                    ),
                    "denialReason": error.denial_reason,
                },
            ) from error
        used = await self._monthly_quota_usage(
            workspace_id=workspace_id,
            period_start=period_start,
        )
        if limit is not None and used + requested > limit:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": MONTHLY_QUOTA_ERROR_CODE,
                    "message": (
                        "O lançamento excede o limite mensal de destinatários de campanha do plano."
                    ),
                    "periodStart": period_start.isoformat(),
                    "limit": limit,
                    "used": used,
                    "requested": requested,
                    "remaining": max(limit - used, 0),
                },
            )
        return period_start, reserved_at

    async def _reserve_worker_quota(
        self,
        *,
        workspace_id: UUID,
        jobs: list[WhatsAppCampaignSendJob],
        at: datetime,
    ) -> tuple[
        list[WhatsAppCampaignSendJob],
        list[WhatsAppCampaignSendJob],
        dict[str, Any],
    ]:
        if not jobs:
            return [], [], {}
        await self._lock_quota_workspace(workspace_id)
        period_start = self._quota_period_start(at)
        try:
            limit = await self._campaign_recipient_limit(
                workspace_id=workspace_id,
                effective_at=at,
            )
        except CampaignCapabilityDeniedError as error:
            return (
                [],
                jobs,
                {
                    "periodStart": period_start.isoformat(),
                    "denialReason": error.denial_reason,
                },
            )
        used = await self._monthly_quota_usage(
            workspace_id=workspace_id,
            period_start=period_start,
        )
        already_reserved: list[WhatsAppCampaignSendJob] = []
        needs_reservation: list[WhatsAppCampaignSendJob] = []
        for job in jobs:
            recipient = job.recipient
            if recipient is None or recipient.workspace_id != workspace_id:
                continue
            if (
                recipient.quota_period_start == period_start
                and recipient.quota_reserved_at is not None
            ):
                already_reserved.append(job)
            else:
                needs_reservation.append(job)

        available = len(needs_reservation) if limit is None else max(limit - used, 0)
        newly_reserved = needs_reservation[:available]
        deferred = needs_reservation[available:]
        reserved_at = self._utc_now()
        for job in newly_reserved:
            recipient = job.recipient
            if recipient is not None:
                recipient.quota_period_start = period_start
                recipient.quota_reserved_at = reserved_at
        allowed_ids = {job.id for job in [*already_reserved, *newly_reserved]}
        return (
            [job for job in jobs if job.id in allowed_ids],
            deferred,
            {
                "periodStart": period_start.isoformat(),
                "limit": limit,
                "used": used,
                "newlyReserved": len(newly_reserved),
            },
        )

    async def _get_campaign(
        self,
        workspace_id: UUID,
        campaign_id: UUID,
        *,
        lock: bool = False,
    ) -> WhatsAppCampaign:
        statement = (
            select(WhatsAppCampaign)
            .execution_options(populate_existing=True)
            .options(
                selectinload(WhatsAppCampaign.audience),
                selectinload(WhatsAppCampaign.template_snapshot),
                selectinload(WhatsAppCampaign.recipients),
            )
            .where(
                WhatsAppCampaign.id == campaign_id,
                WhatsAppCampaign.workspace_id == workspace_id,
            )
        )
        if lock:
            statement = statement.with_for_update(of=WhatsAppCampaign)
        result = await self.db.execute(statement)
        campaign = result.scalars().first()
        if campaign is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "WA-CAM-404", "message": "Campanha não encontrada."},
            )
        return campaign

    async def _resolve_official_connection(
        self,
        workspace_id: UUID,
        connection_id: UUID | None,
    ) -> WhatsAppConnection:
        connection = (
            await self.connection_service.get_connection(connection_id, workspace_id)
            if connection_id
            else await self.connection_service.get_default_connection(
                workspace_id, connected_only=True
            )
        )
        if connection is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-CAM-007",
                    "message": "Nenhum número oficial conectado está disponível para campanhas.",
                },
            )
        if connection.connection_type == ConnectionType.QR_CODE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-CAM-008",
                    "message": "Campanhas oficiais não podem usar conexão via QR Code.",
                },
            )
        if connection.status != WaConnectionStatus.connected:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "WA-CAM-009", "message": "A conexão escolhida não está conectada."},
            )
        return connection

    async def _get_template(self, workspace_id: UUID, template_id: UUID) -> WhatsAppMetaTemplate:
        result = await self.db.execute(
            select(WhatsAppMetaTemplate).where(
                WhatsAppMetaTemplate.id == template_id,
                WhatsAppMetaTemplate.workspace_id == workspace_id,
            )
        )
        template = result.scalars().first()
        if template is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "WA-CAM-010",
                    "message": "Template oficial da Meta não encontrado.",
                },
            )
        return template

    async def _build_audience_preview(
        self,
        workspace_id: UUID,
        audience_filter: CampaignAudienceFilter,
    ) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        if audience_filter.include_crm_contacts:
            stmt = (
                select(WhatsAppContact, Matter)
                .outerjoin(
                    Matter,
                    and_(
                        Matter.id == WhatsAppContact.matter_id,
                        Matter.workspace_id == workspace_id,
                    ),
                )
                .where(WhatsAppContact.workspace_id == workspace_id)
                .order_by(WhatsAppContact.created_at.desc())
            )
            if not audience_filter.include_blocked:
                stmt = stmt.where(WhatsAppContact.is_blocked.is_(False))
            if audience_filter.search:
                pattern = f"%{audience_filter.search.strip()}%"
                stmt = stmt.where(
                    or_(
                        WhatsAppContact.name.ilike(pattern),
                        WhatsAppContact.phone.ilike(pattern),
                        Matter.demand_type.ilike(pattern),
                    )
                )
            if audience_filter.contact_ids:
                stmt = stmt.where(WhatsAppContact.id.in_(audience_filter.contact_ids))
            if audience_filter.column_ids:
                stmt = stmt.where(Matter.column_id.in_(audience_filter.column_ids))
            if audience_filter.practice_areas:
                stmt = stmt.where(Matter.practice_area.in_(audience_filter.practice_areas))
            if audience_filter.lead_sources:
                stmt = stmt.where(Matter.lead_source.in_(audience_filter.lead_sources))
            if audience_filter.assigned_user_ids:
                stmt = stmt.where(
                    Matter.responsible_human_id.in_(audience_filter.assigned_user_ids)
                )
            if audience_filter.classifications:
                stmt = stmt.where(Matter.classification.in_(audience_filter.classifications))
            if audience_filter.queue_statuses:
                stmt = stmt.where(WhatsAppContact.queue_status.in_(audience_filter.queue_statuses))
            if audience_filter.created_from:
                stmt = stmt.where(WhatsAppContact.created_at >= audience_filter.created_from)
            if audience_filter.created_to:
                stmt = stmt.where(WhatsAppContact.created_at <= audience_filter.created_to)
            if audience_filter.has_open_tasks is True:
                stmt = stmt.where(Matter.open_tasks > 0)
            elif audience_filter.has_open_tasks is False:
                stmt = stmt.where(or_(Matter.id.is_(None), Matter.open_tasks == 0))
            if not audience_filter.include_unlinked_contacts:
                stmt = stmt.where(WhatsAppContact.matter_id.is_not(None))
            candidates.extend(
                {
                    "contact": contact,
                    "matter": matter,
                    "contact_id": contact.id,
                    "matter_id": matter.id if matter else None,
                    "phone": normalize_phone_number(contact.phone),
                    "name": contact.name,
                    "email": None,
                    "sources": ["crm"],
                    "audience_list_ids": [],
                }
                for contact, matter in (await self.db.execute(stmt)).all()
            )

        requested_list_ids = set(audience_filter.audience_list_ids)
        if requested_list_ids:
            found_ids = set(
                (
                    await self.db.scalars(
                        select(WhatsAppAudienceList.id).where(
                            WhatsAppAudienceList.workspace_id == workspace_id,
                            WhatsAppAudienceList.id.in_(requested_list_ids),
                            WhatsAppAudienceList.status == "active",
                        )
                    )
                ).all()
            )
            if found_ids != requested_list_ids:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "WA-LIST-404",
                        "message": "Uma ou mais listas não existem neste workspace ou estão arquivadas.",
                    },
                )
            list_rows = (
                await self.db.execute(
                    select(WhatsAppAudienceListMember, WhatsAppContact, Matter)
                    .outerjoin(
                        WhatsAppContact,
                        and_(
                            WhatsAppContact.id == WhatsAppAudienceListMember.contact_id,
                            WhatsAppContact.workspace_id == workspace_id,
                        ),
                    )
                    .outerjoin(
                        Matter,
                        and_(
                            Matter.id == WhatsAppAudienceListMember.matter_id,
                            Matter.workspace_id == workspace_id,
                        ),
                    )
                    .where(
                        WhatsAppAudienceListMember.workspace_id == workspace_id,
                        WhatsAppAudienceListMember.audience_list_id.in_(requested_list_ids),
                        WhatsAppAudienceListMember.status == "valid",
                    )
                    .order_by(WhatsAppAudienceListMember.created_at.desc())
                )
            ).all()
            candidates.extend(
                {
                    "contact": contact,
                    "matter": matter,
                    "contact_id": member.contact_id,
                    "matter_id": member.matter_id,
                    "phone": normalize_phone_number(member.phone),
                    "name": member.name or (contact.name if contact else None),
                    "email": member.email,
                    "sources": ["list"],
                    "audience_list_ids": [str(member.audience_list_id)],
                }
                for member, contact, matter in list_rows
            )

        rows_by_phone: dict[str, dict[str, Any]] = {}
        invalid_rows = 0
        for candidate in candidates:
            phone = candidate["phone"]
            if not phone:
                invalid_rows += 1
                continue
            existing = rows_by_phone.get(phone)
            if existing is None:
                rows_by_phone[phone] = candidate
                continue
            existing["sources"] = sorted(set(existing["sources"] + candidate["sources"]))
            existing["audience_list_ids"] = sorted(
                set(existing["audience_list_ids"] + candidate["audience_list_ids"])
            )
            for field in ("contact", "matter", "contact_id", "matter_id", "name", "email"):
                if not existing.get(field) and candidate.get(field):
                    existing[field] = candidate[field]

        rows = list(rows_by_phone.values())
        phones = set(rows_by_phone)
        opt_ins = await self._opt_in_map(workspace_id, phones)
        suppressions = await self._suppression_map(workspace_id, phones)

        eligible_rows: list[dict[str, Any]] = []
        sample: list[dict[str, Any]] = []
        blocked = invalid_rows
        for row in rows:
            phone = row["phone"]
            contact = row.get("contact")
            matter = row.get("matter")
            opted_in = opt_ins.get(phone, False)
            suppressed = suppressions.get(phone, False)
            contact_blocked = bool(
                contact and contact.is_blocked and not audience_filter.include_blocked
            )
            context = self._build_recipient_context(
                contact,
                matter,
                phone=phone,
                name=row.get("name"),
                email=row.get("email"),
            )
            context.update(
                {
                    "audience_sources": ",".join(row["sources"]),
                    "audience_list_ids": ",".join(row["audience_list_ids"]),
                }
            )
            if opted_in and not suppressed and not contact_blocked:
                item = {**row, "context": context}
                eligible_rows.append(item)
                if len(sample) < 8:
                    sample.append(
                        {
                            "name": row.get("name") or phone,
                            "phone": phone,
                            "eligible": True,
                            "reason": "ok",
                            "columnId": getattr(matter, "column_id", None),
                            "sources": row["sources"],
                        }
                    )
            else:
                blocked += 1
                if len(sample) < 8:
                    sample.append(
                        {
                            "name": row.get("name") or phone,
                            "phone": phone,
                            "eligible": False,
                            "reason": "blocked_contact"
                            if contact_blocked
                            else "suppressed"
                            if suppressed
                            else "missing_opt_in",
                            "columnId": getattr(matter, "column_id", None),
                            "sources": row["sources"],
                        }
                    )
        return {
            "total": len(rows) + invalid_rows,
            "eligible": len(eligible_rows),
            "blocked": blocked,
            "sample": sample,
            "eligible_rows": eligible_rows,
        }

    async def _opt_in_map(self, workspace_id: UUID, phones: Iterable[str]) -> dict[str, bool]:
        records = await self._opt_in_records_for_phones(workspace_id, phones)
        return {phone: record.status == OptInStatus.opted_in for phone, record in records.items()}

    async def _suppression_map(self, workspace_id: UUID, phones: Iterable[str]) -> dict[str, bool]:
        mapping: dict[str, bool] = {}
        ordered = sorted(_expand_phone_variants(phones))
        for offset in range(0, len(ordered), 500):
            chunk = ordered[offset : offset + 500]
            records = (
                await self.db.scalars(
                    select(WhatsAppSuppressionEntry).where(
                        WhatsAppSuppressionEntry.workspace_id == workspace_id,
                        WhatsAppSuppressionEntry.phone.in_(chunk),
                        WhatsAppSuppressionEntry.is_active.is_(True),
                    )
                )
            ).all()
            for record in records:
                canonical = normalize_phone_number(record.phone)
                if canonical:
                    mapping[canonical] = True
        return mapping

    def _build_template_snapshot(
        self,
        template: WhatsAppMetaTemplate,
        *,
        header_parameters: list[str],
        body_parameters: list[str],
        button_parameters: list[Any],
    ) -> WhatsAppCampaignTemplateSnapshot:
        return WhatsAppCampaignTemplateSnapshot(
            source_template_id=template.id,
            template_name=template.name,
            category=template.category,
            language=template.language,
            status=template.status,
            header_parameters=header_parameters,
            body_parameters=body_parameters,
            button_parameters=[
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in button_parameters
            ],
            components=template.components,
            raw_payload=template.raw_payload,
        )

    @staticmethod
    def _validate_template_connection(template: WhatsAppMetaTemplate, connection_id: UUID) -> None:
        if template.whatsapp_connection_id != connection_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "WA-CAM-011",
                    "message": "O template selecionado pertence a outra conexão oficial.",
                },
            )

    @staticmethod
    def _validate_template_parameters(
        template: WhatsAppMetaTemplate,
        *,
        header_parameters: list[str],
        body_parameters: list[str],
        button_parameters: list[Any],
    ) -> None:
        def variable_positions(text: str | None) -> set[int]:
            return {
                int(match.group(1))
                for match in re.finditer(r"\{\{\s*(\d+)\s*\}\}", str(text or ""))
            }

        header_text = next(
            (
                item.get("text")
                for item in template.components or []
                if isinstance(item, dict) and str(item.get("type", "")).upper() == "HEADER"
            ),
            "",
        )
        body_text = next(
            (
                item.get("text")
                for item in template.components or []
                if isinstance(item, dict) and str(item.get("type", "")).upper() == "BODY"
            ),
            "",
        )
        scopes = {
            "header": (header_parameters, variable_positions(header_text)),
            "body": (body_parameters, variable_positions(body_text)),
        }
        dynamic_button_indexes: set[int] = set()
        for component in template.components or []:
            if (
                not isinstance(component, dict)
                or str(component.get("type", "")).upper() != "BUTTONS"
            ):
                continue
            for index, button in enumerate(component.get("buttons", [])):
                if isinstance(button, dict) and variable_positions(button.get("url")):
                    dynamic_button_indexes.add(index)
        if dynamic_button_indexes:
            indexed_values: dict[int, str] = {}
            legacy_index = iter(sorted(dynamic_button_indexes))
            for index, value in enumerate(button_parameters):
                if isinstance(value, dict) or hasattr(value, "index"):
                    raw_index = value.get("index") if isinstance(value, dict) else value.index
                    raw_value = value.get("value") if isinstance(value, dict) else value.value
                    try:
                        index = int(raw_index)
                    except (TypeError, ValueError):
                        index = -1
                    value = raw_value
                else:
                    index = next(legacy_index, -1)
                if index in dynamic_button_indexes and value is not None:
                    indexed_values[index] = str(value)
            scopes["button"] = (
                [indexed_values[index] for index in sorted(indexed_values)],
                set(dynamic_button_indexes),
            )
            if set(indexed_values) != dynamic_button_indexes:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "WA-CAM-PARAMETERS",
                        "message": (
                            "Parâmetros button incompletos: informe cada índice "
                            "de URL dinâmica."
                        ),
                    },
                )
        elif button_parameters:
            scopes["button"] = (button_parameters, set())
        for scope, (values, positions) in scopes.items():
            if scope == "button" and dynamic_button_indexes:
                if any(not str(value).strip() for value in values):
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail={
                            "code": "WA-CAM-PARAMETERS",
                            "message": "Parâmetros button não podem ser vazios.",
                        },
                    )
                continue
            expected = max(positions, default=0)
            if len(values) != expected or any(not str(value).strip() for value in values):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "WA-CAM-PARAMETERS",
                        "message": (
                            f"Parâmetros {scope} incompletos: informe exatamente "
                            f"as variáveis obrigatórias do template."
                        ),
                    },
                )

    async def _reset_campaign_runtime(self, campaign_id: UUID) -> None:
        await self.db.execute(
            delete(WhatsAppCampaignDeliveryEvent).where(
                WhatsAppCampaignDeliveryEvent.campaign_id == campaign_id
            )
        )
        await self.db.execute(
            delete(WhatsAppCampaignSendJob).where(
                WhatsAppCampaignSendJob.campaign_id == campaign_id
            )
        )
        await self.db.execute(
            delete(WhatsAppCampaignRecipient).where(
                WhatsAppCampaignRecipient.campaign_id == campaign_id
            )
        )
        await self.db.flush()

    @staticmethod
    def _apply_campaign_audience_preview(
        *,
        campaign: WhatsAppCampaign,
        audience_filter: CampaignAudienceFilter,
        preview: dict[str, Any],
    ) -> None:
        campaign.total_recipients = preview["total"]
        campaign.eligible_recipients = preview["eligible"]
        campaign.blocked_recipients = preview["blocked"]
        if campaign.audience is None:
            campaign.audience = WhatsAppCampaignAudience(filters={})
        campaign.audience.filters = audience_filter.model_dump(mode="json", exclude_none=True)
        campaign.audience.estimated_recipients = preview["eligible"]
        campaign.audience.sample_recipients = preview["sample"]

    async def _materialize_launch_contacts(
        self,
        *,
        workspace_id: UUID,
        connection: WhatsAppConnection,
        eligible_rows: list[dict[str, Any]],
    ) -> dict[str, WhatsAppContact]:
        """Reserve contact stock and persist every missing campaign contact atomically."""

        rows_by_phone: dict[str, dict[str, Any]] = {}
        for item in eligible_rows:
            canonical_phone = normalize_phone_number(item.get("phone"))
            if canonical_phone:
                rows_by_phone.setdefault(canonical_phone, {**item, "phone": canonical_phone})
        if not rows_by_phone:
            return {}

        # All contact-stock writers serialize on this workspace lock. Re-read
        # only after the lock so a concurrent writer cannot make the preflight
        # stale between the missing-contact count and the bulk quota check.
        await EntitlementEnforcementService(self.db).lock_workspace(workspace_id)
        contacts = (
            await self.db.scalars(
                select(WhatsAppContact).where(
                    WhatsAppContact.workspace_id == workspace_id,
                    WhatsAppContact.whatsapp_connection_id == connection.id,
                    WhatsAppContact.phone.in_(sorted(_expand_phone_variants(rows_by_phone))),
                )
            )
        ).all()
        contacts_by_phone: dict[str, WhatsAppContact] = {}
        for contact in contacts:
            canonical = normalize_phone_number(contact.phone)
            if canonical is None:
                continue
            current = contacts_by_phone.get(canonical)
            if current is None or (contact.phone == canonical and current.phone != canonical):
                contacts_by_phone[canonical] = contact
        missing_phones = sorted(set(rows_by_phone) - set(contacts_by_phone))
        if missing_phones:
            await WhatsAppContactCapacityService(self.db).enforce_new_contact(
                workspace_id=workspace_id,
                requested_quantity=len(missing_phones),
            )
            for phone in missing_phones:
                item = rows_by_phone[phone]
                matter = item.get("matter")
                try:
                    async with self.db.begin_nested():
                        contact = WhatsAppContact(
                            workspace_id=workspace_id,
                            whatsapp_connection_id=connection.id,
                            phone=phone,
                            name=item.get("name"),
                            matter_id=matter.id if matter else item.get("matter_id"),
                        )
                        self.db.add(contact)
                        await self.db.flush()
                except IntegrityError:
                    contact = await self._find_campaign_contact(
                        workspace_id=workspace_id,
                        connection_id=connection.id,
                        phone=phone,
                    )
                    if contact is None:
                        raise
                contacts_by_phone[phone] = contact

        for phone, contact in contacts_by_phone.items():
            item = rows_by_phone[phone]
            matter = item.get("matter")
            if not contact.name and item.get("name"):
                contact.name = item["name"]
            if contact.matter_id is None:
                contact.matter_id = matter.id if matter else item.get("matter_id")
        return contacts_by_phone

    async def _find_campaign_contact(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        phone: str,
    ) -> WhatsAppContact | None:
        """Resolve one person across canonical, Meta alias and E.164 storage forms."""

        canonical_phone = normalize_phone_number(phone)
        if canonical_phone is None:
            return None
        return await self.db.scalar(
            select(WhatsAppContact)
            .where(
                WhatsAppContact.workspace_id == workspace_id,
                WhatsAppContact.whatsapp_connection_id == connection_id,
                WhatsAppContact.phone.in_(sorted(phone_number_lookup_variants(canonical_phone))),
            )
            .order_by(
                case(
                    (WhatsAppContact.phone == canonical_phone, 0),
                    (WhatsAppContact.phone == f"+{canonical_phone}", 1),
                    else_=2,
                ),
                WhatsAppContact.id,
            )
            .limit(1)
        )

    async def _ensure_campaign_contact(
        self,
        *,
        workspace_id: UUID,
        connection: WhatsAppConnection,
        recipient: WhatsAppCampaignRecipient,
    ) -> WhatsAppContact:
        canonical_phone = normalize_phone_number(recipient.phone)
        if canonical_phone is None:
            raise ValueError("campaign recipient phone is invalid")
        contact = await self._find_campaign_contact(
            workspace_id=workspace_id,
            connection_id=connection.id,
            phone=canonical_phone,
        )
        if contact is None:
            await WhatsAppContactCapacityService(self.db).enforce_new_contact(
                workspace_id=workspace_id
            )
            # The stock check serializes writers on the workspace. Re-read after
            # acquiring that lock so concurrent retries converge on one row.
            contact = await self._find_campaign_contact(
                workspace_id=workspace_id,
                connection_id=connection.id,
                phone=canonical_phone,
            )
        if contact is None:
            try:
                async with self.db.begin_nested():
                    contact = WhatsAppContact(
                        workspace_id=workspace_id,
                        whatsapp_connection_id=connection.id,
                        phone=canonical_phone,
                        name=recipient.name,
                        matter_id=recipient.matter_id,
                    )
                    self.db.add(contact)
                    await self.db.flush()
            except IntegrityError:
                contact = await self._find_campaign_contact(
                    workspace_id=workspace_id,
                    connection_id=connection.id,
                    phone=canonical_phone,
                )
                if contact is None:
                    raise
        else:
            if not contact.name and recipient.name:
                contact.name = recipient.name
            if contact.matter_id is None and recipient.matter_id is not None:
                contact.matter_id = recipient.matter_id
        recipient.contact_id = contact.id
        return contact

    async def ensure_direct_contact(
        self,
        *,
        workspace_id: UUID,
        connection: WhatsAppConnection,
        phone: str,
        name: str | None,
    ) -> WhatsAppContact:
        """Materialize a tenant contact before an operator-initiated send."""
        normalized_phone = normalize_phone_number(phone)
        if not normalized_phone:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "WA-START-001", "message": "Informe um telefone válido com DDI."},
            )
        contact = await self._find_campaign_contact(
            workspace_id=workspace_id, connection_id=connection.id, phone=normalized_phone
        )
        if contact is None:
            # A checagem de capacidade NÃO serializa criadores (é FOR KEY SHARE):
            # duas entregas concorrentes passam juntas. O savepoint abaixo é a
            # única barreira real contra a colisão no índice único (S3).
            await WhatsAppContactCapacityService(self.db).enforce_new_contact(
                workspace_id=workspace_id,
            )
            contact = await self._find_campaign_contact(
                workspace_id=workspace_id, connection_id=connection.id, phone=normalized_phone
            )
        if contact is None:
            try:
                async with self.db.begin_nested():
                    contact = WhatsAppContact(
                        workspace_id=workspace_id,
                        whatsapp_connection_id=connection.id,
                        phone=normalized_phone,
                        name=name,
                    )
                    self.db.add(contact)
                    await self.db.flush()
            except IntegrityError:
                contact = await self._find_campaign_contact(
                    workspace_id=workspace_id, connection_id=connection.id, phone=normalized_phone
                )
                if contact is None:
                    raise
        if name and not contact.name:
            contact.name = name
        # `_find_campaign_contact` prefere a linha canônica: se a devolvida não é
        # canônica, não existe irmã canônica e reescrever o telefone é seguro.
        if contact.phone != normalized_phone:
            contact.phone = normalized_phone
        return contact

    @staticmethod
    def _render_template_component(
        text: str,
        parameters: list[str],
    ) -> str:
        rendered = text
        for index, value in enumerate(parameters, start=1):
            rendered = rendered.replace(f"{{{{{index}}}}}", value)
        return re.sub(r"{{\s*\d+\s*}}", "[dado não resolvido]", rendered).strip()

    @classmethod
    def _render_campaign_message(
        cls,
        snapshot: WhatsAppCampaignTemplateSnapshot,
        *,
        header_parameters: list[str],
        body_parameters: list[str],
    ) -> str:
        parts: list[str] = []
        for component in snapshot.components or []:
            if not isinstance(component, dict):
                continue
            component_type = str(component.get("type") or "").upper()
            component_text = str(component.get("text") or "").strip()
            if not component_text:
                continue
            if component_type == "HEADER":
                parts.append(
                    cls._render_template_component(
                        component_text,
                        header_parameters,
                    )
                )
            elif component_type == "BODY":
                parts.append(
                    cls._render_template_component(
                        component_text,
                        body_parameters,
                    )
                )
        if parts:
            return "\n\n".join(part for part in parts if part)
        return f"[Modelo oficial: {snapshot.template_name}]"

    async def _persist_campaign_message(
        self,
        *,
        workspace_id: UUID,
        connection: WhatsAppConnection,
        campaign: WhatsAppCampaign,
        recipient: WhatsAppCampaignRecipient,
        header_parameters: list[str],
        body_parameters: list[str],
    ) -> None:
        contact = await self._ensure_campaign_contact(
            workspace_id=workspace_id,
            connection=connection,
            recipient=recipient,
        )
        idempotency_key = hashlib.sha256(
            f"campaign:{campaign.id}:recipient:{recipient.id}".encode()
        ).hexdigest()
        message = await self.db.scalar(
            select(WhatsAppMessage).where(
                WhatsAppMessage.outbound_idempotency_key == idempotency_key
            )
        )
        if message is None:
            snapshot = campaign.template_snapshot
            if snapshot is None:
                raise RuntimeError("Campaign template snapshot is missing.")
            message = WhatsAppMessage(
                contact_id=contact.id,
                whatsapp_connection_id=connection.id,
                wamid=recipient.wamid,
                direction=WaMessageDirection.outbound,
                status=WaMessageStatus.sent,
                body=self._render_campaign_message(
                    snapshot,
                    header_parameters=header_parameters,
                    body_parameters=body_parameters,
                ),
                sent_by_id=campaign.created_by_id,
                timestamp=recipient.sent_at or self._utc_now(),
                outbound_idempotency_key=idempotency_key,
                message_metadata={
                    "source": "whatsapp_campaign",
                    "originSurface": "campaign",
                    "transportMode": "cloud_api",
                    "campaignId": str(campaign.id),
                    "campaignTitle": campaign.title,
                    "campaignRecipientId": str(recipient.id),
                    "metaTemplateId": str(snapshot.source_template_id)
                    if snapshot.source_template_id
                    else None,
                    "metaTemplateName": snapshot.template_name,
                    "billableByMeta": True,
                    "followUpFlowId": str(campaign.follow_up_flow_id)
                    if campaign.follow_up_flow_id
                    else None,
                    "flowStartMode": campaign.flow_start_mode,
                },
            )
            self.db.add(message)
            await self.db.flush()
        await OmnichannelLegacyBridgeService(self.db).ingest_whatsapp_message(
            workspace_id=workspace_id,
            connection=connection,
            contact=contact,
            message=message,
        )

    async def _process_ready_jobs(
        self,
        campaign_id: UUID,
        connection: WhatsAppConnection,
        *,
        workspace_id: UUID,
        batch_size: int = CAMPAIGN_JOB_BATCH_SIZE,
    ) -> int:
        if not external_delivery_allowed():
            return 0
        result = await self.db.execute(
            select(WhatsAppCampaign)
            .options(
                selectinload(WhatsAppCampaign.template_snapshot),
            )
            .where(
                WhatsAppCampaign.id == campaign_id,
                WhatsAppCampaign.workspace_id == workspace_id,
            )
        )
        campaign = result.scalars().first()
        if campaign is None or campaign.template_snapshot is None:
            return 0
        access_token = await self.connection_service.resolve_meta_access_token(connection)
        now = self._utc_now()
        jobs = list(
            (
                await self.db.scalars(
                    select(WhatsAppCampaignSendJob)
                    .options(selectinload(WhatsAppCampaignSendJob.recipient))
                    .where(
                        WhatsAppCampaignSendJob.campaign_id == campaign.id,
                        WhatsAppCampaignSendJob.workspace_id == workspace_id,
                        WhatsAppCampaignSendJob.status.in_(
                            [
                                CampaignSendJobStatus.pending,
                                CampaignSendJobStatus.scheduled,
                            ]
                        ),
                        or_(
                            WhatsAppCampaignSendJob.scheduled_for.is_(None),
                            WhatsAppCampaignSendJob.scheduled_for <= now,
                        ),
                    )
                    .order_by(
                        WhatsAppCampaignSendJob.scheduled_for,
                        WhatsAppCampaignSendJob.id,
                    )
                    .with_for_update(
                        skip_locked=True,
                        of=WhatsAppCampaignSendJob,
                    )
                    .limit(max(1, min(int(batch_size), 200)))
                )
            ).all()
        )

        # Tier/rampa (W1.5): antes de reivindicar qualquer job, checar a
        # folga real da janela deslizante de 24h (fonte da verdade em
        # connection_health_service). Se o lote selecionado excede a folga,
        # só os primeiros `folga` jobs seguem para envio; o excedente é
        # reagendado sem consumir tentativa (mesmo padrão do backoff de
        # lote por throughput da Meta), com evento de auditoria. Só toca a
        # conexão/health service se houver algum job elegível (evita I/O e
        # lazy-loads desnecessários quando não há nada para processar).
        health_service = WhatsAppConnectionHealthService(self.db)
        if jobs:
            tier_record = await health_service.get_or_create_tier(
                workspace_id=workspace_id, connection_id=connection.id
            )
            quota = await health_service.remaining_daily_quota(
                workspace_id=workspace_id, connection_id=connection.id
            )
            folga = self._effective_tier_folga(quota, tier_record.ramp_up_stage)
            deferred_jobs: list[WhatsAppCampaignSendJob] = []
            if folga is not None and len(jobs) > folga:
                cutoff = max(folga, 0)
                deferred_jobs = jobs[cutoff:]
                jobs = jobs[:cutoff]
            if deferred_jobs:
                await self._defer_jobs_for_tier_quota(
                    campaign=campaign,
                    jobs=deferred_jobs,
                    quota=quota,
                    folga=folga or 0,
                )

        jobs, monthly_deferred_jobs, monthly_quota = await self._reserve_worker_quota(
            workspace_id=workspace_id,
            jobs=jobs,
            at=now,
        )
        if monthly_deferred_jobs:
            await self._defer_jobs_for_monthly_quota(
                campaign=campaign,
                jobs=monthly_deferred_jobs,
                quota=monthly_quota,
            )

        # Claim durável: marcar o lote como processing e COMMITAR antes de
        # qualquer envio. Após o commit os locks do FOR UPDATE são liberados,
        # mas os jobs deixam de ser elegíveis para outros workers (a query só
        # seleciona pending/scheduled), preservando a semântica multi-worker.
        # Em caso de crash entre o envio e o registro do resultado, o job fica
        # em processing (nunca volta a pending), eliminando o reenvio.
        claim_time = self._utc_now()
        claimed: list[WhatsAppCampaignSendJob] = []
        newly_sent_phones: set[str] = set()
        for job in jobs:
            recipient = job.recipient
            if recipient is None or recipient.workspace_id != workspace_id:
                continue
            job.status = CampaignSendJobStatus.processing
            send_context = dict(recipient.send_context or {})
            send_context["claim"] = {"claimedAt": claim_time.isoformat()}
            recipient.send_context = send_context
            claimed.append(job)
        await self.db.commit()

        for position, job in enumerate(claimed):
            recipient = job.recipient
            # S2: reler o status pelo banco antes de cada envio. `pause_campaign`
            # grava em outra sessão e o objeto em memória continua `running`.
            status_atual = await self._campaign_status_in_db(campaign.id, workspace_id)
            if not external_delivery_allowed() or status_atual != CampaignStatus.running:
                restantes = claimed[position:]
                if status_atual == CampaignStatus.paused or status_atual == CampaignStatus.running:
                    self._unclaim_jobs(restantes)
                else:
                    self._cancel_claimed_jobs(restantes)
                if restantes:
                    self.db.add(
                        WhatsAppCampaignDeliveryEvent(
                            campaign_id=campaign.id,
                            recipient_id=restantes[0].recipient_id,
                            wamid=None,
                            status=CampaignRecipientStatus.queued,
                            payload={
                                "event": "batch_interrupted_by_status",
                                "statusRead": status_atual.value if status_atual else None,
                                "position": position,
                                "returnedJobs": len(restantes),
                                "externalDeliveryAllowed": external_delivery_allowed(),
                            },
                            occurred_at=self._utc_now(),
                        )
                    )
                await self.db.commit()
                break
            batch_interrupted = False
            try:
                # The contact projection is isolated per recipient. A uniqueness
                # race or any other flush failure rolls back this savepoint only;
                # retry state is then written from a clean outer transaction.
                async with self.db.begin_nested():
                    await self._ensure_campaign_contact(
                        workspace_id=workspace_id,
                        connection=connection,
                        recipient=recipient,
                    )
                    await self.db.flush()
            except EntitlementError as exc:
                await self.db.refresh(job)
                await self.db.refresh(recipient)
                self._mark_job_entitlement_failure(campaign, job, recipient, exc)
                await self.db.commit()
                continue
            except Exception as exc:
                await self.db.refresh(job)
                await self.db.refresh(recipient)
                error_code = "campaign_contact_materialization_failed"
                error_detail = f"[{error_code}] {safe_error_type(exc)}"
                self._apply_default_job_retry(campaign, job, recipient, error_detail)
                await self.db.commit()
                logger.error(
                    "whatsapp_campaign.contact_materialization_failed "
                    "campaign_id=%s recipient_id=%s error_type=%s",
                    campaign.id,
                    recipient.id,
                    safe_error_type(exc),
                )
                continue

            # A full contact stock must never send first and discover the quota
            # only while projecting the local message.
            await self.db.commit()
            try:
                job.attempt_count += 1
                header_parameters = self._resolve_parameters(
                    campaign.template_snapshot.header_parameters,
                    recipient.send_context,
                )
                body_parameters = self._resolve_parameters(
                    campaign.template_snapshot.body_parameters,
                    recipient.send_context,
                )
                button_parameters = self._resolve_button_parameters(
                    campaign.template_snapshot.button_parameters,
                    recipient.send_context,
                )
                send_kwargs = dict(
                    phone_number_id=connection.phone_number_id or "",
                    access_token=access_token,
                    to=recipient.phone,
                    template_name=campaign.template_snapshot.template_name,
                    language_code=campaign.template_snapshot.language,
                    header_parameters=header_parameters,
                    body_parameters=body_parameters,
                )
                if button_parameters:
                    send_kwargs["button_parameters"] = button_parameters
                response = await self.meta_client.send_template_message(
                    **send_kwargs,
                )
                wamid = response.get("messages", [{}])[0].get("id")
                recipient.wamid = wamid
                recipient.status = CampaignRecipientStatus.sent
                recipient.sent_at = self._utc_now()
                newly_sent_phones.add(recipient.phone)
                job.status = CampaignSendJobStatus.sent
                job.processed_at = self._utc_now()
                job.last_error = None
                self.db.add(
                    WhatsAppCampaignDeliveryEvent(
                        campaign_id=campaign.id,
                        recipient_id=recipient.id,
                        wamid=wamid,
                        status=CampaignRecipientStatus.sent,
                        payload=response,
                        occurred_at=self._utc_now(),
                    )
                )
                try:
                    async with self.db.begin_nested():
                        await self._persist_campaign_message(
                            workspace_id=workspace_id,
                            connection=connection,
                            campaign=campaign,
                            recipient=recipient,
                            header_parameters=header_parameters,
                            body_parameters=body_parameters,
                        )
                    send_context = dict(recipient.send_context or {})
                    send_context["canonicalProjection"] = {
                        "state": "projected",
                        "projectedAt": self._utc_now().isoformat(),
                    }
                    recipient.send_context = send_context
                except Exception as projection_error:
                    send_context = dict(recipient.send_context or {})
                    send_context["canonicalProjection"] = {
                        "state": "failed_known",
                        "errorType": safe_error_type(projection_error),
                        "checkedAt": self._utc_now().isoformat(),
                    }
                    recipient.send_context = send_context
                    logger.error(
                        "whatsapp_campaign.canonical_projection_failed "
                        "campaign_id=%s recipient_id=%s error_type=%s",
                        campaign.id,
                        recipient.id,
                        safe_error_type(projection_error),
                    )
            except EntitlementError as exc:
                # Contact stock/configuration failures are deterministic. They
                # happen before the provider call and must never enter the
                # transient retry path (attempt_count is intentionally intact).
                self._mark_job_entitlement_failure(campaign, job, recipient, exc)
            except WhatsAppProviderError as exc:
                if exc.delivery_uncertain:
                    # Resultado desconhecido: NUNCA reenviar automaticamente.
                    # O job permanece em processing aguardando reconciliação
                    # pelo webhook de status da Meta.
                    self._mark_job_delivery_uncertain(campaign, job, recipient, exc)
                else:
                    policy = classify_meta_error(
                        exc.provider_status_code,
                        exc.provider_error_subcode,
                    )
                    batch_interrupted = await self._apply_meta_error_policy(
                        workspace_id=workspace_id,
                        campaign=campaign,
                        job=job,
                        recipient=recipient,
                        remaining_jobs=claimed[position + 1 :],
                        policy=policy,
                        exc=exc,
                    )
            except Exception as exc:
                # Falha não relacionada à Meta (rede local, banco, template):
                # mantém o retry com backoff limitado do job.
                self._apply_default_job_retry(campaign, job, recipient, str(exc))
            # Commit por job: cada resultado fica durável imediatamente.
            await self.db.commit()
            if batch_interrupted:
                break
            await asyncio.sleep(0.02)

        if newly_sent_phones:
            # Atualiza o cache informativo do health service com os
            # ÚNICOS novos do lote (a fonte da verdade da quota continua
            # sendo a query deslizante; este cache é só para leitura rápida
            # no painel de saúde do número).
            await health_service.register_recipients(
                workspace_id=workspace_id,
                connection_id=connection.id,
                count=len(newly_sent_phones),
            )
            await self.db.commit()

        await self._refresh_campaign_counters(campaign.id)
        status_final = await self._campaign_status_in_db(campaign.id, workspace_id)
        await self._maybe_engage_failure_kill_switch(campaign, current_status=status_final)
        pending = await self.db.execute(
            select(func.count(WhatsAppCampaignSendJob.id)).where(
                WhatsAppCampaignSendJob.campaign_id == campaign.id,
                WhatsAppCampaignSendJob.workspace_id == workspace_id,
                or_(
                    WhatsAppCampaignSendJob.status.in_(
                        [
                            CampaignSendJobStatus.pending,
                            CampaignSendJobStatus.scheduled,
                        ]
                    ),
                    and_(
                        # Jobs em processing ainda contam como pendentes, exceto
                        # os de entrega incerta (aguardam webhook, não bloqueiam
                        # o fechamento da campanha).
                        WhatsAppCampaignSendJob.status == CampaignSendJobStatus.processing,
                        or_(
                            WhatsAppCampaignSendJob.last_error.is_(None),
                            WhatsAppCampaignSendJob.last_error.notlike(
                                f"{DELIVERY_UNCERTAIN_ERROR_PREFIX}%"
                            ),
                        ),
                    ),
                ),
            )
        )
        if (pending.scalar() or 0) == 0:
            # S2: fechar só quem AINDA está `running` no banco (UPDATE condicional,
            # sem sincronizar o objeto: `paused` gravado por fora nunca vira
            # `completed`).
            fechamento = await self.db.execute(
                update(WhatsAppCampaign)
                .where(
                    WhatsAppCampaign.id == campaign.id,
                    WhatsAppCampaign.workspace_id == workspace_id,
                    WhatsAppCampaign.status == CampaignStatus.running,
                )
                .values(status=CampaignStatus.completed, completed_at=self._utc_now())
                .execution_options(synchronize_session=False)
            )
            if fechamento.rowcount == 1:
                await self.db.refresh(campaign, attribute_names=["status", "completed_at"])
        await self.db.commit()
        return len(jobs)

    def _effective_tier_folga(self, quota: dict, ramp_up_stage: int) -> int | None:
        """
        Combina o remaining do tier com o teto adicional de rampa (se o
        estágio estiver aquecendo) e devolve a folga efetiva para o lote
        atual. `None` = sem teto (tier unlimited e rampa concluída).
        """
        candidates: list[int] = []
        if quota["remaining"] is not None:
            candidates.append(quota["remaining"])
        ramp_cap = RAMP_UP_STAGE_CAPS.get(ramp_up_stage)
        if ramp_cap is not None:
            candidates.append(max(ramp_cap - quota["used"], 0))
        if not candidates:
            return None
        return min(candidates)

    async def _defer_jobs_for_tier_quota(
        self,
        *,
        campaign: WhatsAppCampaign,
        jobs: list[WhatsAppCampaignSendJob],
        quota: dict,
        folga: int,
    ) -> None:
        """
        Reagenda o excedente do lote que ultrapassa a folga da janela
        deslizante de 24h (tier/rampa). Não é falha do destinatário nem da
        Meta: os jobs voltam para pending com `scheduled_for` adiante, sem
        consumir tentativa (mesmo padrão do backoff de lote por throughput
        em `_apply_meta_error_policy`), e emite evento de auditoria
        "tier_quota_deferred" com razão legível.
        """
        retry_at = self._utc_now() + timedelta(seconds=TIER_QUOTA_DEFER_SECONDS)
        detail = (
            f"[tier_quota_deferred] Folga de {max(folga, 0)} destinatário(s) únicos "
            f"na janela de 24h (usado {quota['used']}"
            + (f"/{quota['limit']}" if quota["limit"] is not None else "")
            + f"); {len(jobs)} destinatário(s) reagendado(s) para {retry_at.isoformat()}."
        )
        for job in jobs:
            job.status = CampaignSendJobStatus.pending
            job.scheduled_for = retry_at
            job.processed_at = None
            job.last_error = detail
            recipient = job.recipient
            if recipient is not None:
                recipient.status = CampaignRecipientStatus.queued
                recipient.last_error = detail
        first_recipient = jobs[0].recipient if jobs else None
        if first_recipient is not None:
            self.db.add(
                WhatsAppCampaignDeliveryEvent(
                    campaign_id=campaign.id,
                    recipient_id=first_recipient.id,
                    wamid=first_recipient.wamid,
                    status=CampaignRecipientStatus.queued,
                    payload={
                        "event": "tier_quota_deferred",
                        "deferredCount": len(jobs),
                        "quotaUsed": quota["used"],
                        "quotaLimit": quota["limit"],
                        "retryAt": retry_at.isoformat(),
                    },
                    occurred_at=self._utc_now(),
                )
            )
        logger.warning(
            "whatsapp_campaign.tier_quota_deferred campaign_id=%s jobs=%s retry_at=%s",
            campaign.id,
            len(jobs),
            retry_at.isoformat(),
        )

    async def _defer_jobs_for_monthly_quota(
        self,
        *,
        campaign: WhatsAppCampaign,
        jobs: list[WhatsAppCampaignSendJob],
        quota: dict[str, Any],
    ) -> None:
        denial_reason = quota.get("denialReason")
        if denial_reason is not None:
            retry_at = self._utc_now() + timedelta(hours=1)
            detail = (
                "[campaign_capability_denied] Contrato comercial não permite "
                f"campanhas ({denial_reason}); {len(jobs)} destinatário(s) "
                f"reagendado(s) para {retry_at.isoformat()}."
            )
            event_name = "campaign_capability_denied"
        else:
            period_start = date.fromisoformat(str(quota["periodStart"]))
            next_period = self._quota_period_end(period_start)
            retry_at = datetime(next_period.year, next_period.month, 1, tzinfo=UTC)
            detail = (
                "[monthly_campaign_quota_deferred] Limite mensal de destinatários "
                f"atingido ({quota['used']}/{quota['limit']}); "
                f"{len(jobs)} destinatário(s) reagendado(s) para {retry_at.isoformat()}."
            )
            event_name = "monthly_campaign_quota_deferred"
        for job in jobs:
            job.status = CampaignSendJobStatus.pending
            job.scheduled_for = retry_at
            job.processed_at = None
            job.last_error = detail
            recipient = job.recipient
            if recipient is not None:
                recipient.status = CampaignRecipientStatus.queued
                recipient.last_error = detail
        first_recipient = jobs[0].recipient if jobs else None
        if first_recipient is not None:
            self.db.add(
                WhatsAppCampaignDeliveryEvent(
                    campaign_id=campaign.id,
                    recipient_id=first_recipient.id,
                    wamid=first_recipient.wamid,
                    status=CampaignRecipientStatus.queued,
                    payload={
                        "event": event_name,
                        "deferredCount": len(jobs),
                        "periodStart": quota["periodStart"],
                        "quotaUsed": quota.get("used"),
                        "quotaLimit": quota.get("limit"),
                        "denialReason": denial_reason,
                        "retryAt": retry_at.isoformat(),
                    },
                    occurred_at=self._utc_now(),
                )
            )
        logger.warning(
            "whatsapp_campaign.%s campaign_id=%s jobs=%s retry_at=%s",
            event_name,
            campaign.id,
            len(jobs),
            retry_at.isoformat(),
        )

    def _unclaim_jobs(self, jobs: list[WhatsAppCampaignSendJob]) -> None:
        """Devolve jobs reivindicados e não processados para a fila (sempre
        `pending`: job reivindicado nunca tem `scheduled_for` futuro) e limpa o
        marcador de claim do destinatário, para o sweep de órfãos não o tocar."""
        for job in jobs:
            if job.status == CampaignSendJobStatus.processing:
                job.status = CampaignSendJobStatus.pending
                recipient = job.recipient
                if recipient is not None and isinstance(recipient.send_context, dict):
                    send_context = dict(recipient.send_context)
                    if send_context.pop("claim", None) is not None:
                        recipient.send_context = send_context

    async def _campaign_status_in_db(
        self, campaign_id: UUID, workspace_id: UUID
    ) -> CampaignStatus | None:
        """Status relido por coluna: o objeto em memória (`expire_on_commit=False`)
        não vê a pausa gravada por outra sessão (S2)."""
        return await self.db.scalar(
            select(WhatsAppCampaign.status).where(
                WhatsAppCampaign.id == campaign_id,
                WhatsAppCampaign.workspace_id == workspace_id,
            )
        )

    def _cancel_claimed_jobs(self, jobs: list[WhatsAppCampaignSendJob]) -> None:
        """Campanha parada/encerrada por fora durante o lote: os jobs ainda não
        enviados não voltam à fila (não há para onde), são cancelados e a reserva
        de quota do destinatário é liberada, como faz `stop_campaign`."""
        now = self._utc_now()
        for job in jobs:
            if job.status != CampaignSendJobStatus.processing:
                continue
            job.status = CampaignSendJobStatus.cancelled
            job.processed_at = now
            recipient = job.recipient
            if recipient is not None:
                recipient.quota_period_start = None
                recipient.quota_reserved_at = None
                if isinstance(recipient.send_context, dict) and "claim" in recipient.send_context:
                    send_context = dict(recipient.send_context)
                    send_context.pop("claim", None)
                    recipient.send_context = send_context

    def _apply_default_job_retry(
        self,
        campaign: WhatsAppCampaign,
        job: WhatsAppCampaignSendJob,
        recipient: WhatsAppCampaignRecipient,
        error_message: str,
    ) -> None:
        """Retry com backoff limitado do job (erros não mapeados/transientes)."""
        backoff_seconds = min(300, 30 * max(job.attempt_count, 1))
        recipient.last_error = error_message
        if job.attempt_count < CAMPAIGN_JOB_MAX_ATTEMPTS:
            recipient.status = CampaignRecipientStatus.queued
            job.status = CampaignSendJobStatus.pending
            job.last_error = error_message
            job.scheduled_for = self._utc_now() + timedelta(seconds=backoff_seconds)
            job.processed_at = None
        else:
            recipient.status = CampaignRecipientStatus.failed
            job.status = CampaignSendJobStatus.failed
            job.last_error = error_message
            job.processed_at = self._utc_now()
            self.db.add(
                WhatsAppCampaignDeliveryEvent(
                    campaign_id=campaign.id,
                    recipient_id=recipient.id,
                    wamid=recipient.wamid,
                    status=CampaignRecipientStatus.failed,
                    payload={"message": error_message},
                    occurred_at=self._utc_now(),
                )
            )

    def _mark_job_entitlement_failure(
        self,
        campaign: WhatsAppCampaign,
        job: WhatsAppCampaignSendJob,
        recipient: WhatsAppCampaignRecipient,
        error: EntitlementError,
    ) -> None:
        """Persist a terminal, non-provider failure without consuming a send attempt."""

        error_type = safe_error_type(error)
        if isinstance(error, CurrentStockLimitExceededError):
            event_name = "contact_capacity_exceeded"
            detail = (
                "[contact_capacity_exceeded] Limite de contatos atingido "
                f"({error.current_quantity}/{error.limit_quantity}); "
                "nenhum envio externo foi tentado."
            )
            payload: dict[str, Any] = {
                "event": event_name,
                "errorType": error_type,
                "entitlement": error.entitlement.value,
                "currentQuantity": error.current_quantity,
                "limitQuantity": error.limit_quantity,
                "requestedQuantity": error.requested_quantity,
                "providerAttempted": False,
                "retryable": False,
            }
        else:
            event_name = "contact_entitlement_denied"
            detail = (
                "[contact_entitlement_denied] A política comercial bloqueou "
                "a criação do contato; nenhum envio externo foi tentado."
            )
            payload = {
                "event": event_name,
                "errorType": error_type,
                "providerAttempted": False,
                "retryable": False,
            }
        now = self._utc_now()
        recipient.status = CampaignRecipientStatus.failed
        recipient.last_error = detail
        job.status = CampaignSendJobStatus.failed
        job.last_error = detail
        job.scheduled_for = None
        job.processed_at = now
        self.db.add(
            WhatsAppCampaignDeliveryEvent(
                campaign_id=campaign.id,
                recipient_id=recipient.id,
                wamid=recipient.wamid,
                status=CampaignRecipientStatus.failed,
                payload=payload,
                occurred_at=now,
            )
        )

    async def _match_delivery_uncertain_recipient(
        self, wamid: str, payload: dict[str, Any]
    ) -> WhatsAppCampaignRecipient | None:
        """Reconcilia status sem wamid conhecido: casa por telefone um recipient
        congelado em delivery_uncertain (o wamid nunca chegou ao motor) e adota
        o wamid informado pelo webhook."""
        raw_phone = str(payload.get("recipient_id") or "").strip()
        digits = "".join(ch for ch in raw_phone if ch.isdigit())
        if not digits:
            return None
        result = await self.db.execute(
            select(WhatsAppCampaignRecipient)
            .options(selectinload(WhatsAppCampaignRecipient.campaign))
            .where(
                WhatsAppCampaignRecipient.status == CampaignRecipientStatus.processing,
                WhatsAppCampaignRecipient.wamid.is_(None),
            )
            .with_for_update()
        )
        for candidate in result.scalars().all():
            candidate_digits = "".join(ch for ch in (candidate.phone or "") if ch.isdigit())
            context = candidate.send_context or {}
            if candidate_digits == digits and "deliveryUncertain" in context:
                candidate.wamid = wamid
                logger.info(
                    "whatsapp_campaign.delivery_uncertain_reconciled recipient_id=%s",
                    candidate.id,
                )
                return candidate
        return None

    def _mark_job_delivery_uncertain(
        self,
        campaign: WhatsAppCampaign,
        job: WhatsAppCampaignSendJob,
        recipient: WhatsAppCampaignRecipient,
        exc: WhatsAppProviderError,
    ) -> None:
        """Congela o job com entrega incerta até a reconciliação via webhook."""
        note = f"{DELIVERY_UNCERTAIN_ERROR_PREFIX}: {exc.message}"
        job.status = CampaignSendJobStatus.processing
        job.last_error = note
        job.processed_at = self._utc_now()
        recipient.status = CampaignRecipientStatus.processing
        recipient.last_error = note
        send_context = dict(recipient.send_context or {})
        send_context["deliveryUncertain"] = {
            "markedAt": self._utc_now().isoformat(),
            "providerErrorCode": exc.code,
        }
        recipient.send_context = send_context
        self.db.add(
            WhatsAppCampaignDeliveryEvent(
                campaign_id=campaign.id,
                recipient_id=recipient.id,
                wamid=recipient.wamid,
                status=CampaignRecipientStatus.processing,
                payload={"event": "delivery_uncertain", "message": exc.message},
                occurred_at=self._utc_now(),
            )
        )
        logger.warning(
            "whatsapp_campaign.delivery_uncertain campaign_id=%s recipient_id=%s",
            campaign.id,
            recipient.id,
        )

    async def _apply_meta_error_policy(
        self,
        *,
        workspace_id: UUID,
        campaign: WhatsAppCampaign,
        job: WhatsAppCampaignSendJob,
        recipient: WhatsAppCampaignRecipient,
        remaining_jobs: list[WhatsAppCampaignSendJob],
        policy: MetaErrorPolicy,
        exc: WhatsAppProviderError,
    ) -> bool:
        """Despacha o erro da Meta pela política mapeada.

        Retorna True quando o lote atual deve ser interrompido (throughput ou
        pausa/parada da campanha).
        """
        error_code = exc.provider_error_subcode or exc.provider_status_code
        detail = f"[{error_code}] {policy.reason}" if error_code else policy.reason
        now = self._utc_now()

        if policy.disposition == MetaErrorDisposition.retry_batch:
            # Throughput da Meta: reagendar o LOTE inteiro com backoff, sem
            # consumir tentativa do job (a falha não é do destinatário).
            job.attempt_count = max(0, job.attempt_count - 1)
            retry_at = now + timedelta(seconds=CAMPAIGN_BATCH_BACKOFF_SECONDS)
            for batch_job in [job, *remaining_jobs]:
                batch_job.status = CampaignSendJobStatus.pending
                batch_job.scheduled_for = retry_at
                batch_job.processed_at = None
                batch_job.last_error = detail
                if batch_job.recipient is not None:
                    batch_job.recipient.status = CampaignRecipientStatus.queued
            recipient.last_error = detail
            logger.warning(
                "whatsapp_campaign.batch_backoff campaign_id=%s jobs=%s retry_at=%s",
                campaign.id,
                1 + len(remaining_jobs),
                retry_at.isoformat(),
            )
            return True

        if policy.disposition == MetaErrorDisposition.skip_terminal:
            recipient.status = CampaignRecipientStatus.skipped
            recipient.last_error = detail
            job.status = CampaignSendJobStatus.cancelled
            job.last_error = detail
            job.processed_at = now
            event_payload: dict[str, Any] = {
                "event": "recipient_skipped",
                "providerCode": error_code,
                "reason": policy.reason,
                "message": exc.message,
            }
            if policy.suppress_contact:
                await self._ensure_permanent_suppression(
                    workspace_id=workspace_id,
                    campaign=campaign,
                    recipient=recipient,
                    provider_code=error_code,
                    reason=policy.reason,
                )
                event_payload["suppressionCreated"] = True
            self.db.add(
                WhatsAppCampaignDeliveryEvent(
                    campaign_id=campaign.id,
                    recipient_id=recipient.id,
                    wamid=recipient.wamid,
                    status=CampaignRecipientStatus.skipped,
                    payload=event_payload,
                    occurred_at=now,
                )
            )
            return False

        if policy.disposition == MetaErrorDisposition.fail_terminal:
            recipient.status = CampaignRecipientStatus.failed
            recipient.last_error = detail
            job.status = CampaignSendJobStatus.failed
            job.last_error = detail
            job.processed_at = now
            if policy.invalidate_member:
                await self._invalidate_audience_members(
                    workspace_id=workspace_id,
                    phone=recipient.phone,
                    provider_code=error_code,
                    reason=policy.reason,
                )
            self.db.add(
                WhatsAppCampaignDeliveryEvent(
                    campaign_id=campaign.id,
                    recipient_id=recipient.id,
                    wamid=recipient.wamid,
                    status=CampaignRecipientStatus.failed,
                    payload={
                        "event": "recipient_failed_terminal",
                        "providerCode": error_code,
                        "reason": policy.reason,
                        "message": exc.message,
                    },
                    occurred_at=now,
                )
            )
            return False

        if policy.disposition in {
            MetaErrorDisposition.pause_campaign,
            MetaErrorDisposition.halt_campaign,
        }:
            # O envio deste destinatário não aconteceu: devolve o job à fila
            # (sem consumir tentativa) e pausa a campanha imediatamente.
            job.attempt_count = max(0, job.attempt_count - 1)
            job.status = CampaignSendJobStatus.pending
            job.scheduled_for = None
            job.processed_at = None
            job.last_error = detail
            recipient.status = CampaignRecipientStatus.queued
            recipient.last_error = detail
            self._unclaim_jobs(remaining_jobs)
            await self._pause_campaign_with_reason(
                campaign,
                reason=detail,
                provider_code=error_code,
                recipient=recipient,
            )
            return True

        # retry_job (auth/desconhecidos): comportamento padrão preservado.
        self._apply_default_job_retry(campaign, job, recipient, detail or str(exc))
        return False

    async def _ensure_permanent_suppression(
        self,
        *,
        workspace_id: UUID,
        campaign: WhatsAppCampaign,
        recipient: WhatsAppCampaignRecipient,
        provider_code: int | None,
        reason: str,
    ) -> None:
        """Cria/reativa supressão permanente após opt-out sinalizado pela Meta."""
        phone = normalize_phone_number(recipient.phone) or recipient.phone
        entry = (
            (
                await self.db.execute(
                    select(WhatsAppSuppressionEntry).where(
                        WhatsAppSuppressionEntry.workspace_id == workspace_id,
                        WhatsAppSuppressionEntry.phone.in_(phone_number_variants(phone)),
                    )
                )
            )
            .scalars()
            .first()
        )
        if entry is None:
            entry = WhatsAppSuppressionEntry(workspace_id=workspace_id, phone=phone)
        entry.contact_id = recipient.contact_id or entry.contact_id
        entry.reason = "meta_opt_out_marketing"
        entry.details = {
            **(entry.details or {}),
            "origin": "campaign_engine",
            "providerCode": provider_code,
            "policyReason": reason,
            "campaignId": str(campaign.id),
            "recipientId": str(recipient.id),
            "suppressedAt": self._utc_now().isoformat(),
        }
        entry.is_active = True
        self.db.add(entry)
        logger.info(
            "whatsapp_campaign.suppression_created campaign_id=%s recipient_id=%s provider_code=%s",
            campaign.id,
            recipient.id,
            provider_code,
        )

    async def _invalidate_audience_members(
        self,
        *,
        workspace_id: UUID,
        phone: str,
        provider_code: int | None,
        reason: str,
    ) -> None:
        """Invalida o telefone em todas as listas de audiência do workspace."""
        normalized = normalize_phone_number(phone) or phone
        members = (
            (
                await self.db.execute(
                    select(WhatsAppAudienceListMember).where(
                        WhatsAppAudienceListMember.workspace_id == workspace_id,
                        WhatsAppAudienceListMember.phone.in_(phone_number_variants(normalized)),
                        WhatsAppAudienceListMember.status != "invalid",
                    )
                )
            )
            .scalars()
            .all()
        )
        if not members:
            return
        for member in members:
            member.status = "invalid"
            metadata = dict(member.member_metadata or {})
            metadata["invalidatedBy"] = {
                "origin": "campaign_engine",
                "providerCode": provider_code,
                "reason": reason,
                "invalidatedAt": self._utc_now().isoformat(),
            }
            member.member_metadata = metadata
        await self.db.flush()
        for audience_list_id in {member.audience_list_id for member in members}:
            audience_list = await self.db.get(WhatsAppAudienceList, audience_list_id)
            if audience_list is None:
                continue
            invalid_count = (
                await self.db.scalar(
                    select(func.count(WhatsAppAudienceListMember.id)).where(
                        WhatsAppAudienceListMember.workspace_id == workspace_id,
                        WhatsAppAudienceListMember.audience_list_id == audience_list_id,
                        WhatsAppAudienceListMember.status == "invalid",
                    )
                )
                or 0
            )
            await self._refresh_audience_list_counts(
                audience_list,
                invalid=int(invalid_count),
                duplicates=audience_list.duplicate_count,
            )

    async def _pause_campaign_with_reason(
        self,
        campaign: WhatsAppCampaign,
        *,
        reason: str,
        provider_code: int | None = None,
        recipient: WhatsAppCampaignRecipient | None = None,
    ) -> None:
        """Pausa a campanha registrando o motivo em evento de auditoria."""
        campaign.status = CampaignStatus.paused
        campaign.paused_at = self._utc_now()
        campaign.pause_reason = reason[:500]
        campaign.paused_by_policy = True
        event_recipient = recipient
        if event_recipient is None:
            event_recipient = (
                (
                    await self.db.execute(
                        select(WhatsAppCampaignRecipient)
                        .where(
                            WhatsAppCampaignRecipient.campaign_id == campaign.id,
                            WhatsAppCampaignRecipient.status == CampaignRecipientStatus.failed,
                        )
                        .order_by(WhatsAppCampaignRecipient.updated_at.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
        if event_recipient is not None:
            self.db.add(
                WhatsAppCampaignDeliveryEvent(
                    campaign_id=campaign.id,
                    recipient_id=event_recipient.id,
                    wamid=event_recipient.wamid,
                    status=event_recipient.status,
                    payload={
                        "event": "campaign_auto_paused",
                        "pauseReason": reason,
                        "providerCode": provider_code,
                    },
                    occurred_at=self._utc_now(),
                )
            )
        logger.error(
            "whatsapp_campaign.auto_paused campaign_id=%s provider_code=%s reason=%s",
            campaign.id,
            provider_code,
            reason,
        )

    async def _maybe_engage_failure_kill_switch(
        self, campaign: WhatsAppCampaign, *, current_status: CampaignStatus | None = None
    ) -> None:
        """Pausa automaticamente a campanha quando a taxa de falha explode.
        `current_status` é o status relido do banco (S2): uma pausa manual
        gravada por fora não pode ser sobrescrita pela pausa por política."""
        if (current_status or campaign.status) != CampaignStatus.running:
            return
        attempts = campaign.sent_count + campaign.failed_count
        if attempts < CAMPAIGN_KILL_SWITCH_MIN_ATTEMPTS:
            return
        failure_rate = campaign.failed_count / attempts
        if failure_rate <= CAMPAIGN_FAILURE_RATE_KILL_SWITCH:
            return
        await self._pause_campaign_with_reason(
            campaign,
            reason=(
                f"Kill switch automático: taxa de falha de {failure_rate:.0%} "
                f"({campaign.failed_count} falhas em {attempts} tentativas) excedeu "
                f"o limite de {CAMPAIGN_FAILURE_RATE_KILL_SWITCH:.0%}."
            ),
        )

    async def _refresh_campaign_counters(self, campaign_id: UUID) -> None:
        result = await self.db.execute(
            select(
                func.count(WhatsAppCampaignRecipient.id),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                WhatsAppCampaignRecipient.status.in_(
                                    [
                                        CampaignRecipientStatus.sent,
                                        CampaignRecipientStatus.delivered,
                                        CampaignRecipientStatus.read,
                                    ]
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                WhatsAppCampaignRecipient.status.in_(
                                    [
                                        CampaignRecipientStatus.delivered,
                                        CampaignRecipientStatus.read,
                                    ]
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                WhatsAppCampaignRecipient.status == CampaignRecipientStatus.read,
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                WhatsAppCampaignRecipient.status == CampaignRecipientStatus.failed,
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
            ).where(WhatsAppCampaignRecipient.campaign_id == campaign_id)
        )
        total, sent, delivered, read, failed = result.one()
        campaign = await self.db.get(WhatsAppCampaign, campaign_id)
        if campaign is None:
            return
        campaign.total_recipients = int(total or 0)
        campaign.eligible_recipients = int(total or 0)
        campaign.sent_count = int(sent or 0)
        campaign.delivered_count = int(delivered or 0)
        campaign.read_count = int(read or 0)
        campaign.failed_count = int(failed or 0)
        await self.db.flush()

    def _build_recipient_context(
        self,
        contact: WhatsAppContact | None,
        matter: Matter | None,
        *,
        phone: str | None = None,
        name: str | None = None,
        email: str | None = None,
    ) -> dict[str, Any]:
        contact_name = name or (contact.name if contact else None) or "Cliente"
        contact_phone = phone or (contact.phone if contact else "")
        contact_first_name = contact_name.split()[0] if contact_name else None
        return {
            "client_name": name or (contact.name if contact else None) or "Cliente",
            "client_phone": phone or (contact.phone if contact else ""),
            "client_email": email or "",
            "matter_id": str(matter.id) if matter else "",
            "demand_type": matter.demand_type if matter else "",
            "lead_source": matter.lead_source if matter else "",
            "next_action": matter.next_action if matter else "",
            "column_id": matter.column_id.value if matter else "",
            "practice_area": matter.practice_area.value if matter else "",
            # Canonical picker field paths are carried into the follow-up
            # FlowRun context so delayed sequence stages resolve the same
            # field-backed bindings used by the picker.  The flat legacy keys
            # above remain for existing campaign templates.
            "contact": {
                "first_name": contact_first_name,
                "name": contact_name,
                "phone": contact_phone,
            },
            "client": {"email": email or ""},
            "matter": {
                "id": str(matter.id) if matter else "",
                "case_number": matter.case_number if matter else "",
                "demand_type": matter.demand_type if matter else "",
                "practice_area": matter.practice_area.value if matter else "",
            },
        }

    def _resolve_parameters(self, values: list[str], context: dict[str, str]) -> list[str]:
        resolved: list[str] = []
        for value in values:
            rendered = value
            for key, raw in context.items():
                # send_context também carrega marcadores estruturais (claim,
                # deliveryUncertain); somente valores textuais são placeholders.
                if raw is not None and not isinstance(raw, str):
                    continue
                rendered = rendered.replace(f"{{{{{key}}}}}", raw or "")
            resolved.append(rendered)
        return resolved

    def _resolve_button_parameters(
        self, values: list[Any], context: dict[str, str]
    ) -> list[str | dict[str, Any]]:
        """Render button suffixes without losing their real Meta array index."""
        resolved: list[str | dict[str, Any]] = []
        for fallback_index, item in enumerate(values):
            if isinstance(item, dict) or hasattr(item, "index"):
                index = item.get("index", fallback_index) if isinstance(item, dict) else item.index
                raw_value = item.get("value") if isinstance(item, dict) else item.value
                rendered = self._resolve_parameters([str(raw_value or "")], context)[0]
                resolved.append({"index": index, "value": rendered})
            else:
                resolved.extend(self._resolve_parameters([str(item)], context))
        return resolved

    def _normalize_meta_template_status(self, raw_status: Any) -> MetaTemplateStatus:
        normalized = str(raw_status or "").strip().lower()
        mapping = {
            "approved": MetaTemplateStatus.approved,
            "active": MetaTemplateStatus.approved,
            "pending": MetaTemplateStatus.pending,
            "in_appeal": MetaTemplateStatus.pending,
            "rejected": MetaTemplateStatus.rejected,
            "paused": MetaTemplateStatus.paused,
            "disabled": MetaTemplateStatus.disabled,
        }
        return mapping.get(normalized, MetaTemplateStatus.pending)

    def _map_delivery_status(self, raw_status: str) -> CampaignRecipientStatus:
        mapping = {
            "sent": CampaignRecipientStatus.sent,
            "delivered": CampaignRecipientStatus.delivered,
            "read": CampaignRecipientStatus.read,
            "failed": CampaignRecipientStatus.failed,
        }
        return mapping.get(raw_status, CampaignRecipientStatus.sent)

    def _normalize_datetime(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _is_future(self, value: datetime | None, reference: datetime | None = None) -> bool:
        normalized = self._normalize_datetime(value)
        if normalized is None:
            return False
        baseline = self._normalize_datetime(reference) or self._utc_now()
        return normalized > baseline

    def _to_campaign_read(
        self,
        campaign: WhatsAppCampaign,
        *,
        projection: tuple[dict[CampaignRecipientStatus, int], list[WhatsAppCampaignRecipient]]
        | None = None,
    ) -> CampaignRead:
        audience = (
            CampaignAudienceRead.model_validate(campaign.audience) if campaign.audience else None
        )
        template_snapshot = (
            CampaignTemplateSnapshotRead.model_validate(campaign.template_snapshot)
            if campaign.template_snapshot
            else None
        )
        if projection is not None:
            counts, sample = projection
        else:
            loaded = list(campaign.recipients or [])
            counts = {}
            for item in loaded:
                counts[item.status] = counts.get(item.status, 0) + 1
            sample = sorted(
                loaded, key=lambda item: (item.created_at, str(item.id)), reverse=True
            )[:8]
        sent_count = campaign.sent_count
        delivered_count = campaign.delivered_count
        read_count = campaign.read_count
        failed_count = campaign.failed_count
        total_recipients = campaign.total_recipients
        eligible_recipients = campaign.eligible_recipients
        if counts:
            total_from_counts = sum(counts.values())
            total_recipients = max(total_recipients, total_from_counts)
            eligible_recipients = max(eligible_recipients, total_from_counts)
            delivered_or_read = counts.get(CampaignRecipientStatus.delivered, 0) + counts.get(
                CampaignRecipientStatus.read, 0
            )
            sent_count = counts.get(CampaignRecipientStatus.sent, 0) + delivered_or_read
            delivered_count = delivered_or_read
            read_count = counts.get(CampaignRecipientStatus.read, 0)
            failed_count = counts.get(CampaignRecipientStatus.failed, 0)
        sample_recipients = [
            CampaignRecipientRead.model_validate(recipient) for recipient in sample
        ]
        return CampaignRead(
            id=campaign.id,
            whatsappConnectionId=campaign.whatsapp_connection_id,
            createdById=campaign.created_by_id,
            followUpFlowId=campaign.follow_up_flow_id,
            flowStartMode=campaign.flow_start_mode,
            title=campaign.title,
            description=campaign.description,
            status=campaign.status,
            scheduledAt=campaign.scheduled_at,
            launchedAt=campaign.launched_at,
            completedAt=campaign.completed_at,
            pausedAt=campaign.paused_at,
            totalRecipients=total_recipients,
            eligibleRecipients=eligible_recipients,
            blockedRecipients=campaign.blocked_recipients,
            sentCount=sent_count,
            deliveredCount=delivered_count,
            readCount=read_count,
            failedCount=failed_count,
            audience=audience,
            templateSnapshot=template_snapshot,
            sampleRecipients=sample_recipients,
            createdAt=campaign.created_at,
            updatedAt=campaign.updated_at,
        )

    def _utc_now(self) -> datetime:
        return datetime.now(UTC)
