"""
Endpoint: Stripe Webhook
Recebe eventos do Stripe e atualiza o status das assinaturas.

Rota: POST /webhooks/stripe

Eventos tratados:
  - checkout.session.completed      → ativa assinatura após checkout
  - invoice.paid                    → renova período atual
  - invoice.payment_failed          → marca como past_due
  - customer.subscription.updated   → sincroniza status e intervalo
  - customer.subscription.deleted   → cancela assinatura

Segurança:
  - Assinatura HMAC verificada com STRIPE_WEBHOOK_SECRET
  - Janela máxima de 5 minutos para reduzir replay
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_db
from app.core.klm_runtime import EXTERNAL_ACTION_BLOCKED_CODE, external_delivery_allowed
from app.core.logging import logger
from app.core.logging_privacy import opaque_ref, safe_error_type
from app.core.request_body import read_bounded_body
from app.db.platform_control import set_commercial_provider_reconcile_context
from app.db.tenant import set_workspace_context
from app.models.billing import (
    BillingInterval,
    StripeWebhookEventReceipt,
    Subscription,
    SubscriptionStatus,
)
from app.models.commercial_catalog import (
    CommercialCheckoutIntent,
    CommercialCheckoutIntentStatus,
    CommercialContractLineStatus,
    CommercialContractProviderBinding,
    CommercialContractStatus,
    CommercialOffer,
    CommercialOfferFamily,
    CommercialOfferVersion,
    CommercialProvider,
    CommercialProviderBindingStatus,
    CommercialProviderPriceBinding,
)
from app.models.platform_work_item import PlatformWorkItem
from app.schemas.commercial_catalog import (
    WorkspaceContractLineWrite,
    WorkspaceContractRevisionRequest,
)
from app.services.commercial_catalog_service import (
    CommercialCatalogConflictError,
    CommercialCatalogService,
)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

_RECOGNIZED_STRIPE_EVENT_TYPES = frozenset(
    {
        "checkout.session.completed",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "invoice.paid",
        "invoice.payment_failed",
    }
)

# Stripe's `created` value has one-second precision. For distinct events in the
# same second, prefer the state that grants less access and wait for a newer
# event before relaxing it.
_STATUS_TIE_PRIORITY = {
    SubscriptionStatus.active: 10,
    SubscriptionStatus.trialing: 20,
    SubscriptionStatus.past_due: 30,
    SubscriptionStatus.paused: 40,
    SubscriptionStatus.unpaid: 50,
    SubscriptionStatus.cancelled: 60,
}


def _require_external_webhook_delivery() -> None:
    if not external_delivery_allowed():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": EXTERNAL_ACTION_BLOCKED_CODE,
                "message": (
                    "Webhook Stripe bloqueado enquanto entregas externas estiverem desabilitadas."
                ),
            },
        )


def _verify_stripe_signature(payload: bytes, sig_header: str, secret: str) -> dict[str, Any]:
    """
    Verifica assinatura Stripe Webhook (stripe-signature header).
    Retorna o evento parseado ou falha sem aceitar payload não assinado.
    """
    import hashlib
    import hmac
    import time

    secret = secret.strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="STRIPE_WEBHOOK_SECRET não configurado.",
        )
    if not sig_header.strip():
        logger.warning("billing.webhook_signature_invalid", provider="stripe", reason="missing")
        raise HTTPException(status_code=400, detail="stripe-signature ausente.")

    try:
        parts = [part.strip().split("=", 1) for part in sig_header.split(",")]
        timestamp = int(next(value for key, value in parts if key == "t"))
        v1_signatures = [value for key, value in parts if key == "v1" and value]
    except (ValueError, StopIteration):
        logger.warning("billing.webhook_signature_invalid", provider="stripe", reason="malformed")
        raise HTTPException(status_code=400, detail="stripe-signature inválida.") from None
    if not v1_signatures:
        logger.warning("billing.webhook_signature_invalid", provider="stripe", reason="no_v1")
        raise HTTPException(status_code=400, detail="stripe-signature inválida.")

    # Rejeita eventos com mais de 5 minutos de atraso (proteção replay)
    if abs(time.time() - timestamp) > 300:
        logger.warning("billing.webhook_signature_invalid", provider="stripe", reason="expired")
        raise HTTPException(status_code=400, detail="Evento Stripe expirado.")

    signed_payload = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in v1_signatures):
        logger.warning("billing.webhook_signature_invalid", provider="stripe", reason="mismatch")
        raise HTTPException(status_code=400, detail="Assinatura Stripe inválida.")

    event = json.loads(payload)
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="Evento Stripe inválido.")
    return event


def _stripe_expandable_id(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict) and isinstance(value.get("id"), str):
        return value["id"]
    return None


def _invoice_subscription_id(invoice: dict[str, Any]) -> str | None:
    """Read Basil's parent field first, retaining legacy API compatibility."""
    parent = invoice.get("parent")
    if isinstance(parent, dict) and parent.get("type") == "subscription_details":
        subscription_details = parent.get("subscription_details")
        if isinstance(subscription_details, dict):
            subscription_id = _stripe_expandable_id(subscription_details.get("subscription"))
            if subscription_id:
                return subscription_id
    return _stripe_expandable_id(invoice.get("subscription"))


def _event_subscription_status(
    event_type: str,
    obj: dict[str, Any],
) -> SubscriptionStatus | None:
    if event_type == "checkout.session.completed" or event_type == "invoice.paid":
        return SubscriptionStatus.active
    if event_type == "invoice.payment_failed":
        return SubscriptionStatus.past_due
    if event_type == "customer.subscription.deleted":
        return SubscriptionStatus.cancelled

    stripe_status = obj.get("status")
    return {
        "trialing": SubscriptionStatus.trialing,
        "active": SubscriptionStatus.active,
        "past_due": SubscriptionStatus.past_due,
        "canceled": SubscriptionStatus.cancelled,
        "unpaid": SubscriptionStatus.unpaid,
        "paused": SubscriptionStatus.paused,
    }.get(stripe_status)


def _should_apply_subscription_event(
    sub: Subscription,
    *,
    event_type: str,
    event_created: int | None,
    event_id: str | None,
    obj: dict[str, Any],
) -> bool:
    last_created = sub.last_stripe_event_created
    if event_created is None or last_created is None or event_created > last_created:
        return True

    if event_created < last_created:
        logger.info(
            "stripe_webhook.stale_event_ignored",
            subscription_id=str(sub.id),
            event_ref=opaque_ref(event_id, kind="stripe_event"),
            event_created=event_created,
            last_event_created=last_created,
        )
        return False

    incoming_status = _event_subscription_status(event_type, obj)
    incoming_priority = (
        _STATUS_TIE_PRIORITY.get(incoming_status) if incoming_status is not None else None
    )
    current_priority = _STATUS_TIE_PRIORITY.get(sub.status)
    if (
        incoming_priority is not None
        and current_priority is not None
        and incoming_priority > current_priority
    ):
        return True

    logger.info(
        "stripe_webhook.event_tie_ignored",
        subscription_id=str(sub.id),
        event_ref=opaque_ref(event_id, kind="stripe_event"),
        event_created=event_created,
        incoming_status=incoming_status.value if incoming_status else None,
        current_status=sub.status.value,
    )
    return False


async def _handle_subscription_updated(
    event_data: dict[str, Any],
    db: AsyncSession,
    *,
    event_type: str = "",
    event_created: int | None = None,
    event_id: str | None = None,
) -> Subscription | None:
    """Sincroniza status da assinatura após update ou checkout completo."""
    obj = event_data.get("object", {})
    if not isinstance(obj, dict):
        return None
    is_invoice_event = event_type.startswith("invoice.")
    stripe_sub_id = (
        _invoice_subscription_id(obj) if is_invoice_event else _stripe_expandable_id(obj.get("id"))
    )
    if not stripe_sub_id:
        return None

    # Serializa todos os eventos de uma assinatura antes de comparar a ordem.
    result = await db.execute(
        select(Subscription)
        .where(Subscription.stripe_subscription_id == stripe_sub_id)
        .with_for_update()
    )
    sub = result.scalar_one_or_none()
    if not sub:
        logger.warning(
            "stripe_webhook.subscription_not_found",
            subscription_ref=opaque_ref(stripe_sub_id, kind="stripe_subscription"),
        )
        return None

    if not _should_apply_subscription_event(
        sub,
        event_type=event_type,
        event_created=event_created,
        event_id=event_id,
        obj=obj,
    ):
        return sub

    # Mapeia status Stripe → nosso enum
    incoming_status = _event_subscription_status(event_type, obj)
    if incoming_status is not None:
        sub.status = incoming_status

    # Período atual
    lines = obj.get("lines", {}).get("data", []) if is_invoice_event else []
    service_period = lines[0].get("period", {}) if lines else {}
    period_start = (
        service_period.get("start") or obj.get("period_start")
        if is_invoice_event
        else obj.get("current_period_start")
    )
    period_end = (
        service_period.get("end") or obj.get("period_end")
        if is_invoice_event
        else obj.get("current_period_end")
    )
    if period_start:
        sub.current_period_start = datetime.fromtimestamp(period_start, tz=UTC)
    if period_end:
        sub.current_period_end = datetime.fromtimestamp(period_end, tz=UTC)

    # Intervalo
    items = obj.get("items", {}).get("data", [])
    if items:
        interval = items[0].get("plan", {}).get("interval", "month")
        sub.interval = BillingInterval.annually if interval == "year" else BillingInterval.monthly

    # Valor atual
    amount = (
        obj.get("total")
        if is_invoice_event
        else obj.get("plan", {}).get("amount")
        or (items[0].get("plan", {}).get("amount") if items else None)
    )
    if isinstance(amount, int) and amount >= 0:
        sub.amount_cents = amount
    if is_invoice_event and isinstance(obj.get("currency"), str):
        sub.currency = obj["currency"].upper()
    payment_method_id = _stripe_expandable_id(obj.get("default_payment_method"))
    if payment_method_id:
        sub.stripe_payment_method_id = payment_method_id

    # Persist only operational payment references/status, not the full Stripe payload.
    last_event: dict[str, Any] = {
        "event_type": event_type,
        "stripe_sub_id": stripe_sub_id,
        "event_id": event_id,
        "event_created": event_created,
    }
    if is_invoice_event:
        last_event.update(
            {
                "invoice_id": _stripe_expandable_id(obj.get("id")),
                "payment_status": ("paid" if event_type == "invoice.paid" else "failed"),
                "amount_paid": obj.get("amount_paid"),
                "amount_due": obj.get("amount_due"),
                "attempt_count": obj.get("attempt_count"),
                "paid_at": (obj.get("status_transitions") or {}).get("paid_at"),
                "payment_intent_id": _stripe_expandable_id(obj.get("payment_intent")),
                "charge_id": _stripe_expandable_id(obj.get("charge")),
            }
        )
    sub.last_stripe_event = last_event
    if event_id is not None:
        sub.last_stripe_event_id = event_id
    if event_created is not None:
        sub.last_stripe_event_created = event_created

    await db.flush()
    logger.info(
        "stripe_webhook.subscription_synced",
        subscription_id=str(sub.id),
        new_status=sub.status,
    )
    return sub


async def _handle_checkout_completed(
    event_data: dict[str, Any],
    db: AsyncSession,
    *,
    event_created: int | None = None,
    event_id: str | None = None,
) -> Subscription | None:
    """
    checkout.session.completed:
    Vincula stripe_customer_id e stripe_subscription_id à assinatura do workspace.
    O workspace_id é passado como metadata no checkout session criado pelo backend.
    """
    from uuid import UUID

    obj = event_data.get("object", {})
    if not isinstance(obj, dict):
        return None
    metadata = obj.get("metadata", {})
    if not isinstance(metadata, dict):
        return None
    workspace_id_str = metadata.get("workspace_id")
    stripe_customer_id = _stripe_expandable_id(obj.get("customer"))
    stripe_sub_id = _stripe_expandable_id(obj.get("subscription"))

    if not workspace_id_str or not stripe_sub_id:
        return None

    try:
        workspace_id = UUID(workspace_id_str)
    except ValueError:
        return None

    result = await db.execute(
        select(Subscription).where(Subscription.workspace_id == workspace_id).with_for_update()
    )
    sub = result.scalar_one_or_none()
    if not sub:
        return None

    if not _should_apply_subscription_event(
        sub,
        event_type="checkout.session.completed",
        event_created=event_created,
        event_id=event_id,
        obj=obj,
    ):
        return sub

    sub.stripe_customer_id = stripe_customer_id
    sub.stripe_subscription_id = stripe_sub_id
    await db.flush()

    # Sincroniza status da subscription recém-criada
    return await _handle_subscription_updated(
        {"object": {"id": stripe_sub_id, "status": "active"}, "type": "checkout.session.completed"},
        db,
        event_type="checkout.session.completed",
        event_created=event_created,
        event_id=event_id,
    )


def _canonical_status(event_type: str, obj: dict[str, Any]) -> CommercialContractStatus | None:
    if event_type in {"checkout.session.completed", "invoice.paid"}:
        return CommercialContractStatus.active
    if event_type == "invoice.payment_failed":
        return CommercialContractStatus.past_due
    if event_type == "customer.subscription.deleted":
        return CommercialContractStatus.cancelled
    return {
        "trialing": CommercialContractStatus.trialing,
        "active": CommercialContractStatus.active,
        "past_due": CommercialContractStatus.past_due,
        "paused": CommercialContractStatus.paused,
        "canceled": CommercialContractStatus.cancelled,
        "unpaid": CommercialContractStatus.past_due,
    }.get(obj.get("status"))


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize timestamps loaded by dialects that omit timezone metadata."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _contract_lines_for_revision(contract) -> list[WorkspaceContractLineWrite]:
    return [
        WorkspaceContractLineWrite(
            sku=line.sku,
            policy_version_id=line.policy_version_id,
            quantity=line.quantity,
            contracted_price_cents=line.contracted_price_cents,
            billing_interval=line.billing_interval,
            entitlement_overrides=line.entitlement_overrides,
            effective_from=_as_utc(line.effective_from),
            effective_until=_as_utc(line.effective_until),
        )
        for line in contract.lines
        if line.status == CommercialContractLineStatus.active
    ]


def _contract_line_signature(line: WorkspaceContractLineWrite) -> tuple[str, UUID, int, str]:
    return (
        line.sku,
        line.policy_version_id,
        line.quantity,
        line.billing_interval.value,
    )


async def _canonical_reconciliation_work_item(
    db: AsyncSession,
    *,
    intent: CommercialCheckoutIntent,
    event_id: str,
) -> None:
    now = datetime.now(UTC)
    existing = await db.scalar(
        select(PlatformWorkItem).where(
            PlatformWorkItem.dedupe_key == f"commercial_checkout_reconcile:{intent.id}"
        )
    )
    if existing is not None:
        return
    db.add(
        PlatformWorkItem(
            source_type="commercial_checkout_reconcile",
            source_id=intent.id,
            source_version=str(intent.version),
            dedupe_key=f"commercial_checkout_reconcile:{intent.id}",
            kind="alert",
            state="open",
            priority="P1",
            workspace_id=intent.workspace_id,
            safe_title="Pagamento confirmado requer reconciliação",
            safe_summary="O contrato mudou depois da abertura do checkout.",
            href=(
                "/blackbox/workspaces?"
                f"sourceType=commercial_checkout_reconcile&sourceId={intent.id}"
                f"&tab=commercial&focus=workspace&workspaceId={intent.workspace_id}"
            ),
            required_module="commercial_contracts",
            required_action="manage",
            allowed_commands=[],
            opened_at=now,
            source_updated_at=now,
            projected_at=now,
            projection_version=1,
            payload_safe={"eventRef": opaque_ref(event_id, kind="stripe_event")},
        )
    )


def _stripe_subscription_price_ids(obj: dict[str, Any]) -> set[str]:
    """Return the concrete Stripe prices carried by a subscription snapshot."""
    items = obj.get("items")
    if not isinstance(items, dict) or not isinstance(items.get("data"), list):
        return set()
    price_ids: set[str] = set()
    for item in items["data"]:
        if not isinstance(item, dict):
            continue
        price = item.get("price")
        price_id = _stripe_expandable_id(price)
        if price_id:
            price_ids.add(price_id)
    return price_ids


async def _canonical_subscription_reconciliation_work_item(
    db: AsyncSession,
    *,
    binding: CommercialContractProviderBinding,
    event_id: str,
    reason: str,
) -> None:
    """Surface a provider/catalog mismatch without pretending reconciliation."""
    dedupe_key = f"commercial_subscription_reconcile:{binding.id}"
    existing = await db.scalar(
        select(PlatformWorkItem).where(PlatformWorkItem.dedupe_key == dedupe_key)
    )
    now = datetime.now(UTC)
    if existing is not None:
        existing.state = "open"
        existing.closed_at = None
        existing.source_version = str(binding.version)
        existing.safe_summary = reason
        existing.source_updated_at = now
        existing.projected_at = now
        existing.projection_version += 1
        existing.payload_safe = {"eventRef": opaque_ref(event_id, kind="stripe_event")}
        return
    db.add(
        PlatformWorkItem(
            source_type="commercial_subscription_reconcile",
            source_id=binding.id,
            source_version=str(binding.version),
            dedupe_key=dedupe_key,
            kind="alert",
            state="open",
            priority="P1",
            workspace_id=binding.workspace_id,
            safe_title="Assinatura Stripe requer reconciliação",
            safe_summary=reason,
            href=(
                "/blackbox/workspaces?"
                f"sourceType=commercial_subscription_reconcile&sourceId={binding.id}"
                f"&tab=commercial&focus=workspace&workspaceId={binding.workspace_id}"
            ),
            required_module="commercial_contracts",
            required_action="manage",
            allowed_commands=[],
            opened_at=now,
            source_updated_at=now,
            projected_at=now,
            projection_version=1,
            payload_safe={"eventRef": opaque_ref(event_id, kind="stripe_event")},
        )
    )


async def _resolve_canonical_subscription_reconciliation_work_item(
    db: AsyncSession,
    *,
    binding: CommercialContractProviderBinding,
) -> None:
    """Close the operational alert after a later event reconciles successfully."""
    existing = await db.scalar(
        select(PlatformWorkItem).where(
            PlatformWorkItem.dedupe_key
            == f"commercial_subscription_reconcile:{binding.id}"
        )
    )
    if existing is None or existing.state in {"done", "cancelled"}:
        return
    now = datetime.now(UTC)
    existing.state = "done"
    existing.closed_at = now
    existing.source_updated_at = now
    existing.projected_at = now
    existing.projection_version += 1


async def _contract_lines_for_stripe_subscription(
    db: AsyncSession,
    *,
    contract,
    subscription: dict[str, Any],
    provider_binding: CommercialContractProviderBinding,
    event_id: str,
) -> list[WorkspaceContractLineWrite] | None:
    """Map a portal plan change back to the immutable canonical offer version."""
    price_ids = _stripe_subscription_price_ids(subscription)
    if not price_ids:
        return None
    if len(price_ids) != 1:
        await _canonical_subscription_reconciliation_work_item(
            db,
            binding=provider_binding,
            event_id=event_id,
            reason=(
                "A assinatura Stripe possui uma composição de preços que não pode "
                "ser aplicada automaticamente ao contrato V1. Revise a assinatura."
            ),
        )
        return None
    rows = (
        await db.execute(
            select(
                CommercialProviderPriceBinding,
                CommercialOffer,
                CommercialOfferVersion,
            )
            .join(
                CommercialOffer,
                CommercialOffer.id == CommercialProviderPriceBinding.offer_id,
            )
            .join(
                CommercialOfferVersion,
                CommercialOfferVersion.id
                == CommercialProviderPriceBinding.offer_version_id,
            )
            .where(
                CommercialProviderPriceBinding.provider == CommercialProvider.stripe,
                CommercialProviderPriceBinding.provider_price_id.in_(price_ids),
                CommercialProviderPriceBinding.status
                == CommercialProviderBindingStatus.active,
                CommercialOffer.family == CommercialOfferFamily.crm,
            )
        )
    ).all()
    if len(rows) != 1:
        await _canonical_subscription_reconciliation_work_item(
            db,
            binding=provider_binding,
            event_id=event_id,
            reason=(
                "O preço ativo recebido do Stripe não possui um único vínculo CRM "
                "publicado. Revise o binding antes de alterar o contrato."
            ),
        )
        return None
    price_binding, offer, version = rows[0]
    active_lines = [
        line
        for line in contract.lines
        if line.status == CommercialContractLineStatus.active
    ]
    lines = [
        WorkspaceContractLineWrite(
            sku=line.sku,
            policy_version_id=line.policy_version_id,
            quantity=line.quantity,
            contracted_price_cents=line.contracted_price_cents,
            billing_interval=line.billing_interval,
            entitlement_overrides=line.entitlement_overrides,
            effective_from=_as_utc(line.effective_from),
            effective_until=_as_utc(line.effective_until),
        )
        for line in active_lines
        if line.offer_family != CommercialOfferFamily.crm
    ]
    current_crm = next(
        (
            line
            for line in contract.lines
            if line.status == CommercialContractLineStatus.active
            and line.offer_family == CommercialOfferFamily.crm
        ),
        None,
    )
    quantity = 1
    for item in subscription.get("items", {}).get("data", []):
        if not isinstance(item, dict):
            continue
        if _stripe_expandable_id(item.get("price")) == price_binding.provider_price_id:
            raw_quantity = item.get("quantity", 1)
            if isinstance(raw_quantity, int) and not isinstance(raw_quantity, bool):
                quantity = max(1, raw_quantity)
            break
    if current_crm is not None and current_crm.policy_version_id == version.id:
        lines.append(
            WorkspaceContractLineWrite(
                sku=current_crm.sku,
                policy_version_id=current_crm.policy_version_id,
                quantity=quantity,
                contracted_price_cents=current_crm.contracted_price_cents,
                billing_interval=current_crm.billing_interval,
                entitlement_overrides=current_crm.entitlement_overrides,
                effective_from=_as_utc(current_crm.effective_from),
                effective_until=_as_utc(current_crm.effective_until),
            )
        )
    else:
        lines.append(
            WorkspaceContractLineWrite(
                sku=offer.sku,
                policy_version_id=version.id,
                quantity=quantity,
                billing_interval=price_binding.billing_interval,
            )
        )
    await _resolve_canonical_subscription_reconciliation_work_item(
        db,
        binding=provider_binding,
    )
    return lines


async def _handle_canonical_checkout_completed(
    event_data: dict[str, Any],
    db: AsyncSession,
    *,
    event_created: int,
    event_id: str,
) -> CommercialContractProviderBinding | None:
    obj = event_data.get("object", {})
    metadata = obj.get("metadata", {}) if isinstance(obj, dict) else {}
    if not isinstance(metadata, dict) or not metadata.get("commercial_checkout_intent_id"):
        return None
    try:
        intent_id = UUID(str(metadata["commercial_checkout_intent_id"]))
        workspace_id = UUID(str(metadata["workspace_id"]))
        contract_id = UUID(str(metadata["commercial_contract_id"]))
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Metadata canônica do checkout inválida.")

    await set_workspace_context(db, workspace_id)
    await set_commercial_provider_reconcile_context(db)
    intent = await db.scalar(
        select(CommercialCheckoutIntent)
        .where(
            CommercialCheckoutIntent.id == intent_id,
            CommercialCheckoutIntent.workspace_id == workspace_id,
            CommercialCheckoutIntent.contract_id == contract_id,
        )
        .with_for_update()
    )
    if intent is None:
        raise HTTPException(status_code=503, detail="Intenção canônica indisponível.")
    if intent.provider_session_id != _stripe_expandable_id(obj.get("id")):
        raise HTTPException(status_code=409, detail="Sessão Stripe não corresponde à intenção.")

    binding = await db.scalar(
        select(CommercialContractProviderBinding)
        .where(
            CommercialContractProviderBinding.contract_id == contract_id,
            CommercialContractProviderBinding.provider == CommercialProvider.stripe,
        )
        .with_for_update()
    )
    if binding is None:
        binding = CommercialContractProviderBinding(
            workspace_id=workspace_id,
            contract_id=contract_id,
            provider=CommercialProvider.stripe,
            version=1,
        )
        db.add(binding)
    else:
        binding.version += 1
    binding.provider_customer_id = _stripe_expandable_id(obj.get("customer"))
    binding.provider_subscription_id = _stripe_expandable_id(obj.get("subscription"))
    binding.provider_status = "active"
    binding.last_event_id = event_id
    binding.last_event_created = event_created

    if intent.status == CommercialCheckoutIntentStatus.completed:
        await db.flush()
        return binding
    contract = await CommercialCatalogService(db).get_workspace_contract(workspace_id)
    if contract.version != intent.expected_contract_version:
        intent.status = CommercialCheckoutIntentStatus.reconciliation_required
        intent.version += 1
        await _canonical_reconciliation_work_item(db, intent=intent, event_id=event_id)
        await db.flush()
        return binding
    lines = [WorkspaceContractLineWrite.model_validate(item) for item in intent.target_lines]
    await CommercialCatalogService(db).revise_contract(
        contract_id=contract_id,
        expected_version=contract.version,
        payload=WorkspaceContractRevisionRequest(lines=lines, reason=intent.reason),
        actor_user_id=None,
        idempotency_key=f"stripe:{event_id}",
        operation="stripe_checkout_activate_contract",
        event_type="stripe_checkout_completed",
        preserve_exceptions=True,
        target_status=CommercialContractStatus.active,
    )
    intent.status = CommercialCheckoutIntentStatus.completed
    intent.checkout_url = None
    intent.version += 1
    await db.flush()
    return binding


async def _handle_canonical_subscription_event(
    event_data: dict[str, Any],
    db: AsyncSession,
    *,
    event_type: str,
    event_created: int,
    event_id: str,
) -> CommercialContractProviderBinding | None:
    obj = event_data.get("object", {})
    if not isinstance(obj, dict):
        return None
    stripe_sub_id = (
        _invoice_subscription_id(obj)
        if event_type.startswith("invoice.")
        else _stripe_expandable_id(obj.get("id"))
    )
    if not stripe_sub_id:
        return None
    binding = await db.scalar(
        select(CommercialContractProviderBinding)
        .where(
            CommercialContractProviderBinding.provider == CommercialProvider.stripe,
            CommercialContractProviderBinding.provider_subscription_id == stripe_sub_id,
        )
        .with_for_update()
    )
    if binding is None:
        return None
    if binding.last_event_created is not None and event_created < binding.last_event_created:
        return binding
    await set_workspace_context(db, binding.workspace_id)
    await set_commercial_provider_reconcile_context(db)
    target_status = _canonical_status(event_type, obj)
    contract = await CommercialCatalogService(db).get_workspace_contract(binding.workspace_id)
    target_lines = _contract_lines_for_revision(contract)
    if event_type == "customer.subscription.updated":
        mapped_lines = await _contract_lines_for_stripe_subscription(
            db,
            contract=contract,
            subscription=obj,
            provider_binding=binding,
            event_id=event_id,
        )
        if mapped_lines is not None:
            target_lines = mapped_lines
    lines_changed = sorted(_contract_line_signature(line) for line in target_lines) != sorted(
        _contract_line_signature(line) for line in _contract_lines_for_revision(contract)
    )
    if (target_status is not None and target_status != contract.status) or lines_changed:
        try:
            await CommercialCatalogService(db).revise_contract(
                contract_id=contract.id,
                expected_version=contract.version,
                payload=WorkspaceContractRevisionRequest(
                    lines=target_lines,
                    reason=f"Reconciliação Stripe: {event_type}",
                ),
                actor_user_id=None,
                idempotency_key=f"stripe:{event_id}",
                operation="stripe_reconcile_contract",
                event_type="stripe_contract_reconciled",
                preserve_exceptions=True,
                target_status=target_status,
            )
        except CommercialCatalogConflictError:
            raise HTTPException(status_code=503, detail="Contrato mudou durante reconciliação.")
    if event_type == "customer.subscription.updated" and obj.get("cancel_at_period_end") is True:
        binding.provider_status = "cancel_at_period_end"
    else:
        binding.provider_status = (
            target_status.value
            if target_status is not None
            else str(obj.get("status") or "unknown")
        )
    binding.last_event_id = event_id
    binding.last_event_created = event_created
    binding.version += 1
    await db.flush()
    return binding


async def _claim_stripe_event_receipt(
    db: AsyncSession,
    *,
    event_id: str,
    event_type: str,
    event_created: int,
    payload_hash: str,
) -> tuple[StripeWebhookEventReceipt, bool]:
    query = select(StripeWebhookEventReceipt).where(StripeWebhookEventReceipt.event_id == event_id)
    existing = await db.scalar(query)
    if existing is not None:
        if (
            existing.event_type != event_type
            or existing.event_created != event_created
            or not hmac.compare_digest(existing.payload_hash, payload_hash)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="event_id do Stripe já recebido com conteúdo diferente.",
            )
        return existing, True

    receipt = StripeWebhookEventReceipt(
        event_id=event_id,
        event_type=event_type,
        event_created=event_created,
        payload_hash=payload_hash,
        state="processing",
    )
    # SQLite can release a top-level SAVEPOINT in a way that survives the
    # subsequent rollback used for retryable webhook failures. PostgreSQL keeps
    # the nested transaction for the concurrency race; local SQLite uses the
    # surrounding request transaction directly so a 503 leaves no receipt.
    if db.get_bind().dialect.name == "sqlite":
        db.add(receipt)
        await db.flush()
        return receipt, False
    try:
        async with db.begin_nested():
            db.add(receipt)
            await db.flush()
    except IntegrityError:
        existing = await db.scalar(query)
        if existing is None:
            raise
        if (
            existing.event_type != event_type
            or existing.event_created != event_created
            or not hmac.compare_digest(existing.payload_hash, payload_hash)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="event_id do Stripe já recebido com conteúdo diferente.",
            )
        return existing, True
    return receipt, False


@router.post("/stripe", include_in_schema=False)
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Recebe e processa webhooks do Stripe.
    Confirma com 200 somente após o handler reconhecido concluir; falhas
    inesperadas retornam 503 para rollback e reentrega pelo Stripe.
    """
    _require_external_webhook_delivery()
    payload = await read_bounded_body(request)
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = _verify_stripe_signature(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("stripe_webhook.parse_error", error_type=safe_error_type(exc))
        raise HTTPException(status_code=400, detail="Erro ao processar webhook.")

    event_id = event.get("id")
    event_type = event.get("type", "")
    event_created = event.get("created")
    event_data = event.get("data", {})
    if not isinstance(event_id, str) or not event_id.strip():
        raise HTTPException(status_code=400, detail="Evento Stripe sem id válido.")
    if not isinstance(event_created, int) or isinstance(event_created, bool) or event_created < 0:
        raise HTTPException(status_code=400, detail="Evento Stripe sem created válido.")
    if not isinstance(event_type, str) or not isinstance(event_data, dict):
        raise HTTPException(status_code=400, detail="Envelope de evento Stripe inválido.")

    logger.info(
        "stripe_webhook.received",
        event_type=event_type,
        event_ref=opaque_ref(event_id, kind="stripe_event"),
    )

    try:
        receipt, replayed = await _claim_stripe_event_receipt(
            db,
            event_id=event_id,
            event_type=event_type,
            event_created=event_created,
            payload_hash=hashlib.sha256(payload).hexdigest(),
        )
        if replayed:
            logger.info(
                "stripe_webhook.replay_ignored",
                event_ref=opaque_ref(event_id, kind="stripe_event"),
            )
            return {"received": True}

        subscription: Subscription | None = None
        canonical_binding: CommercialContractProviderBinding | None = None
        if event_type == "checkout.session.completed":
            canonical_binding = await _handle_canonical_checkout_completed(
                event_data, db, event_created=event_created, event_id=event_id
            )
            if canonical_binding is None:
                subscription = await _handle_checkout_completed(
                    event_data,
                    db,
                    event_created=event_created,
                    event_id=event_id,
                )

        elif event_type in _RECOGNIZED_STRIPE_EVENT_TYPES:
            canonical_binding = await _handle_canonical_subscription_event(
                event_data,
                db,
                event_type=event_type,
                event_created=event_created,
                event_id=event_id,
            )
            if canonical_binding is None:
                subscription = await _handle_subscription_updated(
                    event_data,
                    db,
                    event_type=event_type,
                    event_created=event_created,
                    event_id=event_id,
                )

        else:
            logger.debug("stripe_webhook.ignored", event_type=event_type)

        if (
            event_type in _RECOGNIZED_STRIPE_EVENT_TYPES
            and subscription is None
            and canonical_binding is None
        ):
            logger.warning(
                "stripe_webhook.recognized_event_subscription_unavailable",
                event_type=event_type,
                event_ref=opaque_ref(event_id, kind="stripe_event"),
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Assinatura local indisponível para o evento Stripe.",
            )

        receipt.subscription_id = subscription.id if subscription else None
        receipt.state = "processed"
        receipt.processed_at = datetime.now(UTC)
        await db.commit()

    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        await db.rollback()
        logger.error(
            "stripe_webhook.handler_error",
            event_type=event_type,
            error_type=safe_error_type(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Falha temporária ao processar evento Stripe.",
        ) from exc

    return {"received": True}
