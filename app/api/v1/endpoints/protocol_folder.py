"""
Endpoints: Pasta de protocolo + manifesto (Onda 7, Plano Mestre §10).

REUSA `protocol_folder_service.py` (montagem + validação + ZIP + registro
de recibo). Este router só amarra `/legal/demands/{demand_id}/protocol-folder`
+ capability `legal` por cima do que já existe.

`PROTOCOLO É EXCLUSIVAMENTE HUMANO`: NÃO existe, neste arquivo, nenhuma
rota que "protocole" nos autos — as únicas rotas de escrita são
`POST .../build` (monta/revalida a pasta, nunca protocola) e
`POST .../receipt` (registra um recibo que JÁ ACONTECEU fora da
plataforma). Ver tests/api/test_protocol_folder.py::
test_no_protocol_execution_route_exists.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.entitlement_errors import entitlement_http_exception
from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_permission
from app.schemas.protocol_folder import (
    ProtocolFolderBuildRequest,
    ProtocolFolderRead,
    ProtocolReceiptRegisterRequest,
)
from app.services.case_360_service import Case360Service
from app.services.entitlement_service import EntitlementError
from app.services.klm_operations_service import KlmOperationsService
from app.services.legal_docx_service import export_docx
from app.services.protocol_folder_service import (
    DefaultDocumentContentResolver,
    ProtocolFolderFile,
    build_protocol_folder,
    build_zip,
    get_protocol_folder,
    register_protocol_receipt,
)

router = APIRouter(
    prefix="/legal/demands/{demand_id}/protocol-folder",
    tags=["Jurídico — Pasta de protocolo (Onda 7)"],
)

require_protocol_view = require_permission("legal", "view")
require_protocol_manage = require_permission("legal", "manage")


async def _require_folder_in_demand(
    db: AsyncSession, workspace_id: UUID, demand_id: UUID, protocol_folder_id: UUID
):
    folder = await get_protocol_folder(db, workspace_id, protocol_folder_id)
    if folder.legal_demand_id != demand_id:
        raise HTTPException(status_code=404, detail="Pasta de protocolo não encontrada.")
    return folder


@router.post(
    "/build",
    response_model=ProtocolFolderRead,
    summary="Montar/revalidar a pasta de protocolo a partir do Caso 360 + artefato aprovado",
)
async def build_demand_protocol_folder(
    demand_id: UUID,
    payload: ProtocolFolderBuildRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_protocol_manage),
    db: AsyncSession = Depends(get_db),
) -> ProtocolFolderRead:
    try:
        result = await build_protocol_folder(
            db,
            workspace_id=workspace_id,
            user_id=auth.user_id,
            legal_demand_id=demand_id,
            artifact_id=payload.artifact_id,
            tribunal=payload.tribunal,
            signature_confirmed=payload.signature_confirmed,
        )
    except EntitlementError as exc:
        raise entitlement_http_exception(exc) from exc
    await db.commit()
    return ProtocolFolderRead.model_validate(result.folder)


@router.get(
    "/{protocol_folder_id}",
    response_model=ProtocolFolderRead,
    summary="Consultar a pasta de protocolo — estado, manifesto e pendências",
)
async def get_demand_protocol_folder(
    demand_id: UUID,
    protocol_folder_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_protocol_view),
    db: AsyncSession = Depends(get_db),
) -> ProtocolFolderRead:
    folder = await _require_folder_in_demand(db, workspace_id, demand_id, protocol_folder_id)
    return ProtocolFolderRead.model_validate(folder)


@router.get(
    "/{protocol_folder_id}/export.zip",
    summary="Baixar a pasta numerada + manifesto num único .zip (entrega para protocolo humano)",
)
async def export_demand_protocol_folder_zip(
    demand_id: UUID,
    protocol_folder_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_protocol_view),
    db: AsyncSession = Depends(get_db),
) -> Response:
    folder = await _require_folder_in_demand(db, workspace_id, demand_id, protocol_folder_id)

    # Reconstrói os bytes a partir do conteúdo já validado no último build —
    # o ZIP nunca é uma segunda coleta independente de arquivos; ver
    # `protocol_folder_service.build_protocol_folder`/`build_zip`.
    artifact = await KlmOperationsService(db).get_legal_artifact(
        workspace_id, folder.artifact_version_id
    )
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artefato da pasta não encontrado.")
    case_360 = await Case360Service(db).get_case_360(workspace_id, folder.legal_demand_id)
    documents_by_id = {document.id: document for document in case_360.documents}
    resolver = DefaultDocumentContentResolver(db)

    reconstructed: list[ProtocolFolderFile] = []
    for row in folder.files:
        if row["sourceType"] == "artifact":
            content = export_docx(artifact).content
        else:
            document = documents_by_id.get(UUID(row["sourceId"]))
            content = (
                await resolver.read(workspace_id=workspace_id, document=document)
                if document is not None
                else None
            )
        if content is None:
            continue
        reconstructed.append(
            ProtocolFolderFile(
                order=row["order"],
                folder=row["folder"],
                filename=row["filename"],
                source_type=row["sourceType"],
                source_id=UUID(row["sourceId"]),
                sha256=row["sha256"],
                size_bytes=row["sizeBytes"],
                extension=row["extension"],
                content=content,
            )
        )
    zip_bytes = build_zip(manifest=folder.manifest, files=reconstructed)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="pasta-protocolo-{protocol_folder_id}.zip"'
            )
        },
    )


@router.post(
    "/{protocol_folder_id}/receipt",
    response_model=ProtocolFolderRead,
    summary="Registrar o recibo de um protocolo que JÁ ACONTECEU fora da plataforma (só humano)",
)
async def register_demand_protocol_receipt(
    demand_id: UUID,
    protocol_folder_id: UUID,
    payload: ProtocolReceiptRegisterRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(require_protocol_manage),
    db: AsyncSession = Depends(get_db),
) -> ProtocolFolderRead:
    await _require_folder_in_demand(db, workspace_id, demand_id, protocol_folder_id)
    try:
        updated = await register_protocol_receipt(
            db,
            workspace_id=workspace_id,
            user_id=auth.user_id,
            protocol_folder_id=protocol_folder_id,
            reference=payload.reference,
            note=payload.note,
            protocoled_at=payload.protocoled_at,
        )
    except EntitlementError as exc:
        raise entitlement_http_exception(exc) from exc
    await db.commit()
    return ProtocolFolderRead.model_validate(updated)
