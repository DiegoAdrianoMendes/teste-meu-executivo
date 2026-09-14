from __future__ import annotations

import pytest

from app.workers.blog_publication_tasks import _async_publish_due
from app.workers.celery_app import celery_app


class _SessionContext:
    def __init__(self, session: object) -> None:
        self.session = session

    async def __aenter__(self) -> object:
        return self.session

    async def __aexit__(self, *_args) -> None:
        return None


@pytest.mark.asyncio
async def test_blog_publication_worker_runs_a_bounded_service_batch() -> None:
    session = object()
    observed: dict[str, object] = {}

    class Service:
        def __init__(self, db: object) -> None:
            observed['db'] = db

        async def publish_due_posts(self, *, limit: int) -> int:
            observed['limit'] = limit
            return 3

    result = await _async_publish_due(
        session_factory=lambda: _SessionContext(session),
        service_factory=Service,
        limit=75,
    )

    assert result == 3
    assert observed == {'db': session, 'limit': 75}


def test_blog_publication_worker_is_registered_in_celery_beat() -> None:
    schedule = celery_app.conf.beat_schedule[
        'publish-due-blog-posts-every-minute'
    ]

    assert schedule['task'] == 'blog_publication_tasks.publish_due'
    assert 'blog_publication_tasks.publish_due' in celery_app.tasks
