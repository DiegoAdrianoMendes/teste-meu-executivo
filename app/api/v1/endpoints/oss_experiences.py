"""Public operational experiences owned by the KLM product."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    AuthContext,
    can_view_sensitive_lead_data,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.schemas.native_public import (
    KLMAutomationContextRead,
    KLMAutomationSyncRead,
    KLMCollaborationSyncRead,
    KLMCollaborationThreadRead,
    KLMCommunicationRoutingWrite,
    KLMCommunicationSyncRead,
    KLMCommunicationThreadRead,
    KLMCRMFinanceRead,
    KLMCRMFunnelRead,
    KLMCRMMatterRead,
    KLMCRMWorkRead,
    KLMFlowContextRead,
    KLMFlowSyncRead,
)
from app.services.native_public_boundary import (
    public_automation_context,
    public_automation_sync,
    public_collaboration_sync,
    public_collaboration_thread,
    public_communication_sync,
    public_communication_thread,
    public_crm_finance,
    public_crm_funnel,
    public_crm_matter,
    public_crm_work,
    public_flow_context,
    public_flow_sync,
)
from app.services.oss_native_experience_service import OSSNativeExperienceService

router = APIRouter(tags=["KLM Native Operations"])


def _not_found(error: LookupError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(error))


@router.get(
    "/communications/threads/{thread_id}",
    response_model=KLMCommunicationThreadRead,
)
async def get_native_chat_context(
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> KLMCommunicationThreadRead:
    try:
        result = await OSSNativeExperienceService(db).chat_context(
            workspace_id,
            thread_id,
        )
    except LookupError as error:
        raise _not_found(error) from error
    return KLMCommunicationThreadRead.model_validate(public_communication_thread(result))


@router.post(
    "/communications/threads/{thread_id}/reconcile",
    response_model=KLMCommunicationSyncRead,
)
async def reconcile_native_chat(
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> KLMCommunicationSyncRead:
    try:
        result = await OSSNativeExperienceService(db).reconcile_chat(
            workspace_id=workspace_id,
            thread_id=thread_id,
            actor_user_id=auth.user_id,
        )
    except LookupError as error:
        raise _not_found(error) from error
    return KLMCommunicationSyncRead.model_validate(public_communication_sync(result))


@router.patch(
    "/communications/threads/{thread_id}/routing",
    response_model=KLMCommunicationThreadRead,
)
async def update_native_chat_routing(
    thread_id: UUID,
    payload: KLMCommunicationRoutingWrite,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> KLMCommunicationThreadRead:
    try:
        result = await OSSNativeExperienceService(db).update_chat_routing(
            workspace_id=workspace_id,
            thread_id=thread_id,
            actor_user_id=auth.user_id,
            changes=payload,
        )
    except LookupError as error:
        raise _not_found(error) from error
    return KLMCommunicationThreadRead.model_validate(public_communication_thread(result))


@router.get(
    "/collaboration/threads/{thread_id}",
    response_model=KLMCollaborationThreadRead,
)
async def get_native_collaboration_context(
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("mensagens", "view")),
) -> KLMCollaborationThreadRead:
    try:
        result = await OSSNativeExperienceService(db).collaboration_context(
            workspace_id,
            thread_id,
        )
    except LookupError as error:
        raise _not_found(error) from error
    if not can_view_sensitive:
        result = {
            **result,
            "room": {
                **(result.get("room") or {}),
                "name": "sala-restrita",
                "fname": "Sala interna restrita",
            },
            "messages": [
                {
                    **message,
                    "msg": "[conteudo oculto pela permissao de dados do lead]",
                    "user": {"name": "Membro da equipe"},
                }
                for message in result["messages"]
            ],
            "discussions": [
                {
                    **discussion,
                    "name": "discussao-restrita",
                    "fname": "Discussao restrita",
                }
                for discussion in result["discussions"]
            ],
            "sensitiveDataHidden": True,
        }
    return KLMCollaborationThreadRead.model_validate(public_collaboration_thread(result))


@router.post(
    "/collaboration/threads/{thread_id}/reconcile",
    response_model=KLMCollaborationSyncRead,
)
async def reconcile_native_collaboration(
    thread_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_permission("mensagens", "send_message")),
) -> KLMCollaborationSyncRead:
    try:
        result = await OSSNativeExperienceService(db).reconcile_collaboration(
            workspace_id=workspace_id,
            thread_id=thread_id,
            actor_user_id=auth.user_id,
        )
    except LookupError as error:
        raise _not_found(error) from error
    return KLMCollaborationSyncRead.model_validate(public_collaboration_sync(result))


@router.get(
    "/automation/agents",
    response_model=KLMAutomationContextRead,
)
async def get_native_agent_context(
    time_range: str = Query(default="7d", pattern="^(24h|7d|30d|90d)$"),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("agents", "view")),
) -> KLMAutomationContextRead:
    result = await OSSNativeExperienceService(db).agent_context(
        workspace_id,
        time_range,
        include_sensitive=can_view_sensitive,
    )
    return KLMAutomationContextRead.model_validate(public_automation_context(result))


@router.post(
    "/automation/agents/reconcile",
    response_model=KLMAutomationSyncRead,
)
async def reconcile_native_agents(
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_permission("agents", "manage")),
) -> KLMAutomationSyncRead:
    result = await OSSNativeExperienceService(db).reconcile_agents(
        workspace_id=workspace_id,
        actor_user_id=auth.user_id,
    )
    return KLMAutomationSyncRead.model_validate(public_automation_sync(result))


@router.get(
    "/automation/flows/{flow_id}",
    response_model=KLMFlowContextRead,
)
async def get_native_flow_context(
    flow_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: AuthContext = Depends(require_permission("automations", "manage")),
) -> KLMFlowContextRead:
    try:
        result = await OSSNativeExperienceService(db).flow_context(
            workspace_id,
            flow_id,
        )
    except LookupError as error:
        raise _not_found(error) from error
    return KLMFlowContextRead.model_validate(public_flow_context(result))


@router.post(
    "/automation/flows/{flow_id}/reconcile",
    response_model=KLMFlowSyncRead,
)
async def reconcile_native_flow(
    flow_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_permission("automations", "manage")),
) -> KLMFlowSyncRead:
    try:
        result = await OSSNativeExperienceService(db).reconcile_flow(
            workspace_id=workspace_id,
            flow_id=flow_id,
            actor_user_id=auth.user_id,
        )
    except LookupError as error:
        raise _not_found(error) from error
    return KLMFlowSyncRead.model_validate(public_flow_sync(result))


@router.get(
    "/crm/funnel",
    response_model=KLMCRMFunnelRead,
)
async def get_native_crm_funnel(
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("kanban", "view")),
) -> KLMCRMFunnelRead:
    result = await OSSNativeExperienceService(db).crm_funnel_context(
        workspace_id,
        include_sensitive=can_view_sensitive,
    )
    return KLMCRMFunnelRead.model_validate(public_crm_funnel(result))


@router.get(
    "/crm/matters/{matter_id}",
    response_model=KLMCRMMatterRead,
)
async def get_native_crm_matter(
    matter_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("lista", "view")),
) -> KLMCRMMatterRead:
    try:
        result = await OSSNativeExperienceService(db).crm_matter_context(
            workspace_id,
            matter_id,
            include_sensitive=can_view_sensitive,
        )
    except LookupError as error:
        raise _not_found(error) from error
    return KLMCRMMatterRead.model_validate(public_crm_matter(result))


@router.get(
    "/crm/work",
    response_model=KLMCRMWorkRead,
)
async def get_native_crm_work(
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("lista", "view")),
) -> KLMCRMWorkRead:
    result = await OSSNativeExperienceService(db).crm_work_context(
        workspace_id,
        include_sensitive=can_view_sensitive,
    )
    return KLMCRMWorkRead.model_validate(public_crm_work(result))


@router.get(
    "/crm/finance",
    response_model=KLMCRMFinanceRead,
)
async def get_native_crm_finance(
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: AuthContext = Depends(require_permission("finance", "view")),
) -> KLMCRMFinanceRead:
    result = await OSSNativeExperienceService(db).crm_finance_context(
        workspace_id,
        include_sensitive=can_view_sensitive,
    )
    return KLMCRMFinanceRead.model_validate(public_crm_finance(result))
