"""Publicação periódica, interna e auditada dos artigos agendados."""

from __future__ import annotations

import asyncio

from app.db.session import WorkerSessionLocal
from app.services.blog_service import BlogService
from app.workers.celery_app import celery_app


async def _async_publish_due(
    *,
    session_factory=None,
    service_factory=BlogService,
    limit: int = 100,
) -> int:
    session_maker = session_factory or WorkerSessionLocal
    async with session_maker() as db:
        return await service_factory(db).publish_due_posts(limit=limit)


@celery_app.task(name="blog_publication_tasks.publish_due")
def publish_due_blog_posts() -> int:
    return asyncio.run(_async_publish_due())


__all__ = ["_async_publish_due", "publish_due_blog_posts"]
