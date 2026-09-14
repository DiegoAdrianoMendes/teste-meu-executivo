"""
Serviço: Webhook de leads de sites (F12 — Central de Conexões).

"Formulário do site → direto pro CRM." Um token opaco por workspace,
guardado (cifrado) em `IntegrationConnection.settings` da integração
provider `site_leads` — no mesmo molde do `webhookSecret` do Asaas
(`app/api/v1/endpoints/asaas_webhook.py`). Resolve o workspace do token em
tempo constante contra o conjunto (pequeno) de integrações `site_leads`
conectadas.

Pipeline do lead (ordem do titular, ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §F12):
  1. Mapeamento tolerante do payload (JSON ou form-encoded).
  2. Honeypot preenchido → descarte silencioso (200, nada gravado).
  3. Dedupe por (workspace, email|telefone normalizado) em 24h — reaproveita
     o lead existente e apenas registra "novo envio do site" na timeline.
  4. Cria Client + Matter na coluna `novo_lead` (reusa o núcleo canal-agnóstico
     de `sdr_intake_service._create_lead_client_and_matter`).
  5. Cria/abre a thread omnichannel do lead.
  6. Primeiro contato automático: lead nunca escreveu → sem janela de 24h →
     MetaPolicyGate sempre exige template aprovado. Gates fechados ou sem
     template/telefone/conexão → rascunho pendente + flag
     `first_contact_failed` no card + Task para humano + KlmAuditEvent.
     Falha nunca é silenciosa.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.models.client import Client
from app.models.finance import IntegrationConnection, IntegrationProvider, IntegrationStatus
from app.models.klm_operations import KlmAuditEvent, OmnichannelChannel, OmnichannelThread
from app.models.matter import KanbanColumnId, Matter
from app.models.omnichannel_v6 import OmnichannelIdentity
from app.models.task import TaskPriority
from app.schemas.task import TaskCreate
from app.services.integration_secrets import protect_integration_settings
from app.services.meta_policy_gate import MetaPolicyGate
from app.services.task_service import TaskService
from app.services.whatsapp.helpers import normalize_phone_number, phone_number_lookup_variants

LEAD_WEBHOOK_LABEL = "Formulário do site"
HONEYPOT_FIELD = "_hp"
MAX_PAYLOAD_BYTES = 32 * 1024
DEDUPE_WINDOW = timedelta(hours=24)
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 30

# Rate-limit simples por token, em memória (por processo). Suficiente para
# conter abuso trivial de um único endpoint público; escala horizontal exige
# um backend compartilhado (Redis), fora do escopo desta tarefa.
_rate_limit_buckets: dict[str, list[float]] = {}

_NAME_KEYS = ("name", "nome", "full_name", "fullName")
_EMAIL_KEYS = ("email", "e-mail", "e_mail")
_PHONE_KEYS = ("phone", "telefone", "whatsapp", "celular", "tel", "phoneNumber")
_MESSAGE_KEYS = ("message", "mensagem", "msg", "comentario", "comment")
_UTM_KEYS = (
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
)
# Click ids que o F12 (formulário do site) recebe do próprio navegador do lead
# (?gclid=...&fbclid=...) — mesmo conjunto que `lead_capture_adapter_service`
# reconhece para Meta/TikTok Lead Ads. Sem isto, todo tráfego pago que chega
# via formulário do site (não via Lead Ads nativo) cai fora do funil de
# atribuição: vira "extras" genérico, nunca um AcquisitionEvent.
_CLICK_ID_KEYS = ("gclid", "gbraid", "wbraid", "fbclid", "ttclid", "msclkid")
_KNOWN_KEYS = frozenset(
    _NAME_KEYS
    + _EMAIL_KEYS
    + _PHONE_KEYS
    + _MESSAGE_KEYS
    + _UTM_KEYS
    + _CLICK_ID_KEYS
    + (HONEYPOT_FIELD,)
)


def _first(payload: dict[str, Any], keys: tuple[str, ...]) -> Optional[str]:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def normalize_phone(phone: Optional[str]) -> Optional[str]:
    return normalize_phone_number(phone)


def normalize_email(email: Optional[str]) -> Optional[str]:
    if not email:
        return None
    return email.strip().lower() or None


@dataclass
class ParsedLeadPayload:
    name: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    message: Optional[str]
    utms: dict[str, str] = field(default_factory=dict)
    click_ids: dict[str, str] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)
    honeypot_triggered: bool = False


def parse_lead_payload(payload: dict[str, Any]) -> ParsedLeadPayload:
    honeypot_value = payload.get(HONEYPOT_FIELD)
    honeypot_triggered = bool(honeypot_value and str(honeypot_value).strip())

    utms = {key: str(payload[key]) for key in _UTM_KEYS if payload.get(key)}
    click_ids = {
        key: str(payload[key]).strip()
        for key in _CLICK_ID_KEYS
        if str(payload.get(key) or "").strip()
    }
    extras = {
        key: value
        for key, value in payload.items()
        if key not in _KNOWN_KEYS and value not in (None, "")
    }

    return ParsedLeadPayload(
        name=_first(payload, _NAME_KEYS),
        email=normalize_email(_first(payload, _EMAIL_KEYS)),
        phone=normalize_phone(_first(payload, _PHONE_KEYS)),
        message=_first(payload, _MESSAGE_KEYS),
        utms=utms,
        click_ids=click_ids,
        extras=extras,
        honeypot_triggered=honeypot_triggered,
    )


def check_rate_limit(token: str) -> bool:
    """True se dentro do limite; False se estourou. Janela deslizante simples em memória."""
    now = time.monotonic()
    bucket = _rate_limit_buckets.setdefault(token, [])
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    while bucket and bucket[0] < cutoff:
        bucket.pop(0)
    if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
        return False
    bucket.append(now)
    return True


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def generate_or_rotate_token(db: AsyncSession, *, workspace_id: UUID) -> str:
    """Gera (ou rotaciona) o token do webhook de leads do site para o workspace.

    Retorna o token em texto plano UMA vez (nunca é lido de volta depois —
    só o hash fica persistido, no mesmo espírito do `WorkspacePublicApiToken`).
    """
    connection = await db.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider == IntegrationProvider.site_leads,
            IntegrationConnection.label == LEAD_WEBHOOK_LABEL,
        )
    )
    plain_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(plain_token)

    if connection is None:
        connection = IntegrationConnection(
            workspace_id=workspace_id,
            provider=IntegrationProvider.site_leads,
            label=LEAD_WEBHOOK_LABEL,
            status=IntegrationStatus.connected,
            settings={},
        )
        db.add(connection)

    existing_settings = dict(connection.settings or {})
    existing_settings["tokenHash"] = token_hash
    existing_settings["tokenPrefix"] = plain_token[:8]
    connection.settings = protect_integration_settings(existing_settings)
    connection.status = IntegrationStatus.connected
    await db.flush()
    return plain_token


async def resolve_workspace_by_token(db: AsyncSession, token: str) -> Optional[UUID]:
    """Resolve o workspace dono do token de leads, em tempo constante, contra
    o conjunto de integrações `site_leads` conectadas — mesmo padrão de
    `asaas_webhook._resolve_workspace_id_by_token`, adaptado para hash
    (o token aqui é gerado por nós, não recebido de terceiro, então guardamos
    só o hash e comparamos por igualdade de hash, sem decifrar nada)."""
    if not token:
        return None
    candidate_hash = _hash_token(token)
    result = await db.execute(
        select(IntegrationConnection).where(
            IntegrationConnection.provider == IntegrationProvider.site_leads,
            IntegrationConnection.status != IntegrationStatus.disconnected,
        )
    )
    matched_workspace_id: Optional[UUID] = None
    for connection in result.scalars().all():
        stored_hash = str((connection.settings or {}).get("tokenHash") or "")
        if not stored_hash:
            continue
        if hmac.compare_digest(stored_hash, candidate_hash):
            matched_workspace_id = connection.workspace_id
            # Sem early-return: percorre todo o conjunto candidato, mesmo
            # padrão de mitigação de canal-lateral básica do Asaas.
    return matched_workspace_id


async def _find_recent_duplicate(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    email: Optional[str],
    phone: Optional[str],
) -> Optional[Client]:
    if not email and not phone:
        return None
    cutoff = datetime.now(timezone.utc) - DEDUPE_WINDOW
    conditions = []
    if email:
        conditions.append(Client.email == email)
    if phone:
        conditions.append(Client.phone.in_(phone_number_lookup_variants(phone)))
    from sqlalchemy import or_

    result = await db.execute(
        select(Client)
        .where(
            Client.workspace_id == workspace_id,
            Client.deleted_at.is_(None),
            Client.created_at >= cutoff,
            or_(*conditions),
        )
        .order_by(Client.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def _get_or_create_thread(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    client: Client,
    matter: Matter,
    phone: Optional[str],
    display_name: str,
) -> OmnichannelThread:
    existing = await db.scalar(
        select(OmnichannelThread).where(
            OmnichannelThread.workspace_id == workspace_id,
            OmnichannelThread.matter_id == matter.id,
        )
    )
    if existing is not None:
        return existing

    identity = OmnichannelIdentity(
        workspace_id=workspace_id,
        client_id=client.id,
        display_name=display_name,
        canonical_phone=phone,
        identity_state="provisional",
        source="site_lead",
    )
    db.add(identity)
    await db.flush()

    thread = OmnichannelThread(
        workspace_id=workspace_id,
        matter_id=matter.id,
        client_id=client.id,
        identity_id=identity.id,
        primary_channel=OmnichannelChannel.webchat,
        subject=f"Lead do site — {display_name}",
        queue="new",
        thread_metadata={"origin": "site_lead"},
    )
    db.add(thread)
    await db.flush()
    return thread


async def _record_audit_event(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    matter_id: UUID,
    event_type: str,
    summary: str,
    after_data: dict[str, Any] | None = None,
) -> None:
    db.add(
        KlmAuditEvent(
            workspace_id=workspace_id,
            actor_type="system",
            actor_label="Webhook de leads do site",
            event_type=event_type,
            entity_type="matter",
            entity_id=matter_id,
            summary=summary,
            after_data=after_data,
        )
    )
    await db.flush()


async def _attempt_first_contact(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    matter: Matter,
    connection: IntegrationConnection,
    phone: Optional[str],
) -> None:
    """Primeiro contato automático via WhatsApp. Lead de site nunca escreveu:
    sem last_inbound_at, o MetaPolicyGate SEMPRE nega envio livre e exige
    template aprovado (business-initiated). Falha nunca é silenciosa: toda
    saída não-sucesso grava flag no card + Task humana + auditoria."""
    now = datetime.now(timezone.utc)
    reason: Optional[str] = None

    if not phone:
        reason = "sem_telefone"
    else:
        decision = MetaPolicyGate.evaluate(
            "whatsapp",
            last_inbound_at=None,
            now=now,
            author_type="system",
            action_type="first_contact",
        )
        if decision.requires == "template" or not decision.allowed:
            template_ref = str(
                (connection.settings or {}).get("firstContactTemplate") or ""
            ).strip()
            has_whatsapp_connection = False
            try:
                from app.models.whatsapp import WaConnectionStatus, WhatsAppConnection

                has_whatsapp_connection = bool(
                    await db.scalar(
                        select(WhatsAppConnection.id).where(
                            WhatsAppConnection.workspace_id == workspace_id,
                            WhatsAppConnection.status == WaConnectionStatus.connected,
                        )
                    )
                )
            except Exception as exc:  # noqa: BLE001 — nunca deixa o pipeline do lead cair por isto
                logger.error(
                    "lead_webhook.connection_lookup_failed",
                    matter_id=str(matter.id),
                    error_type=safe_error_type(exc),
                )

            if not template_ref:
                reason = "sem_template_configurado"
            elif not has_whatsapp_connection:
                reason = "sem_conexao_whatsapp"
            else:
                # Gates abertos + template configurado: dispara best-effort.
                # Qualquer falha aqui vira flag, nunca exceção silenciosa.
                try:
                    from app.services.whatsapp_service import WhatsAppService

                    sent = await WhatsAppService(db).send_text_direct(
                        phone=phone,
                        body=f"[template:{template_ref}]",
                        workspace_id=workspace_id,
                        idempotency_key=f"lead-first-contact:{matter.id}",
                    )
                    if not sent:
                        reason = "envio_falhou"
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "lead_webhook.first_contact_send_failed",
                        matter_id=str(matter.id),
                        error_type=safe_error_type(exc),
                    )
                    reason = "erro_no_envio"
        else:
            reason = "sem_janela_e_sem_template"

    metadata = dict(matter.sdr_qualification or {})

    if reason is None:
        metadata.pop("first_contact_failed", None)
        metadata["first_contact_sent_at"] = now.isoformat()
        matter.sdr_qualification = metadata
        await _record_audit_event(
            db,
            workspace_id=workspace_id,
            matter_id=matter.id,
            event_type="lead_webhook.first_contact_sent",
            summary="Primeiro contato automático enviado via WhatsApp (lead do site).",
        )
        return

    metadata["first_contact_failed"] = {"reason": reason, "at": now.isoformat()}
    matter.sdr_qualification = metadata

    await TaskService(db).create_automatic(
        matter.id,
        TaskCreate(
            title="Primeiro contato automático falhou — agir manualmente",
            description=(
                f"O disparo automático do primeiro contato com o lead do site falhou "
                f"(motivo: {reason}). Entre em contato manualmente."
            ),
            priority=TaskPriority.alta,
            is_ai_generated=True,
            idempotency_key=f"site_lead_first_contact_failed:{matter.id}",
        ),
        workspace_id,
        source="lead_webhook.first_contact_failed",
        preferred_owner_id=matter.responsible_human_id,
        correlation_id=f"lead_webhook:first_contact_failed:{matter.id}",
    )
    await _record_audit_event(
        db,
        workspace_id=workspace_id,
        matter_id=matter.id,
        event_type="lead_webhook.first_contact_failed",
        summary=f"Primeiro contato automático falhou: {reason}.",
        after_data={"reason": reason},
    )


@dataclass
class LeadIngestResult:
    accepted: bool
    reason: str
    matter_id: Optional[UUID] = None
    client_id: Optional[UUID] = None
    reused_existing: bool = False


async def ingest_lead(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    connection: IntegrationConnection,
    payload: dict[str, Any],
    source_label: str = "site",
) -> LeadIngestResult:
    from app.services.sdr_intake_service import _create_lead_client_and_matter

    parsed = parse_lead_payload(payload)

    if parsed.honeypot_triggered:
        logger.info("lead_webhook.honeypot_triggered", workspace_id=str(workspace_id))
        return LeadIngestResult(accepted=False, reason="honeypot")

    if not parsed.name and not parsed.email and not parsed.phone:
        logger.warning("lead_webhook.payload_rejected_empty", workspace_id=str(workspace_id))
        return LeadIngestResult(accepted=False, reason="payload_sem_dados_uteis")

    existing_client = await _find_recent_duplicate(
        db, workspace_id=workspace_id, email=parsed.email, phone=parsed.phone
    )
    if existing_client is not None:
        matter = await db.scalar(
            select(Matter)
            .where(
                Matter.workspace_id == workspace_id,
                Matter.client_id == existing_client.id,
                Matter.deleted_at.is_(None),
            )
            .order_by(Matter.updated_at.desc())
            .limit(1)
        )
        if matter is not None:
            await _record_audit_event(
                db,
                workspace_id=workspace_id,
                matter_id=matter.id,
                event_type="lead_webhook.duplicate_submission",
                summary=f"Novo envio do site ({source_label}) — lead reaproveitado (dedupe 24h).",
                after_data={"utms": parsed.utms} if parsed.utms else None,
            )
            await db.commit()
            return LeadIngestResult(
                accepted=True,
                reason="dedupe_reused",
                matter_id=matter.id,
                client_id=existing_client.id,
                reused_existing=True,
            )

    display_name = parsed.name or parsed.email or parsed.phone or "Lead do site"
    demand_type = "A definir — lead via formulário do site"
    if parsed.message:
        demand_type = parsed.message[:200]

    client, matter = await _create_lead_client_and_matter(
        db,
        workspace_id=workspace_id,
        name=display_name,
        lead_source="site",
        demand_type=demand_type,
        phone=parsed.phone,
        email=parsed.email,
    )
    matter.column_id = KanbanColumnId.novo_lead
    if parsed.utms or parsed.extras or parsed.click_ids:
        current_metadata = dict(matter.sdr_qualification or {})
        if parsed.utms:
            current_metadata["utms"] = parsed.utms
        if parsed.click_ids:
            current_metadata["clickIds"] = parsed.click_ids
        if parsed.extras:
            current_metadata["site_lead_extras"] = parsed.extras
        matter.sdr_qualification = current_metadata
    await db.flush()

    await _record_audit_event(
        db,
        workspace_id=workspace_id,
        matter_id=matter.id,
        event_type="lead_webhook.lead_created",
        summary=f"Lead criado via formulário do site ({source_label}).",
        after_data={"utms": parsed.utms} if parsed.utms else None,
    )

    if parsed.utms or parsed.click_ids:
        # Fecha o loop anúncio→caso→receita (item 9 da auditoria) também para
        # leads que chegam pelo formulário do site em vez de um Lead Ads
        # nativo: sem este AcquisitionEvent, gclid/fbclid capturados pelo
        # navegador do lead nunca alimentam `tracking_summary`/attribution_rows
        # nem o feedback de conversão (`_ensure_conversion_feedback_intent`).
        from app.schemas.klm_operations import AcquisitionEventCreate
        from app.services.klm_operations_service import KlmOperationsService

        try:
            await KlmOperationsService(db).record_acquisition_event(
                workspace_id,
                None,
                AcquisitionEventCreate(
                    matter_id=matter.id,
                    client_id=client.id,
                    event_name="conversation_started",
                    channel="site",
                    source=parsed.utms.get("utm_source"),
                    medium=parsed.utms.get("utm_medium"),
                    campaign=parsed.utms.get("utm_campaign"),
                    dedupe_key=f"site-lead:{matter.id}",
                    click_ids=parsed.click_ids,
                    event_metadata={
                        "sourceLabel": source_label,
                        "utmTerm": parsed.utms.get("utm_term"),
                        "utmContent": parsed.utms.get("utm_content"),
                    },
                ),
            )
        except (ValueError, LookupError) as exc:  # noqa: BLE001 — nunca derruba o lead
            logger.error(
                "lead_webhook.acquisition_event_failed",
                matter_id=str(matter.id),
                error_type=safe_error_type(exc),
            )

    await _get_or_create_thread(
        db,
        workspace_id=workspace_id,
        client=client,
        matter=matter,
        phone=parsed.phone,
        display_name=display_name,
    )

    await _attempt_first_contact(
        db,
        workspace_id=workspace_id,
        matter=matter,
        connection=connection,
        phone=parsed.phone,
    )

    from app.workers.event_bus import dispatch_new_lead

    try:
        await dispatch_new_lead(
            matter.id,
            workspace_id=workspace_id,
            dedupe_key=f"site-lead:{matter.id}",
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, o lead já está durável
        logger.error(
            "lead_webhook.sdr_dispatch_failed",
            matter_id=str(matter.id),
            error_type=safe_error_type(exc),
        )

    await db.commit()
    return LeadIngestResult(
        accepted=True,
        reason="created",
        matter_id=matter.id,
        client_id=client.id,
        reused_existing=False,
    )
