"""Public and authenticated commercial self-service endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.factory import (
    StripeCredentialError,
    get_saas_billing_gateway,
)
from app.billing.port import SaaSBillingGatewayError
from app.billing.stripe_adapter import StripeAdapterError, StripeNotAllowedError
from app.core.config import settings
from app.core.deps import (
    AuthContext,
    get_current_auth_context,
    get_current_workspace_id,
    get_db,
    require_workspace_admin,
)
from app.core.platform_access import require_platform_permission
from app.core.platform_audit import record_canonical_platform_mutation
from app.core.platform_rbac import is_platform_root
from app.db.tenant import WorkspaceContextError, set_workspace_context
from app.models.commercial_catalog import (
    CommercialCheckoutIntent,
    CommercialCheckoutIntentStatus,
    CommercialProvider,
)
from app.models.platform_work_item import PlatformWorkItem
from app.models.user import User
from app.schemas.commercial_catalog import (
    WorkspaceContractLineWrite,
    WorkspaceContractRevisionPreviewRequest,
)
from app.schemas.commercial_self_service import (
    CommercialAccountPreviewRead,
    CommercialAccountRequestCreate,
    CommercialAccountRequestExecute,
    CommercialAccountRequestPage,
    CommercialAccountRequestRead,
    CommercialAccountRequestResolve,
    CommercialAccountSummaryRead,
    CommercialBillingPortalRead,
    CommercialCheckoutSessionCreate,
    CommercialCheckoutSessionRead,
    CommercialProviderPriceBindingPage,
    CommercialProviderPriceBindingRead,
    CommercialProviderPriceBindingWrite,
    PublicCommercialCatalogRead,
)
from app.services.commercial_catalog_service import CommercialContractNotFoundError
from app.services.commercial_self_service import (
    CommercialSelfService,
    CommercialSelfServiceError,
    CommercialSelfServiceNotFoundError,
    present_request,
)
from app.services.commercial_stripe_bridge import (
    CommercialStripeBridge,
    CommercialStripeBridgeError,
)

public_router = APIRouter(prefix="/commercial", tags=["Commercial Public"])
workspace_router = APIRouter(prefix="/commercial/account", tags=["Commercial Account"])
platform_router = APIRouter(
    prefix="/platform/commercial/account-requests",
    tags=["Platform Commercial Requests"],
)


def _error(error: Exception) -> HTTPException | None:
    if isinstance(error, CommercialSelfServiceError):
        return HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error)},
        )
    if isinstance(error, CommercialContractNotFoundError):
        return HTTPException(
            status_code=404,
            detail={"code": "COMMERCIAL-CONTRACT-NOT-FOUND", "message": str(error)},
        )
    if isinstance(error, CommercialStripeBridgeError):
        return HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error)},
        )
    if isinstance(
        error,
        (StripeCredentialError, StripeNotAllowedError, StripeAdapterError, SaaSBillingGatewayError),
    ):
        return HTTPException(
            status_code=503,
            detail={
                "code": "COMMERCIAL-PROVIDER-UNAVAILABLE",
                "message": "O checkout seguro está temporariamente indisponível.",
            },
        )
    return None


def _raise_known(error: Exception) -> None:
    mapped = _error(error)
    if mapped is not None:
        raise mapped from error
    raise error


async def _bind_workspace(db: AsyncSession, workspace_id: UUID) -> None:
    try:
        await set_workspace_context(db, workspace_id)
    except WorkspaceContextError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "COMMERCIAL-WORKSPACE-CONTEXT-CONFLICT",
                "message": "A requisição já está vinculada a outra conta.",
            },
        ) from error


@public_router.get("/offers", response_model=PublicCommercialCatalogRead)
async def public_commercial_offers(
    db: AsyncSession = Depends(get_db),
) -> PublicCommercialCatalogRead:
    return await CommercialSelfService(db).public_catalog()


@workspace_router.get("", response_model=CommercialAccountSummaryRead)
async def get_commercial_account(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> CommercialAccountSummaryRead:
    service = CommercialSelfService(db)
    try:
        contract = await service.catalog.get_workspace_contract(workspace_id)
        requests, _ = await service.list_requests(workspace_id)
        states = {
            provider: await CommercialStripeBridge(db, provider).provider_state()
            for provider in CommercialProvider
        }
        contract_binding = await CommercialStripeBridge(db).contract_binding(
            contract.id, any_provider=True
        )
    except Exception as error:
        _raise_known(error)
        raise
    return CommercialAccountSummaryRead(
        contract=contract,
        requests=[present_request(item) for item in requests],
        automatic_checkout_available=any(item.available for item in states.values()),
        billing_portal_available=bool(
            contract_binding is not None
            and contract_binding.provider == CommercialProvider.stripe
            and contract_binding.provider_customer_id
        ),
        provider_subscription_linked=bool(
            contract_binding is not None and contract_binding.provider_subscription_id
        ),
        provider_subscription_status=(
            contract_binding.provider_status if contract_binding is not None else None
        ),
        provider_state=(
            states[contract_binding.provider].state
            if contract_binding is not None
            else (
                "configured"
                if any(item.available for item in states.values())
                else "not_configured"
            )
        ),
        provider_message=(
            states[contract_binding.provider].message
            if contract_binding is not None
            else "Escolha um dos meios de pagamento configurados para continuar."
            if any(item.available for item in states.values())
            else "Nenhum provedor de assinatura está integralmente configurado neste ambiente."
        ),
        active_provider=contract_binding.provider.value if contract_binding is not None else None,
        available_providers=[
            provider.value for provider, state in states.items() if state.available
        ],
    )


@workspace_router.post(
    "/checkout-sessions",
    response_model=CommercialCheckoutSessionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_canonical_checkout_session(
    payload: CommercialCheckoutSessionCreate,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=200),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> CommercialCheckoutSessionRead:
    if payload.provider:
        selected_provider = CommercialProvider(payload.provider)
    else:
        selected_provider = next(
            (
                provider
                for provider in CommercialProvider
                if (await CommercialStripeBridge(db, provider).provider_state()).available
            ),
            None,
        )
        if selected_provider is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "COMMERCIAL-PROVIDER-UNAVAILABLE",
                    "message": "Nenhum provedor de assinatura está disponível neste ambiente.",
                },
            )
    bridge = CommercialStripeBridge(db, selected_provider)
    try:
        provider = await bridge.provider_state()
        if not provider.available:
            raise HTTPException(
                status_code=503,
                detail={"code": "COMMERCIAL-PROVIDER-UNAVAILABLE", "message": provider.message},
            )
        gateway = get_saas_billing_gateway(selected_provider)
        intent, price_binding, replay = await bridge.prepare_checkout(
            workspace_id=workspace_id,
            actor_user_id=auth.user_id,
            target_sku=payload.target_sku,
            expected_contract_version=payload.expected_contract_version,
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
        if replay and intent.status == CommercialCheckoutIntentStatus.session_created:
            if not intent.checkout_url:
                raise CommercialStripeBridgeError("Sessão de checkout sem URL segura.")
            return CommercialCheckoutSessionRead(
                intent_id=intent.id,
                status="session_created",
                checkout_url=intent.checkout_url,
                provider=intent.provider.value,
            )
        await db.commit()

        user = await db.get(User, auth.user_id)
        _, offer_version = await bridge.latest_offer(payload.target_sku)
        checkout_preview = await bridge.catalog.preview_contract_revision(
            intent.contract_id,
            WorkspaceContractRevisionPreviewRequest(
                expected_version=intent.expected_contract_version,
                lines=[
                    WorkspaceContractLineWrite.model_validate(line)
                    for line in intent.target_lines
                ],
            ),
            preserve_exceptions=True,
        )
        recurring_amount_cents = checkout_preview.price.recurring_price_cents
        if recurring_amount_cents <= 0:
            raise CommercialStripeBridgeError(
                "Checkout externo exige valor recorrente efetivo positivo."
            )
        result = await gateway.create_checkout_session(
            workspace_id=workspace_id,
            provider_price_id=price_binding.provider_price_id,
            amount_cents=recurring_amount_cents,
            offer_name=offer_version.name,
            success_url=f"{settings.FRONTEND_URL.rstrip('/')}/obrigado?checkout=success",
            cancel_url=f"{settings.FRONTEND_URL.rstrip('/')}/app/configuracoes?tab=billing&checkout=cancelled",
            notification_url=(
                f"{settings.PUBLIC_API_URL.strip().rstrip('/')}/api/v1/webhooks/saas/mercado-pago"
                "?source_news=webhooks"
            ),
            customer_email=user.email if user is not None else None,
            metadata={
                "commercial_checkout_intent_id": str(intent.id),
                "commercial_contract_id": str(intent.contract_id),
                "commercial_offer_version_id": str(intent.target_offer_version_id),
                "workspace_id": str(workspace_id),
            },
            idempotency_key=f"canonical_checkout_{intent.id}",
        )
        if not result.session_id or not result.url.startswith("https://"):
            raise SaaSBillingGatewayError(
                "O provedor não devolveu uma sessão hospedada válida."
            )

        await _bind_workspace(db, workspace_id)
        current = await db.scalar(
            select(CommercialCheckoutIntent)
            .where(CommercialCheckoutIntent.id == intent.id)
            .with_for_update()
        )
        if current is None:
            raise CommercialStripeBridgeError("Intenção de checkout não encontrada.")
        current.provider_session_id = result.session_id
        current.checkout_url = result.url
        current.status = CommercialCheckoutIntentStatus.session_created
        current.version += 1
        await db.commit()
        return CommercialCheckoutSessionRead(
            intent_id=current.id,
            status="session_created",
            checkout_url=current.checkout_url,
            provider=current.provider.value,
        )
    except HTTPException:
        await db.rollback()
        raise
    except Exception as error:
        await db.rollback()
        try:
            await _bind_workspace(db, workspace_id)
            failed = await db.scalar(
                select(CommercialCheckoutIntent).where(
                    CommercialCheckoutIntent.workspace_id == workspace_id,
                    CommercialCheckoutIntent.idempotency_key == idempotency_key,
                )
            )
            if failed is not None and failed.status == CommercialCheckoutIntentStatus.prepared:
                failed.status = CommercialCheckoutIntentStatus.failed
                failed.failure_code = "provider_unavailable"
                failed.version += 1
                await db.commit()
        except Exception:
            await db.rollback()
        _raise_known(error)
        raise


@workspace_router.post("/billing-portal", response_model=CommercialBillingPortalRead)
async def create_canonical_billing_portal(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> CommercialBillingPortalRead:
    try:
        contract = await CommercialStripeBridge(db).catalog.get_workspace_contract(workspace_id)
        binding = await CommercialStripeBridge(db).contract_binding(
            contract.id, any_provider=True
        )
        if binding is None or not binding.provider_customer_id:
            raise CommercialStripeBridgeError(
                "A conta ainda não possui cliente de cobrança vinculado."
            )
        gateway = get_saas_billing_gateway(binding.provider)
        if not gateway.supports_customer_portal:
            raise CommercialStripeBridgeError(
                "Este provedor não possui portal hospedado. "
                "Use a administração de assinatura no Meu Executivo."
            )
        result = await gateway.create_billing_portal_session(
            customer_id=binding.provider_customer_id,
            return_url=f"{settings.FRONTEND_URL.rstrip('/')}/app/configuracoes?tab=billing",
        )
        if not result.url.startswith("https://"):
            raise StripeAdapterError("Stripe não devolveu uma URL segura para o portal.")
        return CommercialBillingPortalRead(portal_url=result.url)
    except Exception as error:
        _raise_known(error)
        raise


@workspace_router.post("/preview", response_model=CommercialAccountPreviewRead)
async def preview_commercial_account_request(
    payload: CommercialAccountRequestCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> CommercialAccountPreviewRead:
    try:
        return await CommercialSelfService(db).preview(workspace_id, payload)
    except Exception as error:
        _raise_known(error)
        raise


@workspace_router.post("/requests", response_model=CommercialAccountRequestRead, status_code=201)
async def create_commercial_account_request(
    payload: CommercialAccountRequestCreate,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_workspace_admin),
    db: AsyncSession = Depends(get_db),
) -> CommercialAccountRequestRead:
    try:
        obj, replay = await CommercialSelfService(db).create_request(
            workspace_id=workspace_id,
            actor_user_id=auth.user_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        await db.commit()
    except Exception as error:
        await db.rollback()
        _raise_known(error)
        raise
    response = present_request(obj)
    if replay:
        # Response schema remains stable; clients can safely present the same request.
        return response
    return response


_platform_view = require_platform_permission("commercial_contracts", "view")
_platform_manage = require_platform_permission("commercial_contracts", "manage")


def _present_price_binding(row) -> CommercialProviderPriceBindingRead:
    binding, sku, offer_version = row
    return CommercialProviderPriceBindingRead(
        id=binding.id,
        sku=sku,
        offer_version=offer_version,
        provider=binding.provider.value,
        billing_interval=binding.billing_interval.value,
        provider_price_id=binding.provider_price_id,
        status=binding.status.value,
        version=binding.version,
        updated_at=binding.updated_at,
    )


@platform_router.get("/provider-price-bindings", response_model=CommercialProviderPriceBindingPage)
async def list_commercial_provider_price_bindings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_platform_view),
) -> CommercialProviderPriceBindingPage:
    rows = await CommercialStripeBridge(db).list_price_bindings()
    return CommercialProviderPriceBindingPage(items=[_present_price_binding(row) for row in rows])


@platform_router.post(
    "/provider-price-bindings",
    response_model=CommercialProviderPriceBindingRead,
    status_code=status.HTTP_201_CREATED,
)
async def configure_commercial_provider_price_binding(
    payload: CommercialProviderPriceBindingWrite,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_platform_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> CommercialProviderPriceBindingRead:
    if not is_platform_root(actor):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "COMMERCIAL-PROVIDER-ROOT-REQUIRED",
                "message": "Somente o Super Admin configura preços do provedor.",
            },
        )
    try:
        bridge = CommercialStripeBridge(db, CommercialProvider(payload.provider))
        binding = await bridge.configure_price_binding(
            sku=payload.sku,
            provider_price_id=payload.provider_price_id,
            actor_user_id=actor.id,
            reason=payload.reason,
        )
        row = next(row for row in await bridge.list_price_bindings() if row[0].id == binding.id)
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=("commercial_provider_price_binding", str(binding.id), None),
            after_snapshot=(
                "commercial_provider_price_binding",
                str(binding.id),
                {
                    "sku": payload.sku,
                    "provider": payload.provider,
                    "status": binding.status.value,
                    "providerPriceConfigured": True,
                },
            ),
            module="commercial_contracts",
            action="provider_price_binding.configure",
            reason=payload.reason,
            reason_source="user",
        )
        await db.commit()
        return _present_price_binding(row)
    except Exception as error:
        await db.rollback()
        _raise_known(error)
        raise


@platform_router.get("", response_model=CommercialAccountRequestPage)
async def list_commercial_account_requests(
    workspace_id: UUID = Query(alias="workspaceId"),
    status: str | None = Query(default=None, pattern=r"^(pending|resolved|rejected|cancelled)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_platform_view),
) -> CommercialAccountRequestPage:
    await _bind_workspace(db, workspace_id)
    items, total = await CommercialSelfService(db).list_requests(
        workspace_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return CommercialAccountRequestPage(
        items=[present_request(item) for item in items], total=total
    )


@platform_router.post("/{request_id}/resolve", response_model=CommercialAccountRequestRead)
async def resolve_commercial_account_request(
    request_id: UUID,
    payload: CommercialAccountRequestResolve,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_platform_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> CommercialAccountRequestRead:
    service = CommercialSelfService(db)
    try:
        work_item = await db.scalar(
            select(PlatformWorkItem).where(
                PlatformWorkItem.source_type == "commercial_request",
                PlatformWorkItem.source_id == request_id,
            )
        )
        if work_item is None or work_item.workspace_id is None:
            raise CommercialSelfServiceNotFoundError("Solicitação comercial não encontrada.")
        await _bind_workspace(db, work_item.workspace_id)
        obj = await service.resolve(
            request_id=request_id,
            actor_user_id=actor.id,
            expected_version=payload.expected_version,
            status=payload.status,
            note=payload.note,
        )
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=(
                "commercial_account_request",
                str(obj.id),
                {"status": "pending", "version": payload.expected_version},
            ),
            after_snapshot=(
                "commercial_account_request",
                str(obj.id),
                {"status": obj.status, "version": obj.version},
            ),
            module="commercial_contracts",
            action="commercial_request.resolve",
            reason=payload.note,
            reason_source="user",
        )
        await db.commit()
    except Exception as error:
        await db.rollback()
        _raise_known(error)
        raise
    return present_request(obj)


@platform_router.post(
    "/{request_id}/execute-provider-change",
    response_model=CommercialAccountRequestRead,
)
async def execute_commercial_provider_change(
    request_id: UUID,
    payload: CommercialAccountRequestExecute,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_platform_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> CommercialAccountRequestRead:
    if not is_platform_root(actor):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "COMMERCIAL-PROVIDER-ROOT-REQUIRED",
                "message": "Somente o Super Admin executa alterações no provedor.",
            },
        )
    try:
        work_item = await db.scalar(
            select(PlatformWorkItem).where(
                PlatformWorkItem.source_type == "commercial_request",
                PlatformWorkItem.source_id == request_id,
            )
        )
        if work_item is None or work_item.workspace_id is None:
            raise CommercialSelfServiceNotFoundError("Solicitação comercial não encontrada.")
        await _bind_workspace(db, work_item.workspace_id)
        obj = await CommercialSelfService(db).execute_provider_change(
            request_id=request_id,
            actor_user_id=actor.id,
            expected_version=payload.expected_version,
            note=payload.note,
        )
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=(
                "commercial_account_request",
                str(obj.id),
                {"status": "pending", "version": payload.expected_version},
            ),
            after_snapshot=(
                "commercial_account_request",
                str(obj.id),
                {"status": obj.status, "version": obj.version, "providerExecuted": True},
            ),
            module="commercial_contracts",
            action="commercial_request.execute_provider_change",
            reason=payload.note,
            reason_source="user",
        )
        await db.commit()
    except Exception as error:
        await db.rollback()
        _raise_known(error)
        raise
    return present_request(obj)
