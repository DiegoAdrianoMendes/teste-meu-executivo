"""
Endpoint: Clients
CRUD de clientes do workspace.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    can_view_sensitive_lead_data,
    get_current_user_id,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.core.lead_privacy import mask_client
from app.models.client import Client
from app.schemas.client import ClientCreate, ClientRead, ClientUpdate
from app.services.client_command_service import ClientCommandService

router = APIRouter(prefix="/clients", tags=["Clientes"])


@router.get("", response_model=list[ClientRead], summary="Listar clientes do workspace")
async def list_clients(
    search: str | None = Query(None, description="Busca por nome, CPF, email ou telefone"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("kanban", "view")),
):
    if search and not can_view_sensitive:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PRIVACY-001",
                "message": "A busca por dados de contato está desativada para este acesso.",
            },
        )

    query = (
        select(Client)
        .where(Client.workspace_id == workspace_id, Client.deleted_at.is_(None))
        .order_by(Client.name.asc() if can_view_sensitive else Client.created_at.desc())
    )
    if search:
        term = f"%{search}%"
        query = query.where(
            or_(
                Client.name.ilike(term),
                Client.cpf.ilike(term),
                Client.email.ilike(term),
                Client.phone.ilike(term),
            )
        )
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    clients = [ClientRead.model_validate(c) for c in result.scalars().all()]
    return clients if can_view_sensitive else [mask_client(item) for item in clients]


@router.post(
    "", response_model=ClientRead, status_code=status.HTTP_201_CREATED, summary="Criar cliente"
)
async def create_client(
    data: ClientCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("kanban", "create")),
):
    client = Client(**data.model_dump(), workspace_id=workspace_id)
    db.add(client)
    await db.commit()
    await db.refresh(client)
    response = ClientRead.model_validate(client)
    return response if can_view_sensitive else mask_client(response)


@router.get("/{client_id}", response_model=ClientRead, summary="Detalhe do cliente")
async def get_client(
    client_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    _: object = Depends(require_permission("kanban", "view")),
):
    result = await db.execute(
        select(Client).where(
            Client.id == client_id,
            Client.workspace_id == workspace_id,
            Client.deleted_at.is_(None),
        )
    )
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    response = ClientRead.model_validate(client)
    return response if can_view_sensitive else mask_client(response)


@router.patch("/{client_id}", response_model=ClientRead, summary="Atualizar cliente")
async def update_client(
    client_id: UUID,
    data: ClientUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    current_user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _: object = Depends(require_permission("kanban", "edit")),
    __: object = Depends(require_permission("lead_data", "edit")),
):
    values = data.model_dump(exclude_unset=True)
    expected_updated_at = values.pop("expected_updated_at", None)
    client = await ClientCommandService(db).update(
        workspace_id=workspace_id,
        client_id=client_id,
        changes=values,
        actor_user_id=current_user_id,
        expected_updated_at=expected_updated_at,
        idempotency_key=idempotency_key,
        source="clients_endpoint",
    )
    await db.commit()
    await db.refresh(client)
    response = ClientRead.model_validate(client)
    return response if can_view_sensitive else mask_client(response)
