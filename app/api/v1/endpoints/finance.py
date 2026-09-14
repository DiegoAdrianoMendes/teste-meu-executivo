"""
Endpoints financeiros tenant-aware.
"""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.entitlement_errors import entitlement_http_exception
from app.core.deps import (
    AuthContext,
    can_view_sensitive_lead_data,
    get_current_workspace_id,
    get_db,
    require_finance_access,
)
from app.core.lead_privacy import mask_finance_entry, mask_finance_overview, mask_finance_reminder
from app.models.finance import FinanceEntryStatus, FinanceReminderStatus
from app.schemas.finance import (
    FinanceEntryCreate,
    FinanceEntryRead,
    FinanceEntryUpdate,
    FinanceOverviewRead,
    FinanceReminderRead,
    FinanceReminderUpdate,
    FinanceSettingsRead,
    FinanceSettingsUpdate,
    IntegrationConnectionCreate,
    IntegrationConnectionRead,
    IntegrationConnectionUpdate,
)
from app.services.entitlement_service import EntitlementError
from app.services.finance_service import FinanceService

router = APIRouter(prefix="/finance", tags=["Financeiro"])


@router.get("/overview", response_model=FinanceOverviewRead, summary="Visão geral financeira do workspace")
async def get_finance_overview(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
):
    overview = await FinanceService(db).get_overview(workspace_id)
    return overview if can_view_sensitive else mask_finance_overview(overview)


@router.get("/settings", response_model=FinanceSettingsRead, summary="Configurações financeiras do workspace")
async def get_finance_settings(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    return await FinanceService(db).get_settings(workspace_id)


@router.patch("/settings", response_model=FinanceSettingsRead, summary="Atualizar configurações financeiras")
async def update_finance_settings(
    payload: FinanceSettingsUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    return await FinanceService(db).update_settings(workspace_id, payload)


@router.get("/entries", response_model=List[FinanceEntryRead], summary="Listar lançamentos financeiros")
async def list_finance_entries(
    status_filter: Optional[FinanceEntryStatus] = Query(None, alias="status"),
    provider: Optional[str] = Query(None),
    matter_id: Optional[UUID] = Query(None),
    upcoming_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=300),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
):
    entries = await FinanceService(db).list_entries(
        workspace_id,
        status=status_filter,
        provider=provider,
        matter_id=matter_id,
        upcoming_only=upcoming_only,
        limit=limit,
    )
    response = [FinanceEntryRead.model_validate(entry) for entry in entries]
    return response if can_view_sensitive else [mask_finance_entry(item) for item in response]


@router.post("/entries", response_model=FinanceEntryRead, status_code=status.HTTP_201_CREATED, summary="Criar lançamento financeiro")
async def create_finance_entry(
    payload: FinanceEntryCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
):
    entry = await FinanceService(db).create_entry(workspace_id, payload)
    response = FinanceEntryRead.model_validate(entry)
    return response if can_view_sensitive else mask_finance_entry(response)


@router.patch("/entries/{entry_id}", response_model=FinanceEntryRead, summary="Atualizar lançamento financeiro")
async def update_finance_entry(
    entry_id: UUID,
    payload: FinanceEntryUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
):
    entry = await FinanceService(db).update_entry(workspace_id, entry_id, payload)
    response = FinanceEntryRead.model_validate(entry)
    return response if can_view_sensitive else mask_finance_entry(response)


@router.get("/reminders", response_model=List[FinanceReminderRead], summary="Listar lembretes financeiros")
async def list_finance_reminders(
    status_filter: Optional[FinanceReminderStatus] = Query(None, alias="status"),
    due_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=300),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
):
    reminders = await FinanceService(db).list_reminders(
        workspace_id,
        status=status_filter,
        due_only=due_only,
        limit=limit,
    )
    response = [FinanceReminderRead.model_validate(reminder) for reminder in reminders]
    return response if can_view_sensitive else [mask_finance_reminder(item) for item in response]


@router.patch("/reminders/{reminder_id}", response_model=FinanceReminderRead, summary="Atualizar lembrete financeiro")
async def update_finance_reminder(
    reminder_id: UUID,
    payload: FinanceReminderUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
):
    reminder = await FinanceService(db).update_reminder(workspace_id, reminder_id, payload)
    response = FinanceReminderRead.model_validate(reminder)
    return response if can_view_sensitive else mask_finance_reminder(response)


@router.get("/integrations", response_model=List[IntegrationConnectionRead], summary="Listar integrações financeiras")
async def list_finance_integrations(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    connections = await FinanceService(db).list_integrations(workspace_id)
    return [IntegrationConnectionRead.model_validate(connection) for connection in connections]


@router.post("/integrations", response_model=IntegrationConnectionRead, status_code=status.HTTP_201_CREATED, summary="Criar integração financeira")
async def create_finance_integration(
    payload: IntegrationConnectionCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    try:
        connection = await FinanceService(db).create_integration(workspace_id, payload)
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    return IntegrationConnectionRead.model_validate(connection)


@router.patch("/integrations/{connection_id}", response_model=IntegrationConnectionRead, summary="Atualizar integração financeira")
async def update_finance_integration(
    connection_id: UUID,
    payload: IntegrationConnectionUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_finance_access),
    db: AsyncSession = Depends(get_db),
):
    try:
        connection = await FinanceService(db).update_integration(workspace_id, connection_id, payload)
    except EntitlementError as error:
        raise entitlement_http_exception(error) from error
    return IntegrationConnectionRead.model_validate(connection)
