"""
SDR Intake Service — Intake automático de leads via WhatsApp.
"""
import asyncio
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.core.logging_privacy import opaque_ref, safe_error_type
from app.services.conversation_insight_service import ConversationInsightService
from app.services.knowledge_service import KnowledgeService
from app.services.whatsapp.media_security import trusted_meta_object_id


class InboundMediaStorageState(str, Enum):
    available = "available"
    retry_required = "retry_required"
    unavailable = "unavailable"


@dataclass(frozen=True)
class InboundMediaStorageResolution:
    state: InboundMediaStorageState
    storage_key: str | None
    object_id: UUID | None
    version: int | None
    reason: str | None
    retryable: bool
    reconciliation_required: bool

    def pipeline_state(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "storageKey": self.storage_key,
            "objectId": str(self.object_id) if self.object_id else None,
            "version": self.version,
            "reason": self.reason,
            "retryable": self.retryable,
            "reconciliationRequired": self.reconciliation_required,
        }


def _missing_inbound_media_storage(
    *,
    local_reason: str,
) -> InboundMediaStorageResolution:
    return InboundMediaStorageResolution(
        state=InboundMediaStorageState.retry_required,
        storage_key=None,
        object_id=None,
        version=None,
        reason=("storage_object_missing" if settings.is_production else local_reason),
        retryable=True,
        reconciliation_required=True,
    )


async def resolve_inbound_media_storage(
    *,
    db: AsyncSession,
    workspace_id: UUID,
    candidate_reference: str | None,
) -> InboundMediaStorageResolution:
    """Resolve only a proven tenant-bound StorageObject; never interpret provider IDs."""
    from app.models.storage_object import StorageAVStatus, StorageObject, StorageObjectState

    if not candidate_reference:
        return _missing_inbound_media_storage(
            local_reason="versioned_storage_not_reconciled",
        )

    record = await db.scalar(
        select(StorageObject)
        .where(
            StorageObject.workspace_id == workspace_id,
            StorageObject.storage_key == candidate_reference,
        )
        .order_by(StorageObject.version.desc())
        .limit(1)
    )
    if record is None:
        return _missing_inbound_media_storage(
            local_reason="unversioned_media_reference",
        )
    if record.deleted_at is not None:
        return InboundMediaStorageResolution(
            state=InboundMediaStorageState.unavailable,
            storage_key=None,
            object_id=record.object_id,
            version=record.version,
            reason="storage_object_deleted",
            retryable=False,
            reconciliation_required=False,
        )
    if record.state is StorageObjectState.quarantined:
        if record.av_status is StorageAVStatus.infected:
            return InboundMediaStorageResolution(
                state=InboundMediaStorageState.unavailable,
                storage_key=None,
                object_id=record.object_id,
                version=record.version,
                reason="storage_object_infected",
                retryable=False,
                reconciliation_required=False,
            )
        return InboundMediaStorageResolution(
            state=InboundMediaStorageState.retry_required,
            storage_key=record.storage_key,
            object_id=record.object_id,
            version=record.version,
            reason=(
                "storage_object_scan_error"
                if record.av_status is StorageAVStatus.error
                else "storage_object_awaiting_clean_scan"
            ),
            retryable=True,
            reconciliation_required=True,
        )
    if (
        record.state is not StorageObjectState.available
        or record.byte_size is None
        or not record.checksum_sha256
        or (
            settings.is_production
            and record.av_status is not StorageAVStatus.clean
        )
    ):
        return InboundMediaStorageResolution(
            state=InboundMediaStorageState.retry_required,
            storage_key=None,
            object_id=record.object_id,
            version=record.version,
            reason="storage_object_not_available",
            retryable=True,
            reconciliation_required=True,
        )
    return InboundMediaStorageResolution(
        state=InboundMediaStorageState.available,
        storage_key=record.storage_key,
        object_id=record.object_id,
        version=record.version,
        reason=None,
        retryable=False,
        reconciliation_required=False,
    )


async def handle_inbound_lead(
    db: AsyncSession,
    contact_id: UUID,
    phone: str,
    contact_name: Optional[str],
    message_body: str,
    media_s3_key: Optional[str] = None,
    wamid: Optional[str] = None,
) -> None:
    """
    Ponto de entrada chamado pelo WhatsAppService após persistir mensagem inbound.

    Fluxo:
    1. Se contato não tem Matter → criar Client + Matter + vincular + disparar SDR
    2. Se Matter existe e SDR não concluído → continuar qualificação e responder
    3. Se Matter existe e SDR concluído → não interferir (equipe humana assume)
    """
    from sqlalchemy import select

    from app.models.klm_operations import KlmAuditEvent
    from app.models.matter import Matter
    from app.models.whatsapp import WaQueueStatus, WhatsAppContact
    from app.workers.event_bus import dispatch_new_lead, dispatch_whatsapp_message

    # Recarregar contato para ter matter_id atualizado
    result = await db.execute(select(WhatsAppContact).where(WhatsAppContact.id == contact_id))
    contact = result.scalars().first()
    if not contact:
        return

    # S1: com um humano na conversa a IA cala. O CRM continua (cliente, caso,
    # mídia, índice), só o despacho do SDR é suprimido, com auditoria.
    human_active = contact.queue_status == WaQueueStatus.human
    if human_active:
        db.add(
            KlmAuditEvent(
                workspace_id=contact.workspace_id,
                actor_user_id=None,
                actor_type="system",
                actor_label="SDR",
                event_type="whatsapp_sdr_dispatch_suppressed_human_active",
                entity_type="whatsapp_contact",
                entity_id=contact.id,
                summary="Resposta do SDR suprimida: conversa em atendimento humano.",
                after_data={
                    "assignedUserId": (
                        str(contact.assigned_user_id) if contact.assigned_user_id else None
                    ),
                    "humanControlState": getattr(contact.human_control_state, "value", None),
                    "wamid": wamid,
                },
            )
        )
        logger.info(
            "sdr_intake.dispatch_suppressed_human_active",
            contact_ref=opaque_ref(str(contact.id), kind="contact"),
        )

    if not contact.matter_id:
        # ── Novo lead: criar Client + Matter ──
        matter_id = await _create_client_and_matter(db, contact, phone, contact_name)
        if not matter_id:
            return
        if not human_active:
            # Disparar SDR via EventBus em background
            await dispatch_new_lead(
                matter_id,
                workspace_id=contact.workspace_id,
                dedupe_key=f"whatsapp:{wamid}" if wamid else None,
            )
        logger.info(
            "sdr_intake.new_lead_created",
            matter_id=str(matter_id),
            phone_ref=opaque_ref(phone, kind="phone"),
        )
    else:
        # ── Lead existente: continuar qualificação ──
        matter_id = contact.matter_id
        result = await db.execute(select(Matter).where(Matter.id == matter_id))
        matter = result.scalars().first()
        if not matter:
            return

        sdr_qual = matter.sdr_qualification or {}
        sdr_completed = sdr_qual.get("completed", False)

        if not sdr_completed and not human_active:
            await dispatch_whatsapp_message(
                matter_id=matter_id,
                contact_id=contact_id,
                inbound_text=message_body,
                wamid=wamid,
                workspace_id=contact.workspace_id,
            )
        else:
            logger.debug(
                "sdr_intake.sdr_already_completed",
                matter_id=str(matter_id),
                phone_ref=opaque_ref(phone, kind="phone"),
            )

    if matter_id:
        resolved_media_type = _guess_media_type_from_key(media_s3_key)
        if media_s3_key:
            resolved_media_type = await _ensure_whatsapp_media_document(
                db=db,
                workspace_id=contact.workspace_id,
                matter_id=matter_id,
                media_s3_key=media_s3_key,
                media_type=resolved_media_type,
            )
        await KnowledgeService(db).index_whatsapp_message(
            workspace_id=contact.workspace_id,
            matter_id=matter_id,
            source_ref=wamid or f"{contact_id}:{message_body[:40]}",
            body=message_body,
            phone=phone,
            contact_name=contact_name,
            media_type=resolved_media_type if media_s3_key else None,
            media_url=media_s3_key,
        )
        matter_result = await db.execute(select(Matter).where(Matter.id == matter_id))
        matter = matter_result.scalars().first()
        if matter:
            await record_sdr_lead_qualified(db, matter)
            await ConversationInsightService(db).generate_snapshot(
                matter=matter,
                stage_key="entrada" if matter.column_id.value == "entrada" else "coleta_dados",
            )
        await db.commit()


async def record_sdr_lead_qualified(db: AsyncSession, matter) -> None:
    """Emite lead_qualified (idempotente) quando a qualificação do SDR está completa.

    A transição de `sdr_qualification.completed` acontece no worker do agente SDR;
    este ponto observa o estado persistido e registra o marco uma única vez,
    herdando os click IDs de aquisição capturados no thread canônico (CTWA).
    """
    qualification = matter.sdr_qualification or {}
    if not qualification.get("completed"):
        return

    from app.models.klm_operations import AcquisitionEvent, OmnichannelThread
    from app.schemas.klm_operations import AcquisitionEventCreate
    from app.services.klm_operations_service import KlmOperationsService

    dedupe_key = f"sdr-qualified:{matter.id}"
    existing = await db.scalar(
        select(AcquisitionEvent.id).where(
            AcquisitionEvent.workspace_id == matter.workspace_id,
            AcquisitionEvent.dedupe_key == dedupe_key,
        )
    )
    if existing is not None:
        return

    click_ids: dict[str, str] = {}
    event_metadata: dict[str, object] = {}
    thread = await db.scalar(
        select(OmnichannelThread)
        .where(
            OmnichannelThread.workspace_id == matter.workspace_id,
            OmnichannelThread.matter_id == matter.id,
        )
        .order_by(OmnichannelThread.created_at.asc())
        .limit(1)
    )
    if thread is not None:
        acquisition = (thread.thread_metadata or {}).get("acquisition")
        if isinstance(acquisition, dict):
            raw_click_ids = acquisition.get("clickIds")
            if isinstance(raw_click_ids, dict):
                click_ids = {
                    str(key): str(value).strip()
                    for key, value in raw_click_ids.items()
                    if str(value or "").strip()
                }
        event_metadata["threadId"] = str(thread.id)

    await KlmOperationsService(db).record_acquisition_event(
        matter.workspace_id,
        None,
        AcquisitionEventCreate(
            matter_id=matter.id,
            client_id=matter.client_id,
            event_name="lead_qualified",
            channel="whatsapp",
            dedupe_key=dedupe_key,
            click_ids=click_ids,
            event_metadata=event_metadata,
        ),
    )
    logger.info(
        "sdr_intake.lead_qualified_recorded",
        matter_id=str(matter.id),
    )


async def _create_lead_client_and_matter(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    name: str,
    lead_source: str,
    demand_type: str,
    phone: Optional[str] = None,
    email: Optional[str] = None,
):
    """Núcleo canal-agnóstico: cria Client + Matter na coluna 'entrada' para um novo lead.

    Reutilizado pelo intake WhatsApp (`_create_client_and_matter`) e pelo intake
    social (`handle_inbound_social_lead`). Não vincula a nenhum contato/thread
    específico de canal — isso é responsabilidade do chamador.
    """
    from app.data.legal_area_catalog import infer_area
    from app.models.client import Client
    from app.models.matter import (
        ClientClassification,
        KanbanColumnId,
        Matter,
        MicroStatus,
        PracticeArea,
        Priority,
        Urgency,
    )

    client = Client(
        workspace_id=workspace_id,
        name=name,
        phone=phone,
        email=email,
    )
    db.add(client)
    await db.flush()

    inferred_area = infer_area(demand_type)
    legal_area_key = inferred_area["areaKey"] if inferred_area else "triagem_geral"
    try:
        practice_area = PracticeArea(legal_area_key)
    except ValueError:
        # ``practice_area`` remains a legacy non-null enum.  The extensible
        # legal_area_key is authoritative and keeps an unclassified intake
        # explicit instead of inventing a legal specialty.
        practice_area = PracticeArea.civel

    matter = Matter(
        workspace_id=workspace_id,
        client_id=client.id,
        demand_type=demand_type,
        practice_area=practice_area,
        legal_area_key=legal_area_key,
        column_id=KanbanColumnId.entrada,
        micro_status=MicroStatus.lead_novo,
        priority=Priority.media,
        urgency=Urgency.normal,
        classification=ClientClassification.Bom,
        lead_source=lead_source,
    )
    db.add(matter)
    await db.flush()
    return client, matter


async def _create_client_and_matter(
    db: AsyncSession,
    contact,
    phone: str,
    contact_name: Optional[str],
) -> Optional[UUID]:
    """Cria Client e Matter para um novo lead e vincula ao contato WhatsApp."""
    _client, matter = await _create_lead_client_and_matter(
        db,
        workspace_id=contact.workspace_id,
        name=contact_name or phone,
        lead_source="whatsapp",
        demand_type="A definir — lead via WhatsApp",
        phone=phone,
    )

    # Vincular contato ao Matter
    contact.matter_id = matter.id
    await db.commit()

    logger.info(
        "sdr_intake.client_matter_created",
        client_id=str(_client.id),
        matter_id=str(matter.id),
        phone_ref=opaque_ref(phone, kind="phone"),
    )
    return matter.id


async def handle_inbound_social_lead(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    thread_id: UUID,
    identity_id: UUID,
    channel: str,
    event_type: str,
    correlation: dict | None,
    display_name: Optional[str],
) -> Optional[UUID]:
    """Intake social: cria Client + Matter a partir de uma DM inbound (instagram/messenger)
    numa thread ainda sem caso vinculado, com origem rastreável.

    Chamado pelo `OmnichannelV6Service.ingest_event` no momento em que uma mensagem
    direta (comment-to-DM, story reply/mention ou DM espontânea) consolida a thread.
    Idempotente por thread: `AcquisitionEvent(conversation_started)` usa
    `dedupe_key="social:{thread_id}"`; se a thread já tem `matter_id`, não duplica.
    """
    from app.models.klm_operations import AcquisitionEvent, OmnichannelThread
    from app.models.omnichannel_v6 import OmnichannelIdentity
    from app.schemas.klm_operations import AcquisitionEventCreate
    from app.services.klm_operations_service import KlmOperationsService
    from app.workers.event_bus import dispatch_new_lead

    correlation = correlation or {}

    thread = await db.scalar(
        select(OmnichannelThread)
        .where(
            OmnichannelThread.workspace_id == workspace_id,
            OmnichannelThread.id == thread_id,
        )
        .with_for_update()
    )
    if thread is None:
        return None
    if thread.matter_id is not None:
        # Já existe caso vinculado a esta conversa; intake social não duplica.
        return thread.matter_id

    identity = await db.scalar(
        select(OmnichannelIdentity).where(
            OmnichannelIdentity.workspace_id == workspace_id,
            OmnichannelIdentity.id == identity_id,
        )
    )

    has_comment_origin = bool(correlation.get("postId") or correlation.get("commentId"))
    is_story = event_type in {"story_reply", "story_mention"}
    if is_story:
        origin = "story"
        demand_type = "Resposta de story" if event_type == "story_reply" else "Menção em story"
    elif has_comment_origin:
        origin = "comment"
        demand_type = "Comentário em post"
    else:
        origin = "direct"
        demand_type = f"Mensagem direta via {channel}"

    lead_name = (display_name or "").strip() or f"Lead via {channel}"
    client, matter = await _create_lead_client_and_matter(
        db,
        workspace_id=workspace_id,
        name=lead_name,
        lead_source=channel,
        demand_type=demand_type,
    )

    thread.client_id = client.id
    thread.matter_id = matter.id
    click_ids = await _social_click_ids(
        db, workspace_id=workspace_id, identity_id=identity_id, channel=channel
    )
    metadata = dict(thread.thread_metadata or {})
    acquisition = {
        key: value
        for key, value in {
            "provider": channel,
            "source": origin,
            "adId": correlation.get("adId"),
            "postId": correlation.get("postId"),
            "storyId": correlation.get("storyId"),
            # Código do deep link m.me/ig.me que originou a conversa (ver
            # `social_event_adapter_service._meta_message`). Guardado mesmo sem
            # adId: é a única evidência de origem de conversas iniciadas por link.
            "ref": correlation.get("ref"),
        }.items()
        if value is not None
    }
    if click_ids:
        acquisition["clickIds"] = click_ids
    metadata.setdefault("acquisition", acquisition)
    social_lead = dict(metadata.get("socialLead") or {})
    social_lead.update(
        {
            "origin": origin,
            "channel": channel,
            "hot": is_story,
            "matterId": str(matter.id),
        }
    )
    metadata["socialLead"] = social_lead
    thread.thread_metadata = metadata
    if is_story:
        thread.priority = max(thread.priority, 90)

    if identity is not None and identity.client_id is None:
        identity.client_id = client.id

    await db.flush()

    dedupe_key = f"social:{thread_id}"
    event_metadata: dict[str, object] = {"threadId": str(thread_id), "origin": origin}
    if correlation.get("adId"):
        event_metadata["adId"] = correlation["adId"]
    if correlation.get("postId"):
        event_metadata["postId"] = correlation["postId"]
    if correlation.get("storyId"):
        event_metadata["storyId"] = correlation["storyId"]
    if correlation.get("ref"):
        event_metadata["ref"] = correlation["ref"]

    existing_event = await db.scalar(
        select(AcquisitionEvent.id).where(
            AcquisitionEvent.workspace_id == workspace_id,
            AcquisitionEvent.dedupe_key == dedupe_key,
        )
    )
    if existing_event is None:
        await KlmOperationsService(db).record_acquisition_event(
            workspace_id,
            None,
            AcquisitionEventCreate(
                matter_id=matter.id,
                client_id=client.id,
                event_name="conversation_started",
                channel=channel,
                dedupe_key=dedupe_key,
                click_ids=click_ids,
                event_metadata=event_metadata,
            ),
        )

    from app.core.klm_runtime import external_delivery_allowed

    if external_delivery_allowed():
        # Mesmo gate que protege o disparo do SDR no trilho WhatsApp
        # (`webhook_service.py`): a qualificação roda pelos trilhos existentes,
        # nunca por um transporte próprio criado para o canal social.
        # O disparo é best-effort: o intake (Client/Matter/AcquisitionEvent) já
        # está durável neste ponto; uma falha no enfileiramento do agente não pode
        # derrubar o ingest do evento canônico (mesmo padrão de tolerância do
        # `webhook_service.py:_handle_inbound_message`, que também loga e segue).
        try:
            await dispatch_new_lead(
                matter.id,
                workspace_id=workspace_id,
                dedupe_key=f"social-lead:{thread_id}",
            )
        except Exception as exc:  # noqa: BLE001 — best-effort, ver comentário acima
            logger.error(
                "sdr_intake.social_lead_dispatch_failed",
                matter_id=str(matter.id),
                thread_id=str(thread_id),
                error_type=safe_error_type(exc),
            )

    logger.info(
        "sdr_intake.social_lead_created",
        matter_id=str(matter.id),
        thread_id=str(thread_id),
        channel=channel,
        origin=origin,
    )
    return matter.id


_SOCIAL_CLICK_ID_KEY = {
    "instagram": "igsid",
    "messenger": "psid",
    "facebook": "psid",
}
_SCOPED_USER_ID_RE = re.compile(r"^[0-9]{5,32}$")


async def _social_click_ids(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    identity_id: UUID,
    channel: str,
) -> dict[str, str]:
    """Resolve o click id (psid/igsid) da identidade social para o canal informado.

    O identificador page-scoped do usuário (PSID no Messenger/Facebook, IGSID no
    Instagram) mora em `OmnichannelEndpoint.external_user_id` — é o mesmo valor
    que `ads_conversion_feedback_service._SCOPED_USER_ID_RE` exige (só dígitos,
    5-32 caracteres) para aceitar o match key no envelope CAPI. Endpoints que não
    batem o formato são ignorados (fail-closed: nunca manda lixo pro CAPI).
    """
    from app.models.omnichannel_v6 import OmnichannelEndpoint

    key = _SOCIAL_CLICK_ID_KEY.get((channel or "").strip().lower())
    if key is None:
        return {}

    external_user_id = await db.scalar(
        select(OmnichannelEndpoint.external_user_id)
        .where(
            OmnichannelEndpoint.workspace_id == workspace_id,
            OmnichannelEndpoint.identity_id == identity_id,
            OmnichannelEndpoint.channel == channel,
        )
        .order_by(OmnichannelEndpoint.last_seen_at.desc().nulls_last())
        .limit(1)
    )
    if not external_user_id:
        return {}
    value = str(external_user_id).strip()
    if not _SCOPED_USER_ID_RE.fullmatch(value):
        return {}
    return {key: value}


def _guess_media_type_from_key(media_s3_key: Optional[str]) -> Optional[str]:
    if not media_s3_key:
        return None
    lowered = media_s3_key.lower()
    if lowered.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
        return "image"
    if lowered.endswith((".ogg", ".mp3", ".wav", ".m4a")):
        return "audio"
    if lowered.endswith((".mp4", ".mov", ".avi")):
        return "video"
    if lowered.endswith((".pdf", ".doc", ".docx")):
        return "document"
    return "document"


def _media_type_from_mime(mime_type: str | None) -> str:
    normalized = (mime_type or "").split(";", 1)[0].lower()
    if normalized.startswith("image/"):
        return "image"
    if normalized.startswith("audio/"):
        return "audio"
    if normalized.startswith("video/"):
        return "video"
    return "document"


async def _ensure_whatsapp_media_document(
    *,
    db: AsyncSession,
    workspace_id: UUID,
    matter_id: UUID,
    media_s3_key: str,
    media_type: Optional[str],
) -> str:
    from sqlalchemy import select

    from app.models.document import Document, DocumentStatus, DocumentType
    from app.models.matter import Matter
    from app.models.storage_object import StorageObject
    from app.models.timeline_event import TimelineEvent, TimelineEventType
    from app.services.local_object_storage_service import (
        LocalObjectConflictError,
        LocalObjectNotFoundError,
        LocalObjectStorageService,
    )

    existing = await db.execute(
        select(Document).where(Document.matter_id == matter_id, Document.s3_key == media_s3_key)
    )
    existing_document = existing.scalar_one_or_none()
    storage_object = await db.scalar(
        select(StorageObject)
        .where(
            StorageObject.workspace_id == workspace_id,
            StorageObject.storage_key == media_s3_key,
            StorageObject.deleted_at.is_(None),
        )
        .order_by(StorageObject.version.desc())
        .limit(1)
    )
    if storage_object is None:
        raise LocalObjectNotFoundError(
            "Mídia inbound sem StorageObject versionado foi bloqueada."
        )
    resolved_media_type = _media_type_from_mime(
        storage_object.detected_content_type or storage_object.declared_content_type
    )
    matter = await db.scalar(
        select(Matter).where(
            Matter.id == matter_id,
            Matter.workspace_id == workspace_id,
            Matter.deleted_at.is_(None),
        )
    )
    if matter is None:
        raise LocalObjectNotFoundError("Caso da mídia inbound não foi encontrado.")
    storage_object = await LocalObjectStorageService(db).bind_acl(
        workspace_id=workspace_id,
        object_id=storage_object.object_id,
        matter_id=matter.id,
        client_id=matter.client_id,
    )
    if existing_document is not None:
        if existing_document.id != storage_object.object_id:
            raise LocalObjectConflictError(
                "Documento inbound diverge do StorageObject versionado."
            )
        return resolved_media_type

    filename = storage_object.original_filename
    doc_type = (
        DocumentType.comprovante
        if resolved_media_type == "document"
        else DocumentType.outro
    )
    document = Document(
        id=storage_object.object_id,
        matter_id=matter_id,
        name=filename,
        doc_type=doc_type,
        status=(
            DocumentStatus.completo
            if storage_object.state.value == "available"
            else DocumentStatus.invalido
        ),
        s3_key=media_s3_key,
        s3_url=None,
        file_size=(
            str(storage_object.byte_size) if storage_object.byte_size is not None else None
        ),
        mime_type=storage_object.detected_content_type or storage_object.declared_content_type,
    )
    db.add(document)
    await db.flush()
    await KnowledgeService(db).index_document(
        workspace_id=workspace_id,
        document=document,
        title=f"Mídia recebida via WhatsApp: {filename}",
        source_type="whatsapp_media",
        rank_hint=4,
    )
    db.add(
        TimelineEvent(
            matter_id=matter_id,
            event_type=TimelineEventType.document_added,
            title="Mídia recebida via WhatsApp",
            description=filename,
            event_data={"document_id": str(document.id), "source": "whatsapp"},
        )
    )
    return resolved_media_type


async def upload_media_to_s3(
    media_id: str,
    media_type: str,
    contact_id: UUID,
    matter_id: Optional[UUID],
    *,
    db: AsyncSession | None = None,
    workspace_id: UUID | None = None,
    actor_user_id: UUID | None = None,
) -> Optional[str]:
    """
    Baixa mídia do servidor da Meta e faz upload para S3.

    Returns:
        S3 key do arquivo, ou None em caso de erro.
    """
    import httpx
    from sqlalchemy import select

    from app.core.klm_runtime import aws_external_access_allowed, external_delivery_allowed
    from app.models.matter import Matter
    from app.models.storage_object import StorageAVStatus, StorageObject, StorageObjectState
    from app.models.whatsapp import WhatsAppContact
    from app.services.entitlement_service import EntitlementError
    from app.services.local_object_storage_service import (
        LocalObjectConflictError,
        LocalObjectNotFoundError,
        LocalObjectStorageService,
        LocalObjectUnavailableError,
        LocalObjectValidationError,
    )
    from app.services.private_document_storage_service import (
        PrivateDocumentStorageService,
    )
    from app.services.s3_service import S3Service, StorageUnavailableError
    from app.services.whatsapp.media_security import trusted_meta_media_url

    if not external_delivery_allowed():
        logger.info("sdr_intake.media_upload_skipped", reason="KLM-EXT-001")
        return None
    if not settings.WHATSAPP_ACCESS_TOKEN:
        logger.warning("sdr_intake.media_upload_skipped", reason="WHATSAPP_ACCESS_TOKEN não configurado")
        return None
    if db is None or workspace_id is None:
        raise LocalObjectUnavailableError(
            "Upload de mídia inbound exige sessão e workspace tenant-bound."
        )

    uses_private_storage = aws_external_access_allowed()
    local_storage = LocalObjectStorageService(db)
    private_storage = PrivateDocumentStorageService(db)
    if uses_private_storage:
        private_storage.require_remote_runtime()
        private_storage.verify_private_bucket()
    else:
        local_storage.require_writable_runtime()
    contact = await db.scalar(
        select(WhatsAppContact).where(
            WhatsAppContact.id == contact_id,
            WhatsAppContact.workspace_id == workspace_id,
        )
    )
    if contact is None:
        raise LocalObjectNotFoundError("Contato da mídia inbound não foi encontrado.")
    if matter_id is not None and contact.matter_id not in {None, matter_id}:
        raise LocalObjectConflictError("Caso da mídia diverge do contato tenant-bound.")
    bound_matter_id = matter_id or contact.matter_id
    bound_client_id = None
    if bound_matter_id is not None:
        matter = await db.scalar(
            select(Matter).where(
                Matter.id == bound_matter_id,
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            )
        )
        if matter is None:
            raise LocalObjectNotFoundError("Caso da mídia inbound não foi encontrado.")
        bound_client_id = matter.client_id

    object_id = uuid5(workspace_id, f"whatsapp-media:{media_id}")
    try:
        existing = await db.scalar(
            select(StorageObject)
            .where(
                StorageObject.workspace_id == workspace_id,
                StorageObject.object_id == object_id,
            )
            .order_by(StorageObject.version.desc())
            .limit(1)
        )
    except LocalObjectNotFoundError:
        existing = None
    if existing is not None:
        if existing.deleted_at is not None:
            raise LocalObjectConflictError("Mídia inbound já foi excluída.")
        if (
            existing.state is StorageObjectState.available
            and (
                not uses_private_storage
                or existing.av_status is StorageAVStatus.clean
            )
        ):
            return existing.storage_key
        if existing.state is StorageObjectState.quarantined:
            if existing.av_status is StorageAVStatus.infected:
                return None
            # The durable inbound reconciler will observe this exact object and
            # resume only after the AV worker promotes it to clean storage.
            return existing.storage_key
        if uses_private_storage:
            return existing.storage_key
        existing = await local_storage.reconcile_object(
            workspace_id=workspace_id, object_id=object_id
        )
        if existing.state is StorageObjectState.available:
            return existing.storage_key
        if existing.state is StorageObjectState.quarantined:
            return None
        raise LocalObjectConflictError("Mídia inbound ainda está em processamento.")

    try:
        safe_media_id = trusted_meta_object_id(media_id)
    except ValueError:
        logger.error(
            "sdr_intake.media_id_rejeitado",
            media_ref=opaque_ref(str(media_id), kind="media"),
        )
        return None

    staging_key: str | None = None
    handle = None
    try:
        # 1. Obter URL de download da mídia
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"https://graph.facebook.com/{settings.META_GRAPH_API_VERSION}/{safe_media_id}",
                headers={"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"},
            )
        if resp.status_code != 200:
            logger.error(
                "sdr_intake.media_url_fetch_failed",
                media_ref=opaque_ref(media_id, kind="media"),
                status=resp.status_code,
            )
            return None

        media_url = trusted_meta_media_url(resp.json().get("url"))

        # 2. Baixar o arquivo de mídia com limite durante o streaming.
        max_bytes = (
            settings.DOCUMENT_AV_MAX_BYTES
            if uses_private_storage
            else settings.LOCAL_STORAGE_MAX_BYTES
        )
        if uses_private_storage:
            handle = tempfile.SpooledTemporaryFile(
                max_size=min(max_bytes, 8 * 1024 * 1024),
                mode="w+b",
            )
        else:
            staging_key = local_storage.staging_key(workspace_id)
            handle = await asyncio.to_thread(S3Service.create_local_object, staging_key)
        digest = hashlib.sha256()
        received = 0
        response_content_type = ""
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "GET",
                media_url,
                headers={"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"},
            ) as media_resp:
                if media_resp.status_code != 200:
                    logger.error(
                        "sdr_intake.media_download_failed",
                        media_ref=opaque_ref(media_id, kind="media"),
                    )
                    return None
                declared_length = media_resp.headers.get("content-length")
                if declared_length is not None:
                    try:
                        if int(declared_length) > max_bytes:
                            raise LocalObjectValidationError(
                                "Mídia inbound excede o limite local."
                            )
                    except ValueError as exc:
                        raise LocalObjectValidationError(
                            "Content-Length inválido na mídia inbound."
                        ) from exc
                response_content_type = (
                    media_resp.headers.get("content-type", "").split(";", 1)[0].lower()
                )
                async for chunk in media_resp.aiter_bytes():
                    received += len(chunk)
                    if received > max_bytes:
                        raise LocalObjectValidationError(
                            "Mídia inbound excede o limite local."
                        )
                    digest.update(chunk)
                    await asyncio.to_thread(handle.write, chunk)
        await asyncio.to_thread(handle.flush)
        if not uses_private_storage:
            await asyncio.to_thread(os.fsync, handle.fileno())

        # 3. Finalizar exclusivamente pelo ciclo StorageObject.
        ext_map = {
            "image": "jpg",
            "audio": "ogg",
            "video": "mp4",
            "document": "pdf",
        }
        ext = ext_map.get(media_type, "bin")
        fallback_mime = {
            "image": "image/jpeg",
            "audio": "audio/ogg",
            "video": "video/mp4",
            "document": "application/pdf",
        }.get(media_type, "application/pdf")
        content_type = (
            response_content_type
            if response_content_type and response_content_type != "application/octet-stream"
            else fallback_mime
        )
        common = {
            "workspace_id": workspace_id,
            "actor_user_id": actor_user_id,
            "filename": f"whatsapp-{object_id.hex}.{ext}",
            "content_type": content_type,
            "byte_size": received,
            "checksum_sha256": digest.hexdigest(),
            "retention_until": None,
            "expires_in": 300,
            "object_id": object_id,
            "matter_id": bound_matter_id,
            "client_id": bound_client_id,
            "initial_object_id": True,
        }
        if uses_private_storage:
            authorization = await private_storage.create_upload_authorization(
                **common,
                idempotency_key=f"whatsapp-inbound:{media_id}",
            )
            if authorization.upload_required:
                if authorization.storage_object.quarantine_key is None:
                    raise LocalObjectConflictError("Chave de quarentena ausente.")
                handle.seek(0)
                await asyncio.to_thread(
                    private_storage.s3.upload_authorized_private_file,
                    handle,
                    authorization.storage_object.quarantine_key,
                    content_type=content_type,
                    state="quarantine",
                    authorization_granted=True,
                    checksum_sha256=digest.hexdigest(),
                )
                stored = await private_storage.confirm_upload(
                    workspace_id=workspace_id,
                    object_id=object_id,
                    actor_user_id=actor_user_id,
                )
            else:
                stored = authorization.storage_object
        else:
            authorization = await local_storage.create_upload_authorization(**common)
            handle.close()
            handle = None
            stored = await local_storage.finalize_staged_upload(
                ticket=authorization.ticket.token,
                staging_key=staging_key,
                request_content_type=content_type,
            )
            staging_key = None
        if stored.state not in {
            StorageObjectState.available,
            StorageObjectState.quarantined,
        }:
            raise LocalObjectConflictError("Mídia inbound ainda está em processamento.")

        logger.info(
            "sdr_intake.media_stored",
            object_ref=opaque_ref(stored.storage_key, kind="storage_object"),
            media_type=media_type,
            state=stored.state.value,
            av_status=stored.av_status.value,
        )
        return stored.storage_key

    except EntitlementError:
        raise
    except Exception as exc:
        logger.error(
            "sdr_intake.media_upload_error",
            media_ref=opaque_ref(media_id, kind="media"),
            error_type=safe_error_type(exc),
        )
        return None
    finally:
        if handle is not None:
            handle.close()
        if staging_key is not None:
            try:
                await asyncio.to_thread(
                    S3Service.delete_local_object,
                    staging_key,
                    missing_ok=True,
                )
            except (OSError, ValueError, StorageUnavailableError):
                pass
