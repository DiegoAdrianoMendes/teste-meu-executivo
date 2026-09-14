"""Local-first provider capabilities, social actions and automation simulation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import external_delivery_allowed
from app.db.post_commit import queue_realtime_event
from app.models.klm_operations import (
    KlmAuditEvent,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelMessage,
    OmnichannelThread,
)
from app.models.omnichannel_v6 import (
    OmnichannelActionIntent,
    OmnichannelActionIntentEvent,
    OmnichannelAutomationRule,
    OmnichannelAutomationRun,
    OmnichannelAutomationRunEvent,
    OmnichannelCallSession,
    OmnichannelChannelConnection,
    OmnichannelChannelEvent,
    OmnichannelEndpoint,
    OmnichannelIdentity,
    OmnichannelScheduledMessage,
    OmnichannelThreadBinding,
)
from app.schemas.omnichannel_v6 import (
    AutomationRuleCreate,
    AutomationRuleUpdate,
    AutomationSimulationRequest,
    ChannelConnectionCreate,
    ChannelConnectionUpdate,
    InternalMessageDeliveryReceiptIngest,
    OmnichannelActionIntentCreate,
)
from app.services.meta_policy_gate import clamp_service_window_hours
from app.services.omnichannel_service_window import latest_inbound_at_for_route
from app.services.workspace_time_service import (
    DEFAULT_WORKSPACE_TIMEZONE as DEFAULT_WORKSPACE_TIMEZONE,
)
from app.services.workspace_time_service import resolve_workspace_timezone

_SENSITIVE_CONFIG_KEY = re.compile(
    r"(?:access|refresh|api|private|client)?[_-]?(?:token|secret|password|key|signature|cookie)$",
    re.IGNORECASE,
)
_ACTION_CAPABILITY = {
    "public_reply": "public_reply",
    "private_reply": "private_reply",
    "comment_like": "comment_like",
    "comment_hide": "comment_hide",
    "comment_delete": "comment_delete",
    "send_message": "outbound_messages",
    "mark_answered": "local_state",
}
_SOCIAL_ACTIONS = frozenset(
    {"public_reply", "private_reply", "comment_like", "comment_hide", "comment_delete"}
)
_AUTOMATION_PROVIDER_ACTIONS = frozenset(_ACTION_CAPABILITY) - {"mark_answered"}
_AUTOMATION_LOCAL_ACTIONS = frozenset(
    {
        "add_tag",
        "remove_tag",
        "create_task",
        "transfer_thread",
        "update_card",
        "schedule_message",
        "start_flow",
        "stop_flow",
        "pause_ai",
        "resume_ai",
        "mark_answered",
        "notify_team",
        "request_human_approval",
        "wait",
    }
)
_AUTOMATION_ACTIONS = _AUTOMATION_PROVIDER_ACTIONS | _AUTOMATION_LOCAL_ACTIONS


def _now() -> datetime:
    return datetime.now(UTC)


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _capability(*, supported: bool, state: str, source: str, note: str) -> dict[str, Any]:
    return {
        "supported": supported,
        "enabled": supported,
        "state": state,
        "source": source,
        "note": note,
    }


def _contains_sensitive_key(value: Any, path: str = "configuration") -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_path = f"{path}.{key}"
            if _SENSITIVE_CONFIG_KEY.search(str(key)):
                return key_path
            found = _contains_sensitive_key(item, key_path)
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _contains_sensitive_key(item, f"{path}[{index}]")
            if found:
                return found
    return None


def _value_at(source: dict[str, Any], dotted_path: str) -> Any:
    current: Any = source
    for segment in dotted_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


class OmnichannelOrchestrationConflictError(ValueError):
    def __init__(self, message: str, *, current_version: int | None = None) -> None:
        super().__init__(message)
        self.current_version = current_version


@dataclass(frozen=True)
class ImmediateMessageDispatchResult:
    state: str
    external_actions_executed: int | None
    external_message_id: str | None = None
    error_code: str | None = None


class OmnichannelOrchestrationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _official_whatsapp_dispatcher(
        self,
        connection: OmnichannelChannelConnection,
    ):
        from app.services.whatsapp.omnichannel_dispatcher import (
            MetaCloudScheduledDispatcher,
        )

        return MetaCloudScheduledDispatcher(self.db, connection)

    async def dispatch_immediate_message(
        self,
        *,
        workspace_id: UUID,
        message_id: UUID,
    ) -> ImmediateMessageDispatchResult:
        """Checkpoint and dispatch one canonical composer message through Meta."""

        message = await self.db.scalar(
            select(OmnichannelMessage)
            .where(
                OmnichannelMessage.workspace_id == workspace_id,
                OmnichannelMessage.id == message_id,
            )
            .with_for_update()
        )
        if message is None:
            raise LookupError("Mensagem canônica não encontrada no workspace.")
        if (
            message.direction != MessageDirection.outbound
            or message.channel != "whatsapp"
        ):
            return ImmediateMessageDispatchResult("not_applicable", 0)

        metadata = dict(message.message_metadata or {})
        previous_attempt = metadata.get("immediateDispatch")
        previous_attempt = previous_attempt if isinstance(previous_attempt, dict) else {}
        previous_state = str(previous_attempt.get("state") or "")
        if message.external_message_id and message.delivery_status in {
            MessageDeliveryStatus.sent,
            MessageDeliveryStatus.delivered,
            MessageDeliveryStatus.read,
        }:
            return ImmediateMessageDispatchResult(
                "sent",
                1,
                external_message_id=message.external_message_id,
            )
        if previous_state in {"attempting", "delivery_uncertain"}:
            return ImmediateMessageDispatchResult(
                "delivery_uncertain",
                None,
                external_message_id=message.external_message_id,
                error_code=str(previous_attempt.get("errorCode") or "") or None,
            )
        if previous_state == "failed_known":
            return ImmediateMessageDispatchResult(
                "failed",
                0,
                error_code=str(previous_attempt.get("errorCode") or "") or None,
            )
        if not external_delivery_allowed():
            message.delivery_status = MessageDeliveryStatus.local_draft
            message.provider_status = "local_only"
            metadata["immediateDispatch"] = {
                "state": "local_only",
                "reason": "external_gate_closed",
                "externalActionsExecuted": 0,
                "updatedAt": _now().isoformat(),
            }
            message.message_metadata = metadata
            self.db.add(
                self._audit(
                    workspace_id=workspace_id,
                    user_id=message.author_user_id,
                    event_type="omnichannel_immediate_dispatch_skipped",
                    entity_type="omnichannel_message",
                    entity_id=message.id,
                    summary="Gate externo fechado antes da entrega; mensagem mantida local.",
                    after={"reason": "external_gate_closed", "externalActionsExecuted": 0},
                )
            )
            await self.db.flush()
            return ImmediateMessageDispatchResult("local_only", 0)

        thread = await self.db.scalar(
            select(OmnichannelThread).where(
                OmnichannelThread.workspace_id == workspace_id,
                OmnichannelThread.id == message.thread_id,
            )
        )
        endpoint = (
            await self.db.scalar(
                select(OmnichannelEndpoint).where(
                    OmnichannelEndpoint.workspace_id == workspace_id,
                    OmnichannelEndpoint.id == message.endpoint_id,
                )
            )
            if message.endpoint_id is not None
            else None
        )
        connection = (
            await self.db.scalar(
                select(OmnichannelChannelConnection).where(
                    OmnichannelChannelConnection.workspace_id == workspace_id,
                    OmnichannelChannelConnection.id == message.connection_id,
                )
            )
            if message.connection_id is not None
            else None
        )
        route_is_official = bool(
            thread is not None
            and endpoint is not None
            and connection is not None
            and connection.provider == "meta_cloud"
            and connection.channel == "whatsapp"
            and connection.transport_mode
            in {"cloud_api", "official_api", "coexistence"}
            and endpoint.connection_id in {None, connection.id}
            and endpoint.connection_key == connection.connection_key
        )
        if not route_is_official:
            message.delivery_status = MessageDeliveryStatus.local_draft
            metadata["immediateDispatch"] = {
                "state": "local_only",
                "reason": "official_route_unavailable",
                "externalActionsExecuted": 0,
                "updatedAt": _now().isoformat(),
            }
            message.message_metadata = metadata
            await self.db.flush()
            return ImmediateMessageDispatchResult(
                "local_only",
                0,
                error_code="official_route_unavailable",
            )

        assert thread is not None and endpoint is not None and connection is not None
        service_window_hours = clamp_service_window_hours(
            connection.channel, (connection.routing_policy or {}).get("serviceWindowHours", 24)
        )
        route_last_inbound_at = await latest_inbound_at_for_route(
            self.db,
            workspace_id=workspace_id,
            thread=thread,
            channel=message.channel,
            connection_id=connection.id,
        )
        service_window_expires_at = (
            route_last_inbound_at + timedelta(hours=service_window_hours)
            if route_last_inbound_at
            else None
        )
        structured_content = metadata.get("structuredContent")
        message_payload = (
            dict(structured_content)
            if isinstance(structured_content, dict) and structured_content
            else {"kind": "text", "body": message.body}
        )
        collaboration = metadata.get("collaboration")
        if isinstance(collaboration, dict):
            message_payload["collaboration"] = collaboration
        attachments = metadata.get("attachments")
        attachments = list(attachments) if isinstance(attachments, list) else []

        from app.services.omnichannel_delivery import (
            ScheduledDispatchError,
            ScheduledDispatchRequest,
        )

        dispatcher = self._official_whatsapp_dispatcher(connection)
        attempt_key = f"immediate:{message.id}"
        metadata["immediateDispatch"] = {
            "state": "attempting",
            "attemptKey": attempt_key,
            "attemptedAt": _now().isoformat(),
            "providerSupportsIdempotency": bool(dispatcher.supports_idempotency),
            "externalActionsExecuted": 0,
        }
        message.message_metadata = metadata
        message.delivery_status = MessageDeliveryStatus.queued
        message.provider_status = "dispatching"
        self.db.add(
            self._audit(
                workspace_id=workspace_id,
                user_id=message.author_user_id,
                event_type="omnichannel_immediate_dispatch_started",
                entity_type="omnichannel_message",
                entity_id=message.id,
                summary="Tentativa imediata registrada antes da chamada ao provedor.",
                after={
                    "connectionId": str(connection.id),
                    "provider": connection.provider,
                    "providerSupportsIdempotency": bool(dispatcher.supports_idempotency),
                },
            )
        )
        await self.db.flush()
        await self.db.commit()

        request = ScheduledDispatchRequest(
            workspace_id=workspace_id,
            scheduled_message_id=message.id,
            canonical_message_id=message.id,
            connection_id=connection.id,
            connection_key=connection.connection_key,
            provider=connection.provider,
            transport_mode=connection.transport_mode,
            channel=message.channel.value,
            endpoint_id=endpoint.id,
            external_user_id=endpoint.external_user_id,
            address=endpoint.address,
            body=message.body,
            template_id=message.template_id,
            attachments=attachments,
            message_payload=message_payload,
            idempotency_key=attempt_key,
            service_window_expires_at=service_window_expires_at,
        )
        receipt = None
        try:
            receipt = await dispatcher.dispatch(request)
            if receipt.provider != connection.provider:
                raise ScheduledDispatchError(
                    "O recibo imediato pertence a outro provedor.",
                    code="provider_receipt_mismatch",
                    retryable=False,
                    outcome_uncertain=True,
                )

            from app.services.omnichannel_v6_service import OmnichannelV6Service

            external_event_id = receipt.external_event_id or (
                f"{attempt_key}:accepted:{receipt.external_message_id}"
            )
            message, _, _, _ = await OmnichannelV6Service(
                self.db
            ).ingest_delivery_receipt(
                workspace_id,
                InternalMessageDeliveryReceiptIngest(
                    message_id=message.id,
                    provider=receipt.provider,
                    connection_key=connection.connection_key,
                    external_event_id=external_event_id,
                    external_message_id=receipt.external_message_id,
                    status=MessageDeliveryStatus.sent,
                    provider_status=receipt.provider_status,
                    occurred_at=receipt.accepted_at,
                    cost_amount=receipt.cost_amount,
                    cost_currency=receipt.cost_currency,
                    pricing_category=receipt.pricing_category,
                    provider_invoice_reference=receipt.provider_invoice_reference,
                    payload=receipt.payload,
                    metadata={"source": "canonical_immediate_composer"},
                ),
            )
            metadata = dict(message.message_metadata or {})
            metadata["immediateDispatch"] = {
                "state": "sent",
                "attemptKey": attempt_key,
                "externalActionsExecuted": 1,
                "externalMessageId": receipt.external_message_id,
                "acceptedAt": receipt.accepted_at.isoformat(),
            }
            message.message_metadata = metadata
            self.db.add(
                self._audit(
                    workspace_id=workspace_id,
                    user_id=message.author_user_id,
                    event_type="omnichannel_immediate_dispatch_accepted",
                    entity_type="omnichannel_message",
                    entity_id=message.id,
                    summary="Mensagem imediata aceita pelo provedor oficial.",
                    after={
                        "externalMessageId": receipt.external_message_id,
                        "provider": receipt.provider,
                    },
                )
            )
            await self.db.commit()
            await self.db.refresh(message)
            return ImmediateMessageDispatchResult(
                "sent",
                1,
                external_message_id=receipt.external_message_id,
            )
        except Exception as error:
            await self.db.rollback()
            message = await self.db.scalar(
                select(OmnichannelMessage)
                .where(
                    OmnichannelMessage.workspace_id == workspace_id,
                    OmnichannelMessage.id == message_id,
                )
                .with_for_update()
            )
            if message is None:
                raise
            outcome_uncertain = bool(getattr(error, "outcome_uncertain", False)) or not isinstance(
                error,
                ScheduledDispatchError,
            )
            error_code = str(getattr(error, "code", type(error).__name__))[:120]
            state = "delivery_uncertain" if outcome_uncertain else "failed_known"
            metadata = dict(message.message_metadata or {})
            metadata["immediateDispatch"] = {
                "state": state,
                "attemptKey": attempt_key,
                "errorCode": error_code,
                "providerSupportsIdempotency": bool(dispatcher.supports_idempotency),
                "externalActionsExecuted": None if outcome_uncertain else 0,
                "externalMessageId": (
                    receipt.external_message_id if receipt is not None else None
                ),
                "failedAt": _now().isoformat(),
            }
            message.message_metadata = metadata
            message.delivery_status = (
                MessageDeliveryStatus.queued
                if outcome_uncertain
                else MessageDeliveryStatus.failed
            )
            message.provider_status = state
            self.db.add(
                self._audit(
                    workspace_id=workspace_id,
                    user_id=message.author_user_id,
                    event_type=f"omnichannel_immediate_dispatch_{state}",
                    entity_type="omnichannel_message",
                    entity_id=message.id,
                    summary=(
                        "Entrega externa com resultado incerto; reconciliação necessária."
                        if outcome_uncertain
                        else "Tentativa externa falhou sem entrega confirmada."
                    ),
                    after={
                        "errorCode": error_code,
                        "externalActionsExecuted": None if outcome_uncertain else 0,
                    },
                )
            )
            await self.db.commit()
            await self.db.refresh(message)
            return ImmediateMessageDispatchResult(
                "delivery_uncertain" if outcome_uncertain else "failed",
                None if outcome_uncertain else 0,
                external_message_id=(
                    receipt.external_message_id if receipt is not None else None
                ),
                error_code=error_code,
            )

    @staticmethod
    def _connection_profile(payload: ChannelConnectionCreate) -> dict[str, dict[str, Any]]:
        source = f"{payload.provider}:{payload.transport_mode}"
        configurable = (
            "available_local"
            if payload.transport_mode == "local_fixture"
            else "configuration_required"
        )

        common_local = {
            "local_state": _capability(
                supported=True,
                state="available_local",
                source="klm_native",
                note="Ação interna, sem chamada ao provedor.",
            ),
            "audit": _capability(
                supported=True,
                state="available_local",
                source="klm_native",
                note="Registro append-only no workspace.",
            ),
        }
        channel = payload.channel.value
        if payload.provider in {"meta_ads", "tiktok_ads"}:
            capabilities = {
                "lead_forms": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Formulários instantâneos por webhook idempotente.",
                ),
                "ad_manager": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Campanhas, conjuntos, anúncios, gasto e criativos.",
                ),
                "ad_conversion": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Conversões server-side com event_id para deduplicação.",
                ),
                "audience_sync": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Audiências de remarketing sob aprovação explícita.",
                ),
                "proactive_handoff": _capability(
                    supported=True,
                    state="available_local",
                    source="klm_native",
                    note="Encaminha o lead para um endpoint oficial já configurado.",
                ),
            }
        elif payload.provider == "qr_gateway":
            capabilities = {
                "inbound_messages": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Compatibilidade preservada pelo adaptador local do gateway.",
                ),
                "outbound_messages": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Compatibilidade preservada; não é Cloud API oficial.",
                ),
                "scheduled_messages": _capability(
                    supported=False,
                    state="unsupported",
                    source=source,
                    note="Agendamentos externos exigem uma rota oficial no núcleo nativo.",
                ),
                "media": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Mídia condicionada às capacidades declaradas pelo gateway.",
                ),
                "templates": _capability(
                    supported=False,
                    state="unsupported",
                    source=source,
                    note="Modelos oficiais pertencem à Cloud API da Meta.",
                ),
                "flows": _capability(
                    supported=False,
                    state="unsupported",
                    source=source,
                    note="WhatsApp Flows exige conexão oficial elegível.",
                ),
                "voice_calls": _capability(
                    supported=False,
                    state="unsupported",
                    source=source,
                    note="Calling API exige conexão oficial elegível.",
                ),
                "business_app_simultaneous": _capability(
                    supported=False,
                    state="unsupported",
                    source=source,
                    note="Coexistence é um modo oficial, distinto de sessão QR.",
                ),
                "history_sync": _capability(
                    supported=False,
                    state="unsupported",
                    source=source,
                    note="History Sync oficial não é presumido para gateways QR.",
                ),
                "cost_tracking": _capability(
                    supported=True,
                    state="available_local",
                    source="klm_native",
                    note="Ledger local preserva custo informado pelo adaptador.",
                ),
            }
        elif channel == "whatsapp":
            capabilities = {
                "inbound_messages": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Webhook oficial da Cloud API.",
                ),
                "outbound_messages": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Envio oficial sujeito a janela e modelo.",
                ),
                "scheduled_messages": _capability(
                    supported=True,
                    state="available_local",
                    source="klm_native",
                    note="Agendamento persistente local.",
                ),
                "templates": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Modelos aprovados pelo provedor.",
                ),
                "media": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Mídia oficial com armazenamento KLM.",
                ),
                "flows": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="WhatsApp Flows quando elegível.",
                ),
                "voice_calls": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Calling API exige elegibilidade e permissão.",
                ),
                "business_app_simultaneous": _capability(
                    supported=payload.transport_mode == "coexistence",
                    state=configurable
                    if payload.transport_mode == "coexistence"
                    else "unsupported",
                    source=source,
                    note="Disponível somente pelo onboarding oficial Coexistence.",
                ),
                "history_sync": _capability(
                    supported=payload.transport_mode == "coexistence",
                    state=configurable
                    if payload.transport_mode == "coexistence"
                    else "unsupported",
                    source=source,
                    note="Sincronização depende do fluxo oficial e limites da Meta.",
                ),
                "cost_tracking": _capability(
                    supported=True,
                    state="available_local",
                    source="klm_native",
                    note="Ledger por mensagem, categoria e número.",
                ),
            }
        elif channel == "instagram":
            capabilities = {
                "inbound_messages": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Mensagens e eventos por webhook.",
                ),
                "outbound_messages": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="DM oficial dentro das regras da plataforma.",
                ),
                "public_reply": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Resposta pública ao comentário.",
                ),
                "private_reply": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Resposta privada vinculada ao comentário quando permitida.",
                ),
                "comment_like": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Curtida condicionada ao escopo autorizado.",
                ),
                "comment_hide": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Moderação condicionada ao escopo autorizado.",
                ),
                "comment_delete": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Exclusão condicionada ao escopo autorizado.",
                ),
                "story_events": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Menções e respostas de stories.",
                ),
                "follower_check": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note=(
                        "Consulta is_user_follow_business após interação com consentimento; "
                        "dado ausente nunca é inferido como não seguidor."
                    ),
                ),
                "new_follower_webhook": _capability(
                    supported=False,
                    state="unsupported",
                    source=source,
                    note=(
                        "O webhook público padrão não é presumido. Só pode ser habilitado "
                        "se a conexão aprovada declarar essa capacidade."
                    ),
                ),
            }
        elif channel in {"messenger", "facebook"}:
            capabilities = {
                "inbound_messages": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Mensagens e eventos de Página.",
                ),
                "outbound_messages": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Mensagem oficial da Página.",
                ),
                "public_reply": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Resposta pública ao comentário.",
                ),
                "private_reply": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Resposta privada quando autorizada.",
                ),
                "comment_like": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Reação condicionada ao escopo.",
                ),
                "comment_hide": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Moderação condicionada ao escopo.",
                ),
                "comment_delete": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Exclusão condicionada ao escopo.",
                ),
                "lead_forms": _capability(
                    supported=True, state=configurable, source=source, note="Lead Ads via webhook."
                ),
            }
        elif channel == "tiktok":
            capabilities = {
                "inbound_messages": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Business Messaging quando elegível.",
                ),
                "outbound_messages": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Resposta por Business Messaging.",
                ),
                "public_reply": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Resposta ao comentário conforme API disponível.",
                ),
                "private_reply": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="DM conforme janela e elegibilidade.",
                ),
                "comment_like": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Curtida conforme API e escopos.",
                ),
                "comment_hide": _capability(
                    supported=False,
                    state="unsupported",
                    source=source,
                    note="Não presumido sem contrato oficial confirmado.",
                ),
                "comment_delete": _capability(
                    supported=False,
                    state="unsupported",
                    source=source,
                    note="Não presumido sem contrato oficial confirmado.",
                ),
                "lead_forms": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Instant Forms e webhook de CRM.",
                ),
                "ad_conversion": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Sinal de conversão com deduplicação.",
                ),
            }
        elif channel == "email":
            capabilities = {
                "inbound_messages": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Ingestão e anexos no RAG do caso.",
                ),
                "outbound_messages": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Envio após configuração do provedor.",
                ),
                "scheduled_messages": _capability(
                    supported=True,
                    state="available_local",
                    source="klm_native",
                    note="Agendamento persistente local.",
                ),
            }
        else:
            capabilities = {
                "lead_forms": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Formulário assinado e idempotente.",
                ),
                "webhook": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Entrada versionada com receipt.",
                ),
                "ad_conversion": _capability(
                    supported=True,
                    state=configurable,
                    source=source,
                    note="Atribuição e conversão deduplicadas.",
                ),
            }
        return {**common_local, **capabilities}

    @staticmethod
    def _required_configuration(connection: OmnichannelChannelConnection) -> list[str]:
        if connection.transport_mode == "local_fixture":
            return []
        requirements = {
            "meta_cloud": ["phoneNumberId", "wabaId", "businessAccountId"],
            "meta_ads": ["businessAccountId", "adAccountId", "pixelId"],
            "meta_instagram": ["pageId", "instagramAccountId"],
            "meta_messenger": ["pageId"],
            "tiktok_business": ["businessAccountId", "advertiserId"],
            "tiktok_ads": ["businessAccountId", "advertiserId", "pixelCode"],
            "google_mail": ["accountAddress"],
            "microsoft_graph": ["accountAddress"],
            "imap_smtp": ["accountAddress", "imapHost", "smtpHost"],
            "site": ["sourceKey"],
            "lovable": ["sourceKey"],
            "local_fixture": [],
        }
        required = requirements.get(connection.provider, [])
        return [key for key in required if not connection.configuration.get(key)]

    @staticmethod
    def _apply_capability_preferences(
        profile: dict[str, dict[str, Any]], preferences: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        result = {key: dict(value) for key, value in profile.items()}
        unknown = sorted(set(preferences) - set(result))
        if unknown:
            raise ValueError(f"Capacidade desconhecida para este canal: {', '.join(unknown)}")
        for key, preference in preferences.items():
            requested = preference.get("enabled") if isinstance(preference, dict) else preference
            result[key]["enabled"] = bool(requested) and bool(result[key]["supported"])
        return result

    @staticmethod
    def _audit(
        *,
        workspace_id: UUID,
        user_id: UUID | None,
        event_type: str,
        entity_type: str,
        entity_id: UUID,
        summary: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> KlmAuditEvent:
        return KlmAuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human" if user_id else "system",
            actor_label="Usuário KLM" if user_id else "KLM Runtime",
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=summary,
            before_data=before,
            after_data=after,
        )

    async def list_connections(
        self, workspace_id: UUID, *, channel: str | None = None
    ) -> list[OmnichannelChannelConnection]:
        statement = select(OmnichannelChannelConnection).where(
            OmnichannelChannelConnection.workspace_id == workspace_id
        )
        if channel:
            statement = statement.where(OmnichannelChannelConnection.channel == channel)
        result = await self.db.execute(
            statement.order_by(
                OmnichannelChannelConnection.channel,
                OmnichannelChannelConnection.is_default.desc(),
                OmnichannelChannelConnection.display_name,
            )
        )
        return list(result.scalars().all())

    async def thread_context(self, workspace_id: UUID, thread_id: UUID) -> dict[str, Any]:
        """Load the inbox's canonical read model with a bounded set of queries."""
        thread = await self.db.scalar(
            select(OmnichannelThread).where(
                OmnichannelThread.workspace_id == workspace_id,
                OmnichannelThread.id == thread_id,
            )
        )
        if not thread:
            raise LookupError("Conversa canônica não encontrada.")

        identity = None
        if thread.identity_id:
            identity = await self.db.scalar(
                select(OmnichannelIdentity).where(
                    OmnichannelIdentity.workspace_id == workspace_id,
                    OmnichannelIdentity.id == thread.identity_id,
                )
            )

        bindings_result = await self.db.execute(
            select(OmnichannelThreadBinding)
            .where(
                OmnichannelThreadBinding.workspace_id == workspace_id,
                OmnichannelThreadBinding.thread_id == thread_id,
            )
            .order_by(OmnichannelThreadBinding.updated_at.desc())
        )
        bindings = list(bindings_result.scalars().all())
        endpoint_ids = {row.endpoint_id for row in bindings if row.endpoint_id}
        endpoints: list[OmnichannelEndpoint] = []
        if identity or endpoint_ids:
            endpoint_filters = []
            if identity:
                endpoint_filters.append(OmnichannelEndpoint.identity_id == identity.id)
            if endpoint_ids:
                endpoint_filters.append(OmnichannelEndpoint.id.in_(endpoint_ids))
            endpoints_result = await self.db.execute(
                select(OmnichannelEndpoint)
                .where(
                    OmnichannelEndpoint.workspace_id == workspace_id,
                    or_(*endpoint_filters),
                )
                .order_by(OmnichannelEndpoint.last_seen_at.desc().nullslast())
            )
            endpoints = list(endpoints_result.scalars().all())

        connection_ids = {
            row.connection_id
            for row in [*bindings, *endpoints]
            if getattr(row, "connection_id", None)
        }
        connection_keys = {
            row.connection_key for row in [*bindings, *endpoints] if row.connection_key
        }
        connections: list[OmnichannelChannelConnection] = []
        if connection_ids or connection_keys:
            filters = []
            if connection_ids:
                filters.append(OmnichannelChannelConnection.id.in_(connection_ids))
            if connection_keys:
                filters.append(OmnichannelChannelConnection.connection_key.in_(connection_keys))
            connection_result = await self.db.execute(
                select(OmnichannelChannelConnection)
                .where(
                    OmnichannelChannelConnection.workspace_id == workspace_id,
                    or_(*filters),
                )
                .order_by(OmnichannelChannelConnection.is_default.desc())
            )
            connections = list(connection_result.scalars().all())

        action_result = await self.db.execute(
            select(OmnichannelActionIntent)
            .where(
                OmnichannelActionIntent.workspace_id == workspace_id,
                OmnichannelActionIntent.thread_id == thread_id,
            )
            .order_by(OmnichannelActionIntent.created_at.desc())
            .limit(100)
        )
        scheduled_result = await self.db.execute(
            select(OmnichannelScheduledMessage)
            .where(
                OmnichannelScheduledMessage.workspace_id == workspace_id,
                OmnichannelScheduledMessage.thread_id == thread_id,
            )
            .order_by(OmnichannelScheduledMessage.scheduled_at.desc())
            .limit(100)
        )
        call_result = await self.db.execute(
            select(OmnichannelCallSession)
            .where(
                OmnichannelCallSession.workspace_id == workspace_id,
                OmnichannelCallSession.thread_id == thread_id,
            )
            .order_by(OmnichannelCallSession.requested_at.desc())
            .limit(100)
        )
        run_result = await self.db.execute(
            select(OmnichannelAutomationRun, OmnichannelAutomationRule)
            .join(
                OmnichannelAutomationRule,
                (OmnichannelAutomationRule.workspace_id == OmnichannelAutomationRun.workspace_id)
                & (OmnichannelAutomationRule.id == OmnichannelAutomationRun.rule_id),
            )
            .where(
                OmnichannelAutomationRun.workspace_id == workspace_id,
                OmnichannelAutomationRun.thread_id == thread_id,
            )
            .order_by(OmnichannelAutomationRun.started_at.desc())
            .limit(50)
        )

        from app.services.omnichannel_v6_service import OmnichannelV6Service

        timeline = await OmnichannelV6Service(self.db).timeline(workspace_id, thread_id, limit=300)
        connection_by_id = {row.id: row for row in connections}
        connection_by_key = {row.connection_key: row for row in connections}
        binding_counts: dict[UUID, int] = {}
        binding_by_endpoint: dict[UUID, OmnichannelThreadBinding] = {}
        for binding in bindings:
            if binding.endpoint_id:
                binding_counts[binding.endpoint_id] = binding_counts.get(binding.endpoint_id, 0) + 1
                binding_by_endpoint.setdefault(binding.endpoint_id, binding)
        channels = []
        for endpoint in endpoints:
            connection = connection_by_id.get(endpoint.connection_id) or connection_by_key.get(
                endpoint.connection_key
            )
            channels.append(
                {
                    "endpoint_id": endpoint.id,
                    "binding_id": (
                        binding_by_endpoint[endpoint.id].id
                        if endpoint.id in binding_by_endpoint
                        else None
                    ),
                    "channel": endpoint.channel,
                    "provider": endpoint.provider,
                    "connection_id": connection.id if connection else endpoint.connection_id,
                    "connection_key": endpoint.connection_key,
                    "connection_name": connection.display_name if connection else None,
                    "connection_status": connection.status if connection else None,
                    "transport_mode": connection.transport_mode if connection else None,
                    "external_account_id": endpoint.external_account_id,
                    "address": endpoint.address,
                    "external_user_id": endpoint.external_user_id,
                    "consent_state": endpoint.consent_state,
                    "blocked_at": endpoint.blocked_at,
                    "last_seen_at": endpoint.last_seen_at,
                    "binding_count": binding_counts.get(endpoint.id, 0),
                    "capabilities": endpoint.capabilities,
                }
            )

        automation_runs = []
        for run, rule in run_result.all():
            automation_runs.append(
                {
                    "id": run.id,
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "state": run.state,
                    "current_action": run.current_action,
                    "matched": bool(run.result.get("matched")),
                    "prepared_actions": int(run.result.get("preparedActions") or 0),
                    "coexist_with_ai": bool(run.result.get("coexistWithAi")),
                    "started_at": run.started_at,
                    "completed_at": run.completed_at,
                }
            )
        return {
            "thread": thread,
            "identity": identity,
            "channels": channels,
            "bindings": bindings,
            "action_intents": list(action_result.scalars().all()),
            "scheduled_messages": list(scheduled_result.scalars().all()),
            "calls": list(call_result.scalars().all()),
            "automation_runs": automation_runs,
            "timeline": timeline,
            "external_gate_open": external_delivery_allowed(),
            "generated_at": _now(),
        }

    async def create_connection(
        self,
        workspace_id: UUID,
        user_id: UUID,
        payload: ChannelConnectionCreate,
    ) -> OmnichannelChannelConnection:
        sensitive_path = _contains_sensitive_key(payload.configuration)
        if sensitive_path:
            raise ValueError(
                "Segredo não pode ser salvo na configuração "
                f"({sensitive_path}); use credentialReference."
            )
        if payload.transport_mode == "coexistence" and payload.channel.value != "whatsapp":
            raise ValueError("Coexistence é um modo oficial exclusivo do WhatsApp Business App.")
        if payload.transport_mode == "coexistence" and payload.provider != "meta_cloud":
            raise ValueError("Coexistence exige o provedor oficial meta_cloud.")
        existing = await self.db.scalar(
            select(OmnichannelChannelConnection).where(
                OmnichannelChannelConnection.workspace_id == workspace_id,
                OmnichannelChannelConnection.provider == payload.provider,
                OmnichannelChannelConnection.connection_key == payload.connection_key,
            )
        )
        if existing:
            raise OmnichannelOrchestrationConflictError("Esta conexão já está cadastrada.")
        profile = self._connection_profile(payload)
        capabilities = self._apply_capability_preferences(profile, payload.capabilities)
        if payload.is_default:
            await self._clear_default(workspace_id, payload.channel.value)
        connection = OmnichannelChannelConnection(
            workspace_id=workspace_id,
            channel=payload.channel.value,
            provider=payload.provider,
            connection_key=payload.connection_key,
            display_name=payload.display_name.strip(),
            external_account_id=payload.external_account_id,
            transport_mode=payload.transport_mode,
            status="configured_local",
            is_default=payload.is_default,
            capabilities=capabilities,
            limits=payload.limits,
            routing_policy=payload.routing_policy,
            cost_policy={},
            health={"state": "not_verified", "externalChecks": 0},
            configuration=payload.configuration,
            credential_reference=payload.credential_reference,
            webhook_state="not_configured",
        )
        self.db.add(connection)
        try:
            await self.db.flush()
        except IntegrityError as error:
            raise OmnichannelOrchestrationConflictError("Conexão duplicada.") from error
        self.db.add(
            self._audit(
                workspace_id=workspace_id,
                user_id=user_id,
                event_type="omnichannel_connection_configured_local",
                entity_type="omnichannel_connection",
                entity_id=connection.id,
                summary=(
                    f"Conexão local {payload.channel.value}/{payload.transport_mode} cadastrada."
                ),
                after={"provider": payload.provider, "transportMode": payload.transport_mode},
            )
        )
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="omnichannel.connection.updated",
            data={"connectionId": str(connection.id), "status": connection.status},
        )
        await self.db.refresh(connection)
        return connection

    async def _clear_default(self, workspace_id: UUID, channel: str) -> None:
        result = await self.db.execute(
            select(OmnichannelChannelConnection).where(
                OmnichannelChannelConnection.workspace_id == workspace_id,
                OmnichannelChannelConnection.channel == channel,
                OmnichannelChannelConnection.is_default.is_(True),
            )
        )
        for connection in result.scalars().all():
            connection.is_default = False
            connection.version += 1

    async def get_connection(
        self, workspace_id: UUID, connection_id: UUID
    ) -> OmnichannelChannelConnection:
        connection = await self.db.scalar(
            select(OmnichannelChannelConnection).where(
                OmnichannelChannelConnection.workspace_id == workspace_id,
                OmnichannelChannelConnection.id == connection_id,
            )
        )
        if not connection:
            raise LookupError("Conexão omnichannel não encontrada.")
        return connection

    async def update_connection(
        self,
        workspace_id: UUID,
        connection_id: UUID,
        user_id: UUID,
        payload: ChannelConnectionUpdate,
    ) -> OmnichannelChannelConnection:
        connection = await self.get_connection(workspace_id, connection_id)
        if connection.version != payload.expected_version:
            raise OmnichannelOrchestrationConflictError(
                "A conexão foi alterada por outro usuário.", current_version=connection.version
            )
        update = payload.model_dump(exclude_unset=True, exclude={"expected_version", "action"})
        if "configuration" in update:
            sensitive_path = _contains_sensitive_key(update["configuration"])
            if sensitive_path:
                raise ValueError(
                    "Segredo não pode ser salvo na configuração "
                    f"({sensitive_path}); use credentialReference."
                )
        before = {"status": connection.status, "version": connection.version}
        if update.get("is_default"):
            await self._clear_default(workspace_id, connection.channel)
        for field, value in update.items():
            if field == "capabilities" and value is not None:
                current_profile = {key: dict(item) for key, item in connection.capabilities.items()}
                value = self._apply_capability_preferences(current_profile, value)
            setattr(connection, field, value)
        if payload.action == "pause":
            connection.status = "paused"
        elif payload.action == "resume":
            connection.status = "configured_local"
        elif payload.action == "disconnect_local":
            connection.status = "disconnected_local"
            connection.webhook_state = "not_configured"
            connection.connected_at = None
        connection.version += 1
        await self.db.flush()
        self.db.add(
            self._audit(
                workspace_id=workspace_id,
                user_id=user_id,
                event_type="omnichannel_connection_updated",
                entity_type="omnichannel_connection",
                entity_id=connection.id,
                summary="Configuração local do canal atualizada.",
                before=before,
                after={"status": connection.status, "version": connection.version},
            )
        )
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="omnichannel.connection.updated",
            data={"connectionId": str(connection.id), "status": connection.status},
        )
        await self.db.refresh(connection)
        return connection

    async def validate_connection_local(
        self, workspace_id: UUID, connection_id: UUID
    ) -> dict[str, Any]:
        connection = await self.get_connection(workspace_id, connection_id)
        missing = self._required_configuration(connection)
        supported: list[str] = []
        configuration_required: list[str] = []
        unsupported: list[str] = []
        for key, item in connection.capabilities.items():
            if not item.get("supported"):
                unsupported.append(key)
            elif item.get("state") == "available_local":
                supported.append(key)
            else:
                configuration_required.append(key)
        connection.health = {
            "state": "local_configuration_complete" if not missing else "configuration_incomplete",
            "missingConfiguration": missing,
            "externalChecks": 0,
            "checkedAt": _now().isoformat(),
        }
        connection.last_synced_at = _now()
        await self.db.flush()
        await self.db.refresh(connection)
        return {
            "connection": connection,
            "ready_for_external_setup": not missing,
            "missing_configuration": missing,
            "supported_capabilities": sorted(supported),
            "configuration_required": sorted(configuration_required),
            "unsupported_capabilities": sorted(unsupported),
            "external_gate_open": external_delivery_allowed(),
        }

    async def create_action_intent(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        user_id: UUID | None,
        payload: OmnichannelActionIntentCreate,
    ) -> tuple[OmnichannelActionIntent, bool]:
        thread = await self.db.scalar(
            select(OmnichannelThread).where(
                OmnichannelThread.workspace_id == workspace_id,
                OmnichannelThread.id == thread_id,
            )
        )
        if not thread:
            raise LookupError("Conversa canônica não encontrada.")
        event = None
        if payload.event_id:
            event = await self.db.scalar(
                select(OmnichannelChannelEvent).where(
                    OmnichannelChannelEvent.workspace_id == workspace_id,
                    OmnichannelChannelEvent.id == payload.event_id,
                    OmnichannelChannelEvent.thread_id == thread_id,
                )
            )
            if not event:
                raise LookupError("Evento social não pertence a esta conversa.")
        if payload.action_type in _SOCIAL_ACTIONS and (not event or event.visibility != "public"):
            raise ValueError("Ação de comentário exige um evento público desta conversa.")
        connection = None
        if payload.connection_id:
            connection = await self.get_connection(workspace_id, payload.connection_id)
        required_capability = _ACTION_CAPABILITY[payload.action_type]
        if payload.action_type != "mark_answered" and not connection:
            raise ValueError("Selecione a conexão oficial usada por esta ação.")
        capability_state = "available_local"
        if connection:
            capability = connection.capabilities.get(required_capability)
            if not capability:
                raise ValueError("A conexão não declara a capacidade necessária.")
            if not capability.get("supported") or not capability.get("enabled"):
                raise ValueError("A capacidade está desativada ou não é suportada pela conexão.")
            capability_state = str(capability.get("state") or "configuration_required")
        payload_dump = payload.model_dump(mode="json", by_alias=True)
        payload_hash = _hash({"threadId": str(thread_id), **payload_dump})
        existing = await self.db.scalar(
            select(OmnichannelActionIntent).where(
                OmnichannelActionIntent.workspace_id == workspace_id,
                OmnichannelActionIntent.idempotency_key == payload.idempotency_key,
            )
        )
        if existing:
            if existing.payload_sha256 != payload_hash:
                raise OmnichannelOrchestrationConflictError(
                    "A chave de idempotência já foi usada com outra ação."
                )
            return existing, True
        if payload.action_type == "mark_answered":
            state = "applied_local"
        elif connection and connection.transport_mode == "local_fixture":
            state = "simulated_local"
        elif (
            connection
            and external_delivery_allowed()
            and capability_state in {"available_external", "connected", "ready"}
        ):
            state = "queued"
        else:
            state = "blocked_external"
        intent = OmnichannelActionIntent(
            workspace_id=workspace_id,
            thread_id=thread_id,
            event_id=event.id if event else None,
            connection_id=connection.id if connection else None,
            requested_by_user_id=user_id,
            action_type=payload.action_type,
            target_external_id=payload.target_external_id,
            body=(payload.body or "").strip() or None,
            state=state,
            required_capability=required_capability,
            capability_state=capability_state,
            idempotency_key=payload.idempotency_key,
            payload_sha256=payload_hash,
            scheduled_at=payload.scheduled_at,
            action_payload=payload.payload,
            provider_receipt={
                "externalRequests": 0,
                "localOnly": state != "queued",
            },
            failure_reason=(
                "Ação externa bloqueada até aprovação das APIs e do gate online."
                if state == "blocked_external"
                else None
            ),
        )
        self.db.add(intent)
        try:
            await self.db.flush()
        except IntegrityError as error:
            raise OmnichannelOrchestrationConflictError("Ação concorrente duplicada.") from error
        self.db.add(
            OmnichannelActionIntentEvent(
                workspace_id=workspace_id,
                action_intent_id=intent.id,
                thread_id=thread_id,
                connection_id=connection.id if connection else None,
                provider=connection.provider if connection else "local",
                action_type=payload.action_type,
                event_type="created",
                previous_state="none",
                next_state=state,
                idempotency_key=f"action-created:{intent.id}",
                payload_sha256=payload_hash,
                occurred_at=_now(),
                event_metadata={
                    "requiredCapability": required_capability,
                    "capabilityState": capability_state,
                    "externalRequests": 0,
                },
            )
        )
        self.db.add(
            self._audit(
                workspace_id=workspace_id,
                user_id=user_id,
                event_type="omnichannel_action_prepared_local",
                entity_type="omnichannel_action_intent",
                entity_id=intent.id,
                summary=f"Ação {payload.action_type} preparada sem execução externa.",
                after={"state": state, "requiredCapability": required_capability},
            )
        )
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="omnichannel.action.updated",
            data={"intentId": str(intent.id), "threadId": str(thread_id), "state": state},
        )
        await self.db.refresh(intent)
        return intent, False

    async def list_action_intent_events(
        self,
        workspace_id: UUID,
        intent_id: UUID,
    ) -> list[OmnichannelActionIntentEvent]:
        intent = await self.db.scalar(
            select(OmnichannelActionIntent.id).where(
                OmnichannelActionIntent.workspace_id == workspace_id,
                OmnichannelActionIntent.id == intent_id,
            )
        )
        if intent is None:
            raise LookupError("Ação omnichannel não encontrada.")
        return list(
            (
                await self.db.scalars(
                    select(OmnichannelActionIntentEvent)
                    .where(
                        OmnichannelActionIntentEvent.workspace_id == workspace_id,
                        OmnichannelActionIntentEvent.action_intent_id == intent_id,
                    )
                    .order_by(
                        OmnichannelActionIntentEvent.occurred_at,
                        OmnichannelActionIntentEvent.received_at,
                        OmnichannelActionIntentEvent.id,
                    )
                )
            ).all()
        )

    async def list_automation_rules(self, workspace_id: UUID) -> list[OmnichannelAutomationRule]:
        result = await self.db.execute(
            select(OmnichannelAutomationRule)
            .where(OmnichannelAutomationRule.workspace_id == workspace_id)
            .order_by(OmnichannelAutomationRule.priority, OmnichannelAutomationRule.name)
        )
        return list(result.scalars().all())

    async def create_automation_rule(
        self,
        workspace_id: UUID,
        user_id: UUID,
        payload: AutomationRuleCreate,
    ) -> OmnichannelAutomationRule:
        self._validate_actions(payload.actions)
        existing = await self.db.scalar(
            select(OmnichannelAutomationRule).where(
                OmnichannelAutomationRule.workspace_id == workspace_id,
                OmnichannelAutomationRule.slug == payload.slug,
            )
        )
        if existing:
            raise OmnichannelOrchestrationConflictError("Já existe um flow com este slug.")
        rule = OmnichannelAutomationRule(
            workspace_id=workspace_id,
            created_by_user_id=user_id,
            name=payload.name.strip(),
            slug=payload.slug,
            description=payload.description,
            channel=payload.channel.value if payload.channel else None,
            trigger_type=payload.trigger_type,
            status=payload.status,
            priority=payload.priority,
            coexist_with_ai=payload.coexist_with_ai,
            stop_on_match=payload.stop_on_match,
            trigger_config=payload.trigger_config,
            conditions=payload.conditions,
            actions=payload.actions,
            quiet_hours=payload.quiet_hours,
            rate_limits=payload.rate_limits,
        )
        self.db.add(rule)
        try:
            await self.db.flush()
        except IntegrityError as error:
            raise OmnichannelOrchestrationConflictError("Flow concorrente duplicado.") from error
        self.db.add(
            self._audit(
                workspace_id=workspace_id,
                user_id=user_id,
                event_type="omnichannel_automation_rule_created",
                entity_type="omnichannel_automation_rule",
                entity_id=rule.id,
                summary=f"Flow {rule.name} criado com execução externa bloqueada.",
                after={"status": rule.status, "triggerType": rule.trigger_type},
            )
        )
        await self.db.refresh(rule)
        return rule

    async def update_automation_rule(
        self,
        workspace_id: UUID,
        rule_id: UUID,
        user_id: UUID,
        payload: AutomationRuleUpdate,
    ) -> OmnichannelAutomationRule:
        rule = await self._get_rule(workspace_id, rule_id)
        if rule.version != payload.expected_version:
            raise OmnichannelOrchestrationConflictError(
                "O flow foi alterado por outro usuário.", current_version=rule.version
            )
        update = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
        if "actions" in update and update["actions"] is not None:
            self._validate_actions(update["actions"])
        before = {"status": rule.status, "version": rule.version}
        for field, value in update.items():
            setattr(rule, field, value)
        rule.version += 1
        await self.db.flush()
        self.db.add(
            self._audit(
                workspace_id=workspace_id,
                user_id=user_id,
                event_type="omnichannel_automation_rule_updated",
                entity_type="omnichannel_automation_rule",
                entity_id=rule.id,
                summary=f"Flow {rule.name} atualizado.",
                before=before,
                after={"status": rule.status, "version": rule.version},
            )
        )
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="omnichannel.automation_rule.updated",
            data={"ruleId": str(rule.id), "status": rule.status, "version": rule.version},
        )
        await self.db.refresh(rule)
        return rule

    async def _get_rule(self, workspace_id: UUID, rule_id: UUID) -> OmnichannelAutomationRule:
        rule = await self.db.scalar(
            select(OmnichannelAutomationRule).where(
                OmnichannelAutomationRule.workspace_id == workspace_id,
                OmnichannelAutomationRule.id == rule_id,
            )
        )
        if not rule:
            raise LookupError("Flow omnichannel não encontrado.")
        return rule

    @staticmethod
    def _validate_actions(actions: list[dict[str, Any]]) -> None:
        if not actions:
            raise ValueError("O flow precisa ter ao menos uma ação.")
        for index, action in enumerate(actions):
            action_type = str(action.get("type") or "").strip()
            if not action_type:
                raise ValueError(f"A ação {index + 1} não possui type.")
            if action_type not in _AUTOMATION_ACTIONS:
                raise ValueError(f"A ação {index + 1} usa um tipo não suportado: {action_type}.")
            if action_type in {"public_reply", "private_reply", "send_message"}:
                if not str(action.get("body") or "").strip():
                    raise ValueError(f"A ação {index + 1} precisa de body.")
            delay = action.get("delaySeconds", 0)
            if not isinstance(delay, int) or delay < 0 or delay > 2_592_000:
                raise ValueError(f"A ação {index + 1} possui delaySeconds inválido.")

    @staticmethod
    def _matches_condition(context: dict[str, Any], condition: dict[str, Any]) -> bool:
        field = str(condition.get("field") or "")
        operator = str(condition.get("operator") or "equals")
        expected = condition.get("value")
        actual = _value_at(context, field)
        if operator == "exists":
            return actual is not None
        if operator == "not_equals":
            return actual != expected
        if operator == "contains":
            return str(expected).casefold() in str(actual or "").casefold()
        if operator == "in":
            return actual in (expected if isinstance(expected, list) else [expected])
        return actual == expected

    @classmethod
    def _matches_rule(
        cls,
        rule: OmnichannelAutomationRule,
        event: OmnichannelChannelEvent,
        message: OmnichannelMessage | None,
    ) -> tuple[bool, dict[str, Any]]:
        context = {
            "eventType": event.event_type,
            "channel": event.event_metadata.get("channel"),
            "visibility": event.visibility,
            "actionability": event.actionability,
            "body": message.body if message else "",
            "payload": event.event_payload,
            "correlation": event.correlation,
            "metadata": event.event_metadata,
        }
        trigger_match = rule.trigger_type == event.event_type
        if rule.trigger_type == "message.received":
            trigger_match = (
                event.event_type.endswith("message") or event.event_type == "message.received"
            )
        elif rule.trigger_type == "direct_message":
            trigger_match = event.event_type in {
                "direct_message",
                "message.received",
                "dm.received",
            }
        elif rule.trigger_type == "keyword_comment":
            trigger_match = event.event_type in {
                "keyword_comment",
                "post_comment",
                "page_comment",
                "comment.received",
            }
        channel_match = not rule.channel or rule.channel == context["channel"]
        keyword_config = rule.trigger_config.get("keywords") or []
        if isinstance(keyword_config, str):
            keyword_config = [keyword_config]
        excluded_keywords = rule.trigger_config.get("excludedKeywords") or []
        if isinstance(excluded_keywords, str):
            excluded_keywords = [excluded_keywords]
        case_sensitive = bool(rule.trigger_config.get("caseSensitive", False))
        keyword_mode = str(rule.trigger_config.get("keywordMode") or "contains").strip()
        match_mode = str(rule.trigger_config.get("match") or "any").strip()
        raw_body = str(context["body"] or "")
        body = raw_body if case_sensitive else raw_body.casefold()

        def normalize_keyword(value: Any) -> str:
            text = str(value).strip()
            return text if case_sensitive else text.casefold()

        def keyword_matches(value: Any) -> bool:
            keyword = normalize_keyword(value)
            if not keyword:
                return False
            if keyword_mode == "exact":
                return body.strip() == keyword
            if keyword_mode == "starts_with":
                return body.strip().startswith(keyword)
            if keyword_mode == "ends_with":
                return body.strip().endswith(keyword)
            return keyword in body

        included_matches = [keyword_matches(keyword) for keyword in keyword_config]
        keyword_match = not keyword_config or (
            all(included_matches) if match_mode == "all" else any(included_matches)
        )
        excluded_match = any(keyword_matches(keyword) for keyword in excluded_keywords)
        keyword_match = keyword_match and not excluded_match
        source_ids = rule.trigger_config.get("sourceIds") or []
        source_value = (
            context["correlation"].get("postId")
            or context["correlation"].get("adId")
            or context["correlation"].get("sourceId")
        )
        source_match = not source_ids or source_value in source_ids
        parent_comment_id = (
            context["correlation"].get("parentCommentId")
            or context["payload"].get("parentCommentId")
            or context["payload"].get("parent_comment_id")
        )
        level_match = not bool(rule.trigger_config.get("firstLevelOnly", True)) or not bool(
            parent_comment_id
        )
        social_profile = context["metadata"].get("socialProfile") or context["payload"].get(
            "socialProfile"
        )
        if not isinstance(social_profile, dict):
            social_profile = {}

        def social_flag(*keys: str) -> bool | None:
            for key in keys:
                if key in social_profile and isinstance(social_profile[key], bool):
                    return social_profile[key]
                if key in context["metadata"] and isinstance(context["metadata"][key], bool):
                    return context["metadata"][key]
                if key in context["payload"] and isinstance(context["payload"][key], bool):
                    return context["payload"][key]
            return None

        follows_business = social_flag(
            "followsBusiness",
            "follows_business",
            "followsInstagramAccount",
        )
        follower_requirement = str(
            rule.trigger_config.get("followerStatus") or "any"
        ).strip()
        follower_match = follower_requirement == "any" or (
            follows_business is not None
            and follows_business == (follower_requirement == "follows")
        )

        verified_contact = social_flag("verified", "isVerified", "is_verified")
        verified_requirement = str(
            rule.trigger_config.get("verifiedStatus") or "any"
        ).strip()
        verified_match = verified_requirement == "any" or (
            verified_contact is not None
            and verified_contact == (verified_requirement == "verified")
        )

        messaging_window = str(
            social_profile.get("messagingWindow")
            or context["metadata"].get("messagingWindow")
            or context["payload"].get("messagingWindow")
            or "unknown"
        ).strip()
        window_requirement = str(
            rule.trigger_config.get("messagingWindow") or "any"
        ).strip()
        window_match = window_requirement == "any" or messaging_window == window_requirement
        conditions_match = all(
            cls._matches_condition(context, condition) for condition in rule.conditions
        )
        context["match"] = {
            "trigger": trigger_match,
            "channel": channel_match,
            "keyword": keyword_match,
            "excludedKeyword": not excluded_match,
            "source": source_match,
            "commentLevel": level_match,
            "followerStatus": follower_match,
            "verifiedStatus": verified_match,
            "messagingWindow": window_match,
            "conditions": conditions_match,
        }
        context["keywordEvaluation"] = {
            "caseSensitive": case_sensitive,
            "keywordMode": keyword_mode,
            "match": match_mode,
            "includedCount": len(keyword_config),
            "excludedCount": len(excluded_keywords),
        }
        context["socialProfileEvaluation"] = {
            "followsBusiness": follows_business,
            "followerRequirement": follower_requirement,
            "followerDataAvailable": follows_business is not None,
            "verified": verified_contact,
            "verifiedRequirement": verified_requirement,
            "verifiedDataAvailable": verified_contact is not None,
            "messagingWindow": messaging_window,
            "messagingWindowRequirement": window_requirement,
        }
        return all(context["match"].values()), context

    async def simulate_automation(
        self,
        workspace_id: UUID,
        rule_id: UUID,
        payload: AutomationSimulationRequest,
    ) -> tuple[OmnichannelAutomationRun, list[OmnichannelAutomationRunEvent], bool]:
        rule = await self._get_rule(workspace_id, rule_id)
        event = await self.db.scalar(
            select(OmnichannelChannelEvent).where(
                OmnichannelChannelEvent.workspace_id == workspace_id,
                OmnichannelChannelEvent.id == payload.event_id,
            )
        )
        if not event:
            raise LookupError("Evento canônico não encontrado.")
        existing = await self.db.scalar(
            select(OmnichannelAutomationRun).where(
                OmnichannelAutomationRun.workspace_id == workspace_id,
                OmnichannelAutomationRun.idempotency_key == payload.idempotency_key,
            )
        )
        if existing:
            if existing.rule_id != rule_id or existing.event_id != payload.event_id:
                raise OmnichannelOrchestrationConflictError(
                    "A chave de simulação já foi usada para outro flow ou evento."
                )
            return existing, await self.list_run_trace(workspace_id, existing.id), True
        message = await self.db.scalar(
            select(OmnichannelMessage).where(
                OmnichannelMessage.workspace_id == workspace_id,
                OmnichannelMessage.idempotency_key == f"channel-event:{event.id}",
            )
        )
        matched, context = self._matches_rule(rule, event, message)
        run = OmnichannelAutomationRun(
            workspace_id=workspace_id,
            rule_id=rule.id,
            event_id=event.id,
            thread_id=event.thread_id,
            state="simulating",
            idempotency_key=payload.idempotency_key,
            context=context,
            result={"externalRequests": 0, "matched": matched, "preparedActions": 0},
            error={},
        )
        self.db.add(run)
        try:
            await self.db.flush()
        except IntegrityError as error:
            raise OmnichannelOrchestrationConflictError(
                "Simulação concorrente duplicada."
            ) from error
        trace: list[OmnichannelAutomationRunEvent] = []
        trace.append(
            OmnichannelAutomationRunEvent(
                workspace_id=workspace_id,
                run_id=run.id,
                sequence=1,
                action_key="trigger",
                state="matched" if matched else "skipped",
                input_snapshot={"eventId": str(event.id), "triggerType": rule.trigger_type},
                output_snapshot=context["match"],
            )
        )
        prepared_actions: list[dict[str, Any]] = []
        if matched:
            for index, action in enumerate(rule.actions, start=1):
                action_type = str(action.get("type"))
                action_state = (
                    "blocked_external"
                    if action_type in _AUTOMATION_PROVIDER_ACTIONS
                    else "simulated_local"
                )
                prepared = {
                    "index": index,
                    "type": action_type,
                    "state": action_state,
                    "delaySeconds": int(action.get("delaySeconds") or 0),
                }
                prepared_actions.append(prepared)
                trace.append(
                    OmnichannelAutomationRunEvent(
                        workspace_id=workspace_id,
                        run_id=run.id,
                        sequence=index + 1,
                        action_key=str(action.get("key") or f"action-{index}"),
                        state=action_state,
                        duration_ms=0,
                        input_snapshot={
                            "type": action_type,
                            "hasBody": bool(action.get("body")),
                        },
                        output_snapshot={"externalRequests": 0, "localOnly": True},
                    )
                )
        run.state = "completed_local" if matched else "skipped"
        run.current_action = len(prepared_actions)
        run.result = {
            "externalRequests": 0,
            "matched": matched,
            "preparedActions": len(prepared_actions),
            "actions": prepared_actions,
            "coexistWithAi": rule.coexist_with_ai,
        }
        run.completed_at = _now()
        self.db.add_all(trace)
        await self.db.flush()
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="omnichannel.automation_run.updated",
            data={
                "runId": str(run.id),
                "ruleId": str(rule.id),
                "threadId": str(run.thread_id),
                "state": run.state,
                "trace": [
                    {"sequence": item.sequence, "actionKey": item.action_key, "state": item.state}
                    for item in trace
                ],
            },
        )
        await self.db.refresh(run)
        for item in trace:
            await self.db.refresh(item)
        return run, trace, False

    async def _resolve_workspace_timezone(self, workspace_id: UUID) -> ZoneInfo:
        """Where the workspace timezone lives today: `Workspace.settings["timezone"]`
        (IANA name, e.g. "America/Sao_Paulo")."""
        return await resolve_workspace_timezone(self.db, workspace_id)

    @staticmethod
    def _rule_quiet_hours_blocked(
        quiet_hours: dict[str, Any],
        now: datetime,
        *,
        workspace_timezone: ZoneInfo | None = None,
    ) -> bool:
        """Achado ALTO (auditoria 23/07/2026): `quiet_hours` gravado e nunca lido.

        Formato aceito: `{"enabled": bool, "startHour": int, "endHour": int}`
        (horas 0-23, avaliadas no fuso do workspace — `workspace_timezone`,
        resolvido via `_resolve_workspace_timezone`; UTC apenas se nenhum for
        informado). Ausente/incompleto => não bloqueia.
        """
        if not isinstance(quiet_hours, dict) or not quiet_hours.get("enabled"):
            return False
        try:
            start = int(quiet_hours.get("startHour", quiet_hours.get("start", 0)))
            end = int(quiet_hours.get("endHour", quiet_hours.get("end", 0)))
        except (TypeError, ValueError):
            return False
        if start == end:
            return False
        local_now = now.astimezone(workspace_timezone) if workspace_timezone else now
        hour = local_now.hour
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    async def _rule_rate_limited(
        self,
        workspace_id: UUID,
        rule: OmnichannelAutomationRule,
        run: OmnichannelAutomationRun,
        event: OmnichannelChannelEvent,
        now: datetime,
    ) -> str | None:
        """Achado ALTO: `rate_limits` (perIdentityPerHour/globalPerMinute) gravado
        e nunca lido em `trigger_active_automations_for_event`. Conta runs
        anteriores da MESMA regra (heurística conservadora: conta toda tentativa
        anterior da regra, não só as que casaram, por não haver forma portátil
        de filtrar `matched` dentro do JSON `result` entre SQLite/Postgres — mais
        seguro superestimar o uso do limite do que subestimar).
        Retorna o `reason_code` do bloqueio, ou None se liberado.
        """
        rate_limits = rule.rate_limits or {}
        if not isinstance(rate_limits, dict) or not rate_limits:
            return None
        per_identity = rate_limits.get("perIdentityPerHour")
        if per_identity is not None and event.identity_id is not None:
            try:
                per_identity_limit = int(per_identity)
            except (TypeError, ValueError):
                per_identity_limit = None
            if per_identity_limit is not None and per_identity_limit > 0:
                count = await self.db.scalar(
                    select(func.count(OmnichannelAutomationRun.id))
                    .join(
                        OmnichannelChannelEvent,
                        OmnichannelChannelEvent.id == OmnichannelAutomationRun.event_id,
                    )
                    .where(
                        OmnichannelAutomationRun.workspace_id == workspace_id,
                        OmnichannelAutomationRun.rule_id == rule.id,
                        OmnichannelAutomationRun.id != run.id,
                        OmnichannelChannelEvent.identity_id == event.identity_id,
                        OmnichannelAutomationRun.created_at >= now - timedelta(hours=1),
                    )
                )
                if int(count or 0) >= per_identity_limit:
                    return "rate_limit_per_identity_per_hour"
        global_per_minute = rate_limits.get("globalPerMinute")
        if global_per_minute is not None:
            try:
                global_limit = int(global_per_minute)
            except (TypeError, ValueError):
                global_limit = None
            if global_limit is not None and global_limit > 0:
                count = await self.db.scalar(
                    select(func.count(OmnichannelAutomationRun.id)).where(
                        OmnichannelAutomationRun.workspace_id == workspace_id,
                        OmnichannelAutomationRun.rule_id == rule.id,
                        OmnichannelAutomationRun.id != run.id,
                        OmnichannelAutomationRun.created_at >= now - timedelta(minutes=1),
                    )
                )
                if int(count or 0) >= global_limit:
                    return "rate_limit_global_per_minute"
        return None

    async def _private_reply_already_materialized(
        self,
        workspace_id: UUID,
        target_external_id: str | None,
    ) -> bool:
        """Dedup cross-regra: 1 private reply por comentário, qualquer que seja a
        regra que casou (achado ALTO: "mais de 1 private reply por comentário
        quando várias regras casam")."""
        if not target_external_id:
            return False
        existing = await self.db.scalar(
            select(OmnichannelActionIntent.id)
            .where(
                OmnichannelActionIntent.workspace_id == workspace_id,
                OmnichannelActionIntent.action_type == "private_reply",
                OmnichannelActionIntent.target_external_id == target_external_id,
            )
            .limit(1)
        )
        return existing is not None

    async def trigger_active_automations_for_event(
        self,
        workspace_id: UUID,
        event_id: UUID,
    ) -> list[OmnichannelAutomationRun]:
        """Evaluate active rules once and persist provider actions without delivering them."""
        event = await self.db.scalar(
            select(OmnichannelChannelEvent).where(
                OmnichannelChannelEvent.workspace_id == workspace_id,
                OmnichannelChannelEvent.id == event_id,
            )
        )
        if event is None:
            raise LookupError("Evento canônico não encontrado.")
        channel = str((event.event_metadata or {}).get("channel") or "")
        rules = list(
            (
                await self.db.scalars(
                    select(OmnichannelAutomationRule)
                    .where(
                        OmnichannelAutomationRule.workspace_id == workspace_id,
                        OmnichannelAutomationRule.status == "active",
                        or_(
                            OmnichannelAutomationRule.channel.is_(None),
                            OmnichannelAutomationRule.channel == channel,
                        ),
                    )
                    .order_by(
                        OmnichannelAutomationRule.priority,
                        OmnichannelAutomationRule.created_at,
                    )
                )
            ).all()
        )
        runs: list[OmnichannelAutomationRun] = []
        for rule in rules:
            run_key = f"live:{event.id}:{rule.id}:v{rule.version}"
            run, _trace, deduplicated = await self.simulate_automation(
                workspace_id,
                rule.id,
                AutomationSimulationRequest(
                    event_id=event.id,
                    idempotency_key=run_key,
                ),
            )
            runs.append(run)
            if deduplicated or not bool((run.result or {}).get("matched")):
                if rule.stop_on_match and bool((run.result or {}).get("matched")):
                    break
                continue
            now_reference = _now()
            workspace_timezone = await self._resolve_workspace_timezone(workspace_id)
            if self._rule_quiet_hours_blocked(
                rule.quiet_hours or {}, now_reference, workspace_timezone=workspace_timezone
            ):
                run.result = {
                    **(run.result or {}),
                    "executionMode": "skipped_quiet_hours",
                    "materializedActionIntentIds": [],
                    "externalRequests": 0,
                }
                await self.db.flush()
                continue
            rate_limit_reason = await self._rule_rate_limited(
                workspace_id, rule, run, event, now_reference
            )
            if rate_limit_reason is not None:
                run.result = {
                    **(run.result or {}),
                    "executionMode": "skipped_rate_limited",
                    "rateLimitReason": rate_limit_reason,
                    "materializedActionIntentIds": [],
                    "externalRequests": 0,
                }
                await self.db.flush()
                continue
            materialized: list[str] = []
            for index, action in enumerate(rule.actions, start=1):
                action_type = str(action.get("type") or "")
                if action_type not in _AUTOMATION_PROVIDER_ACTIONS:
                    continue
                if event.connection_id is None:
                    continue
                if action_type == "private_reply" and await self._private_reply_already_materialized(
                    workspace_id, event.external_event_id
                ):
                    run.error = {
                        **(run.error or {}),
                        f"action-{index}": {
                            "type": action_type,
                            "reason": "private_reply já materializado para este comentário por outra regra.",
                        },
                    }
                    continue
                delay_seconds = int(action.get("delaySeconds") or 0)
                try:
                    intent, _ = await self.create_action_intent(
                        workspace_id,
                        event.thread_id,
                        None,
                        OmnichannelActionIntentCreate(
                            event_id=event.id if action_type in _SOCIAL_ACTIONS else None,
                            connection_id=event.connection_id,
                            action_type=action_type,
                            target_external_id=event.external_event_id,
                            body=str(action.get("body") or "").strip() or None,
                            scheduled_at=(
                                _now() + timedelta(seconds=delay_seconds)
                                if delay_seconds
                                else None
                            ),
                            idempotency_key=f"automation:{run.id}:action:{index}",
                            payload={
                                "automationRunId": str(run.id),
                                "automationRuleId": str(rule.id),
                                "actionIndex": index,
                                "delaySeconds": delay_seconds,
                            },
                        ),
                    )
                except ValueError as error:
                    run.error = {
                        **(run.error or {}),
                        f"action-{index}": {
                            "type": action_type,
                            "reason": str(error),
                        },
                    }
                    continue
                materialized.append(str(intent.id))
            run.result = {
                **(run.result or {}),
                "executionMode": "live_local",
                "materializedActionIntentIds": materialized,
                "externalRequests": 0,
            }
            await self.db.flush()
            if rule.stop_on_match:
                break
        return runs

    async def list_run_trace(
        self, workspace_id: UUID, run_id: UUID
    ) -> list[OmnichannelAutomationRunEvent]:
        result = await self.db.execute(
            select(OmnichannelAutomationRunEvent)
            .where(
                OmnichannelAutomationRunEvent.workspace_id == workspace_id,
                OmnichannelAutomationRunEvent.run_id == run_id,
            )
            .order_by(OmnichannelAutomationRunEvent.sequence)
        )
        return list(result.scalars().all())
