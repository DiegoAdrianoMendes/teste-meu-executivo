from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_permission
from app.models.client import Client
from app.models.client_contract import ClientContract
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import Matter
from app.schemas.client_contract import ClientContractCreate, ClientContractRead

router = APIRouter(prefix="/legal/client-contracts", tags=["Jurídico — contratos de clientes"])


@router.get("", response_model=list[ClientContractRead])
async def list_client_contracts(
    client_id: UUID | None = None,
    search: str | None = Query(default=None, max_length=120),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("legal", "view")),
) -> list[ClientContractRead]:
    query = select(ClientContract).where(
        ClientContract.workspace_id == workspace_id,
        ClientContract.deleted_at.is_(None),
    )
    if client_id:
        query = query.where(ClientContract.client_id == client_id)
    if search and search.strip():
        query = query.where(ClientContract.title.ilike(f"%{search.strip()}%"))
    rows = (await db.scalars(query.order_by(ClientContract.updated_at.desc()).limit(50))).all()
    return [ClientContractRead.model_validate(row) for row in rows]


@router.post("", response_model=ClientContractRead, status_code=status.HTTP_201_CREATED)
async def create_client_contract(
    payload: ClientContractCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_permission("legal", "manage")),
) -> ClientContractRead:
    client = await db.scalar(
        select(Client).where(
            Client.id == payload.client_id,
            Client.workspace_id == workspace_id,
            Client.deleted_at.is_(None),
        )
    )
    if client is None:
        raise HTTPException(status_code=404, detail="Cliente não encontrado.")
    if payload.matter_id:
        matter = await db.scalar(
            select(Matter).where(
                Matter.id == payload.matter_id,
                Matter.workspace_id == workspace_id,
                Matter.client_id == payload.client_id,
                Matter.deleted_at.is_(None),
            )
        )
        if matter is None:
            raise HTTPException(status_code=422, detail="Caso incompatível com o cliente.")
    contract = ClientContract(workspace_id=workspace_id, **payload.model_dump())
    db.add(contract)
    await db.flush()
    db.add(
        KlmAuditEvent(
            workspace_id=workspace_id,
            actor_user_id=auth.user_id,
            actor_type="human",
            actor_label="Usuário autenticado",
            event_type="client_contract_created",
            entity_type="client_contract",
            entity_id=contract.id,
            summary="Contrato jurídico do cliente criado.",
            before_data=None,
            after_data={
                "clientId": str(contract.client_id),
                "matterId": str(contract.matter_id) if contract.matter_id else None,
                "title": contract.title,
                "status": contract.status.value,
                "version": contract.version,
            },
        )
    )
    await db.commit()
    await db.refresh(contract)
    return ClientContractRead.model_validate(contract)
