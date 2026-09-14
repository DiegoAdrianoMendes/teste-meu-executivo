"""Dimensional finance endpoints with tenant-scoped validation."""

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    AuthContext,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.models.agent_platform import HumanNotificationDelivery
from app.models.finance_dimensional import (
    FinanceCostCenter,
    FinanceTaxProfile,
    FinanceTaxReformAlert,
)
from app.models.klm_operations import KlmAuditEvent
from app.schemas.finance_dimensional import (
    AllocationRead,
    AllocationWrite,
    BudgetLineRead,
    BudgetLineWrite,
    CostCenterImpactPreview,
    CostCenterRead,
    CostCenterUpdate,
    CostCenterWrite,
    FinanceAnalyticsQuery,
    FinanceDreQuery,
    FinanceLookupResolve,
    ForecastGenerateWrite,
    ForecastRead,
    ForecastWrite,
    TaxProfileRead,
    TaxProfileWrite,
    TaxReformReviewRequest,
    TaxReformSimulationWrite,
    ValuationCalculateWrite,
    ValuationRead,
    ValuationWrite,
)
from app.services.agent_platform_service import HumanRoutingError, HumanRoutingService
from app.services.document_service import DocumentService
from app.services.finance_dimensional_service import (
    FinanceDimensionalService,
    FinanceDimensionValidationError,
)
from app.services.finance_tax_rules_service import (
    tax_profile_alerts,
    tax_reform_reminder_schedule,
    tax_rule_catalog,
)

router = APIRouter(prefix="/finance/dimensional", tags=["Financeiro — Dimensional"])


def view_deps(
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("finance", "view")),
    db: AsyncSession = Depends(get_db),
):
    return workspace_id, auth, db


def manage_deps(
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("finance", "manage")),
    db: AsyncSession = Depends(get_db),
):
    return workspace_id, auth, db


_SENSITIVE_AUDIT_KEYS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "cookie",
    "private_key",
    "recovery_code",
)


def _sanitize_audit_value(value):
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if any(marker in str(key).lower() for marker in _SENSITIVE_AUDIT_KEYS)
                else _sanitize_audit_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_audit_value(item) for item in value]
    return value


def _snapshot(obj) -> dict:
    fields = {
        "FinanceCostCenter": (
            "code",
            "name",
            "parent_id",
            "type",
            "owner_user_id",
            "status",
            "version",
        ),
        "FinanceAllocation": (
            "finance_entry_id",
            "cost_center_id",
            "client_id",
            "contract_id",
            "matter_id",
            "team_id",
            "user_id",
            "percentage",
            "amount",
            "source",
            "version",
        ),
        "FinanceForecast": (
            "period_start",
            "period_end",
            "revenue",
            "expense",
            "scenario",
            "horizon",
            "version",
        ),
        "FinanceValuation": (
            "method",
            "valuation_date",
            "enterprise_value",
            "premise_quality",
            "version",
        ),
        "FinanceTaxProfile": (
            "regime",
            "tax_rate",
            "effective_from",
            "municipality",
            "sources",
            "reform_calendar",
            "version",
        ),
        "FinanceBudgetLine": (
            "period_start",
            "period_end",
            "cost_center_id",
            "category",
            "entry_type",
            "amount",
            "assumptions",
            "status",
            "version",
        ),
    }[obj.__class__.__name__]
    return _sanitize_audit_value(
        {
            field: (
                value.isoformat()
                if hasattr(value, "isoformat")
                else str(value)
                if isinstance(value, (UUID, Decimal))
                else value
            )
            for field in fields
            if (value := getattr(obj, field, None)) is not None
        }
    )


async def _audit(d, obj, event: str, *, before: dict | None = None):
    ws, auth, db = d
    db.add(
        KlmAuditEvent(
            workspace_id=ws,
            actor_user_id=auth.user_id,
            actor_type="user",
            actor_label=str(auth.user_id),
            event_type=event,
            entity_type=obj.__class__.__name__,
            entity_id=obj.id,
            summary=event,
            before_data=before,
            after_data=_snapshot(obj),
        )
    )
    await db.flush()


def _validation_error(error: FinanceDimensionValidationError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))


@router.get("/cost-centers", response_model=list[CostCenterRead])
async def list_cost_centers(d=Depends(view_deps)):
    return await FinanceDimensionalService(d[2]).list_cost_centers(d[0])


@router.get("/lookups")
async def finance_lookup(
    entity_type: Literal["entry", "client", "matter", "contract", "team", "user"],
    search: str = Query(default="", max_length=120),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=200),
    d=Depends(view_deps),
):
    try:
        return await FinanceDimensionalService(d[2]).lookup(
            d[0], entity_type=entity_type, search=search, limit=limit, cursor=cursor
        )
    except FinanceDimensionValidationError as error:
        raise _validation_error(error) from error


@router.post("/lookups/resolve")
async def resolve_finance_lookup(payload: FinanceLookupResolve, d=Depends(view_deps)):
    try:
        return await FinanceDimensionalService(d[2]).resolve_lookups(
            d[0], entity_type=payload.entity_type, ids=payload.ids
        )
    except FinanceDimensionValidationError as error:
        raise _validation_error(error) from error


@router.get("/evidence/{reference}")
async def open_finance_evidence(reference: str, d=Depends(view_deps)):
    try:
        document_id = FinanceDimensionalService.resolve_evidence_ref(reference)
        result = await DocumentService(d[2]).create_download_url(
            document_id, d[0], actor_user_id=d[1].user_id
        )
        return {"downloadUrl": result.download_url, "expiresAt": result.expires_at}
    except Exception as error:
        raise HTTPException(
            status_code=404,
            detail="Comprovante indisponível ou não autorizado",
        ) from error


@router.get("/tax-profile", response_model=TaxProfileRead | None)
async def get_tax_profile(d=Depends(view_deps)):
    return await d[2].scalar(
        select(FinanceTaxProfile).where(FinanceTaxProfile.workspace_id == d[0])
    )


@router.get("/tax-rules")
async def get_tax_rules(d=Depends(view_deps)):
    profile = await d[2].scalar(
        select(FinanceTaxProfile).where(FinanceTaxProfile.workspace_id == d[0])
    )
    schedule = tax_reform_reminder_schedule(profile)
    alert_rows = list(
        (
            await d[2].scalars(
                select(FinanceTaxReformAlert).where(
                    FinanceTaxReformAlert.workspace_id == d[0]
                )
            )
        ).all()
    )
    by_schedule_key = {row.schedule_key: row for row in alert_rows}
    decision_ids = [row.human_decision_id for row in alert_rows if row.human_decision_id]
    owner_phone_by_decision = {}
    if decision_ids:
        owner_phone_rows = list(
            (
                await d[2].scalars(
                    select(HumanNotificationDelivery).where(
                        HumanNotificationDelivery.decision_id.in_(decision_ids),
                        HumanNotificationDelivery.recipient_kind == "owner",
                        HumanNotificationDelivery.channel == "phone",
                    )
                )
            ).all()
        )
        owner_phone_by_decision = {
            row.decision_id: str(getattr(row.status, "value", row.status))
            for row in owner_phone_rows
        }
    return {
        **tax_rule_catalog(),
        "alerts": tax_profile_alerts(profile),
        "reminderSchedule": [
            {
                **item,
                "deliveryState": (
                    by_schedule_key[item["scheduleKey"]].status
                    if item["scheduleKey"] in by_schedule_key
                    else "not_called"
                ),
                "humanDecisionId": (
                    str(by_schedule_key[item["scheduleKey"]].human_decision_id)
                    if item["scheduleKey"] in by_schedule_key
                    and by_schedule_key[item["scheduleKey"]].human_decision_id
                    else None
                ),
                "phoneDeliveryStatus": (
                    owner_phone_by_decision.get(
                        by_schedule_key[item["scheduleKey"]].human_decision_id,
                        "not_configured",
                    )
                    if item["scheduleKey"] in by_schedule_key
                    and by_schedule_key[item["scheduleKey"]].human_decision_id
                    else "not_called"
                ),
            }
            for item in schedule
        ],
    }


@router.post("/cost-centers", response_model=CostCenterRead, status_code=status.HTTP_201_CREATED)
async def create_cost_center(payload: CostCenterWrite, d=Depends(manage_deps)):
    try:
        obj = await FinanceDimensionalService(d[2]).create_cost_center(d[0], payload)
        await _audit(d, obj, "finance.cost_center.created")
        await d[2].commit()
        return obj
    except FinanceDimensionValidationError as error:
        raise _validation_error(error) from error


@router.get("/cost-centers/{cost_center_id}/impact")
async def cost_center_impact(cost_center_id: UUID, d=Depends(view_deps)):
    try:
        return await FinanceDimensionalService(d[2]).cost_center_impact(d[0], cost_center_id)
    except FinanceDimensionValidationError as error:
        raise _validation_error(error) from error


@router.post("/cost-centers/{cost_center_id}/impact/preview")
async def cost_center_impact_preview(
    cost_center_id: UUID, payload: CostCenterImpactPreview, d=Depends(view_deps)
):
    try:
        return await FinanceDimensionalService(d[2]).cost_center_impact_preview(
            d[0], cost_center_id, payload.patch, payload.expected_version
        )
    except FinanceDimensionValidationError as error:
        raise _validation_error(error) from error


@router.patch("/cost-centers/{cost_center_id}", response_model=CostCenterRead)
async def update_cost_center(
    cost_center_id: UUID, payload: CostCenterUpdate, d=Depends(manage_deps)
):
    try:
        existing = await d[2].scalar(
            select(FinanceCostCenter).where(
                FinanceCostCenter.id == cost_center_id,
                FinanceCostCenter.workspace_id == d[0],
            )
        )
        before = _snapshot(existing) if existing else None
        obj = await FinanceDimensionalService(d[2]).update_cost_center(
            d[0], cost_center_id, payload
        )
        await _audit(d, obj, "finance.cost_center.updated", before=before)
        await d[2].commit()
        return obj
    except FinanceDimensionValidationError as error:
        raise _validation_error(error) from error


@router.post("/budget-lines", response_model=BudgetLineRead, status_code=status.HTTP_201_CREATED)
async def create_budget_line(payload: BudgetLineWrite, d=Depends(manage_deps)):
    try:
        obj = await FinanceDimensionalService(d[2]).create_budget_line(d[0], payload)
        await _audit(d, obj, "finance.budget_line.created")
        await d[2].commit()
        return obj
    except FinanceDimensionValidationError as error:
        raise _validation_error(error) from error


@router.get("/budget-lines", response_model=list[BudgetLineRead])
async def list_budget_lines(
    period_start: date | None = Query(None),
    period_end: date | None = Query(None),
    cost_center_id: UUID | None = Query(None),
    d=Depends(view_deps),
):
    return await FinanceDimensionalService(d[2]).list_budget_lines(
        d[0],
        period_start=period_start,
        period_end=period_end,
        cost_center_id=cost_center_id,
    )


@router.post("/allocations", response_model=AllocationRead, status_code=status.HTTP_201_CREATED)
async def create_allocation(payload: AllocationWrite, d=Depends(manage_deps)):
    try:
        obj = await FinanceDimensionalService(d[2]).create_allocation(d[0], payload)
        await _audit(d, obj, "finance.allocation.created")
        await d[2].commit()
        return obj
    except FinanceDimensionValidationError as error:
        raise _validation_error(error) from error


@router.get("/allocations/{finance_entry_id}/closure")
async def allocation_closure(finance_entry_id: UUID, d=Depends(view_deps)):
    try:
        return await FinanceDimensionalService(d[2]).allocation_closure(
            d[0], finance_entry_id=finance_entry_id
        )
    except FinanceDimensionValidationError as error:
        raise _validation_error(error) from error


@router.post("/forecasts", response_model=ForecastRead, status_code=status.HTTP_201_CREATED)
async def create_forecast(payload: ForecastWrite, d=Depends(manage_deps)):
    try:
        obj = await FinanceDimensionalService(d[2]).create_forecast(d[0], payload)
        await _audit(d, obj, "finance.forecast.created")
        await d[2].commit()
        return obj
    except FinanceDimensionValidationError as error:
        raise _validation_error(error) from error


@router.get("/forecasts", response_model=list[ForecastRead])
async def list_forecasts(
    limit: int = Query(default=100, ge=1, le=200), d=Depends(view_deps)
):
    return await FinanceDimensionalService(d[2]).list_forecasts(d[0], limit=limit)


@router.post("/forecasts/generate", response_model=list[ForecastRead])
async def generate_forecasts(payload: ForecastGenerateWrite, d=Depends(manage_deps)):
    try:
        values = await FinanceDimensionalService(d[2]).generate_forecast(
            d[0], **payload.model_dump()
        )
        for value in values:
            await _audit(d, value, "finance.forecast.generated")
        await d[2].commit()
        return values
    except FinanceDimensionValidationError as error:
        raise _validation_error(error) from error


@router.post("/valuations", response_model=ValuationRead, status_code=status.HTTP_201_CREATED)
async def create_valuation(payload: ValuationWrite, d=Depends(manage_deps)):
    try:
        obj = await FinanceDimensionalService(d[2]).create_valuation(d[0], payload)
        await _audit(d, obj, "finance.valuation.created")
        await d[2].commit()
        return obj
    except FinanceDimensionValidationError as error:
        raise _validation_error(error) from error


@router.get("/valuations", response_model=list[ValuationRead])
async def list_valuations(
    limit: int = Query(default=100, ge=1, le=200), d=Depends(view_deps)
):
    return await FinanceDimensionalService(d[2]).list_valuations(d[0], limit=limit)


@router.post("/valuations/calculate", response_model=ValuationRead)
async def calculate_valuation(payload: ValuationCalculateWrite, d=Depends(manage_deps)):
    try:
        value = await FinanceDimensionalService(d[2]).calculate_valuation(
            d[0], **payload.model_dump()
        )
        await _audit(d, value, "finance.valuation.calculated")
        await d[2].commit()
        return value
    except FinanceDimensionValidationError as error:
        raise _validation_error(error) from error


@router.put("/tax-profile", response_model=TaxProfileRead)
async def upsert_tax_profile(payload: TaxProfileWrite, d=Depends(manage_deps)):
    try:
        existing = await d[2].scalar(
            select(FinanceTaxProfile).where(FinanceTaxProfile.workspace_id == d[0])
        )
        before = _snapshot(existing) if existing is not None else None
        obj = await FinanceDimensionalService(d[2]).upsert_tax_profile(d[0], payload)
        await _audit(d, obj, "finance.tax_profile.updated", before=before)
        await d[2].commit()
        return obj
    except FinanceDimensionValidationError as error:
        raise _validation_error(error) from error


@router.post("/analytics/query")
@router.post("/analytics/drilldown")
async def analytics_drilldown(payload: FinanceAnalyticsQuery, d=Depends(view_deps)):
    return await FinanceDimensionalService(d[2]).drilldown(d[0], **payload.model_dump())


@router.post("/dre")
async def finance_dre(payload: FinanceDreQuery, d=Depends(view_deps)):
    return await FinanceDimensionalService(d[2]).dre(d[0], **payload.model_dump())


@router.post("/tax-reform/simulate")
async def simulate_tax_reform(payload: TaxReformSimulationWrite, d=Depends(view_deps)):
    try:
        return await FinanceDimensionalService(d[2]).simulate_tax_reform(d[0], payload)
    except FinanceDimensionValidationError as error:
        raise _validation_error(error) from error


@router.post("/tax-reform/request-review", status_code=status.HTTP_201_CREATED)
async def request_tax_reform_review(
    payload: TaxReformReviewRequest, d=Depends(manage_deps)
):
    """Route the simulation to a qualified human and always alert the owner.

    Simulation itself remains read-only.  A durable human call is only claimed
    after the routing record and both in-app/phone delivery intents exist.
    """
    try:
        simulation = await FinanceDimensionalService(d[2]).simulate_tax_reform(
            d[0], payload.simulation
        )
        decision = await HumanRoutingService(d[2]).create_decision(
            workspace_id=d[0],
            decision_key=f"finance-tax-review:{payload.request_key}",
            options={
                "purpose": "finance_tax_reform_review",
                "reason": payload.reason,
                "simulation": simulation,
                "allowedDecisions": ["approve", "request_changes", "reject"],
            },
            required_roles=["accounting", "finance_manager"],
            required_skills=["tributario"],
            sla_minutes=payload.sla_minutes,
            max_hops=4,
        )
        d[2].add(
            KlmAuditEvent(
                workspace_id=d[0],
                actor_user_id=d[1].user_id,
                actor_type="human",
                actor_label="Responsável financeiro",
                event_type="finance.tax_reform.review_requested",
                entity_type="human_decision",
                entity_id=decision.id,
                summary="Revisão tributária encaminhada pela hierarquia financeira.",
                correlation_id=decision.decision_key,
                after_data={
                    "status": "pending",
                    "assignedUserId": str(decision.assigned_user_id),
                    "ownerUserId": str(decision.owner_user_id),
                    "dueAt": decision.due_at.isoformat() if decision.due_at else None,
                    "ownerNotified": True,
                },
            )
        )
        owner_phone = await d[2].scalar(
            select(HumanNotificationDelivery).where(
                HumanNotificationDelivery.decision_id == decision.id,
                HumanNotificationDelivery.recipient_kind == "owner",
                HumanNotificationDelivery.channel == "phone",
            )
        )
        await d[2].commit()
        return {
            "decisionId": str(decision.id),
            "status": str(getattr(decision.status, "value", decision.status)),
            "assignedUserId": str(decision.assigned_user_id),
            "ownerUserId": str(decision.owner_user_id),
            "dueAt": decision.due_at,
            "routingPath": decision.routing_path,
            "ownerNotified": True,
            "phoneDeliveryStatus": (
                str(getattr(owner_phone.status, "value", owner_phone.status))
                if owner_phone is not None
                else "not_configured"
            ),
        }
    except (FinanceDimensionValidationError, HumanRoutingError) as error:
        await d[2].rollback()
        raise _validation_error(error) from error
