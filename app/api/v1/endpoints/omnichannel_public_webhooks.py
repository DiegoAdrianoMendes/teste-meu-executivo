"""Unauthenticated but signed webhook boundary for native lead sources."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.klm_runtime import EXTERNAL_ACTION_BLOCKED_CODE, external_delivery_allowed
from app.core.request_body import DEFAULT_PUBLIC_BODY_LIMIT_BYTES, read_bounded_body
from app.schemas.omnichannel_v6 import (
    PublicLeadCaptureIngressItemRead,
    PublicLeadCaptureIngressRead,
    PublicSocialIngressItemRead,
    PublicSocialIngressRead,
)
from app.services.lead_capture_adapter_service import LeadCaptureAdapterService
from app.services.lead_hydration_service import LeadHydrationService
from app.services.omnichannel_orchestration_service import OmnichannelOrchestrationService
from app.services.omnichannel_v6_service import OmnichannelV6Service
from app.services.omnichannel_webhook_service import (
    LEAD_INGRESS_PROVIDERS,
    META_INGRESS_PROVIDERS,
    SOCIAL_INGRESS_PROVIDERS,
    OmnichannelWebhookService,
    PublicIngressConflictError,
    WebhookSignatureError,
)
from app.services.social_event_adapter_service import SocialEventAdapterService

router = APIRouter(prefix="/webhooks/omnichannel", tags=["KLM Public Omnichannel"])

MAX_WEBHOOK_BODY_BYTES = DEFAULT_PUBLIC_BODY_LIMIT_BYTES


def _require_external_ingress() -> None:
    if not external_delivery_allowed():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": EXTERNAL_ACTION_BLOCKED_CODE,
                "message": (
                    "Webhook externo bloqueado enquanto as ações externas estiverem "
                    "desabilitadas."
                ),
            },
        )


async def _bounded_body(request: Request) -> bytes:
    return await read_bounded_body(
        request,
        max_bytes=MAX_WEBHOOK_BODY_BYTES,
        too_large_detail="Payload do webhook excede 2 MB.",
    )


def _not_found(error: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))


@router.get(
    "/{workspace_slug}/{provider}/{connection_key}",
    response_class=PlainTextResponse,
    summary="Validar challenge oficial do Meta webhook",
)
async def verify_meta_webhook(
    workspace_slug: str,
    provider: str,
    connection_key: str,
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
    db: AsyncSession = Depends(get_db),
) -> PlainTextResponse:
    _require_external_ingress()
    if provider != "meta_ads" or not hub_challenge:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rota inválida.")
    service = OmnichannelWebhookService(db)
    try:
        _workspace, _connection, credential = await service.resolve_public_ingress(
            workspace_slug=workspace_slug,
            provider=provider,
            connection_key=connection_key,
        )
        service.verify_challenge(
            credential,
            mode=hub_mode,
            verify_token=hub_verify_token,
        )
    except LookupError as error:
        raise _not_found(error) from error
    except WebhookSignatureError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)
        ) from error
    return PlainTextResponse(content=hub_challenge, status_code=status.HTTP_200_OK)


@router.post(
    "/{workspace_slug}/{provider}/{connection_key}",
    response_model=PublicLeadCaptureIngressRead,
    status_code=status.HTTP_200_OK,
    summary="Receber lead assinado de Meta, TikTok ou site",
)
async def receive_signed_lead(
    workspace_slug: str,
    provider: str,
    connection_key: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_hub_signature_256: str = Header(default=""),
    tiktok_signature: str = Header(default="", alias="TikTok-Signature"),
    x_klm_signature: str = Header(default=""),
    x_klm_timestamp: str = Header(default=""),
    x_klm_event_id: str = Header(default=""),
) -> PublicLeadCaptureIngressRead:
    _require_external_ingress()
    if provider not in LEAD_INGRESS_PROVIDERS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rota inválida.")
    body = await _bounded_body(request)
    service = OmnichannelWebhookService(db)
    try:
        workspace, connection, credential = await service.resolve_public_ingress(
            workspace_slug=workspace_slug,
            provider=provider,
            connection_key=connection_key,
        )
        signature_timestamp, signature_scheme, signature_material = service.verify_signature(
            credential,
            provider=provider,
            body=body,
            meta_signature=x_hub_signature_256,
            tiktok_signature=tiktok_signature,
            klm_signature=x_klm_signature,
            klm_timestamp=x_klm_timestamp,
        )
    except LookupError as error:
        raise _not_found(error) from error
    except WebhookSignatureError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)
        ) from error

    try:
        raw_payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload JSON inválido.",
        ) from error
    if not isinstance(raw_payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload do webhook deve ser um objeto JSON.",
        )

    try:
        inspections = LeadCaptureAdapterService.inspect_many(connection, raw_payload)
    except (TypeError, ValueError, ValidationError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Lead não pôde ser normalizado: {error}",
        ) from error

    supplied_event_id = x_klm_event_id.strip()
    if provider in {"site", "lovable"} and not supplied_event_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-KLM-Event-ID é obrigatório para formulários do site.",
        )
    if supplied_event_id:
        external_event_id = supplied_event_id
    elif len(inspections) == 1:
        external_event_id = f"lead:{inspections[0].external_lead_id}"
    else:
        external_event_id = f"batch:{hashlib.sha256(body).hexdigest()}"
    if len(external_event_id) > 255:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Identificador externo excede 255 caracteres.",
        )

    request_metadata = {
        "contentType": request.headers.get("content-type", "")[:120],
        "contentLength": len(body),
        "topLevelKeys": sorted(str(key)[:80] for key in raw_payload)[:100],
        "userAgentSha256": hashlib.sha256(
            request.headers.get("user-agent", "").encode("utf-8")
        ).hexdigest(),
        "leadCount": len(inspections),
    }
    try:
        ingress_receipt, ingress_deduplicated = await service.claim_ingress(
            workspace_id=workspace.id,
            connection=connection,
            external_event_id=external_event_id,
            external_lead_id=(
                inspections[0].external_lead_id if len(inspections) == 1 else None
            ),
            body=body,
            signature_material=signature_material,
            signature_timestamp=signature_timestamp,
            signature_scheme=signature_scheme,
            request_metadata=request_metadata,
        )
        items: list[PublicLeadCaptureIngressItemRead] = []
        hydration_service = LeadHydrationService(db)
        for inspection in inspections:
            if inspection.normalized is not None:
                result = await OmnichannelV6Service(db).ingest_lead_capture(
                    workspace.id, None, inspection.normalized
                )
                items.append(
                    PublicLeadCaptureIngressItemRead(
                        external_lead_id=inspection.external_lead_id,
                        state="processed",
                        lead_capture_receipt_id=result["receipt"].id,
                        deduplicated=bool(result["deduplicated"]),
                    )
                )
                continue
            hydration_job, hydration_deduplicated = await hydration_service.enqueue(
                workspace_id=workspace.id,
                connection=connection,
                ingress_receipt=ingress_receipt,
                external_event_id=external_event_id,
                inspection=inspection,
            )
            items.append(
                PublicLeadCaptureIngressItemRead(
                    external_lead_id=inspection.external_lead_id,
                    state="awaiting_hydration",
                    hydration_job_id=hydration_job.id,
                    deduplicated=hydration_deduplicated,
                )
            )
        processed_count = sum(item.state == "processed" for item in items)
        hydration_count = len(items) - processed_count
        response_state = (
            "processed"
            if hydration_count == 0
            else "awaiting_hydration"
            if processed_count == 0
            else "partially_processed"
        )
        response_deduplicated = bool(
            ingress_deduplicated or all(item.deduplicated for item in items)
        )
        await service.operations.record_audit(
            workspace_id=workspace.id,
            actor_user_id=None,
            actor_type="system",
            actor_label="Webhook omnichannel assinado",
            event_type="omnichannel_public_lead_accepted",
            entity_type="omnichannel_public_ingress_receipt",
            entity_id=ingress_receipt.id,
            summary=f"{len(items)} lead(s) assinado(s) de {provider} aceito(s).",
            correlation_id=external_event_id,
            after_data={
                "connectionId": str(connection.id),
                "processedCount": processed_count,
                "hydrationCount": hydration_count,
                "deduplicated": response_deduplicated,
            },
        )
    except PublicIngressConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error
    except (LookupError, RuntimeError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error

    return PublicLeadCaptureIngressRead(
        provider=provider,
        external_event_id=external_event_id,
        ingress_receipt_id=ingress_receipt.id,
        state=response_state,
        lead_capture_receipt_id=items[0].lead_capture_receipt_id,
        hydration_job_id=items[0].hydration_job_id,
        item_count=len(items),
        items=items,
        deduplicated=response_deduplicated,
        processed_at=datetime.now(UTC),
    )


@router.get(
    "/social/{workspace_slug}/{provider}/{connection_key}",
    response_class=PlainTextResponse,
    summary="Validar challenge oficial do webhook social Meta",
)
async def verify_meta_social_webhook(
    workspace_slug: str,
    provider: str,
    connection_key: str,
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
    db: AsyncSession = Depends(get_db),
) -> PlainTextResponse:
    _require_external_ingress()
    if provider not in META_INGRESS_PROVIDERS or provider == "meta_ads" or not hub_challenge:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rota inválida.")
    service = OmnichannelWebhookService(db)
    try:
        _workspace, _connection, credential = await service.resolve_public_ingress(
            workspace_slug=workspace_slug,
            provider=provider,
            connection_key=connection_key,
        )
        service.verify_challenge(
            credential,
            mode=hub_mode,
            verify_token=hub_verify_token,
        )
    except LookupError as error:
        raise _not_found(error) from error
    except WebhookSignatureError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)
        ) from error
    return PlainTextResponse(content=hub_challenge, status_code=status.HTTP_200_OK)


@router.post(
    "/social/{workspace_slug}/{provider}/{connection_key}",
    response_model=PublicSocialIngressRead,
    status_code=status.HTTP_200_OK,
    summary="Receber mensagens, comentários e menções sociais assinadas",
)
async def receive_signed_social_events(
    workspace_slug: str,
    provider: str,
    connection_key: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_hub_signature_256: str = Header(default=""),
    tiktok_signature: str = Header(default="", alias="TikTok-Signature"),
) -> PublicSocialIngressRead:
    _require_external_ingress()
    if provider not in SOCIAL_INGRESS_PROVIDERS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rota inválida.")
    body = await _bounded_body(request)
    webhook_service = OmnichannelWebhookService(db)
    try:
        workspace, connection, credential = await webhook_service.resolve_public_ingress(
            workspace_slug=workspace_slug,
            provider=provider,
            connection_key=connection_key,
        )
        signature_timestamp, signature_scheme, signature_material = (
            webhook_service.verify_signature(
                credential,
                provider=provider,
                body=body,
                meta_signature=x_hub_signature_256,
                tiktok_signature=tiktok_signature,
            )
        )
    except LookupError as error:
        raise _not_found(error) from error
    except WebhookSignatureError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)
        ) from error

    try:
        raw_payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload JSON inválido.",
        ) from error
    if not isinstance(raw_payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload do webhook deve ser um objeto JSON.",
        )
    try:
        batch = SocialEventAdapterService.normalize(connection, raw_payload)
    except (TypeError, ValueError, ValidationError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Evento social não pôde ser normalizado: {error}",
        ) from error

    common_metadata = {
        "contentType": request.headers.get("content-type", "")[:120],
        "contentLength": len(body),
        "requestBodySha256": hashlib.sha256(body).hexdigest(),
        "topLevelKeys": sorted(str(key)[:80] for key in raw_payload)[:100],
        "userAgentSha256": hashlib.sha256(
            request.headers.get("user-agent", "").encode("utf-8")
        ).hexdigest(),
    }
    items: list[PublicSocialIngressItemRead] = []
    deduplicated_count = 0
    event_service = OmnichannelV6Service(db)
    automation_service = OmnichannelOrchestrationService(db)
    try:
        for normalized in batch.events:
            payload = normalized.payload
            ingress_receipt, ingress_deduplicated = await webhook_service.claim_ingress(
                workspace_id=workspace.id,
                connection=connection,
                external_event_id=payload.external_event_id,
                external_lead_id=None,
                body=normalized.canonical_source_bytes,
                signature_material=signature_material,
                signature_timestamp=signature_timestamp,
                signature_scheme=signature_scheme,
                request_metadata={
                    **common_metadata,
                    "eventType": payload.event_type,
                    "channel": payload.channel.value,
                },
            )
            resolution, channel_event, message, event_deduplicated = (
                await event_service.ingest_event(workspace.id, payload)
            )
            automation_runs = await automation_service.trigger_active_automations_for_event(
                workspace.id,
                channel_event.id,
            )
            deduplicated = bool(ingress_deduplicated or event_deduplicated)
            deduplicated_count += int(deduplicated)
            items.append(
                PublicSocialIngressItemRead(
                    external_event_id=payload.external_event_id,
                    ingress_receipt_id=ingress_receipt.id,
                    channel_event_id=channel_event.id,
                    thread_id=resolution.thread.id,
                    identity_id=resolution.identity.id,
                    message_id=message.id if message else None,
                    deduplicated=deduplicated,
                    automation_run_ids=[run.id for run in automation_runs],
                )
            )
        await webhook_service.operations.record_audit(
            workspace_id=workspace.id,
            actor_user_id=None,
            actor_type="system",
            actor_label="Webhook social assinado",
            event_type="omnichannel_public_social_batch_ingested",
            entity_type="omnichannel_channel_connection",
            entity_id=connection.id,
            summary=f"Lote social de {provider} normalizado no inbox canônico.",
            after_data={
                "acceptedCount": len(items),
                "ignoredCount": batch.ignored_count,
                "deduplicatedCount": deduplicated_count,
                "externalRequests": 0,
            },
        )
    except PublicIngressConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error
    except (LookupError, RuntimeError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error

    return PublicSocialIngressRead(
        provider=provider,
        inspected_count=batch.inspected_count,
        accepted_count=len(items),
        ignored_count=batch.ignored_count,
        deduplicated_count=deduplicated_count,
        items=items,
        processed_at=datetime.now(UTC),
    )
