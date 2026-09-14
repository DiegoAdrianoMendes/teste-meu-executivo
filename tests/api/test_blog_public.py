from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest
from sqlalchemy import select

from app.main import app
from app.models.blog_post import BlogPost
from app.models.platform_audit_event import PlatformAuditEvent
from app.services.blog_service import BlogService
from app.services.platform_audit_event_service import PlatformAuditEventService


@pytest.mark.asyncio
async def test_public_blog_endpoints_are_honest_when_no_article_was_published(
    unauthenticated_client,
):
    index_response = await unauthenticated_client.get('/api/v1/public/blog')
    assert index_response.status_code == 200
    index_data = index_response.json()
    assert index_data['pageTitle'] == 'Blog Meu Executivo'
    assert index_data['posts'] == []
    assert index_data['featuredPost'] is None

    category_slug = 'atendimento-juridico'
    category_response = await unauthenticated_client.get(f'/api/v1/public/blog/category/{category_slug}')
    assert category_response.status_code == 200
    assert category_response.json()['category'] == 'Atendimento jurídico'
    assert category_response.json()['posts'] == []


@pytest.mark.asyncio
async def test_platform_blog_schedule_publishes_and_discovery_updates(
    platform_client,
    db_session,
):
    create_response = await platform_client.post(
        '/api/v1/platform/blog/posts',
        headers={
            'X-Operation-Reason': quote('Criação do artigo de teste editorial.')
        },
        json={
            'title': 'Post agendado para descoberta',
            'subtitle': 'Teste de scheduling com sitemap e rss',
            'categories': ['CRM jurídico'],
            'tags': ['teste', 'seo'],
            'contentBlocks': [
                {'type': 'heading', 'level': 2, 'content': 'Bloco principal', 'items': [], 'metadata': {}},
                {'type': 'paragraph', 'content': 'Conteúdo suficiente para o artigo aparecer publicamente.', 'items': [], 'metadata': {}},
            ],
        },
    )
    assert create_response.status_code == 201
    post_data = create_response.json()

    posts_page = await platform_client.get(
        '/api/v1/platform/blog/posts/page',
        params={'limit': 1, 'offset': 0},
    )
    assert posts_page.status_code == 200
    assert posts_page.json()['limit'] == 1
    assert posts_page.json()['offset'] == 0
    assert posts_page.json()['total'] >= 1
    assert len(posts_page.json()['items']) == 1

    metrics_response = await platform_client.get(
        '/api/v1/platform/blog/posts/metrics'
    )
    assert metrics_response.status_code == 200
    metrics = metrics_response.json()
    assert metrics['total'] >= 1
    assert 0 <= metrics['indexable'] <= metrics['total']
    assert 0 <= metrics['includedInSitemap'] <= metrics['total']
    assert 0 <= metrics['includedInLlms'] <= metrics['total']
    assert 0 <= metrics['scored'] <= metrics['total']
    assert metrics['dataState'] == 'available'
    assert metrics['averageSeoScore'] is None or 0 <= metrics['averageSeoScore'] <= 100

    media_page = await platform_client.get(
        '/api/v1/platform/blog/media/assets/page',
        params={'limit': 24, 'offset': 0},
    )
    assert media_page.status_code == 200
    assert media_page.json()['limit'] == 24
    assert media_page.json()['total'] >= len(media_page.json()['items'])

    jobs_page = await platform_client.get(
        '/api/v1/platform/blog/google-business/jobs/page',
        params={'limit': 25, 'offset': 0},
    )
    assert jobs_page.status_code == 200
    assert jobs_page.json()['limit'] == 25
    assert jobs_page.json()['total'] >= len(jobs_page.json()['items'])

    past_publish_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    schedule_response = await platform_client.post(
        f"/api/v1/platform/blog/posts/{post_data['id']}/schedule",
        params={'publishAt': past_publish_at},
        headers={
            'X-Operation-Reason': quote(
                'Agendamento aprovado para descoberta pública.'
            )
        },
    )
    assert schedule_response.status_code == 200
    assert schedule_response.json()['status'] == 'scheduled'

    before_worker = await platform_client.get(
        f"/api/v1/public/blog/{post_data['slug']}"
    )
    assert before_worker.status_code == 404

    processed = await BlogService(db_session).publish_due_posts(commit=False)
    assert processed == 1
    await db_session.commit()

    public_response = await platform_client.get(f"/api/v1/public/blog/{post_data['slug']}")
    assert public_response.status_code == 200
    assert public_response.json()['status'] == 'published'

    sitemap_response = await platform_client.get('/sitemap.xml')
    assert sitemap_response.status_code == 200
    assert post_data['slug'] in sitemap_response.text

    rss_response = await platform_client.get('/rss.xml')
    assert rss_response.status_code == 200
    assert post_data['title'] in rss_response.text

    llms_response = await platform_client.get('/llms.txt')
    assert llms_response.status_code == 200
    assert post_data['slug'] in llms_response.text

    markdown_response = await platform_client.get(f"/blog/{post_data['slug']}.md")
    assert markdown_response.status_code == 200
    assert post_data['title'] in markdown_response.text

    json_response = await platform_client.get(f"/blog/{post_data['slug']}.json")
    assert json_response.status_code == 200
    assert json_response.json()['slug'] == post_data['slug']

    system_event = await db_session.scalar(
        select(PlatformAuditEvent).where(
            PlatformAuditEvent.action == 'publish_due_blog_post',
            PlatformAuditEvent.resource_id == post_data['id'],
        )
    )
    assert system_event is not None
    assert system_event.actor_id is None
    assert system_event.actor_role_snapshot == 'system'
    assert system_event.session_type == 'system'
    assert system_event.before['status'] == 'scheduled'
    assert system_event.after['status'] == 'published'
    assert {'status', 'publishedAt', 'revision'} <= set(
        system_event.changed_paths
    )


@pytest.mark.asyncio
async def test_platform_blog_metrics_are_honest_when_the_database_is_empty(
    platform_client,
):
    response = await platform_client.get('/api/v1/platform/blog/posts/metrics')
    assert response.status_code == 200
    assert response.json() == {
        'total': 0,
        'indexable': 0,
        'includedInSitemap': 0,
        'includedInLlms': 0,
        'scored': 0,
        'averageSeoScore': None,
        'dataState': 'insufficient_data',
    }


@pytest.mark.asyncio
async def test_platform_blog_requires_reason_and_records_versioned_diff(
    platform_client,
    db_session,
):
    missing_reason = await platform_client.post(
        '/api/v1/platform/blog/posts',
        json={'title': 'Post sem motivo'},
    )
    assert missing_reason.status_code == 422

    created = await platform_client.post(
        '/api/v1/platform/blog/posts',
        headers={
            'X-Operation-Reason': quote(
                'Criação aprovada para auditoria editorial.'
            )
        },
        json={'title': 'Post auditável', 'contentBlocks': []},
    )
    assert created.status_code == 201
    post = created.json()
    assert post['revision'] == 1

    updated = await platform_client.put(
        f"/api/v1/platform/blog/posts/{post['id']}",
        headers={
            'X-Operation-Reason': quote(
                'Revisão aprovada do título editorial.'
            )
        },
        json={'title': 'Post auditável revisado'},
    )
    assert updated.status_code == 200
    assert updated.json()['revision'] == 2

    event = await db_session.scalar(
        select(PlatformAuditEvent)
        .where(PlatformAuditEvent.action == 'update_blog_post')
        .order_by(PlatformAuditEvent.occurred_at.desc())
    )
    assert event is not None
    assert event.resource_type == 'blog_post'
    assert event.resource_label_snapshot == 'Post auditável revisado'
    assert event.resource_version_before == 1
    assert event.resource_version_after == 2
    assert event.before['title'] == 'Post auditável'
    assert event.after['title'] == 'Post auditável revisado'
    assert {'title', 'revision'} <= set(event.changed_paths)


def test_platform_blog_mutations_have_canonical_snapshot_contracts():
    operations = app.openapi()['paths']
    mutation_paths = {
        '/api/v1/platform/blog/posts': ['post'],
        '/api/v1/platform/blog/posts/{post_id}': ['put'],
        '/api/v1/platform/blog/posts/{post_id}/publish': ['post'],
        '/api/v1/platform/blog/posts/{post_id}/schedule': ['post'],
        '/api/v1/platform/blog/posts/{post_id}/archive': ['post'],
        '/api/v1/platform/blog/posts/{post_id}/duplicate': ['post'],
        '/api/v1/platform/blog/posts/{post_id}/seo-autofill': ['post'],
        '/api/v1/platform/blog/posts/{post_id}/assist': ['post'],
        '/api/v1/platform/blog/posts/{post_id}/generate-cover': ['post'],
        '/api/v1/platform/blog/settings': ['put'],
        '/api/v1/platform/blog/taxonomy': ['put'],
        '/api/v1/platform/blog/indexing': ['put'],
        '/api/v1/platform/blog/publish/{resource}': ['post'],
        '/api/v1/platform/blog/media/assets': ['post'],
        '/api/v1/platform/blog/media/assets/{asset_id}/confirm': ['post'],
        '/api/v1/platform/blog/google-business': ['put'],
        '/api/v1/platform/blog/google-business/jobs': ['post'],
    }
    for path, methods in mutation_paths.items():
        for method in methods:
            descriptor = operations[path][method]['x-platform-mutation']
            assert descriptor['snapshotAdapter'] != 'none', (path, method)
            assert descriptor['reasonPolicy'] == 'required', (path, method)


@pytest.mark.asyncio
async def test_platform_blog_update_rolls_back_when_audit_fails(
    platform_client,
    db_session,
    monkeypatch,
):
    created = await platform_client.post(
        '/api/v1/platform/blog/posts',
        headers={
            'X-Operation-Reason': quote('Criação para teste de atomicidade.')
        },
        json={'title': 'Título preservado', 'contentBlocks': []},
    )
    assert created.status_code == 201
    post_id = created.json()['id']

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError('audit storage unavailable')

    monkeypatch.setattr(PlatformAuditEventService, 'record', fail_audit)
    with pytest.raises(RuntimeError, match='audit storage unavailable'):
        await platform_client.put(
            f'/api/v1/platform/blog/posts/{post_id}',
            headers={
                'X-Operation-Reason': quote('Alteração que precisa ser revertida.')
            },
            json={'title': 'Título que não pode persistir'},
        )

    db_session.expire_all()
    persisted = await db_session.get(BlogPost, post_id)
    assert persisted is not None
    assert persisted.title == 'Título preservado'
    assert persisted.revision == 1


@pytest.mark.asyncio
async def test_robots_txt_is_served(platform_client):
    response = await platform_client.get('/robots.txt')
    assert response.status_code == 200
    assert 'User-agent: *' in response.text
