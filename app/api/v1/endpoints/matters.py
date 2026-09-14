"""
Endpoint: Matters
CRUD e operações de Kanban para casos jurídicos.
Integrado com EventBus para acionar agentes de IA automaticamente.
"""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    can_view_sensitive_lead_data,
    get_current_user_id,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.core.lead_privacy import (
    mask_audit_log,
    mask_field_change,
    mask_matter,
    mask_matter_list_item,
    redact_sensitive_metadata,
)
from app.schemas.card_dossier import AuditLogRead, CardFieldChangeRead
from app.schemas.matter import Case360Update, MatterCreate, MatterListItem, MatterMoveRequest, MatterRead, MatterUpdate
from app.services.matter_service import MatterService
from app.models.card_field_change import CardFieldChange
from app.models.timeline_event import TimelineEventType
from app.websockets.manager import ws_manager

router = APIRouter(prefix="/matters", tags=["Casos (Matters)"])


@router.get("", response_model=List[MatterListItem], summary="Listar casos")
async def list_matters(
    # Filtros básicos
    search: Optional[str] = Query(None, description="Busca por nome do cliente, tipo de demanda ou nº do processo"),
    column_id: Optional[str] = Query(None, description="Filtrar por fase do Kanban"),
    priority: Optional[str] = Query(None, description="Filtrar por prioridade: alta | media | baixa"),
    urgency: Optional[str] = Query(None, description="Filtrar por urgência: urgente | normal | baixa"),
    micro_status: Optional[str] = Query(None, description="Filtrar por microstatus"),
    classification: Optional[str] = Query(None, description="Filtrar por classificação do cliente"),
    lead_source: Optional[str] = Query(None, description="Filtrar por origem do lead"),
    # Filtros booleanos
    is_blocked: Optional[bool] = Query(None, description="Filtrar apenas bloqueados"),
    needs_approval: Optional[bool] = Query(None, description="Filtrar apenas com aprovação pendente"),
    # Filtros avançados
    agent_id: Optional[str] = Query(None, description="Filtrar por agente responsável ou atribuído"),
    doc_score_below: Optional[int] = Query(None, description="Filtrar por docScore abaixo de X%"),
    close_intent: Optional[str] = Query(None, description="Filtrar por intenção de fechamento identificada na conversa"),
    document_readiness: Optional[str] = Query(None, description="Filtrar por prontidão documental da conversa"),
    has_unread_messages: Optional[bool] = Query(None, description="Filtrar por leads com mensagens não lidas"),
    has_financial_pending: Optional[bool] = Query(None, description="Filtrar por pendência financeira operacional"),
    updated_within_days: Optional[int] = Query(None, ge=1, description="Filtrar por cards atualizados nos últimos X dias"),
    min_claim_value: Optional[float] = Query(None, ge=0, description="Filtrar por valor mínimo estimado"),
    max_claim_value: Optional[float] = Query(None, ge=0, description="Filtrar por valor máximo estimado"),
    # Paginação
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("kanban", "view")),
):
    """
    Retorna a lista de casos para o Kanban e a visão de Lista.
    Suporta todos os filtros do AdvancedFilterBar do frontend.
    """
    service = MatterService(db)
    matters = await service.get_all(
        search=search,
        include_sensitive_search=can_view_sensitive,
        workspace_id=workspace_id,
        column_id=column_id,
        priority=priority,
        urgency=urgency,
        micro_status=micro_status,
        classification=classification,
        lead_source=lead_source,
        is_blocked=is_blocked,
        needs_approval=needs_approval,
        agent_id=agent_id,
        doc_score_below=doc_score_below,
        close_intent=close_intent,
        document_readiness=document_readiness,
        has_unread_messages=has_unread_messages,
        has_financial_pending=has_financial_pending,
        updated_within_days=updated_within_days,
        min_claim_value=min_claim_value,
        max_claim_value=max_claim_value,
        skip=skip,
        limit=limit,
    )
    return matters if can_view_sensitive else [mask_matter_list_item(item) for item in matters]


@router.post("", response_model=MatterRead, status_code=status.HTTP_201_CREATED, summary="Criar caso")
async def create_matter(
    data: MatterCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("kanban", "create")),
):
    service = MatterService(db)
    try:
        matter = await service.create(data, workspace_id=workspace_id, created_by=current_user_id)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    await ws_manager.broadcast_board_update(
        "CARD_CREATED",
        {"matter_id": str(matter.id)},
        workspace_id=workspace_id,
    )

    # Acionar SDR para qualificação automática de novos leads
    from app.workers.event_bus import dispatch_card_moved
    background_tasks.add_task(
        dispatch_card_moved,
        matter.id,
        data.column_id.value,
        current_user_id,
        workspace_id=workspace_id,
    )

    return matter if can_view_sensitive else mask_matter(matter)


@router.get("/{matter_id}", response_model=MatterRead, summary="Detalhe do caso")
async def get_matter(
    matter_id: UUID,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("kanban", "view")),
):
    """Retorna o MatterCard completo com tasks, documentos, timeline e conversas."""
    service = MatterService(db)
    matter = await service.get_by_id(matter_id, workspace_id)
    if not matter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Caso não encontrado.")
    return matter if can_view_sensitive else mask_matter(matter)


@router.patch("/{matter_id}", response_model=MatterRead, summary="Atualizar campos do caso")
async def update_matter(
    matter_id: UUID,
    data: MatterUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    requested_change_source: str = Header(default="manual_api", alias="X-KLM-Change-Source"),
    _: object = Depends(require_permission("kanban", "edit")),
):
    """
    Atualização parcial de campos do card.
    Usado pelo frontend para updateCardField (starsRating, strategyNotes, etc.).
    """
    service = MatterService(db)
    change_source = requested_change_source if requested_change_source in {
        "manual_api",
        "manual_spa",
        "manual_case_360",
    } else "manual_api"
    try:
        matter = await service.update(
            matter_id,
            data,
            updated_by=current_user_id,
            workspace_id=workspace_id,
            change_source=change_source,
        )
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    if not matter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Caso não encontrado.")
    await ws_manager.broadcast_board_update(
        "CARD_UPDATED",
        {"matter_id": str(matter_id)},
        workspace_id=workspace_id,
    )
    return matter if can_view_sensitive else mask_matter(matter)


@router.patch(
    "/{matter_id}/case-360",
    response_model=MatterRead,
    summary="Atualizar cliente e dossiê de forma atômica no Caso 360",
)
async def update_case_360(
    matter_id: UUID,
    data: Case360Update,
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("kanban", "edit")),
    __: object = Depends(require_permission("lead_data", "edit")),
):
    service = MatterService(db)
    matter = await service.get_by_id(matter_id, workspace_id)
    if matter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Caso não encontrado.")

    if data.client is not None:
        client_changes = []
        for field, value in data.client.model_dump(exclude_unset=True).items():
            if field == "name" and (value is None or not value.strip()):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="O nome do cliente não pode ficar vazio.",
                )
            old_value = getattr(matter.client, field)
            if old_value == value:
                continue
            client_changes.append((field, old_value, value))
            setattr(matter.client, field, value)
        await service.record_manual_client_changes(
            workspace_id=workspace_id,
            client_id=matter.client_id,
            changes=client_changes,
            updated_by=current_user_id,
        )

    if data.attribution is not None:
        await service.update_manual_attribution(
            matter=matter,
            values=data.attribution.model_dump(exclude_unset=True),
            updated_by=current_user_id,
        )

    try:
        updated = await service.update(
            matter_id,
            data.matter,
            updated_by=current_user_id,
            workspace_id=workspace_id,
            change_source="manual_case_360",
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Caso não encontrado.")
    await ws_manager.broadcast_board_update(
        "CARD_UPDATED",
        {"matter_id": str(matter_id), "source": "manual_case_360"},
        workspace_id=workspace_id,
    )
    return updated if can_view_sensitive else mask_matter(updated)


@router.put("/{matter_id}/move", response_model=MatterRead, summary="Mover card no Kanban (drag-and-drop)")
async def move_matter(
    matter_id: UUID,
    move: MatterMoveRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("kanban", "edit")),
):
    """
    Move o card para uma nova coluna do Kanban.
    Registra evento na timeline, notifica via WebSocket e aciona agente de IA se necessário.

    Chamado pelo frontend quando o usuário solta um card em uma nova coluna (drag-and-drop).
    """
    service = MatterService(db)
    matter = await service.move_card(matter_id, move, moved_by=current_user_id, workspace_id=workspace_id)
    if not matter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Caso não encontrado.")

    await ws_manager.broadcast_board_update(
        "CARD_MOVED",
        {
            "matter_id": str(matter_id),
            "column_id": move.column_id.value,
            "micro_status": move.micro_status.value if move.micro_status else None,
        },
        workspace_id=workspace_id,
    )

    # EventBus: acionar agente de IA se a nova coluna tiver um agente associado
    from app.workers.event_bus import dispatch_card_moved
    background_tasks.add_task(
        dispatch_card_moved,
        matter_id,
        move.column_id.value,
        current_user_id,
        workspace_id=workspace_id,
    )

    return matter if can_view_sensitive else mask_matter(matter)


@router.put("/{matter_id}/reorder", summary="Reordenar card dentro da coluna")
async def reorder_matter(
    matter_id: UUID,
    body: dict,
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("kanban", "edit")),
):
    """
    Salva a nova posição (order) de um card dentro de uma coluna.
    Chamado pelo frontend após drag-and-drop vertical dentro da mesma coluna.

    Body: { "order": int }
    """
    new_order = body.get("order")
    if new_order is None or not isinstance(new_order, int):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Campo 'order' (int) é obrigatório.",
        )
    service = MatterService(db)
    matter = await service.update_order(matter_id, new_order, workspace_id=workspace_id)
    if not matter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Caso não encontrado.")
    await ws_manager.broadcast_board_update(
        "CARD_REORDERED",
        {"matter_id": str(matter_id), "order": new_order},
        workspace_id=workspace_id,
    )
    return {"message": "Ordem atualizada.", "matter_id": str(matter_id), "order": new_order}


@router.get("/{matter_id}/notes", summary="Listar observações do caso")
async def list_notes(
    matter_id: UUID,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("kanban", "view")),
):
    """
    Retorna as observações (timeline events do tipo 'comment') do caso.
    Chamado pelo frontend no DetailDrawer para exibir o histórico de notas.
    """
    service = MatterService(db)
    matter = await service.get_by_id(matter_id, workspace_id)
    if not matter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Caso não encontrado.")
    notes = [e for e in matter.timeline_events if e.event_type == TimelineEventType.comment]
    names = (matter.client.name,) if matter.client else ()
    return [
        {
            "id": str(note.id),
            "content": note.description
            if can_view_sensitive
            else redact_sensitive_metadata(note.description, names),
            "createdAt": note.timestamp.isoformat(),
        }
        for note in notes
    ]


@router.get("/{matter_id}/audit", response_model=List[AuditLogRead], summary="Auditoria granular do card")
async def get_matter_audit(
    matter_id: UUID,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("kanban", "view")),
):
    service = MatterService(db)
    matter = await service.get_by_id(matter_id, workspace_id)
    if not matter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Caso não encontrado.")
    if can_view_sensitive:
        return matter.audit_logs
    names = (matter.client.name,) if matter.client else ()
    return [mask_audit_log(item, names) for item in matter.audit_logs]


@router.get("/{matter_id}/field-changes", response_model=List[CardFieldChangeRead], summary="Mudanças de campo do dossiê")
async def get_matter_field_changes(
    matter_id: UUID,
    db: AsyncSession = Depends(get_db),
    workspace_id: UUID = Depends(get_current_workspace_id),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("kanban", "view")),
):
    service = MatterService(db)
    matter = await service.get_by_id(matter_id, workspace_id)
    if not matter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Caso não encontrado.")
    changes = list(
        (
            await db.scalars(
                select(CardFieldChange)
                .where(CardFieldChange.matter_id == matter_id)
                .order_by(CardFieldChange.created_at.desc())
            )
        ).all()
    )
    if can_view_sensitive:
        return changes
    names = (matter.client.name,) if matter.client else ()
    return [mask_field_change(item, names) for item in changes]


@router.post("/{matter_id}/notes", status_code=status.HTTP_201_CREATED, summary="Adicionar observação")
async def add_note(
    matter_id: UUID,
    body: dict,
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("kanban", "edit")),
):
    """
    Adiciona uma observação rápida na timeline do caso.
    Chamado pelo frontend no DetailDrawer (campo 'Observação rápida').
    """
    note = body.get("note", "").strip()
    if not note:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Nota não pode ser vazia.")
    service = MatterService(db)
    event = await service.add_note(matter_id, note, agent_id=current_user_id, workspace_id=workspace_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Caso não encontrado.")
    return {"message": "Observação adicionada.", "event_id": str(event.id)}


@router.delete("/{matter_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Arquivar caso")
async def delete_matter(
    matter_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: object = Depends(require_permission("kanban", "delete")),
):
    service = MatterService(db)
    deleted = await service.soft_delete(matter_id, workspace_id=workspace_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Caso não encontrado.")
    await ws_manager.broadcast_board_update(
        "CARD_DELETED",
        {"matter_id": str(matter_id)},
        workspace_id=workspace_id,
    )
