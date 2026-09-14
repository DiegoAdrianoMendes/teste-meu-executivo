from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import case, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.klm_runtime import external_delivery_allowed
from app.core.logging_privacy import opaque_ref, safe_error_type
from app.db.post_commit import queue_realtime_event
from app.db.tenant import set_workspace_context
from app.db.webhook_tenant import (
    resolve_whatsapp_template_webhook_workspace,
    resolve_whatsapp_webhook_workspace,
)
from app.models.klm_operations import (
    KlmAuditEvent,
    MessageDeliveryStatus,
    OmnichannelMessage,
    OmnichannelThread,
)
from app.models.matter import Matter
from app.models.notification import NotificationType
from app.models.omnichannel_v6 import OmnichannelMessageDeliveryEvent
from app.models.oss_bridge import sanitize_oss_json
from app.models.platform_official_whatsapp import PlatformOfficialWhatsAppConnection
from app.models.storage_object import StorageObject
from app.models.user import User
from app.models.whatsapp import (
    ConnectionType,
    HumanControlState,
    WaMessageDirection,
    WaMessageStatus,
    WaQueueStatus,
    WhatsAppConnection,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.models.whatsapp_campaign import (
    MetaTemplateStatus,
    WhatsAppAudienceListMember,
    WhatsAppCampaignRecipient,
    WhatsAppMetaTemplate,
    WhatsAppOptInRecord,
    WhatsAppSuppressionEntry,
)
from app.models.whatsapp_note import WhatsAppConversationNote
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.schemas.omnichannel_v6 import InternalMessageDeliveryReceiptIngest
from app.services.audit_actor_service import resolve_human_actor_label
from app.services.conversation_ownership_service import (
    human_already_holds,
    human_takeover,
    resolve_contact_thread,
)
from app.services.message_evidence_service import (
    MessageEvidenceService,
    ProviderEnvelopeContext,
    canonical_sha256,
)
from app.services.notification_service import NotificationService
from app.services.omnichannel_v6_service import OmnichannelV6Service
from app.services.operational_alert_service import OperationalAlertService
from app.services.whatsapp.automation_rule_service import WhatsAppAutomationRuleService
from app.services.whatsapp.campaign_service import (
    WhatsAppCampaignService,
    whatsapp_delivery_status_event_key,
    whatsapp_delivery_status_occurred_at,
)
from app.services.whatsapp.connection_service import WhatsAppConnectionService
from app.services.whatsapp.contact_capacity_service import WhatsAppContactCapacityService
from app.services.whatsapp.errors import classify_meta_error
from app.services.whatsapp.helpers import (
    normalize_meta_phone_number,
    phone_number_lookup_variants,
    phone_number_variants,
)
from app.services.whatsapp.identity_resolution import (
    WhatsAppIdentityRef,
    extract_inbound_identity,
)
from app.services.whatsapp.message_service import WhatsAppMessageService
from app.services.whatsapp.meta_webhook_operations_service import (
    MetaWebhookOperationsService,
    supported_meta_webhook_fields,
)
from app.services.whatsapp.webhooks.dispatcher import MetaWebhookDispatcher

logger = logging.getLogger(__name__)

INBOUND_PIPELINE_LEASE_TTL = timedelta(minutes=5)
INBOUND_MEDIA_RETRY_DELAY = timedelta(minutes=1)

# W2.2 — eventos de status/categoria de template Meta, entregues no MESMO
# webhook de WABA que `messages`/`smb_message_echoes`, mas roteados por WABA
# (sem `phone_number_id`) em vez de por número.
TEMPLATE_STATUS_UPDATE_FIELD = "message_template_status_update"
TEMPLATE_CATEGORY_UPDATE_FIELD = "template_category_update"
TEMPLATE_WEBHOOK_FIELDS = frozenset(
    {TEMPLATE_STATUS_UPDATE_FIELD, TEMPLATE_CATEGORY_UPDATE_FIELD}
)
SUPPORTED_META_WEBHOOK_FIELDS = supported_meta_webhook_fields()


class WebhookSigningIdentityError(RuntimeError):
    """The delivery references more than one or no resolvable Meta authority."""


@dataclass(frozen=True)
class _WebhookRouteReference:
    kind: str
    identifier: str


@dataclass(frozen=True)
class _WebhookSigningAuthority:
    owner_kind: str
    owner_id: str
    app_secret: str | None = dataclass_field(repr=False)


# Meta `event` -> nosso enum interno. Eventos que não representam um estado
# terminal/estável conhecido (ex.: FLAGGED, IN_APPEAL, PENDING_DELETION) não
# têm equivalente e preservam o status atual, guardando o evento cru.
_META_TEMPLATE_EVENT_TO_STATUS: dict[str, MetaTemplateStatus] = {
    "APPROVED": MetaTemplateStatus.approved,
    "REJECTED": MetaTemplateStatus.rejected,
    "PENDING": MetaTemplateStatus.pending,
    "PAUSED": MetaTemplateStatus.paused,
    "DISABLED": MetaTemplateStatus.disabled,
}

# Motivos de rejeição da Meta traduzidos para linguagem de gente (usados na
# notificação interna; o mapa completo com "o que corrigir" vive no frontend
# — frontend/src/pages/WhatsAppMetaTemplates.tsx).
REJECTED_REASON_TRANSLATIONS: dict[str, str] = {
    "INVALID_FORMAT": "formato inválido (placeholders ou estrutura fora do padrão da Meta)",
    "ABUSIVE_CONTENT": "conteúdo sinalizado como abusivo",
    "INCORRECT_CATEGORY": "categoria incorreta para o conteúdo do template",
    "SCAM": "conteúdo sinalizado como golpe/fraude",
    "PROMOTIONAL": "conteúdo promocional em template de utilidade",
    "TAG_CONTENT_MISMATCH": "conteúdo não corresponde à categoria declarada",
    "INVALID_LENGTH": "texto fora do limite de tamanho permitido",
    "NONE": "motivo não informado pela Meta",
}


def _translate_rejected_reason(reason: str | None) -> str:
    key = str(reason or "NONE").strip().upper()
    return REJECTED_REASON_TRANSLATIONS.get(key, f"motivo '{key}' (não catalogado)")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _unavailable_media_state(reason: str) -> dict[str, object]:
    return {
        "state": "unavailable",
        "storageKey": None,
        "objectId": None,
        "version": None,
        "reason": reason,
        "retryable": False,
        "reconciliationRequired": False,
    }


def _unreconciled_media_state(*, source_media_id: str | None) -> dict[str, object]:
    if settings.is_production:
        return _unavailable_media_state("storage_object_missing")
    if not source_media_id:
        return _unavailable_media_state("source_media_id_missing")
    return {
        "state": "retry_required",
        "storageKey": None,
        "objectId": None,
        "version": None,
        "reason": "versioned_storage_not_reconciled",
        "retryable": True,
        "reconciliationRequired": True,
    }


def _media_reconciliation_pending(pipeline: dict[str, Any]) -> bool:
    media_storage = pipeline.get("mediaStorage")
    return bool(
        isinstance(media_storage, dict)
        and media_storage.get("state") == "retry_required"
        and media_storage.get("reconciliationRequired") is True
    )


def _provider_reply_context_pending(pipeline: dict[str, Any]) -> bool:
    return bool(
        str(pipeline.get("providerContextId") or "").strip()
        and not pipeline.get("contextCompleted")
    )


def _scheduled_media_retry(
    pipeline: dict[str, Any],
    *,
    now: datetime,
    attempt: int | None = None,
    error_type: str | None = None,
) -> dict[str, object]:
    current = pipeline.get("mediaRetry")
    current = dict(current) if isinstance(current, dict) else {}
    retry_attempt = int(current.get("attempt", 0) or 0) if attempt is None else attempt
    retry = {
        "state": "scheduled",
        "attempt": retry_attempt,
        "scheduledAt": current.get("scheduledAt") or now.isoformat(),
        "nextAttemptAt": (now + INBOUND_MEDIA_RETRY_DELAY).isoformat(),
    }
    if error_type:
        retry["lastErrorType"] = error_type
    elif current.get("lastErrorType"):
        retry["lastErrorType"] = current["lastErrorType"]
    return retry


@dataclass(frozen=True)
class InboundPipelineLease:
    token: str
    generation: int


class WhatsAppContactWithoutIdentifierError(RuntimeError):
    """Evento sem telefone e sem BSUID.

    Cenário criado pela mudança de identidade de 2026: o usuário adota username,
    a Meta omite o `wa_id` e, se o `user_id` também não vier, não há como
    reconhecer nem responder. Precisa subir como exceção operacional visível,
    porque mensagem que some em silêncio é cliente perdido sem ninguém saber.
    """


class InboundPipelineLeaseLostError(RuntimeError):
    """Raised when a stale or expired worker tries to persist a checkpoint."""


class InboundPipelineReconciliationNotFoundError(RuntimeError):
    """Raised when a tenant-bound inbound message cannot be found."""


class InboundPipelineReconciliationConflictError(RuntimeError):
    """Raised when an inbound message has no human-resolvable uncertain stage."""


@dataclass(frozen=True)
class ParsedWhatsAppContent:
    body: str
    media_type: str | None = None
    source_media_id: str | None = None
    structured_content: dict[str, Any] | None = None


def _normalize_wa_phone(value: Any) -> str:
    return normalize_meta_phone_number(str(value or "")) or ""


# W1.11 — Opt-out inbound automático (auditoria M3 / plano P1-6).
#
# Lista documentada de gatilhos de descadastro. Duas categorias, com regras
# de correspondência distintas para reduzir falsos positivos:
#   * INBOUND_OPT_OUT_SINGLE_WORD_TRIGGERS: casam como PALAVRA ISOLADA
#     (fronteira de palavra) em qualquer ponto do texto normalizado — nunca
#     como substring de uma palavra maior. Ex.: "pare" casa em "por favor
#     pare com isso", mas não em "compare os valores".
#   * INBOUND_OPT_OUT_PHRASE_TRIGGERS: frases completas, casadas como
#     substring do texto normalizado (já são específicas o bastante para não
#     precisar de fronteira de palavra adicional).
# Texto normalizado = casefold + remoção de acentuação + trim + espaços
# colapsados. Caso de referência (auditoria 23/07/2026): "não quero perder
# o prazo" NÃO deve suprimir (não contém nenhum gatilho).
INBOUND_OPT_OUT_SINGLE_WORD_TRIGGERS: tuple[str, ...] = (
    "sair",
    "pare",
    "parar",
    "stop",
    "cancelar",
    "remover",
)
INBOUND_OPT_OUT_PHRASE_TRIGGERS: tuple[str, ...] = (
    "nao quero mais",
    "nao quero receber",
)
INBOUND_OPT_OUT_SUPPRESSION_REASON = "inbound_opt_out"


def _normalize_opt_out_text(value: str) -> str:
    """Casefold + remove acentos/diacríticos + colapsa espaços."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold().strip()
    return re.sub(r"\s+", " ", text)


def _detect_inbound_opt_out(body: str) -> str | None:
    """Retorna o gatilho casado, ou None se a mensagem não pedir descadastro."""
    normalized = _normalize_opt_out_text(body)
    if not normalized:
        return None
    for trigger in INBOUND_OPT_OUT_SINGLE_WORD_TRIGGERS:
        if re.search(rf"(?<!\w){re.escape(trigger)}(?!\w)", normalized):
            return trigger
    for trigger in INBOUND_OPT_OUT_PHRASE_TRIGGERS:
        if trigger in normalized:
            return trigger
    return None


_REFERRAL_FIELDS = (
    "ctwa_clid",
    "source_url",
    "source_id",
    "source_type",
    "headline",
    "body",
)


def _parse_referral(message: dict[str, Any]) -> dict[str, str] | None:
    """Extract the CTWA/ad referral fields that exist on an inbound message."""
    referral = message.get("referral")
    if not isinstance(referral, dict):
        return None
    data: dict[str, str] = {}
    for field in _REFERRAL_FIELDS:
        value = str(referral.get(field) or "").strip()
        if value:
            data[field] = value
    return data or None


def _parse_message_content(message: Dict[str, Any]) -> ParsedWhatsAppContent:
    message_type = str(message.get("type") or "text").strip().lower()
    if message_type == "text":
        return ParsedWhatsAppContent(body=str(message.get("text", {}).get("body") or ""))
    if message_type in {"image", "audio", "video", "document", "sticker"}:
        media = message.get(message_type) or {}
        body = str(media.get("caption") or f"[{message_type}]")
        return ParsedWhatsAppContent(
            body=body,
            media_type=message_type,
            source_media_id=str(media.get("id") or "").strip() or None,
            structured_content={
                "kind": "media",
                "type": message_type,
                "mimeType": media.get("mime_type"),
                "filename": media.get("filename"),
            },
        )
    if message_type == "location":
        location = message.get("location") or {}
        latitude = location.get("latitude")
        longitude = location.get("longitude")
        return ParsedWhatsAppContent(
            body=f"[Localização: {latitude}, {longitude}]",
            structured_content={"kind": "location", **location},
        )
    if message_type == "interactive":
        interactive = message.get("interactive") or {}
        response_type = str(interactive.get("type") or "").strip().lower()
        response = interactive.get(response_type) or {}
        response_id = str(response.get("id") or "").strip() or None
        title = str(response.get("title") or response.get("name") or "").strip()
        if response_type == "nfm_reply":
            raw_json = response.get("response_json")
            try:
                flow_response = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
            except json.JSONDecodeError:
                flow_response = {"raw": str(raw_json)}
            structured = {
                "kind": "flow_reply",
                "id": response_id,
                "title": title or "Resposta do formulário",
                "response": flow_response or {},
            }
        else:
            structured = {
                "kind": response_type or "interactive_reply",
                "id": response_id,
                "title": title or None,
                "description": response.get("description"),
            }
        return ParsedWhatsAppContent(
            body=title or response_id or "[Resposta interativa]",
            structured_content=structured,
        )
    if message_type == "button":
        button = message.get("button") or {}
        title = str(button.get("text") or "").strip()
        payload = str(button.get("payload") or "").strip()
        return ParsedWhatsAppContent(
            body=title or payload or "[Resposta de botão]",
            structured_content={"kind": "template_button_reply", "id": payload, "title": title},
        )
    if message_type == "reaction":
        reaction = message.get("reaction") or {}
        emoji = str(reaction.get("emoji") or "").strip()
        return ParsedWhatsAppContent(
            body=emoji or "[Reação removida]",
            structured_content={
                "kind": "reaction",
                "messageId": reaction.get("message_id"),
                "emoji": emoji or None,
            },
        )
    return ParsedWhatsAppContent(
        body=f"[Mensagem do tipo {message_type}]",
        structured_content={"kind": "unsupported", "providerType": message_type},
    )


def _provider_error_codes(payload: Dict[str, Any]) -> set[int]:
    codes: set[int] = set()
    errors = payload.get("errors") or []
    if isinstance(errors, dict):
        errors = [errors]
    for error in errors if isinstance(errors, list) else []:
        if not isinstance(error, dict):
            continue
        raw_code = error.get("code")
        try:
            codes.add(int(raw_code))
        except (TypeError, ValueError):
            continue
    return codes


def _is_provider_delete_signal(payload: Dict[str, Any]) -> bool:
    status = str(payload.get("status") or "").strip().lower()
    message_type = str(payload.get("type") or "").strip().lower()
    return status == "deleted" or (
        message_type == "unsupported" and 131051 in _provider_error_codes(payload)
    )


def _provider_timestamp(payload: Dict[str, Any]) -> datetime | None:
    raw = payload.get("timestamp")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc) if value else None


def _optional_uuid(value: Any) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


class WhatsAppWebhookService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        connection_service: WhatsAppConnectionService,
        message_service: WhatsAppMessageService,
        automation_rule_service: WhatsAppAutomationRuleService,
        campaign_service: WhatsAppCampaignService,
    ) -> None:
        self.db = db
        self.connection_service = connection_service
        self.message_service = message_service
        self.automation_rule_service = automation_rule_service
        self.campaign_service = campaign_service

    @staticmethod
    def _hmac_matches(app_secret: str, payload_bytes: bytes, signature: str) -> bool:
        expected = hmac.new(
            app_secret.encode("utf-8"),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(f"sha256={expected}", signature)

    @staticmethod
    def _extract_webhook_route_references(
        payload_bytes: bytes,
    ) -> list[_WebhookRouteReference]:
        """Extract every routing identity that the authenticated body can affect.

        A Meta delivery is signed once but may contain many entries/changes.
        Authentication therefore has to bind the *whole* body to one authority,
        not merely find one candidate key that happens to match. Both the entry
        WABA and every value-level phone-number ID participate in that binding.
        """
        try:
            payload = json.loads(payload_bytes)
        except (ValueError, TypeError):
            return []
        if not isinstance(payload, dict):
            return []

        references: list[_WebhookRouteReference] = []
        seen: set[tuple[str, str]] = set()

        def add_reference(kind: str, raw_identifier: object) -> None:
            identifier = str(raw_identifier or "").strip()
            key = (kind, identifier)
            if not identifier or key in seen:
                return
            seen.add(key)
            references.append(
                _WebhookRouteReference(kind=kind, identifier=identifier)
            )

        # Coexistence history/state-sync uses a top-level partner envelope
        # instead of `entry[].changes[]`, but the worker still routes it by the
        # same Meta phone-number ID.
        partner_data = payload.get("data")
        if isinstance(partner_data, dict):
            partner_metadata = partner_data.get("metadata")
            if isinstance(partner_metadata, dict):
                add_reference(
                    "phone_number_id",
                    partner_metadata.get("phone_number_id"),
                )

        for entry in payload.get("entry") or []:
            if not isinstance(entry, dict):
                continue
            add_reference("waba_id", entry.get("id"))
            for change in entry.get("changes") or []:
                if not isinstance(change, dict):
                    continue
                value = change.get("value")
                if not isinstance(value, dict):
                    continue
                metadata = value.get("metadata")
                if not isinstance(metadata, dict):
                    continue
                add_reference("phone_number_id", metadata.get("phone_number_id"))
        return references

    @staticmethod
    def _connection_app_secret(
        connection: WhatsAppConnection,
        *,
        cipher,
    ) -> str | None:
        if not connection.app_secret_encrypted:
            return None
        secret = cipher.decrypt(connection.app_secret_encrypted)
        return secret if secret else None

    async def _resolve_reference_authorities(
        self,
        reference: _WebhookRouteReference,
        *,
        global_secret: str | None,
        cipher,
    ) -> list[_WebhookSigningAuthority]:
        """Resolve one untrusted route reference through narrow RLS bootstrap."""
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as tenant_db:
            if reference.kind == "phone_number_id":
                platform_connections = list(
                    (
                        await tenant_db.scalars(
                            select(PlatformOfficialWhatsAppConnection).where(
                                PlatformOfficialWhatsAppConnection.phone_number_id
                                == reference.identifier
                            )
                        )
                    ).all()
                )
                workspace_id = await resolve_whatsapp_webhook_workspace(
                    tenant_db,
                    phone_number_id=reference.identifier,
                )
                tenant_connections: list[WhatsAppConnection] = []
                if workspace_id is not None:
                    await set_workspace_context(tenant_db, workspace_id)
                    tenant_connections = list(
                        (
                            await tenant_db.scalars(
                                select(WhatsAppConnection).where(
                                    WhatsAppConnection.workspace_id == workspace_id,
                                    WhatsAppConnection.phone_number_id
                                    == reference.identifier,
                                )
                            )
                        ).all()
                    )
            else:
                platform_connections = list(
                    (
                        await tenant_db.scalars(
                            select(PlatformOfficialWhatsAppConnection).where(
                                or_(
                                    PlatformOfficialWhatsAppConnection.waba_id
                                    == reference.identifier,
                                    PlatformOfficialWhatsAppConnection.business_account_id
                                    == reference.identifier,
                                )
                            )
                        )
                    ).all()
                )
                if tenant_db.get_bind().dialect.name == "postgresql":
                    workspace_id = (
                        await resolve_whatsapp_template_webhook_workspace(
                            tenant_db,
                            waba_id=reference.identifier,
                        )
                    )
                else:
                    # SQLite has no SECURITY DEFINER/RLS function. Preserve the
                    # production resolver's ambiguity contract in local tests.
                    workspace_ids = {
                        UUID(str(value))
                        for value in (
                            await tenant_db.scalars(
                                select(WhatsAppConnection.workspace_id).where(
                                    WhatsAppConnection.waba_id
                                    == reference.identifier
                                )
                            )
                        ).all()
                    }
                    workspace_id = (
                        next(iter(workspace_ids))
                        if len(workspace_ids) == 1
                        else None
                    )
                tenant_connections = []
                if workspace_id is not None:
                    await set_workspace_context(tenant_db, workspace_id)
                    tenant_connections = list(
                        (
                            await tenant_db.scalars(
                                select(WhatsAppConnection).where(
                                    WhatsAppConnection.workspace_id == workspace_id,
                                    WhatsAppConnection.waba_id
                                    == reference.identifier,
                                )
                            )
                        ).all()
                    )

        authorities: list[_WebhookSigningAuthority] = []
        if platform_connections:
            authorities.append(
                _WebhookSigningAuthority(
                    owner_kind="platform",
                    owner_id="platform",
                    app_secret=global_secret,
                )
            )
        if not tenant_connections:
            return authorities

        explicit_secrets = {
            secret
            for connection in tenant_connections
            if (
                secret := self._connection_app_secret(
                    connection,
                    cipher=cipher,
                )
            )
        }
        effective_secrets: set[str | None]
        if reference.kind == "waba_id" and explicit_secrets:
            # App Secret is app-scoped although stored per connection. A unique
            # configured BYOA secret governs the WABA; missing copies on sibling
            # phone rows cannot reopen the global fallback.
            effective_secrets = set(explicit_secrets)
        else:
            effective_secrets = {
                self._connection_app_secret(connection, cipher=cipher)
                or global_secret
                for connection in tenant_connections
            }

        for secret in effective_secrets:
            authorities.append(
                _WebhookSigningAuthority(
                    owner_kind="workspace",
                    owner_id=str(tenant_connections[0].workspace_id),
                    app_secret=secret,
                )
            )
        return authorities

    async def _resolve_connection_app_secrets(self, payload_bytes: bytes) -> list[str]:
        """Bind the entire delivery to exactly one owner and one effective key."""
        references = self._extract_webhook_route_references(payload_bytes)
        global_secret = settings.WHATSAPP_APP_SECRET.strip() or None
        if not references:
            return [global_secret] if global_secret else []

        from app.services.whatsapp.secrets import WhatsAppSecretCipher

        cipher = WhatsAppSecretCipher()
        authorities: list[_WebhookSigningAuthority] = []
        try:
            for reference in references:
                resolved = await self._resolve_reference_authorities(
                    reference,
                    global_secret=global_secret,
                    cipher=cipher,
                )
                if not resolved:
                    logger.warning(
                        "whatsapp.webhook_signature_identity_unresolved "
                        "kind=%s identity_ref=%s",
                        reference.kind,
                        opaque_ref(reference.identifier, kind=reference.kind),
                    )
                    raise WebhookSigningIdentityError(
                        "Webhook signing identity could not be resolved"
                    )
                authorities.extend(resolved)
        except WebhookSigningIdentityError:
            raise
        except Exception as exc:
            logger.warning(
                "whatsapp.webhook_signature_authority_lookup_failed "
                "error_type=%s",
                safe_error_type(exc),
            )
            return []

        owner_keys = {
            (authority.owner_kind, authority.owner_id)
            for authority in authorities
        }
        secrets = {authority.app_secret for authority in authorities}
        if len(owner_keys) != 1 or len(secrets) != 1:
            logger.warning(
                "whatsapp.webhook_signature_identity_mixed owners=%s secrets=%s",
                len(owner_keys),
                len(secrets),
            )
            raise WebhookSigningIdentityError(
                "Webhook references mixed signing identities"
            )

        app_secret = next(iter(secrets))
        return [app_secret] if app_secret else []

    async def resolve_webhook_signing_secrets(self, payload_bytes: bytes) -> list[str]:
        """Return at most one key, already bound to the delivery's authority.

        The global key is used only when the resolved authority has no BYOA
        secret (or when a legacy body has no routing references). It is never
        appended beside a connection-specific secret.
        """
        return await self._resolve_connection_app_secrets(payload_bytes)

    def verify_webhook_signature(
        self,
        payload_bytes: bytes,
        signature: str,
        *,
        candidate_secrets: list[str],
    ) -> bool:
        """Fail closed against the single authority-bound candidate secret."""
        if not signature:
            return False
        return len(candidate_secrets) == 1 and self._hmac_matches(
            candidate_secrets[0],
            payload_bytes,
            signature,
        )

    @staticmethod
    def _evidence_provider(connection: WhatsAppConnection) -> str:
        return "qr_gateway" if connection.connection_type == ConnectionType.QR_CODE else "meta_cloud"

    async def _canonical_message_for_legacy(
        self,
        *,
        workspace_id: UUID,
        legacy_message: WhatsAppMessage,
    ) -> OmnichannelMessage | None:
        metadata = dict(legacy_message.message_metadata or {})
        canonical = metadata.get("canonical")
        canonical = canonical if isinstance(canonical, dict) else {}
        pipeline = dict(legacy_message.inbound_pipeline or {})
        message_id = _optional_uuid(
            canonical.get("messageId") or pipeline.get("canonicalMessageId")
        )
        if message_id is not None:
            message = await self.db.scalar(
                select(OmnichannelMessage).where(
                    OmnichannelMessage.workspace_id == workspace_id,
                    OmnichannelMessage.id == message_id,
                )
            )
            if message is not None:
                return message
        if legacy_message.wamid:
            return await self.db.scalar(
                select(OmnichannelMessage).where(
                    OmnichannelMessage.workspace_id == workspace_id,
                    OmnichannelMessage.external_message_id == legacy_message.wamid,
                )
            )
        return None

    async def _storage_evidence(
        self,
        *,
        workspace_id: UUID,
        legacy_message: WhatsAppMessage,
        provider_media_id: str | None,
    ) -> StorageObject | None:
        pipeline = dict(legacy_message.inbound_pipeline or {})
        captured_provider_media_id = str(pipeline.get("sourceMediaId") or "").strip() or None
        if (
            provider_media_id
            and captured_provider_media_id
            and provider_media_id != captured_provider_media_id
        ):
            return None
        media_storage = pipeline.get("mediaStorage")
        if not isinstance(media_storage, dict):
            return None
        object_id = _optional_uuid(media_storage.get("objectId"))
        try:
            version = int(media_storage.get("version") or 0)
        except (TypeError, ValueError):
            version = 0
        if object_id is None or version <= 0:
            return None
        return await self.db.scalar(
            select(StorageObject).where(
                StorageObject.workspace_id == workspace_id,
                StorageObject.object_id == object_id,
                StorageObject.version == version,
            )
        )

    async def _record_provider_observation(
        self,
        *,
        connection: WhatsAppConnection,
        workspace_id: UUID,
        legacy_message: WhatsAppMessage,
        message_data: Dict[str, Any],
        parsed: ParsedWhatsAppContent,
        envelope: ProviderEnvelopeContext | None,
        event_type: str = "provider_receipt",
        observation_state: str = "observed",
    ) -> bool:
        canonical = await self._canonical_message_for_legacy(
            workspace_id=workspace_id,
            legacy_message=legacy_message,
        )
        if canonical is None:
            return False
        metadata = dict(legacy_message.message_metadata or {})
        canonical_metadata = metadata.get("canonical")
        canonical_metadata = canonical_metadata if isinstance(canonical_metadata, dict) else {}
        pipeline = dict(legacy_message.inbound_pipeline or {})
        channel_event_id = _optional_uuid(
            canonical_metadata.get("eventId") or pipeline.get("canonicalEventId")
        )
        content_type = parsed.media_type or str(message_data.get("type") or "text")
        body_snapshot = legacy_message.body if event_type == "deleted" else parsed.body
        evidence_service = MessageEvidenceService(self.db)
        await evidence_service.record_event(
            workspace_id=workspace_id,
            provider=self._evidence_provider(connection),
            connection_key=f"whatsapp:{connection.id}",
            external_message_id=str(legacy_message.wamid or legacy_message.id),
            event_type=event_type,
            observation_state=observation_state,
            message_id=canonical.id,
            thread_id=canonical.thread_id,
            channel_event_id=channel_event_id,
            connection_id=canonical.connection_id,
            legacy_whatsapp_message_id=legacy_message.id,
            envelope=envelope,
            body_snapshot=body_snapshot,
            content_type=content_type,
            structured_content=parsed.structured_content or {},
            event_payload=message_data,
            provider_media_id=parsed.source_media_id,
            provider_occurred_at=_provider_timestamp(message_data) or legacy_message.timestamp,
            received_at=envelope.received_at if envelope else None,
        )

        if parsed.media_type:
            storage = await self._storage_evidence(
                workspace_id=workspace_id,
                legacy_message=legacy_message,
                provider_media_id=parsed.source_media_id,
            )
            pipeline_storage = pipeline.get("mediaStorage")
            pipeline_storage = pipeline_storage if isinstance(pipeline_storage, dict) else {}
            media_event_type = (
                "media_captured"
                if storage is not None and storage.checksum_sha256 and storage.byte_size is not None
                else "media_unavailable"
            )
            await evidence_service.record_event(
                workspace_id=workspace_id,
                provider=self._evidence_provider(connection),
                connection_key=f"whatsapp:{connection.id}",
                external_message_id=str(legacy_message.wamid or legacy_message.id),
                event_type=media_event_type,
                observation_state=("observed" if media_event_type == "media_captured" else "unavailable"),
                message_id=canonical.id,
                thread_id=canonical.thread_id,
                channel_event_id=channel_event_id,
                connection_id=canonical.connection_id,
                legacy_whatsapp_message_id=legacy_message.id,
                envelope=envelope,
                body_snapshot=parsed.body,
                content_type=content_type,
                structured_content=parsed.structured_content or {},
                event_payload={
                    "providerMessage": message_data,
                    "storageState": pipeline_storage,
                },
                storage_object_id=storage.id if storage else None,
                storage_object_version=storage.version if storage else None,
                media_sha256=storage.checksum_sha256 if storage else None,
                media_byte_size=storage.byte_size if storage else None,
                media_mime_type=(
                    storage.detected_content_type or storage.declared_content_type
                    if storage
                    else str((parsed.structured_content or {}).get("mimeType") or "") or None
                ),
                media_filename=(
                    storage.original_filename
                    if storage
                    else str((parsed.structured_content or {}).get("filename") or "") or None
                ),
                provider_media_id=parsed.source_media_id,
                provider_occurred_at=_provider_timestamp(message_data) or legacy_message.timestamp,
                received_at=envelope.received_at if envelope else None,
            )
        return True

    async def _record_existing_message_observation(
        self,
        *,
        connection: WhatsAppConnection,
        workspace_id: UUID,
        legacy_message: WhatsAppMessage,
        message_data: Dict[str, Any],
        parsed: ParsedWhatsAppContent,
        envelope: ProviderEnvelopeContext | None,
    ) -> bool:
        canonical = await self._canonical_message_for_legacy(
            workspace_id=workspace_id,
            legacy_message=legacy_message,
        )
        if canonical is None:
            return False
        metadata = dict(legacy_message.message_metadata or {})
        previous_structured = metadata.get("structuredContent")
        previous_structured = previous_structured if isinstance(previous_structured, dict) else {}
        delete_signal = _is_provider_delete_signal(message_data)
        content_changed = bool(
            parsed.body != legacy_message.body
            or parsed.media_type != legacy_message.media_type
            or canonical_sha256(parsed.structured_content or {})
            != canonical_sha256(previous_structured)
        )
        unsupported = (parsed.structured_content or {}).get("kind") == "unsupported"
        event_type = (
            "deleted"
            if delete_signal
            else "edited"
            if content_changed
            else "provider_unsupported"
            if unsupported
            else "provider_receipt"
        )
        observation_state = (
            "provider_signal" if event_type in {"deleted", "provider_unsupported"} else "observed"
        )
        recorded = await self._record_provider_observation(
            connection=connection,
            workspace_id=workspace_id,
            legacy_message=legacy_message,
            message_data=message_data,
            parsed=parsed,
            envelope=envelope,
            event_type=event_type,
            observation_state=observation_state,
        )
        if recorded and event_type == "edited":
            legacy_message.body = parsed.body
            legacy_message.media_type = parsed.media_type
            metadata["structuredContent"] = parsed.structured_content or {}
            metadata["contentLifecycleState"] = "edited"
            metadata["contentEditedAt"] = (
                envelope.received_at if envelope else _now()
            ).isoformat()
            legacy_message.message_metadata = metadata
        elif recorded and event_type == "deleted":
            metadata["contentLifecycleState"] = "deleted"
            metadata["contentDeletedAt"] = (
                envelope.received_at if envelope else _now()
            ).isoformat()
            legacy_message.message_metadata = metadata
        await self.db.flush()
        return event_type in {"edited", "deleted", "provider_unsupported"}

    async def process_webhook(
        self,
        payload: Dict[str, Any],
        *,
        envelope: ProviderEnvelopeContext | None = None,
    ) -> None:
        if payload.get("event") in {"history", "smb_app_state_sync"}:
            await self._process_partner_coexistence_event(payload, envelope=envelope)
            return
        if payload.get("object") != "whatsapp_business_account":
            return

        routed_values: dict[Any, list[Dict[str, Any]]] = {}
        for entry in payload.get("entry", []):
            waba_id = str(entry.get("id") or "")
            for change in entry.get("changes", []):
                webhook_field = str(change.get("field") or "")
                if webhook_field not in SUPPORTED_META_WEBHOOK_FIELDS:
                    logger.info(
                        "whatsapp.webhook_unsupported_field field=%s",
                        webhook_field[:80],
                    )
                    continue
                value = dict(change.get("value", {}) or {})
                value["_klmWebhookField"] = webhook_field
                value["_klmWabaId"] = waba_id
                metadata = value.get("metadata", {}) or {}
                phone_number_id = str(metadata.get("phone_number_id") or "")
                workspace_id = None
                if phone_number_id:
                    workspace_id = await resolve_whatsapp_webhook_workspace(
                        self.db,
                        phone_number_id=phone_number_id,
                    )
                if workspace_id is None and waba_id:
                    workspace_id = await resolve_whatsapp_template_webhook_workspace(
                        self.db, waba_id=waba_id
                    )
                if workspace_id is None:
                    logger.warning(
                        "whatsapp.webhook_unknown_route field=%s phone_number_ref=%s waba_ref=%s",
                        webhook_field,
                        opaque_ref(phone_number_id, kind="phone_number_id"),
                        opaque_ref(waba_id, kind="waba_id"),
                    )
                    continue
                routed_values.setdefault(workspace_id, []).append(value)

        if not routed_values:
            return
        if len(routed_values) == 1:
            workspace_id, values = next(iter(routed_values.items()))
            await set_workspace_context(self.db, workspace_id)
            await self.process_routed_values(workspace_id, values, envelope=envelope)
            return

        # Meta may batch different phone numbers in one signed delivery. Each
        # tenant gets an independent transaction-bound session so the RLS
        # context is never switched in-place.
        from app.db.session import AsyncSessionLocal
        from app.services.whatsapp_service import WhatsAppService

        for workspace_id, values in routed_values.items():
            async with AsyncSessionLocal() as tenant_db:
                await set_workspace_context(tenant_db, workspace_id)
                tenant_service = WhatsAppService(tenant_db).webhook_service
                await tenant_service.process_routed_values(
                    workspace_id,
                    values,
                    envelope=envelope,
                )
                await tenant_db.commit()

    async def process_routed_values(
        self,
        workspace_id,
        values: List[Dict[str, Any]],
        *,
        envelope: ProviderEnvelopeContext | None = None,
    ) -> None:
        """Process values only after their transaction has a fixed RLS tenant."""
        for value in values:
            await self._handle_value(value, workspace_id, envelope=envelope)

    async def _handle_value(
        self,
        value: dict[str, Any],
        workspace_id,
        *,
        envelope: ProviderEnvelopeContext | None = None,
    ) -> None:
        early_field = str(value.get("_klmWebhookField") or "")
        metadata = value.get("metadata", {}) or {}
        connection = None
        phone_number_id = str(metadata.get("phone_number_id") or "")
        if phone_number_id:
            connection = await self.connection_service.find_connection_by_phone_number_id(
                phone_number_id
            )
        if connection is None:
            waba_id = str(value.get("_klmWabaId") or "")
            if waba_id:
                connection = await self.db.scalar(
                    select(WhatsAppConnection).where(
                        WhatsAppConnection.workspace_id == workspace_id,
                        or_(
                            WhatsAppConnection.waba_id == waba_id,
                            WhatsAppConnection.business_account_id == waba_id,
                        ),
                    )
                )
        operations = MetaWebhookOperationsService(self.db)
        safe_value = {key: item for key, item in value.items() if not key.startswith("_klm")}
        await operations.record(
            workspace_id=workspace_id,
            field=early_field or "messages",
            value=safe_value,
            connection=connection,
            envelope=envelope,
        )
        await operations.apply_connection_projection(
            field=early_field or "messages",
            value=safe_value,
            connection=connection,
        )
        dispatcher = MetaWebhookDispatcher(self.db)
        receipt = await dispatcher.dispatch(
            workspace_id=workspace_id,
            field=early_field or "messages",
            value=safe_value,
            connection=connection,
            envelope=envelope,
        )
        try:
            if early_field in {
                "message_template_quality_update",
                "message_template_components_update",
                "template_correct_category_detection",
            }:
                await self._handle_template_operational_update(
                    early_field,
                    value,
                    workspace_id=workspace_id,
                )
                await dispatcher.complete_native(receipt)
                return
            if early_field in TEMPLATE_WEBHOOK_FIELDS:
                await self._route_template_event(early_field, value, workspace_id=workspace_id)
                await dispatcher.complete_native(receipt)
                return
            if early_field not in {"messages", "message_echoes", "smb_message_echoes"}:
                await dispatcher.complete_native(receipt)
                return
            if connection is None or connection.workspace_id != workspace_id:
                logger.warning(
                    "whatsapp.webhook_unknown_number phone_number_ref=%s display_phone_ref=%s",
                    opaque_ref(metadata.get("phone_number_id"), kind="phone_number_id"),
                    opaque_ref(metadata.get("display_phone_number"), kind="phone"),
                )
                return

            webhook_field = str(value.get("_klmWebhookField") or "messages")
            if webhook_field in {"message_echoes", "smb_message_echoes"}:
                for message in value.get("message_echoes", []):
                    await self._handle_outbound_echo(
                        connection,
                        workspace_id,
                        message,
                        envelope=envelope,
                    )
                await dispatcher.complete_native(receipt)
                return

            for message in value.get("messages", []):
                await self._handle_inbound_message(
                    connection.id,
                    workspace_id,
                    message,
                    value.get("contacts", []),
                    envelope=envelope,
                )
            for status_data in value.get("statuses", []):
                await self._handle_status_update(
                    connection.id,
                    status_data,
                    workspace_id=workspace_id,
                    envelope=envelope,
                )
            await dispatcher.complete_native(receipt)
        except Exception as exc:
            await dispatcher.fail_native(receipt, exc)
            raise

    async def _route_template_event(
        self,
        webhook_field: str,
        value: dict[str, Any],
        *,
        workspace_id,
    ) -> None:
        if webhook_field == TEMPLATE_STATUS_UPDATE_FIELD:
            await self._handle_template_status_update(value, workspace_id=workspace_id)
            return
        if webhook_field == TEMPLATE_CATEGORY_UPDATE_FIELD:
            await self._handle_template_category_update(value, workspace_id=workspace_id)
            return
        logger.warning("whatsapp.webhook_template_event_unhandled_field field=%s", webhook_field)

    async def _find_template_for_event(
        self, value: Dict[str, Any], *, workspace_id
    ) -> WhatsAppMetaTemplate | None:
        external_id = str(value.get("message_template_id") or "").strip()
        name = str(value.get("message_template_name") or "").strip()
        language = str(value.get("message_template_language") or "").strip()
        template: WhatsAppMetaTemplate | None = None
        if external_id:
            template = await self.db.scalar(
                select(WhatsAppMetaTemplate).where(
                    WhatsAppMetaTemplate.workspace_id == workspace_id,
                    WhatsAppMetaTemplate.external_template_id == external_id,
                )
            )
        if template is None and name and language:
            template = await self.db.scalar(
                select(WhatsAppMetaTemplate).where(
                    WhatsAppMetaTemplate.workspace_id == workspace_id,
                    WhatsAppMetaTemplate.name == name,
                    WhatsAppMetaTemplate.language == language,
                )
            )
        if template is None:
            logger.warning(
                "whatsapp.template_webhook_event_unknown_template external_id_ref=%s name=%s language=%s",
                opaque_ref(external_id, kind="meta_template_id"),
                name,
                language,
            )
        return template

    async def _notify_template_approvers(
        self, *, workspace_id, title: str, message: str
    ) -> None:
        """Notify every active workspace_owner/workspace_admin, mirroring the
        pattern already used for Hermes owner handoffs (klm_agent_service)."""
        approver_ids = await self.db.scalars(
            select(WorkspaceMembership.user_id).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.role.in_(
                    (WorkspaceRole.workspace_owner, WorkspaceRole.workspace_admin)
                ),
                WorkspaceMembership.status == MembershipStatus.active,
            )
        )
        notification_service = NotificationService(self.db)
        for user_id in approver_ids.all():
            await notification_service.create(
                user_id,
                title,
                message,
                notification_type=NotificationType.warning,
                workspace_id=workspace_id,
            )

    async def _handle_template_status_update(
        self, value: Dict[str, Any], *, workspace_id
    ) -> None:
        template = await self._find_template_for_event(value, workspace_id=workspace_id)
        if template is None:
            return

        meta_event = str(value.get("event") or "").strip().upper()
        new_status = _META_TEMPLATE_EVENT_TO_STATUS.get(meta_event)
        raw = dict(template.raw_payload or {})
        previous_status = template.status
        previous_reason = raw.get("rejected_reason")
        reason = value.get("reason")

        unchanged = (
            (new_status is None or new_status == previous_status)
            and reason == previous_reason
            and raw.get("last_status_event") == meta_event
        )
        if unchanged:
            # Replay do mesmo evento (retry de Celery/reentrega da Meta):
            # estado já reflete este evento, no-op idempotente.
            return

        if new_status is not None:
            template.status = new_status
        raw["last_status_event"] = meta_event
        raw["last_status_event_at"] = _now().isoformat()
        if meta_event == "REJECTED":
            raw["rejected_reason"] = reason
        elif reason is not None:
            raw["rejected_reason"] = reason
        template.raw_payload = raw
        template.last_synced_at = _now()
        self.db.add(template)

        human_status = {
            "APPROVED": "aprovado",
            "REJECTED": "rejeitado",
            "PENDING": "pendente de análise",
            "PAUSED": "pausado pela Meta",
            "DISABLED": "desabilitado pela Meta",
        }.get(meta_event, meta_event.lower() or "atualizado")
        summary = f"Template Meta '{template.name}' {human_status}."
        if meta_event == "REJECTED":
            summary += f" Motivo: {_translate_rejected_reason(reason)}."

        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_type="webhook",
                actor_label="Meta WhatsApp Business Platform",
                event_type="whatsapp_template_status_update",
                entity_type="whatsapp_meta_template",
                entity_id=template.id,
                summary=summary,
                before_data={"status": previous_status.value if previous_status else None},
                after_data={
                    "status": template.status.value if template.status else None,
                    "metaEvent": meta_event,
                    "reason": reason,
                },
            )
        )
        await self._notify_template_approvers(
            workspace_id=workspace_id,
            title=f"Template {template.name}: {human_status}",
            message=summary,
        )
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="WHATSAPP_TEMPLATE_STATUS_UPDATED",
            data={
                "templateId": str(template.id),
                "status": template.status.value if template.status else None,
                "metaEvent": meta_event,
            },
        )
        await self.db.flush()

    async def _handle_template_operational_update(
        self,
        field: str,
        value: dict[str, Any],
        *,
        workspace_id,
    ) -> None:
        """Project non-status template signals without inventing approval state."""
        template = await self._find_template_for_event(value, workspace_id=workspace_id)
        if template is None:
            return
        raw = dict(template.raw_payload or {})
        updates = dict(raw.get("metaOperationalUpdates") or {})
        safe_payload = sanitize_oss_json(
            {key: item for key, item in value.items() if not key.startswith("_klm")}
        )
        payload_hash = canonical_sha256(safe_payload)
        previous = updates.get(field)
        if isinstance(previous, dict) and previous.get("payloadHash") == payload_hash:
            return
        updates[field] = {
            "payloadHash": payload_hash,
            "receivedAt": _now().isoformat(),
            "payload": safe_payload,
        }
        raw["metaOperationalUpdates"] = updates
        template.raw_payload = raw
        template.last_synced_at = _now()
        self.db.add(template)
        await self.db.flush()

    async def _handle_template_category_update(
        self, value: Dict[str, Any], *, workspace_id
    ) -> None:
        template = await self._find_template_for_event(value, workspace_id=workspace_id)
        if template is None:
            return

        previous_category = str(value.get("previous_category") or template.category or "")
        new_category = str(value.get("new_category") or "").strip() or previous_category
        raw = dict(template.raw_payload or {})

        if (
            template.category == new_category
            and raw.get("previousCategory") == previous_category
        ):
            # Replay idempotente: mesma recategorização já aplicada.
            return

        raw["previousCategory"] = previous_category
        raw["categoryChangedAt"] = _now().isoformat()
        template.category = new_category
        template.raw_payload = raw
        template.last_synced_at = _now()
        self.db.add(template)

        summary = (
            f"Template Meta '{template.name}' recategorizado de "
            f"{previous_category or 'desconhecida'} para {new_category}."
        )
        cost_note = (
            " Atenção: recategorização para marketing pode elevar o custo por "
            "mensagem em até ~7x."
            if new_category == "marketing"
            else ""
        )
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_type="webhook",
                actor_label="Meta WhatsApp Business Platform",
                event_type="whatsapp_template_category_update",
                entity_type="whatsapp_meta_template",
                entity_id=template.id,
                summary=summary + cost_note,
                before_data={"category": previous_category},
                after_data={"category": new_category},
            )
        )
        await self._notify_template_approvers(
            workspace_id=workspace_id,
            title=f"Template {template.name} recategorizado",
            message=summary + cost_note,
        )
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="WHATSAPP_TEMPLATE_CATEGORY_UPDATED",
            data={
                "templateId": str(template.id),
                "previousCategory": previous_category,
                "newCategory": new_category,
            },
        )
        await self.db.flush()

    async def _process_partner_coexistence_event(
        self,
        payload: Dict[str, Any],
        *,
        envelope: ProviderEnvelopeContext | None = None,
    ) -> None:
        data = payload.get("data") or {}
        metadata = data.get("metadata") or {}
        workspace_id = await resolve_whatsapp_webhook_workspace(
            self.db,
            phone_number_id=str(metadata.get("phone_number_id") or ""),
        )
        if workspace_id is None:
            logger.warning("whatsapp.coexistence_event_unknown_number")
            return
        await set_workspace_context(self.db, workspace_id)
        connection = await self.connection_service.find_connection_by_phone_number_id(
            metadata.get("phone_number_id")
        )
        if connection is None or connection.workspace_id != workspace_id:
            return
        event_type = str(payload.get("event") or "")
        await MetaWebhookOperationsService(self.db).record(
            workspace_id=workspace_id,
            field=event_type,
            value=data if isinstance(data, dict) else {},
            connection=connection,
            envelope=envelope,
        )
        dispatcher = MetaWebhookDispatcher(self.db)
        receipt = await dispatcher.dispatch(
            workspace_id=workspace_id,
            field=event_type,
            value=data if isinstance(data, dict) else {},
            connection=connection,
            envelope=envelope,
        )
        try:
            if event_type == "smb_app_state_sync":
                for item in data.get("state_sync", []):
                    contact_data = item.get("contact") or {}
                    phone = _normalize_wa_phone(contact_data.get("phone_number"))
                    if not phone:
                        continue
                    name = str(
                        contact_data.get("full_name") or contact_data.get("first_name") or ""
                    ).strip() or None
                    await self._get_or_create_contact(phone, workspace_id, connection.id, name)
                if connection.coexistence_sync_status not in {"completed", "failed"}:
                    connection.coexistence_sync_status = "contacts_received"
                await dispatcher.complete_native(receipt)
                await self.db.commit()
                return
            if event_type != "history":
                await dispatcher.complete_native(receipt)
                return
            business_phone = "".join(
                character
                for character in str(metadata.get("display_phone_number") or "")
                if character.isdigit()
            )
            history_failed = False
            for history in data.get("history", []):
                if history.get("errors"):
                    history_failed = True
                    connection.last_error_code = "WA-COEX-HISTORY"
                    connection.last_error_message = str(history.get("errors"))[:2000]
                    continue
                for thread in history.get("threads", []):
                    thread_phone = str(thread.get("id") or "").strip()
                    for message in thread.get("messages", []):
                        sender = "".join(
                            character
                            for character in str(message.get("from") or "")
                            if character.isdigit()
                        )
                        outbound = bool(business_phone and sender == business_phone)
                        await self._ingest_coexistence_message(
                            connection=connection,
                            workspace_id=workspace_id,
                            message_data=message,
                            contact_phone=(
                                str(message.get("to") or thread_phone)
                                if outbound
                                else str(message.get("from") or thread_phone)
                            ),
                            outbound=outbound,
                            historical=True,
                            envelope=envelope,
                        )
            if history_failed:
                connection.coexistence_sync_status = "partial"
            else:
                connection.coexistence_sync_status = "completed"
                connection.last_error_code = None
                connection.last_error_message = None
            await dispatcher.complete_native(receipt)
            await self.db.commit()
        except Exception as exc:
            await dispatcher.fail_native(receipt, exc)
            raise

    async def _handle_outbound_echo(
        self,
        connection: WhatsAppConnection,
        workspace_id,
        message_data: Dict[str, Any],
        *,
        envelope: ProviderEnvelopeContext | None = None,
    ) -> None:
        await self._ingest_coexistence_message(
            connection=connection,
            workspace_id=workspace_id,
            message_data=message_data,
            contact_phone=str(message_data.get("to") or ""),
            outbound=True,
            historical=False,
            envelope=envelope,
        )
        await self.db.commit()

    async def _ingest_coexistence_message(
        self,
        *,
        connection: WhatsAppConnection,
        workspace_id,
        message_data: Dict[str, Any],
        contact_phone: str,
        outbound: bool,
        historical: bool,
        envelope: ProviderEnvelopeContext | None = None,
    ) -> None:
        wamid = str(message_data.get("id") or "").strip()
        phone = _normalize_wa_phone(contact_phone)
        if not wamid or not phone:
            return
        parsed = _parse_message_content(message_data)
        existing = await self.db.scalar(select(WhatsAppMessage).where(WhatsAppMessage.wamid == wamid))
        if existing is not None:
            existing_contact = await self.db.get(WhatsAppContact, existing.contact_id)
            if (
                existing.whatsapp_connection_id != connection.id
                or existing_contact is None
                or existing_contact.workspace_id != workspace_id
            ):
                logger.error(
                    "whatsapp.coexistence_wamid_binding_conflict message_ref=%s",
                    opaque_ref(wamid, kind="whatsapp_message"),
                )
                return
            await self._record_existing_message_observation(
                connection=connection,
                workspace_id=workspace_id,
                legacy_message=existing,
                message_data=message_data,
                parsed=parsed,
                envelope=envelope,
            )
            return
        timestamp_value = int(message_data.get("timestamp") or 0)
        occurred_at = (
            datetime.fromtimestamp(timestamp_value, tz=timezone.utc)
            if timestamp_value
            else datetime.now(timezone.utc)
        )
        contact = await self._get_or_create_contact(phone, workspace_id, connection.id, None)
        message = WhatsAppMessage(
            contact_id=contact.id,
            whatsapp_connection_id=connection.id,
            wamid=wamid,
            direction=(WaMessageDirection.outbound if outbound else WaMessageDirection.inbound),
            status=(WaMessageStatus.sent if outbound else WaMessageStatus.delivered),
            body=parsed.body,
            media_type=parsed.media_type,
            timestamp=occurred_at,
            message_metadata={
                "originSurface": "business_app" if outbound else "customer",
                "transportMode": "coexistence",
                "coexistenceEcho": outbound and not historical,
                "historySync": historical,
                "billableByMeta": False if outbound else None,
                "providerType": message_data.get("type"),
                "structuredContent": parsed.structured_content or {},
            },
        )
        try:
            async with self.db.begin_nested():
                self.db.add(message)
                await self.db.flush()
        except IntegrityError:
            existing = await self.db.scalar(
                select(WhatsAppMessage).where(WhatsAppMessage.wamid == wamid)
            )
            if existing is None:
                raise
            existing_contact = await self.db.get(WhatsAppContact, existing.contact_id)
            if (
                existing.whatsapp_connection_id != connection.id
                or existing_contact is None
                or existing_contact.workspace_id != workspace_id
            ):
                logger.error(
                    "whatsapp.coexistence_wamid_binding_conflict message_ref=%s",
                    opaque_ref(wamid, kind="whatsapp_message"),
                )
            return
        from app.services.omnichannel_legacy_bridge_service import (
            OmnichannelLegacyBridgeService,
        )

        canonical = await OmnichannelLegacyBridgeService(self.db).ingest_whatsapp_message(
            workspace_id=workspace_id,
            connection=connection,
            contact=contact,
            message=message,
            body=parsed.body,
        )
        provider_event_type = (
            "deleted"
            if _is_provider_delete_signal(message_data)
            else "provider_unsupported"
            if (parsed.structured_content or {}).get("kind") == "unsupported"
            else "provider_receipt"
        )
        await self._record_provider_observation(
            connection=connection,
            workspace_id=workspace_id,
            legacy_message=message,
            message_data=message_data,
            parsed=parsed,
            envelope=envelope,
            event_type=provider_event_type,
            observation_state=(
                "provider_signal"
                if provider_event_type in {"deleted", "provider_unsupported"}
                else "observed"
            ),
        )
        if outbound and not historical:
            contact.queue_status = WaQueueStatus.human
            contact.human_control_state = HumanControlState.manual_pause
            thread = canonical.resolution.thread
            policy = dict(thread.policy_state or {})
            policy.update(
                {
                    "aiPaused": True,
                    "humanControlState": "manual_pause",
                    "pauseReason": "whatsapp_business_app_echo",
                    "pausedAt": datetime.now(timezone.utc).isoformat(),
                }
            )
            thread.policy_state = policy
            self.db.add(
                KlmAuditEvent(
                    workspace_id=workspace_id,
                    actor_type="human",
                    actor_label="Equipe pelo WhatsApp Business App",
                    event_type="whatsapp_coexistence_human_reply",
                    entity_type="omnichannel_thread",
                    entity_id=thread.id,
                    summary="Resposta humana pelo aplicativo pausou a IA nesta conversa.",
                    after_data={"humanControlState": "manual_pause", "messageId": str(message.id)},
                )
            )

    async def _record_ctwa_acquisition(
        self,
        *,
        workspace_id,
        wamid: str,
        referral: dict[str, str],
        thread_id: UUID | None,
        happened_at: datetime,
    ) -> None:
        """Persist the ad referral on the canonical thread and emit conversation_started."""
        from app.schemas.klm_operations import AcquisitionEventCreate
        from app.services.klm_operations_service import KlmOperationsService

        ctwa_clid = referral.get("ctwa_clid")
        click_ids = {"ctwa_clid": ctwa_clid} if ctwa_clid else {}

        if thread_id is not None:
            thread = await self.db.get(OmnichannelThread, thread_id)
            if thread is not None and thread.workspace_id == workspace_id:
                metadata = dict(thread.thread_metadata or {})
                acquisition = {
                    "provider": "meta",
                    "source": referral.get("source_type") or "ctwa",
                    "ad": referral.get("source_id"),
                    "clickIds": click_ids,
                    "sourceUrl": referral.get("source_url"),
                    "headline": referral.get("headline"),
                    "body": referral.get("body"),
                }
                metadata.setdefault("acquisition", acquisition)
                metadata["acquisitionTouches"] = list(
                    {
                        str(item.get("wamid")): item
                        for item in [
                            *(metadata.get("acquisitionTouches") or []),
                            {**acquisition, "wamid": wamid},
                        ]
                        if isinstance(item, dict)
                    }.values()
                )
                thread.thread_metadata = metadata

        event_metadata: dict[str, Any] = {}
        if referral.get("source_id"):
            event_metadata["adId"] = referral["source_id"]
        if referral.get("source_url"):
            event_metadata["sourceUrl"] = referral["source_url"]
        if referral.get("source_type"):
            event_metadata["sourceType"] = referral["source_type"]
        if referral.get("headline"):
            event_metadata["headline"] = referral["headline"]
        if referral.get("body"):
            event_metadata["body"] = referral["body"]
        if thread_id is not None:
            event_metadata["threadId"] = str(thread_id)

        await KlmOperationsService(self.db).record_acquisition_event(
            workspace_id,
            None,
            AcquisitionEventCreate(
                event_name="conversation_started",
                channel="whatsapp",
                click_ids=click_ids,
                event_metadata=event_metadata,
                dedupe_key=f"ctwa:{wamid}",
                happened_at=happened_at,
            ),
        )

    async def _handle_inbound_message(
        self,
        connection_id,
        workspace_id,
        msg_data: Dict[str, Any],
        contacts_data: List[Dict[str, Any]],
        *,
        envelope: ProviderEnvelopeContext | None = None,
    ) -> None:
        wamid = str(msg_data.get("id") or "").strip()
        if not wamid:
            logger.warning("whatsapp.webhook_message_without_wamid")
            return
        # Identidade de 2026: telefone, BSUID e username convivem, e o telefone
        # pode faltar. A extração casa o bloco `contacts` por wa_id OU user_id,
        # em vez de assumir que o `from` sempre existe.
        identity = extract_inbound_identity(msg_data, contacts_data)
        phone = _normalize_wa_phone(msg_data.get("from")) or (identity.phone or "")
        msg_type = str(msg_data.get("type") or "text")
        provider_msg_type = msg_type.strip().lower()
        connection = await self.connection_service.get_connection(connection_id, workspace_id)

        contact_name = identity.display_name

        parsed = _parse_message_content(msg_data)
        body = parsed.body
        source_media_id = parsed.source_media_id
        media_type = parsed.media_type
        referral_data = _parse_referral(msg_data)

        ts_unix = int(msg_data.get("timestamp", 0))
        timestamp = (
            datetime.fromtimestamp(ts_unix, tz=timezone.utc)
            if ts_unix
            else datetime.now(timezone.utc)
        )

        message = await self.db.scalar(
            select(WhatsAppMessage)
            .where(WhatsAppMessage.wamid == wamid)
            .execution_options(populate_existing=True)
        )
        if message is not None:
            if (
                message.whatsapp_connection_id != connection_id
                or message.direction != WaMessageDirection.inbound
            ):
                logger.error(
                    "whatsapp.webhook_wamid_binding_conflict message_ref=%s",
                    opaque_ref(wamid, kind="whatsapp_message"),
                )
                return
            contact = await self.db.get(WhatsAppContact, message.contact_id)
            if contact is None or contact.workspace_id != workspace_id:
                logger.error(
                    "whatsapp.webhook_wamid_tenant_conflict message_ref=%s",
                    opaque_ref(wamid, kind="whatsapp_message"),
                )
                return
            mutation_recorded = await self._record_existing_message_observation(
                connection=connection,
                workspace_id=workspace_id,
                legacy_message=message,
                message_data=msg_data,
                parsed=parsed,
                envelope=envelope,
            )
            if mutation_recorded:
                await self.db.commit()
                return
            pipeline = dict(message.inbound_pipeline or {})
            provider_context = msg_data.get("context")
            if isinstance(provider_context, dict) and provider_context:
                existing_metadata = dict(message.message_metadata or {})
                existing_metadata["providerContext"] = dict(provider_context)
                message.message_metadata = existing_metadata
                provider_context_id = str(provider_context.get("id") or "").strip()
                if provider_context_id:
                    pipeline.setdefault("providerContextId", provider_context_id)
            stored_referral = (message.message_metadata or {}).get("referral")
            if referral_data is None and isinstance(stored_referral, dict):
                referral_data = {
                    field: str(stored_referral.get(field) or "").strip()
                    for field in _REFERRAL_FIELDS
                    if str(stored_referral.get(field) or "").strip()
                } or None
            elif referral_data is not None and not isinstance(stored_referral, dict):
                existing_metadata = dict(message.message_metadata or {})
                existing_metadata["referral"] = dict(referral_data)
                message.message_metadata = existing_metadata
            source_media_id = str(pipeline.get("sourceMediaId") or "").strip() or None
            media_type = message.media_type
            state = str(pipeline.get("state") or "accepted")
            if state in {"completed", "reconciliation_required"} and not (
                (media_type and _media_reconciliation_pending(pipeline))
                or _provider_reply_context_pending(pipeline)
            ):
                await self.db.commit()
                return
            body = message.body
            msg_type = media_type or msg_type
        else:
            try:
                contact = await self._get_or_create_contact(
                    phone,
                    workspace_id,
                    connection_id,
                    contact_name,
                    identity=identity,
                )
                contact.last_seen = timestamp
                message = WhatsAppMessage(
                    contact_id=contact.id,
                    whatsapp_connection_id=connection_id,
                    wamid=wamid,
                    direction=WaMessageDirection.inbound,
                    status=WaMessageStatus.delivered,
                    body=body,
                    media_url=None,
                    media_type=media_type,
                    message_metadata={
                        "originSurface": "customer",
                        "transportMode": getattr(
                            connection, "transport_mode", "official_api"
                        ),
                        "structuredContent": parsed.structured_content or {},
                        "providerType": msg_type,
                        "providerContext": (
                            dict(msg_data.get("context") or {})
                            if isinstance(msg_data.get("context"), dict)
                            else {}
                        ),
                        **({"referral": dict(referral_data)} if referral_data else {}),
                    },
                    timestamp=timestamp,
                    inbound_pipeline={
                        "state": "accepted",
                        "attempt": 0,
                        "sourceMediaId": source_media_id,
                        "providerContextId": (
                            str((msg_data.get("context") or {}).get("id") or "").strip()
                            if isinstance(msg_data.get("context"), dict)
                            else None
                        )
                        or None,
                        "mediaStorage": (
                            _unreconciled_media_state(source_media_id=source_media_id)
                            if media_type
                            else None
                        ),
                    },
                )
                self.db.add(message)
                await self.db.commit()
            except IntegrityError:
                await self.db.rollback()
                concurrent_message_id = await self.db.scalar(
                    select(WhatsAppMessage.id).where(WhatsAppMessage.wamid == wamid)
                )
                if concurrent_message_id is None:
                    raise
                await self._handle_inbound_message(
                    connection_id,
                    workspace_id,
                    msg_data,
                    contacts_data,
                    envelope=envelope,
                )
                return
            logger.info(
                "whatsapp.message_received workspace_id=%s message_id=%s contact_id=%s phone_ref=%s",
                workspace_id,
                message.id,
                contact.id,
                opaque_ref(phone, kind="phone"),
            )

        await self._maybe_register_inbound_opt_out(
            message=message,
            contact=contact,
            workspace_id=workspace_id,
            wamid=wamid,
            body=body,
            provider_msg_type=provider_msg_type,
        )

        # A customer reply is the durable interruption signal for sequence
        # stages. It runs after the inbound message has been persisted, so a
        # retry of the provider webhook remains idempotent and auditable.
        from app.services.flow_sequence_service import interrupt_sequences_on_inbound

        await interrupt_sequences_on_inbound(
            self.db,
            workspace_id=workspace_id,
            phone=phone or getattr(contact, "phone", None),
            inbound_message_id=message.id,
        )
        await self.db.commit()

        lease = await self._acquire_inbound_pipeline_lease(
            message.id,
            workspace_id=workspace_id,
        )
        if lease is None:
            return
        message = await self.db.scalar(
            select(WhatsAppMessage)
            .where(WhatsAppMessage.id == message.id)
            .execution_options(populate_existing=True)
        )
        if message is None:
            raise InboundPipelineLeaseLostError(
                "inbound message disappeared after lease acquisition"
            )

        pipeline = dict(message.inbound_pipeline or {})
        provider_context = msg_data.get("context")
        if isinstance(provider_context, dict):
            provider_context_id = str(provider_context.get("id") or "").strip()
            if provider_context_id:
                pipeline.setdefault("providerContextId", provider_context_id)
        state = str(pipeline.get("state") or "accepted")
        if state in {"completed", "reconciliation_required"} and not (
            (media_type and _media_reconciliation_pending(pipeline))
            or _provider_reply_context_pending(pipeline)
        ):
            return
        if state in {"automation_dispatching", "escalation_dispatching"}:
            await self._checkpoint_inbound_pipeline(
                message,
                lease,
                "reconciliation_required",
                reconciliationRequired=True,
                uncertainStage=state,
            )
            logger.warning(
                "whatsapp.inbound_reconciliation_required message_id=%s stage=%s",
                message.id,
                state,
            )
            await self._relinquish_inbound_pipeline_lease(message.id, lease)
            return

        resolved_media_key = None
        media_checkpoint: dict[str, object] = {}
        if media_type:
            from app.services.sdr_intake_service import resolve_inbound_media_storage

            resolution = await resolve_inbound_media_storage(
                db=self.db,
                workspace_id=workspace_id,
                candidate_reference=message.media_url,
            )
            resolved_media_key = resolution.storage_key
            message.media_url = resolved_media_key
            media_storage = resolution.pipeline_state()
            if resolution.reconciliation_required and not source_media_id:
                media_storage = _unavailable_media_state("source_media_id_missing")
            media_checkpoint["mediaStorage"] = media_storage
            if media_storage["reconciliationRequired"] is True:
                scheduled_at = datetime.now(timezone.utc)
                await self._checkpoint_inbound_pipeline(
                    message,
                    lease,
                    "reconciliation_required",
                    reconciliationRequired=True,
                    reconciliationKind="media_storage",
                    mediaRetry=_scheduled_media_retry(
                        pipeline,
                        now=scheduled_at,
                    ),
                    **media_checkpoint,
                )
                await self._relinquish_inbound_pipeline_lease(message.id, lease)
                logger.info(
                    "whatsapp.inbound_media_reconciliation_scheduled workspace_id=%s message_id=%s",
                    workspace_id,
                    message.id,
                )
                return
            if pipeline.get("reconciliationKind") == "media_storage":
                media_checkpoint.update(
                    reconciliationKind=None,
                    mediaRetry={
                        "state": "resolved",
                        "attempt": int((pipeline.get("mediaRetry") or {}).get("attempt", 0) or 0),
                        "completedAt": datetime.now(timezone.utc).isoformat(),
                    },
                )

        pipeline = await self._checkpoint_inbound_pipeline(
            message,
            lease,
            "processing",
            attempt=int(pipeline.get("attempt", 0) or 0) + 1,
            reconciliationRequired=False,
            **media_checkpoint,
        )

        inbound_text_for_sdr = message.transcript or body
        media_ai_state = dict(pipeline.get("mediaAI") or {})
        wa_access_token = None
        if connection.phone_number_id and connection.connection_type != ConnectionType.QR_CODE:
            try:
                wa_access_token = self.connection_service.get_meta_access_token(connection)
            except Exception:
                wa_access_token = None

        if (
            external_delivery_allowed()
            and msg_type in {"audio", "voice"}
            and source_media_id
            and wa_access_token
            and not message.transcript
            and not media_ai_state
        ):
            try:
                from app.services.transcription_service import get_transcription_service

                transcription_service = get_transcription_service()
                audio_bytes, audio_mime = await transcription_service.download_whatsapp_media(
                    source_media_id,
                    wa_access_token,
                )
                transcript = await transcription_service.transcribe_audio(
                    audio_bytes,
                    audio_mime,
                    workspace_id=workspace_id,
                    source_ref=str(message.id),
                    allow_fallback=False,
                )
                message.transcript = transcript
                inbound_text_for_sdr = transcript
                await self.db.commit()
                pipeline = await self._checkpoint_inbound_pipeline(
                    message,
                    lease,
                    "processing",
                    mediaAI={
                        "kind": "audio",
                        "status": "completed",
                        "requestStarted": True,
                        "reconciliationRequired": False,
                    },
                )
            except Exception as exc:
                request_started = bool(getattr(exc, "request_started", False))
                billing_unknown = bool(getattr(exc, "billing_unknown", False))
                pipeline = await self._checkpoint_inbound_pipeline(
                    message,
                    lease,
                    "processing",
                    mediaAI={
                        "kind": "audio",
                        "status": (
                            "reconciliation_required"
                            if request_started and billing_unknown
                            else "failed_known"
                            if request_started
                            else "not_started"
                        ),
                        "requestStarted": request_started,
                        "billingUnknown": billing_unknown,
                        "reconciliationRequired": request_started and billing_unknown,
                        "errorType": safe_error_type(exc),
                    },
                )
                logger.warning(
                    "whatsapp.audio_transcription_failed message_id=%s error_type=%s",
                    message.id,
                    safe_error_type(exc),
                )
        elif (
            external_delivery_allowed()
            and msg_type == "image"
            and source_media_id
            and wa_access_token
            and not message.image_transcript
            and not media_ai_state
        ):
            try:
                from app.services.transcription_service import get_transcription_service

                transcription_service = get_transcription_service()
                image_bytes, image_mime = await transcription_service.download_whatsapp_media(
                    source_media_id,
                    wa_access_token,
                )
                image_transcript = await transcription_service.transcribe_image(
                    image_bytes,
                    image_mime,
                    workspace_id=workspace_id,
                    source_ref=str(message.id),
                    allow_fallback=False,
                )
                message.image_transcript = image_transcript
                await self.db.commit()
                pipeline = await self._checkpoint_inbound_pipeline(
                    message,
                    lease,
                    "processing",
                    mediaAI={
                        "kind": "image",
                        "status": "completed",
                        "requestStarted": True,
                        "reconciliationRequired": False,
                    },
                )
            except Exception as exc:
                request_started = bool(getattr(exc, "request_started", False))
                billing_unknown = bool(getattr(exc, "billing_unknown", False))
                pipeline = await self._checkpoint_inbound_pipeline(
                    message,
                    lease,
                    "processing",
                    mediaAI={
                        "kind": "image",
                        "status": (
                            "reconciliation_required"
                            if request_started and billing_unknown
                            else "failed_known"
                            if request_started
                            else "not_started"
                        ),
                        "requestStarted": request_started,
                        "billingUnknown": billing_unknown,
                        "reconciliationRequired": request_started and billing_unknown,
                        "errorType": safe_error_type(exc),
                    },
                )
                logger.warning(
                    "whatsapp.image_transcription_failed message_id=%s error_type=%s",
                    message.id,
                    safe_error_type(exc),
                )

        if not pipeline.get("canonicalCompleted"):
            try:
                from app.services.omnichannel_legacy_bridge_service import (
                    OmnichannelLegacyBridgeService,
                )

                canonical = await OmnichannelLegacyBridgeService(self.db).ingest_whatsapp_message(
                    workspace_id=workspace_id,
                    connection=connection,
                    contact=contact,
                    message=message,
                    body=inbound_text_for_sdr or body,
                )
                pipeline = await self._checkpoint_inbound_pipeline(
                    message,
                    lease,
                    "canonical_completed",
                    canonicalCompleted=True,
                    canonicalIdentityId=str(canonical.resolution.identity.id),
                    canonicalThreadId=str(canonical.resolution.thread.id),
                    canonicalEventId=str(canonical.event_id),
                    canonicalMessageId=(
                        str(canonical.message_id) if canonical.message_id else None
                    ),
                )
            except InboundPipelineLeaseLostError:
                raise
            except Exception as exc:
                logger.error(
                    "whatsapp.canonical_ingest_failed workspace_id=%s message_id=%s error_type=%s",
                    workspace_id,
                    message.id,
                    safe_error_type(exc),
                )
                await self._checkpoint_inbound_pipeline(
                    message,
                    lease,
                    "canonical_retryable",
                    canonicalLastErrorType=safe_error_type(exc),
                )
                await self._relinquish_inbound_pipeline_lease(message.id, lease)
                raise

        if referral_data and not pipeline.get("acquisitionCompleted"):
            try:
                await self._record_ctwa_acquisition(
                    workspace_id=workspace_id,
                    wamid=wamid,
                    referral=referral_data,
                    thread_id=_optional_uuid(pipeline.get("canonicalThreadId")),
                    happened_at=message.timestamp,
                )
                pipeline = await self._checkpoint_inbound_pipeline(
                    message,
                    lease,
                    "acquisition_completed",
                    acquisitionCompleted=True,
                )
            except InboundPipelineLeaseLostError:
                raise
            except Exception as exc:
                logger.error(
                    "whatsapp.ctwa_acquisition_failed workspace_id=%s message_id=%s error_type=%s",
                    workspace_id,
                    message.id,
                    safe_error_type(exc),
                )
                await self._checkpoint_inbound_pipeline(
                    message,
                    lease,
                    "acquisition_retryable",
                    acquisitionLastErrorType=safe_error_type(exc),
                )
                await self._relinquish_inbound_pipeline_lease(message.id, lease)
                raise

        provider_context = msg_data.get("context")
        provider_context = provider_context if isinstance(provider_context, dict) else {}
        provider_context_id = str(provider_context.get("id") or "").strip()
        if provider_context_id and not pipeline.get("contextCompleted"):
            source_message_id = _optional_uuid(pipeline.get("canonicalMessageId"))
            canonical_thread_id = _optional_uuid(pipeline.get("canonicalThreadId"))
            reference = None
            if source_message_id is not None and canonical_thread_id is not None:
                from app.services.klm_operations_service import KlmOperationsService

                reference = await KlmOperationsService(
                    self.db
                ).resolve_provider_reply_context(
                    workspace_id=workspace_id,
                    thread_id=canonical_thread_id,
                    source_message_id=source_message_id,
                    provider_message_id=provider_context_id,
                    connection_id=None,
                    correlation_id=f"{wamid}:{provider_context_id}",
                )
            pipeline = await self._checkpoint_inbound_pipeline(
                message,
                lease,
                "context_completed" if reference is not None else "context_unresolved",
                providerContextId=provider_context_id,
                contextCompleted=reference is not None,
                contextResolutionState=(
                    "resolved_same_thread" if reference is not None else "unresolved_same_thread"
                ),
                contextReferenceId=str(reference.id) if reference is not None else None,
                contextTargetMessageId=(
                    str(reference.target_message_id) if reference is not None else None
                ),
            )

        if not pipeline.get("evidenceCompleted"):
            provider_event_type = (
                "deleted"
                if _is_provider_delete_signal(msg_data)
                else "provider_unsupported"
                if (parsed.structured_content or {}).get("kind") == "unsupported"
                else "provider_receipt"
            )
            recorded = await self._record_provider_observation(
                connection=connection,
                workspace_id=workspace_id,
                legacy_message=message,
                message_data=msg_data,
                parsed=parsed,
                envelope=envelope,
                event_type=provider_event_type,
                observation_state=(
                    "provider_signal"
                    if provider_event_type in {"deleted", "provider_unsupported"}
                    else "observed"
                ),
            )
            if recorded:
                pipeline = await self._checkpoint_inbound_pipeline(
                    message,
                    lease,
                    "evidence_completed",
                    evidenceCompleted=True,
                )

        if not pipeline.get("campaignFlowCompleted"):
            pipeline = await self._checkpoint_inbound_pipeline(
                message,
                lease,
                "campaign_flow_dispatching",
                campaignFlowDispatchStarted=True,
            )
            canonical_thread_id = None
            try:
                raw_thread_id = str(pipeline.get("canonicalThreadId") or "").strip()
                canonical_thread_id = UUID(raw_thread_id) if raw_thread_id else None
            except ValueError:
                canonical_thread_id = None
            flow_run_id = await self.campaign_service.trigger_follow_up_flow_on_reply(
                workspace_id=workspace_id,
                phone=phone,
                matter_id=contact.matter_id,
                thread_id=canonical_thread_id,
            )
            pipeline = await self._checkpoint_inbound_pipeline(
                message,
                lease,
                "campaign_flow_completed",
                campaignFlowCompleted=True,
                campaignFlowRunId=str(flow_run_id) if flow_run_id else None,
            )

        if contact.matter_id and not pipeline.get("ragCompleted"):
            pipeline = await self._checkpoint_inbound_pipeline(
                message,
                lease,
                "rag_dispatching",
                ragDispatchStarted=True,
            )
            try:
                from app.services.knowledge_service import KnowledgeService

                rag_body = inbound_text_for_sdr or body
                await KnowledgeService(self.db).index_whatsapp_message(
                    workspace_id=workspace_id,
                    matter_id=contact.matter_id,
                    source_ref=str(message.id),
                    body=rag_body,
                    phone=phone,
                    contact_name=contact_name,
                    media_type=media_type if msg_type in {"audio", "voice", "image"} else None,
                )
                pipeline = await self._checkpoint_inbound_pipeline(
                    message,
                    lease,
                    "rag_completed",
                    ragCompleted=True,
                )
            except InboundPipelineLeaseLostError:
                raise
            except Exception as exc:
                logger.error(
                    "whatsapp.rag_index_failed matter_id=%s message_id=%s error_type=%s",
                    contact.matter_id,
                    message.id,
                    safe_error_type(exc),
                )
                await self._checkpoint_inbound_pipeline(
                    message,
                    lease,
                    "rag_retryable",
                    ragLastErrorType=safe_error_type(exc),
                )
                await self._relinquish_inbound_pipeline_lease(message.id, lease)
                raise

        if external_delivery_allowed():
            if not pipeline.get("sdrCompleted"):
                pipeline = await self._checkpoint_inbound_pipeline(
                    message,
                    lease,
                    "sdr_dispatching",
                    sdrDispatchStarted=True,
                )
                try:
                    from app.services.sdr_intake_service import handle_inbound_lead

                    await handle_inbound_lead(
                        db=self.db,
                        contact_id=contact.id,
                        phone=phone,
                        contact_name=contact_name,
                        message_body=inbound_text_for_sdr,
                        media_s3_key=resolved_media_key,
                        wamid=wamid,
                    )
                    pipeline = await self._checkpoint_inbound_pipeline(
                        message,
                        lease,
                        "sdr_completed",
                        sdrCompleted=True,
                    )
                except InboundPipelineLeaseLostError:
                    raise
                except Exception as exc:
                    logger.error(
                        "whatsapp_service.sdr_intake_error contact_id=%s phone_ref=%s error_type=%s",
                        contact.id,
                        opaque_ref(phone, kind="phone"),
                        safe_error_type(exc),
                    )
                    await self._checkpoint_inbound_pipeline(
                        message,
                        lease,
                        "sdr_retryable",
                        sdrLastErrorType=safe_error_type(exc),
                    )
                    await self._relinquish_inbound_pipeline_lease(message.id, lease)
                    raise
        else:
            logger.info(
                "whatsapp.inbound_recorded_without_external_automation workspace_id=%s message_id=%s",
                workspace_id,
                message.id,
            )

        await self.db.refresh(contact)
        if not pipeline.get("automationCompleted"):
            pipeline = await self._checkpoint_inbound_pipeline(
                message,
                lease,
                "automation_dispatching",
                automationDispatchStarted=True,
            )
            automation_completed = await self._apply_automation_rules(
                contact,
                body or inbound_text_for_sdr or "",
                message=message,
                lease=lease,
            )
            if not automation_completed:
                await self._relinquish_inbound_pipeline_lease(message.id, lease)
                return
            pipeline = await self._checkpoint_inbound_pipeline(
                message,
                lease,
                "automation_completed",
                automationCompleted=True,
            )

        if not pipeline.get("escalationCompleted"):
            pipeline = await self._checkpoint_inbound_pipeline(
                message,
                lease,
                "escalation_dispatching",
                escalationDispatchStarted=True,
            )
            await self._maybe_escalate_to_human(contact, body or inbound_text_for_sdr or "")
        await self._checkpoint_inbound_pipeline(
            message,
            lease,
            "completed",
            escalationCompleted=True,
            completedAt=datetime.now(timezone.utc).isoformat(),
        )

    async def _resume_inbound_pipeline(
        self,
        *,
        message: WhatsAppMessage,
        contact: WhatsAppContact,
        workspace_id,
    ) -> None:
        message_type = message.media_type or "text"
        replay: dict[str, Any] = {
            "id": message.wamid,
            "from": contact.phone,
            "timestamp": str(int(message.timestamp.timestamp())),
            "type": message_type,
        }
        if message.media_type:
            pipeline = dict(message.inbound_pipeline or {})
            source_media_id = str(pipeline.get("sourceMediaId") or "").strip() or None
            source_payload: dict[str, Any] = {"id": source_media_id} if source_media_id else {}
            if message.body and message.body != f"[{message.media_type}]":
                source_payload["caption"] = message.body
            replay[message.media_type] = source_payload
        else:
            replay["text"] = {"body": message.body}
        provider_context = (message.message_metadata or {}).get("providerContext")
        if isinstance(provider_context, dict) and provider_context.get("id"):
            replay["context"] = dict(provider_context)

        await self._handle_inbound_message(
            message.whatsapp_connection_id,
            workspace_id,
            replay,
            [
                {
                    "wa_id": contact.phone,
                    "profile": {"name": contact.name},
                }
            ],
        )

    async def retry_inbound_media(
        self,
        *,
        workspace_id,
        message_id,
        uploader=None,
    ) -> bool:
        message = await self.db.scalar(
            select(WhatsAppMessage)
            .where(
                WhatsAppMessage.id == message_id,
                WhatsAppMessage.direction == WaMessageDirection.inbound,
            )
            .execution_options(populate_existing=True)
        )
        if message is None or not message.media_type:
            return False
        pipeline = dict(message.inbound_pipeline or {})
        if not _media_reconciliation_pending(pipeline):
            return False

        contact = await self.db.get(WhatsAppContact, message.contact_id)
        if contact is None or contact.workspace_id != workspace_id:
            return False
        lease = await self._acquire_inbound_pipeline_lease(
            message.id,
            workspace_id=workspace_id,
        )
        if lease is None:
            return False
        message = await self.db.scalar(
            select(WhatsAppMessage)
            .where(WhatsAppMessage.id == message_id)
            .execution_options(populate_existing=True)
        )
        if message is None:
            raise InboundPipelineLeaseLostError(
                "inbound message disappeared during media reconciliation"
            )
        pipeline = dict(message.inbound_pipeline or {})
        if not _media_reconciliation_pending(pipeline):
            await self._relinquish_inbound_pipeline_lease(message.id, lease)
            return False

        retry = pipeline.get("mediaRetry")
        retry = dict(retry) if isinstance(retry, dict) else {}
        retry_attempt = int(retry.get("attempt", 0) or 0) + 1
        started_at = datetime.now(timezone.utc)
        await self._checkpoint_inbound_pipeline(
            message,
            lease,
            "media_reconciling",
            reconciliationRequired=True,
            reconciliationKind="media_storage",
            mediaRetry={
                **retry,
                "state": "running",
                "attempt": retry_attempt,
                "lastAttemptAt": started_at.isoformat(),
            },
        )

        from app.services.sdr_intake_service import (
            resolve_inbound_media_storage,
            upload_media_to_s3,
        )

        source_media_id = str(pipeline.get("sourceMediaId") or "").strip() or None
        resolution = await resolve_inbound_media_storage(
            db=self.db,
            workspace_id=workspace_id,
            candidate_reference=message.media_url,
        )
        error_type = None
        if resolution.reconciliation_required and source_media_id:
            media_uploader = uploader or upload_media_to_s3
            try:
                candidate_storage_key = await media_uploader(
                    media_id=source_media_id,
                    media_type=message.media_type,
                    contact_id=contact.id,
                    matter_id=contact.matter_id,
                    db=self.db,
                    workspace_id=workspace_id,
                    actor_user_id=None,
                )
            except Exception as exc:
                error_type = safe_error_type(exc)
                candidate_storage_key = None
            if candidate_storage_key:
                resolution = await resolve_inbound_media_storage(
                    db=self.db,
                    workspace_id=workspace_id,
                    candidate_reference=candidate_storage_key,
                )
        elif resolution.reconciliation_required:
            resolution = None

        if resolution is None:
            media_storage = _unavailable_media_state("source_media_id_missing")
        else:
            media_storage = resolution.pipeline_state()

        if media_storage["reconciliationRequired"] is True:
            await self._checkpoint_inbound_pipeline(
                message,
                lease,
                "reconciliation_required",
                reconciliationRequired=True,
                reconciliationKind="media_storage",
                mediaStorage=media_storage,
                mediaRetry=_scheduled_media_retry(
                    pipeline,
                    now=datetime.now(timezone.utc),
                    attempt=retry_attempt,
                    error_type=error_type,
                ),
            )
            await self._relinquish_inbound_pipeline_lease(message.id, lease)
            return False

        previous_state = str(pipeline.get("state") or "reconciliation_required")
        resume_state = "completed" if previous_state == "completed" else "accepted"
        message.media_url = media_storage["storageKey"]
        await self._checkpoint_inbound_pipeline(
            message,
            lease,
            resume_state,
            reconciliationRequired=False,
            reconciliationKind=None,
            mediaStorage=media_storage,
            mediaRetry={
                "state": "resolved" if media_storage["state"] == "available" else "cancelled",
                "attempt": retry_attempt,
                "completedAt": datetime.now(timezone.utc).isoformat(),
            },
        )
        await self._relinquish_inbound_pipeline_lease(message.id, lease)
        if resume_state == "completed":
            return True

        await self._resume_inbound_pipeline(
            message=message,
            contact=contact,
            workspace_id=workspace_id,
        )
        refreshed_pipeline = await self.db.scalar(
            select(WhatsAppMessage.inbound_pipeline).where(WhatsAppMessage.id == message.id)
        )
        return bool(
            isinstance(refreshed_pipeline, dict) and refreshed_pipeline.get("state") == "completed"
        )

    async def resolve_inbound_delivery_reconciliation(
        self,
        *,
        workspace_id,
        message_id,
        actor_user_id,
        decision: str,
        reason: str,
    ) -> dict[str, Any]:
        """Apply one audited human verdict to an uncertain external stage."""
        normalized_decision = decision.strip().lower()
        if normalized_decision not in {"delivered", "not_delivered"}:
            raise ValueError("invalid inbound reconciliation decision")
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("inbound reconciliation reason is required")

        actor_label = await resolve_human_actor_label(self.db, actor_user_id)
        row = (
            await self.db.execute(
                select(WhatsAppMessage, WhatsAppContact)
                .join(
                    WhatsAppContact,
                    WhatsAppContact.id == WhatsAppMessage.contact_id,
                )
                .where(
                    WhatsAppMessage.id == message_id,
                    WhatsAppMessage.direction == WaMessageDirection.inbound,
                    WhatsAppContact.workspace_id == workspace_id,
                )
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if row is None:
            raise InboundPipelineReconciliationNotFoundError(
                "Mensagem inbound não foi encontrada neste workspace."
            )
        message, contact = row
        pipeline = dict(message.inbound_pipeline or {})
        uncertain_stage = self._human_reconciliation_stage(pipeline)
        if uncertain_stage is None:
            raise InboundPipelineReconciliationConflictError(
                "A mensagem não possui etapa externa incerta para decisão humana."
            )

        lease = await self._acquire_inbound_pipeline_lease(
            message.id,
            workspace_id=workspace_id,
        )
        if lease is None:
            raise InboundPipelineReconciliationConflictError(
                "A mensagem está sendo processada por outro worker."
            )

        message = await self.db.scalar(
            select(WhatsAppMessage)
            .join(
                WhatsAppContact,
                WhatsAppContact.id == WhatsAppMessage.contact_id,
            )
            .where(
                WhatsAppMessage.id == message_id,
                WhatsAppContact.workspace_id == workspace_id,
            )
            .execution_options(populate_existing=True)
        )
        if message is None:
            await self._relinquish_inbound_pipeline_lease(message_id, lease)
            raise InboundPipelineReconciliationNotFoundError(
                "Mensagem inbound não foi encontrada neste workspace."
            )
        pipeline = dict(message.inbound_pipeline or {})
        uncertain_stage = self._human_reconciliation_stage(pipeline)
        if uncertain_stage is None:
            await self._relinquish_inbound_pipeline_lease(message.id, lease)
            raise InboundPipelineReconciliationConflictError(
                "A etapa incerta já foi resolvida por outra operação."
            )

        resolved_at = datetime.now(timezone.utc)
        resolution = {
            "id": uuid4().hex,
            "stage": uncertain_stage,
            "decision": normalized_decision,
            "reason": normalized_reason,
            "actorUserId": str(actor_user_id),
            "resolvedAt": resolved_at.isoformat(),
        }
        if uncertain_stage == "automation":
            resume_pipeline = True
            next_state = (
                "automation_completed"
                if normalized_decision == "delivered"
                else "automation_retry_authorized"
            )
            stage_updates = {
                "automationCompleted": normalized_decision == "delivered",
                "automationDeliveryOutcome": normalized_decision,
            }
        else:
            resume_pipeline = normalized_decision == "not_delivered"
            next_state = (
                "completed" if normalized_decision == "delivered" else "escalation_retry_authorized"
            )
            stage_updates = {
                "escalationCompleted": normalized_decision == "delivered",
                "escalationDeliveryOutcome": normalized_decision,
            }
            if normalized_decision == "delivered":
                stage_updates["completedAt"] = resolved_at.isoformat()

        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human",
                actor_label=actor_label,
                event_type="whatsapp.inbound_reconciliation_resolved",
                entity_type="whatsapp_message",
                entity_id=message.id,
                summary="Decisão humana registrada para entrega externa incerta.",
                correlation_id=f"whatsapp-inbound:{message.id}:{lease.generation}",
                before_data={
                    "state": pipeline.get("state"),
                    "uncertainStage": pipeline.get("uncertainStage"),
                },
                after_data={
                    "state": next_state,
                    "stage": uncertain_stage,
                    "decision": normalized_decision,
                    "reason": normalized_reason,
                    "resolutionId": resolution["id"],
                },
            )
        )
        try:
            await self._checkpoint_inbound_pipeline(
                message,
                lease,
                next_state,
                reconciliationRequired=False,
                reconciliationKind=None,
                uncertainStage=None,
                reconciliationResolution=resolution,
                **stage_updates,
            )
        except Exception:
            await self.db.rollback()
            await self._relinquish_inbound_pipeline_lease(message.id, lease)
            raise
        await self._relinquish_inbound_pipeline_lease(message.id, lease)

        if resume_pipeline:
            await self._resume_inbound_pipeline(
                message=message,
                contact=contact,
                workspace_id=workspace_id,
            )

        final_pipeline = await self.db.scalar(
            select(WhatsAppMessage.inbound_pipeline)
            .join(
                WhatsAppContact,
                WhatsAppContact.id == WhatsAppMessage.contact_id,
            )
            .where(
                WhatsAppMessage.id == message.id,
                WhatsAppContact.workspace_id == workspace_id,
            )
        )
        return {
            "messageId": message.id,
            "decision": normalized_decision,
            "stage": uncertain_stage,
            "state": str((final_pipeline or {}).get("state") or next_state),
            "resumed": resume_pipeline,
            "resolvedAt": resolved_at,
        }

    @staticmethod
    def _human_reconciliation_stage(pipeline: dict[str, Any]) -> str | None:
        state = str(pipeline.get("state") or "")
        uncertain_stage = str(pipeline.get("uncertainStage") or "")
        candidate = state if state.endswith("_dispatching") else uncertain_stage
        if candidate in {"automation_dispatching", "automation_send"}:
            return "automation"
        if candidate == "escalation_dispatching":
            return "escalation"
        return None

    async def _acquire_inbound_pipeline_lease(
        self,
        message_id,
        *,
        workspace_id=None,
    ) -> InboundPipelineLease | None:
        now = datetime.now(timezone.utc)
        lease_token = uuid4().hex
        tenant_conditions = []
        if workspace_id is not None:
            tenant_conditions.append(
                WhatsAppMessage.contact_id.in_(
                    select(WhatsAppContact.id).where(WhatsAppContact.workspace_id == workspace_id)
                )
            )
        claim = await self.db.execute(
            update(WhatsAppMessage)
            .where(
                WhatsAppMessage.id == message_id,
                WhatsAppMessage.direction == WaMessageDirection.inbound,
                *tenant_conditions,
                or_(
                    WhatsAppMessage.inbound_lease_token.is_(None),
                    WhatsAppMessage.inbound_lease_expires_at.is_(None),
                    WhatsAppMessage.inbound_lease_expires_at <= now,
                ),
            )
            .values(
                inbound_lease_token=lease_token,
                inbound_lease_expires_at=now + INBOUND_PIPELINE_LEASE_TTL,
                inbound_lease_generation=WhatsAppMessage.inbound_lease_generation + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if claim.rowcount != 1:
            await self.db.commit()
            return None

        generation = await self.db.scalar(
            select(WhatsAppMessage.inbound_lease_generation).where(
                WhatsAppMessage.id == message_id,
                WhatsAppMessage.inbound_lease_token == lease_token,
            )
        )
        if generation is None:
            await self.db.rollback()
            raise InboundPipelineLeaseLostError("claimed inbound lease could not be read back")
        await self.db.commit()
        return InboundPipelineLease(token=lease_token, generation=int(generation))

    async def _relinquish_inbound_pipeline_lease(
        self,
        message_id,
        lease: InboundPipelineLease,
    ) -> bool:
        released_at = datetime.now(timezone.utc)
        release = await self.db.execute(
            update(WhatsAppMessage)
            .where(
                WhatsAppMessage.id == message_id,
                WhatsAppMessage.inbound_lease_token == lease.token,
                WhatsAppMessage.inbound_lease_generation == lease.generation,
                WhatsAppMessage.inbound_lease_expires_at > released_at,
            )
            .values(inbound_lease_expires_at=released_at)
            .execution_options(synchronize_session=False)
        )
        await self.db.commit()
        return release.rowcount == 1

    async def _checkpoint_inbound_pipeline(
        self,
        message: WhatsAppMessage,
        lease: InboundPipelineLease,
        state: str,
        **updates: Any,
    ) -> dict[str, Any]:
        stored_pipeline = await self.db.scalar(
            select(WhatsAppMessage.inbound_pipeline).where(
                WhatsAppMessage.id == message.id,
                WhatsAppMessage.inbound_lease_token == lease.token,
                WhatsAppMessage.inbound_lease_generation == lease.generation,
            )
        )
        if stored_pipeline is None:
            await self.db.rollback()
            raise InboundPipelineLeaseLostError("inbound lease was replaced before checkpoint")

        checkpointed_at = datetime.now(timezone.utc)
        pipeline = dict(stored_pipeline or {})
        pipeline.update(updates)
        pipeline["state"] = state
        pipeline["leaseGeneration"] = lease.generation
        pipeline["updatedAt"] = checkpointed_at.isoformat()
        checkpoint = await self.db.execute(
            update(WhatsAppMessage)
            .where(
                WhatsAppMessage.id == message.id,
                WhatsAppMessage.inbound_lease_token == lease.token,
                WhatsAppMessage.inbound_lease_generation == lease.generation,
                WhatsAppMessage.inbound_lease_expires_at > checkpointed_at,
            )
            .values(
                inbound_pipeline=pipeline,
                inbound_lease_expires_at=checkpointed_at + INBOUND_PIPELINE_LEASE_TTL,
            )
            .execution_options(synchronize_session=False)
        )
        if checkpoint.rowcount != 1:
            await self.db.rollback()
            raise InboundPipelineLeaseLostError("inbound lease expired before checkpoint")
        await self.db.commit()
        return pipeline

    async def _handle_status_update(
        self,
        connection_id,
        status_data: Dict[str, Any],
        *,
        workspace_id=None,
        envelope: ProviderEnvelopeContext | None = None,
    ) -> None:
        wamid = status_data.get("id")
        new_status = status_data.get("status")
        if not wamid or not new_status:
            return
        result = await self.db.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.wamid == wamid)
        )
        message = result.scalars().first()
        if not message:
            await self.campaign_service.sync_delivery_status(wamid, new_status, status_data)
            # V30: a mensagem enviada pela inbox (dispatch imediato) não tem registro
            # legado; sem isto o "entregue"/"lida" do provedor nunca chegava à mensagem
            # canônica que a conversa mostra. Gate antes da projeção, como no ramo legado.
            if not external_delivery_allowed():
                return
            projected = await self._project_canonical_delivery_status(
                connection_id=connection_id,
                workspace_id=workspace_id,
                wamid=wamid,
                new_status=new_status,
                status_data=status_data,
                receipt_key=whatsapp_delivery_status_event_key(wamid, new_status, status_data),
            )
            if projected:
                await self.db.commit()
            return

        contact = await self.db.get(WhatsAppContact, message.contact_id)
        if contact is None or (
            workspace_id is not None and contact.workspace_id != workspace_id
        ):
            logger.error(
                "whatsapp.status_wamid_tenant_conflict message_ref=%s",
                opaque_ref(wamid, kind="whatsapp_message"),
            )
            return
        workspace_id = contact.workspace_id
        if str(new_status).lower() == "deleted":
            connection = await self.connection_service.get_connection(
                connection_id,
                workspace_id,
            )
            metadata = dict(message.message_metadata or {})
            structured = metadata.get("structuredContent")
            parsed = ParsedWhatsAppContent(
                body=message.body,
                media_type=message.media_type,
                source_media_id=str(
                    (message.inbound_pipeline or {}).get("sourceMediaId") or ""
                ).strip()
                or None,
                structured_content=structured if isinstance(structured, dict) else {},
            )
            await self._record_provider_observation(
                connection=connection,
                workspace_id=workspace_id,
                legacy_message=message,
                message_data=status_data,
                parsed=parsed,
                envelope=envelope,
                event_type="deleted",
                observation_state="provider_signal",
            )
            metadata["contentLifecycleState"] = "deleted"
            metadata["contentDeletedAt"] = (
                envelope.received_at if envelope else _now()
            ).isoformat()
            message.message_metadata = metadata
            await self.db.commit()
            return

        if not external_delivery_allowed():
            logger.info(
                "whatsapp.status_update_ignored_by_klm_gate message_ref=%s",
                opaque_ref(status_data.get("id"), kind="whatsapp_message"),
            )
            return

        status_map = {
            "sent": WaMessageStatus.sent,
            "delivered": WaMessageStatus.delivered,
            "read": WaMessageStatus.read,
            "failed": WaMessageStatus.failed,
        }
        mapped = status_map.get(new_status)
        if mapped is None:
            return

        receipt_key = whatsapp_delivery_status_event_key(wamid, new_status, status_data)
        metadata = dict(message.message_metadata or {})
        recorded_receipt_keys = metadata.get("providerStatusReceiptKeys") or []
        if receipt_key in recorded_receipt_keys:
            return

        message_id = message.id
        # Campaign projection commits independently. Make it idempotent first,
        # then lock and update the legacy message. A worker crash after the
        # campaign commit can safely replay without duplicating its event.
        await self.campaign_service.sync_delivery_status(wamid, new_status, status_data)
        message = await self.db.scalar(
            select(WhatsAppMessage)
            .where(WhatsAppMessage.id == message_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if message is None:
            return
        metadata = dict(message.message_metadata or {})
        recorded_receipt_keys = [
            str(value)
            for value in (metadata.get("providerStatusReceiptKeys") or [])
            if value
        ]
        if receipt_key in recorded_receipt_keys:
            await self.db.commit()
            return

        message.status = mapped
        message.whatsapp_connection_id = connection_id
        now = whatsapp_delivery_status_occurred_at(status_data)
        if mapped == WaMessageStatus.delivered:
            message.delivered_at = now
        elif mapped == WaMessageStatus.read:
            message.read_at = now
        if mapped == WaMessageStatus.failed:
            await self._record_status_failure_errors(
                message=message,
                workspace_id=workspace_id,
                status_data=status_data,
                now=now,
            )
        metadata = dict(message.message_metadata or {})
        metadata["providerStatusReceiptKeys"] = [
            *recorded_receipt_keys[-31:],
            receipt_key,
        ]
        message.message_metadata = metadata
        await self._project_canonical_delivery_status(
            connection_id=connection_id,
            workspace_id=workspace_id,
            wamid=wamid,
            new_status=new_status,
            status_data=status_data,
            receipt_key=receipt_key,
        )
        await self.db.commit()

    _CANONICAL_DELIVERY_STATUS = {
        "sent": MessageDeliveryStatus.sent,
        "delivered": MessageDeliveryStatus.delivered,
        "read": MessageDeliveryStatus.read,
        "failed": MessageDeliveryStatus.failed,
    }

    async def _project_canonical_delivery_status(
        self,
        *,
        connection_id,
        workspace_id,
        wamid: str,
        new_status: str,
        status_data: dict[str, Any],
        receipt_key: str,
    ) -> bool:
        """V30: projeta o status do provedor na mensagem canônica (OmnichannelMessage),
        idempotente pelo mesmo `receipt_key` do legado. Só `LookupError` (mensagem sem
        canônica) é engolido; falha transitória propaga para o worker reprocessar, em vez
        de gravar o `receipt_key` e virar ausência permanente do status na conversa."""
        canonical_status = self._CANONICAL_DELIVERY_STATUS.get(str(new_status).lower())
        if canonical_status is None:
            return False
        connection = await self.db.get(WhatsAppConnection, connection_id)
        if connection is None:
            return False
        tenant_id = workspace_id or connection.workspace_id
        if tenant_id is None:
            return False
        # Sem canônica com esse wamid (app em coexistência, número sem registro): nada a
        # projetar, e sem abrir savepoint à toa.
        canonical_exists = await self.db.scalar(
            select(OmnichannelMessage.id).where(
                OmnichannelMessage.workspace_id == tenant_id,
                OmnichannelMessage.external_message_id == wamid,
            )
        )
        if canonical_exists is None:
            return False
        # Campanha já projeta o mesmo recibo (`campaign-status:<key>`) em
        # `sync_delivery_status`; um segundo evento seria trilha dobrada.
        campaign_event = await self.db.scalar(
            select(OmnichannelMessageDeliveryEvent.id).where(
                OmnichannelMessageDeliveryEvent.workspace_id == tenant_id,
                OmnichannelMessageDeliveryEvent.external_event_id
                == f"campaign-status:{receipt_key}",
            )
        )
        if campaign_event is not None:
            return False
        errors = status_data.get("errors") or []
        first_error = errors[0] if errors and isinstance(errors[0], dict) else {}
        error_code = str(first_error.get("code") or "").strip() or None
        error_message = (
            str(first_error.get("message") or first_error.get("title") or "").strip() or None
        )
        try:
            async with self.db.begin_nested():
                await WhatsAppConnectionService(self.db).ensure_omnichannel_projection(connection)
                await OmnichannelV6Service(self.db).ingest_delivery_receipt(
                    tenant_id,
                    InternalMessageDeliveryReceiptIngest(
                        provider=self._evidence_provider(connection),
                        connection_key=f"whatsapp:{connection.id}",
                        external_event_id=f"whatsapp-status:{receipt_key}",
                        external_message_id=wamid,
                        status=canonical_status,
                        provider_status=str(new_status),
                        occurred_at=whatsapp_delivery_status_occurred_at(status_data),
                        error_code=error_code,
                        error_message=error_message,
                        payload=status_data,
                        metadata={"source": "whatsapp_status_webhook"},
                    ),
                )
        except LookupError:
            # A canônica sumiu entre a checagem e o lock, ou não pertence à conexão.
            return False
        return True

    async def _record_status_failure_errors(
        self,
        *,
        message: WhatsAppMessage,
        workspace_id,
        status_data: Dict[str, Any],
        now: datetime,
    ) -> None:
        """P1-6: persiste errors[] completo do status `failed` e reage a
        códigos conhecidos (131050 → supressão permanente; 131049 → cap-hit).

        Nunca bloqueia o fluxo normal de status: falhas aqui só enriquecem
        metadata/auditoria, o status da mensagem já foi aplicado acima.
        """
        raw_errors = status_data.get("errors") or []
        if isinstance(raw_errors, dict):
            raw_errors = [raw_errors]
        errors_payload = [
            {
                "code": error.get("code"),
                "title": error.get("title"),
                "message": error.get("message") or error.get("error_data", {}).get("details"),
            }
            for error in raw_errors
            if isinstance(error, dict)
        ]
        metadata = dict(message.message_metadata or {})
        metadata["lastStatusErrors"] = errors_payload
        message.message_metadata = metadata

        codes = _provider_error_codes(status_data)
        for provider_code in codes:
            policy = classify_meta_error(provider_code)
            if policy.suppress_contact:
                contact = await self.db.get(WhatsAppContact, message.contact_id)
                phone = contact.phone if contact is not None else None
                if phone:
                    await self._ensure_inbound_opt_out_suppression(
                        workspace_id=workspace_id,
                        contact_id=message.contact_id,
                        phone=phone,
                        reason="meta_opt_out_status",
                        details={
                            "origin": "webhook_status_failed",
                            "providerCode": provider_code,
                            "wamid": message.wamid,
                            "suppressedAt": now.isoformat(),
                        },
                    )
                    logger.info(
                        "whatsapp.status_failed_suppression_created message_ref=%s "
                        "provider_code=%s",
                        opaque_ref(message.wamid, kind="whatsapp_message"),
                        provider_code,
                    )
            elif provider_code == 131049:
                metadata = dict(message.message_metadata or {})
                metadata["capHit"] = {
                    "providerCode": provider_code,
                    "detectedAt": now.isoformat(),
                }
                message.message_metadata = metadata
                logger.info(
                    "whatsapp.status_failed_cap_hit message_ref=%s",
                    opaque_ref(message.wamid, kind="whatsapp_message"),
                )

    async def _maybe_register_inbound_opt_out(
        self,
        *,
        message: WhatsAppMessage,
        contact: WhatsAppContact,
        workspace_id,
        wamid: str,
        body: str,
        provider_msg_type: str,
    ) -> None:
        """W1.11 — detecta opt-out em mensagem inbound de texto e suprime.

        NUNCA bloqueia o processamento normal da mensagem: o atendimento
        (automação, escalonamento humano, SDR) continua de qualquer forma;
        apenas o disparo de marketing passa a ser suprimido para este
        telefone. Idempotente: uma mensagem já marcada não é reprocessada.
        """
        if provider_msg_type != "text":
            return
        metadata = dict(message.message_metadata or {})
        if metadata.get("optOutDetected"):
            return
        trigger = _detect_inbound_opt_out(body)
        if trigger is None:
            return
        phone = contact.phone if contact is not None else None
        if not phone:
            return
        truncated_text = str(body or "")[:280]
        await self._ensure_inbound_opt_out_suppression(
            workspace_id=workspace_id,
            contact_id=contact.id,
            phone=phone,
            reason=INBOUND_OPT_OUT_SUPPRESSION_REASON,
            details={
                "origin": "inbound_message",
                "wamid": wamid,
                "triggerMatched": trigger,
                "originalTextTruncated": truncated_text,
                "suppressedAt": datetime.now(timezone.utc).isoformat(),
            },
        )
        metadata["optOutDetected"] = True
        metadata["optOutTrigger"] = trigger
        message.message_metadata = metadata
        await self.db.commit()
        logger.info(
            "whatsapp.inbound_opt_out_detected message_ref=%s trigger=%s",
            opaque_ref(wamid, kind="whatsapp_message"),
            trigger,
        )

    async def _ensure_inbound_opt_out_suppression(
        self,
        *,
        workspace_id,
        contact_id,
        phone: str,
        reason: str,
        details: Dict[str, Any],
    ) -> WhatsAppSuppressionEntry:
        """Cria/reativa `WhatsAppSuppressionEntry` permanente (upsert idempotente).

        Duplicado de forma mínima a partir de
        `WhatsAppCampaignService._ensure_permanent_suppression`: aquele método
        exige uma campanha/destinatário do motor de disparos, que não existem
        no contexto do webhook (mensagem inbound avulsa ou status sem
        campanha). Reimportar o motor de campanhas aqui não criaria ciclo
        (este módulo já importa `WhatsAppCampaignService`), mas a assinatura
        não é reutilizável sem os objetos de campanha; por isso o upsert é
        replicado aqui, documentado, e não deve divergir da semântica daquele
        método (permanente, uma linha por telefone/workspace).
        """
        normalized_phone = normalize_meta_phone_number(phone)
        if normalized_phone is None:
            logger.warning(
                "whatsapp.webhook.invalid_meta_phone",
                extra={
                    "workspace_id": str(workspace_id),
                    "contact_id": str(contact_id) if contact_id else None,
                },
            )
            raise ValueError("A Meta enviou um telefone inválido no webhook.")
        entry = (
            await self.db.execute(
                select(WhatsAppSuppressionEntry).where(
                    WhatsAppSuppressionEntry.workspace_id == workspace_id,
                    WhatsAppSuppressionEntry.phone.in_(phone_number_variants(normalized_phone)),
                )
            )
        ).scalars().first()
        is_new = entry is None
        if entry is None:
            entry = WhatsAppSuppressionEntry(
                workspace_id=workspace_id,
                phone=normalized_phone,
            )
        entry.contact_id = contact_id or entry.contact_id
        entry.reason = reason
        entry.details = {**(entry.details or {}), **details}
        entry.is_active = True
        self.db.add(entry)
        await self.db.flush()
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_type="contact",
                actor_label="Contato via WhatsApp",
                event_type="whatsapp_suppression_created",
                entity_type="whatsapp_suppression_entry",
                entity_id=entry.id,
                summary=(
                    "Supressão de marketing criada/reativada para este contato "
                    f"(motivo: {reason})."
                ),
                after_data={
                    "reason": reason,
                    "details": entry.details,
                    "wasNew": is_new,
                },
            )
        )
        return entry

    async def _get_or_create_contact(
        self,
        phone: str,
        workspace_id,
        connection_id,
        name: str | None,
        identity: WhatsAppIdentityRef | None = None,
    ) -> WhatsAppContact:
        """Resolve o contato por BSUID primeiro, telefone depois.

        A ordem não é preferência: a partir de 2026 o telefone pode ser ocultado
        pelo usuário e o username muda, mas o BSUID permanece dentro do
        portfólio. Buscar por telefone primeiro criaria contato duplicado toda
        vez que o mesmo cliente voltasse sem o número.

        Identificador que aparece pela primeira vez num contato existente é
        preenchido: é assim que a base se atualiza sozinha durante a
        convivência, sem varredura nem migração de dados.
        """
        identity = identity or WhatsAppIdentityRef()
        raw_phone = str(phone or "").strip()
        normalized_phone = _normalize_wa_phone(raw_phone) or normalize_meta_phone_number(
            identity.phone
        )
        bsuid = identity.bsuid

        contact: Optional[WhatsAppContact] = None

        if bsuid:
            result = await self.db.execute(
                select(WhatsAppContact).where(
                    WhatsAppContact.bsuid == bsuid,
                    WhatsAppContact.workspace_id == workspace_id,
                    WhatsAppContact.whatsapp_connection_id == connection_id,
                )
            )
            contact = result.scalars().first()

        if contact is not None and normalized_phone:
            aliases = list(
                (
                    await self.db.scalars(
                        select(WhatsAppContact)
                        .where(
                            WhatsAppContact.id != contact.id,
                            WhatsAppContact.phone.in_(
                                phone_number_lookup_variants(normalized_phone)
                            ),
                            WhatsAppContact.workspace_id == workspace_id,
                            WhatsAppContact.whatsapp_connection_id == connection_id,
                            or_(
                                WhatsAppContact.bsuid.is_(None),
                                WhatsAppContact.bsuid == bsuid,
                            ),
                        )
                        .with_for_update()
                    )
                ).all()
            )
            if aliases:
                await self._merge_contact_aliases(winner=contact, aliases=aliases)

        if contact is None and normalized_phone:
            candidates = phone_number_lookup_variants(normalized_phone)
            phone_contacts = list(
                (
                    await self.db.scalars(
                        select(WhatsAppContact)
                        .where(
                            WhatsAppContact.phone.in_(candidates),
                            WhatsAppContact.workspace_id == workspace_id,
                            WhatsAppContact.whatsapp_connection_id == connection_id,
                        )
                        .with_for_update()
                    )
                ).all()
            )
            compatible = [
                item
                for item in phone_contacts
                if not bsuid or not item.bsuid or item.bsuid == bsuid
            ]
            contact = next(
                (item for item in compatible if item.phone == normalized_phone),
                compatible[0] if compatible else None,
            )
            if contact is not None and len(compatible) > 1:
                await self._merge_contact_aliases(
                    winner=contact,
                    aliases=[item for item in compatible if item.id != contact.id],
                )

        if contact:
            self._enrich_contact_identity(
                contact,
                name=name,
                bsuid=bsuid,
                normalized_phone=normalized_phone,
                identity=identity,
            )
            return contact

        if not normalized_phone and not bsuid:
            # Sem nenhum identificador não existe contato possível. Quem chama
            # trata como exceção operacional visível — jamais como mensagem
            # descartada em silêncio.
            raise WhatsAppContactWithoutIdentifierError(
                "Evento do WhatsApp sem telefone e sem BSUID: o contato não "
                "pode ser criado nem reconhecido."
            )

        await WhatsAppContactCapacityService(self.db).enforce_new_contact(
            workspace_id=workspace_id
        )
        # A checagem de capacidade NÃO serializa criadores (`lock_workspace` é
        # FOR KEY SHARE): esta releitura reduz a janela, e o SAVEPOINT na criação
        # abaixo é o que resolve a corrida de fato (S3).
        if bsuid:
            contact = await self.db.scalar(
                select(WhatsAppContact).where(
                    WhatsAppContact.bsuid == bsuid,
                    WhatsAppContact.workspace_id == workspace_id,
                    WhatsAppContact.whatsapp_connection_id == connection_id,
                )
            )
        if contact is None and normalized_phone:
            candidates = phone_number_lookup_variants(normalized_phone)
            phone_contacts = list(
                (
                    await self.db.scalars(
                        select(WhatsAppContact)
                        .where(
                            WhatsAppContact.phone.in_(candidates),
                            WhatsAppContact.workspace_id == workspace_id,
                            WhatsAppContact.whatsapp_connection_id == connection_id,
                        )
                        .with_for_update()
                    )
                ).all()
            )
            compatible = [
                item
                for item in phone_contacts
                if not bsuid or not item.bsuid or item.bsuid == bsuid
            ]
            contact = next(
                (item for item in compatible if item.phone == normalized_phone),
                compatible[0] if compatible else None,
            )
            if contact is not None and len(compatible) > 1:
                await self._merge_contact_aliases(
                    winner=contact,
                    aliases=[item for item in compatible if item.id != contact.id],
                )
        if contact is not None:
            self._enrich_contact_identity(
                contact,
                name=name,
                bsuid=bsuid,
                normalized_phone=normalized_phone,
                identity=identity,
            )
            return contact

        try:
            # S3: duas entregas do mesmo contato chegam juntas (a checagem de
            # capacidade é FOR KEY SHARE e não serializa). O SAVEPOINT isola a
            # colisão no índice único: a transação do pipeline continua viva e
            # a releitura devolve a linha que venceu a corrida.
            async with self.db.begin_nested():
                contact = WhatsAppContact(
                    phone=normalized_phone or None,
                    bsuid=bsuid,
                    parent_bsuid=identity.parent_bsuid,
                    username=identity.username,
                    name=name or identity.display_name,
                    workspace_id=workspace_id,
                    whatsapp_connection_id=connection_id,
                )
                self.db.add(contact)
                await self.db.flush()
        except IntegrityError:
            contact = await self._relookup_contact_after_collision(
                workspace_id=workspace_id,
                connection_id=connection_id,
                bsuid=bsuid,
                normalized_phone=normalized_phone,
            )
            if contact is None:
                raise
            if bsuid and contact.bsuid and contact.bsuid != bsuid:
                # Mesmo telefone, BSUID diferente: não sobrescrever a identidade
                # da linha existente; registrar o conflito e devolver a linha.
                logger.warning(
                    "whatsapp_contact.identity_conflict contact_id=%s workspace_id=%s",
                    contact.id,
                    workspace_id,
                )
                return contact
            self._enrich_contact_identity(
                contact,
                name=name,
                bsuid=bsuid,
                normalized_phone=normalized_phone,
                identity=identity,
            )
        return contact

    @staticmethod
    def _enrich_contact_identity(
        contact: WhatsAppContact,
        *,
        name: str | None,
        bsuid: str | None,
        normalized_phone: str | None,
        identity: WhatsAppIdentityRef,
    ) -> None:
        """Identificador que aparece pela primeira vez num contato existente é
        preenchido; username é exibição e sempre sobrescreve."""
        if name and not contact.name:
            contact.name = name
        if bsuid and not contact.bsuid:
            contact.bsuid = bsuid
        if normalized_phone and contact.phone != normalized_phone:
            contact.phone = normalized_phone
        if identity.parent_bsuid and not contact.parent_bsuid:
            contact.parent_bsuid = identity.parent_bsuid
        if identity.username:
            contact.username = identity.username

    async def _relookup_contact_after_collision(
        self,
        *,
        workspace_id,
        connection_id,
        bsuid: str | None,
        normalized_phone: str | None,
    ) -> WhatsAppContact | None:
        if bsuid:
            contact = await self.db.scalar(
                select(WhatsAppContact).where(
                    WhatsAppContact.bsuid == bsuid,
                    WhatsAppContact.workspace_id == workspace_id,
                    WhatsAppContact.whatsapp_connection_id == connection_id,
                )
            )
            if contact is not None:
                return contact
        if not normalized_phone:
            return None
        return await self.db.scalar(
            select(WhatsAppContact)
            .where(
                WhatsAppContact.phone.in_(sorted(phone_number_lookup_variants(normalized_phone))),
                WhatsAppContact.workspace_id == workspace_id,
                WhatsAppContact.whatsapp_connection_id == connection_id,
            )
            .order_by(
                case((WhatsAppContact.phone == normalized_phone, 0), else_=1),
                WhatsAppContact.id,
            )
            .limit(1)
        )

    async def _merge_contact_aliases(
        self,
        *,
        winner: WhatsAppContact,
        aliases: list[WhatsAppContact],
    ) -> None:
        """Move todo o historico antes de remover contatos BR equivalentes."""
        reference_models = (
            WhatsAppMessage,
            WhatsAppConversationNote,
            WhatsAppOptInRecord,
            WhatsAppSuppressionEntry,
            WhatsAppAudienceListMember,
            WhatsAppCampaignRecipient,
        )
        merged_ids: list[str] = []
        for alias in aliases:
            for model in reference_models:
                await self.db.execute(
                    update(model)
                    .where(model.contact_id == alias.id)
                    .values(contact_id=winner.id)
                )
            if winner.name is None:
                winner.name = alias.name
            if winner.avatar_url is None:
                winner.avatar_url = alias.avatar_url
            if winner.bsuid is None:
                winner.bsuid = alias.bsuid
            if winner.parent_bsuid is None:
                winner.parent_bsuid = alias.parent_bsuid
            if winner.username is None:
                winner.username = alias.username
            if winner.matter_id is None:
                winner.matter_id = alias.matter_id
            if winner.assigned_user_id is None:
                winner.assigned_user_id = alias.assigned_user_id
            if winner.last_seen is None or (
                alias.last_seen is not None and alias.last_seen > winner.last_seen
            ):
                winner.last_seen = alias.last_seen
            winner.is_blocked = winner.is_blocked or alias.is_blocked
            merged_ids.append(str(alias.id))
            await self.db.delete(alias)
        self.db.add(
            KlmAuditEvent(
                workspace_id=winner.workspace_id,
                actor_type="system",
                actor_label="Meu Executivo",
                event_type="whatsapp_contact_aliases_merged",
                entity_type="whatsapp_contact",
                entity_id=winner.id,
                summary="Contatos equivalentes foram consolidados sem perder o historico.",
                after_data={"mergedContactIds": merged_ids},
            )
        )
        await self.db.flush()

    async def _take_over_contact_thread(
        self,
        contact: WhatsAppContact,
        *,
        assigned_user_id: UUID,
        thread_id: UUID | None = None,
        reason: str,
    ) -> None:
        """S1: a escalada automática também toma a thread canônica do contato,
        idempotente (sem novo evento nem nova geração quando o mesmo humano já
        está ativo e nenhum agente é dono) e best-effort (o estágio do pipeline
        nunca quebra por causa da thread)."""
        try:
            thread = None
            if thread_id is not None:
                thread = await self.db.scalar(
                    select(OmnichannelThread).where(
                        OmnichannelThread.id == thread_id,
                        OmnichannelThread.workspace_id == contact.workspace_id,
                    )
                )
            if thread is None:
                thread = await resolve_contact_thread(
                    self.db, workspace_id=contact.workspace_id, contact_id=contact.id
                )
            if thread is None or thread.status.value in ("closed", "blocked"):
                return
            if human_already_holds(thread, assigned_user_id):
                return
            await human_takeover(
                self.db,
                workspace_id=contact.workspace_id,
                thread_id=thread.id,
                human_user_id=assigned_user_id,
                actor_label="Escalada automática",
                actor_type="system",
                reason=reason,
            )
            await self.db.commit()
        except Exception as exc:  # noqa: BLE001 - best-effort, registrado
            logger.warning(
                "whatsapp.escalation_thread_takeover_failed contact_id=%s error_type=%s",
                str(contact.id),
                type(exc).__name__,
            )

    async def _maybe_escalate_to_human(self, contact: WhatsAppContact, inbound_text: str) -> None:
        reason = self._escalation_reason(contact, inbound_text)
        if not reason:
            return
        assigned_user = await self._resolve_human_owner(contact)
        if assigned_user is None:
            return

        contact.assigned_user_id = assigned_user.id
        contact.queue_status = WaQueueStatus.human
        contact.human_control_state = HumanControlState.copilot
        contact.next_follow_up_at = None

        if contact.matter_id:
            matter = await self.db.scalar(
                select(Matter).where(
                    Matter.id == contact.matter_id,
                    Matter.workspace_id == contact.workspace_id,
                    Matter.deleted_at.is_(None),
                )
            )
            if matter and not matter.responsible_human_id:
                matter.responsible_human_id = assigned_user.id
                matter.next_action = "Atendimento humano solicitado via inbox do WhatsApp."

        await self.db.commit()
        # A thread vem pelo binding do contato (o `inbound_pipeline` do ORM fica
        # desatualizado depois dos checkpoints por UPDATE).
        await self._take_over_contact_thread(
            contact, assigned_user_id=assigned_user.id, reason="auto_escalation"
        )
        if external_delivery_allowed():
            await OperationalAlertService(self.db).notify_human_takeover(contact.id, reason=reason)

    async def _apply_automation_rules(
        self,
        contact: WhatsAppContact,
        inbound_text: str,
        *,
        message: WhatsAppMessage,
        lease: InboundPipelineLease,
    ) -> bool:
        outcome = await self.automation_rule_service.apply_inbound_rules(
            contact=contact,
            inbound_text=inbound_text,
        )
        if not outcome:
            return True
        if outcome.get("suppressed"):
            logger.info(
                "whatsapp.automation_reply_suppressed contact_id=%s rule_id=%s reason=%s",
                contact.id,
                outcome.get("ruleId"),
                outcome.get("suppressionReason"),
            )
            return True
        reply_text = outcome.get("replyText")
        if reply_text and external_delivery_allowed():
            try:
                delivery_confirmed = await self.message_service.send_text_direct(
                    phone=contact.phone,
                    body=reply_text,
                    workspace_id=contact.workspace_id,
                    connection_id=contact.whatsapp_connection_id,
                    idempotency_key=f"automation-reply:{message.id}",
                )
            except Exception as exc:
                error_type = safe_error_type(exc)
                logger.warning(
                    "whatsapp.automation_auto_reply_failed contact_id=%s error_type=%s",
                    contact.id,
                    error_type,
                )
                await self._checkpoint_inbound_pipeline(
                    message,
                    lease,
                    "reconciliation_required",
                    reconciliationRequired=True,
                    uncertainStage="automation_send",
                    automationDeliveryOutcome="unknown",
                    automationSendLastErrorType=error_type,
                    automationCompleted=False,
                )
                return False
            if not delivery_confirmed:
                logger.warning(
                    "whatsapp.automation_auto_reply_unconfirmed contact_id=%s",
                    contact.id,
                )
                await self._checkpoint_inbound_pipeline(
                    message,
                    lease,
                    "reconciliation_required",
                    reconciliationRequired=True,
                    uncertainStage="automation_send",
                    automationDeliveryOutcome="unknown",
                    automationSendLastErrorType="UnconfirmedDirectSend",
                    automationCompleted=False,
                )
                return False

        if outcome.get("escalateToHuman"):
            assigned_user = await self._resolve_human_owner(contact)
            if assigned_user is None:
                return True
            contact.assigned_user_id = assigned_user.id
            contact.queue_status = WaQueueStatus.human
            contact.human_control_state = HumanControlState.copilot
            await self.db.commit()
            await self._take_over_contact_thread(
                contact, assigned_user_id=assigned_user.id, reason="auto_escalation_rule"
            )
            if external_delivery_allowed():
                await OperationalAlertService(self.db).notify_human_takeover(
                    contact.id,
                    reason=(
                        "Regra automática acionada: "
                        f"{outcome.get('label') or 'Escalada operacional'}"
                    ),
                )
        return True

    def _escalation_reason(self, contact: WhatsAppContact, inbound_text: str) -> str | None:
        normalized = (inbound_text or "").strip().lower()
        if contact.queue_status == WaQueueStatus.follow_up:
            return "O lead respondeu durante a etapa de follow-up e precisa de retomada humana."
        escalation_phrases = (
            "quero falar com humano",
            "quero falar com atendente",
            "quero falar com advogado",
            "preciso falar com advogado",
            "me liga",
            "pode me ligar",
            "atendimento humano",
            "quero falar com uma pessoa",
            "não entendi",
            "nao entendi",
            "não ajudou",
            "nao ajudou",
            "não resolveu",
            "nao resolveu",
        )
        if any(phrase in normalized for phrase in escalation_phrases):
            return "O lead pediu atendimento humano ou indicou que a IA não resolveu a demanda."
        return None

    async def _resolve_human_owner(self, contact: WhatsAppContact) -> User | None:
        if contact.assigned_user_id:
            assigned = await self._active_workspace_user(
                contact.workspace_id,
                contact.assigned_user_id,
            )
            if assigned:
                return assigned
        if contact.matter_id:
            matter = await self.db.scalar(
                select(Matter).where(
                    Matter.id == contact.matter_id,
                    Matter.workspace_id == contact.workspace_id,
                    Matter.deleted_at.is_(None),
                )
            )
            if matter and matter.responsible_human_id:
                responsible = await self._active_workspace_user(
                    contact.workspace_id,
                    matter.responsible_human_id,
                )
                if responsible:
                    return responsible
        result = await self.db.execute(
            select(WorkspaceMembership)
            .join(User, User.id == WorkspaceMembership.user_id)
            .options(selectinload(WorkspaceMembership.user))
            .where(
                WorkspaceMembership.workspace_id == contact.workspace_id,
                WorkspaceMembership.status == MembershipStatus.active,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
                WorkspaceMembership.role.in_(
                    [
                        WorkspaceRole.workspace_owner,
                        WorkspaceRole.workspace_admin,
                        WorkspaceRole.legal_manager,
                        WorkspaceRole.operator,
                    ]
                ),
            )
            .order_by(WorkspaceMembership.is_default.desc(), WorkspaceMembership.created_at.asc())
        )
        membership = result.scalars().first()
        return membership.user if membership and membership.user else None

    async def _active_workspace_user(
        self,
        workspace_id,
        user_id,
    ) -> User | None:
        return await self.db.scalar(
            select(User)
            .join(WorkspaceMembership, WorkspaceMembership.user_id == User.id)
            .where(
                User.id == user_id,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.status == MembershipStatus.active,
            )
        )
