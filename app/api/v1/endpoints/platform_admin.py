"""
Endpoints do painel global da plataforma.
"""

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.entitlement_errors import entitlement_http_exception
from app.core.config import settings
from app.core.deps import AuthContext, get_current_auth_context, get_db
from app.core.platform_access import require_platform_permission, require_platform_root
from app.core.platform_audit import (
    _record_platform_mutation,
    record_canonical_platform_mutation,
    snapshot_platform_entitlement_overrides,
    snapshot_platform_finance_harmonization_quota,
    snapshot_platform_invitation,
    snapshot_platform_membership,
    snapshot_platform_request_resource,
    snapshot_platform_user,
    snapshot_platform_workspace,
    snapshot_platform_workspace_ownership,
)
from app.core.platform_rbac import (
    effective_platform_permissions,
    has_platform_permission,
    is_platform_root,
    platform_permission_key,
)
from app.db.tenant import set_workspace_context
from app.models.ai_token_package import (
    AiTokenPackage,
    AiTokenPackageRequestStatus,
    WorkspaceAiTokenPackageRequest,
)
from app.models.auth_security import AdministrativeSession
from app.models.configuration import PlatformConfigDocument
from app.models.finance_dimensional import FinanceExportJob, FinanceTaxProfile
from app.models.meta_commercial_rate import MetaCommercialRateCard
from app.models.support_access import SupportAccessGrant, SupportAccessScope
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceStatus
from app.schemas.ai_costs import (
    AiTokenPackageDecision,
    AiTokenPackageRead,
    AiTokenPackageRevoke,
    AiTokenPackageUpsert,
    WorkspaceAiCostPolicyUpdate,
    WorkspaceAiCostSummaryRead,
    WorkspaceAiTokenPackageRequestPageRead,
    WorkspaceAiTokenPackageRequestRead,
)
from app.schemas.ai_operations import AiOperationsEventPage, AiOperationsSummary
from app.schemas.configuration import (
    ConfigVersionPageRead,
    ConfigVersionRead,
    PlatformConfigurationDocumentRead,
    PlatformConfigurationDocumentWrite,
)
from app.schemas.email import CanonicalEmail
from app.schemas.finance_dimensional import (
    BudgetLineWrite,
    FinanceAnalyticsQuery,
    FinanceDreQuery,
    ForecastGenerateWrite,
    TaxProfileWrite,
    TaxReformSimulationWrite,
    ValuationCalculateWrite,
)
from app.schemas.finance_executive import ExecutiveAnalyticsRead
from app.schemas.finance_export import FinanceExportCreate, FinanceExportRead
from app.schemas.finance_harmonization import (
    FinanceHarmonizationBonusGrantWrite,
    FinanceHarmonizationQuotaRead,
)
from app.schemas.klm_operations import (
    AgentBillingReconciliationCreate,
    AgentBillingReconciliationPendingPage,
    AgentRunRead,
)
from app.schemas.meta_commercial_rate import (
    MetaCommercialRateCardRead,
    MetaCommercialRateCardUpsert,
)
from app.schemas.platform_admin import (
    BlackboxSessionManifestRead,
    PlatformRolePackageRead,
    PlatformStaffCreate,
    PlatformStaffPageRead,
    PlatformStaffRead,
    PlatformStaffSessionRevoke,
    PlatformStaffUpdate,
    PlatformTemporaryPasswordRead,
    PlatformTemporaryPasswordSet,
    PlatformWorkspaceBulkAction,
    PlatformWorkspaceBulkActionRead,
    PlatformWorkspaceCreate,
    PlatformWorkspacePageRead,
    PlatformWorkspaceRead,
    PlatformWorkspaceUpdate,
    WorkspacePlanCatalogItemRead,
    WorkspacePlanCatalogItemUpdate,
)
from app.schemas.platform_billing import PlatformSubscriptionEventPageRead
from app.schemas.whatsapp_campaign import UtilityTemplateCatalogRead
from app.schemas.workspace import WorkspaceAccessPackageRead
from app.schemas.workspace_ai_models import PlatformAiModelsOverviewRead
from app.services.ai_cost_service import AiCostService, AiCostWorkspaceNotFoundError
from app.services.ai_operations_service import AiOperationsService
from app.services.ai_token_package_service import (
    AiTokenPackageConflictError,
    AiTokenPackageNotFoundError,
    AiTokenPackageService,
    AiTokenPackageTransitionError,
)
from app.services.configuration_service import (
    ConfigConflictError,
    ConfigurationService,
    ConfigValidationError,
)
from app.services.entitlement_service import EntitlementError
from app.services.finance_dimensional_service import FinanceDimensionalService
from app.services.finance_executive_service import FinanceExecutiveService
from app.services.finance_export_service import record_finance_export_event
from app.services.finance_harmonization_service import FinanceHarmonizationService
from app.services.klm_agent_service import KlmAgentService
from app.services.meta_commercial_rate_service import (
    MetaCommercialRateConflictError,
    MetaCommercialRateService,
)
from app.services.platform_billing_service import (
    CanonicalCommercialContractConflictError,
    InvalidSubscriptionActionError,
    PlatformBillingService,
    SubscriptionNotFoundError,
)
from app.services.platform_export_service import PlatformExportError, PlatformExportService
from app.services.platform_metrics_service import (
    CATEGORIES,
    LEGACY_CATEGORY_ALIASES,
    PlatformMetricsService,
)
from app.services.platform_service import (
    InvalidPlatformPlanKeyError,
    LegacyPlanEditorDisabledError,
    PlatformService,
)
from app.services.platform_staff_service import PlatformStaffService
from app.services.whatsapp.utility_template_catalog import UTILITY_TEMPLATE_BY_KEY
from app.services.whatsapp_service import WhatsAppService
from app.services.workspace_ai_model_routing_service import (
    build_platform_ai_models_overview,
)
from app.services.workspace_entitlement_override_service import (
    MAX_SAFE_ENTITLEMENT_QUANTITY,
    WorkspaceEntitlementOverrideNotFoundError,
    WorkspaceEntitlementOverrideService,
    WorkspaceEntitlementOverrideValidationError,
    WorkspaceEntitlementOverrideWorkspaceNotFoundError,
)
from app.services.workspace_overview_service import (
    WorkspaceOverviewService,
    WorkspaceOverviewWorkspaceNotFoundError,
)
from app.services.workspace_service import WorkspaceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/platform", tags=["Platform Admin"])


def _meta_rate_card_snapshot(card: MetaCommercialRateCard) -> tuple[str, str, dict]:
    return (
        "meta_commercial_rate_card",
        str(card.id),
        {
            "key": card.rate_card_key,
            "category": card.category,
            "countryCode": card.country_code,
            "currency": card.currency,
            "commercialRate": str(card.commercial_rate),
            "effectiveFrom": card.effective_from,
            "effectiveTo": card.effective_to,
            "sourceUrl": card.source_url,
            "checkedAt": card.checked_at,
            "isActive": card.is_active,
        },
    )


def _ai_token_package_snapshot(package: AiTokenPackage) -> tuple[str, str, dict]:
    return (
        "ai_token_package",
        str(package.id),
        {
            "key": package.key,
            "name": package.name,
            "descriptionPresent": bool(package.description),
            "tokenQuantity": package.token_quantity,
            "commercialPriceCents": package.commercial_price_cents,
            "currency": package.currency,
            "isActive": package.is_active,
            "sortOrder": package.sort_order,
        },
    )


def _ai_token_package_request_snapshot(
    request_row: WorkspaceAiTokenPackageRequest | None,
) -> tuple[str, str, dict] | None:
    if request_row is None:
        return None
    return (
        "workspace_ai_token_package_request",
        str(request_row.id),
        {
            "workspaceId": request_row.workspace_id,
            "packageId": request_row.package_id,
            "status": request_row.status.value,
            "packageKey": request_row.package_key_snapshot,
            "packageName": request_row.package_name_snapshot,
            "tokenQuantity": request_row.token_quantity_snapshot,
            "commercialPriceCents": request_row.commercial_price_cents_snapshot,
            "currency": request_row.currency_snapshot,
            "periodStart": request_row.period_start,
            "periodEnd": request_row.period_end,
            "decisionNote": request_row.decision_note,
            "decidedByUserId": request_row.decided_by_user_id,
            "decidedAt": request_row.decided_at,
        },
    )


require_platform_admin = require_platform_root

_dashboard_view = require_platform_permission("dashboard", "view")
_finance_view = require_platform_permission("finance", "view")
_finance_manage = require_platform_permission("finance", "manage")


async def _require_finance_workspace_session(
    *,
    actor: User,
    context: AuthContext,
    workspace_id: UUID,
    required_scope: SupportAccessScope,
    request: Request,
    db: AsyncSession,
) -> None:
    """Bind every tenant-finance operation to one live, selected admin session."""
    code = "PLATFORM-FINANCE-SESSION-REQUIRED"
    message = "Entre diretamente na conta antes de operar o financeiro dela."
    valid_type = (
        context.administrative_session_type == "root_direct"
        if is_platform_root(actor)
        else context.administrative_session_type == "support_approved"
    )
    valid_scope = is_platform_root(actor) or required_scope.value in context.support_scopes
    valid_workspace = context.workspace_id == workspace_id
    session = None
    if valid_type and valid_scope and valid_workspace and context.administrative_session_id:
        session = await db.scalar(
            select(AdministrativeSession).where(
                AdministrativeSession.id == context.administrative_session_id,
                AdministrativeSession.user_id == actor.id,
                AdministrativeSession.revoked_at.is_(None),
                AdministrativeSession.expires_at > datetime.now(UTC),
            )
        )
    if session is not None:
        return
    if context.workspace_id is not None and context.workspace_id != workspace_id:
        code = "PLATFORM-FINANCE-SESSION-WORKSPACE-MISMATCH"
        message = "A sessão administrativa ativa pertence a outra conta."
    await _record_platform_mutation(
        request,
        context,
        db,
        "denied",
        code,
        module="finance",
        action="tenant_session.validate",
        reason=message,
        reason_source="system",
    )
    await db.commit()
    raise HTTPException(status_code=403, detail={"code": code, "message": message})


def _finance_dimension_snapshot(obj):
    return (
        obj.__class__.__name__,
        str(obj.id),
        {
            k: str(v) if isinstance(v, (UUID, Decimal, date, datetime)) else v
            for k, v in vars(obj).items()
            if not k.startswith("_") and k not in {"workspace_id"}
        },
    )


_reports_view = require_platform_permission("reports", "view")
_customers_view = require_platform_permission("customers", "view")
_finance_view = require_platform_permission("finance", "view")
_customers_manage = require_platform_permission("customers", "manage")
_plans_view = require_platform_permission("plans", "view")
_plans_manage = require_platform_permission("plans", "manage")
_settings_view = require_platform_permission("settings", "view")
_settings_manage = require_platform_permission("settings", "manage")
_staff_view = require_platform_permission("staff", "view")
_staff_manage = require_platform_permission("staff", "manage")
_support_view = require_platform_permission("support", "view")
_support_assign = require_platform_permission("support", "assign")
_support_manage = require_platform_permission("support", "manage")
_ai_operations_view = require_platform_permission("ai_operations", "view")

_ROOT_ONLY_KPI_PREFIXES = (
    "receita.custo_",
    "receita.ebitda",
    "receita.lucro_",
    "receita.margem_",
)


def _allowed_metric_categories(actor: User) -> set[str]:
    if is_platform_root(actor):
        return set(CATEGORIES)
    allowed = {"produto", "sucesso_cliente"}
    if has_platform_permission(actor, "finance", "view"):
        allowed.update({"receita", "assinaturas", "cobranca", "retencao", "churn"})
    if has_platform_permission(actor, "sales", "view") or has_platform_permission(
        actor, "marketing", "view"
    ):
        allowed.update({"aquisicao", "vendas"})
    if has_platform_permission(actor, "ai_operations", "view"):
        allowed.add("ia")
    module_category_pairs = {
        "onboarding": "onboarding",
        "success": "sucesso_cliente",
        "support": "suporte",
        "whatsapp": "conversas",
        "integrations": "canais",
        "infrastructure": "infraestrutura",
        "staff": "equipe",
        "security": "seguranca",
    }
    for module, category in module_category_pairs.items():
        if has_platform_permission(actor, module, "view"):
            allowed.add(category)
    return allowed


def _filter_metric_payload(payload: dict[str, Any], actor: User) -> dict[str, Any]:
    if is_platform_root(actor):
        return payload
    allowed = _allowed_metric_categories(actor)
    categories = []
    for category in payload.get("categories", []):
        if category.get("key") not in allowed:
            continue
        kpis = [
            item
            for item in category.get("kpis", [])
            if not str(item.get("key", "")).startswith(_ROOT_ONLY_KPI_PREFIXES)
        ]
        categories.append({**category, "count": len(kpis), "kpis": kpis})
    return {
        **payload,
        "categories": categories,
        "total_kpis": sum(item["count"] for item in categories),
    }


@router.get(
    "/session-manifest",
    response_model=BlackboxSessionManifestRead,
    summary="Manifesto canônico de capacidades da sessão Blackbox",
)
async def get_blackbox_session_manifest(
    db: AsyncSession = Depends(get_db),
    context: AuthContext = Depends(get_current_auth_context),
    actor: User = Depends(_dashboard_view),
) -> BlackboxSessionManifestRead:
    permissions = effective_platform_permissions(actor)
    modules = sorted({platform_permission_key(item)[0] for item in permissions})
    flags_document = await db.scalar(
        select(PlatformConfigDocument).where(
            PlatformConfigDocument.namespace == "blackbox.feature_flags"
        )
    )
    raw_flags = (flags_document.payload or {}).get("flags", {}) if flags_document else {}
    feature_flags = (
        {
            str(key): value
            for key, value in raw_flags.items()
            if isinstance(key, str) and isinstance(value, bool)
        }
        if isinstance(raw_flags, dict)
        else {}
    )
    support_session: dict[str, object] | None = None
    if context.administrative_session_type == "root_direct" and context.administrative_session_id:
        session = await db.scalar(
            select(AdministrativeSession).where(
                AdministrativeSession.id == context.administrative_session_id,
                AdministrativeSession.user_id == actor.id,
                AdministrativeSession.revoked_at.is_(None),
            )
        )
        # The authentication layer already validates this session.  The
        # manifest mirrors only safe routing metadata; it never exposes the
        # token JTI, MFA proof, revocation reason, or a credential.
        if session is not None:
            support_session = {
                "id": str(session.id),
                "type": "root_direct",
                "workspaceId": str(context.workspace_id) if context.workspace_id else None,
                "expiresAt": session.expires_at,
                "scopes": [],
            }
    elif context.administrative_session_type == "support_approved" and context.support_grant_id:
        grant = await db.scalar(
            select(SupportAccessGrant).where(
                SupportAccessGrant.id == context.support_grant_id,
                SupportAccessGrant.requester_user_id == actor.id,
            )
        )
        if grant is not None:
            support_session = {
                "id": str(grant.id),
                "type": "support_approved",
                "workspaceId": str(context.workspace_id)
                if context.workspace_id
                else str(grant.workspace_id),
                "expiresAt": grant.expires_at,
                "scopes": sorted(context.support_scopes),
            }

    return BlackboxSessionManifestRead(
        user_id=actor.id,
        is_platform_root=is_platform_root(actor),
        role_package=actor.platform_role_package,
        permissions=[{"module": item.module, "action": item.action} for item in permissions],
        available_modules=modules,
        masked_fields=[]
        if is_platform_root(actor)
        else [
            "credentials",
            "customer_content",
            "internal_costs",
            "private_financial_data",
            "secrets",
        ],
        support_session=support_session,
        feature_flags=feature_flags,
        frontend_version="0.0.0",
        backend_version=settings.APP_VERSION,
    )


@router.get(
    "/workspaces/{workspace_id}/agent-runs/billing-reconciliation/pending",
    response_model=AgentBillingReconciliationPendingPage,
    summary="Listar execuções com cobrança incerta pendentes de conciliação",
)
async def list_workspace_agent_runs_pending_billing_reconciliation(
    workspace_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_platform_root),
    db: AsyncSession = Depends(get_db),
) -> AgentBillingReconciliationPendingPage:
    await set_workspace_context(db, workspace_id)
    items, total = await KlmAgentService(db).list_billing_reconciliation_pending(
        workspace_id=workspace_id,
        limit=limit,
        offset=offset,
    )
    return AgentBillingReconciliationPendingPage(
        total=total,
        limit=limit,
        offset=offset,
        items=[AgentRunRead.model_validate(item) for item in items],
    )


@router.post(
    "/workspaces/{workspace_id}/agent-runs/{run_id}/billing-reconciliation",
    response_model=AgentRunRead,
    summary="Conciliar cobrança incerta de provider pela Central Blackbox",
)
async def reconcile_workspace_agent_run_billing(
    workspace_id: UUID,
    run_id: UUID,
    payload: AgentBillingReconciliationCreate,
    actor: User = Depends(require_platform_root),
    db: AsyncSession = Depends(get_db),
) -> AgentRunRead:
    await set_workspace_context(db, workspace_id)
    try:
        run = await KlmAgentService(db).reconcile_billing(
            workspace_id=workspace_id,
            user_id=actor.id,
            run_id=run_id,
            decision=payload.decision,
            actual_cost_usd=payload.actual_cost_usd,
            evidence_reference=payload.evidence_reference,
            reason=payload.reason,
            provider_request_id=payload.provider_request_id,
        )
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    except LookupError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    return AgentRunRead.model_validate(run)


@router.get(
    "/meta-commercial-rate-cards",
    response_model=list[MetaCommercialRateCardRead],
    summary="Listar rate cards comerciais Meta",
)
async def list_meta_commercial_rate_cards(
    include_inactive: bool = Query(default=True, alias="includeInactive"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_root),
) -> list[MetaCommercialRateCardRead]:
    return await MetaCommercialRateService(db).list_rate_cards(include_inactive=include_inactive)


@router.put(
    "/meta-commercial-rate-cards/{rate_card_key}",
    response_model=MetaCommercialRateCardRead,
    summary="Criar ou atualizar versão de rate card comercial Meta",
)
async def upsert_meta_commercial_rate_card(
    rate_card_key: str,
    payload: MetaCommercialRateCardUpsert,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_root),
    context: AuthContext = Depends(get_current_auth_context),
) -> MetaCommercialRateCardRead:
    try:
        normalized_key = rate_card_key.strip().lower()
        existing = await db.scalar(
            select(MetaCommercialRateCard).where(
                MetaCommercialRateCard.rate_card_key == normalized_key
            )
        )
        before = _meta_rate_card_snapshot(existing) if existing is not None else None
        card = await MetaCommercialRateService(db).upsert_rate_card(
            rate_card_key=rate_card_key,
            payload=payload,
        )
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=_meta_rate_card_snapshot(card),
            module="finance",
            action="meta_rate_card.upsert",
            reason=f"Rate card comercial {normalized_key} publicado ou atualizado.",
            reason_source="system",
        )
        return card
    except MetaCommercialRateConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "META-COMMERCIAL-RATE-CONFLICT", "message": str(error)},
        ) from error


@router.get("/overview", summary="Visão geral consolidada da plataforma")
async def get_platform_overview(
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_dashboard_view),
) -> dict[str, Any]:
    """
    Dashboard consolidado para o dono da plataforma.
    Retorna MRR, workspaces, uso de IA, custo AWS e health scores.
    """
    svc = PlatformService(db)

    requests: dict[str, Any] = {}
    if has_platform_permission(actor, "finance", "view"):
        requests["mrr"] = svc.get_mrr_breakdown()
    if has_platform_permission(actor, "customers", "view"):
        requests["workspaces"] = svc.get_workspace_summary()
        requests["workspace_health"] = svc.get_workspace_health_scores(limit=10)
    if is_platform_root(actor):
        requests["ai_costs_30d"] = svc.get_token_usage_summary(days=30)
    if is_platform_root(actor):
        requests["aws_costs"] = svc.get_aws_cost_estimate()

    keys = list(requests)
    values = await asyncio.gather(*requests.values(), return_exceptions=True)
    result: dict[str, Any] = {}
    for key, value in zip(keys, values):
        if isinstance(value, Exception):
            result[key] = [] if key == "workspace_health" else {"error": "Métrica indisponível."}
        else:
            result[key] = value
    return result


@router.get("/mrr", summary="MRR e ARR detalhados por plano")
async def get_mrr(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_finance_view),
) -> dict[str, Any]:
    svc = PlatformService(db)
    return await svc.get_mrr_breakdown()


@router.get(
    "/finance/workspaces/{workspace_id}/executive",
    response_model=ExecutiveAnalyticsRead,
    summary="Financeiro Executivo 360 de uma conta, com contexto administrativo explícito",
)
async def platform_workspace_finance_executive(
    workspace_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_finance_view),
    context: AuthContext = Depends(get_current_auth_context),
) -> ExecutiveAnalyticsRead:
    """Read a tenant's finance metrics without changing the tenant session.

    The selected workspace is an explicit path parameter and every service
    query remains tenant-scoped. The actor is the platform employee/root and
    is not impersonated as a customer user.
    """
    await _require_finance_workspace_session(
        actor=actor, context=context, workspace_id=workspace_id,
        required_scope=SupportAccessScope.finance_read, request=request, db=db,
    )
    workspace = await db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Conta não encontrada")
    result = await FinanceExecutiveService(db).analytics(workspace_id)
    return result


@router.post(
    "/finance/workspaces/{workspace_id}/drilldown",
    openapi_extra={
        "x-platform-mutation": {
            "key": "platform.finance_drilldown.query",
            "module": "finance",
            "action": "view",
            "resourceType": "not_applicable_query",
            "reasonPolicy": "system_allowed",
            "snapshotAdapter": "not_applicable_query",
            "mutationKind": "query",
            "restorationAdapter": None,
            "idempotencyPolicy": "not_applicable",
            "externalEffect": False,
            "auditSchemaVersion": 1,
        }
    },
)
async def platform_workspace_finance_drilldown(
    workspace_id: UUID,
    payload: FinanceAnalyticsQuery,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_finance_view),
    context: AuthContext = Depends(get_current_auth_context),
):
    await _require_finance_workspace_session(
        actor=actor, context=context, workspace_id=workspace_id,
        required_scope=SupportAccessScope.finance_read, request=request, db=db,
    )
    if await db.get(Workspace, workspace_id) is None:
        raise HTTPException(404, "Conta não encontrada")
    return await FinanceDimensionalService(db).drilldown(
        workspace_id,
        **payload.model_dump(),
    )


@router.post("/finance/workspaces/{workspace_id}/dre")
async def platform_workspace_finance_dre(
    workspace_id: UUID,
    payload: FinanceDreQuery,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_finance_view),
    context: AuthContext = Depends(get_current_auth_context),
):
    await _require_finance_workspace_session(
        actor=actor, context=context, workspace_id=workspace_id,
        required_scope=SupportAccessScope.finance_read, request=request, db=db,
    )
    if await db.get(Workspace, workspace_id) is None:
        raise HTTPException(404, "Conta não encontrada")
    return await FinanceDimensionalService(db).dre(workspace_id, **payload.model_dump())


@router.post("/finance/workspaces/{workspace_id}/forecasts/generate")
async def platform_workspace_finance_forecasts(
    workspace_id: UUID,
    payload: ForecastGenerateWrite,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_finance_manage),
    context: AuthContext = Depends(get_current_auth_context),
):
    await _require_finance_workspace_session(
        actor=actor, context=context, workspace_id=workspace_id,
        required_scope=SupportAccessScope.finance_write, request=request, db=db,
    )
    if await db.get(Workspace, workspace_id) is None:
        raise HTTPException(404, "Conta não encontrada")
    values = await FinanceDimensionalService(db).generate_forecast(
        workspace_id, **payload.model_dump()
    )
    for value in values:
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=None,
            after_snapshot=_finance_dimension_snapshot(value),
            module="finance",
            action="forecast.generate",
            reason="Projeção financeira gerada.",
            reason_source="user",
        )
    await db.commit()
    return values


@router.post("/finance/workspaces/{workspace_id}/valuations/calculate")
async def platform_workspace_finance_valuation(
    workspace_id: UUID,
    payload: ValuationCalculateWrite,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_finance_manage),
    context: AuthContext = Depends(get_current_auth_context),
):
    await _require_finance_workspace_session(
        actor=actor, context=context, workspace_id=workspace_id,
        required_scope=SupportAccessScope.finance_write, request=request, db=db,
    )
    if await db.get(Workspace, workspace_id) is None:
        raise HTTPException(404, "Conta não encontrada")
    value = await FinanceDimensionalService(db).calculate_valuation(
        workspace_id, **payload.model_dump()
    )
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=None,
        after_snapshot=_finance_dimension_snapshot(value),
        module="finance",
        action="valuation.calculate",
        reason="Valuation financeira calculada.",
        reason_source="user",
    )
    await db.commit()
    return value


@router.get("/finance/workspaces/{workspace_id}/tax-profile")
async def platform_workspace_finance_tax_profile(
    workspace_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_finance_view),
    context: AuthContext = Depends(get_current_auth_context),
):
    await _require_finance_workspace_session(
        actor=actor, context=context, workspace_id=workspace_id,
        required_scope=SupportAccessScope.finance_read, request=request, db=db,
    )
    return (
        await db.scalars(
            select(FinanceTaxProfile)
            .where(FinanceTaxProfile.workspace_id == workspace_id)
            .order_by(FinanceTaxProfile.effective_from.desc())
        )
    ).all()


@router.put("/finance/workspaces/{workspace_id}/tax-profile")
async def platform_workspace_finance_tax_profile_update(
    workspace_id: UUID,
    payload: TaxProfileWrite,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_finance_manage),
    context: AuthContext = Depends(get_current_auth_context),
):
    await _require_finance_workspace_session(
        actor=actor, context=context, workspace_id=workspace_id,
        required_scope=SupportAccessScope.finance_write, request=request, db=db,
    )
    if await db.get(Workspace, workspace_id) is None:
        raise HTTPException(404, "Conta não encontrada")
    # O serviço versiona o perfil: uma alteração cria a próxima política e
    # encerra a vigente. Capture a política vigente sob o mesmo lock antes da
    # mutação para que o evento canônico tenha um diff real, sem inventar um
    # estado anterior para a primeira criação.
    previous = await db.scalar(
        select(FinanceTaxProfile)
        .where(FinanceTaxProfile.workspace_id == workspace_id)
        .order_by(FinanceTaxProfile.effective_from.desc(), FinanceTaxProfile.version.desc())
        .with_for_update()
    )
    before_snapshot = _finance_dimension_snapshot(previous) if previous is not None else None
    value = await FinanceDimensionalService(db).upsert_tax_profile(workspace_id, payload)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before_snapshot,
        after_snapshot=_finance_dimension_snapshot(value),
        module="finance",
        action="tax_profile.update",
        reason="Perfil tributário atualizado.",
        reason_source="user",
    )
    await db.commit()
    return value


@router.post("/finance/workspaces/{workspace_id}/tax-reform/simulate")
async def platform_workspace_finance_tax_reform(
    workspace_id: UUID,
    payload: TaxReformSimulationWrite,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_finance_view),
    context: AuthContext = Depends(get_current_auth_context),
):
    await _require_finance_workspace_session(
        actor=actor, context=context, workspace_id=workspace_id,
        required_scope=SupportAccessScope.finance_read, request=request, db=db,
    )
    if await db.get(Workspace, workspace_id) is None:
        raise HTTPException(404, "Conta não encontrada")
    return await FinanceDimensionalService(db).simulate_tax_reform(workspace_id, payload)


@router.get("/finance/workspaces/{workspace_id}/budget-lines")
async def platform_workspace_finance_budget_lines(
    workspace_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_finance_view),
    context: AuthContext = Depends(get_current_auth_context),
):
    await _require_finance_workspace_session(
        actor=actor, context=context, workspace_id=workspace_id,
        required_scope=SupportAccessScope.finance_read, request=request, db=db,
    )
    if await db.get(Workspace, workspace_id) is None:
        raise HTTPException(404, "Conta não encontrada")
    return await FinanceDimensionalService(db).list_budget_lines(workspace_id)


@router.post("/finance/workspaces/{workspace_id}/budget-lines")
async def platform_workspace_finance_budget_line(
    workspace_id: UUID,
    payload: BudgetLineWrite,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_finance_manage),
    context: AuthContext = Depends(get_current_auth_context),
):
    await _require_finance_workspace_session(
        actor=actor, context=context, workspace_id=workspace_id,
        required_scope=SupportAccessScope.finance_write, request=request, db=db,
    )
    if await db.get(Workspace, workspace_id) is None:
        raise HTTPException(404, "Conta não encontrada")
    value = await FinanceDimensionalService(db).create_budget_line(workspace_id, payload)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=None,
        after_snapshot=_finance_dimension_snapshot(value),
        module="finance",
        action="budget_line.create",
        reason="Linha de orçamento criada.",
        reason_source="user",
    )
    await db.commit()
    return value


@router.get("/finance/workspaces/{workspace_id}/insights")
async def platform_workspace_finance_insights(
    workspace_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_finance_view),
    context: AuthContext = Depends(get_current_auth_context),
):
    await _require_finance_workspace_session(
        actor=actor, context=context, workspace_id=workspace_id,
        required_scope=SupportAccessScope.finance_read, request=request, db=db,
    )
    if await db.get(Workspace, workspace_id) is None:
        raise HTTPException(404, "Conta não encontrada")
    metrics = await FinanceExecutiveService(db).analytics(workspace_id)
    insights = []
    concentration = getattr(metrics.concentration, "value", None)
    runway = getattr(metrics.runway, "value", None)
    if concentration is not None and Decimal(str(concentration)) >= Decimal("30"):
        insights.append(
            {
                "key": "client_concentration",
                "severity": "warning",
                "message": "Concentração de receita por cliente acima de 30%.",
                "humanReviewRequired": True,
            }
        )
    if runway is not None and Decimal(str(runway)) < Decimal("6"):
        insights.append(
            {
                "key": "short_runway",
                "severity": "critical",
                "message": "Runway inferior a seis meses.",
                "humanReviewRequired": True,
            }
        )
    return {
        "workspaceId": str(workspace_id),
        "insights": insights,
        "quality": "derived",
        "source": "FinanceExecutiveService",
        "humanReviewRequired": bool(insights),
        "metrics": metrics,
    }


@router.post(
    "/finance/workspaces/{workspace_id}/exports",
    response_model=FinanceExportRead,
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra={
        "x-platform-mutation": {
            "key": "platform.finance_export.create",
            "module": "finance",
            "action": "export",
            "resourceType": "finance_export_job",
            "reasonPolicy": "system_allowed",
            "snapshotAdapter": "finance_export_job",
            "mutationKind": "command",
            "restorationAdapter": None,
            "idempotencyPolicy": "optional",
            "externalEffect": True,
            "auditSchemaVersion": 1,
        }
    },
)
async def platform_workspace_finance_export(
    workspace_id: UUID,
    payload: FinanceExportCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_permission("finance", "export")),
    context: AuthContext = Depends(get_current_auth_context),
):
    await _require_finance_workspace_session(
        actor=actor, context=context, workspace_id=workspace_id,
        required_scope=SupportAccessScope.finance_write, request=request, db=db,
    )
    if await db.get(Workspace, workspace_id) is None:
        raise HTTPException(404, "Conta não encontrada")
    obj = FinanceExportJob(
        workspace_id=workspace_id,
        actor_user_id=actor.id,
        format=payload.format,
        status="queued",
        drill_context=payload.drill_context,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    db.add(obj)
    await db.flush()
    record_finance_export_event(db, obj, "finance_export_requested")
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=None,
        after_snapshot=_platform_finance_export_snapshot(obj),
        module="finance",
        action="export",
        reason="Exportação financeira solicitada pelo Blackbox.",
        reason_source="system",
    )
    await db.commit()
    await db.refresh(obj)
    from app.workers.finance_export_tasks import run_finance_export

    try:
        run_finance_export.apply_async(args=[str(obj.id), str(workspace_id)], retry=False)
    except Exception:
        # The committed queued job is the durable intent. Beat recovery will
        # wake it without reporting a false terminal failure to the operator.
        logger.warning("finance_export.initial_wakeup_failed")
    return obj


async def _platform_finance_export(
    workspace_id: UUID,
    job_id: UUID,
    db: AsyncSession,
) -> FinanceExportJob:
    obj = await db.get(FinanceExportJob, job_id)
    if not obj or obj.workspace_id != workspace_id:
        raise HTTPException(404, "Export não encontrada")
    return obj


def _platform_finance_export_snapshot(
    obj: FinanceExportJob,
) -> tuple[str, str, dict[str, Any]]:
    return (
        "finance_export_job",
        str(obj.id),
        {
            "workspaceId": str(obj.workspace_id),
            "format": obj.format,
            "status": str(getattr(obj.status, "value", obj.status)),
            "progress": obj.progress,
            "rowCount": obj.row_count,
            "checksumSha256": obj.checksum_sha256,
            "expiresAt": obj.expires_at,
            "cancelRequestedAt": obj.cancel_requested_at,
            "errorCode": obj.error_detail,
        },
    )


@router.get("/finance/workspaces/{workspace_id}/exports")
async def list_platform_finance_exports(
    workspace_id: UUID,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_permission("finance", "export")),
    context: AuthContext = Depends(get_current_auth_context),
):
    await _require_finance_workspace_session(
        actor=actor, context=context, workspace_id=workspace_id,
        required_scope=SupportAccessScope.finance_read, request=request, db=db,
    )
    if await db.get(Workspace, workspace_id) is None:
        raise HTTPException(404, "Conta não encontrada")
    base = select(FinanceExportJob).where(FinanceExportJob.workspace_id == workspace_id)
    total = await db.scalar(select(func.count()).select_from(base.subquery()))
    items = list(
        (
            await db.scalars(
                base.order_by(FinanceExportJob.created_at.desc(), FinanceExportJob.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    return {
        "items": items,
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
        "hasMore": offset + len(items) < int(total or 0),
    }


@router.get("/finance/workspaces/{workspace_id}/exports/{job_id}", response_model=FinanceExportRead)
async def get_platform_finance_export(
    workspace_id: UUID,
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_permission("finance", "export")),
    context: AuthContext = Depends(get_current_auth_context),
):
    await _require_finance_workspace_session(
        actor=actor, context=context, workspace_id=workspace_id,
        required_scope=SupportAccessScope.finance_read, request=request, db=db,
    )
    return await _platform_finance_export(workspace_id, job_id, db)


@router.post(
    "/finance/workspaces/{workspace_id}/exports/{job_id}/cancel",
    response_model=FinanceExportRead,
    openapi_extra={
        "x-platform-mutation": {
            "key": "platform.finance_export.cancel",
            "module": "finance",
            "action": "export",
            "resourceType": "finance_export_job",
            "reasonPolicy": "system_allowed",
            "snapshotAdapter": "finance_export_job",
            "mutationKind": "command",
            "restorationAdapter": None,
            "idempotencyPolicy": "optional",
            "externalEffect": False,
            "auditSchemaVersion": 1,
        }
    },
)
async def cancel_platform_finance_export(
    workspace_id: UUID,
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_permission("finance", "export")),
    context: AuthContext = Depends(get_current_auth_context),
):
    await _require_finance_workspace_session(
        actor=actor, context=context, workspace_id=workspace_id,
        required_scope=SupportAccessScope.finance_write, request=request, db=db,
    )
    obj = await _platform_finance_export(workspace_id, job_id, db)
    if obj.status in ("queued", "running"):
        before_snapshot = _platform_finance_export_snapshot(obj)
        before = str(getattr(obj.status, "value", obj.status))
        obj.cancel_requested_at = datetime.now(UTC)
        obj.status = "cancelled"
        record_finance_export_event(
            db,
            obj,
            "finance_export_cancelled",
            before_status=before,
        )
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before_snapshot,
            after_snapshot=_platform_finance_export_snapshot(obj),
            module="finance",
            action="export",
            reason="Exportação financeira cancelada pelo Blackbox.",
            reason_source="system",
        )
        await db.commit()
    return obj


@router.get("/finance/workspaces/{workspace_id}/exports/{job_id}/download")
async def download_platform_finance_export(
    workspace_id: UUID,
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_permission("finance", "export")),
    context: AuthContext = Depends(get_current_auth_context),
):
    await _require_finance_workspace_session(
        actor=actor, context=context, workspace_id=workspace_id,
        required_scope=SupportAccessScope.finance_read, request=request, db=db,
    )
    obj = await _platform_finance_export(workspace_id, job_id, db)
    now = datetime.now(UTC)
    expires_at = obj.expires_at
    if expires_at is not None:
        expires_at = expires_at.replace(tzinfo=UTC) if expires_at.tzinfo is None else expires_at
    if expires_at is not None and expires_at <= now:
        before_snapshot = _platform_finance_export_snapshot(obj)
        before = str(getattr(obj.status, "value", obj.status))
        obj.status = "expired"
        record_finance_export_event(
            db,
            obj,
            "finance_export_expired",
            before_status=before,
        )
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before_snapshot,
            after_snapshot=_platform_finance_export_snapshot(obj),
            module="finance",
            action="export.expired",
            reason="Exportação financeira expirada antes do download.",
            reason_source="system",
        )
        await db.commit()
        raise HTTPException(410, "Export expirada")
    if obj.status != "completed" or not obj.storage_path:
        raise HTTPException(409, "Export ainda não está disponível")
    try:
        artifact = await PlatformExportService(db).authorize_download(obj)
    except PlatformExportError as error:
        raise HTTPException(error.status_code, error.message) from error
    media = {
        "pdf": "application/pdf",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }[obj.format]
    before_snapshot = _platform_finance_export_snapshot(obj)
    record_finance_export_event(
        db,
        obj,
        "finance_export_downloaded",
        before_status=str(getattr(obj.status, "value", obj.status)),
    )
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before_snapshot,
        after_snapshot=_platform_finance_export_snapshot(obj),
        module="finance",
        action="export.downloaded",
        reason="Exportação financeira baixada pelo Blackbox.",
        reason_source="system",
    )
    await db.commit()
    headers = {
        "X-Checksum-SHA256": obj.checksum_sha256 or "",
        "Cache-Control": "private, no-store",
    }
    if artifact.signed_url:
        return RedirectResponse(artifact.signed_url, status_code=307, headers=headers)
    return FileResponse(
        artifact.local_path,
        media_type=media,
        filename=f"finance-{obj.id}.{obj.format}",
        headers=headers,
    )


@router.get("/workspaces", summary="Lista de workspaces com health score")
async def list_workspaces_health(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_customers_view),
) -> list[PlatformWorkspaceRead]:
    svc = PlatformService(db)
    return await svc.list_platform_workspaces(limit=limit)


@router.get(
    "/workspaces/page",
    response_model=PlatformWorkspacePageRead,
    summary="Lista paginada de contas com total real",
)
async def list_workspaces_health_page(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, max_length=120),
    status_filter: WorkspaceStatus | None = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_customers_view),
) -> PlatformWorkspacePageRead:
    svc = PlatformService(db)
    items = await svc.list_platform_workspaces(
        limit=limit,
        offset=offset,
        search=search,
        status_filter=status_filter,
    )
    total = await svc.count_platform_workspaces(
        search=search,
        status_filter=status_filter,
    )
    return PlatformWorkspacePageRead(
        items=[PlatformWorkspaceRead(**item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
        sort=[
            {"field": "createdAt", "direction": "desc"},
            {"field": "id", "direction": "desc"},
        ],
    )


@router.get("/token-usage", summary="Uso de tokens e custo interno de IA")
async def get_token_usage(
    days: int = Query(default=30, ge=1, le=365),
    workspace_id: UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_root),
) -> dict[str, Any]:
    svc = PlatformService(db)
    return await svc.get_token_usage_summary(days=days, workspace_id=workspace_id)


@router.get("/meta-health", summary="Saúde Meta e funil por workspace (Super Admin)")
async def get_meta_health(
    days: int = Query(default=7, ge=1, le=90),
    workspace_id: UUID | None = Query(default=None, alias="workspaceId"),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_root),
) -> dict[str, Any]:
    """
    Painel de auditoria para o titular da plataforma: campanhas pausadas por
    política (kill switch/erro Meta) com motivo legível, top códigos de erro
    Meta, funil de aquisição (24h/7d) e supressões (opt-out) por motivo.
    """
    svc = PlatformService(db)
    return await svc.get_meta_health(days=days, workspace_id=workspace_id, limit=limit)


@router.get(
    "/ai-costs",
    response_model=WorkspaceAiCostSummaryRead,
    summary="Custo de IA global ou por workspace",
)
async def get_platform_ai_costs(
    days: int = Query(default=30, ge=1, le=365),
    workspace_id: UUID | None = Query(default=None, alias="workspaceId"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_root),
) -> WorkspaceAiCostSummaryRead:
    try:
        return await AiCostService(db).get_summary(
            workspace_id=workspace_id,
            days=days,
        )
    except AiCostWorkspaceNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "AI-COST-001", "message": str(error)},
        ) from error


@router.patch(
    "/ai-costs/{workspace_id}",
    response_model=WorkspaceAiCostSummaryRead,
    summary="Atualizar orçamento de IA de um workspace",
)
async def update_platform_ai_costs(
    workspace_id: UUID,
    payload: WorkspaceAiCostPolicyUpdate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_root),
) -> WorkspaceAiCostSummaryRead:
    try:
        return await AiCostService(db).update_policy(
            workspace_id=workspace_id,
            payload=payload,
            actor_user_id=actor.id,
        )
    except AiCostWorkspaceNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "AI-COST-001", "message": str(error)},
        ) from error


@router.get(
    "/ai-models",
    response_model=PlatformAiModelsOverviewRead,
    summary="Defaults globais de modelos por classe e workspaces com chave própria",
)
async def get_platform_ai_models(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_root),
) -> PlatformAiModelsOverviewRead:
    """Visão do super admin — as chaves BYOK aparecem SEMPRE redigidas."""
    return await build_platform_ai_models_overview(db)


def _raise_ai_token_package_error(error: Exception) -> None:
    if isinstance(error, AiTokenPackageNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "AI-TOKEN-PACKAGE-001", "message": str(error)},
        ) from error
    if isinstance(error, AiTokenPackageConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "AI-TOKEN-PACKAGE-002", "message": str(error)},
        ) from error
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "AI-TOKEN-PACKAGE-003", "message": str(error)},
    ) from error


@router.get(
    "/ai-token-packages",
    response_model=list[AiTokenPackageRead],
    summary="Catálogo comercial de pacotes extras de tokens",
)
async def list_platform_ai_token_packages(
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_support_view),
) -> list[AiTokenPackageRead]:
    return await AiTokenPackageService(db).list_catalog(include_inactive=is_platform_root(actor))


@router.put(
    "/ai-token-packages/{package_key}",
    response_model=AiTokenPackageRead,
    summary="Publicar ou atualizar pacote extra de tokens",
)
async def upsert_platform_ai_token_package(
    package_key: str,
    payload: AiTokenPackageUpsert,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_root),
    context: AuthContext = Depends(get_current_auth_context),
) -> AiTokenPackageRead:
    try:
        normalized_key = package_key.strip().lower()
        existing = await db.scalar(
            select(AiTokenPackage).where(AiTokenPackage.key == normalized_key)
        )
        before = _ai_token_package_snapshot(existing) if existing is not None else None
        package = await AiTokenPackageService(db).upsert_catalog_item(
            key=package_key,
            payload=payload,
        )
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=_ai_token_package_snapshot(package),
            module="ai",
            action="ai_token_package.upsert",
            reason=f"Pacote comercial de tokens {normalized_key} publicado ou atualizado.",
            reason_source="system",
        )
        return package
    except AiTokenPackageConflictError as error:
        _raise_ai_token_package_error(error)


@router.get(
    "/workspaces/{workspace_id}/ai-token-package-requests",
    response_model=list[WorkspaceAiTokenPackageRequestRead],
    summary="Solicitações de tokens de um workspace",
    deprecated=True,
)
async def list_platform_workspace_ai_token_package_requests(
    workspace_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_support_view),
) -> list[WorkspaceAiTokenPackageRequestRead]:
    try:
        return await AiTokenPackageService(db).list_workspace_requests(workspace_id=workspace_id)
    except AiTokenPackageNotFoundError as error:
        _raise_ai_token_package_error(error)


@router.get(
    "/workspaces/{workspace_id}/ai-token-package-requests/page",
    response_model=WorkspaceAiTokenPackageRequestPageRead,
    summary="Solicitações paginadas de tokens de uma conta",
)
async def page_platform_workspace_ai_token_package_requests(
    workspace_id: UUID,
    request_status: AiTokenPackageRequestStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_support_view),
) -> WorkspaceAiTokenPackageRequestPageRead:
    try:
        items, total = await AiTokenPackageService(db).page_workspace_requests(
            workspace_id=workspace_id,
            limit=limit,
            offset=offset,
            status=request_status,
        )
        return WorkspaceAiTokenPackageRequestPageRead(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(items) < total,
        )
    except AiTokenPackageNotFoundError as error:
        _raise_ai_token_package_error(error)


@router.post(
    "/workspaces/{workspace_id}/ai-token-package-requests/{request_id}/decision",
    response_model=WorkspaceAiTokenPackageRequestRead,
    summary="Aprovar ou recusar pacote extra de tokens",
)
async def decide_platform_workspace_ai_token_package_request(
    workspace_id: UUID,
    request_id: UUID,
    payload: AiTokenPackageDecision,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_support_assign),
    context: AuthContext = Depends(get_current_auth_context),
) -> WorkspaceAiTokenPackageRequestRead:
    try:
        await set_workspace_context(db, workspace_id)
        before_row = await db.scalar(
            select(WorkspaceAiTokenPackageRequest)
            .where(
                WorkspaceAiTokenPackageRequest.id == request_id,
                WorkspaceAiTokenPackageRequest.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        before = _ai_token_package_request_snapshot(before_row)
        result = await AiTokenPackageService(db).decide_request(
            workspace_id=workspace_id,
            request_id=request_id,
            actor_user_id=actor.id,
            decision=AiTokenPackageRequestStatus(payload.decision),
            decision_note=payload.decision_note,
        )
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=_ai_token_package_request_snapshot(result),
            module="ai_operations",
            action=f"ai_token_package_request.{payload.decision}",
            reason=payload.decision_note,
        )
        return result
    except (
        AiTokenPackageNotFoundError,
        AiTokenPackageTransitionError,
    ) as error:
        _raise_ai_token_package_error(error)


@router.post(
    "/workspaces/{workspace_id}/ai-token-package-requests/{request_id}/revoke",
    response_model=WorkspaceAiTokenPackageRequestRead,
    summary="Revogar pacote extra de tokens previamente liberado",
)
async def revoke_platform_workspace_ai_token_package_request(
    workspace_id: UUID,
    request_id: UUID,
    payload: AiTokenPackageRevoke,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_support_assign),
    context: AuthContext = Depends(get_current_auth_context),
) -> WorkspaceAiTokenPackageRequestRead:
    try:
        await set_workspace_context(db, workspace_id)
        before_row = await db.scalar(
            select(WorkspaceAiTokenPackageRequest)
            .where(
                WorkspaceAiTokenPackageRequest.id == request_id,
                WorkspaceAiTokenPackageRequest.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        before = _ai_token_package_request_snapshot(before_row)
        result = await AiTokenPackageService(db).revoke_request(
            workspace_id=workspace_id,
            request_id=request_id,
            actor_user_id=actor.id,
            decision_note=payload.decision_note,
        )
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=_ai_token_package_request_snapshot(result),
            module="ai_operations",
            action="ai_token_package_request.revoked",
            reason=payload.decision_note,
        )
        return result
    except (
        AiTokenPackageNotFoundError,
        AiTokenPackageTransitionError,
    ) as error:
        _raise_ai_token_package_error(error)


@router.post(
    "/workspaces/{workspace_id}/finance-harmonization-quota/bonus",
    response_model=FinanceHarmonizationQuotaRead,
    summary="Conceder bônus extra de verificações completas de harmonização (F14) a um workspace",
)
async def grant_finance_harmonization_bonus(
    workspace_id: UUID,
    payload: FinanceHarmonizationBonusGrantWrite,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_finance_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> FinanceHarmonizationQuotaRead:
    await set_workspace_context(db, workspace_id)
    service = FinanceHarmonizationService(db)
    before = await snapshot_platform_finance_harmonization_quota(
        db, workspace_id, payload.competence, for_update=True
    )
    await service.admin_grant_bonus(
        workspace_id,
        competence=payload.competence,
        extra=payload.extra,
        granted_by=actor.id,
    )
    status_payload = await service.get_quota_status(
        workspace_id, today=date.fromisoformat(f"{payload.competence}-01")
    )
    after = await snapshot_platform_finance_harmonization_quota(
        db, workspace_id, payload.competence
    )
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="billing",
        action="grant_finance_harmonization_bonus",
        reason=payload.reason,
    )
    return FinanceHarmonizationQuotaRead(**status_payload)


@router.get("/aws-costs", summary="Custos AWS via Cost Explorer")
async def get_aws_costs(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_root),
) -> dict[str, Any]:
    svc = PlatformService(db)
    return await svc.get_aws_cost_estimate()


@router.get(
    "/plans", response_model=list[WorkspacePlanCatalogItemRead], summary="Catálogo de planos do MVP"
)
async def list_platform_plans(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_plans_view),
) -> list[WorkspacePlanCatalogItemRead]:
    return await PlatformService(db).list_plan_catalog()


@router.put(
    "/plans/{plan_key}",
    response_model=WorkspacePlanCatalogItemRead,
    summary="Atualizar catálogo de planos",
)
async def update_platform_plan(
    plan_key: str,
    payload: WorkspacePlanCatalogItemUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_plans_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> WorkspacePlanCatalogItemRead:
    before = await snapshot_platform_request_resource(request, db)
    try:
        result = await PlatformService(db).upsert_plan_catalog_item(
            plan_key,
            payload.model_dump(exclude_unset=True),
        )
    except LegacyPlanEditorDisabledError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "PLAN-LEGACY-EDITOR-DISABLED",
                "message": str(error),
            },
        ) from error
    except InvalidPlatformPlanKeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PLAN-CATALOG-NOT-FOUND", "message": str(error)},
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "PLAN-CATALOG-INVALID", "message": str(error)},
        ) from error
    after = await snapshot_platform_request_resource(request, db)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="plans",
        action="update_platform_plan",
        reason=f"Atualização administrativa do catálogo do plano {plan_key}.",
        reason_source="system",
    )
    return result


@router.post(
    "/workspaces",
    response_model=PlatformWorkspaceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar workspace pela central da plataforma",
)
async def create_platform_workspace(
    payload: PlatformWorkspaceCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_customers_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformWorkspaceRead:
    result = await PlatformService(db).create_platform_workspace(
        payload.model_dump(),
        commit=False,
        actor_user_id=actor.id,
    )
    workspace = await db.scalar(
        select(Workspace)
        .options(selectinload(Workspace.subscription))
        .where(Workspace.id == result["id"])
    )
    after = (
        (
            "workspace",
            str(result["id"]),
            snapshot_platform_workspace(workspace),
        )
        if workspace
        else None
    )
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=None,
        after_snapshot=after,
        module="customers",
        action="create_platform_workspace",
        reason="Criação administrativa de conta no SaaS.",
        reason_source="system",
    )
    return result


@router.patch(
    "/workspaces/{workspace_id}",
    response_model=PlatformWorkspaceRead,
    summary="Aprovar, orientar ou ajustar workspace pela central da plataforma",
)
async def update_platform_workspace(
    workspace_id: UUID,
    payload: PlatformWorkspaceUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_customers_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformWorkspaceRead:
    before = await snapshot_platform_request_resource(request, db)
    result = await PlatformService(db).update_platform_workspace(
        workspace_id,
        payload.model_dump(exclude_unset=True, exclude={"reason"}),
        commit=False,
        actor_user_id=actor.id,
    )
    after = await snapshot_platform_request_resource(request, db)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="customers",
        action="update_platform_workspace",
        reason=payload.reason,
    )
    return result


@router.post(
    "/workspace-bulk-actions",
    response_model=PlatformWorkspaceBulkActionRead,
    summary="Compatibilidade descontinuada do lote síncrono de contas",
    deprecated=True,
)
async def bulk_update_platform_workspaces(
    payload: PlatformWorkspaceBulkAction,
    actor: User = Depends(require_platform_root),
) -> PlatformWorkspaceBulkActionRead:
    del payload, actor
    raise HTTPException(
        status_code=status.HTTP_428_PRECONDITION_REQUIRED,
        detail={
            "code": "PLATFORM-BULK-PREVIEW-REQUIRED",
            "message": (
                "O lote síncrono foi descontinuado. Crie uma seleção em "
                "/api/v1/platform/operations/selections, gere a prévia e execute o job persistido."
            ),
        },
    )


@router.get(
    "/staff/packages",
    response_model=list[PlatformRolePackageRead],
    summary="Pacotes de acesso da equipe interna do SaaS",
)
async def list_platform_staff_packages(
    _: User = Depends(_staff_view),
) -> list[PlatformRolePackageRead]:
    return PlatformStaffService.package_catalog()


@router.get(
    "/staff",
    response_model=list[PlatformStaffRead],
    summary="Listar funcionários internos do SaaS",
    deprecated=True,
)
async def list_platform_staff(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_staff_view),
) -> list[PlatformStaffRead]:
    return await PlatformStaffService(db).list()


@router.get(
    "/staff/page",
    response_model=PlatformStaffPageRead,
    summary="Lista paginada de funcionários com total real",
)
async def list_platform_staff_page(
    search: str | None = Query(default=None),
    role_package: str | None = Query(default=None, alias="rolePackage"),
    access_active: bool | None = Query(default=None, alias="accessActive"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_staff_view),
) -> PlatformStaffPageRead:
    return await PlatformStaffService(db).list_page(
        search=search,
        role_package=role_package,
        access_active=access_active,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/staff",
    response_model=PlatformStaffRead,
    status_code=status.HTTP_201_CREATED,
    summary="Criar acesso de funcionário do SaaS",
)
async def create_platform_staff(
    payload: PlatformStaffCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_staff_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformStaffRead:
    if payload.temporary_password is not None and not is_platform_root(actor):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PLATFORM-ROOT-REQUIRED",
                "message": "Somente o titular da plataforma pode emitir senha provisória.",
            },
        )
    result = await PlatformStaffService(db).create(payload, actor=actor)
    user = await db.get(User, result.id)
    after = ("staff", str(result.id), snapshot_platform_user(user)) if user else None
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=None,
        after_snapshot=after,
        module="staff",
        action="create_platform_staff",
        reason="Criação ou ativação de acesso interno do SaaS.",
        reason_source="system",
    )
    return result


@router.patch(
    "/staff/{user_id}",
    response_model=PlatformStaffRead,
    summary="Atualizar pacote, exceções ou suspensão de funcionário",
)
async def update_platform_staff(
    user_id: UUID,
    payload: PlatformStaffUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_staff_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformStaffRead:
    before = await snapshot_platform_request_resource(request, db)
    result = await PlatformStaffService(db).update(user_id, payload, actor=actor)
    after = await snapshot_platform_request_resource(request, db)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="staff",
        action="update_platform_staff",
        reason=payload.reason,
    )
    return result


@router.post(
    "/staff/{user_id}/temporary-password",
    response_model=PlatformTemporaryPasswordRead,
    summary="Definir senha provisória de funcionário como titular da plataforma",
)
async def set_platform_staff_temporary_password(
    user_id: UUID,
    payload: PlatformTemporaryPasswordSet,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_root),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformTemporaryPasswordRead:
    before = await snapshot_platform_request_resource(request, db)
    result = await PlatformStaffService(db).set_temporary_password(
        user_id,
        payload.temporary_password,
        actor=actor,
        staff_only=True,
    )
    after = await snapshot_platform_request_resource(request, db)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="staff",
        action="set_temporary_password",
        reason=payload.reason,
    )
    return result


@router.post(
    "/staff/{user_id}/sessions/revoke", summary="Encerrar todas as sessões de um funcionário"
)
async def revoke_platform_staff_sessions(
    user_id: UUID,
    payload: PlatformStaffSessionRevoke,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_staff_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> dict[str, int | bool]:
    before = await snapshot_platform_request_resource(request, db)
    revoked = await PlatformStaffService(db).revoke_sessions(user_id, actor=actor)
    after = await snapshot_platform_request_resource(request, db)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="staff",
        action="revoke_platform_staff_sessions",
        reason=payload.reason,
    )
    return {"ok": True, "revokedCount": revoked}


def _platform_config_error(exc: Exception) -> None:
    if isinstance(exc, ConfigConflictError):
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"code": "CFG-001", "message": str(exc)},
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "CFG-002", "message": str(exc)},
    ) from exc


@router.get(
    "/configurations/{namespace}",
    response_model=PlatformConfigurationDocumentRead,
    summary="Ler configuração global versionada",
)
async def get_platform_configuration(
    namespace: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_settings_view),
):
    document = await db.scalar(
        select(PlatformConfigDocument).where(PlatformConfigDocument.namespace == namespace)
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CFG-003", "message": "Configuração global não encontrada."},
        )
    response.headers["ETag"] = f'"{document.etag}"'
    return document


@router.put(
    "/configurations/{namespace}",
    response_model=PlatformConfigurationDocumentRead,
    summary="Criar ou atualizar configuração global",
)
async def put_platform_configuration(
    namespace: str,
    payload: PlatformConfigurationDocumentWrite,
    response: Response,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_settings_manage),
    if_match: str | None = Header(default=None, alias="If-Match"),
):
    try:
        document = await ConfigurationService(db).write_platform(
            namespace=namespace,
            payload=payload.payload,
            actor_user_id=actor.id,
            schema_version=payload.schema_version,
            expected_etag=if_match,
            reason=payload.reason,
        )
    except (ConfigConflictError, ConfigValidationError) as exc:
        _platform_config_error(exc)
    response.headers["ETag"] = f'"{document.etag}"'
    return document


@router.get(
    "/configurations/{namespace}/versions",
    response_model=list[ConfigVersionRead],
    summary="Histórico da configuração global",
    deprecated=True,
)
async def list_platform_configuration_versions(
    namespace: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_settings_view),
):
    document = await db.scalar(
        select(PlatformConfigDocument).where(PlatformConfigDocument.namespace == namespace)
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return await ConfigurationService(db).platform_history(document.id)


@router.get(
    "/configurations/{namespace}/versions/page",
    response_model=ConfigVersionPageRead,
    summary="Histórico paginado da configuração global",
)
async def page_platform_configuration_versions(
    namespace: str,
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_settings_view),
):
    document = await db.scalar(
        select(PlatformConfigDocument).where(PlatformConfigDocument.namespace == namespace)
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    items, total = await ConfigurationService(db).page_platform_history(
        document.id,
        limit=limit,
        offset=offset,
    )
    return ConfigVersionPageRead(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
    )


@router.post(
    "/configurations/{namespace}/rollback/{version}",
    response_model=PlatformConfigurationDocumentRead,
    summary="Restaurar versão global como nova revisão",
)
async def rollback_platform_configuration(
    namespace: str,
    version: int,
    response: Response,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_settings_manage),
    if_match: str | None = Header(default=None, alias="If-Match"),
):
    document = await db.scalar(
        select(PlatformConfigDocument).where(PlatformConfigDocument.namespace == namespace)
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    try:
        rolled_back = await ConfigurationService(db).rollback_platform(
            document=document,
            target_version=version,
            actor_user_id=actor.id,
            expected_etag=if_match,
        )
    except (ConfigConflictError, ConfigValidationError) as exc:
        _platform_config_error(exc)
    response.headers["ETag"] = f'"{rolled_back.etag}"'
    return rolled_back


# ── B1: Centro de Comando — Financeiro / Pessoas / Workspace 360 ─────────


def _raise_subscription_error(exc: Exception) -> None:
    if isinstance(exc, SubscriptionNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SUB-001", "message": str(exc)},
        ) from exc
    if isinstance(exc, CanonicalCommercialContractConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "SUB-CANONICAL-CONFLICT", "message": str(exc)},
        ) from exc
    if isinstance(exc, InvalidSubscriptionActionError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "SUB-002", "message": str(exc)},
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": "SUB-003", "message": str(exc)},
    ) from exc


@router.get("/metrics", summary="Bateria completa de KPIs do Super Admin, agrupados por categoria")
async def get_platform_metrics(
    period_days: int = Query(default=30, ge=1, le=365, alias="periodDays"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_reports_view),
) -> dict[str, Any]:
    payload = await PlatformMetricsService(db).get_all_metrics(period_days=period_days)
    return _filter_metric_payload(payload, actor)


@router.get(
    "/metrics/{category}",
    summary=(
        "KPIs de uma categoria específica "
        "(receita, assinantes, uso_engajamento, aquisicao, operacao_ia, saude)"
    ),
)
async def get_platform_metrics_category(
    category: str,
    period_days: int = Query(default=30, ge=1, le=365, alias="periodDays"),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_reports_view),
) -> dict[str, Any]:
    normalized_category = LEGACY_CATEGORY_ALIASES.get(category, category)
    if normalized_category not in CATEGORIES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "METRICS-001", "message": f"Categoria desconhecida: {category}"},
        )
    if normalized_category not in _allowed_metric_categories(actor):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PLATFORM-METRIC-CATEGORY-DENIED",
                "message": "Sua função não autoriza esta categoria de indicadores.",
                "category": normalized_category,
            },
        )
    payload = await PlatformMetricsService(db).get_category_metrics(
        normalized_category,
        period_days=period_days,
    )
    if not is_platform_root(actor):
        kpis = [
            item
            for item in payload.get("kpis", [])
            if not str(item.get("key", "")).startswith(_ROOT_ONLY_KPI_PREFIXES)
        ]
        payload = {**payload, "count": len(kpis), "kpis": kpis}
    return payload


@router.get("/billing/overview", summary="MRR/ARR/churn/LTV + série (Centro de Comando)")
async def get_billing_overview(
    period_days: int = Query(default=30, ge=1, le=365, alias="periodDays"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_finance_view),
) -> dict[str, Any]:
    return await PlatformBillingService(db).get_overview(period_days=period_days)


@router.get("/subscriptions", summary="Listar assinaturas por workspace com filtros")
async def list_subscriptions(
    plano: str | None = Query(default=None),
    status_: str | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_finance_view),
) -> dict[str, Any]:
    return await PlatformBillingService(db).list_subscriptions(
        plano=plano, status=status_, search=search, limit=limit, offset=offset
    )


class SubscriptionChangePlanRequest(BaseModel):
    novo_plano: str = Field(..., alias="novoPlano")
    motivo: str = Field(min_length=8, max_length=500)
    valor_mensal_cents: int | None = Field(default=None, alias="valorMensalCents")

    class Config:
        populate_by_name = True


class SubscriptionDiscountRequest(BaseModel):
    desconto_pct: Decimal = Field(default=Decimal("0"), alias="descontoPct")
    cortesia: bool = False
    motivo: str = Field(min_length=8, max_length=500)

    class Config:
        populate_by_name = True


class SubscriptionExtendTrialRequest(BaseModel):
    dias: int
    motivo: str = Field(min_length=8, max_length=500)


class SubscriptionActionRequest(BaseModel):
    motivo: str = Field(min_length=8, max_length=500)


@router.patch(
    "/subscriptions/{workspace_id}/plan",
    summary="Mudar o plano de uma assinatura (auditado)",
)
async def change_subscription_plan(
    workspace_id: UUID,
    payload: SubscriptionChangePlanRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_finance_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> dict[str, Any]:
    try:
        before = await snapshot_platform_request_resource(request, db)
        result = await PlatformBillingService(db).change_plan(
            workspace_id,
            novo_plano=payload.novo_plano,
            actor=actor,
            motivo=payload.motivo,
            valor_mensal_cents=payload.valor_mensal_cents,
        )
        after = await snapshot_platform_request_resource(request, db)
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=after,
            module="finance",
            action="change_subscription_plan",
            reason=payload.motivo,
        )
        return result
    except (SubscriptionNotFoundError, InvalidSubscriptionActionError) as exc:
        await db.rollback()
        _raise_subscription_error(exc)


@router.patch(
    "/subscriptions/{workspace_id}/discount",
    summary="Aplicar desconto/cortesia (auditado)",
)
async def apply_subscription_discount(
    workspace_id: UUID,
    payload: SubscriptionDiscountRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_finance_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> dict[str, Any]:
    try:
        before = await snapshot_platform_request_resource(request, db)
        result = await PlatformBillingService(db).apply_discount(
            workspace_id,
            desconto_pct=payload.desconto_pct,
            cortesia=payload.cortesia,
            actor=actor,
            motivo=payload.motivo,
        )
        after = await snapshot_platform_request_resource(request, db)
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=after,
            module="finance",
            action="apply_subscription_discount",
            reason=payload.motivo,
        )
        return result
    except (SubscriptionNotFoundError, InvalidSubscriptionActionError) as exc:
        await db.rollback()
        _raise_subscription_error(exc)


@router.patch(
    "/subscriptions/{workspace_id}/extend-trial",
    summary="Estender trial (auditado)",
)
async def extend_subscription_trial(
    workspace_id: UUID,
    payload: SubscriptionExtendTrialRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_finance_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> dict[str, Any]:
    try:
        before = await snapshot_platform_request_resource(request, db)
        result = await PlatformBillingService(db).extend_trial(
            workspace_id, dias=payload.dias, actor=actor, motivo=payload.motivo
        )
        after = await snapshot_platform_request_resource(request, db)
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=after,
            module="finance",
            action="extend_subscription_trial",
            reason=payload.motivo,
        )
        return result
    except (SubscriptionNotFoundError, InvalidSubscriptionActionError) as exc:
        await db.rollback()
        _raise_subscription_error(exc)


@router.patch(
    "/subscriptions/{workspace_id}/suspend",
    summary="Suspender assinatura (auditado)",
)
async def suspend_subscription(
    workspace_id: UUID,
    payload: SubscriptionActionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_finance_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> dict[str, Any]:
    try:
        before = await snapshot_platform_request_resource(request, db)
        result = await PlatformBillingService(db).suspend(
            workspace_id, actor=actor, motivo=payload.motivo
        )
        after = await snapshot_platform_request_resource(request, db)
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=after,
            module="finance",
            action="suspend_subscription",
            reason=payload.motivo,
        )
        return result
    except (SubscriptionNotFoundError, InvalidSubscriptionActionError) as exc:
        await db.rollback()
        _raise_subscription_error(exc)


@router.patch(
    "/subscriptions/{workspace_id}/reactivate",
    summary="Reativar assinatura pausada (auditado)",
)
async def reactivate_subscription(
    workspace_id: UUID,
    payload: SubscriptionActionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_finance_manage),
    context: AuthContext = Depends(get_current_auth_context),
) -> dict[str, Any]:
    try:
        before = await snapshot_platform_request_resource(request, db)
        result = await PlatformBillingService(db).reactivate(
            workspace_id, actor=actor, motivo=payload.motivo
        )
        after = await snapshot_platform_request_resource(request, db)
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=after,
            module="finance",
            action="reactivate_subscription",
            reason=payload.motivo,
        )
        return result
    except (SubscriptionNotFoundError, InvalidSubscriptionActionError) as exc:
        await db.rollback()
        _raise_subscription_error(exc)


@router.get(
    "/subscriptions/{workspace_id}/events",
    summary="Histórico de auditoria de uma assinatura",
    deprecated=True,
)
async def list_subscription_events(
    workspace_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_finance_view),
) -> list[dict[str, Any]]:
    try:
        return await PlatformBillingService(db).list_events(workspace_id, limit=limit)
    except SubscriptionNotFoundError as exc:
        _raise_subscription_error(exc)


@router.get(
    "/subscriptions/{workspace_id}/events/page",
    response_model=PlatformSubscriptionEventPageRead,
    summary="Histórico paginado de auditoria de uma assinatura",
)
async def page_subscription_events(
    workspace_id: UUID,
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_finance_view),
) -> PlatformSubscriptionEventPageRead:
    try:
        page = await PlatformBillingService(db).page_events(
            workspace_id,
            limit=limit,
            offset=offset,
        )
        return PlatformSubscriptionEventPageRead.model_validate(page)
    except SubscriptionNotFoundError as exc:
        _raise_subscription_error(exc)


@router.get(
    "/workspace-360/{workspace_id}",
    summary="Drawer consolidado do workspace: assinatura + consumo + saúde Meta + usuários",
)
async def get_workspace_360(
    workspace_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_customers_view),
) -> dict[str, Any]:
    svc = PlatformService(db)
    try:
        return await svc.get_workspace_360(workspace_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "WS-360-001", "message": str(exc)},
        ) from exc


@router.get("/people", summary="Todos os usuários de todos os workspaces, com busca")
async def list_people(
    search: str | None = Query(default=None),
    role: str | None = Query(default=None),
    workspace_id: UUID | None = Query(default=None, alias="workspaceId"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_customers_view),
) -> dict[str, Any]:
    return await PlatformService(db).list_people(
        search=search, role=role, workspace_id=workspace_id, limit=limit, offset=offset
    )


@router.get(
    "/people/access-packages",
    response_model=list[WorkspaceAccessPackageRead],
    summary="Catálogo de pacotes de acesso aplicáveis aos usuários das contas",
)
async def list_people_access_packages(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_customers_view),
) -> list[WorkspaceAccessPackageRead]:
    return WorkspaceService(db).list_access_package_catalog()


class PersonBlockRequest(BaseModel):
    blocked: bool
    reason: str = Field(..., min_length=8, max_length=500)


class PersonInvitationRequest(BaseModel):
    workspace_id: UUID = Field(..., alias="workspaceId")
    name: str = Field(..., min_length=2, max_length=200)
    email: CanonicalEmail
    access_package: str = Field(..., alias="accessPackage", min_length=2, max_length=80)

    class Config:
        populate_by_name = True


@router.post(
    "/people/{workspace_id}/{user_id}/temporary-password",
    response_model=PlatformTemporaryPasswordRead,
    summary="Definir senha provisória de usuário de conta como titular da plataforma",
)
async def set_platform_person_temporary_password(
    workspace_id: UUID,
    user_id: UUID,
    payload: PlatformTemporaryPasswordSet,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_root),
    context: AuthContext = Depends(get_current_auth_context),
) -> PlatformTemporaryPasswordRead:
    user = await db.get(User, user_id)
    before = ("user", str(user_id), snapshot_platform_user(user)) if user else None
    result = await PlatformStaffService(db).set_temporary_password(
        user_id,
        payload.temporary_password,
        actor=actor,
        workspace_id=workspace_id,
    )
    user = await db.get(User, user_id)
    after = ("user", str(user_id), snapshot_platform_user(user)) if user else None
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="customers",
        action="set_temporary_password",
        reason=payload.reason,
    )
    return result


@router.post(
    "/people/invitations",
    status_code=status.HTTP_201_CREATED,
    summary="Convidar usuário para uma conta como Super Admin",
)
async def invite_person(
    payload: PersonInvitationRequest,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=200),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_root),
    context: AuthContext = Depends(get_current_auth_context),
) -> dict[str, Any]:
    await set_workspace_context(db, payload.workspace_id)
    try:
        result = await PlatformService(db).invite_person(
            workspace_id=payload.workspace_id,
            name=payload.name,
            email=payload.email,
            access_package=payload.access_package,
            actor=actor,
            idempotency_key=idempotency_key,
        )
        after = await snapshot_platform_invitation(db, UUID(str(result["id"])))
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=None,
            after_snapshot=after,
            module="customers",
            action="invite_person",
            reason="Convite administrativo de usuário para uma conta.",
            reason_source="system",
        )
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "PEOPLE-INVITE-001", "message": str(exc)},
        ) from exc


@router.post(
    "/people/invitations/{workspace_id}/{invitation_id}/resend",
    summary="Reenviar convite de usuário como Super Admin",
)
async def resend_person_invitation(
    workspace_id: UUID,
    invitation_id: UUID,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=200),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_root),
    context: AuthContext = Depends(get_current_auth_context),
) -> dict[str, Any]:
    await set_workspace_context(db, workspace_id)
    before = await snapshot_platform_invitation(db, invitation_id, for_update=True)
    result = await PlatformService(db).resend_person_invitation(
        workspace_id=workspace_id,
        invitation_id=invitation_id,
        actor=actor,
        idempotency_key=idempotency_key,
    )
    after = await snapshot_platform_invitation(db, invitation_id)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="customers",
        action="resend_person_invitation",
        reason="Reenvio administrativo de convite pendente.",
        reason_source="system",
    )
    return result


@router.delete(
    "/people/invitations/{workspace_id}/{invitation_id}",
    summary="Cancelar convite de usuário como Super Admin",
)
async def revoke_person_invitation(
    workspace_id: UUID,
    invitation_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_root),
    context: AuthContext = Depends(get_current_auth_context),
) -> dict[str, Any]:
    await set_workspace_context(db, workspace_id)
    before = await snapshot_platform_invitation(db, invitation_id, for_update=True)
    result = await PlatformService(db).revoke_person_invitation(
        workspace_id=workspace_id,
        invitation_id=invitation_id,
        actor=actor,
    )
    after = await snapshot_platform_invitation(db, invitation_id)
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=before,
        after_snapshot=after,
        module="customers",
        action="revoke_person_invitation",
        reason="Cancelamento administrativo de convite pendente.",
        reason_source="system",
    )
    return result


class PersonAccessPackageRequest(BaseModel):
    access_package: str = Field(..., alias="accessPackage")
    reason: str = Field(..., min_length=8, max_length=500)

    class Config:
        populate_by_name = True


class PersonOwnershipTransferRequest(BaseModel):
    reason: str = Field(..., min_length=8, max_length=500)


class PersonSessionRevokeRequest(BaseModel):
    reason: str = Field(..., min_length=8, max_length=500)


@router.patch(
    "/people/{workspace_id}/{user_id}/block",
    summary="Bloquear ou desbloquear o acesso de um usuário a um workspace",
)
async def set_person_blocked(
    workspace_id: UUID,
    user_id: UUID,
    payload: PersonBlockRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_root),
    context: AuthContext = Depends(get_current_auth_context),
) -> dict[str, Any]:
    try:
        before = await snapshot_platform_membership(db, workspace_id, user_id, for_update=True)
        result = await PlatformService(db).set_person_blocked(
            user_id=user_id, workspace_id=workspace_id, blocked=payload.blocked, actor=actor
        )
        after = await snapshot_platform_membership(db, workspace_id, user_id)
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=after,
            module="customers",
            action="set_person_blocked",
            reason=payload.reason,
        )
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PEOPLE-001", "message": str(exc)},
        ) from exc


@router.patch(
    "/people/{workspace_id}/{user_id}/access-package",
    summary="Trocar o pacote de acesso de um usuário num workspace",
)
async def set_person_access_package(
    workspace_id: UUID,
    user_id: UUID,
    payload: PersonAccessPackageRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_root),
    context: AuthContext = Depends(get_current_auth_context),
) -> dict[str, Any]:
    try:
        before = await snapshot_platform_membership(db, workspace_id, user_id, for_update=True)
        result = await PlatformService(db).set_person_access_package(
            user_id=user_id,
            workspace_id=workspace_id,
            access_package=payload.access_package,
            actor=actor,
        )
        after = await snapshot_platform_membership(db, workspace_id, user_id)
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=after,
            module="customers",
            action="set_person_access_package",
            reason=payload.reason,
        )
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PEOPLE-002", "message": str(exc)},
        ) from exc


@router.post(
    "/people/{workspace_id}/{user_id}/sessions/revoke",
    summary="Encerrar todas as sessões de um usuário de conta",
)
async def revoke_person_sessions(
    workspace_id: UUID,
    user_id: UUID,
    payload: PersonSessionRevokeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_root),
    context: AuthContext = Depends(get_current_auth_context),
) -> dict[str, Any]:
    try:
        before = await snapshot_platform_membership(db, workspace_id, user_id, for_update=True)
        result = await PlatformService(db).revoke_person_sessions(
            user_id=user_id,
            workspace_id=workspace_id,
            actor=actor,
        )
        after = await snapshot_platform_membership(db, workspace_id, user_id)
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=after,
            module="customers",
            action="revoke_person_sessions",
            reason=payload.reason,
        )
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PEOPLE-003", "message": str(exc)},
        ) from exc


@router.post(
    "/people/{workspace_id}/{user_id}/transfer-ownership",
    summary="Transferir a titularidade de uma conta",
)
async def transfer_workspace_ownership(
    workspace_id: UUID,
    user_id: UUID,
    payload: PersonOwnershipTransferRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_root),
    context: AuthContext = Depends(get_current_auth_context),
) -> dict[str, Any]:
    try:
        before = await snapshot_platform_workspace_ownership(db, workspace_id, for_update=True)
        result = await PlatformService(db).transfer_workspace_ownership(
            user_id=user_id,
            workspace_id=workspace_id,
            actor=actor,
            reason=payload.reason.strip(),
        )
        after = await snapshot_platform_workspace_ownership(db, workspace_id)
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=after,
            module="customers",
            action="transfer_workspace_ownership",
            reason=payload.reason,
        )
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "PEOPLE-OWNERSHIP-001", "message": str(exc)},
        ) from exc


@router.get(
    "/ai-operations/summary",
    response_model=AiOperationsSummary,
    summary="Operação de IA: custo, rotas, fallback e alertas (todos os workspaces)",
)
async def get_ai_operations_summary(
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_ai_operations_view),
) -> AiOperationsSummary:
    data = await AiOperationsService(db).summary(workspace_id=None, days=days)
    return AiOperationsSummary.model_validate(data)


@router.get(
    "/ai-operations/events",
    response_model=AiOperationsEventPage,
    summary="Operação de IA: eventos do gateway (todos os workspaces)",
)
async def get_ai_operations_events(
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    only_fallbacks: bool = Query(False),
    agent_name: str | None = Query(None),
    provider: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_ai_operations_view),
) -> AiOperationsEventPage:
    data = await AiOperationsService(db).events(
        workspace_id=None,
        days=days,
        limit=limit,
        offset=offset,
        only_fallbacks=only_fallbacks,
        agent_name=agent_name,
        provider=provider,
    )
    return AiOperationsEventPage.model_validate(data)


# ── Overrides de entitlement por conta + Agregado 360 (Blackbox) ────────


@router.get(
    "/workspaces/{workspace_id}/overview",
    summary=(
        "Agregado 360 da conta: identificação, entitlements, uso, integrações, "
        "agentes, financeiro e auditoria"
    ),
)
async def get_workspace_overview(
    workspace_id: UUID,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_customers_view),
) -> dict[str, Any]:
    await set_workspace_context(db, workspace_id)
    try:
        return await WorkspaceOverviewService(db).get_overview(
            workspace_id,
            # A identidade root amplia o RBAC da plataforma, mas não substitui
            # uma sessão administrativa atribuída. Conteúdo tenant privado é
            # carregado somente depois de `root_direct`/`support_approved`.
            include_private=False,
        )
    except WorkspaceOverviewWorkspaceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "WS-OVERVIEW-001", "message": str(exc)},
        ) from exc


@router.get(
    "/workspaces/{workspace_id}/whatsapp/utility-template-library",
    response_model=UtilityTemplateCatalogRead,
    summary="Acompanhar no Blackbox a biblioteca UTILITY da WABA da conta",
)
async def platform_workspace_utility_template_library(
    workspace_id: UUID,
    connection_id: UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_customers_view),
):
    await set_workspace_context(db, workspace_id)
    return await WhatsAppService(db).list_utility_template_catalog(
        workspace_id, connection_id
    )


@router.post(
    "/workspaces/{workspace_id}/whatsapp/utility-template-library/{catalog_key}/retry",
    response_model=UtilityTemplateCatalogRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Reenfileirar no Blackbox um template UTILITY da conta",
    openapi_extra={
        "x-platform-mutation": {
            "key": "platform.workspace.utility_template.retry",
            "module": "customers",
            "action": "manage",
            "resourceType": "whatsapp_utility_template",
            "reasonPolicy": "system_allowed",
            "snapshotAdapter": "request_resource",
            "mutationKind": "command",
            "restorationAdapter": None,
            "idempotencyPolicy": "optional",
            "externalEffect": True,
            "auditSchemaVersion": 1,
        }
    },
)
async def platform_retry_workspace_utility_template(
    workspace_id: UUID,
    catalog_key: str,
    request: Request,
    connection_id: UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_customers_manage),
    context: AuthContext = Depends(get_current_auth_context),
):
    if catalog_key not in UTILITY_TEMPLATE_BY_KEY:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "WA-TPL-CATALOG-UNKNOWN",
                "message": "Template não pertence à biblioteca publicada.",
            },
        )
    await set_workspace_context(db, workspace_id)
    result = await WhatsAppService(db).retry_utility_template_catalog_item(
        workspace_id, catalog_key, connection_id
    )
    await record_canonical_platform_mutation(
        request,
        context,
        db,
        before_snapshot=None,
        after_snapshot=(
            "whatsapp_utility_template",
            f"{workspace_id}:{catalog_key}",
            {"workspaceId": str(workspace_id), "catalogKey": catalog_key, "state": "queued"},
        ),
        module="customers",
        action="manage",
        reason="Reenvio da biblioteca UTILITY solicitado pelo Blackbox.",
        reason_source="system",
    )
    await db.commit()
    return result


class EntitlementOverrideWriteItem(BaseModel):
    entitlement_key: str = Field(..., alias="entitlementKey")
    action: str
    mode: str | None = None
    quantity: int | None = Field(
        default=None,
        strict=True,
        ge=0,
        le=MAX_SAFE_ENTITLEMENT_QUANTITY,
    )
    reason: str

    class Config:
        populate_by_name = True


class EntitlementOverridesWriteRequest(BaseModel):
    overrides: list[EntitlementOverrideWriteItem]


def _serialize_override(row: Any) -> dict[str, Any]:
    return {
        "entitlement": row.entitlement.value,
        "mode": row.mode.value,
        "quantity": row.quantity,
        "reason": row.reason,
        "createdByUserId": str(row.created_by_user_id) if row.created_by_user_id else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.put(
    "/workspaces/{workspace_id}/entitlement-overrides",
    summary="Conceder, revogar, relimitar ou remover overrides de entitlement de uma conta",
)
async def put_workspace_entitlement_overrides(
    workspace_id: UUID,
    payload: EntitlementOverridesWriteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_platform_root),
    context: AuthContext = Depends(get_current_auth_context),
) -> dict[str, Any]:
    await set_workspace_context(db, workspace_id)
    service = WorkspaceEntitlementOverrideService(db)
    try:
        before = await snapshot_platform_entitlement_overrides(db, workspace_id, for_update=True)
        for item in payload.overrides:
            if item.action == "delete":
                await service.delete_override(
                    workspace_id=workspace_id,
                    entitlement_key=item.entitlement_key,
                    reason=item.reason,
                    actor_user_id=actor.id,
                )
            elif item.action == "upsert":
                if item.mode is None:
                    raise WorkspaceEntitlementOverrideValidationError(
                        "mode é obrigatório para action=upsert."
                    )
                await service.upsert_override(
                    workspace_id=workspace_id,
                    entitlement_key=item.entitlement_key,
                    mode=item.mode,
                    quantity=item.quantity,
                    reason=item.reason,
                    actor_user_id=actor.id,
                )
            else:
                raise WorkspaceEntitlementOverrideValidationError(
                    f"action inválida: {item.action!r}. Use upsert ou delete."
                )
        overrides = await service.list_overrides(workspace_id)
        after = await snapshot_platform_entitlement_overrides(db, workspace_id)
        reasons = list(dict.fromkeys(item.reason.strip() for item in payload.overrides))
        await record_canonical_platform_mutation(
            request,
            context,
            db,
            before_snapshot=before,
            after_snapshot=after,
            module="workspace_configuration",
            action="update_workspace_entitlement_overrides",
            reason=(
                "; ".join(reasons)
                if reasons
                else "Validação sem alteração do bundle de entitlements."
            )[:500],
            reason_source="user" if reasons else "system",
        )
    except WorkspaceEntitlementOverrideWorkspaceNotFoundError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "WS-ENT-OVR-001", "message": str(exc)},
        ) from exc
    except WorkspaceEntitlementOverrideNotFoundError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "WS-ENT-OVR-002", "message": str(exc)},
        ) from exc
    except WorkspaceEntitlementOverrideValidationError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "WS-ENT-OVR-003", "message": str(exc)},
        ) from exc
    return {"overrides": [_serialize_override(row) for row in overrides]}
