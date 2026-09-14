"""Blackbox API for the canonical, provider-agnostic commercial catalog."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.factory import (
    StripeCredentialError,
    get_saas_billing_gateway,
)
from app.billing.port import SaaSBillingGatewayError
from app.billing.stripe_adapter import StripeAdapterError, StripeNotAllowedError
from app.core.config import settings
from app.core.deps import get_db
from app.core.platform_access import require_platform_permission, require_platform_root
from app.db.tenant import WorkspaceContextError, set_workspace_context
from app.models.commercial_catalog import CommercialProvider
from app.models.user import User
from app.schemas.commercial_catalog import (
    CommercialAdjustmentCommandRead,
    CommercialAdjustmentCreateRequest,
    CommercialAdjustmentRead,
    CommercialAdjustmentUpdateRequest,
    CommercialCatalogBootstrapRead,
    CommercialOfferCommandRead,
    CommercialOfferDraftCommandRead,
    CommercialOfferDraftPreviewRead,
    CommercialOfferDraftPublishRequest,
    CommercialOfferDraftRead,
    CommercialOfferDraftSaveRequest,
    CommercialOfferPageRead,
    CommercialOfferPublishRequest,
    CommercialOfferRestorePreviewRead,
    CommercialOfferRestorePreviewRequest,
    CommercialOfferRestoreRequest,
    CommercialOfferRevisionHistoryRead,
    CommercialRecordRevokeRequest,
    EffectiveCapabilityManifestRead,
    EntitlementBonusCommandRead,
    EntitlementBonusCreateRequest,
    EntitlementBonusRead,
    EntitlementBonusUpdateRequest,
    WorkspaceContractCreateRequest,
    WorkspaceContractHistoryRead,
    WorkspaceContractPageRead,
    WorkspaceContractPreviewRead,
    WorkspaceContractPreviewRequest,
    WorkspaceContractRead,
    WorkspaceContractRestorePreviewRead,
    WorkspaceContractRestorePreviewRequest,
    WorkspaceContractRestoreRequest,
    WorkspaceContractRevisionPreviewRead,
    WorkspaceContractRevisionPreviewRequest,
    WorkspaceContractRevisionRequest,
)
from app.schemas.commercial_self_service import (
    CommercialBillingPortalRead,
    CommercialWorkspaceProviderStateRead,
)
from app.services.commercial_catalog_service import (
    CommercialCatalogConflictError,
    CommercialCatalogNotFoundError,
    CommercialCatalogService,
    CommercialCatalogValidationError,
)
from app.services.commercial_legacy_bridge_service import (
    CanonicalContractCommand,
    CommercialBridgeError,
)
from app.services.commercial_stripe_bridge import (
    CommercialStripeBridge,
    CommercialStripeBridgeError,
)

router = APIRouter(
    prefix="/platform/commercial",
    tags=["Platform Commercial Catalog"],
)

_plans_view = require_platform_permission("plans", "view")
_plans_draft = require_platform_permission("plans", "draft")
_plans_preview = require_platform_permission("plans", "preview")
_contracts_view = require_platform_permission("commercial_contracts", "view")
_contracts_manage = require_platform_permission("commercial_contracts", "manage")
_adjustments_view = require_platform_permission("commercial_adjustments", "view")
_adjustments_manage = require_platform_permission("commercial_adjustments", "manage")
_bonuses_view = require_platform_permission("entitlement_bonuses", "view")
_bonuses_manage = require_platform_permission("entitlement_bonuses", "manage")


def _raise_commercial_error(error: Exception) -> None:
    if isinstance(error, CommercialCatalogNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "COMMERCIAL-NOT-FOUND", "message": str(error)},
        ) from error
    if isinstance(error, (CommercialCatalogConflictError, CommercialBridgeError)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "COMMERCIAL-CONFLICT", "message": str(error)},
        ) from error
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "COMMERCIAL-INVALID", "message": str(error)},
    ) from error


def _raise_provider_error(error: Exception) -> None:
    if isinstance(error, CommercialStripeBridgeError):
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error)},
        ) from error
    if isinstance(
        error,
        (
            StripeCredentialError,
            StripeNotAllowedError,
            StripeAdapterError,
            SaaSBillingGatewayError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "COMMERCIAL-PROVIDER-UNAVAILABLE",
                "message": "A administração do provedor está temporariamente indisponível.",
            },
        ) from error
    raise error


def _required_version(if_match: str | None) -> int:
    if if_match is None:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail={
                "code": "COMMERCIAL-IF-MATCH-REQUIRED",
                "message": "Envie If-Match com a versão exibida na prévia.",
            },
        )
    normalized = if_match.strip()
    if normalized.startswith("W/"):
        normalized = normalized[2:].strip()
    normalized = normalized.strip('"')
    try:
        version = int(normalized)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "COMMERCIAL-INVALID-IF-MATCH", "message": "ETag inválido."},
        ) from error
    if version < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "COMMERCIAL-INVALID-IF-MATCH", "message": "ETag inválido."},
        )
    return version


def _optional_draft_version(if_match: str | None) -> int | None:
    if if_match is None:
        return None
    normalized = if_match.strip()
    if normalized.startswith("W/"):
        normalized = normalized[2:].strip()
    normalized = normalized.strip('"')
    try:
        version = int(normalized)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "COMMERCIAL-INVALID-IF-MATCH", "message": "ETag inválido."},
        ) from error
    if version < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "COMMERCIAL-INVALID-IF-MATCH", "message": "ETag inválido."},
        )
    return version


async def _bind_single_workspace_context(
    db: AsyncSession,
    workspace_id: UUID,
) -> None:
    """Bind one Account 360 to tenant RLS without granting wider access.

    The platform permission dependency has already authorized and attributed
    the request.  This second, narrower context is needed only because the
    effective manifest reads tenant-owned overrides and monthly usage.  A
    session that is already bound to another account is rejected instead of
    returning a partial manifest.
    """

    try:
        await set_workspace_context(db, workspace_id)
    except WorkspaceContextError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "COMMERCIAL-WORKSPACE-CONTEXT-CONFLICT",
                "message": (
                    "A sessão comercial já está vinculada a outra conta. "
                    "Abra uma nova requisição para continuar."
                ),
            },
        ) from error


@router.get(
    "/offers",
    response_model=CommercialOfferPageRead,
    summary="Listar a revisão mais recente de cada oferta comercial",
)
async def list_commercial_offers(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    public_only: bool = Query(default=False, alias="publicOnly"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_plans_view),
) -> CommercialOfferPageRead:
    items, total = await CommercialCatalogService(db).list_offers(
        limit=limit,
        offset=offset,
        public_only=public_only,
    )
    return CommercialOfferPageRead(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
    )


@router.post(
    "/offers/bootstrap",
    response_model=CommercialCatalogBootstrapRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar o catálogo inicial aprovado sem reprificar contratos",
)
async def bootstrap_commercial_offers(
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_root),
) -> CommercialCatalogBootstrapRead:
    try:
        items, replay = await CommercialCatalogService(db).bootstrap_default_catalog(
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
        )
        return CommercialCatalogBootstrapRead(items=items, idempotent_replay=replay)
    except (CommercialCatalogConflictError, CommercialCatalogValidationError) as error:
        _raise_commercial_error(error)


@router.post(
    "/offers/publish",
    response_model=CommercialOfferCommandRead,
    status_code=status.HTTP_201_CREATED,
    summary="Publicar nova revisão imutável de uma oferta",
)
async def publish_commercial_offer(
    payload: CommercialOfferPublishRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_root),
) -> CommercialOfferCommandRead:
    try:
        offer, replay = await CommercialCatalogService(db).publish_offer(
            payload=payload,
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
        )
        return CommercialOfferCommandRead(offer=offer, idempotent_replay=replay)
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
        CommercialBridgeError,
    ) as error:
        _raise_commercial_error(error)


@router.get(
    "/offers/{sku}/history",
    response_model=CommercialOfferRevisionHistoryRead,
    summary="Consultar histórico imutável e diff de uma oferta",
)
async def get_commercial_offer_history(
    sku: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_plans_view),
) -> CommercialOfferRevisionHistoryRead:
    try:
        return await CommercialCatalogService(db).offer_revision_history(sku)
    except CommercialCatalogNotFoundError as error:
        _raise_commercial_error(error)


@router.get(
    "/offers/{sku}/draft",
    response_model=CommercialOfferDraftRead,
    summary="Consultar o rascunho persistido de uma oferta",
)
async def get_commercial_offer_draft(
    sku: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_plans_draft),
) -> CommercialOfferDraftRead:
    try:
        draft = await CommercialCatalogService(db).get_offer_draft(sku)
        response.headers["ETag"] = f'"{draft.draft_version}"'
        return draft
    except CommercialCatalogNotFoundError as error:
        _raise_commercial_error(error)


@router.put(
    "/offers/{sku}/draft",
    response_model=CommercialOfferDraftCommandRead,
    summary="Salvar rascunho versionado sem alterar enforcement",
)
async def save_commercial_offer_draft(
    sku: str,
    payload: CommercialOfferDraftSaveRequest,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_plans_draft),
) -> CommercialOfferDraftCommandRead:
    try:
        draft, replay = await CommercialCatalogService(db).save_offer_draft(
            sku=sku,
            payload=payload,
            expected_draft_version=_optional_draft_version(if_match),
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
        )
        response.headers["ETag"] = f'"{draft.draft_version}"'
        return CommercialOfferDraftCommandRead(draft=draft, idempotent_replay=replay)
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
    ) as error:
        _raise_commercial_error(error)


@router.post(
    "/offers/{sku}/draft/discard",
    response_model=CommercialOfferDraftCommandRead,
    summary="Descartar rascunho sem apagar sua evidência histórica",
)
async def discard_commercial_offer_draft(
    sku: str,
    payload: CommercialOfferDraftPublishRequest,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_plans_draft),
) -> CommercialOfferDraftCommandRead:
    try:
        draft, replay = await CommercialCatalogService(db).discard_offer_draft(
            sku=sku,
            expected_draft_version=_required_version(if_match),
            reason=payload.reason,
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
        )
        response.headers["ETag"] = f'"{draft.draft_version}"'
        return CommercialOfferDraftCommandRead(draft=draft, idempotent_replay=replay)
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
    ) as error:
        _raise_commercial_error(error)


@router.post(
    "/offers/{sku}/draft/preview",
    response_model=CommercialOfferDraftPreviewRead,
    summary="Gerar diff e impacto do rascunho persistido",
)
async def preview_commercial_offer_draft(
    sku: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_plans_preview),
) -> CommercialOfferDraftPreviewRead:
    try:
        return await CommercialCatalogService(db).preview_offer_draft(sku)
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
    ) as error:
        _raise_commercial_error(error)


@router.post(
    "/offers/{sku}/draft/publish",
    response_model=CommercialOfferCommandRead,
    summary="Publicar um rascunho como nova revisão imutável",
)
async def publish_commercial_offer_draft(
    sku: str,
    payload: CommercialOfferDraftPublishRequest,
    if_match: str | None = Header(default=None, alias="If-Match"),
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_root),
) -> CommercialOfferCommandRead:
    try:
        offer, replay = await CommercialCatalogService(db).publish_offer_draft(
            sku=sku,
            expected_draft_version=_required_version(if_match),
            payload=payload,
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
        )
        return CommercialOfferCommandRead(offer=offer, idempotent_replay=replay)
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
    ) as error:
        _raise_commercial_error(error)


@router.post(
    "/offers/{sku}/restore-preview",
    response_model=CommercialOfferRestorePreviewRead,
    summary="Pré-visualizar restauração de uma revisão publicada",
)
async def preview_commercial_offer_restore(
    sku: str,
    payload: CommercialOfferRestorePreviewRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_root),
) -> CommercialOfferRestorePreviewRead:
    try:
        return await CommercialCatalogService(db).preview_offer_restore(
            sku=sku,
            target_version=payload.target_version,
            expected_current_version=payload.expected_current_version,
        )
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
    ) as error:
        _raise_commercial_error(error)


@router.post(
    "/offers/{sku}/restore",
    response_model=CommercialOfferCommandRead,
    summary="Restaurar oferta como nova revisão, sem reprificar contratos",
)
async def restore_commercial_offer(
    sku: str,
    payload: CommercialOfferRestoreRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_root),
) -> CommercialOfferCommandRead:
    try:
        offer, replay = await CommercialCatalogService(db).restore_offer(
            sku=sku,
            payload=payload,
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
        )
        return CommercialOfferCommandRead(offer=offer, idempotent_replay=replay)
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
    ) as error:
        _raise_commercial_error(error)


@router.post(
    "/contracts/preview",
    response_model=WorkspaceContractPreviewRead,
    summary="Pré-visualizar preço e capacidades antes de contratar",
)
async def preview_commercial_contract(
    payload: WorkspaceContractPreviewRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_contracts_manage),
) -> WorkspaceContractPreviewRead:
    try:
        await _bind_single_workspace_context(db, payload.workspace_id)
        return await CommercialCatalogService(db).preview_contract(payload)
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
    ) as error:
        _raise_commercial_error(error)


@router.post(
    "/contracts",
    response_model=WorkspaceContractRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar contrato multiproduto idempotente",
)
async def create_commercial_contract(
    payload: WorkspaceContractCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_contracts_manage),
) -> WorkspaceContractRead:
    try:
        await _bind_single_workspace_context(db, payload.workspace_id)
        contract, _ = await CanonicalContractCommand(
            db,
            control_plane=True,
        ).create_contract(
            payload=payload,
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
        )
        return contract
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
    ) as error:
        _raise_commercial_error(error)


@router.post(
    "/contracts/legacy-preview",
    response_model=WorkspaceContractPreviewRead,
    summary="Pré-visualizar importação anual legada reservada ao titular",
)
async def preview_legacy_commercial_contract(
    payload: WorkspaceContractPreviewRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_root),
) -> WorkspaceContractPreviewRead:
    try:
        await _bind_single_workspace_context(db, payload.workspace_id)
        return await CommercialCatalogService(db).preview_contract(
            payload,
            allow_legacy_import=True,
        )
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
    ) as error:
        _raise_commercial_error(error)


@router.post(
    "/contracts/legacy-import",
    response_model=WorkspaceContractRead,
    status_code=status.HTTP_201_CREATED,
    summary="Importar contrato anual legado sem oferecê-lo em novas vendas",
)
async def import_legacy_commercial_contract(
    payload: WorkspaceContractCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_root),
) -> WorkspaceContractRead:
    try:
        await _bind_single_workspace_context(db, payload.workspace_id)
        contract, _ = await CanonicalContractCommand(
            db,
            control_plane=True,
        ).create_contract(
            payload=payload,
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
            allow_legacy_import=True,
        )
        return contract
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
    ) as error:
        _raise_commercial_error(error)


@router.get(
    "/contracts",
    response_model=WorkspaceContractPageRead,
    summary="Listar contratos canônicos com paginação real",
)
async def list_commercial_contracts(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_contracts_view),
) -> WorkspaceContractPageRead:
    items, total = await CommercialCatalogService(db).list_contracts(limit=limit, offset=offset)
    return WorkspaceContractPageRead(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
    )


@router.get(
    "/contracts/{contract_id}",
    response_model=WorkspaceContractRead,
    summary="Consultar contrato e preço efetivo",
)
async def get_commercial_contract(
    contract_id: UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_contracts_view),
) -> WorkspaceContractRead:
    try:
        contract = await CommercialCatalogService(db).get_contract(contract_id)
        response.headers["ETag"] = f'"{contract.version}"'
        return contract
    except CommercialCatalogNotFoundError as error:
        _raise_commercial_error(error)


@router.get(
    "/contracts/{contract_id}/history",
    response_model=WorkspaceContractHistoryRead,
    summary="Consultar histórico e diff do contrato",
)
async def get_commercial_contract_history(
    contract_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_contracts_view),
) -> WorkspaceContractHistoryRead:
    try:
        return await CommercialCatalogService(db).contract_history(contract_id)
    except CommercialCatalogNotFoundError as error:
        _raise_commercial_error(error)


@router.post(
    "/contracts/{contract_id}/restore-preview",
    response_model=WorkspaceContractRestorePreviewRead,
    summary="Pré-visualizar restauração preservando descontos e bônus",
)
async def preview_commercial_contract_restore(
    contract_id: UUID,
    payload: WorkspaceContractRestorePreviewRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_root),
) -> WorkspaceContractRestorePreviewRead:
    try:
        service = CommercialCatalogService(db)
        workspace_id = await service.get_contract_workspace_id(contract_id)
        await _bind_single_workspace_context(db, workspace_id)
        return await service.preview_contract_restore(
            contract_id=contract_id,
            target_version=payload.target_version,
            expected_current_version=payload.expected_current_version,
        )
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
    ) as error:
        _raise_commercial_error(error)


@router.post(
    "/contracts/{contract_id}/restore",
    response_model=WorkspaceContractRead,
    summary="Restaurar contrato como nova versão preservando exceções individuais",
)
async def restore_commercial_contract(
    contract_id: UUID,
    payload: WorkspaceContractRestoreRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_root),
) -> WorkspaceContractRead:
    try:
        service = CommercialCatalogService(db)
        workspace_id = await service.get_contract_workspace_id(contract_id)
        await _bind_single_workspace_context(db, workspace_id)
        contract, _ = await CanonicalContractCommand(
            db,
            control_plane=True,
        ).restore_contract(
            contract_id=contract_id,
            payload=payload,
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
        )
        response.headers["ETag"] = f'"{contract.version}"'
        return contract
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
        CommercialBridgeError,
    ) as error:
        _raise_commercial_error(error)


@router.get(
    "/workspaces/{workspace_id}/contract",
    response_model=WorkspaceContractRead,
    summary="Consultar contrato de uma Conta 360",
)
async def get_workspace_commercial_contract(
    workspace_id: UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_contracts_view),
) -> WorkspaceContractRead:
    try:
        contract = await CommercialCatalogService(db).get_workspace_contract(workspace_id)
        response.headers["ETag"] = f'"{contract.version}"'
        return contract
    except CommercialCatalogNotFoundError as error:
        _raise_commercial_error(error)


@router.get(
    "/workspaces/{workspace_id}/provider-state",
    response_model=CommercialWorkspaceProviderStateRead,
    summary="Consultar vínculo seguro da cobrança da Conta 360",
)
async def get_workspace_commercial_provider_state(
    workspace_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_contracts_view),
) -> CommercialWorkspaceProviderStateRead:
    try:
        bridge = CommercialStripeBridge(db)
        contract = await bridge.catalog.get_workspace_contract(workspace_id)
        await _bind_single_workspace_context(db, workspace_id)
        binding = await bridge.contract_binding(contract.id, any_provider=True)
        active_provider = binding.provider if binding is not None else CommercialProvider.stripe
        active_bridge = CommercialStripeBridge(db, active_provider)
        provider = await active_bridge.provider_state()
        gateway = None
        if provider.available:
            try:
                gateway = get_saas_billing_gateway(active_provider)
            except SaaSBillingGatewayError:
                gateway = None
        return CommercialWorkspaceProviderStateRead(
            provider=active_provider.value if binding is not None else None,
            provider_state=provider.state,
            provider_message=provider.message,
            customer_linked=bool(binding and binding.provider_customer_id),
            subscription_linked=bool(binding and binding.provider_subscription_id),
            billing_portal_available=bool(
                binding and binding.provider_customer_id and provider.available
                and gateway is not None and gateway.supports_customer_portal
            ),
            provider_status=binding.provider_status if binding else None,
        )
    except Exception as error:
        if isinstance(error, CommercialCatalogNotFoundError):
            _raise_commercial_error(error)
        _raise_provider_error(error)
        raise


@router.post(
    "/workspaces/{workspace_id}/billing-portal",
    response_model=CommercialBillingPortalRead,
    summary="Abrir portal hospedado do provedor quando disponível",
)
async def create_workspace_commercial_billing_portal(
    workspace_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_root),
) -> CommercialBillingPortalRead:
    try:
        bridge = CommercialStripeBridge(db)
        contract = await bridge.catalog.get_workspace_contract(workspace_id)
        await _bind_single_workspace_context(db, workspace_id)
        binding = await bridge.contract_binding(contract.id, any_provider=True)
        if binding is None or not binding.provider_customer_id:
            raise CommercialStripeBridgeError(
                "A conta ainda não possui cliente de cobrança vinculado."
            )
        gateway = get_saas_billing_gateway(binding.provider)
        if not gateway.supports_customer_portal:
            raise CommercialStripeBridgeError(
                "Este provedor não possui portal hospedado; use os comandos internos de assinatura."
            )
        result = await gateway.create_billing_portal_session(
            customer_id=binding.provider_customer_id,
            return_url=f"{settings.BLACKBOX_FRONTEND_URL.rstrip('/')}/blackbox",
        )
        if not result.url.startswith("https://"):
            raise SaaSBillingGatewayError("O provedor não devolveu uma URL segura para o portal.")
        return CommercialBillingPortalRead(portal_url=result.url)
    except Exception as error:
        if isinstance(error, CommercialCatalogNotFoundError):
            _raise_commercial_error(error)
        _raise_provider_error(error)
        raise


@router.post(
    "/contracts/{contract_id}/revision-preview",
    response_model=WorkspaceContractRevisionPreviewRead,
    summary="Pré-visualizar troca do Core, inclusão ou remoção do Jurídico 360",
)
async def preview_commercial_contract_revision(
    contract_id: UUID,
    payload: WorkspaceContractRevisionPreviewRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_contracts_manage),
) -> WorkspaceContractRevisionPreviewRead:
    try:
        service = CommercialCatalogService(db)
        workspace_id = await service.get_contract_workspace_id(contract_id)
        await _bind_single_workspace_context(db, workspace_id)
        return await service.preview_contract_revision(contract_id, payload)
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
        CommercialBridgeError,
    ) as error:
        _raise_commercial_error(error)


@router.put(
    "/contracts/{contract_id}",
    response_model=WorkspaceContractRead,
    summary="Aplicar revisão versionada do contrato sem criar um segundo contrato",
)
async def revise_commercial_contract(
    contract_id: UUID,
    payload: WorkspaceContractRevisionRequest,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_contracts_manage),
) -> WorkspaceContractRead:
    try:
        service = CommercialCatalogService(db)
        workspace_id = await service.get_contract_workspace_id(contract_id)
        await _bind_single_workspace_context(db, workspace_id)
        contract, _ = await CanonicalContractCommand(
            db,
            control_plane=True,
        ).revise_contract(
            contract_id=contract_id,
            expected_version=_required_version(if_match),
            payload=payload,
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
        )
        response.headers["ETag"] = f'"{contract.version}"'
        return contract
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
        CommercialBridgeError,
    ) as error:
        _raise_commercial_error(error)


@router.get(
    "/workspaces/{workspace_id}/manifest",
    response_model=EffectiveCapabilityManifestRead,
    summary="Resolver plano, Jurídico, bônus e override da conta",
)
async def get_effective_capability_manifest(
    workspace_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_contracts_view),
) -> EffectiveCapabilityManifestRead:
    try:
        await _bind_single_workspace_context(db, workspace_id)
        return await CommercialCatalogService(db).resolve_effective_manifest(workspace_id)
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
    ) as error:
        _raise_commercial_error(error)


@router.post(
    "/contracts/{contract_id}/adjustments",
    response_model=CommercialAdjustmentCommandRead,
    status_code=status.HTTP_201_CREATED,
    summary="Adicionar desconto, preço negociado, cortesia ou crédito",
)
async def add_commercial_adjustment(
    contract_id: UUID,
    payload: CommercialAdjustmentCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_adjustments_manage),
) -> CommercialAdjustmentCommandRead:
    try:
        adjustment, replay = await CanonicalContractCommand(
            db,
            control_plane=True,
        ).add_adjustment(
            contract_id=contract_id,
            payload=payload,
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
        )
        return CommercialAdjustmentCommandRead(adjustment=adjustment, idempotent_replay=replay)
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
    ) as error:
        _raise_commercial_error(error)


@router.get(
    "/contracts/{contract_id}/adjustments",
    response_model=list[CommercialAdjustmentRead],
    summary="Listar ajustes comerciais sem misturá-los às quotas",
)
async def list_commercial_adjustments(
    contract_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_adjustments_view),
) -> list[CommercialAdjustmentRead]:
    try:
        return await CommercialCatalogService(db).list_adjustments(contract_id)
    except CommercialCatalogNotFoundError as error:
        _raise_commercial_error(error)


@router.put(
    "/adjustments/{adjustment_id}",
    response_model=CommercialAdjustmentCommandRead,
    summary="Substituir termos mutáveis de um ajuste com controle de versão",
)
async def update_commercial_adjustment(
    adjustment_id: UUID,
    payload: CommercialAdjustmentUpdateRequest,
    if_match: str | None = Header(default=None, alias="If-Match"),
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_adjustments_manage),
) -> CommercialAdjustmentCommandRead:
    try:
        adjustment, replay = await CanonicalContractCommand(
            db,
            control_plane=True,
        ).update_adjustment(
            adjustment_id=adjustment_id,
            expected_version=_required_version(if_match),
            payload=payload,
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
        )
        return CommercialAdjustmentCommandRead(
            adjustment=adjustment, idempotent_replay=replay
        )
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
    ) as error:
        _raise_commercial_error(error)


@router.post(
    "/adjustments/{adjustment_id}/revoke",
    response_model=CommercialAdjustmentCommandRead,
    summary="Revogar ajuste comercial com motivo e controle de versão",
)
async def revoke_commercial_adjustment(
    adjustment_id: UUID,
    payload: CommercialRecordRevokeRequest,
    if_match: str | None = Header(default=None, alias="If-Match"),
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_adjustments_manage),
) -> CommercialAdjustmentCommandRead:
    try:
        adjustment, replay = await CanonicalContractCommand(
            db,
            control_plane=True,
        ).revoke_adjustment(
            adjustment_id=adjustment_id,
            expected_version=_required_version(if_match),
            payload=payload,
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
        )
        return CommercialAdjustmentCommandRead(
            adjustment=adjustment, idempotent_replay=replay
        )
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
    ) as error:
        _raise_commercial_error(error)


@router.post(
    "/workspaces/{workspace_id}/bonuses",
    response_model=EntitlementBonusCommandRead,
    status_code=status.HTTP_201_CREATED,
    summary="Adicionar bônus de capacidade sem alterar preço",
)
async def add_entitlement_bonus(
    workspace_id: UUID,
    payload: EntitlementBonusCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_bonuses_manage),
) -> EntitlementBonusCommandRead:
    try:
        bonus, replay = await CanonicalContractCommand(
            db,
            control_plane=True,
        ).add_bonus(
            workspace_id=workspace_id,
            payload=payload,
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
        )
        return EntitlementBonusCommandRead(bonus=bonus, idempotent_replay=replay)
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
    ) as error:
        _raise_commercial_error(error)


@router.get(
    "/workspaces/{workspace_id}/bonuses",
    response_model=list[EntitlementBonusRead],
    summary="Listar bônus independentes do contrato e do catálogo",
)
async def list_entitlement_bonuses(
    workspace_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_bonuses_view),
) -> list[EntitlementBonusRead]:
    return await CommercialCatalogService(db).list_bonuses(workspace_id)


@router.put(
    "/bonuses/{bonus_id}",
    response_model=EntitlementBonusCommandRead,
    summary="Substituir termos mutáveis de um bônus com controle de versão",
)
async def update_entitlement_bonus(
    bonus_id: UUID,
    payload: EntitlementBonusUpdateRequest,
    if_match: str | None = Header(default=None, alias="If-Match"),
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_bonuses_manage),
) -> EntitlementBonusCommandRead:
    try:
        bonus, replay = await CanonicalContractCommand(
            db,
            control_plane=True,
        ).update_bonus(
            bonus_id=bonus_id,
            expected_version=_required_version(if_match),
            payload=payload,
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
        )
        return EntitlementBonusCommandRead(bonus=bonus, idempotent_replay=replay)
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
    ) as error:
        _raise_commercial_error(error)


@router.post(
    "/bonuses/{bonus_id}/revoke",
    response_model=EntitlementBonusCommandRead,
    summary="Revogar bônus com motivo e controle de versão",
)
async def revoke_entitlement_bonus(
    bonus_id: UUID,
    payload: CommercialRecordRevokeRequest,
    if_match: str | None = Header(default=None, alias="If-Match"),
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_bonuses_manage),
) -> EntitlementBonusCommandRead:
    try:
        bonus, replay = await CanonicalContractCommand(
            db,
            control_plane=True,
        ).revoke_bonus(
            bonus_id=bonus_id,
            expected_version=_required_version(if_match),
            payload=payload,
            actor_user_id=actor.id,
            idempotency_key=idempotency_key,
        )
        return EntitlementBonusCommandRead(bonus=bonus, idempotent_replay=replay)
    except (
        CommercialCatalogNotFoundError,
        CommercialCatalogConflictError,
        CommercialCatalogValidationError,
    ) as error:
        _raise_commercial_error(error)


__all__ = ["router"]
