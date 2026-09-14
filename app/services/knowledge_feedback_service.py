"""
Service: KnowledgeArticleFeedback
Upsert idempotente do feedback (thumbs) de um artigo de Conhecimento por
usuário/workspace, com contagem agregada — fecha o elo "feedback só em
localStorage" do backlog de dormentes (Onda 1 item 16): antes, o produto
não tinha visibilidade nenhuma de quais artigos funcionam.

`article_id` é o `slug` do artigo estático do front
(`frontend/src/content/knowledge`), não um UUID — não há tabela de artigos
no backend hoje, então não validamos a existência do slug aqui (validação
fica a cargo do catálogo estático do front, que já filtra o que é
navegável).
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_feedback import KnowledgeArticleFeedback, KnowledgeFeedbackValue
from app.schemas.knowledge_feedback import (
    KnowledgeArticleFeedbackCreate,
    KnowledgeArticleFeedbackSummary,
)


class KnowledgeFeedbackService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def submit_feedback(
        self,
        workspace_id: UUID,
        user_id: UUID,
        article_id: str,
        payload: KnowledgeArticleFeedbackCreate,
    ) -> KnowledgeArticleFeedbackSummary:
        normalized_article_id = article_id.strip()
        if not normalized_article_id:
            raise ValueError("article_id não pode ser vazio.")

        existing = await self.db.scalar(
            select(KnowledgeArticleFeedback)
            .where(
                KnowledgeArticleFeedback.workspace_id == workspace_id,
                KnowledgeArticleFeedback.user_id == user_id,
                KnowledgeArticleFeedback.article_id == normalized_article_id,
            )
            .with_for_update()
        )
        if existing:
            existing.value = payload.value
        else:
            self.db.add(
                KnowledgeArticleFeedback(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    article_id=normalized_article_id,
                    value=payload.value,
                )
            )
        await self.db.flush()
        return await self.get_summary(workspace_id, user_id, normalized_article_id)

    async def get_summary(
        self, workspace_id: UUID, user_id: UUID, article_id: str
    ) -> KnowledgeArticleFeedbackSummary:
        rows = (
            await self.db.execute(
                select(KnowledgeArticleFeedback.value, func.count())
                .where(
                    KnowledgeArticleFeedback.workspace_id == workspace_id,
                    KnowledgeArticleFeedback.article_id == article_id,
                )
                .group_by(KnowledgeArticleFeedback.value)
            )
        ).all()
        counts = {value: total for value, total in rows}
        mine = await self.db.scalar(
            select(KnowledgeArticleFeedback.value).where(
                KnowledgeArticleFeedback.workspace_id == workspace_id,
                KnowledgeArticleFeedback.user_id == user_id,
                KnowledgeArticleFeedback.article_id == article_id,
            )
        )
        return KnowledgeArticleFeedbackSummary(
            article_id=article_id,
            up_count=int(counts.get(KnowledgeFeedbackValue.up, 0)),
            down_count=int(counts.get(KnowledgeFeedbackValue.down, 0)),
            my_feedback=mine,
        )
