"""
Serviço: Webhook de Lead Form do Google Ads (auditoria 2026-07-26, item 4).

Contrato `WebhookLead` verificado em
`docs/GOOGLE_TRACKING_KNOWLEDGE_2026-07.md` §2
(developers.google.com/google-ads/webhook/docs/implementation):

  * `google_key` — chave configurada no Google Ads; um único endpoint público
    atende todos os workspaces, cada um com sua própria chave (mesmo desenho
    do token do F12, `lead_webhook_service.py`, adaptado porque aqui a chave
    vem DENTRO do payload, não na URL — é assim que o Google Ads a envia).
    Validação em tempo constante contra o conjunto de integrações
    `google_ads` com webhook configurado.
  * `lead_id` — entrega NÃO é exactly-once → dedupe obrigatório. Reaproveita
    a garantia de unicidade de `AcquisitionEvent(workspace_id, dedupe_key)`:
    a claim de idempotência é inserida ANTES de criar Client/Matter (sem
    matter/client ainda), e só then o evento é promovido com os ids reais —
    um retry do Google nunca duplica o caso.
  * `is_test` — aceitar com 200, marcar como teste, nunca criar caso real.
  * `user_column_data[]` — `column_id` tipado (FULL_NAME, EMAIL,
    PHONE_NUMBER…); `column_name` está depreciado e é ignorado.
  * `gcl_id` — persistido em `AcquisitionEvent.click_ids["gclid"]` para
    fechar o loop lead → CRM → conversão offline (Data Manager API, ver
    `google_conversions_service.py`).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.models.finance import IntegrationConnection, IntegrationProvider, IntegrationStatus
from app.models.klm_operations import (
    AcquisitionEvent,
    KlmAuditEvent,
    OmnichannelChannel,
    OmnichannelThread,
)
from app.models.matter import KanbanColumnId, Matter
from app.models.omnichannel_v6 import OmnichannelIdentity
from app.schemas.klm_operations import AcquisitionEventCreate
from app.services.integration_secrets import protect_integration_settings
from app.services.klm_operations_service import KlmOperationsService

GOOGLE_LEAD_WEBHOOK_LABEL = "Google Lead Form Webhook"
MAX_PAYLOAD_BYTES = 32 * 1024

_COLUMN_NAME_ALIASES = {
    "FULL_NAME": "name",
    "FIRST_NAME": "first_name",
    "LAST_NAME": "last_name",
    "EMAIL": "email",
    "PHONE_NUMBER": "phone",
}


def _hash_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def generate_or_rotate_google_key(db: AsyncSession, *, workspace_id: UUID) -> str:
    """Gera (ou rotaciona) a `google_key` que o workspace configura no Google
    Ads. Retornada em texto plano uma única vez — só o hash fica persistido."""
    connection = await db.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider == IntegrationProvider.google_ads,
            IntegrationConnection.label == GOOGLE_LEAD_WEBHOOK_LABEL,
        )
    )
    plain_key = secrets.token_urlsafe(32)
    key_hash = _hash_key(plain_key)

    if connection is None:
        connection = IntegrationConnection(
            workspace_id=workspace_id,
            provider=IntegrationProvider.google_ads,
            label=GOOGLE_LEAD_WEBHOOK_LABEL,
            status=IntegrationStatus.connected,
            settings={},
        )
        db.add(connection)

    existing_settings = dict(connection.settings or {})
    existing_settings["googleKeyHash"] = key_hash
    existing_settings["googleKeyPrefix"] = plain_key[:8]
    connection.settings = protect_integration_settings(existing_settings)
    connection.status = IntegrationStatus.connected
    await db.flush()
    return plain_key


async def resolve_workspace_by_google_key(db: AsyncSession, google_key: str) -> UUID | None:
    """Resolve o workspace dono da `google_key`, em tempo constante contra o
    conjunto de integrações `google_ads` com webhook configurado — mesmo
    padrão de `lead_webhook_service.resolve_workspace_by_token`."""
    if not google_key:
        return None
    candidate_hash = _hash_key(google_key)
    result = await db.execute(
        select(IntegrationConnection).where(
            IntegrationConnection.provider == IntegrationProvider.google_ads,
            IntegrationConnection.label == GOOGLE_LEAD_WEBHOOK_LABEL,
            IntegrationConnection.status != IntegrationStatus.disconnected,
        )
    )
    matched_workspace_id: UUID | None = None
    for connection in result.scalars().all():
        stored_hash = str((connection.settings or {}).get("googleKeyHash") or "")
        if not stored_hash:
            continue
        if hmac.compare_digest(stored_hash, candidate_hash):
            matched_workspace_id = connection.workspace_id
            # Sem early-return: percorre todo o conjunto candidato (mitigação
            # básica de canal-lateral, mesmo padrão do Asaas/F12).
    return matched_workspace_id


@dataclass
class ParsedGoogleLead:
    lead_id: str
    is_test: bool
    gcl_id: str | None
    campaign_id: str | None
    adgroup_id: str | None
    creative_id: str | None
    asset_group_id: str | None
    form_id: str | None
    lead_submit_time: str | None
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    extra_fields: dict[str, str] = field(default_factory=dict)


def _column_value(entry: dict[str, Any]) -> str | None:
    value = entry.get("string_value")
    if value is None:
        value = entry.get("stringValue")
    text = str(value).strip() if value is not None else ""
    return text or None


def parse_webhook_lead_payload(payload: dict[str, Any]) -> ParsedGoogleLead | None:
    lead_id = str(payload.get("lead_id") or payload.get("leadId") or "").strip()
    if not lead_id:
        return None

    fields: dict[str, str] = {}
    for entry_value in payload.get("user_column_data") or payload.get("userColumnData") or []:
        if not isinstance(entry_value, dict):
            continue
        # `column_name` está depreciado (docs/GOOGLE_TRACKING_KNOWLEDGE_2026-07.md
        # §2) — só `column_id` é fonte de verdade.
        column_id = (
            str(entry_value.get("column_id") or entry_value.get("columnId") or "")
            .strip()
            .upper()
        )
        value = _column_value(entry_value)
        if not column_id or value is None:
            continue
        key = _COLUMN_NAME_ALIASES.get(column_id)
        if key:
            fields[key] = value
        else:
            fields[column_id.lower()] = value

    name = fields.get("name") or " ".join(
        part for part in (fields.get("first_name"), fields.get("last_name")) if part
    ).strip() or None
    email = fields.get("email")
    phone = fields.get("phone")
    extras = {
        key: value
        for key, value in fields.items()
        if key not in {"name", "first_name", "last_name", "email", "phone"}
    }

    return ParsedGoogleLead(
        lead_id=lead_id,
        is_test=bool(payload.get("is_test") or payload.get("isTest")),
        gcl_id=str(payload.get("gcl_id") or payload.get("gclId") or "").strip() or None,
        campaign_id=str(
            payload.get("campaign_id") or payload.get("campaignId") or ""
        ).strip()
        or None,
        adgroup_id=str(payload.get("adgroup_id") or payload.get("adgroupId") or "").strip()
        or None,
        creative_id=str(payload.get("creative_id") or payload.get("creativeId") or "").strip()
        or None,
        asset_group_id=str(
            payload.get("asset_group_id") or payload.get("assetGroupId") or ""
        ).strip()
        or None,
        form_id=str(payload.get("form_id") or payload.get("formId") or "").strip() or None,
        lead_submit_time=str(
            payload.get("lead_submit_time") or payload.get("leadSubmitTime") or ""
        ).strip()
        or None,
        name=name,
        email=email,
        phone=re.sub(r"\D", "", phone) if phone else None,
        extra_fields=extras,
    )


@dataclass
class GoogleLeadIngestResult:
    accepted: bool
    reason: str
    matter_id: UUID | None = None
    client_id: UUID | None = None


async def _get_or_create_thread(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    client,
    matter: Matter,
    phone: str | None,
    display_name: str,
) -> OmnichannelThread:
    identity = OmnichannelIdentity(
        workspace_id=workspace_id,
        client_id=client.id,
        display_name=display_name,
        canonical_phone=phone,
        identity_state="provisional",
        source="google_lead_form",
    )
    db.add(identity)
    await db.flush()

    thread = OmnichannelThread(
        workspace_id=workspace_id,
        matter_id=matter.id,
        client_id=client.id,
        identity_id=identity.id,
        primary_channel=OmnichannelChannel.form,
        subject=f"Lead do Google Ads — {display_name}",
        queue="new",
        thread_metadata={"origin": "google_lead_form"},
    )
    db.add(thread)
    await db.flush()
    return thread


async def ingest_google_lead(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    payload: dict[str, Any],
) -> GoogleLeadIngestResult:
    from app.services.sdr_intake_service import _create_lead_client_and_matter

    parsed = parse_webhook_lead_payload(payload)
    if parsed is None:
        logger.warning(
            "google_lead_webhook.payload_missing_lead_id", workspace_id=str(workspace_id)
        )
        return GoogleLeadIngestResult(accepted=False, reason="lead_id_ausente")

    dedupe_key = f"google-lead:{parsed.lead_id}"

    # Um lead_id já processado (Google não garante entrega exactly-once) tem
    # que ser reconhecido ANTES de tentar reinserir a claim: depois que o
    # Matter/Client existe, a claim original é promovida com esses ids (ver
    # abaixo) — uma segunda tentativa de inserção com matter_id=None bateria
    # no "replay" idempotente e seria (corretamente) rejeitada por não bater
    # com o evento já promovido. Checar primeiro evita esse falso conflito.
    already_processed = await db.scalar(
        select(AcquisitionEvent).where(
            AcquisitionEvent.workspace_id == workspace_id,
            AcquisitionEvent.dedupe_key == dedupe_key,
        )
    )
    if already_processed is not None:
        return GoogleLeadIngestResult(
            accepted=True,
            reason="dedupe_reused",
            matter_id=already_processed.matter_id,
            client_id=already_processed.client_id,
        )

    # Claim de idempotência ANTES de criar Client/Matter: reaproveita o
    # UNIQUE(workspace_id, dedupe_key) de AcquisitionEvent (matter_id/client_id
    # são opcionais no schema) para proteger contra a corrida de dois
    # webhooks concorrentes para o mesmo lead_id.
    click_ids = {"gclid": parsed.gcl_id} if parsed.gcl_id else {}
    acquisition_event, reused = await KlmOperationsService(db).record_acquisition_event(
        workspace_id,
        None,
        AcquisitionEventCreate(
            event_name="conversation_started",
            channel="google_ads",
            campaign=parsed.campaign_id,
            dedupe_key=dedupe_key,
            click_ids=click_ids,
            event_metadata={
                "leadId": parsed.lead_id,
                "isTest": parsed.is_test,
                "adgroupId": parsed.adgroup_id,
                "creativeId": parsed.creative_id,
                "assetGroupId": parsed.asset_group_id,
                "formId": parsed.form_id,
                "leadSubmitTime": parsed.lead_submit_time,
            },
        ),
    )
    if reused:
        return GoogleLeadIngestResult(
            accepted=True,
            reason="dedupe_reused",
            matter_id=acquisition_event.matter_id,
            client_id=acquisition_event.client_id,
        )

    if parsed.is_test:
        await db.commit()
        return GoogleLeadIngestResult(accepted=True, reason="test_lead_ignored")

    display_name = (
        parsed.name or parsed.email or parsed.phone or f"Lead Google Ads {parsed.lead_id[:8]}"
    )
    demand_type = "A definir — lead via Google Ads Lead Form"

    client, matter = await _create_lead_client_and_matter(
        db,
        workspace_id=workspace_id,
        name=display_name,
        lead_source="google_ads",
        demand_type=demand_type,
        phone=parsed.phone,
        email=parsed.email,
    )
    matter.column_id = KanbanColumnId.novo_lead
    if parsed.extra_fields:
        matter.sdr_qualification = {"google_lead_extras": parsed.extra_fields}

    # Promove a claim de idempotência com os ids reais (mesma transação).
    acquisition_event.matter_id = matter.id
    acquisition_event.client_id = client.id
    await db.flush()

    await _get_or_create_thread(
        db,
        workspace_id=workspace_id,
        client=client,
        matter=matter,
        phone=parsed.phone,
        display_name=display_name,
    )

    db.add(
        KlmAuditEvent(
            workspace_id=workspace_id,
            actor_type="system",
            actor_label="Webhook de Lead Form do Google Ads",
            event_type="google_lead_webhook.lead_created",
            entity_type="matter",
            entity_id=matter.id,
            summary="Lead criado via Google Ads Lead Form Webhook.",
            after_data={"leadId": parsed.lead_id, "gclid": parsed.gcl_id},
            correlation_id=dedupe_key,
        )
    )
    await db.flush()

    from app.workers.event_bus import dispatch_new_lead

    try:
        await dispatch_new_lead(
            matter.id,
            workspace_id=workspace_id,
            dedupe_key=f"google-lead-sdr:{matter.id}",
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, lead já está durável
        logger.error(
            "google_lead_webhook.sdr_dispatch_failed",
            matter_id=str(matter.id),
            error_type=safe_error_type(exc),
        )

    await db.commit()
    return GoogleLeadIngestResult(
        accepted=True,
        reason="created",
        matter_id=matter.id,
        client_id=client.id,
    )
