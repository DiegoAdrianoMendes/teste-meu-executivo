"""
Endpoint: Webhook de leads de sites (F12 — Central de Conexões).

Rota pública: POST /api/v1/webhooks/leads/{token}
Rotas de configuração (RBAC admin do workspace): POST/GET .../leads/token

Ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §F12 e
`app/services/lead_webhook_service.py` para o contrato completo.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    AuthContext,
    get_current_workspace_id,
    get_db,
    require_workspace_admin,
)
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.core.request_body import read_bounded_body
from app.models.finance import IntegrationConnection, IntegrationProvider, IntegrationStatus
from app.services.lead_webhook_service import (
    MAX_PAYLOAD_BYTES,
    check_rate_limit,
    generate_or_rotate_token,
    ingest_lead,
    resolve_workspace_by_token,
)
from sqlalchemy import select

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])
config_router = APIRouter(prefix="/integrations/site-leads", tags=["Integrações — Leads de sites"])


@router.post("/leads/{token}", include_in_schema=False)
async def receive_site_lead(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Recebe um lead vindo de um formulário de site. Fail-closed contra
    scanners: qualquer rejeição (token inválido, payload ilegível, honeypot)
    responde 200 quando possível, sem vazar detalhe de implementação — a
    única exceção é o token inválido, que responde 401 (contrato explícito
    do titular: "token errado 401")."""
    raw_body = await read_bounded_body(
        request,
        max_bytes=MAX_PAYLOAD_BYTES,
        too_large_detail="Payload excede o tamanho máximo.",
    )

    workspace_id = await resolve_workspace_by_token(db, token)
    if workspace_id is None:
        logger.warning("lead_webhook.token_invalid")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "LEAD-WH-001", "message": "Token do webhook de leads inválido."},
        )

    if not check_rate_limit(token):
        logger.warning("lead_webhook.rate_limited", workspace_id=str(workspace_id))
        return {"status": "rejected", "reason": "rate_limited"}

    try:
        # Reconstrói um Request "puro" a partir do body já lido, evitando
        # depender de `request.form()`/`request.json()` reconsumirem o stream.
        import json as _json

        content_type = (request.headers.get("content-type") or "").lower()
        if "application/json" in content_type:
            payload = _json.loads(raw_body) if raw_body else {}
            if not isinstance(payload, dict):
                raise ValueError("payload não é um objeto")
        elif "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
            payload = dict((await request.form()).multi_items())
        else:
            payload = _json.loads(raw_body) if raw_body else {}
            if not isinstance(payload, dict):
                raise ValueError("payload não é um objeto")
    except Exception as exc:  # noqa: BLE001 — payload ilegível: 200 com rejeição registrada, nunca vaza erro
        logger.warning(
            "lead_webhook.payload_unparseable",
            workspace_id=str(workspace_id),
            error_type=safe_error_type(exc),
        )
        return {"status": "rejected", "reason": "payload_ilegivel"}

    connection = await db.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider == IntegrationProvider.site_leads,
            IntegrationConnection.status != IntegrationStatus.disconnected,
        )
    )
    if connection is None:
        # Token válido mas integração desativada nesse meio-tempo — fail-closed silencioso.
        return {"status": "rejected", "reason": "integracao_inativa"}

    try:
        result = await ingest_lead(db, workspace_id=workspace_id, connection=connection, payload=payload)
    except Exception as exc:  # noqa: BLE001 — nunca vaza stacktrace para o endpoint público
        await db.rollback()
        logger.error(
            "lead_webhook.ingest_failed",
            workspace_id=str(workspace_id),
            error_type=safe_error_type(exc),
        )
        return {"status": "rejected", "reason": "erro_interno_registrado"}

    if not result.accepted:
        return {"status": "rejected", "reason": result.reason}

    return {"status": "accepted", "reason": result.reason}


@config_router.post("/token")
async def rotate_site_leads_token(
    workspace_id=Depends(get_current_workspace_id),
    _admin: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
):
    """Gera/rotaciona o token do webhook de leads do site do workspace atual.
    RBAC: apenas admin do workspace (configuração de integração)."""
    plain_token = await generate_or_rotate_token(db, workspace_id=workspace_id)
    await db.commit()
    return {
        "token": plain_token,
        "webhookUrl": f"/api/v1/webhooks/leads/{plain_token}",
    }
