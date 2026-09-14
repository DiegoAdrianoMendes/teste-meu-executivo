"""Endpoints do módulo processual contratável à parte (add-on) — /processos.

Toda superfície aqui é gateada em duas camadas: `require_permission("processos", ...)`
(quem no workspace pode operar) e o entitlement `monitored_processes`
resolvido dentro do `ProcessMonitoringService` (se o add-on está
contratado). A ausência de qualquer uma delas resulta em recusa limpa —
nunca em prazo fatal ou dado vazando entre workspaces.
"""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.entitlement_errors import entitlement_http_exception
from app.core.deps import (
    AuthContext,
    get_current_auth_context,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.models.process_monitoring import DeadlineStatus, MonitoredProcessStatus
from app.schemas.process_monitoring import (
    DeadlineResolutionRequest,
    MonitoredProcessCreate,
    MonitoredProcessRead,
    MonitoredProcessUpdate,
    ProcessPublicationRead,
)
from app.services.entitlement_service import CurrentStockLimitExceededError, EntitlementError
from app.services.process_monitoring_service import (
    ProcessMonitoringDeadlineStateError,
    ProcessMonitoringDuplicateProcessError,
    ProcessMonitoringInvalidCnjError,
    ProcessMonitoringInvalidOabError,
    ProcessMonitoringModuleNotContractedError,
    ProcessMonitoringNotFoundError,
    ProcessMonitoringService,
)

router = APIRouter(prefix="/processos", tags=["Monitoramento Processual"])

require_processos_view = require_permission("processos", "view")
require_processos_manage = require_permission("processos", "manage")


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, ProcessMonitoringModuleNotContractedError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PROC-MON-001",
                "message": "Módulo de monitoramento processual não contratado para este workspace.",
            },
        )
    if isinstance(error, ProcessMonitoringInvalidCnjError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "PROC-MON-002", "message": str(error)},
        )
    if isinstance(error, ProcessMonitoringInvalidOabError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "PROC-MON-006", "message": str(error)},
        )
    if isinstance(error, ProcessMonitoringDuplicateProcessError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "PROC-MON-003", "message": str(error)},
        )
    if isinstance(error, ProcessMonitoringNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PROC-MON-004", "message": str(error)},
        )
    if isinstance(error, ProcessMonitoringDeadlineStateError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "PROC-MON-005", "message": str(error)},
        )
    if isinstance(error, CurrentStockLimitExceededError):
        return entitlement_http_exception(error)
    if isinstance(error, EntitlementError):  # pragma: no cover — defensivo
        return entitlement_http_exception(error)
    raise error  # pragma: no cover — erro não mapeado, deixa propagar


@router.post(
    "",
    response_model=MonitoredProcessRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cadastrar processo para monitoramento (consome quota do add-on)",
)
async def create_monitored_process(
    payload: MonitoredProcessCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_processos_manage),
    db: AsyncSession = Depends(get_db),
) -> MonitoredProcessRead:
    service = ProcessMonitoringService(db)
    try:
        process = await service.register_process(
            workspace_id,
            numero_cnj=payload.numero_cnj,
            tribunal=payload.tribunal,
            matter_id=payload.matter_id,
            oab_vinculada=payload.oab_vinculada,
            responsible_user_id=payload.responsible_user_id,
            created_by_user_id=context.user_id,
        )
    except (
        ProcessMonitoringModuleNotContractedError,
        ProcessMonitoringInvalidCnjError,
        ProcessMonitoringInvalidOabError,
        ProcessMonitoringDuplicateProcessError,
        ProcessMonitoringNotFoundError,
        CurrentStockLimitExceededError,
    ) as error:
        raise _http_error(error) from error
    await db.commit()
    await db.refresh(process)
    return MonitoredProcessRead.model_validate(process)


@router.patch(
    "/{process_id}",
    response_model=MonitoredProcessRead,
    summary="Editar OAB vinculada, caso ou responsável de um processo monitorado",
)
async def update_monitored_process(
    process_id: UUID,
    payload: MonitoredProcessUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_processos_manage),
    db: AsyncSession = Depends(get_db),
) -> MonitoredProcessRead:
    service = ProcessMonitoringService(db)
    try:
        process = await service.update_process(
            workspace_id, process_id, campos=payload.model_dump(exclude_unset=True)
        )
    except (
        ProcessMonitoringModuleNotContractedError,
        ProcessMonitoringInvalidOabError,
        ProcessMonitoringNotFoundError,
    ) as error:
        raise _http_error(error) from error
    await db.commit()
    await db.refresh(process)
    return MonitoredProcessRead.model_validate(process)


@router.get(
    "",
    response_model=List[MonitoredProcessRead],
    summary="Listar processos monitorados do workspace",
)
async def list_monitored_processes(
    status_filter: Optional[MonitoredProcessStatus] = Query(default=None, alias="status"),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_processos_view),
    db: AsyncSession = Depends(get_db),
) -> List[MonitoredProcessRead]:
    service = ProcessMonitoringService(db)
    try:
        processes = await service.list_processes(workspace_id, status=status_filter)
    except ProcessMonitoringModuleNotContractedError as error:
        raise _http_error(error) from error
    return [MonitoredProcessRead.model_validate(process) for process in processes]


@router.post(
    "/{process_id}/pausar",
    response_model=MonitoredProcessRead,
    summary="Pausar monitoramento de um processo",
)
async def pause_monitored_process(
    process_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_processos_manage),
    db: AsyncSession = Depends(get_db),
) -> MonitoredProcessRead:
    service = ProcessMonitoringService(db)
    try:
        process = await service.pause_process(workspace_id, process_id)
    except (ProcessMonitoringModuleNotContractedError, ProcessMonitoringNotFoundError) as error:
        raise _http_error(error) from error
    await db.commit()
    await db.refresh(process)
    return MonitoredProcessRead.model_validate(process)


@router.post(
    "/{process_id}/retomar",
    response_model=MonitoredProcessRead,
    summary="Retomar monitoramento de um processo pausado (consome quota novamente)",
)
async def resume_monitored_process(
    process_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_processos_manage),
    db: AsyncSession = Depends(get_db),
) -> MonitoredProcessRead:
    service = ProcessMonitoringService(db)
    try:
        process = await service.resume_process(workspace_id, process_id)
    except (
        ProcessMonitoringModuleNotContractedError,
        ProcessMonitoringNotFoundError,
        CurrentStockLimitExceededError,
    ) as error:
        raise _http_error(error) from error
    await db.commit()
    await db.refresh(process)
    return MonitoredProcessRead.model_validate(process)


@router.get(
    "/publicacoes",
    response_model=List[ProcessPublicationRead],
    summary="Listar publicações ingeridas, com filtro por urgência",
)
async def list_process_publications(
    urgencia: Optional[str] = Query(default=None),
    prazo_status: Optional[DeadlineStatus] = Query(default=None),
    monitored_process_id: Optional[UUID] = Query(default=None),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_processos_view),
    db: AsyncSession = Depends(get_db),
) -> List[ProcessPublicationRead]:
    service = ProcessMonitoringService(db)
    try:
        publications = await service.list_publications(
            workspace_id,
            urgencia=urgencia,
            prazo_status=prazo_status,
            monitored_process_id=monitored_process_id,
        )
    except ProcessMonitoringModuleNotContractedError as error:
        raise _http_error(error) from error
    return [ProcessPublicationRead.model_validate(publication) for publication in publications]


@router.get(
    "/prazos",
    response_model=List[ProcessPublicationRead],
    summary="Agenda de prazos do módulo — publicações com prazo calculado, mais urgente primeiro",
)
async def list_process_deadlines(
    prazo_status: Optional[DeadlineStatus] = Query(default=None, alias="status"),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_processos_view),
    db: AsyncSession = Depends(get_db),
) -> List[ProcessPublicationRead]:
    service = ProcessMonitoringService(db)
    try:
        deadlines = await service.list_deadlines(workspace_id, prazo_status=prazo_status)
    except ProcessMonitoringModuleNotContractedError as error:
        raise _http_error(error) from error
    return [ProcessPublicationRead.model_validate(deadline) for deadline in deadlines]


@router.post(
    "/publicacoes/{publication_id}/confirmar-prazo",
    response_model=ProcessPublicationRead,
    summary="Confirmar prazo sugerido — só aqui ele vira compromisso oficial (auditado)",
)
async def confirm_process_publication_deadline(
    publication_id: UUID,
    _payload: DeadlineResolutionRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(get_current_auth_context),
    __: AuthContext = Depends(require_processos_manage),
    db: AsyncSession = Depends(get_db),
) -> ProcessPublicationRead:
    service = ProcessMonitoringService(db)
    try:
        publication = await service.confirm_deadline(
            workspace_id, publication_id, user_id=context.user_id
        )
    except (
        ProcessMonitoringModuleNotContractedError,
        ProcessMonitoringNotFoundError,
        ProcessMonitoringDeadlineStateError,
    ) as error:
        raise _http_error(error) from error
    await db.commit()
    await db.refresh(publication)
    return ProcessPublicationRead.model_validate(publication)


@router.post(
    "/publicacoes/{publication_id}/dispensar-prazo",
    response_model=ProcessPublicationRead,
    summary="Dispensar prazo sugerido (ex.: falso positivo) — auditado",
)
async def dismiss_process_publication_deadline(
    publication_id: UUID,
    _payload: DeadlineResolutionRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    context: AuthContext = Depends(get_current_auth_context),
    __: AuthContext = Depends(require_processos_manage),
    db: AsyncSession = Depends(get_db),
) -> ProcessPublicationRead:
    service = ProcessMonitoringService(db)
    try:
        publication = await service.dismiss_deadline(
            workspace_id, publication_id, user_id=context.user_id
        )
    except (
        ProcessMonitoringModuleNotContractedError,
        ProcessMonitoringNotFoundError,
        ProcessMonitoringDeadlineStateError,
    ) as error:
        raise _http_error(error) from error
    await db.commit()
    await db.refresh(publication)
    return ProcessPublicationRead.model_validate(publication)
