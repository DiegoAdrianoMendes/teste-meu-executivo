from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import external_delivery_allowed
from app.models.klm_operations import KlmAuditEvent
from app.models.whatsapp import (
    WaMessageDirection,
    WaMessageStatus,
    WhatsAppConnection,
    WhatsAppConnectionTier,
    WhatsAppConnectionTiers,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    WhatsAppCampaign,
    WhatsAppCampaignRecipient,
)
from app.services.whatsapp.connection_service import WhatsAppConnectionService
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.meta_cloud_client import MetaCloudClient

logger = logging.getLogger(__name__)

# Janela rolante da regra de mensagens únicas/24h da Meta.
ROLLING_WINDOW = timedelta(hours=24)

VALID_QUALITY_RATINGS = {"verde", "amarelo", "vermelho"}

# Estados que contam como "envio efetivo" para a quota de destinatários
# únicos/24h da Meta (ela conta o envio confirmado, não a leitura/resposta,
# mas delivered/read implicam sent, então entram na mesma contagem).
EFFECTIVE_SEND_RECIPIENT_STATUSES = (
    CampaignRecipientStatus.sent,
    CampaignRecipientStatus.delivered,
    CampaignRecipientStatus.read,
)
EFFECTIVE_SEND_MESSAGE_STATUSES = (
    WaMessageStatus.sent,
    WaMessageStatus.delivered,
    WaMessageStatus.read,
)

# Limite diário de destinatários únicos por tier (None = ilimitado).
TIER_DAILY_LIMITS: dict[WhatsAppConnectionTier, Optional[int]] = {
    WhatsAppConnectionTier.tier_250: 250,
    WhatsAppConnectionTier.tier_1k: 1_000,
    WhatsAppConnectionTier.tier_10k: 10_000,
    WhatsAppConnectionTier.tier_100k: 100_000,
    WhatsAppConnectionTier.unlimited: None,
}

META_QUALITY_RATINGS = {
    "GREEN": "verde",
    "YELLOW": "amarelo",
    "RED": "vermelho",
}

META_MESSAGING_TIERS = {
    "TIER_250": WhatsAppConnectionTier.tier_250,
    "TIER_1K": WhatsAppConnectionTier.tier_1k,
    "TIER_10K": WhatsAppConnectionTier.tier_10k,
    "TIER_100K": WhatsAppConnectionTier.tier_100k,
    "TIER_UNLIMITED": WhatsAppConnectionTier.unlimited,
    "UNLIMITED": WhatsAppConnectionTier.unlimited,
}


class ConnectionHealthError(Exception):
    """Erro de domínio da saúde do número WhatsApp."""

    def __init__(self, code: str, message: str, status_code: int = status.HTTP_400_BAD_REQUEST) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)

    def to_http_exception(self) -> HTTPException:
        return HTTPException(
            status_code=self.status_code,
            detail={"code": self.code, "message": self.message},
        )


class WhatsAppConnectionHealthService:
    """
    Painel de saúde do número WhatsApp (W5.6): tier de envio, quota diária,
    quality rating e estágio de rampa por conexão. Toda a lógica é local
    (fail-closed honesto): a sincronização real com a Business Management
    API da Meta só ocorre quando `external_delivery_allowed()` estiver
    ligado; até lá, os dados refletem apenas o estado registrado no KLM.

    Fonte da verdade da quota de 24h (W1.5, revisão pós-auditoria): a janela
    ROLANTE REAL calculada por `sliding_window_usage`, que consulta
    diretamente `WhatsAppCampaignRecipient` (campanhas) e `WhatsAppMessage`
    (envios avulsos) da conexão nas últimas 24h e conta telefones distintos
    (destinatários únicos), sem estado novo. `register_recipients` e os
    campos `daily_unique_recipients`/`window_started_at` de
    `WhatsAppConnectionTiers` continuam existindo por compatibilidade (e
    para exibição rápida sem custo de query), mas são apenas um CACHE
    INFORMATIVO do último lote registrado — nunca a base de decisão de
    quota. A versão anterior desses campos era "tumbling" (a janela só
    resetava quando expirava por completo, permitindo até 2x o limite no
    boundary); a query desta classe elimina esse problema por construção,
    pois sempre soma exatamente os eventos com timestamp dentro das
    últimas 24h a partir do instante da consulta.
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        meta_client: MetaCloudClient | None = None,
        connection_service: WhatsAppConnectionService | None = None,
    ) -> None:
        self.db = db
        self.meta_client = meta_client or MetaCloudClient()
        self.connection_service = connection_service or WhatsAppConnectionService(
            db,
            meta_client=self.meta_client,
        )

    async def get_or_create_tier(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
    ) -> WhatsAppConnectionTiers:
        record = await self._find(connection_id)
        if record is not None:
            if record.workspace_id != workspace_id:
                raise ConnectionHealthError(
                    code="WA-HEALTH-001",
                    message="Esta conexão WhatsApp não pertence ao workspace informado.",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            return record

        connection = await self._require_connection(workspace_id, connection_id)
        record = WhatsAppConnectionTiers(
            workspace_id=connection.workspace_id,
            connection_id=connection.id,
            tier=WhatsAppConnectionTier.tier_250,
            daily_unique_recipients=0,
            window_started_at=datetime.now(UTC),
            ramp_up_stage=0,
            quality_rating=None,
            last_synced_at=None,
        )
        self.db.add(record)
        await self.db.flush()
        return record

    async def register_recipients(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        count: int,
        when: Optional[datetime] = None,
    ) -> WhatsAppConnectionTiers:
        """
        Atualiza o CACHE INFORMATIVO (`daily_unique_recipients`/
        `window_started_at`) com o último lote de destinatários únicos
        registrado. Mantido por compatibilidade (leitura rápida sem query)
        e para telemetria; a decisão de quota real usa
        `sliding_window_usage`, não este contador. Comportamento tumbling
        preservado aqui de propósito: é só um cache, não a fonte da
        verdade — se a janela do cache expirou no momento `when`, o
        contador reseta antes de somar o novo lote.
        """
        if count < 0:
            raise ConnectionHealthError(
                code="WA-HEALTH-002",
                message="Quantidade de destinatários não pode ser negativa.",
            )
        moment = when or datetime.now(UTC)
        record = await self.get_or_create_tier(workspace_id=workspace_id, connection_id=connection_id)

        window_started_at = record.window_started_at
        if window_started_at.tzinfo is None:
            window_started_at = window_started_at.replace(tzinfo=UTC)

        if moment - window_started_at >= ROLLING_WINDOW:
            record.daily_unique_recipients = 0
            record.window_started_at = moment

        record.daily_unique_recipients += count
        await self.db.flush()
        return record

    async def sliding_window_usage(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        now: Optional[datetime] = None,
    ) -> dict:
        """
        Janela deslizante REAL (W1.5): destinatários únicos das últimas 24h
        calculados por query sobre dados que já existem, sem estado novo.

        Une, por telefone distinto:
        (a) `WhatsAppCampaignRecipient` de campanhas desta conexão com
            envio efetivo (sent/delivered/read) e `sent_at` dentro da
            janela;
        (b) `WhatsAppMessage` outbound avulsas desta conexão (fora de
            campanha) com envio efetivo e `timestamp` dentro da janela.

        Ao contrário do contador tumbling anterior, o corte é sempre
        `now - 24h` recalculado a cada chamada: não há reset discreto que
        permita 2x o limite no boundary (ex.: 250 destinatários às 23h de
        ontem e uma nova tentativa de 250 às 10h de hoje conta os primeiros
        como ainda dentro da janela, pois `23h de ontem` está a menos de
        24h de `10h de hoje`).
        """
        moment = now or datetime.now(UTC)
        cutoff = moment - ROLLING_WINDOW

        campaign_phones = (
            await self.db.execute(
                select(WhatsAppCampaignRecipient.phone)
                .join(
                    WhatsAppCampaign,
                    WhatsAppCampaign.id == WhatsAppCampaignRecipient.campaign_id,
                )
                .where(
                    WhatsAppCampaign.whatsapp_connection_id == connection_id,
                    WhatsAppCampaignRecipient.workspace_id == workspace_id,
                    WhatsAppCampaignRecipient.status.in_(EFFECTIVE_SEND_RECIPIENT_STATUSES),
                    WhatsAppCampaignRecipient.sent_at.is_not(None),
                    WhatsAppCampaignRecipient.sent_at > cutoff,
                    WhatsAppCampaignRecipient.sent_at <= moment,
                )
                .distinct()
            )
        ).scalars().all()

        message_phones = (
            await self.db.execute(
                select(WhatsAppContact.phone)
                .join(WhatsAppMessage, WhatsAppMessage.contact_id == WhatsAppContact.id)
                .where(
                    WhatsAppMessage.whatsapp_connection_id == connection_id,
                    WhatsAppContact.workspace_id == workspace_id,
                    WhatsAppMessage.direction == WaMessageDirection.outbound,
                    WhatsAppMessage.status.in_(EFFECTIVE_SEND_MESSAGE_STATUSES),
                    WhatsAppMessage.timestamp > cutoff,
                    WhatsAppMessage.timestamp <= moment,
                )
                .distinct()
            )
        ).scalars().all()

        unique_recipients = {phone for phone in campaign_phones if phone} | {
            phone for phone in message_phones if phone
        }
        return {
            "used": len(unique_recipients),
            "windowStartedAt": cutoff,
            "now": moment,
        }

    async def remaining_daily_quota(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        now: Optional[datetime] = None,
    ) -> dict:
        """
        Retorna {tier, limit, used, remaining} usando a janela deslizante
        REAL (`sliding_window_usage`) como fonte da verdade — não mais o
        cache tumbling de `daily_unique_recipients`.
        """
        record = await self.get_or_create_tier(workspace_id=workspace_id, connection_id=connection_id)
        limit = TIER_DAILY_LIMITS[record.tier]

        usage = await self.sliding_window_usage(
            workspace_id=workspace_id, connection_id=connection_id, now=now
        )
        used = usage["used"]

        remaining = None if limit is None else max(limit - used, 0)
        return {
            "tier": record.tier,
            "limit": limit,
            "used": used,
            "remaining": remaining,
            "windowStartedAt": usage["windowStartedAt"],
        }

    async def sync_from_provider(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
    ) -> dict:
        """
        Sincroniza tier e qualidade com a Graph API usando o token cifrado
        da própria conexão. O gate fechado e falhas do provedor preservam o
        último estado local, mas nunca são apresentados como sincronização.
        """
        record = await self.get_or_create_tier(workspace_id=workspace_id, connection_id=connection_id)
        if not external_delivery_allowed():
            return {
                "synced": False,
                "reason": "external_delivery_disabled",
                "message": (
                    "Sincronização com a Meta desabilitada neste ambiente; "
                    "exibindo apenas dados locais do KLM."
                ),
                "record": record,
            }

        connection = await self._require_connection(workspace_id, connection_id)
        if not connection.phone_number_id:
            return {
                "synced": False,
                "reason": "phone_number_id_missing",
                "message": "A conexão ainda não possui Phone Number ID para consultar na Meta.",
                "record": record,
            }

        try:
            access_token = await self.connection_service.resolve_meta_access_token(connection)
            details = await self.meta_client.get_phone_number_details(
                connection.phone_number_id,
                access_token,
            )
        except WhatsAppProviderError as exc:
            logger.warning(
                "whatsapp_health_provider_sync_failed",
                extra={
                    "workspace_id": str(workspace_id),
                    "connection_id": str(connection_id),
                    "provider_error_code": exc.code,
                },
            )
            return {
                "synced": False,
                "reason": "provider_unavailable",
                "message": (
                    "A Meta não confirmou a saúde do número agora; "
                    "o último estado local foi preservado."
                ),
                "record": record,
            }

        before = {
            "tier": record.tier.value,
            "qualityRating": record.quality_rating,
        }
        provider_quality = str(details.get("quality_rating") or "").upper()
        provider_tier = str(details.get("messaging_limit_tier") or "").upper()
        mapped_quality = META_QUALITY_RATINGS.get(provider_quality)
        mapped_tier = META_MESSAGING_TIERS.get(provider_tier)
        if mapped_quality is not None:
            record.quality_rating = mapped_quality
        if mapped_tier is not None:
            record.tier = mapped_tier
        record.last_synced_at = datetime.now(UTC)
        after = {
            "tier": record.tier.value,
            "qualityRating": record.quality_rating,
        }
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_type="system",
                actor_label="Meta Graph API",
                event_type="whatsapp_connection_health_synced",
                entity_type="whatsapp_connection_tiers",
                entity_id=record.id,
                summary=f"Saúde da conexão WhatsApp {connection_id} sincronizada com a Meta.",
                before_data=before,
                after_data=after,
            )
        )
        await self.db.flush()
        return {
            "synced": True,
            "reason": "provider_confirmed",
            "message": "Tier e qualidade confirmados pela Meta.",
            "record": record,
        }

    async def sync_from_provider_stub(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
    ) -> dict:
        """Alias temporário para consumidores legados do método antigo."""
        return await self.sync_from_provider(
            workspace_id=workspace_id,
            connection_id=connection_id,
        )

    async def set_quality_rating(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        quality_rating: Optional[str],
        actor_label: str = "Sistema KLM",
        actor_type: str = "system",
    ) -> WhatsAppConnectionTiers:
        if quality_rating is not None and quality_rating not in VALID_QUALITY_RATINGS:
            raise ConnectionHealthError(
                code="WA-HEALTH-003",
                message="quality_rating deve ser 'verde', 'amarelo', 'vermelho' ou nulo.",
            )
        record = await self.get_or_create_tier(workspace_id=workspace_id, connection_id=connection_id)
        before = record.quality_rating
        record.quality_rating = quality_rating
        record.last_synced_at = datetime.now(UTC)
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_type=actor_type,
                actor_label=actor_label,
                event_type="whatsapp_connection_quality_rating_updated",
                entity_type="whatsapp_connection_tiers",
                entity_id=record.id,
                summary=(
                    f"Quality rating da conexão WhatsApp {connection_id} "
                    f"alterado de {before or 'desconhecido'} para {quality_rating or 'desconhecido'}."
                ),
                before_data={"qualityRating": before},
                after_data={"qualityRating": quality_rating},
            )
        )
        await self.db.flush()
        return record

    async def get_health(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
    ) -> dict:
        """Payload consolidado para o endpoint de saúde do número."""
        await self._require_connection(workspace_id, connection_id)
        quota = await self.remaining_daily_quota(workspace_id=workspace_id, connection_id=connection_id)
        sync_result = await self.sync_from_provider(
            workspace_id=workspace_id,
            connection_id=connection_id,
        )
        record = sync_result["record"]
        return {
            "connectionId": connection_id,
            "tier": record.tier,
            "dailyLimit": quota["limit"],
            "dailyUsed": quota["used"],
            "dailyRemaining": quota["remaining"],
            "windowStartedAt": quota["windowStartedAt"],
            "qualityRating": record.quality_rating,
            "rampUpStage": record.ramp_up_stage,
            "lastSyncedAt": record.last_synced_at,
            "syncedFromMeta": sync_result["synced"],
            "syncMessage": sync_result["message"],
            "honest": True,
        }

    async def _find(self, connection_id: UUID) -> Optional[WhatsAppConnectionTiers]:
        result = await self.db.execute(
            select(WhatsAppConnectionTiers).where(
                WhatsAppConnectionTiers.connection_id == connection_id
            )
        )
        return result.scalars().first()

    async def _require_connection(self, workspace_id: UUID, connection_id: UUID) -> WhatsAppConnection:
        result = await self.db.execute(
            select(WhatsAppConnection).where(
                WhatsAppConnection.id == connection_id,
                WhatsAppConnection.workspace_id == workspace_id,
            )
        )
        connection = result.scalars().first()
        if not connection:
            raise ConnectionHealthError(
                code="WA-HEALTH-004",
                message="Conexão WhatsApp não encontrada.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return connection
