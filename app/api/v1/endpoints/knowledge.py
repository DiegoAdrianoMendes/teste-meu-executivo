"""
Endpoint: Knowledge (artigos de ajuda estáticos, /app/conhecimento)
  - POST /knowledge/articles/{article_id}/feedback → registra thumbs up/down

`article_id` é o `slug` do artigo (catálogo estático em
`frontend/src/content/knowledge`, sem tabela própria no backend) — qualquer
usuário autenticado com workspace ativo pode registrar seu voto, sem
permissão de módulo dedicada, mesmo padrão de `POST /notifications/mark-all-read`.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user_id, get_current_workspace_id, get_db
from app.schemas.knowledge_feedback import (
    KnowledgeArticleFeedbackCreate,
    KnowledgeArticleFeedbackSummary,
)
from app.services.knowledge_feedback_service import KnowledgeFeedbackService

router = APIRouter(prefix="/knowledge", tags=["Conhecimento"])


@router.post(
    "/articles/{article_id}/feedback",
    response_model=KnowledgeArticleFeedbackSummary,
    status_code=status.HTTP_200_OK,
    summary="Registrar feedback (thumbs) de um artigo de Conhecimento",
)
async def submit_article_feedback(
    article_id: str,
    payload: KnowledgeArticleFeedbackCreate,
    db: AsyncSession = Depends(get_db),
    current_user_id: UUID = Depends(get_current_user_id),
    workspace_id: UUID = Depends(get_current_workspace_id),
):
    service = KnowledgeFeedbackService(db)
    try:
        return await service.submit_feedback(workspace_id, current_user_id, article_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
