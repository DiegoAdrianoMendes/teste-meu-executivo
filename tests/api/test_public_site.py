import pytest
from sqlalchemy import select
from urllib.parse import quote

from app.main import app
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.public_site_document import PublicSiteDocument
from app.schemas.public_site import PublicSiteSettingsRead
from app.services.platform_audit_event_service import PlatformAuditEventService


@pytest.mark.asyncio
async def test_public_site_endpoints_seed_defaults(unauthenticated_client):
    site_response = await unauthenticated_client.get('/api/v1/public/site')
    assert site_response.status_code == 200
    site_data = site_response.json()
    assert site_data['brandName'] == 'Meu Executivo'
    assert site_data['whatsappUrl'].startswith('https://wa.me/')

    home_response = await unauthenticated_client.get('/api/v1/public/home')
    assert home_response.status_code == 200
    home_data = home_response.json()
    assert home_data['home']['sections'][0]['key'] == 'hero'
    assert len(home_data['faq']['items']) >= 1

    legal_response = await unauthenticated_client.get('/api/v1/public/legal/terms')
    assert legal_response.status_code == 200
    legal_data = legal_response.json()
    assert legal_data['title'] == 'Termos de Uso'
    assert len(legal_data['sections']) >= 1


@pytest.mark.asyncio
async def test_platform_site_can_update_and_publish(platform_client):
    settings_response = await platform_client.get('/api/v1/platform/site/settings')
    assert settings_response.status_code == 200
    settings_data = settings_response.json()['draft']
    settings_data['brandName'] = 'Banca Teste'

    save_response = await platform_client.put(
        '/api/v1/platform/site/settings',
        headers={'X-Operation-Reason': quote('Atualização editorial aprovada.')},
        json=settings_data,
    )
    assert save_response.status_code == 200
    assert save_response.json()['draft']['brandName'] == 'Banca Teste'

    publish_response = await platform_client.post(
        '/api/v1/platform/site/publish/settings',
        headers={'X-Operation-Reason': quote('Publicação editorial aprovada.')},
    )
    assert publish_response.status_code == 200
    assert publish_response.json()['published']['brandName'] == 'Banca Teste'

    public_response = await platform_client.get('/api/v1/public/site')
    assert public_response.status_code == 200
    assert public_response.json()['brandName'] == 'Banca Teste'


@pytest.mark.asyncio
async def test_platform_site_home_document_is_editable(platform_client):
    home_response = await platform_client.get('/api/v1/platform/site/home')
    assert home_response.status_code == 200
    home_draft = home_response.json()['draft']
    home_draft['sections'][0]['title'] = 'Hero alterado pelo platform admin'

    save_response = await platform_client.put(
        '/api/v1/platform/site/home',
        headers={'X-Operation-Reason': quote('Revisão da mensagem principal.')},
        json=home_draft,
    )
    assert save_response.status_code == 200
    assert (
        save_response.json()['draft']['sections'][0]['title']
        == 'Hero alterado pelo platform admin'
    )


@pytest.mark.asyncio
async def test_platform_site_requires_reason_and_records_semantic_diff(
    platform_client,
    db_session,
):
    current = await platform_client.get('/api/v1/platform/site/settings')
    payload = current.json()['draft']
    original = payload['brandName']
    payload['brandName'] = 'Meu Executivo Auditável'

    missing_reason = await platform_client.put(
        '/api/v1/platform/site/settings',
        json=payload,
    )
    assert missing_reason.status_code == 422

    blank_reason = await platform_client.put(
        '/api/v1/platform/site/settings',
        headers={'X-Operation-Reason': quote('        ')},
        json=payload,
    )
    assert blank_reason.status_code == 422

    updated = await platform_client.put(
        '/api/v1/platform/site/settings',
        headers={
            'X-Operation-Reason': quote('Ajuste aprovado pela equipe de marca.')
        },
        json=payload,
    )
    assert updated.status_code == 200

    event = await db_session.scalar(
        select(PlatformAuditEvent)
        .where(PlatformAuditEvent.action == 'update_platform_site_settings')
        .order_by(PlatformAuditEvent.occurred_at.desc())
    )
    assert event is not None
    assert event.resource_type == 'public_site_document'
    assert event.resource_label_snapshot == 'Configurações do site'
    assert event.reason == 'Ajuste aprovado pela equipe de marca.'
    assert event.resource_version_before == current.json()['version']
    assert event.resource_version_after == updated.json()['version']
    assert event.before['draft']['brandName'] == original
    assert event.after['draft']['brandName'] == 'Meu Executivo Auditável'
    assert set(event.changed_paths) == {'draft.brandName', 'updatedByUserId'}


@pytest.mark.asyncio
async def test_platform_site_update_rolls_back_when_canonical_audit_fails(
    platform_client,
    db_session,
    monkeypatch,
):
    current = await platform_client.get('/api/v1/platform/site/settings')
    original = current.json()['draft']['brandName']
    payload = current.json()['draft']
    payload['brandName'] = 'Alteração sem auditoria não pode persistir'

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError('audit storage unavailable')

    monkeypatch.setattr(PlatformAuditEventService, 'record', fail_audit)
    with pytest.raises(RuntimeError, match='audit storage unavailable'):
        await platform_client.put(
            '/api/v1/platform/site/settings',
            headers={
                'X-Operation-Reason': quote('Teste de atomicidade administrativa.')
            },
            json=payload,
        )

    db_session.expire_all()
    document = await db_session.scalar(
        select(PublicSiteDocument).where(PublicSiteDocument.key == 'settings')
    )
    assert document is not None
    assert (
        PublicSiteSettingsRead.model_validate(document.draft_content).brand_name
        == original
    )


def test_platform_site_mutation_contract_declares_snapshot_and_reason():
    operations = app.openapi()['paths']
    for path, method in (
        ('/api/v1/platform/site/settings', 'put'),
        ('/api/v1/platform/site/home', 'put'),
        ('/api/v1/platform/site/faq', 'put'),
        ('/api/v1/platform/site/media', 'put'),
        ('/api/v1/platform/site/legal/{kind}', 'put'),
        ('/api/v1/platform/site/publish/{resource}', 'post'),
    ):
        descriptor = operations[path][method]['x-platform-mutation']
        assert descriptor['resourceType'] == 'public_site_document'
        assert descriptor['snapshotAdapter'] == 'public_site_document'
        assert descriptor['reasonPolicy'] == 'required'
