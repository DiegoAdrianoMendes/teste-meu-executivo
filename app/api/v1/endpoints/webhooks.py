"""
Endpoint: Webhooks
Recebe eventos de sistemas externos (OpenClaw, ZapSign, etc.)
e os injeta na timeline dos casos correspondentes.
"""
import hashlib
import hmac
import json
import time
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_db
from app.core.klm_runtime import EXTERNAL_ACTION_BLOCKED_CODE, external_delivery_allowed
from app.core.logging import logger
from app.core.logging_privacy import opaque_ref, safe_error_type
from app.core.request_body import read_bounded_body
from app.db.tenant import set_workspace_context
from app.db.webhook_tenant import resolve_zapsign_webhook_workspace
from app.models.klm_operations import ExternalActionIntent, ExternalActionIntentStatus
from app.models.matter import KanbanColumnId, Matter, MicroStatus
from app.models.timeline_event import TimelineEvent, TimelineEventType
from app.models.workspace import Workspace
from app.schemas.zapsign import ZapSignWebhookPayload
from app.services.zapsign_service import get_zapsign_webhook_secret

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

OPENCLAW_WEBHOOK_MAX_AGE_SECONDS = 5 * 60


def _require_external_webhook_delivery() -> None:
    if not external_delivery_allowed():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": EXTERNAL_ACTION_BLOCKED_CODE,
                "message": (
                    "Webhook externo bloqueado enquanto entregas externas estiverem "
                    "desabilitadas."
                ),
            },
        )


def _openclaw_canonical_message(
    payload: bytes,
    *,
    timestamp: str,
    delivery_id: str,
    workspace_slug: str,
) -> bytes:
    """Build the versioned, unambiguous byte string signed by OpenClaw."""
    return b"\n".join(
        (
            b"klm-openclaw-v1",
            timestamp.encode("ascii"),
            delivery_id.encode("utf-8"),
            workspace_slug.encode("utf-8"),
            payload,
        )
    )


def _verify_openclaw_signature(
    payload: bytes,
    signature: str,
    *,
    timestamp: str,
    delivery_id: str,
    workspace_slug: str,
) -> bool:
    """Valida a assinatura HMAC do webhook do OpenClaw."""
    expected = hmac.new(
        settings.OPENCLAW_WEBHOOK_SECRET.strip().encode("utf-8"),
        _openclaw_canonical_message(
            payload,
            timestamp=timestamp,
            delivery_id=delivery_id,
            workspace_slug=workspace_slug,
        ),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature.removeprefix("sha256="))


def _validate_openclaw_request(
    payload: bytes,
    *,
    delivery_id: str,
    timestamp: str,
    signature: str,
    workspace_slug: str,
) -> tuple[str, int]:
    webhook_secret = settings.OPENCLAW_WEBHOOK_SECRET.strip()
    if not webhook_secret or webhook_secret == "change-me":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook não configurado.",
        )

    normalized_delivery_id = delivery_id.strip()
    normalized_timestamp = timestamp.strip()
    normalized_signature = signature.strip()
    normalized_workspace_slug = workspace_slug.strip()
    if (
        not normalized_delivery_id
        or len(normalized_delivery_id) > 255
        or not normalized_timestamp
        or not normalized_signature
        or not normalized_workspace_slug
        or len(normalized_workspace_slug) > 255
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Headers assinados do OpenClaw ausentes ou inválidos.",
        )

    try:
        normalized_timestamp.encode("ascii")
        timestamp_value = int(normalized_timestamp)
    except (UnicodeEncodeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Timestamp do OpenClaw inválido.",
        ) from exc

    if abs(int(time.time()) - timestamp_value) > OPENCLAW_WEBHOOK_MAX_AGE_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Webhook do OpenClaw expirado.",
        )
    if not _verify_openclaw_signature(
        payload,
        normalized_signature,
        timestamp=normalized_timestamp,
        delivery_id=normalized_delivery_id,
        workspace_slug=normalized_workspace_slug,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Assinatura inválida.",
        )
    return normalized_delivery_id, timestamp_value


def _openclaw_event_id(workspace_id: UUID, delivery_id: str) -> UUID:
    """Converte a chave de idempotência em uma PK estável e atomicamente única."""
    return uuid5(NAMESPACE_URL, f"klm:openclaw:{workspace_id}:{delivery_id}")


def _openclaw_deduplicated_response(
    event: TimelineEvent,
    *,
    delivery_id: str,
    payload_hash: str,
) -> dict[str, Any]:
    event_data = event.event_data if isinstance(event.event_data, dict) else {}
    stored_hash = event_data.get("payload_sha256")
    is_same_delivery = (
        event_data.get("source") == "openclaw"
        and event_data.get("delivery_id") == delivery_id
        and isinstance(stored_hash, str)
        and hmac.compare_digest(stored_hash, payload_hash)
    )
    if not is_same_delivery:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Delivery ID do OpenClaw já utilizado com outro payload.",
        )
    return {
        "status": "ok",
        "matter_id": str(event.matter_id),
        "event_id": str(event.id),
        "deduplicated": True,
    }


@router.post("/openclaw", status_code=status.HTTP_200_OK, summary="Webhook do OpenClaw")
async def openclaw_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_openclaw_signature: str = Header(default=""),
    x_openclaw_delivery: str = Header(default=""),
    x_openclaw_timestamp: str = Header(default=""),
    x_workspace_slug: str = Header(default=""),
):
    """
    Recebe atualizações de processos judiciais do OpenClaw.
    Valida a assinatura HMAC e injeta o evento na timeline do caso correspondente.
    """
    _require_external_webhook_delivery()
    body = await read_bounded_body(request)
    header_workspace_slug = x_workspace_slug.strip()
    delivery_id, delivery_timestamp = _validate_openclaw_request(
        body,
        delivery_id=x_openclaw_delivery,
        timestamp=x_openclaw_timestamp,
        signature=x_openclaw_signature,
        workspace_slug=header_workspace_slug,
    )
    try:
        payload: dict[str, Any] = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="JSON do OpenClaw inválido.",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload do OpenClaw deve ser um objeto.",
        )

    case_number = payload.get("case_number") or payload.get("numero_processo")
    body_workspace_slug = payload.get("workspace_slug") or payload.get("workspaceSlug")
    if not isinstance(body_workspace_slug, str) or not body_workspace_slug.strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant do OpenClaw ausente no corpo.",
        )
    workspace_slug = body_workspace_slug.strip()
    if not header_workspace_slug or not hmac.compare_digest(
        header_workspace_slug,
        workspace_slug,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant do OpenClaw diverge entre header e corpo.",
        )

    body_delivery_id = payload.get("delivery_id") or payload.get("deliveryId")
    if body_delivery_id is not None and (
        not isinstance(body_delivery_id, str)
        or not hmac.compare_digest(body_delivery_id.strip(), x_openclaw_delivery.strip())
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Delivery ID do OpenClaw diverge entre header e corpo.",
        )

    if not case_number or not workspace_slug:
        logger.warning(
            "openclaw_webhook_missing_case_number",
            has_case_number=bool(case_number),
            has_workspace=bool(workspace_slug),
        )
        return {"status": "ignored", "reason": "case_number or workspace_slug not found in payload"}

    workspace_result = await db.execute(
        select(Workspace).where(Workspace.slug == workspace_slug, Workspace.deleted_at.is_(None))
    )
    workspace = workspace_result.scalar_one_or_none()
    if not workspace:
        logger.warning(
            "openclaw_webhook_workspace_not_found",
            workspace_ref=opaque_ref(workspace_slug, kind="workspace_slug"),
        )
        return {"status": "ignored", "reason": f"Workspace '{workspace_slug}' not found"}
    await set_workspace_context(db, workspace.id)

    payload_hash = hashlib.sha256(body).hexdigest()
    event_id = _openclaw_event_id(workspace.id, delivery_id)
    existing_event = await db.scalar(
        select(TimelineEvent).where(TimelineEvent.id == event_id)
    )
    if existing_event is not None:
        return _openclaw_deduplicated_response(
            existing_event,
            delivery_id=delivery_id,
            payload_hash=payload_hash,
        )

    # Buscar o caso pelo número do processo
    result = await db.execute(
        select(Matter).where(
            Matter.case_number == case_number,
            Matter.workspace_id == workspace.id,
            Matter.deleted_at.is_(None),
        )
    )
    matter = result.scalar_one_or_none()

    if not matter:
        logger.warning(
            "openclaw_webhook_matter_not_found",
            case_ref=opaque_ref(case_number, kind="case_number"),
            workspace_id=str(workspace.id),
        )
        return {"status": "ignored", "reason": f"Matter with case_number '{case_number}' not found"}

    # Injetar evento na timeline
    event_description = payload.get("description") or payload.get("descricao") or str(payload)
    event = TimelineEvent(
        id=event_id,
        matter_id=matter.id,
        event_type=TimelineEventType.webhook_received,
        title=payload.get("title") or payload.get("titulo") or "Atualização recebida do OpenClaw",
        description=event_description[:2000],
        event_data={
            "source": "openclaw",
            "delivery_id": delivery_id,
            "delivery_timestamp": delivery_timestamp,
            "payload_sha256": payload_hash,
            "raw": payload,
        },
    )
    try:
        async with db.begin_nested():
            db.add(event)
            await db.flush()
    except IntegrityError:
        concurrent_event = await db.scalar(
            select(TimelineEvent)
            .where(TimelineEvent.id == event_id)
            .execution_options(populate_existing=True)
        )
        if concurrent_event is None:
            raise
        return _openclaw_deduplicated_response(
            concurrent_event,
            delivery_id=delivery_id,
            payload_hash=payload_hash,
        )

    logger.info(
        "openclaw_webhook_processed",
        matter_id=str(matter.id),
        case_ref=opaque_ref(case_number, kind="case_number"),
        delivery_ref=opaque_ref(delivery_id, kind="openclaw_delivery"),
    )
    return {
        "status": "ok",
        "matter_id": str(matter.id),
        "event_id": str(event.id),
        "deduplicated": False,
    }


@router.post("/zapsign", status_code=status.HTTP_200_OK, summary="Webhook do ZapSign")
async def zapsign_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: str = Header(default=""),
):
    """
    Recebe eventos de assinatura eletrônica do ZapSign.
    Atualiza o caso correspondente (via external_id = matter_id) e
    injeta evento na timeline. Retorna 200 sempre para evitar reenvios.
    """
    _require_external_webhook_delivery()
    raw_body = await read_bounded_body(request)
    try:
        body = json.loads(raw_body)
        if not isinstance(body, dict):
            raise TypeError("ZapSign payload must be an object")
        payload = ZapSignWebhookPayload.model_validate(body)
    except Exception as exc:
        logger.warning("zapsign_webhook_parse_error", error_type=safe_error_type(exc))
        # Retorna 200 mesmo em erro de parse para evitar reenvios infinitos do ZapSign
        return {"status": "ignored", "reason": "invalid_payload"}

    logger.info(
        "zapsign_webhook_received",
        event_type=payload.event_type,
        document_ref=opaque_ref(payload.token, kind="zapsign_doc"),
        external_ref=opaque_ref(payload.external_id, kind="external_id"),
    )

    # Processar apenas eventos de documento assinado com status confirmado
    if payload.event_type == "doc_signed" and payload.status == "signed":
        if not payload.external_id:
            logger.warning(
                "zapsign_webhook_missing_external_id",
                document_ref=opaque_ref(payload.token, kind="zapsign_doc"),
            )
            return {"status": "ignored", "reason": "external_id not found in payload"}

        try:
            matter_id = UUID(payload.external_id)
        except ValueError:
            return {"status": "ignored", "reason": "invalid external_id"}

        workspace_id = await resolve_zapsign_webhook_workspace(
            db,
            matter_id=matter_id,
            document_token=payload.token,
        )
        if workspace_id is None:
            logger.warning(
                "zapsign_webhook_tenant_binding_not_found",
                matter_id=str(matter_id),
                document_ref=opaque_ref(payload.token, kind="zapsign_doc"),
            )
            return {"status": "ignored", "reason": "doc_token not linked to matter"}
        await set_workspace_context(db, workspace_id)
        webhook_secret = await get_zapsign_webhook_secret(
            workspace_id=workspace_id,
            db=db,
        )
        if not webhook_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Webhook ZapSign não configurado para este workspace.",
            )
        if not hmac.compare_digest(
            authorization,
            f"Bearer {webhook_secret}",
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Credencial de webhook inválida.",
            )

        # RLS now enforces the tenant returned by the bound bootstrap lookup.
        result = await db.execute(
            select(Matter).where(
                Matter.id == matter_id,
                Matter.workspace_id == workspace_id,
                Matter.deleted_at.is_(None),
            ).with_for_update()
        )
        matter = result.scalar_one_or_none()

        if not matter:
            logger.warning("zapsign_webhook_matter_not_found", matter_id=str(matter_id))
            return {"status": "ignored", "reason": f"Matter '{payload.external_id}' not found"}

        if not matter.zapsign_doc_token or not hmac.compare_digest(
            matter.zapsign_doc_token,
            payload.token,
        ):
            logger.warning(
                "zapsign_webhook_token_mismatch",
                matter_id=str(matter.id),
                document_ref=opaque_ref(payload.token, kind="zapsign_doc"),
            )
            return {"status": "ignored", "reason": "doc_token not linked to matter"}

        if matter.zapsign_status == "signed":
            return {"status": "ok", "matter_id": str(matter.id), "deduplicated": True}

        # Atualizar campos ZapSign e mover o card no Kanban
        matter.zapsign_status = "signed"
        matter.column_id = KanbanColumnId.assinado
        matter.micro_status = MicroStatus.contrato_assinado
        if payload.signed_file:
            # Preservar URL do arquivo assinado no doc_token field (metadado)
            matter.zapsign_doc_token = payload.token

        # Injetar evento na timeline
        event = TimelineEvent(
            matter_id=matter.id,
            event_type=TimelineEventType.status_change,
            title="Contrato assinado via ZapSign",
            description=(
                f"Documento '{payload.name or payload.token}' assinado eletronicamente. "
                f"Arquivo assinado disponível em: {payload.signed_file or 'não disponível'}."
            ),
            event_data={
                "source": "zapsign",
                "event_type": payload.event_type,
                "doc_token": payload.token,
                "signed_file": payload.signed_file,
                "signer_who_signed": payload.signer_who_signed,
            },
        )
        db.add(event)

        # Marco de funil: contrato assinado vira AcquisitionEvent na mesma
        # transação que move o Kanban (idempotente por dedupe_key).
        from app.models.honorarium_estimate import HonorariumEstimate
        from app.schemas.klm_operations import AcquisitionEventCreate
        from app.services.klm_operations_service import KlmOperationsService

        # HonorariumEstimate é único por matter (uq_honorarium_estimates_matter)
        # e o matter já foi resolvido dentro do workspace do webhook.
        estimate = await db.scalar(
            select(HonorariumEstimate).where(HonorariumEstimate.matter_id == matter.id)
        )
        estimated_value = None
        if estimate is not None:
            estimated_value = (
                estimate.total_estimated_value
                if estimate.total_estimated_value is not None
                else estimate.fixed_fee
            )
        try:
            await KlmOperationsService(db).record_acquisition_event(
                workspace_id,
                None,
                AcquisitionEventCreate(
                    matter_id=matter.id,
                    client_id=matter.client_id,
                    event_name="contract_created",
                    channel="zapsign",
                    source="zapsign",
                    event_id=payload.token,
                    dedupe_key=f"zapsign:signed:{matter.id}:{payload.token}",
                    value=estimated_value if estimated_value and estimated_value > 0 else None,
                    event_metadata={"docToken": payload.token},
                ),
            )
        except ValueError:
            # O marco de funil jamais pode derrubar o fluxo de negócio
            # (Kanban + notificação); divergência de replay fica registrada.
            logger.warning(
                "zapsign.acquisition_event_replay_mismatch",
                matter_id=str(matter.id),
                document_ref=opaque_ref(payload.token, kind="zapsign_doc"),
            )

        db.add(
            ExternalActionIntent(
                workspace_id=workspace_id,
                matter_id=matter.id,
                requested_by_user_id=None,
                action_type="contract_signed_client_notification",
                provider="meta_whatsapp",
                idempotency_key=f"zapsign:signed:client-notification:{matter.id}",
                status=ExternalActionIntentStatus.awaiting_approval,
                payload={
                    "channel": "whatsapp",
                    "templateKey": "contract_signed_next_steps",
                    "body": (
                        "Documento assinado com sucesso. Nossa equipe revisará os próximos "
                        "passos e manterá você atualizado por este WhatsApp."
                    ),
                    "sourceEvent": "zapsign_doc_signed",
                    "externalActionsExecuted": 0,
                },
                reason=(
                    "Evento confirmado pelo ZapSign; notificação ao cliente aguarda "
                    "aprovação humana e não foi enviada."
                ),
            )
        )
        await db.flush()

        # O alerta interno só é disparado depois do commit. A comunicação ao cliente
        # permanece na fila de intenções, sujeita a aprovação e ao gate de entrega.
        from app.db.post_commit import queue_operational_alert

        queue_operational_alert(
            db,
            workspace_id=workspace_id,
            alert_type="contract_signed",
            entity_id=matter.id,
        )

        logger.info(
            "zapsign_webhook_processed",
            matter_id=str(matter.id),
            document_ref=opaque_ref(payload.token, kind="zapsign_doc"),
            event_id=str(event.id),
        )
        return {
            "status": "ok",
            "matter_id": str(matter.id),
            "event_id": str(event.id),
            "deduplicated": False,
        }

    # Evento ignorado (não é doc_signed / signed) — retorna 200 mesmo assim
    logger.info(
        "zapsign_webhook_ignored",
        event_type=payload.event_type,
        status=payload.status,
        document_ref=opaque_ref(payload.token, kind="zapsign_doc"),
    )
    return {
        "status": "ignored",
        "reason": f"event_type={payload.event_type} status={payload.status}",
    }
