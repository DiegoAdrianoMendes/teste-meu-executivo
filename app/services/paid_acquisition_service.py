"""Service: Aquisição paga integrada (Sprint F5).

Fecha o ciclo "campanha → conversa → receita → sinal → campanha melhor" sem
executar nenhuma chamada real à Marketing API enquanto os gates externos do
KLM Hub estiverem fechados (`KLM_EXTERNAL_ACTIONS_ENABLED` etc, ver
`app.core.klm_runtime`).

Duas responsabilidades:

1. `sync_status` / preparação de sync: leitura honesta do estado de
   sincronização (usa a projeção já existente do Ads Manager —
   `AdsManagerEntity`/`AdsManagerImportReceipt`/`AdsMetricObservation` — sem
   reimplementar ingestão; a ingestão de snapshot em si é feita por
   `AdsManagerService.ingest_snapshot`, já gated).
2. `prepare_campaign_draft`: valida pré-requisitos por canal (CTWA exige
   WhatsApp conectado, CTM exige Página/Messenger conectado, CTD exige
   Instagram conectado) e registra um `ExternalActionIntent` com o payload
   completo da Marketing API que SERIA enviado. Status nasce
   `awaiting_approval`: a aprovação/rejeição usa o fluxo genérico já existente
   (`KlmOperationsService.approve_intent_locally` / `reject_intent_locally`,
   expostos em `/api/v1/klm/external-intents/{id}/approve-local`). A execução
   real (ponto de extensão) fica atrás de `require_external_delivery_allowed`
   e não é implementada aqui — não há nenhuma chamada de rede neste módulo.
3. `campaign_funnel_summary`: junta spend por campanha (projeção do Ads
   Manager) com conversas/qualificados/contratos/receita atribuídos via
   `AcquisitionEvent` (fonte primária de verdade do funil, não o snapshot do
   provedor) para produzir CPL/CAC/ROAS por campanha.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import external_delivery_allowed
from app.models.ads_manager import (
    AdsManagerEntity,
    AdsManagerImportReceipt,
)
from app.models.klm_operations import (
    AcquisitionEvent,
    ExternalActionIntent,
    ExternalActionIntentStatus,
)
from app.models.omnichannel_v6 import OmnichannelChannelConnection
from app.schemas.paid_acquisition import (
    CHANNEL_LABELS,
    PrepareCampaignDraftRequest,
)
from app.services.acquisition_attribution_service import extract_attribution
from app.services.ads_manager_service import AdsManagerService

PAID_ACQUISITION_PROVIDER = "meta_ads"
PAID_ACQUISITION_ACTION_PREFIX = "ads_acquisition."
PAID_ACQUISITION_CAMPAIGN_CREATE = f"{PAID_ACQUISITION_ACTION_PREFIX}campaign_create"

# Canal de destino da campanha -> canal de conexão exigido (OmnichannelChannel.value)
_CHANNEL_REQUIREMENT: dict[str, tuple[str, str]] = {
    "ctwa": ("whatsapp", "Número de WhatsApp conectado e ativo"),
    "ctm": ("messenger", "Página do Facebook (Messenger) conectada e ativa"),
    "ctd": ("instagram", "Conta do Instagram conectada e ativa"),
}
_INACTIVE_CONNECTION_STATUSES = {"paused", "disconnected_local"}

# Nomes de evento (AcquisitionEvent.event_name), já normalizados, que contam
# como cada estágio do funil de aquisição paga. Alinhado ao vocabulário de
# app.services.klm_operations_service (TRACKING_*), mas mantido local e
# independente para não acoplar a este serviço num arquivo de frente ativa.
_CONVERSATION_EVENTS = frozenset(
    {"conversation_started", "conversation", "contact", "conversationstarted"}
)
_QUALIFIED_EVENTS = frozenset({"qualified_lead", "qualifiedlead", "lead_qualified"})
_CONTRACT_EVENTS = frozenset({"client", "contract_created", "client_created"})
_REVENUE_EVENTS = frozenset({"payment_confirmed", "purchase"})


class PaidAcquisitionError(RuntimeError):
    """Base error for the paid-acquisition governed layer."""


class PaidAcquisitionValidationError(PaidAcquisitionError):
    """Raised when a campaign draft fails a channel prerequisite."""


class PaidAcquisitionNotFoundError(PaidAcquisitionError):
    """Raised when a requested campaign draft does not exist for the workspace."""


def _normalize_event_name(value: str) -> str:
    return "".join(ch for ch in value.strip().lower() if ch.isalnum() or ch == "_").strip("_")


def _decimal_to_float(value: Any) -> float:
    if value is None:
        return 0.0
    return float(Decimal(str(value)))


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if not denominator:
        return None
    return round(numerator / denominator, 4)


class PaidAcquisitionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self._ads_manager = AdsManagerService(db)

    # ------------------------------------------------------------------
    # Sync status (leitura honesta, nenhuma chamada externa)
    # ------------------------------------------------------------------
    async def sync_status(self, workspace_id: UUID) -> dict[str, Any]:
        gate_open = external_delivery_allowed()
        synced_campaigns = len(
            (
                await self.db.scalars(
                    select(AdsManagerEntity.id).where(
                        AdsManagerEntity.workspace_id == workspace_id,
                        AdsManagerEntity.entity_type == "campaign",
                        AdsManagerEntity.provider == PAID_ACQUISITION_PROVIDER,
                    )
                )
            ).all()
        )
        receipts = list(
            (
                await self.db.scalars(
                    select(AdsManagerImportReceipt)
                    .where(
                        AdsManagerImportReceipt.workspace_id == workspace_id,
                        AdsManagerImportReceipt.provider == PAID_ACQUISITION_PROVIDER,
                    )
                    .order_by(AdsManagerImportReceipt.received_at.desc())
                )
            ).all()
        )
        last_receipt = receipts[0] if receipts else None
        connected = synced_campaigns > 0
        if not connected:
            return {
                "gate_open": gate_open,
                "connected": False,
                "provider": PAID_ACQUISITION_PROVIDER,
                "last_synced_at": None,
                "synced_campaigns": 0,
                "synced_receipts": 0,
                "last_receipt_source": None,
                "state": "not_synced",
                "message": (
                    "Nenhum sync do Meta Ads registrado ainda. "
                    + (
                        "As chamadas reais à Marketing API estão bloqueadas pelo gate "
                        "do Meu Executivo; ingira um snapshot local (dev) para desenvolver."
                        if not gate_open
                        else "Conecte a conta de anúncios para iniciar a sincronização."
                    )
                ),
            }
        source = last_receipt.source if last_receipt else None
        state = (
            "synced_official"
            if source == "provider_sync"
            else "synced_manual"
            if source == "manual"
            else "synced_demo"
        )
        return {
            "gate_open": gate_open,
            "connected": True,
            "provider": PAID_ACQUISITION_PROVIDER,
            "last_synced_at": last_receipt.received_at if last_receipt else None,
            "synced_campaigns": synced_campaigns,
            "synced_receipts": len(receipts),
            "last_receipt_source": source,
            "state": state,
            "message": (
                "Sincronização honesta a partir de snapshot local; nenhuma chamada real "
                "à Marketing API foi executada (gate fechado)."
                if not gate_open
                else "Última sincronização carregada da projeção do Ads Manager."
            ),
        }

    # ------------------------------------------------------------------
    # Campaign draft (governed by ExternalActionIntent + approval flow)
    # ------------------------------------------------------------------
    async def _resolve_channel_connection(
        self,
        workspace_id: UUID,
        channel: str,
        connection_id: UUID | None,
    ) -> OmnichannelChannelConnection:
        required_channel, requirement_label = _CHANNEL_REQUIREMENT[channel]
        query = select(OmnichannelChannelConnection).where(
            OmnichannelChannelConnection.workspace_id == workspace_id,
            OmnichannelChannelConnection.channel == required_channel,
        )
        if connection_id is not None:
            query = query.where(OmnichannelChannelConnection.id == connection_id)
        else:
            query = query.order_by(
                OmnichannelChannelConnection.is_default.desc(),
                OmnichannelChannelConnection.connected_at.desc().nullslast(),
            )
        candidates = list((await self.db.scalars(query)).all())
        active = [
            item
            for item in candidates
            if item.status not in _INACTIVE_CONNECTION_STATUSES
        ]
        if not active:
            raise PaidAcquisitionValidationError(
                f"Pré-requisito do canal não atendido: {requirement_label}. "
                "Conecte e ative o canal antes de preparar esta campanha."
            )
        return active[0]

    def _build_marketing_api_payload(
        self,
        *,
        payload: PrepareCampaignDraftRequest,
        connection: OmnichannelChannelConnection,
    ) -> dict[str, Any]:
        """Payload da Marketing API que SERIA enviado. Nunca é transmitido aqui."""
        destination_field = {
            "ctwa": "whatsapp_number",
            "ctm": "page_id",
            "ctd": "instagram_actor_id",
        }[payload.channel]
        return {
            "campaign": {
                "name": payload.campaign_name,
                "objective": {
                    "lead_generation": "OUTCOME_LEADS",
                    "conversations": "OUTCOME_ENGAGEMENT",
                    "traffic": "OUTCOME_TRAFFIC",
                    "conversions": "OUTCOME_SALES",
                }[payload.objective],
                "status": "PAUSED",
                "special_ad_categories": [],
            },
            "adset": {
                "daily_budget_minor_units": int(payload.daily_budget * 100),
                "currency": payload.currency,
                "destination_type": {
                    "ctwa": "WHATSAPP",
                    "ctm": "MESSENGER",
                    "ctd": "INSTAGRAM_DIRECT",
                }[payload.channel],
                destination_field: connection.external_account_id,
            },
            "creative": {
                "headline": payload.creative.headline,
                "primary_text": payload.creative.primary_text,
                "image_ref": payload.creative.image_ref,
                "call_to_action_type": payload.creative.call_to_action,
            },
            "messaging": {
                "welcome_message": payload.welcome_message,
                "ice_breakers": payload.ice_breakers,
            },
            "executionState": "intent_only",
            "externalEffects": 0,
        }

    async def prepare_campaign_draft(
        self,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        payload: PrepareCampaignDraftRequest,
    ) -> tuple[ExternalActionIntent, bool]:
        connection = await self._resolve_channel_connection(
            workspace_id, payload.channel, payload.connection_id
        )
        marketing_api_payload = self._build_marketing_api_payload(
            payload=payload, connection=connection
        )
        idempotency_key = "paid-acquisition:" + hashlib.sha256(
            f"{workspace_id}:{payload.idempotency_key}".encode("utf-8")
        ).hexdigest()[:48]

        existing = await self.db.scalar(
            select(ExternalActionIntent).where(
                ExternalActionIntent.workspace_id == workspace_id,
                ExternalActionIntent.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing, True

        intent = ExternalActionIntent(
            workspace_id=workspace_id,
            requested_by_user_id=actor_user_id,
            action_type=PAID_ACQUISITION_CAMPAIGN_CREATE,
            provider=PAID_ACQUISITION_PROVIDER,
            idempotency_key=idempotency_key,
            status=ExternalActionIntentStatus.awaiting_approval,
            payload={
                "channel": payload.channel,
                "channelLabel": CHANNEL_LABELS[payload.channel],
                "connectionId": str(connection.id),
                "connectionDisplayName": connection.display_name,
                "marketingApiPayload": marketing_api_payload,
            },
            reason=(
                "Rascunho de campanha de aquisição paga aguardando aprovação humana; "
                "nenhuma chamada à Marketing API foi executada."
            ),
        )
        try:
            async with self.db.begin_nested():
                self.db.add(intent)
                await self.db.flush()
        except IntegrityError:
            existing = await self.db.scalar(
                select(ExternalActionIntent).where(
                    ExternalActionIntent.workspace_id == workspace_id,
                    ExternalActionIntent.idempotency_key == idempotency_key,
                )
            )
            if existing is None:
                raise
            return existing, True
        return intent, False

    async def list_campaign_drafts(
        self, workspace_id: UUID, *, status: str | None = None
    ) -> list[ExternalActionIntent]:
        query = select(ExternalActionIntent).where(
            ExternalActionIntent.workspace_id == workspace_id,
            ExternalActionIntent.action_type == PAID_ACQUISITION_CAMPAIGN_CREATE,
        )
        if status:
            query = query.where(ExternalActionIntent.status == status)
        return list(
            (
                await self.db.scalars(
                    query.order_by(ExternalActionIntent.created_at.desc())
                )
            ).all()
        )

    async def get_campaign_draft(
        self, workspace_id: UUID, intent_id: UUID
    ) -> ExternalActionIntent:
        intent = await self.db.scalar(
            select(ExternalActionIntent).where(
                ExternalActionIntent.workspace_id == workspace_id,
                ExternalActionIntent.id == intent_id,
                ExternalActionIntent.action_type == PAID_ACQUISITION_CAMPAIGN_CREATE,
            )
        )
        if intent is None:
            raise PaidAcquisitionNotFoundError("Rascunho de campanha não encontrado.")
        return intent

    # ------------------------------------------------------------------
    # Funnel summary: spend (Ads Manager) x eventos atribuídos (AcquisitionEvent)
    # ------------------------------------------------------------------
    async def campaign_funnel_summary(
        self,
        workspace_id: UUID,
        *,
        date_from: date,
        date_to: date,
    ) -> dict[str, Any]:
        if date_to < date_from:
            raise ValueError("A data final deve ser igual ou posterior à inicial.")

        spend_summary = await self._ads_manager.summary(
            workspace_id,
            date_from=date_from,
            date_to=date_to,
            provider=PAID_ACQUISITION_PROVIDER,
        )
        spend_by_campaign: dict[str, dict[str, Any]] = {
            str(row["external_id"]): row for row in spend_summary["campaigns"]
        }

        # Selo de origem do spend por campanha, a partir do último receipt do
        # workspace para o provedor (mesmo padrão honesto usado no sync_status).
        last_receipt = await self.db.scalar(
            select(AdsManagerImportReceipt)
            .where(
                AdsManagerImportReceipt.workspace_id == workspace_id,
                AdsManagerImportReceipt.provider == PAID_ACQUISITION_PROVIDER,
            )
            .order_by(AdsManagerImportReceipt.received_at.desc())
        )
        spend_origin = "sem_dado"
        if last_receipt is not None:
            spend_origin = (
                "oficial"
                if last_receipt.source == "provider_sync"
                else "manual"
                if last_receipt.source == "manual"
                else "demonstracao"
            )

        entity_rows = list(
            (
                await self.db.scalars(
                    select(AdsManagerEntity).where(
                        AdsManagerEntity.workspace_id == workspace_id
                    )
                )
            ).all()
        )
        entity_names = {
            (row.provider, row.entity_type, row.external_id): row.display_name
            for row in entity_rows
        }

        start_at = datetime.combine(date_from, datetime.min.time())
        end_at = datetime.combine(date_to, datetime.max.time())
        events = list(
            (
                await self.db.scalars(
                    select(AcquisitionEvent).where(
                        AcquisitionEvent.workspace_id == workspace_id,
                        AcquisitionEvent.happened_at >= start_at,
                        AcquisitionEvent.happened_at <= end_at,
                    )
                )
            ).all()
        )

        buckets: dict[str, dict[str, Any]] = {}

        def bucket_for(key: str, name: str, campaign_external_id: str | None) -> dict[str, Any]:
            if key not in buckets:
                buckets[key] = {
                    "campaign_key": key,
                    "campaign_external_id": campaign_external_id,
                    "campaign_name": name,
                    "provider": PAID_ACQUISITION_PROVIDER,
                    "conversations": 0,
                    "qualified_leads": 0,
                    "contracts": 0,
                    "revenue": 0.0,
                }
            return buckets[key]

        for event in events:
            evidence = extract_attribution(event, entity_names=entity_names)
            if evidence.provider != PAID_ACQUISITION_PROVIDER:
                continue
            campaign_id = evidence.campaign_id
            key = campaign_id or "unattributed"
            name = evidence.campaign_name or (
                "Sem atribuição de campanha" if key == "unattributed" else key
            )
            row = bucket_for(key, name, campaign_id)
            normalized = _normalize_event_name(event.event_name)
            if normalized in _CONVERSATION_EVENTS:
                row["conversations"] += 1
            elif normalized in _QUALIFIED_EVENTS:
                row["qualified_leads"] += 1
            elif normalized in _CONTRACT_EVENTS:
                row["contracts"] += 1
            if normalized in _REVENUE_EVENTS and event.value:
                row["revenue"] += _decimal_to_float(event.value)

        # Garante que toda campanha com spend apareça mesmo sem eventos atribuídos.
        for external_id, spend_row in spend_by_campaign.items():
            bucket_for(external_id, spend_row["display_name"] or external_id, external_id)

        rows: list[dict[str, Any]] = []
        total_spend = 0.0
        total_conversations = 0
        total_qualified = 0
        total_contracts = 0
        total_revenue = 0.0
        currency = spend_summary.get("currency") or "BRL"
        for key, row in sorted(
            buckets.items(), key=lambda item: item[1]["revenue"], reverse=True
        ):
            spend_row = spend_by_campaign.get(row["campaign_external_id"] or "")
            spend = _decimal_to_float(spend_row["spend"]) if spend_row else 0.0
            row_currency = spend_row["currency"] if spend_row else currency
            origin = spend_origin if spend_row else "sem_dado"
            cpl = _safe_ratio(spend, row["conversations"])
            cac = _safe_ratio(spend, row["contracts"])
            roas = _safe_ratio(row["revenue"], spend)
            rows.append(
                {
                    **row,
                    "channel_hint": None,
                    "spend": round(spend, 2),
                    "spend_currency": row_currency,
                    "spend_origin": origin,
                    "revenue": round(row["revenue"], 2),
                    "cpl": cpl,
                    "cac": cac,
                    "roas": roas,
                }
            )
            total_spend += spend
            total_conversations += row["conversations"]
            total_qualified += row["qualified_leads"]
            total_contracts += row["contracts"]
            total_revenue += row["revenue"]

        totals = {
            "campaign_key": "__totals__",
            "campaign_external_id": None,
            "campaign_name": "Total",
            "provider": PAID_ACQUISITION_PROVIDER,
            "channel_hint": None,
            "spend": round(total_spend, 2),
            "spend_currency": currency,
            "spend_origin": spend_origin if spend_by_campaign else "sem_dado",
            "conversations": total_conversations,
            "qualified_leads": total_qualified,
            "contracts": total_contracts,
            "revenue": round(total_revenue, 2),
            "cpl": _safe_ratio(total_spend, total_conversations),
            "cac": _safe_ratio(total_spend, total_contracts),
            "roas": _safe_ratio(total_revenue, total_spend),
        }
        return {
            "date_from": date_from,
            "date_to": date_to,
            "rows": rows,
            "totals": totals,
        }


__all__ = [
    "PaidAcquisitionService",
    "PaidAcquisitionError",
    "PaidAcquisitionValidationError",
    "PaidAcquisitionNotFoundError",
    "PAID_ACQUISITION_CAMPAIGN_CREATE",
    "PAID_ACQUISITION_PROVIDER",
]
