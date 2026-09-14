"""
Endpoints: Editor jurídico — artefatos de uma LegalDemand (Onda 6, §9 item 3).

REUSA: `KlmOperationsService`/`LegalArtifactVersion` (CRUD + submit para
aprovação, Onda 2/5), `legal_editor_service.py` (geração a partir do
office_template + Caso 360, Onda 6) e `legal_docx_service.py` (export/
reimport `.docx`, Onda 6). Nenhum destes caminhos é duplicado aqui — este
router só amarra path `/legal/demands/{demand_id}/artifacts` + capability
`legal` por cima do que já existe.
"""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import (
    AuthContext,
    can_view_sensitive_lead_data,
    get_current_auth_context,
    get_current_workspace_id,
    get_db,
    require_permission,
)
from app.core.lead_privacy import mask_legal_artifact
from app.core.request_body import read_bounded_upload
from app.schemas.klm_operations import (
    LegalArtifactChecklistItemRead,
    LegalArtifactChecklistRead,
    LegalArtifactGenerateRequest,
    LegalArtifactSectionGenerateRequest,
    LegalArtifactSubmitRead,
    LegalArtifactVersionCreate,
    LegalArtifactVersionRead,
)
from app.services.klm_operations_service import KlmOperationsService
from app.services.legal_demand_service import LegalDemandService
from app.services.legal_docx_service import (
    DocxArtifactNotFoundError,
    DocxMergeConflictError,
    DocxVersionRejectedError,
    export_docx,
    reimport_docx,
)
from app.services.legal_editor_service import (
    LegalEditorArtifactNotFoundError,
    LegalEditorMissingMatterError,
    LegalEditorUnsupportedKindError,
    generate_artifact,
    generate_section,
    recompute_checklist,
)
from app.services.playbook_service import PlaybookOfficeTemplateMissingError

router = APIRouter(prefix="/legal/demands", tags=["Jurídico — Editor (Onda 6)"])


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, PlaybookOfficeTemplateMissingError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if isinstance(
        error,
        (LegalEditorUnsupportedKindError, LegalEditorMissingMatterError, DocxVersionRejectedError),
    ):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    if isinstance(error, DocxMergeConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if isinstance(error, (LookupError, DocxArtifactNotFoundError)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))


async def _serialize(artifact, *, can_view_sensitive: bool) -> LegalArtifactVersionRead:
    return artifact if can_view_sensitive else mask_legal_artifact(artifact)


@router.get(
    "/{demand_id}/artifacts",
    response_model=List[LegalArtifactVersionRead],
    summary="Listar as versões jurídicas de uma demanda",
)
async def list_demand_artifacts(
    demand_id: UUID,
    kind: Optional[str] = Query(default=None),
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("legal", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> List[LegalArtifactVersionRead]:
    await LegalDemandService(db).get_demand(workspace_id, demand_id)
    artifacts = await KlmOperationsService(db).list_legal_artifacts(
        workspace_id, legal_demand_id=demand_id, kind=kind
    )
    return [await _serialize(item, can_view_sensitive=can_view_sensitive) for item in artifacts]


@router.get(
    "/{demand_id}/artifacts/{artifact_id}",
    response_model=LegalArtifactVersionRead,
    summary="Consultar uma versão jurídica exata da demanda",
)
async def get_demand_artifact(
    demand_id: UUID,
    artifact_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("legal", "view")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> LegalArtifactVersionRead:
    artifact = await KlmOperationsService(db).get_legal_artifact(workspace_id, artifact_id)
    if artifact is None or artifact.legal_demand_id != demand_id:
        raise HTTPException(status_code=404, detail="Documento jurídico não encontrado.")
    return await _serialize(artifact, can_view_sensitive=can_view_sensitive)


@router.post(
    "/{demand_id}/artifacts",
    response_model=LegalArtifactVersionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar manualmente uma versão jurídica da demanda",
)
async def create_demand_artifact(
    demand_id: UUID,
    payload: LegalArtifactVersionCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("legal", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> LegalArtifactVersionRead:
    demand = await LegalDemandService(db).get_demand(workspace_id, demand_id)
    if demand.matter_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Esta demanda não tem um Caso operacional (Matter) vinculado; "
                "documentos jurídicos exigem um Matter."
            ),
        )
    # A demanda no path é sempre a fonte de verdade — sobrescreve qualquer
    # matter_id/legal_demand_id enviado no corpo.
    create_payload = payload.model_copy(
        update={"matter_id": demand.matter_id, "legal_demand_id": demand_id}
    )
    try:
        artifact = await KlmOperationsService(db).create_legal_artifact_version(
            workspace_id, auth.user_id, create_payload
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    return await _serialize(artifact, can_view_sensitive=can_view_sensitive)


@router.post(
    "/{demand_id}/artifacts/generate",
    response_model=LegalArtifactVersionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Gerar o artefato integral a partir do office_template + Caso 360",
)
async def generate_demand_artifact(
    demand_id: UUID,
    payload: LegalArtifactGenerateRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("legal", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> LegalArtifactVersionRead:
    generate_payload = payload.model_copy(update={"legal_demand_id": demand_id})
    try:
        artifact = await generate_artifact(
            db, workspace_id=workspace_id, user_id=auth.user_id, payload=generate_payload
        )
    except (
        ValueError,
        LookupError,
        PlaybookOfficeTemplateMissingError,
    ) as error:
        raise _http_error(error) from error
    return await _serialize(artifact, can_view_sensitive=can_view_sensitive)


@router.post(
    "/{demand_id}/artifacts/{artifact_id}/sections/generate",
    response_model=LegalArtifactVersionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Regenerar uma seção do artefato a partir do Caso 360 atual",
)
async def generate_demand_artifact_section(
    demand_id: UUID,
    artifact_id: UUID,
    payload: LegalArtifactSectionGenerateRequest,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("legal", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> LegalArtifactVersionRead:
    existing = await KlmOperationsService(db).get_legal_artifact(workspace_id, artifact_id)
    if existing is None or existing.legal_demand_id != demand_id:
        raise HTTPException(status_code=404, detail="Documento jurídico não encontrado.")
    try:
        artifact = await generate_section(
            db,
            workspace_id=workspace_id,
            user_id=auth.user_id,
            artifact_id=artifact_id,
            section_id=payload.section_id,
            expected_version=payload.expected_version,
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    return await _serialize(artifact, can_view_sensitive=can_view_sensitive)


@router.post(
    "/{demand_id}/artifacts/{artifact_id}/submit",
    response_model=LegalArtifactSubmitRead,
    summary="Enviar uma versão jurídica da demanda para aprovação humana",
)
async def submit_demand_artifact(
    demand_id: UUID,
    artifact_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("legal", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> LegalArtifactSubmitRead:
    existing = await KlmOperationsService(db).get_legal_artifact(workspace_id, artifact_id)
    if existing is None or existing.legal_demand_id != demand_id:
        raise HTTPException(status_code=404, detail="Documento jurídico não encontrado.")
    try:
        artifact, approval = await KlmOperationsService(db).submit_legal_artifact_for_approval(
            workspace_id, auth.user_id, artifact_id
        )
    except (ValueError, LookupError) as error:
        raise _http_error(error) from error
    serialized = await _serialize(artifact, can_view_sensitive=can_view_sensitive)
    return LegalArtifactSubmitRead(
        artifact=LegalArtifactVersionRead.model_validate(serialized),
        approval_id=approval.id,
    )


@router.get(
    "/{demand_id}/artifacts/{artifact_id}/checklist",
    response_model=LegalArtifactChecklistRead,
    summary="Checklist de protocolabilidade do artefato (placeholder/procuração/custas/citações)",
)
async def get_demand_artifact_checklist(
    demand_id: UUID,
    artifact_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("legal", "view")),
    db: AsyncSession = Depends(get_db),
) -> LegalArtifactChecklistRead:
    existing = await KlmOperationsService(db).get_legal_artifact(workspace_id, artifact_id)
    if existing is None or existing.legal_demand_id != demand_id:
        raise HTTPException(status_code=404, detail="Documento jurídico não encontrado.")
    try:
        checklist = await recompute_checklist(
            db, workspace_id=workspace_id, artifact_id=artifact_id
        )
    except LegalEditorArtifactNotFoundError as error:
        raise _http_error(error) from error
    return LegalArtifactChecklistRead(
        has_placeholder=checklist.has_placeholder,
        missing_variables=checklist.missing_variables,
        has_procuracao=checklist.has_procuracao,
        has_custas_pagas=checklist.has_custas_pagas,
        pending_items=[
            LegalArtifactChecklistItemRead(item=item["item"], detail=item["detail"])
            for item in checklist.pending_items
        ],
        protocolability_state=checklist.protocolability_state,
    )


@router.get(
    "/{demand_id}/artifacts/{artifact_id}/export.docx",
    summary="Exportar a versão como .docx editável (round-trip KLM)",
)
async def export_demand_artifact_docx(
    demand_id: UUID,
    artifact_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("legal", "view")),
    db: AsyncSession = Depends(get_db),
) -> Response:
    artifact = await KlmOperationsService(db).get_legal_artifact(workspace_id, artifact_id)
    if artifact is None or artifact.legal_demand_id != demand_id:
        raise HTTPException(status_code=404, detail="Documento jurídico não encontrado.")
    try:
        result = export_docx(artifact)
    except ValueError as error:
        raise _http_error(error) from error
    return Response(
        content=result.content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{result.filename}"'},
    )


@router.post(
    "/{demand_id}/artifacts/{artifact_id}/reimport",
    response_model=LegalArtifactVersionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Reimportar um .docx editado (nunca sobrescreve; nunca mescla automático)",
)
async def reimport_demand_artifact_docx(
    demand_id: UUID,
    artifact_id: UUID,
    expected_version: int = Query(..., ge=1),
    file: UploadFile = File(...),
    workspace_id: UUID = Depends(get_current_workspace_id),
    auth: AuthContext = Depends(get_current_auth_context),
    _: AuthContext = Depends(require_permission("legal", "manage")),
    can_view_sensitive: bool = Depends(can_view_sensitive_lead_data),
    db: AsyncSession = Depends(get_db),
) -> LegalArtifactVersionRead:
    anchor = await KlmOperationsService(db).get_legal_artifact(workspace_id, artifact_id)
    if anchor is None or anchor.legal_demand_id != demand_id:
        raise HTTPException(status_code=404, detail="Documento jurídico não encontrado.")
    file_bytes = await read_bounded_upload(
        file,
        max_bytes=settings.LEGAL_DOCX_UPLOAD_MAX_BYTES,
    )
    try:
        result = await reimport_docx(
            db,
            workspace_id=workspace_id,
            user_id=auth.user_id,
            artifact_id=artifact_id,
            file_bytes=file_bytes,
            expected_version=expected_version,
        )
    except (DocxArtifactNotFoundError, DocxVersionRejectedError, DocxMergeConflictError) as error:
        raise _http_error(error) from error
    return await _serialize(result.artifact, can_view_sensitive=can_view_sensitive)
