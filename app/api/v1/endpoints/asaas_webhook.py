"""
Endpoint: Asaas Webhook Receiver
Recebe eventos de webhook do Asaas (cobranças) e agenda o processamento
assíncrono. Ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md seções 3.6, 4.3
e 4.4.

Rota: POST /api/v1/webhooks/asaas

Contrato (F1 — Tarefa C):
  - Header `asaas-access-token`: token que NÓS definimos por workspace,
    guardado (cifrado) em `IntegrationConnection.settings.webhookSecret` da
    integração `asaas`, com o digest em claro ao lado (`webhookSecretDigest`).
    O workspace é resolvido pelo digest por um resolver SECURITY DEFINER
    (`klm_resolve_asaas_webhook_workspace`, ver app/db/webhook_tenant.py),
    porque a sessão do request não tem contexto e, sob FORCE RLS fail-closed,
    varrer `integration_connections` devolvia zero linhas (S7: todo evento
    respondia 401). Depois de `set_workspace_context`, o segredo completo é
    conferido em tempo constante contra as integrações Asaas do workspace.
  - Sem token/token inválido/nenhum workspace correspondente (inclusive digest
    presente em mais de um workspace) → 401, NENHUM recibo é gravado.
  - Recibo idempotente por (workspace_id, event_id): evento duplicado é
    no-op (200, sem reagendar processamento).
  - Responde 200 IMEDIATO após gravar o recibo; o processamento de negócio
    (mapear evento → estado do charge, baixa da FinanceEntry, auditoria,
    timeline) roda assíncrono via Celery (app/workers/finance_tasks.py).
  - Tolera payload com campos desconhecidos/extras (a doc do Asaas avisa que
    novos eventos e campos podem aparecer).
"""
from __future__ import annotations

import hmac
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.logging import logger
from app.core.logging_privacy import opaque_ref, safe_error_type
from app.core.request_body import read_bounded_body
from app.db.tenant import set_workspace_context
from app.db.webhook_tenant import resolve_asaas_webhook_workspace
from app.models.asaas_webhook_receipt import AsaasWebhookReceipt, AsaasWebhookReceiptStatus
from app.models.finance import IntegrationConnection, IntegrationProvider, IntegrationStatus
from app.services.integration_secrets import reveal_integration_secret

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


async def _resolve_workspace_id_by_token(db: AsyncSession, token: str) -> Optional[UUID]:
    """Resolve o workspace dono do webhook em dois passos: o resolver
    SECURITY DEFINER localiza o workspace pelo digest do token (sem ler o
    segredo e sem contornar a RLS); com o contexto do workspace aberto, o
    segredo completo é conferido em tempo constante contra todas as
    integrações Asaas ativas do workspace (pode haver mais de uma), sem
    interromper cedo. Digest sem segredo correspondente (gravado por engano
    ou por chave rotacionada) não passa."""
    if not token:
        return None
    workspace_id = await resolve_asaas_webhook_workspace(db, token=token)
    if workspace_id is None:
        return None
    await set_workspace_context(db, workspace_id)
    result = await db.execute(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider == IntegrationProvider.asaas,
            IntegrationConnection.status != IntegrationStatus.disconnected,
        )
    )
    matched = False
    for connection in result.scalars().all():
        configured_token = reveal_integration_secret(connection.settings, "webhookSecret")
        if configured_token and hmac.compare_digest(configured_token, token):
            matched = True
    return workspace_id if matched else None


async def _claim_asaas_event_receipt(
    db: AsyncSession,
    *,
    workspace_id: Any,
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> tuple[AsaasWebhookReceipt, bool]:
    """Grava o recibo idempotente por (workspace_id, event_id). Retorna
    (recibo, já_existia)."""
    query = select(AsaasWebhookReceipt).where(
        AsaasWebhookReceipt.workspace_id == workspace_id,
        AsaasWebhookReceipt.event_id == event_id,
    )
    existing = await db.scalar(query)
    if existing is not None:
        return existing, True

    receipt = AsaasWebhookReceipt(
        workspace_id=workspace_id,
        event_id=event_id,
        event_type=event_type,
        status=AsaasWebhookReceiptStatus.recebido,
        payload=payload,
    )
    try:
        async with db.begin_nested():
            db.add(receipt)
            await db.flush()
    except IntegrityError:
        existing = await db.scalar(query)
        if existing is None:
            raise
        return existing, True
    return receipt, False


@router.post("/asaas", include_in_schema=False)
async def asaas_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Recebe webhooks do Asaas: valida token, grava recibo idempotente,
    responde 200 imediato e agenda o processamento assíncrono."""
    token = request.headers.get("asaas-access-token", "")
    payload_bytes = await read_bounded_body(request)

    try:
        import json

        body = json.loads(payload_bytes) if payload_bytes else {}
    except (ValueError, TypeError) as exc:
        logger.error("asaas_webhook.parse_error", error_type=safe_error_type(exc))
        raise HTTPException(status_code=400, detail="Payload do webhook Asaas inválido.") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Payload do webhook Asaas inválido.")

    workspace_id = await _resolve_workspace_id_by_token(db, token)
    if workspace_id is None:
        logger.warning("asaas_webhook.token_invalid")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "ASAAS-WH-001", "message": "Token do webhook Asaas inválido."},
        )

    event_id = body.get("id")
    event_type = body.get("event")
    if not isinstance(event_id, str) or not event_id.strip():
        raise HTTPException(status_code=400, detail="Evento Asaas sem id válido.")
    if not isinstance(event_type, str) or not event_type.strip():
        raise HTTPException(status_code=400, detail="Evento Asaas sem tipo válido.")

    logger.info(
        "asaas_webhook.received",
        event_type=event_type,
        event_ref=opaque_ref(event_id, kind="asaas_event"),
        workspace_id=str(workspace_id),
    )

    try:
        receipt, replayed = await _claim_asaas_event_receipt(
            db,
            workspace_id=workspace_id,
            event_id=event_id,
            event_type=event_type,
            payload=body,
        )
        replay_pending = replayed and receipt.status != AsaasWebhookReceiptStatus.processado
        if replayed and not replay_pending:
            logger.info(
                "asaas_webhook.replay_ignored",
                event_ref=opaque_ref(event_id, kind="asaas_event"),
            )
            await db.commit()
            return {"received": True}

        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        await db.rollback()
        logger.error("asaas_webhook.receipt_error", error_type=safe_error_type(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Falha temporária ao registrar webhook Asaas.",
        ) from exc

    # O recibo é durável; o agendamento pode falhar (broker fora do ar). Nesse
    # caso respondemos 503 para o Asaas reentregar — e a reentrega de um recibo
    # ainda não processado (replay_pending) re-agenda em vez de ignorar. Assim
    # nenhuma baixa morre em silêncio.
    from app.workers.finance_tasks import process_asaas_webhook_receipt

    try:
        process_asaas_webhook_receipt.apply_async(
            kwargs={"receipt_id": str(receipt.id), "workspace_id": str(workspace_id)}
        )
    except Exception as exc:
        logger.error("asaas_webhook.enqueue_error", error_type=safe_error_type(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Falha temporária ao agendar processamento do webhook Asaas.",
        ) from exc
    if replay_pending:
        logger.info(
            "asaas_webhook.replay_requeued",
            event_ref=opaque_ref(event_id, kind="asaas_event"),
        )
    return {"received": True}
