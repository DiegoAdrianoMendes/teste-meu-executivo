"""Reconciliação canônica de assinaturas SaaS não-Stripe.

O webhook autenticado fornece apenas identificadores do provedor. Este serviço
aplica a intenção já persistida; preço, plano, descontos e bônus continuam sob
autoridade do contrato comercial interno.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.platform_control import set_commercial_provider_reconcile_context
from app.db.tenant import set_workspace_context
from app.models.commercial_catalog import (
    CommercialCheckoutIntent,
    CommercialCheckoutIntentStatus,
    CommercialContractLineStatus,
    CommercialContractProviderBinding,
    CommercialContractStatus,
    CommercialProvider,
)
from app.models.platform_work_item import PlatformWorkItem
from app.schemas.commercial_catalog import (
    WorkspaceContractLineWrite,
    WorkspaceContractRevisionRequest,
)
from app.services.commercial_catalog_service import CommercialCatalogService


class CommercialProviderReconciliationError(RuntimeError):
    """Evento válido que não pode ser aplicado com segurança."""


def parse_checkout_reference(value: object) -> tuple[UUID, UUID]:
    try:
        intent_raw, workspace_raw = str(value).split("|", 1)
        return UUID(intent_raw), UUID(workspace_raw)
    except (AttributeError, TypeError, ValueError) as error:
        raise CommercialProviderReconciliationError(
            "Referência canônica do checkout inválida."
        ) from error


def _active_lines(contract) -> list[WorkspaceContractLineWrite]:
    return [
        WorkspaceContractLineWrite(
            sku=line.sku,
            policy_version_id=line.policy_version_id,
            quantity=line.quantity,
            contracted_price_cents=line.contracted_price_cents,
            billing_interval=line.billing_interval,
            entitlement_overrides=line.entitlement_overrides,
            effective_from=line.effective_from,
            effective_until=line.effective_until,
        )
        for line in contract.lines
        if line.status == CommercialContractLineStatus.active
    ]


async def _reconciliation_item(
    db: AsyncSession, *, intent: CommercialCheckoutIntent, provider: CommercialProvider
) -> None:
    key = f"commercial_checkout_reconcile:{intent.id}"
    if await db.scalar(select(PlatformWorkItem.id).where(PlatformWorkItem.dedupe_key == key)):
        return
    now = datetime.now(UTC)
    db.add(PlatformWorkItem(
        source_type="commercial_checkout_reconcile",
        source_id=intent.id,
        source_version=str(intent.version),
        dedupe_key=key,
        kind="alert",
        state="open",
        priority="P1",
        workspace_id=intent.workspace_id,
        safe_title="Pagamento confirmado requer reconciliação",
        safe_summary=f"O contrato mudou após abrir o checkout {provider.value}.",
        href=f"/blackbox/workspaces?tab=commercial&workspaceId={intent.workspace_id}",
        required_module="commercial_contracts",
        required_action="manage",
        allowed_commands=[],
        opened_at=now,
        source_updated_at=now,
        projected_at=now,
        projection_version=1,
        payload_safe={"provider": provider.value},
    ))


async def activate_checkout(
    db: AsyncSession,
    *,
    provider: CommercialProvider,
    event_id: str,
    event_created: int,
    external_reference: object,
    provider_session_id: str,
    provider_subscription_id: str,
    provider_customer_id: str | None,
    provider_status: str,
) -> CommercialContractProviderBinding:
    intent_id, workspace_id = parse_checkout_reference(external_reference)
    await set_workspace_context(db, workspace_id)
    await set_commercial_provider_reconcile_context(db)
    intent = await db.scalar(
        select(CommercialCheckoutIntent)
        .where(
            CommercialCheckoutIntent.id == intent_id,
            CommercialCheckoutIntent.workspace_id == workspace_id,
            CommercialCheckoutIntent.provider == provider,
        )
        .with_for_update()
    )
    if intent is None or intent.provider_session_id != provider_session_id:
        raise CommercialProviderReconciliationError(
            "Checkout não corresponde à intenção comercial persistida."
        )
    binding = await db.scalar(
        select(CommercialContractProviderBinding)
        .where(
            CommercialContractProviderBinding.contract_id == intent.contract_id,
            CommercialContractProviderBinding.provider == provider,
        )
        .with_for_update()
    )
    if binding is None:
        binding = CommercialContractProviderBinding(
            workspace_id=workspace_id,
            contract_id=intent.contract_id,
            provider=provider,
            version=1,
        )
        db.add(binding)
    elif binding.last_event_id == event_id:
        return binding
    else:
        binding.version += 1
    binding.provider_customer_id = provider_customer_id
    binding.provider_subscription_id = provider_subscription_id
    binding.provider_status = provider_status
    binding.last_event_id = event_id
    binding.last_event_created = event_created
    if intent.status == CommercialCheckoutIntentStatus.completed:
        await db.flush()
        return binding
    contract = await CommercialCatalogService(db).get_workspace_contract(workspace_id)
    if contract.version != intent.expected_contract_version:
        intent.status = CommercialCheckoutIntentStatus.reconciliation_required
        intent.version += 1
        await _reconciliation_item(db, intent=intent, provider=provider)
        await db.flush()
        return binding
    await CommercialCatalogService(db).revise_contract(
        contract_id=contract.id,
        expected_version=contract.version,
        payload=WorkspaceContractRevisionRequest(
            lines=[WorkspaceContractLineWrite.model_validate(item) for item in intent.target_lines],
            reason=intent.reason,
        ),
        actor_user_id=None,
        idempotency_key=f"{provider.value}:{event_id}",
        operation=f"{provider.value}_checkout_activate_contract",
        event_type=f"{provider.value}_checkout_completed",
        preserve_exceptions=True,
        target_status=CommercialContractStatus.active,
    )
    intent.status = CommercialCheckoutIntentStatus.completed
    intent.checkout_url = None
    intent.version += 1
    await db.flush()
    return binding


async def reconcile_subscription_status(
    db: AsyncSession,
    *,
    provider: CommercialProvider,
    event_id: str,
    event_created: int,
    provider_subscription_id: str,
    provider_status: str,
    target_status: CommercialContractStatus | None,
) -> CommercialContractProviderBinding | None:
    await set_commercial_provider_reconcile_context(db)
    binding = await db.scalar(
        select(CommercialContractProviderBinding)
        .where(
            CommercialContractProviderBinding.provider == provider,
            CommercialContractProviderBinding.provider_subscription_id == provider_subscription_id,
        )
        .with_for_update()
    )
    if binding is None:
        return None
    if binding.last_event_id == event_id or (
        binding.last_event_created is not None and event_created < binding.last_event_created
    ):
        return binding
    await set_workspace_context(db, binding.workspace_id)
    await set_commercial_provider_reconcile_context(db)
    contract = await CommercialCatalogService(db).get_workspace_contract(binding.workspace_id)
    if target_status is not None and contract.status != target_status:
        await CommercialCatalogService(db).revise_contract(
            contract_id=contract.id,
            expected_version=contract.version,
            payload=WorkspaceContractRevisionRequest(
                lines=_active_lines(contract),
                reason=f"Reconciliação {provider.value}: {provider_status}",
            ),
            actor_user_id=None,
            idempotency_key=f"{provider.value}:{event_id}",
            operation=f"{provider.value}_reconcile_contract",
            event_type=f"{provider.value}_contract_reconciled",
            preserve_exceptions=True,
            target_status=target_status,
        )
    binding.provider_status = provider_status
    binding.last_event_id = event_id
    binding.last_event_created = event_created
    binding.version += 1
    await db.flush()
    return binding


async def mark_checkout_terminal(
    db: AsyncSession,
    *,
    provider: CommercialProvider,
    external_reference: object,
    provider_session_id: str,
    status: CommercialCheckoutIntentStatus,
    failure_code: str,
) -> None:
    intent_id, workspace_id = parse_checkout_reference(external_reference)
    await set_workspace_context(db, workspace_id)
    await set_commercial_provider_reconcile_context(db)
    intent = await db.scalar(
        select(CommercialCheckoutIntent)
        .where(
            CommercialCheckoutIntent.id == intent_id,
            CommercialCheckoutIntent.workspace_id == workspace_id,
            CommercialCheckoutIntent.provider == provider,
        )
        .with_for_update()
    )
    if intent is None or intent.provider_session_id != provider_session_id:
        raise CommercialProviderReconciliationError(
            "Checkout terminal não corresponde à intenção persistida."
        )
    if intent.status in {
        CommercialCheckoutIntentStatus.completed,
        CommercialCheckoutIntentStatus.reconciliation_required,
    }:
        return
    if intent.status == status and intent.failure_code == failure_code:
        return
    intent.status = status
    intent.failure_code = failure_code
    intent.checkout_url = None
    intent.version += 1
    await db.flush()
