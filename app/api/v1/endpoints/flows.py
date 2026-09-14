"""
Flow Builder REST API

ATENÇÃO: Ordem das rotas importa no FastAPI.
Rotas estáticas (/templates, /runs, /scheduled-messages) DEVEM vir antes
das rotas parametrizadas (/{flow_id}) para evitar conflito de matching.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.entitlement_errors import entitlement_http_exception
from app.core.deps import (
    AuthContext,
    can_view_sensitive_lead_data,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.core.lead_privacy import mask_flow_run, mask_scheduled_message
from app.models.flow import verified_flow_revision_snapshot
from app.schemas.flow import (
    FlowCopilotPlanRead,
    FlowCopilotPlanRequest,
    FlowCreate,
    FlowDryRunRead,
    FlowDryRunRequest,
    FlowExternalActionReconciliationRequest,
    FlowGraphValidationRead,
    FlowGraphValidationRequest,
    FlowNodeCatalogRead,
    FlowRead,
    FlowRevisionRead,
    FlowRunRead,
    FlowRunRequest,
    FlowUpdate,
    MatterFlowJourneyKpisRead,
    MatterFlowJourneyOverviewRead,
    MatterFlowJourneyRead,
    MatterFlowJourneyStepRead,
    ScheduledMessageCreate,
    ScheduledMessageRead,
    ScheduledMessageReconciliationRequest,
    analyze_flow_graph,
)
from app.services.entitlement_service import EntitlementError
from app.services.flow_copilot_service import FlowCopilotService
from app.services.flow_node_registry import FLOW_EXTERNAL_NODE_TYPES, flow_node_catalog
from app.services.flow_service import (
    ExternalDeliveryBlockedError,
    FlowRevisionConflictError,
    FlowRevisionIntegrityError,
    FlowRevisionRequiredError,
    FlowService,
    InvalidFlowRunTransition,
)

router = APIRouter(prefix="/flows", tags=["flows"])


def _svc(db: AsyncSession = Depends(get_db)) -> FlowService:
    return FlowService(db)


# ── 1. Rotas estáticas (sem parâmetro de path) ─────────────────────────


@router.get("", response_model=list[FlowRead])
async def list_flows(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("automations", "manage")),
    svc: FlowService = Depends(_svc),
):
    return await svc.list_flows(workspace_id)


@router.post("", response_model=FlowRead, status_code=status.HTTP_201_CREATED)
async def create_flow(
    data: FlowCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("automations", "manage")),
    svc: FlowService = Depends(_svc),
):
    try:
        return await svc.create_flow(
            workspace_id,
            data,
            actor_user_id=auth.user_id,
        )
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error


@router.get("/templates", response_model=list[FlowRead])
async def list_templates(
    practice_area: str | None = None,
    _: AuthContext = Depends(require_permission("automations", "manage")),
    svc: FlowService = Depends(_svc),
):
    """Retorna biblioteca pública de templates por área do direito."""
    return await svc.get_templates(practice_area)


@router.get(
    "/catalog",
    response_model=FlowNodeCatalogRead,
    # Omite as chaves schema/uischema/outputSchema nos tipos ainda no contrato
    # v1 (aditivo/opcional): só o nó migrado as expõe.
    response_model_exclude_none=True,
    summary="Catálogo autoritativo dos nós reconhecidos pelo runtime",
)
async def get_flow_node_catalog(
    _: AuthContext = Depends(require_permission("automations", "view")),
):
    nodes = flow_node_catalog()
    return FlowNodeCatalogRead(
        node_count=len(nodes),
        external_node_types=sorted(FLOW_EXTERNAL_NODE_TYPES),
        nodes=nodes,
    )


@router.post(
    "/validate",
    response_model=FlowGraphValidationRead,
    summary="Compilar e validar um grafo sem executar efeitos",
)
async def validate_flow_draft(
    body: FlowGraphValidationRequest,
    _: AuthContext = Depends(require_permission("automations", "manage")),
):
    return analyze_flow_graph(body.nodes, body.edges)


@router.post(
    "/simulate",
    response_model=FlowGraphValidationRead,
    summary="Simular custo e gates estruturais de um grafo sem executar ferramentas",
)
async def simulate_flow_draft(
    body: FlowGraphValidationRequest,
    _: AuthContext = Depends(require_permission("automations", "manage")),
):
    # A simulation is deliberately structural and deterministic. Provider/model
    # execution remains in the governed dry-run endpoint after publication.
    return analyze_flow_graph(body.nodes, body.edges)


@router.post(
    "/copilot/plan",
    response_model=FlowCopilotPlanRead,
    summary="Propor um patch de fluxo governado e sujeito a aprovação",
)
async def plan_with_flow_copilot(
    body: FlowCopilotPlanRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("automations", "manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await FlowCopilotService(db).plan(workspace_id, body)
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error


@router.get(
    "/runs", response_model=list[FlowRunRead], summary="Listar execuções de flows do workspace"
)
async def list_flow_runs(
    matter_id: UUID | None = None,
    include_terminal: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("automations", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select

    from app.models.flow import FlowRun

    query = select(FlowRun).where(FlowRun.workspace_id == workspace_id)
    if matter_id:
        query = query.where(FlowRun.matter_id == matter_id)
    if not include_terminal:
        query = query.where(FlowRun.status.in_(["active", "paused", "waiting_human"]))
    result = await db.execute(query.order_by(FlowRun.started_at.desc()).limit(limit))
    runs = [FlowRunRead.model_validate(item) for item in result.scalars().all()]
    return runs if can_view_sensitive else [mask_flow_run(item) for item in runs]


@router.get(
    "/journeys/matters/{matter_id}",
    response_model=MatterFlowJourneyOverviewRead,
    summary="Timeline e KPIs dos flows vinculados ao Caso 360",
)
async def get_matter_flow_journeys(
    matter_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("kanban", "view")),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models.flow import FlowRun
    from app.models.matter import Matter

    matter_exists = await db.scalar(
        select(Matter.id).where(
            Matter.id == matter_id,
            Matter.workspace_id == workspace_id,
            Matter.deleted_at.is_(None),
        )
    )
    if matter_exists is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Caso não encontrado.")

    runs = list(
        (
            await db.scalars(
                select(FlowRun)
                .options(selectinload(FlowRun.flow), selectinload(FlowRun.revision))
                .where(
                    FlowRun.workspace_id == workspace_id,
                    FlowRun.matter_id == matter_id,
                )
                .order_by(FlowRun.started_at.desc())
                .limit(limit)
            )
        ).all()
    )
    now = datetime.now(UTC)
    journeys: list[MatterFlowJourneyRead] = []
    durations: list[int] = []

    for run in runs:
        try:
            snapshot = verified_flow_revision_snapshot(run.revision) if run.revision else {}
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A revisão vinculada à execução falhou na verificação de integridade.",
            ) from error
        nodes = snapshot.get("nodes") if isinstance(snapshot.get("nodes"), list) else []
        edges = snapshot.get("edges") if isinstance(snapshot.get("edges"), list) else []
        nodes_by_id = {
            str(node.get("id")): node
            for node in nodes
            if isinstance(node, dict) and node.get("id") is not None
        }
        context = run.context if isinstance(run.context, dict) else {}
        raw_trace = context.get("trace") if isinstance(context.get("trace"), list) else []
        trace = [entry for entry in raw_trace if isinstance(entry, dict)]
        timeline: list[MatterFlowJourneyStepRead] = []
        visited: set[str] = set()
        completed_steps = 0

        for index, entry in enumerate(trace):
            node_id = str(entry.get("nodeId") or "")
            if not node_id:
                continue
            visited.add(node_id)
            trace_status = str(entry.get("status") or "simulated")
            is_last = index == len(trace) - 1
            is_current_wait = (
                trace_status == "waiting_human"
                and run.status == "waiting_human"
                and node_id == run.current_node_id
            )
            if is_current_wait:
                state = "waiting_human"
            elif trace_status == "failed" or (run.status == "failed" and is_last):
                state = "failed"
            else:
                state = "completed"
                completed_steps += 1
            decision = entry.get("decision") if isinstance(entry.get("decision"), dict) else None
            safe_decision = (
                {key: decision[key] for key in ("field", "operator", "branch") if key in decision}
                if decision
                else None
            )
            timeline.append(
                MatterFlowJourneyStepRead(
                    node_id=node_id,
                    node_type=str(
                        entry.get("type") or nodes_by_id.get(node_id, {}).get("type") or "step"
                    ),
                    label=str(entry.get("label") or node_id),
                    state=state,
                    step=int(entry.get("step") or index + 1),
                    occurred_at=entry.get("occurredAt") or entry.get("completedAt"),
                    decision=safe_decision,
                )
            )

        current_node_id = run.current_node_id
        if (
            current_node_id
            and current_node_id not in visited
            and run.status not in {"completed", "cancelled"}
        ):
            current_node = nodes_by_id.get(current_node_id, {})
            current_data = (
                current_node.get("data") if isinstance(current_node.get("data"), dict) else {}
            )
            timeline.append(
                MatterFlowJourneyStepRead(
                    node_id=current_node_id,
                    node_type=str(current_node.get("type") or "step"),
                    label=str(
                        current_data.get("label") or current_node.get("label") or current_node_id
                    ),
                    state="waiting_human" if run.status == "waiting_human" else "current",
                    step=len(timeline) + 1,
                )
            )
            visited.add(current_node_id)

        if run.status not in {"completed", "cancelled", "failed"}:
            resume_node_id = context.get("flowResumeNodeId")
            frontier = [str(resume_node_id)] if resume_node_id else []
            if not frontier and current_node_id:
                frontier = [
                    str(edge.get("target"))
                    for edge in edges
                    if isinstance(edge, dict) and str(edge.get("source")) == current_node_id
                ]
            upcoming_seen: set[str] = set()
            while frontier and len(upcoming_seen) < 5:
                node_id = frontier.pop(0)
                if not node_id or node_id in visited or node_id in upcoming_seen:
                    continue
                upcoming_seen.add(node_id)
                node = nodes_by_id.get(node_id, {})
                data = node.get("data") if isinstance(node.get("data"), dict) else {}
                timeline.append(
                    MatterFlowJourneyStepRead(
                        node_id=node_id,
                        node_type=str(node.get("type") or "step"),
                        label=str(data.get("label") or node.get("label") or node_id),
                        state="upcoming",
                        step=len(timeline) + 1,
                    )
                )
                frontier.extend(
                    str(edge.get("target"))
                    for edge in edges
                    if isinstance(edge, dict) and str(edge.get("source")) == node_id
                )

        total_steps = max(len(nodes_by_id), completed_steps, 1)
        progress = (
            100.0
            if run.status == "completed"
            else round(min(99.0, completed_steps / total_steps * 100), 1)
        )
        finished_at = run.completed_at or now
        started_at = run.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        if finished_at.tzinfo is None:
            finished_at = finished_at.replace(tzinfo=UTC)
        elapsed = max(0, int((finished_at - started_at).total_seconds()))
        if run.completed_at is not None:
            durations.append(elapsed)
        current_step = next(
            (
                step.label
                for step in timeline
                if step.state in {"current", "waiting_human", "failed"}
            ),
            timeline[-1].label if timeline else None,
        )
        journeys.append(
            MatterFlowJourneyRead(
                run_id=run.id,
                flow_id=run.flow_id,
                flow_name=run.flow.name if run.flow else "Flow sem nome",
                revision=run.revision.revision_number if run.revision else 0,
                status=run.status,
                progress_percent=progress,
                completed_steps=completed_steps,
                total_steps=total_steps,
                current_node_id=current_node_id,
                current_step_label=current_step,
                pause_reason=run.pause_reason,
                error_message=run.error_message,
                started_at=run.started_at,
                completed_at=run.completed_at,
                paused_at=run.paused_at,
                elapsed_seconds=elapsed,
                timeline=timeline,
            )
        )

    completed_runs = sum(journey.status == "completed" for journey in journeys)
    failed_runs = sum(journey.status == "failed" for journey in journeys)
    active_runs = sum(journey.status in {"active", "paused"} for journey in journeys)
    waiting_runs = sum(journey.status == "waiting_human" for journey in journeys)
    total_completed_steps = sum(journey.completed_steps for journey in journeys)
    total_steps = sum(journey.total_steps for journey in journeys)
    overall_progress = (
        round(sum(journey.progress_percent for journey in journeys) / len(journeys), 1)
        if journeys
        else 0.0
    )
    terminal_count = completed_runs + failed_runs
    return MatterFlowJourneyOverviewRead(
        matter_id=matter_id,
        generated_at=now,
        kpis=MatterFlowJourneyKpisRead(
            total_runs=len(journeys),
            active_runs=active_runs,
            waiting_human_runs=waiting_runs,
            completed_runs=completed_runs,
            failed_runs=failed_runs,
            completion_rate=round(completed_runs / terminal_count * 100, 1)
            if terminal_count
            else 0.0,
            overall_progress_percent=overall_progress,
            average_duration_seconds=round(sum(durations) / len(durations)) if durations else None,
            completed_steps=total_completed_steps,
            total_steps=total_steps,
        ),
        journeys=journeys,
    )


@router.post(
    "/dry-runs",
    response_model=FlowDryRunRead,
    summary="Simular um grafo sem persistir execução ou produzir efeitos",
)
async def dry_run_flow(
    body: FlowDryRunRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("automations", "manage")),
    svc: FlowService = Depends(_svc),
):
    try:
        return await svc.dry_run_flow(
            body.flow_id,
            body.matter_id,
            workspace_id,
            body.context,
            max_steps=body.max_steps,
            revision_number=body.revision,
        )
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error


@router.get("/scheduled-messages", response_model=list[ScheduledMessageRead])
async def list_scheduled_messages(
    matter_id: UUID | None = None,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("automations", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    svc: FlowService = Depends(_svc),
):
    messages = await svc.list_scheduled_messages(workspace_id, matter_id)
    return messages if can_view_sensitive else [mask_scheduled_message(item) for item in messages]


@router.post(
    "/scheduled-messages", response_model=ScheduledMessageRead, status_code=status.HTTP_201_CREATED
)
async def create_scheduled_message(
    data: ScheduledMessageCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("automations", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    svc: FlowService = Depends(_svc),
):
    try:
        message = await svc.schedule_message(
            matter_id=data.matter_id,
            workspace_id=workspace_id,
            contact_phone=data.contact_phone,
            message=data.message,
            scheduled_at=data.scheduled_at,
            flow_run_id=data.flow_run_id,
        )
    except (
        ExternalDeliveryBlockedError,
        FlowRevisionIntegrityError,
        InvalidFlowRunTransition,
    ) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))
    return message if can_view_sensitive else mask_scheduled_message(message)


# ── 2. Rotas com /runs/{run_id} — static prefix "runs/" ────────────────


@router.get("/runs/{run_id}", response_model=FlowRunRead)
async def get_flow_run(
    run_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("automations", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select

    from app.models.flow import FlowRun

    result = await db.execute(
        select(FlowRun).where(FlowRun.id == run_id, FlowRun.workspace_id == workspace_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Flow run não encontrado")
    response = FlowRunRead.model_validate(run)
    return response if can_view_sensitive else mask_flow_run(response)


@router.post("/runs/{run_id}/pause", response_model=FlowRunRead)
async def pause_flow_run(
    run_id: UUID,
    reason: str = "",
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("automations", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    svc: FlowService = Depends(_svc),
):
    try:
        run = await svc.pause_flow_run(run_id, workspace_id, reason)
    except InvalidFlowRunTransition as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if not run:
        raise HTTPException(status_code=404, detail="Flow run não encontrado")
    response = FlowRunRead.model_validate(run)
    return response if can_view_sensitive else mask_flow_run(response)


@router.post("/runs/{run_id}/resume", response_model=FlowRunRead)
async def resume_flow_run(
    run_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("automations", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    svc: FlowService = Depends(_svc),
):
    try:
        run = await svc.resume_flow_run(run_id, workspace_id)
    except (
        ExternalDeliveryBlockedError,
        FlowRevisionIntegrityError,
        InvalidFlowRunTransition,
    ) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if not run:
        raise HTTPException(status_code=404, detail="Flow run não encontrado")
    response = FlowRunRead.model_validate(run)
    return response if can_view_sensitive else mask_flow_run(response)


@router.post(
    "/runs/{run_id}/external-action/reconcile",
    response_model=FlowRunRead,
    summary="Reconciliar um efeito externo de resultado incerto",
)
async def reconcile_flow_external_action(
    run_id: UUID,
    data: FlowExternalActionReconciliationRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("automations", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    svc: FlowService = Depends(_svc),
):
    try:
        run = await svc.reconcile_uncertain_external_action(
            run_id,
            workspace_id,
            outcome=data.outcome,
            actor_user_id=auth.user_id,
            provider_reference=data.provider_reference,
            note=data.note,
        )
    except (
        ExternalDeliveryBlockedError,
        FlowRevisionIntegrityError,
        InvalidFlowRunTransition,
    ) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if not run:
        raise HTTPException(status_code=404, detail="Flow run não encontrado")
    response = FlowRunRead.model_validate(run)
    return response if can_view_sensitive else mask_flow_run(response)


@router.post("/runs/{run_id}/cancel", response_model=FlowRunRead)
async def cancel_flow_run(
    run_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("automations", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    svc: FlowService = Depends(_svc),
):
    try:
        run = await svc.cancel_flow_run(run_id, workspace_id)
    except InvalidFlowRunTransition as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if not run:
        raise HTTPException(status_code=404, detail="Flow run não encontrado")
    response = FlowRunRead.model_validate(run)
    return response if can_view_sensitive else mask_flow_run(response)


@router.delete("/scheduled-messages/{msg_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_scheduled_message(
    msg_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("automations", "manage")),
    svc: FlowService = Depends(_svc),
):
    try:
        cancelled = await svc.cancel_scheduled_message(msg_id, workspace_id)
    except InvalidFlowRunTransition as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if not cancelled:
        raise HTTPException(status_code=404, detail="Mensagem não encontrada ou já enviada")


@router.post(
    "/scheduled-messages/{msg_id}/reconcile",
    response_model=ScheduledMessageRead,
    summary="Reconciliar uma entrega agendada de resultado incerto",
)
async def reconcile_scheduled_message(
    msg_id: UUID,
    data: ScheduledMessageReconciliationRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("automations", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    svc: FlowService = Depends(_svc),
):
    try:
        message = await svc.reconcile_scheduled_message(
            msg_id,
            workspace_id,
            outcome=data.outcome,
            actor_user_id=auth.user_id,
            provider_reference=data.provider_reference,
            retry_at=data.retry_at,
            note=data.note,
        )
    except (ExternalDeliveryBlockedError, InvalidFlowRunTransition) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if not message:
        raise HTTPException(status_code=404, detail="Mensagem agendada não encontrada")
    response = ScheduledMessageRead.model_validate(message)
    return response if can_view_sensitive else mask_scheduled_message(response)


# ── 3. Templates — apply precisa vir antes de /{flow_id} ───────────────


@router.post(
    "/templates/{template_id}/apply", response_model=FlowRead, status_code=status.HTTP_201_CREATED
)
async def apply_template(
    template_id: UUID,
    custom_name: str | None = None,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("automations", "manage")),
    svc: FlowService = Depends(_svc),
):
    """Copia template da biblioteca para o workspace do usuário."""
    try:
        return await svc.apply_template(
            template_id,
            workspace_id,
            custom_name,
            actor_user_id=auth.user_id,
        )
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── 4. Rotas parametrizadas — devem ficar por último ───────────────────


@router.get("/{flow_id}/revisions", response_model=list[FlowRevisionRead])
async def list_flow_revisions(
    flow_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("automations", "manage")),
    svc: FlowService = Depends(_svc),
):
    revisions = await svc.list_flow_revisions(flow_id, workspace_id)
    if revisions is None:
        raise HTTPException(status_code=404, detail="Flow não encontrado")
    return revisions


@router.get(
    "/{flow_id}/revisions/{revision_number}",
    response_model=FlowRevisionRead,
)
async def get_flow_revision(
    flow_id: UUID,
    revision_number: int,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("automations", "manage")),
    svc: FlowService = Depends(_svc),
):
    revision = await svc.get_flow_revision(
        flow_id,
        revision_number,
        workspace_id,
    )
    if not revision:
        raise HTTPException(status_code=404, detail="Revisão do flow não encontrada")
    return revision


@router.get("/{flow_id}", response_model=FlowRead)
async def get_flow(
    flow_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("automations", "manage")),
    svc: FlowService = Depends(_svc),
):
    flow = await svc.get_flow(flow_id, workspace_id)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow não encontrado")
    return flow


@router.put("/{flow_id}", response_model=FlowRead)
async def update_flow(
    flow_id: UUID,
    data: FlowUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("automations", "manage")),
    svc: FlowService = Depends(_svc),
):
    try:
        flow = await svc.update_flow(
            flow_id,
            workspace_id,
            data,
            actor_user_id=auth.user_id,
        )
    except FlowRevisionConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "stale_flow_revision",
                "message": str(error),
                "expected_revision": error.expected_revision,
                "current_revision": error.current_revision,
            },
        ) from error
    except FlowRevisionRequiredError as error:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail={
                "code": "expected_revision_required",
                "message": str(error),
                "current_revision": error.current_revision,
            },
        ) from error
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        )
    if not flow:
        raise HTTPException(status_code=404, detail="Flow não encontrado")
    return flow


@router.delete("/{flow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_flow(
    flow_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("automations", "manage")),
    svc: FlowService = Depends(_svc),
):
    deleted = await svc.delete_flow(flow_id, workspace_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Flow não encontrado")


@router.post("/{flow_id}/run", response_model=FlowRunRead, status_code=status.HTTP_201_CREATED)
async def run_flow(
    flow_id: UUID,
    body: FlowRunRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_permission("automations", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    svc: FlowService = Depends(_svc),
):
    """Inicia execução de um flow para um matter."""
    try:
        run = await svc.start_flow_run(
            flow_id,
            body.matter_id,
            workspace_id,
            body.context,
            thread_id=body.thread_id,
            actor_user_id=auth.user_id,
        )
        response = FlowRunRead.model_validate(run)
        return response if can_view_sensitive else mask_flow_run(response)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
