"""Webhooks globais de Asaas e Mercado Pago para a assinatura do SaaS.

Estas rotas não reutilizam o webhook Asaas tenant em ``/webhooks/asaas``.
Credenciais, recibos e efeitos pertencem à plataforma Meu Executivo.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.asaas_adapter import AsaasSaaSBillingAdapter
from app.billing.mercado_pago_adapter import MercadoPagoSaaSBillingAdapter
from app.billing.port import SaaSBillingGatewayError
from app.core.config import settings
from app.core.deps import get_db
from app.core.klm_runtime import EXTERNAL_ACTION_BLOCKED_CODE, external_delivery_allowed
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.core.request_body import read_bounded_body
from app.models.commercial_catalog import (
    CommercialCheckoutIntentStatus,
    CommercialContractStatus,
    CommercialProvider,
)
from app.services.commercial_provider_reconciliation import (
    CommercialProviderReconciliationError,
    activate_checkout,
    mark_checkout_terminal,
    reconcile_subscription_status,
)

router = APIRouter(prefix="/webhooks/saas", tags=["SaaS Billing Webhooks"])


def _require_external_gate() -> None:
    if not external_delivery_allowed():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": EXTERNAL_ACTION_BLOCKED_CODE,
                "message": "Entregas externas estão desabilitadas.",
            },
        )


def _json_object(payload: bytes, provider: str) -> dict[str, Any]:
    try:
        body = json.loads(payload) if payload else {}
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Payload {provider} inválido.") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail=f"Payload {provider} inválido.")
    return body


def _resource_id(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or "")
    return str(value or "")


def _iso_timestamp(value: object) -> int:
    if not value:
        return int(datetime.now(UTC).timestamp())
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(parsed.timestamp())
    except ValueError:
        return int(datetime.now(UTC).timestamp())


def _verify_mercado_pago_signature(request: Request, data_id: str) -> tuple[str, int]:
    secret = settings.MERCADO_PAGO_WEBHOOK_SECRET.strip()
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook Mercado Pago não configurado.")
    request_id = request.headers.get("x-request-id", "").strip()
    parts = {}
    for item in request.headers.get("x-signature", "").split(","):
        key, separator, value = item.strip().partition("=")
        if separator:
            parts[key] = value
    ts = parts.get("ts", "")
    received = parts.get("v1", "")
    try:
        timestamp = int(ts)
    except ValueError:
        logger.warning(
            "billing.webhook_signature_invalid", provider="mercado_pago", reason="malformed"
        )
        raise HTTPException(status_code=401, detail="Assinatura Mercado Pago inválida.") from None
    seconds = timestamp // 1000 if timestamp > 10_000_000_000 else timestamp
    if abs(int(datetime.now(UTC).timestamp()) - seconds) > 300:
        logger.warning(
            "billing.webhook_signature_invalid", provider="mercado_pago", reason="expired"
        )
        raise HTTPException(status_code=401, detail="Assinatura Mercado Pago expirada.")
    # Mercado Pago requires an alphanumeric data.id to be normalized to
    # lowercase when composing the signed manifest.
    signed_data_id = data_id.lower() if not data_id.isdigit() else data_id
    manifest = f"id:{signed_data_id};request-id:{request_id};ts:{ts};"
    expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    if not received or not request_id or not hmac.compare_digest(expected, received):
        logger.warning(
            "billing.webhook_signature_invalid", provider="mercado_pago", reason="mismatch"
        )
        raise HTTPException(status_code=401, detail="Assinatura Mercado Pago inválida.")
    return request_id, seconds


def _asaas_target(event_type: str) -> CommercialContractStatus | None:
    if event_type in {
        "SUBSCRIPTION_CREATED", "SUBSCRIPTION_UPDATED", "PAYMENT_CONFIRMED", "PAYMENT_RECEIVED"
    }:
        return CommercialContractStatus.active
    if event_type in {"SUBSCRIPTION_INACTIVATED"}:
        return CommercialContractStatus.paused
    if event_type in {"SUBSCRIPTION_DELETED"}:
        return CommercialContractStatus.cancelled
    if event_type in {"PAYMENT_OVERDUE", "PAYMENT_CREDIT_CARD_CAPTURE_REFUSED"}:
        return CommercialContractStatus.past_due
    return None


@router.post("/asaas", include_in_schema=False)
async def saas_asaas_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    _require_external_gate()
    token = request.headers.get("asaas-access-token", "")
    secret = settings.SAAS_ASAAS_WEBHOOK_TOKEN.strip()
    if not secret or not hmac.compare_digest(token, secret):
        logger.warning(
            "billing.webhook_signature_invalid",
            provider="asaas_saas",
            reason="not_configured" if not secret else "mismatch",
        )
        raise HTTPException(status_code=401, detail="Token do webhook Asaas SaaS inválido.")
    body = _json_object(await read_bounded_body(request), "Asaas")
    event_id = str(body.get("id") or "").strip()
    event_type = str(body.get("event") or "").strip()
    if not event_id or not event_type:
        raise HTTPException(status_code=400, detail="Evento Asaas SaaS incompleto.")
    resource = body.get("checkout") or body.get("subscription") or body.get("payment") or {}
    if not isinstance(resource, dict):
        raise HTTPException(status_code=400, detail="Recurso Asaas SaaS inválido.")
    created = _iso_timestamp(body.get("dateCreated") or resource.get("dateCreated"))
    adapter = AsaasSaaSBillingAdapter(
        access_token=settings.SAAS_ASAAS_ACCESS_TOKEN,
        sandbox=settings.SAAS_ASAAS_SANDBOX,
    )
    try:
        if event_type.startswith("CHECKOUT_"):
            checkout_id = _resource_id(resource)
            # Eventos terminais podem chegar depois de o checkout deixar de ser
            # consultável. O payload assinado é suficiente para localizar a
            # intenção; no pagamento, consultamos a fonte para confirmar a
            # assinatura criada e seus identificadores.
            canonical = resource
            if event_type == "CHECKOUT_PAID" or not resource.get("externalReference"):
                remote = await adapter.retrieve_checkout(checkout_id)
                canonical = {**resource, **remote}
            reference = canonical.get("externalReference") or resource.get("externalReference")
            if event_type == "CHECKOUT_PAID":
                subscription_id = _resource_id(canonical.get("subscription"))
                if not subscription_id:
                    raise CommercialProviderReconciliationError(
                        "Asaas confirmou o checkout sem identificar a assinatura recorrente."
                    )
                await activate_checkout(
                    db,
                    provider=CommercialProvider.asaas,
                    event_id=event_id,
                    event_created=created,
                    external_reference=reference,
                    provider_session_id=checkout_id,
                    provider_subscription_id=subscription_id,
                    provider_customer_id=_resource_id(canonical.get("customer")) or None,
                    provider_status="active",
                )
            elif event_type in {"CHECKOUT_CANCELED", "CHECKOUT_EXPIRED"}:
                await mark_checkout_terminal(
                    db,
                    provider=CommercialProvider.asaas,
                    external_reference=reference,
                    provider_session_id=checkout_id,
                    status=(
                        CommercialCheckoutIntentStatus.cancelled
                        if event_type == "CHECKOUT_CANCELED"
                        else CommercialCheckoutIntentStatus.expired
                    ),
                    failure_code=event_type.lower(),
                )
        else:
            subscription_id = _resource_id(resource.get("subscription"))
            if event_type.startswith("SUBSCRIPTION_"):
                subscription_id = _resource_id(resource)
            if subscription_id:
                await reconcile_subscription_status(
                    db,
                    provider=CommercialProvider.asaas,
                    event_id=event_id,
                    event_created=created,
                    provider_subscription_id=subscription_id,
                    provider_status=event_type.lower(),
                    target_status=_asaas_target(event_type),
                )
        await db.commit()
    except (CommercialProviderReconciliationError, SaaSBillingGatewayError) as error:
        await db.rollback()
        logger.warning("saas_asaas_webhook.retry", error_type=safe_error_type(error))
        raise HTTPException(
            status_code=503, detail="Evento Asaas SaaS não reconciliado."
        ) from error
    return {"received": True}


@router.post("/mercado-pago", include_in_schema=False)
async def saas_mercado_pago_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    _require_external_gate()
    body = _json_object(await read_bounded_body(request), "Mercado Pago")
    body_data = body.get("data") if isinstance(body.get("data"), dict) else {}
    data_id = str(request.query_params.get("data.id") or body_data.get("id") or "").strip()
    if not data_id:
        raise HTTPException(status_code=400, detail="Evento Mercado Pago sem assinatura.")
    request_id, created = _verify_mercado_pago_signature(request, data_id)
    adapter = MercadoPagoSaaSBillingAdapter(access_token=settings.MERCADO_PAGO_ACCESS_TOKEN)
    try:
        preapproval = await adapter.retrieve_preapproval(data_id)
        provider_status = str(preapproval.get("status") or "unknown")
        event_id = hashlib.sha256(
            f"{request_id}:{data_id}:{body.get('action')}:{created}".encode()
        ).hexdigest()
        reference = preapproval.get("external_reference")
        if provider_status == "authorized" and reference:
            await activate_checkout(
                db,
                provider=CommercialProvider.mercado_pago,
                event_id=event_id,
                event_created=created,
                external_reference=reference,
                provider_session_id=data_id,
                provider_subscription_id=data_id,
                provider_customer_id=_resource_id(preapproval.get("payer_id")) or None,
                provider_status=provider_status,
            )
        else:
            target_status = {
                "authorized": CommercialContractStatus.active,
                "paused": CommercialContractStatus.paused,
                "cancelled": CommercialContractStatus.cancelled,
            }.get(provider_status)
            binding = await reconcile_subscription_status(
                db,
                provider=CommercialProvider.mercado_pago,
                event_id=event_id,
                event_created=created,
                provider_subscription_id=data_id,
                provider_status=provider_status,
                target_status=target_status,
            )
            if binding is None and provider_status == "cancelled" and reference:
                await mark_checkout_terminal(
                    db,
                    provider=CommercialProvider.mercado_pago,
                    external_reference=reference,
                    provider_session_id=data_id,
                    status=CommercialCheckoutIntentStatus.cancelled,
                    failure_code="cancelled",
                )
        await db.commit()
    except (CommercialProviderReconciliationError, SaaSBillingGatewayError) as error:
        await db.rollback()
        logger.warning("saas_mercado_pago_webhook.retry", error_type=safe_error_type(error))
        raise HTTPException(
            status_code=503, detail="Evento Mercado Pago não reconciliado."
        ) from error
    return {"received": True}
