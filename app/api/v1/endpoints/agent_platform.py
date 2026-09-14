from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    AuthContext,
    get_current_auth_context,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.core.platform_audit import record_canonical_platform_mutation
from app.core.platform_rbac import has_platform_permission, is_platform_root
from app.core.workspace_access_packages import WORKSPACE_ACCESS_PACKAGES
from app.db.tenant import set_workspace_context
from app.models.agent_platform import AgentDeployment, HumanDecision, HumanNotificationDelivery
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.client import Client
from app.models.contract_billing import ContractBillingProposal
from app.models.conversation import Conversation
from app.models.finance_dimensional import FinanceCostCenter
from app.models.flow import FlowRun
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import Matter
from app.models.task import Task
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.agent_platform import (
    AgentRunHumanInput,
    ContextRunCreate,
    DeploymentCreate,
    HumanDecisionCreate,
    HumanDecisionResolve,
    HumanRoutingCatalogRead,
    HumanRoutingPolicyCatalogRead,
)
from app.services.agent_platform_service import (
    HumanRoutingError,
    HumanRoutingService,
    consume_human_decision,
    routing_policy_catalog,
    validate_deployment_configuration,
    validate_deployment_ready,
    verify_deployment,
)
from app.services.agent_runtime_service import AgentRuntimeService
from app.services.entitlement_service import EntitlementError
from app.services.flow_node_registry import flow_node_catalog
from app.services.flow_service import FlowService, InvalidFlowRunTransition
from app.services.klm_agent_service import KlmAgentService


def _queue_agent_wakeup_after_commit(db: AsyncSession, run: AgentRun) -> None:
    """Register one durable post-commit wakeup; broker failures stay recovery-visible."""
    from app.db.post_commit import queue_agent_run_wakeup

    # Lightweight endpoint unit doubles may not expose AsyncSession.info;
    # production sessions always do, and recovery remains the fallback.
    if not hasattr(db, "info"):
        return
    if not getattr(run, "celery_task_id", None):
        run.celery_task_id = f"agent-run:{run.id}:{run.attempt_count}"
    queue_agent_run_wakeup(
        db,
        workspace_id=run.workspace_id,
        agent_run_id=run.id,
        task_id=run.celery_task_id,
    )


def _as_utc(value: datetime) -> datetime:
    """Normalize SQLite's naive timestamps to the UTC contract used in production."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _audit(
    db,
    ws,
    auth,
    event_type: str,
    entity_type: str,
    entity_id,
    summary: str,
    before=None,
    after=None,
):
    db.add(
        KlmAuditEvent(
            workspace_id=ws,
            actor_user_id=getattr(auth, "user_id", None),
            actor_type="human",
            actor_label="Usuário do workspace",
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=summary[:500],
            before_data=before,
            after_data=after,
            occurred_at=datetime.now(UTC),
        )
    )


router = APIRouter(prefix="/agent-platform", tags=["agent-platform"])


async def _bind_agent_workspace_context(db: AsyncSession, workspace_id: UUID) -> None:
    """Apply the tenant RLS context for real request sessions.

    Lightweight endpoint unit doubles intentionally do not implement the
    SQLAlchemy session contract; production and integration sessions do.
    """
    if isinstance(db, AsyncSession):
        await set_workspace_context(db, workspace_id)


def require_agent_platform_access(action: str):
    """Require platform AI RBAC in addition to any approved support scope.

    A support token proves the tenant grant and its finite configuration scope,
    but it must never turn a staff member without ``ai_operations`` into an
    agent operator.  Workspace users retain the pre-existing ``agents`` RBAC.
    """

    async def dependency(
        context: AuthContext = Depends(get_current_auth_context),
        db: AsyncSession = Depends(get_db),
    ) -> AuthContext:
        # A root-direct session is intentionally a tenant-scoped administrative
        # identity, not a normal ``surface=platform`` session.  It must retain
        # the platform AI RBAC check while operating the selected account.
        is_root_direct = context.administrative_session_type == "root_direct"
        if context.surface == "platform" or context.is_support_access or is_root_direct:
            actor = await db.get(User, context.user_id)
            if actor is None or not has_platform_permission(actor, "ai_operations", action):
                raise HTTPException(
                    403,
                    {
                        "code": "PLATFORM-PERMISSION-DENIED",
                        "message": "Seu pacote de acesso não autoriza operações de agentes.",
                        "module": "ai_operations",
                        "action": action,
                    },
                )
            # A normal Blackbox root session may inspect platform data, but it
            # cannot mutate a tenant's agents until it enters that tenant.  A
            # direct session carries both a revocable administrative session
            # identifier and the exact workspace established at entry.
            if (
                action == "manage"
                and is_platform_root(actor)
                and (
                    not is_root_direct
                    or context.administrative_session_id is None
                    or context.workspace_id is None
                )
            ):
                raise HTTPException(
                    401,
                    {
                        "code": "ROOT-DIRECT-SESSION-REQUIRED",
                        "message": (
                            "Entre diretamente na conta antes de criar ou alterar "
                            "agentes, fluxos ou execuções."
                        ),
                    },
                )
            return context
        return await require_permission("agents", action)(context=context, db=db)

    return dependency


async def _is_root_actor(db: AsyncSession, auth: AuthContext) -> bool:
    if auth.surface != "platform" and auth.administrative_session_type != "root_direct":
        return False
    return is_platform_root(await db.get(User, auth.user_id))


def _safe_decision_purpose(item: HumanDecision) -> str:
    """Return a non-sensitive operational label, never caller supplied JSON."""
    known = {
        "request_human_review": "Revisão humana solicitada",
        "request_approval": "Aprovação humana solicitada",
        "request_human_input": "Informação humana solicitada",
        "human_takeover": "Assunção humana solicitada",
        "finance_tax_review": "Validação tributária solicitada",
    }
    prefix = str(item.decision_key or "").split(":", 1)[0]
    return known.get(prefix, "Decisão humana solicitada")


async def agent_workspace_scope(
    requested_workspace_id: UUID | None = Query(default=None, alias="workspaceId"),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    """Resolve runtime scope; only platform_owner may select another workspace."""
    if auth.surface == "platform":
        actor = await db.get(User, auth.user_id)
        if not is_platform_root(actor):
            raise HTTPException(403, "somente o Super Admin pode escolher o workspace do runtime")
        if requested_workspace_id is None:
            raise HTTPException(400, "workspaceId é obrigatório na operação de plataforma")
        if await db.get(Workspace, requested_workspace_id) is None:
            raise HTTPException(404, "workspace não encontrado")
        await _bind_agent_workspace_context(db, requested_workspace_id)
        return requested_workspace_id
    current = await get_current_workspace_id(context=auth, db=db)
    if requested_workspace_id is not None and requested_workspace_id != current:
        raise HTTPException(403, "workspace fora do contexto da sessão")
    await _bind_agent_workspace_context(db, current)
    return current


@router.get("/nodes")
async def list_nodes(_: AuthContext = Depends(require_agent_platform_access("view"))):
    return flow_node_catalog()


@router.get("/catalog")
async def agent_catalog(
    ws: UUID = Depends(agent_workspace_scope),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_agent_platform_access("view")),
):
    """Return the authoritative tenant catalog for the explicitly selected account.

    Blackbox cannot reuse ``/klm/agents`` because that route is bound to the
    operator's own tenant session.  The selected workspace is resolved by
    ``agent_workspace_scope`` and remains root-only for cross-account access.
    """
    registry = await KlmAgentService(db).registry(ws)
    return {
        "policyVersion": registry.get("policyVersion"),
        "runtime": registry.get("runtime"),
        "agents": [
            *list(registry.get("agents") or []),
            *list(registry.get("specializedAgents") or []),
            *list(registry.get("customAgents") or []),
        ],
        "rolePackages": [
            {
                "key": package.key,
                "label": package.label,
                "description": package.description,
            }
            for package in WORKSPACE_ACCESS_PACKAGES.values()
        ],
    }


@router.get("/human-routing/catalog", response_model=HumanRoutingCatalogRead)
async def human_routing_catalog(
    ws: UUID = Depends(agent_workspace_scope),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_agent_platform_access("view")),
):
    """Expose only live, workspace-scoped human routing choices.

    The response intentionally contains stable slugs and human labels, never
    people or team UUIDs.  The owner fallback is explicit so a sparse
    workspace cannot create an unassigned or phantom human wait.
    """
    try:
        return await HumanRoutingService(db).routing_catalog(workspace_id=ws)
    except HumanRoutingError as error:
        raise HTTPException(409, str(error)) from error


@router.get("/human-routing/policies", response_model=HumanRoutingPolicyCatalogRead)
async def human_routing_policies(
    ws: UUID = Depends(agent_workspace_scope),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_agent_platform_access("view")),
):
    """Return versioned policies combined with live workspace routing proof."""
    try:
        live_catalog = await HumanRoutingService(db).routing_catalog(workspace_id=ws)
        return routing_policy_catalog(live_catalog)
    except HumanRoutingError as error:
        raise HTTPException(409, str(error)) from error


@router.get("/deployments")
async def list_deployments(
    ws: UUID = Depends(agent_workspace_scope),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_agent_platform_access("view")),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    total = int(
        await db.scalar(
            select(func.count(AgentDeployment.id)).where(AgentDeployment.workspace_id == ws)
        )
        or 0
    )
    deployments = list(
        (
            await db.scalars(
                select(AgentDeployment)
                .where(AgentDeployment.workspace_id == ws)
                .order_by(AgentDeployment.agent_key, AgentDeployment.version.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )
    now = datetime.now(UTC)
    period_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    usage_rows = (
        await db.execute(
            select(
                AgentRun.deployment_id,
                func.coalesce(func.sum(AgentRun.input_tokens + AgentRun.output_tokens), 0),
            )
            .where(
                AgentRun.workspace_id == ws,
                AgentRun.deployment_id.is_not(None),
                AgentRun.deployment_id.in_([item.id for item in deployments]),
                AgentRun.created_at >= period_start,
            )
            .group_by(AgentRun.deployment_id)
        )
    ).all()
    usage_by_deployment = {deployment_id: int(tokens or 0) for deployment_id, tokens in usage_rows}
    result = []
    for deployment in deployments:
        used = usage_by_deployment.get(deployment.id, 0)
        budget_limit = int((deployment.budget_policy or {}).get("monthlyTokenLimit") or 0)
        result.append(
            {
                "id": str(deployment.id),
                "agentKey": deployment.agent_key,
                "rolePackage": deployment.role_package,
                "version": deployment.version,
                "status": str(getattr(deployment.status, "value", deployment.status)),
                "config": deployment.config,
                "gates": deployment.gates,
                "toolBindings": deployment.tool_bindings,
                "knowledgeBindings": deployment.knowledge_bindings,
                "autonomyPolicy": deployment.autonomy_policy,
                "humanRoutingPolicy": deployment.human_routing_policy,
                "budgetPolicy": deployment.budget_policy,
                "budgetUsage": {
                    "periodStart": period_start,
                    "usedTokens": used,
                    "limitTokens": budget_limit or None,
                    "remainingTokens": max(0, budget_limit - used) if budget_limit else None,
                    "exhausted": bool(budget_limit and used >= budget_limit),
                },
                "createdAt": deployment.created_at,
                "updatedAt": deployment.updated_at,
            }
        )
    return {
        "items": result,
        "total": total,
        "limit": limit,
        "offset": offset,
        "hasMore": offset + len(result) < total,
    }


@router.get("/decisions")
async def list_decisions(
    ws: UUID = Depends(agent_workspace_scope),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_agent_platform_access("view")),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    total = int(
        await db.scalar(
            select(func.count(HumanDecision.id)).where(HumanDecision.workspace_id == ws)
        )
        or 0
    )
    decisions = list(
        (
            await db.scalars(
                select(HumanDecision)
                .where(HumanDecision.workspace_id == ws)
                .order_by(HumanDecision.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )
    routing_user_ids: set[UUID] = set()
    for item in decisions:
        for value in item.routing_path:
            try:
                routing_user_ids.add(UUID(str(value)))
            except (TypeError, ValueError):
                continue
    user_ids = routing_user_ids | {
        value
        for item in decisions
        for value in [item.assigned_user_id, item.owner_user_id]
        if value
    }
    users = (
        {
            user.id: user.name
            for user in (await db.scalars(select(User).where(User.id.in_(user_ids)))).all()
        }
        if user_ids
        else {}
    )
    deliveries = (
        list(
            (
                await db.scalars(
                    select(HumanNotificationDelivery).where(
                        HumanNotificationDelivery.decision_id.in_([item.id for item in decisions])
                    )
                )
            ).all()
        )
        if decisions
        else []
    )
    by_decision: dict[UUID, list[HumanNotificationDelivery]] = {}
    for delivery in deliveries:
        by_decision.setdefault(delivery.decision_id, []).append(delivery)
    response = []
    for item in decisions:
        item_deliveries = by_decision.get(item.id, [])
        routing_path = []
        for index, raw_value in enumerate(item.routing_path):
            try:
                value = UUID(str(raw_value))
            except (TypeError, ValueError):
                continue
            routing_path.append(
                {
                    "userId": str(value),
                    "label": users.get(value, "Responsável indisponível"),
                    "level": index,
                }
            )
        next_level = item.escalation_level + 1
        next_escalation = next(
            (entry for entry in routing_path if entry["level"] == next_level), None
        )
        response.append(
            {
                "id": str(item.id),
                "status": item.status,
                # Arbitrary node options can contain PII or credentials.  They
                # remain server-side; the control plane receives an allowlisted
                # display contract only.
                "purpose": _safe_decision_purpose(item),
                "resolutionMode": "agent_outcome" if item.agent_run_id else "flow_checkpoint",
                "agentRunId": str(item.agent_run_id) if item.agent_run_id else None,
                "allowedActions": ["approve", "reject"],
                "inputFields": sorted(
                    set((item.options or {}).get("inputFields") or [])
                    | set(
                        (((item.options or {}).get("inputSchema") or {}).get("properties") or {})
                        if isinstance((item.options or {}).get("inputSchema"), dict)
                        else {}
                    )
                )[:32],
                "assignedUserId": str(item.assigned_user_id) if item.assigned_user_id else None,
                "assignedUserLabel": users.get(item.assigned_user_id),
                "ownerUserId": str(item.owner_user_id) if item.owner_user_id else None,
                "ownerUserLabel": users.get(item.owner_user_id),
                "dueAt": item.due_at,
                "acknowledgedAt": item.acknowledged_at,
                "escalationLevel": item.escalation_level,
                "routingLevels": len(item.routing_path),
                "routingPathLabels": routing_path,
                "nextEscalation": next_escalation,
                "createdAt": item.created_at,
                "phoneDelivery": [
                    {
                        "recipientKind": d.recipient_kind,
                        "status": d.status,
                        "attempts": d.attempts,
                        "lastErrorCode": d.last_error_code,
                        # A provider receipt is an operational correlation
                        # reference, not credential material.  It is exposed
                        # only for deliveries the provider actually confirmed.
                        "providerReceiptId": (
                            d.provider_receipt_id
                            if str(getattr(d.status, "value", d.status)) == "delivered"
                            else None
                        ),
                    }
                    for d in item_deliveries
                    if d.channel == "phone"
                ],
                "notificationDelivery": [
                    {
                        "channel": d.channel,
                        "recipientKind": d.recipient_kind,
                        "status": d.status,
                        "attempts": d.attempts,
                        "lastErrorCode": d.last_error_code,
                        "deliveredAt": d.delivered_at,
                        "providerReceiptId": (
                            d.provider_receipt_id
                            if str(getattr(d.status, "value", d.status)) == "delivered"
                            else None
                        ),
                    }
                    for d in item_deliveries
                ],
            }
        )
    return {
        "items": response,
        "total": total,
        "limit": limit,
        "offset": offset,
        "hasMore": offset + len(response) < total,
    }


@router.get("/decisions/overview")
async def decisions_overview(
    ws: UUID = Depends(agent_workspace_scope),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_agent_platform_access("view")),
):
    decisions = list(
        (await db.scalars(select(HumanDecision).where(HumanDecision.workspace_id == ws))).all()
    )
    deliveries = list(
        (
            await db.scalars(
                select(HumanNotificationDelivery).where(
                    HumanNotificationDelivery.workspace_id == ws
                )
            )
        ).all()
    )
    now = datetime.now(UTC)
    pending = [item for item in decisions if item.status == "pending"]
    return {
        "open": len(pending),
        "assigned": sum(item.assigned_user_id is not None for item in pending),
        "acknowledged": sum(item.acknowledged_at is not None for item in pending),
        "overdue": sum(item.due_at is not None and _as_utc(item.due_at) < now for item in pending),
        "escalated": sum(item.escalation_level > 0 for item in pending),
        "phone": {
            "pending": sum(
                item.channel == "phone" and item.status == "pending" for item in deliveries
            ),
            "delivered": sum(
                item.channel == "phone" and item.status == "delivered" for item in deliveries
            ),
            "failed": sum(
                item.channel == "phone" and item.status == "failed" for item in deliveries
            ),
            "notConfigured": sum(
                item.channel == "phone" and item.status == "not_configured" for item in deliveries
            ),
        },
    }


@router.get("/owner-alert-channel")
async def owner_alert_channel(
    ws: UUID = Depends(agent_workspace_scope),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_agent_platform_access("view")),
):
    """Expose only whether the selected workspace owner phone is configured.

    The Blackbox must never fall back to the platform actor's implicit
    workspace when it reports owner oversight for a customer account.  The
    actual phone is intentionally masked; delivery evidence remains attached
    to each human decision.
    """
    workspace = await db.get(Workspace, ws)
    if workspace is None:
        raise HTTPException(404, "workspace não encontrado")
    phone = (workspace.owner_whatsapp_phone or "").strip()
    return {
        "ownerWhatsappPhone": f"••••{phone[-4:]}" if phone else None,
        "configured": bool(phone),
    }


@router.post("/runs/context")
async def create_context_runtime_run(
    data: ContextRunCreate,
    request: Request,
    ws: UUID = Depends(agent_workspace_scope),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_agent_platform_access("manage")),
):
    try:
        run = await AgentRuntimeService(db).create_context_run(
            workspace_id=ws,
            context_type=data.context_type,
            context_id=data.context_id,
            agent_name=data.agent_name,
            trigger_source=data.trigger_source,
            triggered_by_user_id=auth.user_id,
            input_payload=data.input_payload,
            dedupe_key=data.dedupe_key,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "agent_context_run_rejected",
                "message": str(error),
            },
        ) from error
    await record_canonical_platform_mutation(
        request=request,
        context=auth,
        db=db,
        before_snapshot=None,
        after_snapshot=(
            "agent_run",
            str(run.id),
            {
                "workspaceId": str(run.workspace_id),
                "deploymentId": str(run.deployment_id) if run.deployment_id else None,
                "agentName": run.agent_name,
                "triggerSource": run.trigger_source,
                "contextType": run.context_type,
                "contextId": str(run.context_id) if run.context_id else None,
                "status": run.status.value,
                "attemptCount": run.attempt_count,
            },
        ),
        module="ai_operations",
        action="create_context_agent_run",
        reason=f"Execução manual do agente {run.agent_name} no contexto {run.context_type}.",
        reason_source="user",
    )
    # A contextual run and its canonical audit event are one command. Commit
    # both before returning the run id so an immediate GET from another
    # request can observe the resource deterministically. The request-scoped
    # dependency still owns rollback on failure and a final no-op commit.
    await db.commit()
    return {
        "id": str(run.id),
        "status": run.status.value,
        "contextType": run.context_type,
        "contextId": str(run.context_id) if run.context_id else None,
    }


@router.get("/runs/context-options")
async def list_context_runtime_options(
    context_type: str = Query(alias="contextType"),
    query: str = Query(default="", max_length=120),
    limit: int = Query(default=50, ge=1, le=100),
    ws: UUID = Depends(agent_workspace_scope),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_agent_platform_access("manage")),
):
    """Tenant-scoped labels for the Blackbox; raw UUID entry is never a fallback."""
    needle = query.strip().lower()
    rows: list[tuple[UUID, str]] = []
    if context_type == "client":
        statement = select(Client.id, Client.name).where(
            Client.workspace_id == ws, Client.deleted_at.is_(None)
        )
        if needle:
            statement = statement.where(func.lower(Client.name).contains(needle))
        result = await db.execute(statement.order_by(Client.name, Client.id).limit(limit))
        rows = list(result.all())
    elif context_type == "matter":
        statement = select(Matter.id, Matter.demand_type).where(
            Matter.workspace_id == ws, Matter.deleted_at.is_(None)
        )
        if needle:
            statement = statement.where(
                or_(
                    func.lower(Matter.demand_type).contains(needle),
                    func.lower(func.coalesce(Matter.case_number, "")).contains(needle),
                )
            )
        result = await db.execute(
            statement.order_by(Matter.updated_at.desc(), Matter.id).limit(limit)
        )
        rows = list(result.all())
    elif context_type == "contract":
        statement = select(
            ContractBillingProposal.id,
            func.coalesce(ContractBillingProposal.source_document_name, "Contrato sem título"),
        ).where(ContractBillingProposal.workspace_id == ws)
        if needle:
            document_name = func.coalesce(ContractBillingProposal.source_document_name, "")
            statement = statement.where(func.lower(document_name).contains(needle))
        result = await db.execute(
            statement.order_by(
                ContractBillingProposal.updated_at.desc(), ContractBillingProposal.id
            ).limit(limit)
        )
        rows = list(result.all())
    elif context_type == "conversation":
        statement = (
            select(Conversation.id, Matter.demand_type)
            .join(Matter, Matter.id == Conversation.matter_id)
            .where(Matter.workspace_id == ws, Matter.deleted_at.is_(None))
        )
        if needle:
            statement = statement.where(func.lower(Matter.demand_type).contains(needle))
        result = await db.execute(
            statement.order_by(Conversation.timestamp.desc(), Conversation.id).limit(limit)
        )
        rows = list(result.all())
    elif context_type == "task":
        statement = (
            select(Task.id, Task.title)
            .join(Matter, Matter.id == Task.matter_id)
            .where(Matter.workspace_id == ws, Matter.deleted_at.is_(None))
        )
        if needle:
            statement = statement.where(func.lower(Task.title).contains(needle))
        result = await db.execute(statement.order_by(Task.updated_at.desc(), Task.id).limit(limit))
        rows = list(result.all())
    elif context_type == "finance":
        statement = select(
            FinanceCostCenter.id,
            (FinanceCostCenter.code + " · " + FinanceCostCenter.name),
        ).where(FinanceCostCenter.workspace_id == ws)
        if needle:
            statement = statement.where(
                or_(
                    func.lower(FinanceCostCenter.code).contains(needle),
                    func.lower(FinanceCostCenter.name).contains(needle),
                )
            )
        result = await db.execute(
            statement.order_by(FinanceCostCenter.code, FinanceCostCenter.id).limit(limit)
        )
        rows = list(result.all())
    else:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "agent_context_type_invalid",
                "message": "Tipo de contexto não pesquisável.",
            },
        )
    return {"items": [{"id": str(item_id), "label": label} for item_id, label in rows]}


@router.get("/runs")
async def list_runtime_runs(
    ws: UUID = Depends(agent_workspace_scope),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_agent_platform_access("view")),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Safe operational projection; payloads, prompts and case content never leave the API."""
    rows = list(
        (
            await db.scalars(
                select(AgentRun)
                .where(AgentRun.workspace_id == ws)
                .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    total = await db.scalar(select(func.count(AgentRun.id)).where(AgentRun.workspace_id == ws))
    return {
        "items": [
            {
                "id": str(run.id),
                "agentName": run.agent_name,
                "deploymentId": (
                    str(getattr(run, "deployment_id", None))
                    if getattr(run, "deployment_id", None)
                    else None
                ),
                "contextType": run.context_type or "matter",
                "contextId": str(run.context_id or run.matter_id),
                "triggerSource": run.trigger_source,
                "status": run.status.value if hasattr(run.status, "value") else str(run.status),
                "attemptCount": run.attempt_count,
                "inputTokens": int(getattr(run, "input_tokens", 0) or 0),
                "outputTokens": int(getattr(run, "output_tokens", 0) or 0),
                "costUsd": str(getattr(run, "cost_usd", 0) or 0),
                "startedAt": run.started_at,
                "completedAt": run.completed_at,
                "createdAt": run.created_at,
                "updatedAt": run.updated_at,
            }
            for run in rows
        ],
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
        "hasMore": offset + len(rows) < int(total or 0),
    }


@router.post("/runs/{run_id}/cancel")
async def cancel_runtime_run(
    run_id: UUID,
    ws: UUID = Depends(agent_workspace_scope),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_agent_platform_access("manage")),
):
    run = await db.scalar(
        select(AgentRun).where(AgentRun.id == run_id, AgentRun.workspace_id == ws).with_for_update()
    )
    if run is None:
        raise HTTPException(404, "execution not found")
    if run.status in {AgentRunStatus.completed, AgentRunStatus.failed, AgentRunStatus.cancelled}:
        raise HTTPException(409, "execution is terminal")
    run.status = AgentRunStatus.cancelled
    await _audit(db, ws, auth, "agent_run_cancelled", "agent_run", run.id, "Execução cancelada")
    await db.commit()
    return {"id": str(run.id), "status": run.status.value}


@router.post("/runs/{run_id}/retry")
async def retry_runtime_run(
    run_id: UUID,
    ws: UUID = Depends(agent_workspace_scope),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_agent_platform_access("manage")),
):
    run = await db.scalar(
        select(AgentRun).where(AgentRun.id == run_id, AgentRun.workspace_id == ws).with_for_update()
    )
    if run is None:
        raise HTTPException(404, "execution not found")
    if run.status not in {
        AgentRunStatus.failed,
        AgentRunStatus.cancelled,
        AgentRunStatus.paused_budget,
    }:
        raise HTTPException(409, "only failed, cancelled or budget-paused executions can retry")
    run.status = AgentRunStatus.queued
    run.attempt_count = getattr(run, "attempt_count", 0) + 1
    _queue_agent_wakeup_after_commit(db, run)
    await _audit(db, ws, auth, "agent_run_retried", "agent_run", run.id, "Execução reenfileirada")
    await db.commit()
    return {"id": str(run.id), "status": run.status.value, "attemptCount": run.attempt_count}


@router.post("/runs/{run_id}/human-input")
async def provide_runtime_human_input(
    run_id: UUID,
    data: AgentRunHumanInput,
    ws: UUID = Depends(agent_workspace_scope),
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Consume exactly one pending checkpoint and resume the waiting run.

    This is deliberately narrower than decision resolution: it accepts only
    fields declared by the checkpoint and never treats arbitrary JSON as an
    agent instruction.
    """
    run = await db.scalar(
        select(AgentRun).where(AgentRun.id == run_id, AgentRun.workspace_id == ws).with_for_update()
    )
    if run is None:
        raise HTTPException(404, "execution not found")
    if run.status not in {AgentRunStatus.waiting_human, AgentRunStatus.waiting_data}:
        raise HTTPException(409, "execution is not waiting for human input")
    decision = await db.scalar(
        select(HumanDecision)
        .where(
            HumanDecision.workspace_id == ws,
            HumanDecision.agent_run_id == run.id,
            HumanDecision.status == "pending",
        )
        .order_by(HumanDecision.created_at.asc())
        .with_for_update()
    )
    if decision is None:
        raise HTTPException(409, "pending human checkpoint unavailable")
    if (
        decision.assigned_user_id not in {None, auth.user_id}
        and decision.owner_user_id not in {None, auth.user_id}
        and not await _is_root_actor(db, auth)
    ):
        raise HTTPException(403, "checkpoint is assigned to another human")
    options = dict(decision.options or {})
    schema = options.get("inputSchema") if isinstance(options.get("inputSchema"), dict) else {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    declared = set(properties) or set(options.get("inputFields") or [])
    if not declared or set(data.values) - declared:
        raise HTTPException(422, "input contains fields not declared by the checkpoint")
    decision = await consume_human_decision(
        db,
        decision.id,
        workspace_id=ws,
        user_id=auth.user_id,
        choice={"action": "provide_input", "values": data.values},
    )
    if decision is None:
        raise HTTPException(409, "checkpoint already consumed or expired")
    run.input_payload = {**dict(run.input_payload or {}), "humanInput": data.values}
    run.status = AgentRunStatus.queued
    run.attempt_count = getattr(run, "attempt_count", 0) + 1
    _queue_agent_wakeup_after_commit(db, run)
    await _audit(
        db,
        ws,
        auth,
        "agent_run_human_input_provided",
        "agent_run",
        run.id,
        "Entrada humana fornecida; execução reenfileirada",
        after={"decisionId": str(decision.id), "fields": sorted(data.values)},
    )
    await db.commit()
    return {"id": str(run.id), "status": run.status.value, "decisionId": str(decision.id)}


@router.post("/deployments")
async def create_deployment(
    data: DeploymentCreate,
    ws: UUID = Depends(agent_workspace_scope),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_agent_platform_access("manage")),
):
    values = data.model_dump()
    config = dict(values.get("config") or {})
    # V2-compatible default for callers created before definitionVersion was
    # exposed; persisted deployments always carry an explicit version.
    config.setdefault("definitionVersion", int(values.get("version") or 1))
    if config.get("rolePackage") not in {None, values["role_package"]}:
        raise HTTPException(422, "rolePackage diverges from role_package")
    config["rolePackage"] = values["role_package"]
    try:
        validate_deployment_configuration(config)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    values["config"] = config
    values["tool_bindings"] = list(config.get("tools") or [])
    values["knowledge_bindings"] = list(config.get("knowledgeSources") or [])
    values["autonomy_policy"] = {"key": config.get("autonomyPolicy")}
    values["human_routing_policy"] = {"key": config.get("humanRoutingPolicy")}
    values["budget_policy"] = dict(config.get("budgetPolicy") or {})
    values["gates"] = {
        "definitionVerified": False,
        "roleVerified": False,
        "modelVerified": False,
        "knowledgeVerified": False,
        "autonomyVerified": False,
        "policyVerified": False,
        "entitlementVerified": False,
        "toolsVerified": False,
        "humanQueueVerified": False,
        "budgetVerified": False,
        "testPassed": False,
    }
    obj = AgentDeployment(workspace_id=ws, **values)
    db.add(obj)
    await db.flush()
    await _audit(
        db,
        ws,
        auth,
        "agent_deployment_created",
        "agent_deployment",
        obj.id,
        "Deployment criado",
        after={"status": obj.status, "name": getattr(obj, "name", None)},
    )
    await db.commit()
    await db.refresh(obj)
    return obj


@router.post("/deployments/{deployment_id}/validate")
async def validate_deployment(
    deployment_id: UUID,
    ws: UUID = Depends(agent_workspace_scope),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_agent_platform_access("manage")),
):
    obj = await db.scalar(
        select(AgentDeployment)
        .where(AgentDeployment.id == deployment_id, AgentDeployment.workspace_id == ws)
        .with_for_update()
    )
    if not obj:
        raise HTTPException(404, "deployment not found")
    if obj.status == "active":
        raise HTTPException(409, "active deployment is immutable; create a new version")
    try:
        obj.gates = await verify_deployment(db, obj)
        config = dict(obj.config or {})
        config["testedAt"] = obj.gates["verifiedAt"]
        config["testEvidence"] = {
            "kind": obj.gates["testKind"],
            "externalModelCalled": obj.gates["externalModelCalled"],
            **dict(obj.gates.get("testEvidence") or {}),
        }
        obj.config = config
    except (ValueError, EntitlementError) as error:
        await db.rollback()
        raise HTTPException(422, str(error)) from error
    await _audit(
        db,
        ws,
        auth,
        "agent_deployment_validated",
        "agent_deployment",
        obj.id,
        "Deployment validado",
        after={"gates": obj.gates},
    )
    await db.commit()
    await db.refresh(obj)
    return obj


@router.post("/deployments/{deployment_id}/publish")
async def publish_deployment(
    deployment_id: UUID,
    ws: UUID = Depends(agent_workspace_scope),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_agent_platform_access("manage")),
):
    obj = await db.scalar(
        select(AgentDeployment).where(
            AgentDeployment.id == deployment_id, AgentDeployment.workspace_id == ws
        )
    )
    if not obj:
        raise HTTPException(404, "deployment not found")
    try:
        validate_deployment_ready(obj.config, obj.gates)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    before = {"status": obj.status}
    obj.status = "active"
    await _audit(
        db,
        ws,
        auth,
        "agent_deployment_published",
        "agent_deployment",
        obj.id,
        "Deployment publicado",
        before=before,
        after={"status": obj.status},
    )
    await db.commit()
    return obj


@router.post("/deployments/{deployment_id}/pause")
async def pause_deployment(
    deployment_id: UUID,
    ws: UUID = Depends(agent_workspace_scope),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_agent_platform_access("manage")),
):
    obj = await db.scalar(
        select(AgentDeployment).where(
            AgentDeployment.id == deployment_id, AgentDeployment.workspace_id == ws
        )
    )
    if not obj:
        raise HTTPException(404, "deployment not found")
    before = {"status": obj.status}
    obj.status = "paused"
    await _audit(
        db,
        ws,
        auth,
        "agent_deployment_paused",
        "agent_deployment",
        obj.id,
        "Deployment pausado",
        before=before,
        after={"status": obj.status},
    )
    await db.commit()
    return obj


@router.post("/decisions")
async def request_decision(
    data: HumanDecisionCreate,
    ws: UUID = Depends(agent_workspace_scope),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_agent_platform_access("manage")),
):
    # Execution-linked decisions are created atomically by the flow/agent
    # runtime together with their durable checkpoint.  Allowing an operator to
    # attach a manual decision here would leave a run in ``waiting_human``
    # without a resumable checkpoint, so fail closed instead of creating an
    # uncallable wait.
    if data.flow_run_id is not None or data.agent_run_id is not None:
        raise HTTPException(
            422,
            "execution-linked decisions must be created by the runtime checkpoint",
        )
    try:
        payload = data.model_dump()
        obj = await HumanRoutingService(db).create_decision(
            workspace_id=ws,
            decision_key=f"manual:{uuid4()}",
            **payload,
        )
    except HumanRoutingError as error:
        await db.rollback()
        raise HTTPException(422, str(error)) from error
    await db.commit()
    await db.refresh(obj)
    return {
        "id": str(obj.id),
        "status": obj.status,
        "purpose": _safe_decision_purpose(obj),
        "assignedUserId": str(obj.assigned_user_id) if obj.assigned_user_id else None,
        "ownerUserId": str(obj.owner_user_id) if obj.owner_user_id else None,
        "dueAt": obj.due_at,
    }


@router.post("/decisions/{decision_id}/acknowledge")
async def acknowledge_decision(
    decision_id: UUID,
    ws: UUID = Depends(agent_workspace_scope),
    # The recipient and the workspace owner must be able to acknowledge a
    # call even if their operational role does not administer agents.
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime

    obj = await db.scalar(
        select(HumanDecision)
        .where(HumanDecision.id == decision_id, HumanDecision.workspace_id == ws)
        .with_for_update()
    )
    if not obj:
        raise HTTPException(404, "decision not found")
    if obj.status != "pending":
        raise HTTPException(409, "decision is not pending")
    if (
        obj.assigned_user_id != auth.user_id
        and obj.owner_user_id != auth.user_id
        and not await _is_root_actor(db, auth)
    ):
        raise HTTPException(403, "decision is assigned to another human")
    obj.acknowledged_at = datetime.now(UTC)
    await _audit(
        db,
        ws,
        auth,
        "human_decision_acknowledged",
        "human_decision",
        obj.id,
        "Decisão humana reconhecida",
        after={"acknowledged": True},
    )
    await db.commit()
    return {"status": "acknowledged", "id": str(obj.id), "acknowledgedAt": obj.acknowledged_at}


@router.post("/decisions/{decision_id}/resolve")
async def resolve_decision(
    decision_id: UUID,
    data: HumanDecisionResolve,
    ws: UUID = Depends(agent_workspace_scope),
    # Resolving a decision is assignment-bound and must not require the
    # broader agents:manage privilege from the assigned human.
    auth: AuthContext = Depends(get_current_auth_context),
    db: AsyncSession = Depends(get_db),
):
    obj = await db.scalar(
        select(HumanDecision).where(
            HumanDecision.id == decision_id, HumanDecision.workspace_id == ws
        )
    )
    if not obj:
        raise HTTPException(404, "decision not found")
    if (
        obj.assigned_user_id != auth.user_id
        and obj.owner_user_id != auth.user_id
        and not await _is_root_actor(db, auth)
    ):
        raise HTTPException(403, "decision is assigned to another human")
    decision = await consume_human_decision(
        db,
        decision_id,
        workspace_id=ws,
        user_id=auth.user_id,
        choice=data.choice,
    )
    if decision is None:
        raise HTTPException(409, "decision already consumed or expired")
    await _audit(
        db,
        ws,
        auth,
        "human_decision_resolved",
        "human_decision",
        decision.id,
        "Decisão humana resolvida",
        after={"choice": data.choice, "status": "decided"},
    )
    resumed_flow_run_id: str | None = None
    raw_action = str(data.choice.get("action") or data.choice.get("decision") or "").lower()
    approved = raw_action in {"approve", "approved"}
    rejected = raw_action in {"reject", "rejected"}
    if not approved and not rejected:
        await db.rollback()
        raise HTTPException(422, "choice must explicitly approve or reject")

    linked_agent_run = None
    if decision.agent_run_id is not None:
        linked_agent_run = await db.scalar(
            select(AgentRun)
            .where(AgentRun.id == decision.agent_run_id, AgentRun.workspace_id == ws)
            .with_for_update()
        )
        if linked_agent_run is None or linked_agent_run.status not in {
            AgentRunStatus.failed,
            AgentRunStatus.cancelled,
            AgentRunStatus.waiting_human,
        }:
            await db.rollback()
            raise HTTPException(409, "linked agent run is not awaiting a human outcome")
        resume_after_approval = bool(
            isinstance(decision.options, dict) and decision.options.get("resumeAgentRunOnApprove")
        )
        linked_agent_run.completed_at = datetime.now(UTC)
        if approved and resume_after_approval:
            # Tool/external timeout and budget pauses are not completed by a
            # human merely acknowledging them.  Approval is the explicit,
            # auditable permission to resume the durable queue.  The recovery
            # worker dispatches it; this endpoint never runs an agent inline.
            linked_agent_run.status = AgentRunStatus.queued
            linked_agent_run.completed_at = None
            linked_agent_run.attempt_count = getattr(linked_agent_run, "attempt_count", 0) + 1
            linked_agent_run.celery_task_id = None
            _queue_agent_wakeup_after_commit(db, linked_agent_run)
            linked_agent_run.error_message = None
            linked_agent_run.output_payload = {
                **dict(linked_agent_run.output_payload or {}),
                "humanOutcome": "approved_resume",
                "decisionId": str(decision.id),
            }
        elif approved:
            linked_agent_run.status = AgentRunStatus.completed
            linked_agent_run.error_message = None
            linked_agent_run.output_payload = {
                **dict(linked_agent_run.output_payload or {}),
                "humanOutcome": "approved",
                "decisionId": str(decision.id),
            }
        else:
            linked_agent_run.status = AgentRunStatus.failed
            linked_agent_run.error_message = "Execução encerrada por decisão humana."
    if decision.flow_run_id is not None:
        flow_run = await db.scalar(
            select(FlowRun)
            .where(FlowRun.id == decision.flow_run_id, FlowRun.workspace_id == ws)
            .with_for_update()
        )
        if flow_run is None or flow_run.status != "waiting_human":
            await db.rollback()
            raise HTTPException(409, "linked flow is not waiting for a human decision")
        context = dict(flow_run.context or {})
        checkpoints = dict(context.get("humanCheckpoints") or {})
        checkpoint_key = next(
            (
                key
                for key, value in checkpoints.items()
                if isinstance(value, dict) and value.get("decisionKey") == decision.decision_key
            ),
            None,
        )
        if checkpoint_key is None:
            await db.rollback()
            raise HTTPException(409, "linked flow checkpoint is unavailable")
        checkpoint = dict(checkpoints[checkpoint_key])
        checkpoint.update(
            {
                "status": "decided",
                "decision": data.choice,
                "decidedBy": str(auth.user_id),
                "decidedAt": decision.decided_at.isoformat() if decision.decided_at else None,
            }
        )
        checkpoints[checkpoint_key] = checkpoint
        context["humanCheckpoints"] = checkpoints
        # A flow with no immutable author (for example a legacy/system flow)
        # may continue only under the real human who approved this exact
        # checkpoint.  This prevents an unresolved/no-owner loop while never
        # fabricating a service identity for a sensitive local action.
        authorized_actors = dict(context.get("humanAuthorizedActors") or {})
        authorized_actors[checkpoint_key] = str(auth.user_id)
        context["humanAuthorizedActors"] = authorized_actors
        flow_run.context = context
        await db.flush()
        if linked_agent_run is not None and rejected:
            flow_run.status = "failed"
            flow_run.error_message = "Execução encerrada por decisão humana."
            flow_run.completed_at = datetime.now(UTC)
            await db.commit()
        else:
            try:
                resumed = await FlowService(db).resume_flow_run(flow_run.id, ws)
            except InvalidFlowRunTransition as error:
                await db.rollback()
                raise HTTPException(409, str(error)) from error
            resumed_flow_run_id = str(resumed.id) if resumed else None
    else:
        await db.commit()
    return {
        "status": "decided",
        "id": str(decision_id),
        "resumedFlowRunId": resumed_flow_run_id,
    }
